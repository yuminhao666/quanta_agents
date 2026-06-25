from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import queue
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.hard_timeout import hard_timeout
from quanta_agents.core.io import atomic_write_text, dated_parts, relative_to_root, stable_json_dumps, utc_now_iso, write_json
from quanta_agents.core.llm_client import chat, get_provider
from quanta_agents.core.llm_json import parse_json_object, strip_fences
from quanta_agents.market_themes import recent_news_topics, topic_evolution
from quanta_agents.news_brief import hourly as news_brief
from quanta_agents.opinion_radar import db


SCHEMA_VERSION = "news_market_update.v1"
MAINLINE_SCHEMA_VERSION = "news_market_mainlines.v1"
AGENT_VERSION = "0.1.0"
MAINLINE_LLM_VERSION = "news_market_mainline_synthesis.v1"
MAINLINE_ENV_PREFIX = "QUANTA_NEWS_MARKET_UPDATE_LLM"


def _isolated_worker(result_queue: multiprocessing.Queue, func: Callable[..., Any], kwargs: dict[str, Any]) -> None:
    try:
        result_queue.put(("ok", func(**kwargs)))
    except BaseException as exc:  # pragma: no cover - only exercised by child process failures.
        result_queue.put(("error", {"type": exc.__class__.__name__, "message": str(exc)}))


def _terminate_process(process: multiprocessing.Process) -> None:
    if process.is_alive():
        process.terminate()
        process.join(5)
    if process.is_alive():  # pragma: no cover - defensive path for unresponsive children.
        process.kill()
        process.join(5)


def _run_isolated(func: Callable[..., Any], kwargs: dict[str, Any], *, timeout_seconds: int) -> Any:
    """Run a window step in a child process so stuck vendor HTTP reads can be killed."""

    ctx = multiprocessing.get_context("spawn")
    result_queue: multiprocessing.Queue = ctx.Queue(maxsize=1)
    process = ctx.Process(target=_isolated_worker, args=(result_queue, func, kwargs))
    process.start()
    deadline = time.monotonic() + timeout_seconds
    status: str | None = None
    payload: Any = None
    try:
        while True:
            try:
                status, payload = result_queue.get_nowait()
                break
            except queue.Empty:
                if not process.is_alive():
                    process.join(0)
                    try:
                        status, payload = result_queue.get_nowait()
                        break
                    except queue.Empty as exc:
                        raise RuntimeError(
                            f"{func.__module__}.{func.__name__} exited with code "
                            f"{process.exitcode} without returning a result"
                        ) from exc
                if time.monotonic() >= deadline:
                    _terminate_process(process)
                    raise TimeoutError(f"{func.__module__}.{func.__name__} exceeded {timeout_seconds}s")
                time.sleep(0.1)

        # Read the Queue before join. Large half-day/topic payloads can otherwise
        # block the child process in multiprocessing's Queue feeder during exit.
        process.join(5)
        if process.is_alive():
            _terminate_process(process)
        if status == "ok":
            return payload
        raise RuntimeError(f"{payload.get('type')}: {payload.get('message')}")
    finally:
        result_queue.close()


def _emit_progress(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[news-market-update] {message}", flush=True)


def _clean_text(value: Any, limit: int | None = None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        return text[: max(limit - 1, 0)].rstrip() + "…"
    return text


def _hash_id(prefix: str, *parts: Any, size: int = 12) -> str:
    raw = "||".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:size].upper()}"


def _slug(value: Any, *, prefix: str = "NEWS_MAINLINE") -> str:
    text = str(value or "").strip().upper()
    text = text.removeprefix(prefix + "_").removeprefix(prefix + "-")
    text = re.sub(r"[^A-Z0-9_]+", "_", text).strip("_")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", text):
        text = hashlib.sha1(str(value or "mainline").encode("utf-8")).hexdigest()[:10].upper()
    return f"{prefix}_{text}"


