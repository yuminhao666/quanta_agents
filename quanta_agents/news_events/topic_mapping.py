from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quanta_agents.core.io import utc_now_iso
from quanta_agents.core.llm_client import chat
from quanta_agents.core.llm_json import parse_json_object

from .event_matching import _overlap
from .ids import stable_topic_id, topic_alias_id, topic_membership_id


def _prompt_path(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "prompts" / name


def topic_title_for_event(event: dict[str, Any], asset_links: list[dict[str, Any]] | None = None) -> str:
    entities = event.get("core_entities") or []
    signature = event.get("core_signature") or {}
    locations = signature.get("location") or []
    text = " ".join([event.get("canonical_summary") or "", *entities, *locations])
    if "霍尔木兹" in text or ("伊朗" in text and event.get("event_type") == "geopolitics"):
        return "霍尔木兹通行与中东地缘风险"
    if event.get("event_type") == "macro" and ("美联储" in text or "FOMC" in text):
        return "美联储政策与美元利率预期"
    if event.get("event_type") == "inventory":
        labels = [link.get("asset_label") for link in asset_links or [] if link.get("asset_label")]
        return f"{labels[0]}库存变化" if labels else "库存变化"
    if entities:
        return f"{entities[0]}{event_type_label(event.get('event_type'))}事件"
    return event.get("canonical_summary")[:40] or "未命名新闻事件话题"


def event_type_label(event_type: Any) -> str:
    return {
        "geopolitics": "地缘",
        "policy": "政策",
        "macro": "宏观",
        "supply": "供应",
        "demand": "需求",
        "inventory": "库存",
        "cost": "成本",
        "logistics": "物流",
        "market": "行情",
    }.get(str(event_type or ""), "新闻")


def score_event_to_topic(event: dict[str, Any], topic: dict[str, Any], asset_links: list[dict[str, Any]] | None = None) -> float:
    title = topic.get("canonical_title") or ""
    entity_score = _overlap(event.get("core_entities"), topic.get("core_entities"))
    type_score = 1.0 if event.get("event_type") in (topic.get("core_event_types") or []) else 0.0
    title_score = _overlap(event.get("canonical_summary"), title)
    asset_text = " ".join(link.get("asset_label") or "" for link in asset_links or [])
    asset_score = _overlap(asset_text, " ".join(topic.get("included_scope") or []))
    return round(min(1.0, 0.42 * entity_score + 0.24 * type_score + 0.24 * title_score + 0.10 * asset_score), 3)


def map_event_to_topic(
    event: dict[str, Any],
    topics: list[dict[str, Any]],
    *,
    asset_links: list[dict[str, Any]] | None = None,
    framework_links: list[dict[str, Any]] | None = None,
    agent_run_id: str = "",
    thresholds: dict[str, Any] | None = None,
    use_llm: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    thresholds = thresholds or {}
    auto_gate = float(thresholds.get("topic_auto_membership") or 0.84)
    llm_gate = float(thresholds.get("topic_llm_judge") or 0.62)
    scored = sorted(
        [(score_event_to_topic(event, topic, asset_links), topic) for topic in topics],
        key=lambda row: row[0],
        reverse=True,
    )
    selected: dict[str, Any] | None = None
    score = 0.0
    assigned_by = "rule"
    relation = "updates"
    if scored and scored[0][0] >= auto_gate:
        score, selected = scored[0]
    elif scored and scored[0][0] >= llm_gate and use_llm:
        judged = llm_topic_judge(event, [topic for _, topic in scored[:5]])
        decisions = judged.get("decisions") if isinstance(judged.get("decisions"), list) else []
        if decisions:
            decision = decisions[0]
            target_id = str(decision.get("topic_id") or "")
            selected = next((topic for _, topic in scored if topic.get("topic_id") == target_id), None)
            if selected:
                score = float(decision.get("membership_score") or scored[0][0])
                relation = str(decision.get("relation_to_topic") or relation)
                assigned_by = "llm"
    if selected is None:
        selected = create_topic_for_event(event, asset_links=asset_links, framework_links=framework_links)
        score = 0.55
        relation = "creates"
        assigned_by = "rule"
    else:
        selected = update_topic_from_event(selected, event)
        if event.get("lifecycle_state") == "disputed" or (event.get("core_signature") or {}).get("event_stage") == "denial":
            relation = "contradicts"

    membership = {
        "membership_id": topic_membership_id(event["event_id"], selected["topic_id"], relation),
        "object_id": event["event_id"],
        "topic_id": selected["topic_id"],
        "membership_role": "primary",
        "relation_to_topic": relation,
        "membership_score": round(float(score), 3),
        "assigned_by": assigned_by,
        "agent_run_id": agent_run_id,
        "effective_time": event.get("event_time") or event.get("last_seen_at") or utc_now_iso(),
        "status": "candidate",
        "reason": f"{assigned_by} topic mapping",
    }
    aliases = [
        {"alias_id": topic_alias_id(selected["topic_id"], alias), "topic_id": selected["topic_id"], "alias": alias, "status": "candidate"}
        for alias in [selected.get("canonical_title"), *(event.get("core_entities") or [])]
        if alias
    ]
    return selected, membership, aliases


def create_topic_for_event(
    event: dict[str, Any],
    *,
    asset_links: list[dict[str, Any]] | None = None,
    framework_links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    title = topic_title_for_event(event, asset_links)
    now = utc_now_iso()
    logic_nodes = [link.get("node_id") for link in framework_links or [] if link.get("node_id")]
    included_scope = [link.get("asset_label") for link in asset_links or [] if link.get("asset_label")]
    return {
        "topic_id": stable_topic_id(title, "event_topic"),
        "canonical_title": title,
        "topic_type": "event_topic",
        "definition": event.get("canonical_summary") or title,
        "core_entities": event.get("core_entities") or [],
        "core_event_types": [event.get("event_type") or "other"],
        "core_logic_node_ids": list(dict.fromkeys(logic_nodes)),
        "included_scope": list(dict.fromkeys(included_scope)),
        "excluded_scope": [],
        "first_seen_at": event.get("first_seen_at") or now,
        "last_active_at": event.get("last_seen_at") or now,
        "lifecycle_state": "candidate",
        "heat_score": float(event.get("market_attention") or 0.0),
        "credibility_score": 0.35,
        "status": "candidate",
        "version": 1,
    }


def update_topic_from_event(topic: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    updated = dict(topic)
    updated["last_active_at"] = max(str(updated.get("last_active_at") or ""), str(event.get("last_seen_at") or ""))
    updated["core_entities"] = list(dict.fromkeys([*(updated.get("core_entities") or []), *(event.get("core_entities") or [])]))
    updated["core_event_types"] = list(dict.fromkeys([*(updated.get("core_event_types") or []), event.get("event_type") or "other"]))
    updated["heat_score"] = round(max(float(updated.get("heat_score") or 0.0), float(event.get("market_attention") or 0.0)), 3)
    if updated.get("lifecycle_state") == "candidate" and updated["heat_score"] >= 0.5:
        updated["lifecycle_state"] = "emerging"
    return updated


def llm_topic_judge(event: dict[str, Any], topics: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = _prompt_path("topic_membership_judge.v1.txt").read_text(encoding="utf-8")
    payload = {
        "canonical_event": event,
        "candidate_topics": [
            {
                "topic_id": topic.get("topic_id"),
                "canonical_title": topic.get("canonical_title"),
                "definition": topic.get("definition"),
                "included_scope": topic.get("included_scope"),
                "excluded_scope": topic.get("excluded_scope"),
                "core_entities": topic.get("core_entities"),
                "core_event_types": topic.get("core_event_types"),
            }
            for topic in topics[:5]
        ],
    }
    try:
        return parse_json_object(
            chat(
                f"{prompt}\n\n输入：\n{json.dumps(payload, ensure_ascii=False)}",
                max_tokens=1500,
                temperature=0.1,
                timeout=80,
            )
        )
    except Exception as exc:
        return {"decisions": [], "new_topic_required": False, "abstain": True, "abstain_reason": str(exc)}
