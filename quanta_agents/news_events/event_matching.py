from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.llm_client import chat
from quanta_agents.core.llm_json import parse_json_object


DEFAULT_WEIGHTS = {
    "subject_object_match": 0.22,
    "action_match": 0.18,
    "entity_overlap": 0.15,
    "event_type_match": 0.10,
    "location_match": 0.10,
    "time_proximity": 0.10,
    "semantic_similarity": 0.08,
    "stage_compatibility": 0.04,
    "source_reference_match": 0.03,
}
ACTION_GROUPS = (
    {"封锁", "通航", "恢复通行", "航运"},
    {"否认", "驳斥", "不属实"},
    {"撤回", "收回", "更正"},
    {"公布", "发布", "宣布", "批准"},
    {"减产", "增产", "限产"},
    {"上涨", "下跌", "回落", "拉升", "跳水", "市场观察"},
)
CONTRADICTION_STAGES = {"denial"}
RETRACTION_STAGES = {"retraction"}


def _prompt_path(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "prompts" / name


def load_policy(root: str | Path | None = None) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    candidates = [
        root_path / "configs/policies/news_event_clustering.v1.yaml",
        Path(__file__).resolve().parents[1] / "config" / "news_event_clustering.v1.yaml",
    ]
    for path in candidates:
        if not path.exists():
            continue
        payload = _load_policy_file(path)
        if isinstance(payload, dict):
            payload.setdefault("score_weights", DEFAULT_WEIGHTS)
            payload.setdefault("thresholds", {})
            payload["thresholds"].setdefault("auto_same_event", 0.86)
            payload["thresholds"].setdefault("llm_pair_judge", 0.65)
            return payload
    return {
        "schema_version": "news_event_clustering_policy.v1",
        "score_weights": DEFAULT_WEIGHTS,
        "thresholds": {"auto_same_event": 0.86, "llm_pair_judge": 0.65},
        "retrieval": {"lookback_days": 7, "top_k": 20},
    }


def _load_policy_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except Exception:
        return _parse_simple_yaml(text)


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the small bundled policy file when PyYAML is unavailable."""
    root: dict[str, Any] = {}
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if indent == 0 and not value:
            current = {}
            root[key] = current
            continue
        target = current if indent > 0 and current is not None else root
        if value.lower() in {"true", "false"}:
            parsed: Any = value.lower() == "true"
        else:
            try:
                parsed = float(value) if "." in value else int(value)
            except ValueError:
                parsed = value.strip('"')
        target[key] = parsed
    return root


def _terms(values: Any) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        raw_values = [values]
    elif isinstance(values, list):
        raw_values = [str(item) for item in values if item]
    else:
        raw_values = [str(values)]
    out: set[str] = set()
    for value in raw_values:
        chunks = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_+-]{1,}", value)
        out.update(chunk.lower() for chunk in chunks if chunk.strip())
    return out


def _overlap(left: Any, right: Any) -> float:
    a = _terms(left)
    b = _terms(right)
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a | b), 1)


def _action_group(action: str) -> int:
    text = str(action or "")
    for index, group in enumerate(ACTION_GROUPS):
        if any(word in text for word in group):
            return index
    return -1


def _action_match(left: str, right: str) -> float:
    if left == right and left:
        return 1.0
    if _action_group(left) >= 0 and _action_group(left) == _action_group(right):
        return 0.7
    if left and right and (left in right or right in left):
        return 0.6
    return 0.0


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _time_proximity(left: Any, right: Any) -> float:
    a = _parse_time(left)
    b = _parse_time(right)
    if not a or not b:
        return 0.3
    hours = abs((a - b).total_seconds()) / 3600.0
    if hours <= 6:
        return 1.0
    if hours <= 24:
        return 0.75
    if hours <= 72:
        return 0.45
    if hours <= 168:
        return 0.2
    return 0.0


def _stage_compatibility(mention_stage: str, event_stage: str) -> float:
    if mention_stage == event_stage:
        return 1.0
    if mention_stage in CONTRADICTION_STAGES or event_stage in CONTRADICTION_STAGES:
        return 0.0
    if mention_stage in RETRACTION_STAGES or event_stage in RETRACTION_STAGES:
        return 0.0
    compatible = {"rumor", "proposal", "announced", "ongoing", "update", "observation"}
    if mention_stage in compatible and event_stage in compatible:
        return 0.55
    return 0.2


def score_mention_to_event(mention: dict[str, Any], event: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    signature = event.get("core_signature") or {}
    weights = policy.get("score_weights") or DEFAULT_WEIGHTS
    components = {
        "subject_object_match": (
            _overlap(mention.get("subject"), signature.get("subject"))
            + _overlap(mention.get("object"), signature.get("object"))
        )
        / 2.0,
        "action_match": _action_match(str(mention.get("action") or ""), str(signature.get("action") or "")),
        "entity_overlap": _overlap(mention.get("entities"), event.get("core_entities")),
        "event_type_match": 1.0 if mention.get("event_type") == event.get("event_type") else 0.0,
        "location_match": _overlap(mention.get("location"), signature.get("location")),
        "time_proximity": _time_proximity(mention.get("event_time"), event.get("event_time") or event.get("last_seen_at")),
        "semantic_similarity": _overlap(mention.get("canonical_summary"), event.get("canonical_summary")),
        "stage_compatibility": _stage_compatibility(
            str(mention.get("event_stage") or ""),
            str(signature.get("event_stage") or ""),
        ),
        "source_reference_match": 0.0,
    }
    score = sum(float(weights.get(key, 0.0)) * value for key, value in components.items())
    return {"score": round(score, 4), "components": components}


def is_contradiction(mention: dict[str, Any], event: dict[str, Any]) -> bool:
    stage = str(mention.get("event_stage") or "")
    action = str(mention.get("action") or "")
    if stage not in CONTRADICTION_STAGES and "否认" not in action:
        return False
    signature = event.get("core_signature") or {}
    same_object = _overlap(mention.get("object"), signature.get("object")) >= 0.2
    same_location = _overlap(mention.get("location"), signature.get("location")) >= 0.2
    same_entities = _overlap(mention.get("entities"), event.get("core_entities")) >= 0.2
    candidate_stage = str(signature.get("event_stage") or "")
    return (same_object or same_location) and same_entities and candidate_stage not in CONTRADICTION_STAGES


def is_retraction(mention: dict[str, Any], event: dict[str, Any]) -> bool:
    stage = str(mention.get("event_stage") or "")
    if stage not in RETRACTION_STAGES:
        return False
    signature = event.get("core_signature") or {}
    return _overlap(mention.get("object"), signature.get("object")) >= 0.2 or _overlap(
        mention.get("entities"), event.get("core_entities")
    ) >= 0.2


def choose_event_match(
    mention: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    policy: dict[str, Any],
    use_llm: bool = False,
) -> dict[str, Any]:
    thresholds = policy.get("thresholds") or {}
    auto_same = float(thresholds.get("auto_same_event") or 0.86)
    llm_gate = float(thresholds.get("llm_pair_judge") or 0.65)
    scored = []
    for event in candidates:
        score = score_mention_to_event(mention, event, policy)
        scored.append((score["score"], event, score))
    scored.sort(key=lambda row: row[0], reverse=True)
    if not scored:
        return {"decision": "CREATE_NEW", "score": 0.0, "target_event_id": "", "reason": "no candidates"}

    best_score, best_event, best_detail = scored[0]
    if is_retraction(mention, best_event):
        return {
            "decision": "RETRACT",
            "score": max(best_score, 0.75),
            "target_event_id": best_event["event_id"],
            "components": best_detail["components"],
            "reason": "rule retraction relation",
        }
    if is_contradiction(mention, best_event):
        return {
            "decision": "CONTRADICT",
            "score": max(best_score, 0.75),
            "target_event_id": best_event["event_id"],
            "components": best_detail["components"],
            "reason": "rule denial contradicts existing event",
        }
    if best_score >= auto_same and best_detail["components"]["stage_compatibility"] > 0:
        return {
            "decision": "SAME_EVENT",
            "score": best_score,
            "target_event_id": best_event["event_id"],
            "components": best_detail["components"],
            "reason": "score above auto SAME_EVENT threshold",
        }
    if best_score >= llm_gate and use_llm:
        judged = llm_pair_judge(mention, [event for _, event, _ in scored[:5]])
        if judged.get("decision") and judged.get("decision") != "UNRELATED":
            judged.setdefault("score", best_score)
            judged.setdefault("components", best_detail["components"])
            return judged
    if best_score >= llm_gate and best_detail["components"]["stage_compatibility"] >= 0.55:
        return {
            "decision": "SAME_EVENT",
            "score": best_score,
            "target_event_id": best_event["event_id"],
            "components": best_detail["components"],
            "reason": "rule fallback medium score with compatible stage",
        }
    return {
        "decision": "CREATE_NEW",
        "score": best_score,
        "target_event_id": best_event["event_id"],
        "components": best_detail["components"],
        "reason": "below SAME_EVENT threshold",
    }


def llm_pair_judge(mention: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = _prompt_path("event_pair_judge.v1.txt").read_text(encoding="utf-8")
    payload = {
        "new_event_mention": mention,
        "candidate_canonical_events": [
            {
                "event_id": event.get("event_id"),
                "canonical_summary": event.get("canonical_summary"),
                "event_type": event.get("event_type"),
                "event_time": event.get("event_time"),
                "core_signature": event.get("core_signature"),
                "truth_status": event.get("truth_status"),
                "representative_mentions": (event.get("representative_mentions") or [])[:3],
            }
            for event in candidates[:5]
        ],
    }
    try:
        parsed = parse_json_object(
            chat(
                f"{prompt}\n\n输入：\n{json.dumps(payload, ensure_ascii=False)}",
                max_tokens=1200,
                temperature=0.1,
                timeout=80,
            )
        )
    except Exception as exc:
        return {"decision": "UNRELATED", "target_event_id": "", "confidence": 0.0, "reason": f"llm failed: {exc}"}
    decision = str(parsed.get("decision") or "UNRELATED").strip()
    if decision not in {"SAME_EVENT", "UPDATE", "CONFIRM", "CONTRADICT", "RETRACT", "CAUSES", "UNRELATED"}:
        decision = "UNRELATED"
    try:
        confidence = float(parsed.get("confidence") or 0.0)
        if math.isnan(confidence):
            confidence = 0.0
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "decision": decision,
        "target_event_id": str(parsed.get("target_event_id") or ""),
        "confidence": confidence,
        "reason": str(parsed.get("reason") or "llm pair judge"),
    }