def _bounded(value: Any, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _relative(path: Path, root: Path) -> str:
    return relative_to_root(path, root)


def _parse_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return datetime.fromisoformat(text)


def _window_iter(
    start: datetime,
    end: datetime,
    *,
    window_hours: float,
) -> list[tuple[datetime, datetime]]:
    windows = []
    cursor = start
    step = timedelta(hours=window_hours)
    while cursor < end:
        window_end = min(cursor + step, end)
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


def _llm_available(provider: str | None) -> tuple[bool, str]:
    try:
        llm = get_provider(provider, env_prefix=MAINLINE_ENV_PREFIX)
    except Exception as exc:  # pragma: no cover - defensive local env path.
        return False, str(exc)
    if not llm.api_keys:
        return False, f"missing API key for {llm.name}; set {llm.api_key_hint}"
    return True, f"{llm.name}:{llm.model}"


def _latest_observed_snapshot(topic: dict[str, Any]) -> dict[str, Any]:
    snapshots = [item for item in _as_list(topic.get("timeline")) if isinstance(item, dict)]
    return next((item for item in reversed(snapshots) if item.get("observed")), {})


def _topic_pack(evolution_payload: dict[str, Any], *, max_topics: int = 24) -> dict[str, Any]:
    topics = []
    valid_topic_ids = set()
    valid_evidence_refs: set[tuple[str, str]] = set()
    for topic in _as_list(evolution_payload.get("topics")):
        if not isinstance(topic, dict):
            continue
        topic_id = _clean_text(topic.get("topic_id"), 120)
        if not topic_id:
            continue
        snapshot = _latest_observed_snapshot(topic)
        state = snapshot.get("state") if isinstance(snapshot.get("state"), dict) else {}
        evidence_items = []
        for item in _as_list(snapshot.get("primary_evidence"))[:6]:
            if not isinstance(item, dict):
                continue
            object_id = _clean_text(item.get("object_id") or item.get("membership_id"), 160)
            if object_id:
                valid_evidence_refs.add((topic_id, object_id))
            evidence_items.append(
                {
                    "object_id": object_id,
                    "summary": _clean_text(item.get("summary"), 260),
                    "source_refs": item.get("source_refs") or [],
                    "truth_status": item.get("truth_status") or "unverified",
                    "evidence_weight": item.get("evidence_weight", 0.0),
                }
            )
        valid_topic_ids.add(topic_id)
        topics.append(
            {
                "topic_id": topic_id,
                "canonical_name": _clean_text(topic.get("canonical_name"), 120),
                "lifecycle_state": topic.get("lifecycle_state") or "candidate",
                "first_seen": topic.get("first_seen") or "",
                "last_seen": topic.get("last_seen") or "",
                "observation_count": topic.get("observation_count") or 0,
                "state": {
                    "heat": state.get("heat", 0.0),
                    "strength": state.get("strength", 0.0),
                    "confidence": state.get("confidence", 0.0),
                    "trend": state.get("trend") or "",
                    "dominant_assets": state.get("dominant_assets") or [],
                    "driver_refs": state.get("driver_refs") or [],
                    "change_explanation": _clean_text(state.get("change_explanation"), 260),
                },
                "evidence": evidence_items,
            }
        )
    topics.sort(
        key=lambda item: (
            float((item.get("state") or {}).get("heat") or 0),
            float((item.get("state") or {}).get("strength") or 0),
            int(item.get("observation_count") or 0),
        ),
        reverse=True,
    )
    return {
        "topics": topics[:max_topics],
        "valid_topic_ids": sorted(valid_topic_ids),
        "valid_evidence_refs": sorted(f"{topic_id}|{object_id}" for topic_id, object_id in valid_evidence_refs),
    }


def _mainline_prompt(pack: dict[str, Any], *, top: int) -> str:
    return f"""你是 Quanta 新闻主线梳理 Agent。

任务：基于 topic evolution 的结构化话题和证据，合并出最近窗口内真正值得持续跟踪的市场主线。

硬约束：
- 只能使用输入 topics 中的信息，不能补外部事实、价格预测、交易建议或仓位建议。
- 每条主线必须引用 topic_ids；证据引用只能来自该 topic 的 evidence.object_id。
- 自动新闻简报属于派生产物，只能帮助理解；主线证据必须可回到 topic/evidence/source_refs。
- 优先合并同一事件链或同一驱动逻辑下的多个 topic，不要把每条 topic 机械改写成一条主线。
- 最多输出 {top} 条，按重要性排序。

输出严格 JSON，不要 Markdown：
{{
  "mainlines": [
    {{
      "mainline_id": "NEWS_MAINLINE_STABLE_ID",
      "title": "中文主线名",
      "summary": "这条主线当前在交易什么、为何重要",
      "state": "emerging|strengthening|active|cooling|fading",
      "driver_refs": ["geopolitics"],
      "asset_refs": ["原油"],
      "topic_ids": ["MKT_TOPIC_..."],
      "evidence_refs": [{{"topic_id":"MKT_TOPIC_...","object_id":"EV-..."}}],
      "watch_items": ["后续确认点或分歧点"],
      "confidence": 0.0
    }}
  ],
  "quality_notes": []
}}

输入：
```json
{json.dumps({"topics": pack.get("topics") or []}, ensure_ascii=False, indent=2)}
```
"""


def _parse_mainline_response(text: str) -> dict[str, Any]:
    stripped = strip_fences(text)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = parse_json_object(stripped)
    if isinstance(parsed, list):
        parsed = {"mainlines": parsed}
    if isinstance(parsed, dict) and not isinstance(parsed.get("mainlines"), list):
        for key in ("market_mainlines", "themes", "lines"):
            if isinstance(parsed.get(key), list):
                parsed["mainlines"] = parsed[key]
                break
    if not isinstance(parsed, dict) or not isinstance(parsed.get("mainlines"), list):
        raise ValueError("LLM output missing mainlines list")
    return parsed


def _fallback_mainlines(pack: dict[str, Any], *, top: int, reason: str) -> dict[str, Any]:
    mainlines = []
    for topic in _as_list(pack.get("topics"))[:top]:
        if not isinstance(topic, dict):
            continue
        topic_id = topic.get("topic_id")
        state = topic.get("state") if isinstance(topic.get("state"), dict) else {}
        evidence_refs = [
            {"topic_id": topic_id, "object_id": item.get("object_id")}
            for item in _as_list(topic.get("evidence"))[:3]
            if isinstance(item, dict) and item.get("object_id")
        ]
        mainlines.append(
            {
                "mainline_id": _slug(topic_id or topic.get("canonical_name")),
                "title": topic.get("canonical_name") or topic_id,
                "summary": state.get("change_explanation")
                or f"{topic.get('canonical_name') or topic_id} 仍在新闻窗口中被观察到。",
                "state": topic.get("lifecycle_state") or "emerging",
                "driver_refs": state.get("driver_refs") or [],
                "asset_refs": state.get("dominant_assets") or [],
                "topic_ids": [topic_id] if topic_id else [],
                "evidence_refs": evidence_refs,
                "watch_items": [],
                "confidence": state.get("confidence", 0.35),
            }
        )
    return {
        "mainlines": mainlines,
        "quality_notes": [f"LLM mainline synthesis unavailable or failed; used rule fallback: {reason}"],
    }


def _normalize_mainlines(
    parsed: dict[str, Any],
    pack: dict[str, Any],
    *,
    generated_at: str,
    method: str,
    provider_status: str,
    top: int,
) -> dict[str, Any]:
    valid_topics = set(pack.get("valid_topic_ids") or [])
    valid_refs = set(pack.get("valid_evidence_refs") or [])
    topic_by_id = {item["topic_id"]: item for item in _as_list(pack.get("topics")) if isinstance(item, dict)}
    mainlines = []
    invalid_ref_count = 0
    for raw in _as_list(parsed.get("mainlines"))[:top]:
        if not isinstance(raw, dict):
            continue
        topic_ids = [
            _clean_text(item, 120)
            for item in _as_list(raw.get("topic_ids") or raw.get("topics"))
            if _clean_text(item, 120) in valid_topics
        ]
        invalid_ref_count += len(
            [
                item
                for item in _as_list(raw.get("topic_ids") or raw.get("topics"))
                if _clean_text(item, 120) not in valid_topics
            ]
        )
        if not topic_ids:
            continue
        evidence_refs = []
        for ref in _as_list(raw.get("evidence_refs")):
            if not isinstance(ref, dict):
                continue
            topic_id = _clean_text(ref.get("topic_id"), 120)
            object_id = _clean_text(ref.get("object_id") or ref.get("evidence_id"), 160)
            if topic_id in valid_topics and f"{topic_id}|{object_id}" in valid_refs:
                evidence_refs.append({"topic_id": topic_id, "object_id": object_id})
            else:
                invalid_ref_count += 1
        if not evidence_refs:
            for topic_id in topic_ids:
                topic = topic_by_id.get(topic_id) or {}
                for item in _as_list(topic.get("evidence"))[:2]:
                    if isinstance(item, dict) and item.get("object_id"):
                        evidence_refs.append({"topic_id": topic_id, "object_id": item["object_id"]})
        title = _clean_text(raw.get("title") or raw.get("canonical_name"), 120)
        if not title:
            title = " / ".join(
                _clean_text((topic_by_id.get(topic_id) or {}).get("canonical_name"), 60)
                for topic_id in topic_ids[:2]
            )
        driver_refs = sorted(
            {
                _clean_text(item, 60)
                for item in _as_list(raw.get("driver_refs"))
                if _clean_text(item, 60)
            }
        )
        asset_refs = sorted(
            {
                _clean_text(item, 80)
                for item in _as_list(raw.get("asset_refs"))
                if _clean_text(item, 80)
            }
        )
        if not driver_refs:
            driver_refs = sorted(
                {
                    _clean_text(item, 60)
                    for topic_id in topic_ids
                    for item in ((topic_by_id.get(topic_id) or {}).get("state") or {}).get("driver_refs", [])
                    if _clean_text(item, 60)
                }
            )
        if not asset_refs:
            asset_refs = sorted(
                {
                    _clean_text(item, 80)
                    for topic_id in topic_ids
                    for item in ((topic_by_id.get(topic_id) or {}).get("state") or {}).get("dominant_assets", [])
                    if _clean_text(item, 80)
                }
            )
        state = _clean_text(raw.get("state"), 40)
        if state not in {"emerging", "strengthening", "active", "cooling", "fading"}:
            state = "emerging"
        mainlines.append(
            {
                "schema_version": "news_market_mainline.v1",
                "mainline_id": _slug(raw.get("mainline_id") or title),
                "title": title,
                "summary": _clean_text(raw.get("summary") or raw.get("reason"), 520),
                "state": state,
                "driver_refs": driver_refs,
                "asset_refs": asset_refs,
                "topic_ids": topic_ids,
                "evidence_refs": evidence_refs[:12],
                "watch_items": [
                    _clean_text(item, 180)
                    for item in _as_list(raw.get("watch_items"))
                    if _clean_text(item, 180)
                ][:8],
                "confidence": round(_bounded(raw.get("confidence"), 0.55), 3),
                "generated_at": generated_at,
                "classification": {
                    "method": method,
                    "provider": provider_status,
                    "version": MAINLINE_LLM_VERSION,
                },
            }
        )
    return {
        "mainlines": mainlines,
        "invalid_ref_count": invalid_ref_count,
        "quality_notes": [
            _clean_text(item, 220)
            for item in _as_list(parsed.get("quality_notes"))
            if _clean_text(item, 220)
        ],
    }


def _mainline_content_hash(payload: dict[str, Any]) -> str:
    content = {
        key: value
        for key, value in payload.items()
        if key not in {"derived_report", "report_dependencies"}
    }
    raw = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _add_report_dependency(
    dependencies: list[dict[str, Any]],
    seen: set[tuple[str, str, str, str, str, str]],
    *,
    report_id: str,
    input_object_id: str,
    input_object_type: str,
    relation: str,
    dependency_role: str,
    generated_by_run_id: str,
    topic_id: str = "",
    mainline_id: str = "",
) -> None:
    key = (input_object_type, input_object_id, relation, dependency_role, topic_id, mainline_id)
    if not input_object_id or key in seen:
        return
    seen.add(key)
    dependencies.append(
        {
            "schema_version": "report_dependency.v1",
            "dependency_id": _hash_id(
                "RDEP",
                report_id,
                input_object_type,
                input_object_id,
                relation,
                dependency_role,
                topic_id,
                mainline_id,
            ),
            "report_id": report_id,
            "input_object_id": input_object_id,
            "input_object_type": input_object_type,
            "relation": relation,
            "dependency_role": dependency_role,
            "topic_id": topic_id,
            "mainline_id": mainline_id,
            "generated_by_run_id": generated_by_run_id,
        }
    )


def _attach_mainline_report_provenance(
    payload: dict[str, Any],
    *,
    generated_by_run_id: str,
    content_uri: str,
) -> None:
    report_id = _hash_id("REPORT", "news_market_mainlines", generated_by_run_id, content_uri)
    topic_ids = sorted(
        {
            _clean_text(topic_id, 120)
            for mainline in _as_list(payload.get("mainlines"))
            if isinstance(mainline, dict)
            for topic_id in _as_list(mainline.get("topic_ids"))
            if _clean_text(topic_id, 120)
        }
    )
    evidence_ids = sorted(
        {
            _clean_text(ref.get("object_id") or ref.get("evidence_id"), 160)
            for mainline in _as_list(payload.get("mainlines"))
            if isinstance(mainline, dict)
            for ref in _as_list(mainline.get("evidence_refs"))
            if isinstance(ref, dict) and _clean_text(ref.get("object_id") or ref.get("evidence_id"), 160)
        }
    )
    source_run_ids = [
        _clean_text(run_id, 120)
        for run_id in _as_list(payload.get("source_topic_run_ids"))
        if _clean_text(run_id, 120)
    ]
    dependencies: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    for run_id in source_run_ids:
        _add_report_dependency(
            dependencies,
            seen,
            report_id=report_id,
            input_object_id=run_id,
            input_object_type="agent_run",
            relation="used",
            dependency_role="source_topic_run",
            generated_by_run_id=generated_by_run_id,
        )
    for mainline in _as_list(payload.get("mainlines")):
        if not isinstance(mainline, dict):
            continue
        mainline_id = _clean_text(mainline.get("mainline_id"), 120)
        for topic_id in _as_list(mainline.get("topic_ids")):
            clean_topic_id = _clean_text(topic_id, 120)
            _add_report_dependency(
                dependencies,
                seen,
                report_id=report_id,
                input_object_id=clean_topic_id,
                input_object_type="market_topic",
                relation="summarizes",
                dependency_role="topic_context",
                generated_by_run_id=generated_by_run_id,
                topic_id=clean_topic_id,
                mainline_id=mainline_id,
            )
        for ref in _as_list(mainline.get("evidence_refs")):
            if not isinstance(ref, dict):
                continue
            clean_topic_id = _clean_text(ref.get("topic_id"), 120)
            object_id = _clean_text(ref.get("object_id") or ref.get("evidence_id"), 160)
            _add_report_dependency(
                dependencies,
                seen,
                report_id=report_id,
                input_object_id=object_id,
                input_object_type="evidence",
                relation="used",
                dependency_role="mainline_evidence",
                generated_by_run_id=generated_by_run_id,
                topic_id=clean_topic_id,
                mainline_id=mainline_id,
            )

    payload["derived_report"] = {
        "schema_version": "derived_report.v1",
        "report_id": report_id,
        "report_type": "news_market_mainlines",
        "status": payload.get("status") or "candidate",
        "title": "新闻主题主线梳理",
        "period_start": (payload.get("window") or {}).get("start") or "",
        "period_end": (payload.get("window") or {}).get("end") or "",
        "generated_by_run_id": generated_by_run_id,
        "input_event_ids": [],
        "input_claim_ids": [],
        "input_observation_ids": [],
        "input_logic_node_ids": [],
        "input_state_snapshot_ids": [],
        "input_topic_ids": topic_ids,
        "input_evidence_ids": evidence_ids,
        "input_object_ids": source_run_ids + topic_ids + evidence_ids,
        "content_uri": content_uri,
        "content_hash": _mainline_content_hash(payload),
        "prompt_version": MAINLINE_LLM_VERSION,
        "model_version": (payload.get("synthesis") or {}).get("provider") or "",
        "evidence_weight": 0.0,
    }
    payload["report_dependencies"] = dependencies


def _render_mainlines_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 新闻主题主线梳理",
        "",
        f"- 生成时间: {payload.get('generated_at')}",
        f"- 覆盖窗口: {payload.get('window', {}).get('start')} 至 {payload.get('window', {}).get('end')}",
        f"- 来源 topic run: {len(payload.get('source_topic_run_ids') or [])}",
        f"- 主线数: {len(payload.get('mainlines') or [])}",
        f"- 方法: {payload.get('synthesis', {}).get('method')} / {payload.get('synthesis', {}).get('provider')}",
        f"- DerivedReport: `{(payload.get('derived_report') or {}).get('report_id', '')}`",
        f"- ReportDependency: {len(payload.get('report_dependencies') or [])}",
        "",
    ]
    for index, item in enumerate(payload.get("mainlines") or [], 1):
        assets = "、".join(item.get("asset_refs") or []) or "宏观/跨资产"
        drivers = "、".join(item.get("driver_refs") or []) or "market_theme"
        lines.extend(
            [
                f"## {index}. {item.get('title')}",
                "",
                f"- mainline_id: `{item.get('mainline_id')}`",
                f"- 状态: {item.get('state')} / confidence {item.get('confidence')}",
                f"- 资产: {assets}",
                f"- driver: {drivers}",
                f"- 关联话题: {', '.join(item.get('topic_ids') or [])}",
                f"- 概括: {item.get('summary')}",
                "- 证据:",
            ]
        )
        for ref in (item.get("evidence_refs") or [])[:6]:
            lines.append(f"  - {ref.get('topic_id')} / {ref.get('object_id')}")
        watch_items = item.get("watch_items") or []
        if watch_items:
            lines.append("- 后续跟踪:")
            for watch in watch_items[:5]:
                lines.append(f"  - {watch}")
        lines.append("")
    quality_notes = payload.get("synthesis", {}).get("quality_notes") or []
    if quality_notes:
        lines.extend(["## 质量备注", ""])
        for note in quality_notes:
            lines.append(f"- {note}")
    return "\n".join(lines).rstrip() + "\n"


def build_news_mainlines(
    *,
    evolution_payload: dict[str, Any],
    source_topic_run_ids: list[str],
    window: dict[str, Any],
    generated_at: str,
    top: int = 8,
    llm_provider: str | None = None,
    llm_timeout: int = 120,
    use_llm: bool = True,
    require_llm: bool = False,
    news_update_run_id: str = "",
) -> dict[str, Any]:
    pack = _topic_pack(evolution_payload)
    provider_status = "rule_disabled"
    method = "rule_fallback"
    if use_llm and pack.get("topics"):
        available, provider_status = _llm_available(llm_provider)
        if not available:
            if require_llm:
                raise RuntimeError(f"LLM mainline synthesis required but unavailable: {provider_status}")
            parsed = _fallback_mainlines(pack, top=top, reason=provider_status)
        else:
            try:
                with hard_timeout(llm_timeout + 5, "LLM mainline synthesis hard timeout"):
                    raw_response = chat(
                        _mainline_prompt(pack, top=top),
                        provider=llm_provider,
                        max_tokens=2600,
                        temperature=0.15,
                        timeout=llm_timeout,
                        env_prefix=MAINLINE_ENV_PREFIX,
                    )
                parsed = _parse_mainline_response(raw_response)
                method = "llm"
            except Exception as exc:
                if require_llm:
                    raise RuntimeError(f"LLM mainline synthesis failed: {str(exc)[:300]}") from exc
                provider_status = f"{provider_status}; failed: {str(exc)[:160]}"
                parsed = _fallback_mainlines(pack, top=top, reason=str(exc)[:180])
    else:
        parsed = _fallback_mainlines(pack, top=top, reason="llm disabled or no topics")
    normalized = _normalize_mainlines(
        parsed,
        pack,
        generated_at=generated_at,
        method=method,
        provider_status=provider_status,
        top=top,
    )
    return {
        "schema_version": MAINLINE_SCHEMA_VERSION,
        "status": "candidate",
        "generated_at": generated_at,
        "generated_by_run_id": news_update_run_id,
        "window": window,
        "source_topic_run_ids": source_topic_run_ids,
        "source": {
            "topic_evolution_schema": evolution_payload.get("schema_version"),
            "source_run_count": evolution_payload.get("source_run_count"),
            "topic_count": evolution_payload.get("topic_count"),
        },
        "synthesis": {
            "method": method,
            "provider": provider_status,
            "version": MAINLINE_LLM_VERSION,
            "input_topic_count": len(pack.get("topics") or []),
            "mainline_count": len(normalized["mainlines"]),
            "invalid_ref_count": normalized["invalid_ref_count"],
            "quality_notes": normalized["quality_notes"],
        },
        "mainlines": normalized["mainlines"],
    }


def _write_mainlines(
    *,
    root: Path,
    run_dir: Path,
    payload: dict[str, Any],
) -> dict[str, str]:
    latest_dir = root / "agent_workspace" / "candidates" / "market_topics" / "latest"
    run_json = run_dir / "news-mainlines.json"
    run_md = run_dir / "news-mainlines.md"
    run_derived_report = run_dir / "news-mainlines-derived-report.json"
    run_report_dependencies = run_dir / "news-mainlines-report-dependencies.jsonl"
    latest_json = latest_dir / "news-mainlines.json"
    latest_md = latest_dir / "news-mainlines.md"
    latest_derived_report = latest_dir / "news-mainlines-derived-report.json"
    latest_report_dependencies = latest_dir / "news-mainlines-report-dependencies.jsonl"
    generated_by_run_id = _clean_text(payload.get("generated_by_run_id"), 160) or run_dir.name
    _attach_mainline_report_provenance(
        payload,
        generated_by_run_id=generated_by_run_id,
        content_uri=_relative(run_json, root),
    )
    markdown = _render_mainlines_markdown(payload)
    write_json(run_json, payload)
    atomic_write_text(run_md, markdown)
    write_json(run_derived_report, payload["derived_report"])
    dependencies_jsonl = "\n".join(
        json.dumps(item, ensure_ascii=False)
        for item in payload.get("report_dependencies") or []
    )
    atomic_write_text(run_report_dependencies, dependencies_jsonl + ("\n" if dependencies_jsonl else ""))
    write_json(latest_json, payload)
    atomic_write_text(latest_md, markdown)
    write_json(latest_derived_report, payload["derived_report"])
    atomic_write_text(latest_report_dependencies, dependencies_jsonl + ("\n" if dependencies_jsonl else ""))
    return {
        "run_json": str(run_json),
        "run_markdown": str(run_md),
        "run_derived_report": str(run_derived_report),
        "run_report_dependencies": str(run_report_dependencies),
        "latest_json": str(latest_json),
        "latest_markdown": str(latest_md),
        "latest_derived_report": str(latest_derived_report),
        "latest_report_dependencies": str(latest_report_dependencies),
    }


def run_news_market_update(
    *,
    root: str | Path | None = None,
    days: int = 7,
    window_hours: float = 12.0,
    end_time: datetime | None = None,
    use_latest_data_time: bool = True,
    use_llm_brief: bool = True,
    brief_llm_provider: str | None = None,
    topic_llm_provider: str | None = None,
    mainline_llm_provider: str | None = None,
    llm_timeout: int = 120,
    topic_top: int = 12,
    topic_evidence_limit: int = recent_news_topics.DEFAULT_LLM_EVIDENCE_LIMIT,
    topic_batch_size: int = recent_news_topics.DEFAULT_LLM_BATCH_SIZE,
    topic_batch_workers: int = recent_news_topics.DEFAULT_LLM_BATCH_WORKERS,
    mainline_top: int = 8,
    require_topic_llm: bool = True,
    use_llm_mainlines: bool = True,
    require_mainline_llm: bool = False,
    isolate_model_calls: bool = True,
    operation_timeout: int = 120,
    continue_on_error: bool = True,
    emit_progress: bool = False,
) -> dict[str, Any]:
    if days <= 0:
        raise ValueError("days must be positive")
    if window_hours <= 0:
        raise ValueError("window_hours must be positive")

    root_path = quanta_data_root(root)
    latest = end_time or (db.latest_time() if use_latest_data_time else None) or datetime.now()
    start = latest - timedelta(days=days)
    date_key = latest.strftime("%Y%m%d")
    yyyy, mm, dd = dated_parts(date_key)
    run_id = f"RUN-NEWS-MARKET-UPDATE-{start:%Y%m%d}-{latest:%Y%m%d}-{datetime.now():%H%M%S}"
    run_dir = (
        root_path
        / "agent_workspace"
        / "runs"
        / "market_topics"
        / "news_market_update"
        / yyyy
        / mm
        / dd
        / run_id
    )
    started_at = utc_now_iso()
    windows = _window_iter(start, latest, window_hours=window_hours)
    _emit_progress(
        emit_progress,
        f"run {run_id} covers {start.isoformat(sep=' ')} -> {latest.isoformat(sep=' ')} "
        f"with {len(windows)} windows",
    )

    window_summaries: list[dict[str, Any]] = []
    topic_run_ids: list[str] = []
    errors: list[dict[str, Any]] = []
    totals = {
        "raw_flash_count": 0,
        "kept_flash_count": 0,
        "mapped_event_count": 0,
        "topic_count": 0,
        "topic_membership_count": 0,
    }

    for window_index, (window_start, window_end) in enumerate(windows, 1):
        window_hours_actual = (window_end - window_start).total_seconds() / 3600.0
        summary: dict[str, Any] = {
            "window_start": window_start.isoformat(sep=" "),
            "window_end": window_end.isoformat(sep=" "),
            "status": "pending",
        }
        try:
            _emit_progress(
                emit_progress,
                f"window {window_index}/{len(windows)} "
                f"{window_start.isoformat(sep=' ')} -> {window_end.isoformat(sep=' ')} fetch",
            )
            raw_flashes = db.fetch_flashes(window_start, window_end)
            _emit_progress(
                emit_progress,
                f"window {window_index}/{len(windows)} raw_flashes={len(raw_flashes)} brief start",
            )
            brief_kwargs = {
                "root": root_path,
                "hours": window_hours_actual,
                "period": "half_day",
                "end_time": window_end,
                "flashes": raw_flashes,
                "use_llm_brief": use_llm_brief,
                "llm_brief_provider": brief_llm_provider,
                "llm_brief_timeout": llm_timeout,
            }
            brief_result = (
                _run_isolated(
                    news_brief.publish_hourly_news_brief,
                    brief_kwargs,
                    timeout_seconds=operation_timeout,
                )
                if isolate_model_calls
                else news_brief.publish_hourly_news_brief(**brief_kwargs)
            )
            brief_payload = brief_result.get("payload") or {}
            brief_stats = brief_payload.get("stats") or {}
            _emit_progress(
                emit_progress,
                f"window {window_index}/{len(windows)} brief status={brief_result.get('status')} "
                f"kept={brief_stats.get('kept_flash_count', 0)} "
                f"mapped={brief_stats.get('mapped_event_count', 0)}",
            )
            totals["raw_flash_count"] += int(brief_stats.get("raw_flash_count") or 0)
            totals["kept_flash_count"] += int(brief_stats.get("kept_flash_count") or 0)
            totals["mapped_event_count"] += int(brief_stats.get("mapped_event_count") or 0)
            summary.update(
                {
                    "brief_status": brief_result.get("status"),
                    "brief_run_id": brief_result.get("run_id"),
                    "brief_json": _relative(Path(brief_result["paths"]["run_brief_json"]), root_path),
                    "brief_markdown": _relative(
                        Path(brief_result["paths"]["run_brief_markdown"]),
                        root_path,
                    ),
                    "raw_flash_count": brief_stats.get("raw_flash_count", 0),
                    "kept_flash_count": brief_stats.get("kept_flash_count", 0),
                    "mapped_event_count": brief_stats.get("mapped_event_count", 0),
                    "brief_method": (brief_payload.get("llm_brief") or {}).get("method"),
                    "brief_citation_policy_status": (brief_payload.get("llm_brief") or {}).get(
                        "citation_policy_status"
                    ),
                }
            )
            if brief_result.get("status") == "failed":
                summary["status"] = "brief_failed"
                window_summaries.append(summary)
                _emit_progress(
                    emit_progress,
                    f"window {window_index}/{len(windows)} skipped topic because brief failed",
                )
                continue

            _emit_progress(emit_progress, f"window {window_index}/{len(windows)} topic extraction start")
            topic_kwargs = {
                "root": root_path,
                "half_day_path": brief_result["paths"]["run_brief_json"],
                "include_hourly": False,
                "top": topic_top,
                "llm_evidence_limit": topic_evidence_limit,
                "llm_batch_size": topic_batch_size,
                "llm_batch_workers": topic_batch_workers,
                "llm_provider": topic_llm_provider,
                "llm_timeout": llm_timeout,
                "require_llm": require_topic_llm,
                "now": window_end - timedelta(seconds=1),
            }
            topic_result = (
                _run_isolated(
                    recent_news_topics.publish_recent_news_topics,
                    topic_kwargs,
                    timeout_seconds=operation_timeout,
                )
                if isolate_model_calls
                else recent_news_topics.publish_recent_news_topics(**topic_kwargs)
            )
            topic_payload = topic_result["payload"]
            topic_run_ids.append(topic_result["run_id"])
            topic_count = len(topic_payload.get("topic_nodes") or [])
            membership_count = len(topic_payload.get("topic_memberships") or [])
            _emit_progress(
                emit_progress,
                f"window {window_index}/{len(windows)} topic status=succeeded "
                f"topics={topic_count} memberships={membership_count}",
            )
            totals["topic_count"] += topic_count
            totals["topic_membership_count"] += membership_count
            summary.update(
                {
                    "status": "succeeded",
                    "topic_run_id": topic_result["run_id"],
                    "topic_json": _relative(Path(topic_result["latest_json"]), root_path),
                    "topic_count": topic_count,
                    "topic_membership_count": membership_count,
                    "topic_method": topic_payload.get("extraction", {}).get("method"),
                    "topic_provider": topic_payload.get("extraction", {}).get("provider"),
                    "topic_input_evidence_count": topic_payload.get("extraction", {}).get("input_evidence_count"),
                    "topic_candidate_evidence_count": topic_payload.get("extraction", {}).get(
                        "candidate_evidence_count"
                    ),
                    "topic_llm_batch_count": topic_payload.get("extraction", {}).get("llm_batch_count"),
                    "topic_prompt_char_count": topic_payload.get("extraction", {}).get("prompt_char_count"),
                    "topic_prompt_total_char_count": topic_payload.get("extraction", {}).get(
                        "prompt_total_char_count"
                    ),
                    "topic_llm_retry_count": topic_payload.get("extraction", {}).get("llm_retry_count"),
                    "topic_llm_parse_error": topic_payload.get("extraction", {}).get("llm_parse_error"),
                    "invalid_ref_count": topic_payload.get("extraction", {}).get("invalid_ref_count"),
                }
            )
            window_summaries.append(summary)
        except Exception as exc:
            error = {
                "window_start": window_start.isoformat(sep=" "),
                "window_end": window_end.isoformat(sep=" "),
                "error": str(exc)[:600],
            }
            errors.append(error)
            summary.update(error)
            summary["status"] = "failed"
            window_summaries.append(summary)
            _emit_progress(
                emit_progress,
                f"window {window_index}/{len(windows)} failed: {str(exc)[:240]}",
            )
            if not continue_on_error:
                raise

    _emit_progress(emit_progress, f"topic evolution start with {len(topic_run_ids)} topic runs")
    evolution_result = topic_evolution.publish_topic_evolution(
        root=root_path,
        run_ids=topic_run_ids,
        output_dir=run_dir,
        now=latest,
    )
    generated_at = datetime.now().isoformat(sep=" ")
    mainline_kwargs = {
        "evolution_payload": evolution_result["payload"],
        "source_topic_run_ids": topic_run_ids,
        "window": {"start": start.isoformat(sep=" "), "end": latest.isoformat(sep=" ")},
        "generated_at": generated_at,
        "top": mainline_top,
        "llm_provider": mainline_llm_provider,
        "llm_timeout": llm_timeout,
        "use_llm": use_llm_mainlines,
        "require_llm": require_mainline_llm,
        "news_update_run_id": run_id,
    }
    _emit_progress(emit_progress, "news mainline synthesis start")
    mainline_payload = (
        _run_isolated(
            build_news_mainlines,
            mainline_kwargs,
            timeout_seconds=operation_timeout,
        )
        if isolate_model_calls
        else build_news_mainlines(**mainline_kwargs)
    )
    mainline_paths = _write_mainlines(root=root_path, run_dir=run_dir, payload=mainline_payload)
    latest_dir = root_path / "agent_workspace" / "candidates" / "market_topics" / "latest"
    status = "succeeded" if not errors and len(topic_run_ids) == len(windows) else "partial"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "run_id": run_id,
        "generator": {
            "name": "news_market_update",
            "version": AGENT_VERSION,
        },
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "parameters": {
            "days": days,
            "window_hours": window_hours,
            "use_llm_brief": use_llm_brief,
            "brief_llm_provider": brief_llm_provider or "",
            "topic_llm_provider": topic_llm_provider or "",
            "mainline_llm_provider": mainline_llm_provider or "",
            "topic_top": topic_top,
            "topic_evidence_limit": topic_evidence_limit,
            "topic_batch_size": topic_batch_size,
            "topic_batch_workers": topic_batch_workers,
            "mainline_top": mainline_top,
            "require_topic_llm": require_topic_llm,
            "use_llm_mainlines": use_llm_mainlines,
            "require_mainline_llm": require_mainline_llm,
            "isolate_model_calls": isolate_model_calls,
            "operation_timeout": operation_timeout,
        },
        "window": {
            "start": start.isoformat(sep=" "),
            "end": latest.isoformat(sep=" "),
            "timezone": "local_runtime",
        },
        "totals": totals,
        "window_count": len(windows),
        "topic_run_count": len(topic_run_ids),
        "topic_run_ids": topic_run_ids,
        "windows": window_summaries,
        "errors": errors,
        "outputs": {
            "run_dir": _relative(run_dir, root_path),
            "topic_evolution_read_model": _relative(
                Path(evolution_result.get("archive_json") or evolution_result["latest_json"]),
                root_path,
            ),
            "topic_evolution_markdown": _relative(
                Path(evolution_result.get("archive_markdown") or evolution_result["latest_markdown"]),
                root_path,
            ),
            "latest_topic_evolution_read_model": _relative(
                Path(evolution_result["latest_json"]),
                root_path,
            ),
            "latest_topic_evolution_markdown": _relative(
                Path(evolution_result["latest_markdown"]),
                root_path,
            ),
            "news_mainlines_json": _relative(Path(mainline_paths["latest_json"]), root_path),
            "news_mainlines_markdown": _relative(
                Path(mainline_paths["latest_markdown"]),
                root_path,
            ),
            "news_mainlines_derived_report": _relative(
                Path(mainline_paths["latest_derived_report"]),
                root_path,
            ),
            "news_mainlines_report_dependencies": _relative(
                Path(mainline_paths["latest_report_dependencies"]),
                root_path,
            ),
        },
    }
    write_json(run_dir / "manifest.json", manifest)
    write_json(latest_dir / "news-market-update-manifest.json", manifest)
    _emit_progress(
        emit_progress,
        f"finished status={status} topic_run_count={len(topic_run_ids)} errors={len(errors)}",
    )
    return {
        "run_id": run_id,
        "manifest": manifest,
        "manifest_path": str(run_dir / "manifest.json"),
        "latest_manifest_path": str(latest_dir / "news-market-update-manifest.json"),
        "topic_evolution_json": evolution_result.get("archive_json") or evolution_result["latest_json"],
        "topic_evolution_markdown": evolution_result.get("archive_markdown") or evolution_result["latest_markdown"],
        "latest_topic_evolution_json": evolution_result["latest_json"],
        "latest_topic_evolution_markdown": evolution_result["latest_markdown"],
        "news_mainlines_json": mainline_paths["latest_json"],
        "news_mainlines_markdown": mainline_paths["latest_markdown"],
        "news_mainlines_derived_report": mainline_paths["latest_derived_report"],
        "news_mainlines_report_dependencies": mainline_paths["latest_report_dependencies"],
        "mainlines_payload": mainline_payload,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run half-day news brief -> recent topics -> topic evolution -> mainlines."
    )
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--window-hours", type=float, default=12.0)
    parser.add_argument("--end-time", help="Window end time, ISO or YYYY-MM-DD HH:MM:SS.")
    parser.add_argument("--wall-clock-end", action="store_true", help="Use wall-clock now instead of DB max publish_time.")
    parser.add_argument("--no-llm-brief", action="store_true")
    parser.add_argument("--brief-llm-provider", help="LLM provider for half-day brief narration.")
    parser.add_argument("--topic-llm-provider", help="LLM provider for recent topic extraction.")
    parser.add_argument("--mainline-llm-provider", help="LLM provider for mainline synthesis.")
    parser.add_argument("--llm-timeout", type=int, default=120)
    parser.add_argument("--topic-top", type=int, default=12)
    parser.add_argument("--topic-evidence-limit", type=int, default=recent_news_topics.DEFAULT_LLM_EVIDENCE_LIMIT)
    parser.add_argument("--topic-batch-size", type=int, default=recent_news_topics.DEFAULT_LLM_BATCH_SIZE)
    parser.add_argument("--topic-batch-workers", type=int, default=recent_news_topics.DEFAULT_LLM_BATCH_WORKERS)
    parser.add_argument("--mainline-top", type=int, default=8)
    parser.add_argument(
        "--allow-topic-rule-fallback",
        action="store_true",
        help="Allow low-quality rule fallback topics when topic LLM fails.",
    )
    parser.add_argument("--no-llm-mainlines", action="store_true")
    parser.add_argument("--require-mainline-llm", action="store_true")
    parser.add_argument(
        "--no-isolated-model-calls",
        action="store_true",
        help="Debug mode: run model steps in-process instead of killable child processes.",
    )
    parser.add_argument(
        "--operation-timeout",
        type=int,
        default=120,
        help="Hard timeout for each half-day/topic/mainline child process.",
    )
    parser.add_argument("--stop-on-error", action="store_true")
    args = parser.parse_args(argv)

    result = run_news_market_update(
        root=args.quanta_root,
        days=args.days,
        window_hours=args.window_hours,
        end_time=_parse_datetime(args.end_time) if args.end_time else None,
        use_latest_data_time=not args.wall_clock_end,
        use_llm_brief=not args.no_llm_brief,
        brief_llm_provider=args.brief_llm_provider,
        topic_llm_provider=args.topic_llm_provider,
        mainline_llm_provider=args.mainline_llm_provider,
        llm_timeout=args.llm_timeout,
        topic_top=args.topic_top,
        topic_evidence_limit=args.topic_evidence_limit,
        topic_batch_size=args.topic_batch_size,
        topic_batch_workers=args.topic_batch_workers,
        mainline_top=args.mainline_top,
        require_topic_llm=not args.allow_topic_rule_fallback,
        use_llm_mainlines=not args.no_llm_mainlines,
        require_mainline_llm=args.require_mainline_llm,
        isolate_model_calls=not args.no_isolated_model_calls,
        operation_timeout=args.operation_timeout,
        continue_on_error=not args.stop_on_error,
        emit_progress=True,
    )
    manifest = result["manifest"]
    print(f"run_id: {result['run_id']}")
    print(f"manifest: {result['manifest_path']}")
    print(f"latest_manifest: {result['latest_manifest_path']}")
    print(f"topic_evolution_json: {result['topic_evolution_json']}")
    print(f"topic_evolution_markdown: {result['topic_evolution_markdown']}")
    print(f"news_mainlines_json: {result['news_mainlines_json']}")
    print(f"news_mainlines_markdown: {result['news_mainlines_markdown']}")
    print(f"window: {manifest['window']['start']} -> {manifest['window']['end']}")
    print(f"window_count: {manifest['window_count']}")
    print(f"topic_run_count: {manifest['topic_run_count']}")
    print(f"totals: {stable_json_dumps(manifest['totals'], indent=2)}")
    print(f"errors: {len(manifest['errors'])}")


if __name__ == "__main__":
    main()
