from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.hard_timeout import hard_timeout
from quanta_agents.core.io import atomic_write_text, dated_parts, read_json, relative_to_root, utc_now_iso, write_json
from quanta_agents.core.llm_client import chat, get_provider
from quanta_agents.core.llm_json import parse_json_object, strip_fences


SCHEMA_VERSION = "recent_news_market_topics.v1"
AGENT_VERSION = "0.1.0"
LLM_VERSION = "recent_news_topic_extractor.v1"
DEFAULT_TOPICS = 18
DEFAULT_LLM_EVIDENCE_LIMIT = 0
DEFAULT_LLM_BATCH_SIZE = 15
DEFAULT_LLM_BATCH_WORKERS = 2


DRIVER_TYPES = {
    "geopolitics",
    "monetary_policy",
    "liquidity",
    "trade_policy",
    "supply",
    "demand",
    "inventory",
    "sentiment",
    "policy",
    "production",
    "logistics",
    "market_theme",
}


def _clean_text(value: Any, limit: int | None = None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"<[^>]+>", "", text).replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        return text[: max(limit - 1, 0)].rstrip() + "…"
    return text


def _hash_id(prefix: str, *parts: Any, size: int = 12) -> str:
    raw = "||".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:size].upper()}"


def _relative(path: Path, root: Path) -> str:
    return relative_to_root(path, root)


def _slug(value: Any, *, prefix: str = "MKT_TOPIC") -> str:
    text = str(value or "").strip().upper()
    text = text.removeprefix("MKT-THEME-MACRO-").removeprefix("MKT-THEME-")
    text = text.removeprefix("MKT_TOPIC_").removeprefix("MKT_TOPIC-")
    text = re.sub(r"[^A-Z0-9_]+", "_", text).strip("_")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", text):
        text = hashlib.sha1(str(value or "topic").encode("utf-8")).hexdigest()[:10].upper()
    return f"{prefix}_{text}"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _source_refs(value: Any) -> list[dict[str, str]]:
    refs = []
    for item in _as_list(value):
        if isinstance(item, str):
            refs.append({"ref_type": "flash", "id": item})
        elif isinstance(item, dict):
            ref_type = _clean_text(item.get("ref_type"), 40)
            ref_id = _clean_text(item.get("id"), 120)
            if ref_type and ref_id:
                refs.append({"ref_type": ref_type, "id": ref_id})
    return refs


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def _brief_path(root: Path, period: str) -> Path:
    if period == "hourly":
        return root / "agent_workspace" / "candidates" / "news_brief" / "hourly" / "latest" / "hourly-news-brief.json"
    return root / "agent_workspace" / "candidates" / "news_brief" / "half_day" / "latest" / "half-day-news-brief.json"


def _event_group_key(event: dict[str, Any]) -> str:
    return _clean_text(event.get("flash_id"), 120) or _clean_text(event.get("event_id"), 120)


def _compact_topic_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "event_id": _clean_text(event.get("event_id"), 120),
            "asset": _clean_text(event.get("asset"), 80),
            "dimension": _clean_text(event.get("dimension_label"), 120),
            "direction": event.get("direction_score"),
            "heat": event.get("heat"),
            "consistency": _clean_text(event.get("consistency_status"), 60),
        }.items()
        if value not in (None, "", [])
    }


def _group_graph_events(events: list[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        key = _event_group_key(event)
        if not key:
            continue
        grouped.setdefault(key, []).append(event)
    return grouped


def _event_source_refs(events: list[dict[str, Any]], *, flash_id: str = "") -> list[dict[str, str]]:
    refs = []
    seen = set()
    if flash_id:
        seen.add(("flash", flash_id))
        refs.append({"ref_type": "flash", "id": flash_id})
    for event in events:
        event_id = _clean_text(event.get("event_id"), 120)
        event_flash_id = _clean_text(event.get("flash_id"), 120)
        for ref_type, ref_id in (("event", event_id), ("flash", event_flash_id)):
            if not ref_id or (ref_type, ref_id) in seen:
                continue
            seen.add((ref_type, ref_id))
            refs.append({"ref_type": ref_type, "id": ref_id})
    return refs


def _event_assets(events: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            _clean_text(event.get("asset"), 80)
            for event in events
            if _clean_text(event.get("asset"), 80)
        }
    )


def _event_weight(events: list[dict[str, Any]], *, base: float) -> float:
    if not events:
        return base
    heat = max((_bounded(event.get("heat"), 0.0) for event in events), default=0.0)
    return base + min(0.30, 0.03 * len(events)) + 0.08 * heat


def _evidence_from_brief(root: Path, path: Path, source_type: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = _load_json(path)
    evidence: list[dict[str, Any]] = []
    source_path = _relative(path, root) if path.exists() else str(path)
    graph_events = _as_list(payload.get("graph_trigger_candidates"))
    events_by_flash = _group_graph_events(graph_events)
    consumed_event_keys: set[str] = set()

    for index, flash in enumerate(_as_list(payload.get("top_flashes"))[:40]):
        if not isinstance(flash, dict):
            continue
        text = _clean_text(flash.get("summary") or flash.get("title") or flash.get("text"), 360)
        if len(text) < 8:
            continue
        flash_id = _clean_text(flash.get("flash_id") or flash.get("id"), 120) or _hash_id(
            "FLASH", flash.get("publish_time"), text
        )
        events = events_by_flash.get(flash_id, [])
        consumed_event_keys.add(flash_id)
        assets = _event_assets(events)
        evidence.append(
            {
                "evidence_id": _hash_id("NTEV", source_type, "top_flashes", index, flash_id, text),
                "source_type": source_type,
                "source_path": source_path,
                "source_field": f"top_flashes[{index}]",
                "source_role": "top_flash",
                "object_type": "event_report",
                "truth_status": "unverified",
                "epistemic_status": "event_report",
                "evidence_weight": 0.0,
                "summary": text,
                "asset_refs": assets,
                "source_refs": _event_source_refs(events, flash_id=flash_id),
                "publish_time": _clean_text(flash.get("publish_time"), 40),
                "url": flash.get("url") or "",
                "weight": _event_weight(
                    events,
                    base=0.16 + 0.04 * min(int(flash.get("important") or 0), 2),
                ),
                "structured": {
                    "flash_id": flash_id,
                    "important": int(flash.get("important") or 0),
                    "mapped_event_count": len(events),
                    "events": [_compact_topic_event(event) for event in events[:18]],
                },
            }
        )

    for group_key, events in events_by_flash.items():
        if group_key in consumed_event_keys:
            continue
        first_event = events[0] if events else {}
        flash_id = _clean_text(first_event.get("flash_id"), 120)
        text = _clean_text(first_event.get("text"), 360)
        if len(text) < 8:
            continue
        assets = _event_assets(events)
        evidence.append(
            {
                "evidence_id": _hash_id("NTEV", source_type, "mapped_flash", group_key, text),
                "source_type": source_type,
                "source_path": source_path,
                "source_field": "graph_trigger_candidates",
                "source_role": "mapped_flash",
                "object_type": "event_report",
                "truth_status": "unverified",
                "epistemic_status": "mapped_event",
                "evidence_weight": 0.0,
                "summary": text,
                "asset_refs": assets,
                "source_refs": _event_source_refs(events, flash_id=flash_id),
                "publish_time": _clean_text(first_event.get("publish_time"), 40),
                "weight": _event_weight(events, base=0.18),
                "structured": {
                    "flash_id": flash_id,
                    "mapped_event_count": len(events),
                    "events": [_compact_topic_event(event) for event in events[:18]],
                },
            }
        )

    llm_brief = payload.get("llm_brief") if isinstance(payload.get("llm_brief"), dict) else {}
    field_weights = {
        "overview": 0.20,
        "key_news": 0.30,
        "asset_notes": 0.22,
        "watch_items": 0.18,
        "graph_notes": 0.20,
    }
    for field, weight in field_weights.items():
        for index, item in enumerate(_as_list(llm_brief.get(field))):
            if not isinstance(item, dict):
                continue
            text = _clean_text(item.get("text"), 520)
            refs = _source_refs(item.get("source_refs"))
            if len(text) < 8 or not refs:
                continue
            asset = _clean_text(item.get("asset"), 80)
            evidence.append(
                {
                    "evidence_id": _hash_id("NTEV", source_type, field, index, text),
                    "source_type": source_type,
                    "source_path": source_path,
                    "source_field": f"llm_brief.{field}[{index}]",
                    "source_role": field,
                    "object_type": "derived_summary",
                    "truth_status": "unverified",
                    "epistemic_status": "derived",
                    "evidence_weight": 0.0,
                    "summary": text,
                    "asset_refs": [asset] if asset else [],
                    "source_refs": refs,
                    "weight": weight,
                }
            )

    source_info = {
        "path": source_path,
        "generated_at": payload.get("generated_at") or "",
        "title": payload.get("title") or "",
        "time_window": payload.get("time_window") or {},
        "stats": payload.get("stats") or {},
        "evidence_count": len(evidence),
        "structured_counts": {
            "top_flashes": len(_as_list(payload.get("top_flashes"))),
            "graph_trigger_candidates": len(_as_list(payload.get("graph_trigger_candidates"))),
            "assets": len(_as_list(payload.get("assets"))),
        },
    }
    return evidence, source_info


def _select_llm_evidence(evidence: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    """Keep the LLM prompt small while preserving the strongest local evidence map."""

    if limit <= 0 or len(evidence) <= limit:
        return list(evidence)
    role_priority = {
        "key_news": 100,
        "watch_items": 90,
        "asset_notes": 80,
        "graph_notes": 70,
        "top_flash": 60,
        "mapped_flash": 55,
        "overview": 10,
    }
    ranked = sorted(
        enumerate(evidence),
        key=lambda item: (
            role_priority.get(str(item[1].get("source_role") or ""), 0),
            float(item[1].get("evidence_weight") or 0),
            float(item[1].get("weight") or 0),
            -item[0],
        ),
        reverse=True,
    )
    keep_indices = {index for index, _ in ranked[:limit]}
    return [item for index, item in enumerate(evidence) if index in keep_indices]


def _candidate_pack(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    signals = []
    for item in evidence:
        structured = item.get("structured") if isinstance(item.get("structured"), dict) else {}
        events = []
        for event in _as_list(structured.get("events")):
            if not isinstance(event, dict):
                continue
            row = {
                "id": event.get("event_id"),
                "a": event.get("asset"),
                "d": event.get("dimension"),
                "dir": event.get("direction"),
                "h": event.get("heat"),
                "c": event.get("consistency"),
            }
            events.append({key: value for key, value in row.items() if value not in (None, "", [])})
        signal = {
            "id": item["evidence_id"],
            "r": item["source_role"],
            "t": item.get("publish_time") or "",
            "w": item.get("evidence_weight", 1.0),
            "a": item.get("asset_refs") or [],
            "m": structured.get("mapped_event_count", 0),
            "ev": events,
            "txt": item["summary"],
        }
        signals.append({key: value for key, value in signal.items() if value not in (None, "", [])})
    return {
        "task": "recent_news_market_topic_extraction",
        "schema_version": LLM_VERSION,
        "driver_types": sorted(DRIVER_TYPES),
        "signals": signals,
    }


def _prompt(evidence: list[dict[str, Any]], *, top: int) -> str:
    pack = _candidate_pack(evidence)
    return f"""你是 Quanta 最近新闻主题维护 Agent。

任务：从 signals 中抽取最近新闻主题。每个主题既是新闻热点，也可能是资产驱动逻辑节点。

硬约束：
- 只能使用 signals 中的信息，不能补外部事实。
- 每个主题必须引用 signals[].id 作为 evidence_id；没有证据的主题不要输出。
- 本步骤是 MarketTopic 候选抽取，不是新闻主线总结合并；新闻主线合并由后续 news-mainlines Agent 完成。
- 输出稳定 topic_id / concept_id，不使用当天短标题做 ID。
- 主题是持续维护的 MarketTopic，不是一次性摘要；优先抽取能持续跟踪的热点、政策、地缘、宏观、供需、跨资产驱动。
- 要覆盖主要独立驱动簇。除非窗口确实只有一个事件链，否则不要只输出 1 个总主题；央行/利率、国内政策、地缘供应、库存供需、产业链、风险偏好应拆成独立 topic。
- 自动简报/报告不是独立证据。必须保留 source_refs，让后续能回到 flash/event 等底层来源。
- evidence_weight=0 的候选只能帮助理解和归类，不能被当成独立证据来源。
- 如果某个主题只是资产子主题，也可以保留，但要在 asset_refs 中写明。
- 输入已包含本窗口全部可用的结构化 signals；不要因为候选多而逐条改写，必须聚合成稳定主题。
- signals 字段说明：id 是必须引用的 evidence_id；r 是来源角色；txt 是一条新闻信号；a 是资产；m 是映射事件数；ev 是该新闻信号映射出的事件数组，事件内 a=资产、d=框架维度、dir=方向分、h=热度、c=一致性状态。
- source_refs 已由本地系统保留，不在输入中展开；你只需要引用 evidence_ids，后续系统会回填底层 flash/event 引用。
- 最多输出 {top} 个主题，按重要性排序。

输出严格 JSON，不要 Markdown：
{{
  "topics": [
    {{
      "topic_id": "MKT_TOPIC_STABLE_ID",
      "canonical_name": "中文主题名",
      "topic_type": "geopolitics|monetary_policy|liquidity|trade_policy|supply|demand|inventory|sentiment|policy|production|logistics|market_theme",
      "definition": "一句话定义这个可持续维护的话题",
      "aliases": ["别名1", "别名2"],
      "hotspot_role": true,
      "driver_role": true,
      "driver_refs": ["geopolitics"],
      "asset_refs": ["原油"],
      "evidence_ids": ["NTEV-..."],
      "confidence": 0.0,
      "state": {{
        "heat": 0.0,
        "strength": 0.0,
        "trend": "emerging|strengthening|stable|weakening",
        "change_explanation": "为什么这个主题值得进入状态池"
      }},
      "watch_items": ["后续跟踪点"]
    }}
  ],
  "discarded_evidence": [
    {{"evidence_id": "NTEV-...", "reason": "不构成稳定话题"}}
  ],
  "quality_notes": []
}}

输入：
```json
{json.dumps(pack, ensure_ascii=False, separators=(",", ":"))}
```
"""


def _topic_slim(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "topic_id": raw.get("topic_id") or raw.get("concept_id"),
            "canonical_name": raw.get("canonical_name") or raw.get("title"),
            "topic_type": raw.get("topic_type"),
            "definition": raw.get("definition") or raw.get("reason"),
            "aliases": raw.get("aliases") or [],
            "hotspot_role": raw.get("hotspot_role", True),
            "driver_role": raw.get("driver_role", True),
            "driver_refs": raw.get("driver_refs") or [],
            "asset_refs": raw.get("asset_refs") or [],
            "evidence_ids": raw.get("evidence_ids") or [],
            "confidence": raw.get("confidence"),
            "state": raw.get("state") or {},
            "watch_items": raw.get("watch_items") or [],
        }.items()
        if value not in (None, "", [])
    }


def _merge_prompt(candidates: list[dict[str, Any]], *, top: int) -> str:
    return f"""你是 Quanta MarketTopic 合并 Agent。

任务：把多个 batch 抽出的 topic 候选合并成同一窗口的稳定 MarketTopic。

要求：
- 只能使用输入 candidate_topics 中的信息，不能补外部事实。
- 合并语义相同或同一事件链的 topic，保留并合并 evidence_ids。
- 这是 topic 候选层，不是新闻主线层。不要把独立驱动强行合并：地缘、央行/利率、政策、供需、库存、产业链、风险偏好应分别成 topic。
- 如果输入候选覆盖多个驱动簇，目标输出 4 到 {top} 个 topic；只有候选确实都属于同一事件链时才输出 1 个。
- 输出最多 {top} 个，按市场重要性排序。
- 输出 JSON object，根字段 topics。

输入：
```json
{json.dumps({"candidate_topics": candidates}, ensure_ascii=False, separators=(",", ":"))}
```
"""


def _chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    if size <= 0 or len(items) <= size:
        return [items]
    return [items[index : index + size] for index in range(0, len(items), size)]


def _extract_topic_batches(
    evidence: list[dict[str, Any]],
    *,
    top: int,
    llm_provider: str | None,
    llm_timeout: int,
    batch_size: int,
    batch_workers: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    batches = _chunks(evidence, batch_size)

    def run_batch(batch_index: int, batch: list[dict[str, Any]]) -> dict[str, Any]:
        batch_top = min(top, max(4, min(8, len(batch) // 3 or 1)))
        prompt = _prompt(batch, top=batch_top)
        extra_retry = 0
        try:
            parsed, meta = _chat_topic_json(
                prompt,
                llm_provider=llm_provider,
                llm_timeout=llm_timeout,
                max_tokens=1800,
            )
        except Exception:
            extra_retry = 1
            parsed, meta = _chat_topic_json(
                prompt,
                llm_provider=llm_provider,
                llm_timeout=llm_timeout,
                max_tokens=1800,
            )
        return {
            "batch_index": batch_index,
            "parsed": parsed,
            "retry_count": extra_retry + int(meta.get("retry_count") or 0),
            "parse_error": _clean_text(meta.get("parse_error"), 220),
            "prompt_char_count": len(prompt),
            "prompt_byte_count": len(prompt.encode("utf-8")),
        }

    if len(batches) == 1:
        batch_results = [run_batch(0, batches[0])]
    else:
        workers = max(1, min(batch_workers, len(batches)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(run_batch, batch_index, batch)
                for batch_index, batch in enumerate(batches)
            ]
            batch_results = [future.result() for future in concurrent.futures.as_completed(futures)]
        batch_results.sort(key=lambda item: int(item["batch_index"]))

    parsed_batches = [item["parsed"] for item in batch_results]
    prompt_char_counts = [int(item["prompt_char_count"]) for item in batch_results]
    prompt_byte_counts = [int(item["prompt_byte_count"]) for item in batch_results]
    retry_count = sum(int(item["retry_count"]) for item in batch_results)
    parse_errors = [
        str(item["parse_error"])
        for item in batch_results
        if item.get("parse_error")
    ]

    if len(parsed_batches) == 1:
        return parsed_batches[0], {
            "batch_count": 1,
            "llm_retry_count": retry_count,
            "llm_parse_error": "; ".join(parse_errors),
            "prompt_char_count": prompt_char_counts[0] if prompt_char_counts else 0,
            "prompt_byte_count": prompt_byte_counts[0] if prompt_byte_counts else 0,
            "prompt_total_char_count": sum(prompt_char_counts),
            "prompt_total_byte_count": sum(prompt_byte_counts),
        }

    candidates = []
    for batch_index, parsed in enumerate(parsed_batches):
        for raw in _as_list(parsed.get("topics")):
            if not isinstance(raw, dict):
                continue
            slim = _topic_slim(raw)
            if not slim.get("evidence_ids"):
                continue
            slim["batch_index"] = batch_index
            candidates.append(slim)

    merge_prompt = _merge_prompt(candidates, top=top)
    prompt_char_counts.append(len(merge_prompt))
    prompt_byte_counts.append(len(merge_prompt.encode("utf-8")))
    try:
        merged, merge_meta = _chat_topic_json(
            merge_prompt,
            llm_provider=llm_provider,
            llm_timeout=llm_timeout,
            max_tokens=3200,
        )
        retry_count += int(merge_meta.get("retry_count") or 0)
        if merge_meta.get("parse_error"):
            parse_errors.append(_clean_text(merge_meta.get("parse_error"), 220))
    except Exception as exc:
        merged = {
            "topics": candidates[:top],
            "discarded_evidence": [],
            "quality_notes": [f"Batch merge LLM failed; used ranked batch candidates: {str(exc)[:180]}"],
        }
        parse_errors.append(f"merge_failed: {str(exc)[:180]}")

    return merged, {
        "batch_count": len(batches),
        "llm_retry_count": retry_count,
        "llm_parse_error": "; ".join(parse_errors),
        "prompt_char_count": max(prompt_char_counts) if prompt_char_counts else 0,
        "prompt_byte_count": max(prompt_byte_counts) if prompt_byte_counts else 0,
        "prompt_total_char_count": sum(prompt_char_counts),
        "prompt_total_byte_count": sum(prompt_byte_counts),
    }


def _coerce_topics_payload(parsed: Any) -> dict[str, Any] | None:
    if isinstance(parsed, list):
        if parsed and all(isinstance(item, dict) for item in parsed) and any(
            item.get("topic_id") or item.get("canonical_name") or item.get("evidence_ids")
            for item in parsed
        ):
            return {"topics": parsed}
        return None
    if isinstance(parsed, dict) and not isinstance(parsed.get("topics"), list):
        for key in ("market_topics", "topic_candidates", "themes"):
            if isinstance(parsed.get(key), list):
                parsed["topics"] = parsed[key]
                break
        result = parsed.get("result")
        if not isinstance(parsed.get("topics"), list) and isinstance(result, dict):
            for key in ("topics", "market_topics", "topic_candidates", "themes"):
                if isinstance(result.get(key), list):
                    parsed["topics"] = result[key]
                    break
    if isinstance(parsed, dict) and isinstance(parsed.get("topics"), list):
        return parsed
    if isinstance(parsed, dict) and parsed.get("topic_id"):
        return {"topics": [parsed]}
    return None


def _iter_json_values(text: str) -> list[Any]:
    stripped = strip_fences(text)
    values: list[Any] = []
    try:
        values.append(json.loads(stripped))
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for start, char in enumerate(stripped):
        if char not in "{[":
            continue
        try:
            parsed, _ = decoder.raw_decode(stripped[start:])
        except json.JSONDecodeError:
            continue
        values.append(parsed)
    return values


def _parse_response(text: str) -> dict[str, Any]:
    for parsed in _iter_json_values(text):
        coerced = _coerce_topics_payload(parsed)
        if coerced is not None:
            return coerced
    try:
        parsed = parse_json_object(text)
    except Exception:
        parsed = {}
    if isinstance(parsed, dict):
        keys = ", ".join(list(parsed)[:8])
        raise ValueError(f"LLM output missing topics list; keys={keys}")
    raise ValueError("LLM output missing topics list")


def _repair_prompt(raw_response: str) -> str:
    return f"""你是 JSON 修复器。

下面是另一个模型对 recent_news_market_topic_extraction 的输出，但格式未通过解析。
请只做格式修复：保留原有事实、topic 字段和 evidence_ids，不新增外部信息，不改 evidence_id。

必须输出严格 JSON object，根字段必须包含 topics 数组：
{{"topics":[...],"discarded_evidence":[],"quality_notes":[]}}

原始输出：
```text
{_clean_text(raw_response, 12000)}
```
"""


def _chat_topic_json(
    prompt: str,
    *,
    llm_provider: str | None,
    llm_timeout: int,
    max_tokens: int = 3200,
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_response = ""
    try:
        with hard_timeout(llm_timeout + 5, "LLM recent news topic extraction hard timeout"):
            raw_response = chat(
                prompt,
                provider=llm_provider,
                max_tokens=max_tokens,
                temperature=0.1,
                timeout=llm_timeout,
            )
        return _parse_response(raw_response), {"retry_count": 0, "parse_error": ""}
    except Exception as first_exc:
        parse_error = str(first_exc)[:220]
        if not raw_response:
            raise
        with hard_timeout(llm_timeout + 5, "LLM recent news topic repair hard timeout"):
            repaired = chat(
                _repair_prompt(raw_response),
                provider=llm_provider,
                max_tokens=max_tokens,
                temperature=0.0,
                timeout=llm_timeout,
            )
        return _parse_response(repaired), {"retry_count": 1, "parse_error": parse_error}


def _llm_available(provider: str | None) -> tuple[bool, str]:
    try:
        llm = get_provider(provider)
    except Exception as exc:  # pragma: no cover - defensive environment path.
        return False, str(exc)
    if not llm.api_keys:
        return False, f"missing API key for {llm.name}; set {llm.api_key_hint}"
    return True, f"{llm.name}:{llm.model}"


def _bounded(value: Any, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _evidence_weight(item: dict[str, Any]) -> float:
    try:
        weight = float(item.get("evidence_weight") or 0.0)
    except (TypeError, ValueError):
        weight = 0.0
    return max(0.0, weight)


def _is_derived_context(item: dict[str, Any]) -> bool:
    object_type = str(item.get("object_type") or "")
    epistemic_status = str(item.get("epistemic_status") or "")
    return _evidence_weight(item) <= 0 and (
        object_type in {"derived_summary", "event_report", "derived_report"}
        or epistemic_status in {"derived", "event_report", "mapped_event"}
    )


def _evidence_weight_summary(
    *,
    candidate_evidence: list[dict[str, Any]],
    evidence_links: list[dict[str, Any]],
) -> dict[str, Any]:
    candidate_weights = [_evidence_weight(item) for item in candidate_evidence]
    used_weights = [_evidence_weight(item) for item in evidence_links]
    return {
        "candidate_evidence_count": len(candidate_evidence),
        "candidate_positive_weight_count": sum(1 for weight in candidate_weights if weight > 0),
        "candidate_zero_weight_count": sum(1 for weight in candidate_weights if weight <= 0),
        "used_evidence_count": len(evidence_links),
        "supporting_evidence_count": sum(1 for weight in used_weights if weight > 0),
        "contextual_evidence_count": sum(1 for weight in used_weights if weight <= 0),
        "derived_context_count": sum(1 for item in evidence_links if _is_derived_context(item)),
        "total_evidence_weight": round(sum(used_weights), 3),
    }


def _fallback_topics(evidence: list[dict[str, Any]], *, top: int, reason: str) -> dict[str, Any]:
    topics = []
    for item in evidence[:top]:
        seed = _clean_text(item.get("summary"), 30)
        topics.append(
            {
                "topic_id": _slug(seed),
                "canonical_name": seed,
                "topic_type": "market_theme",
                "definition": seed,
                "aliases": [],
                "hotspot_role": True,
                "driver_role": True,
                "driver_refs": ["market_theme"],
                "asset_refs": item.get("asset_refs") or [],
                "evidence_ids": [item["evidence_id"]],
                "confidence": 0.35,
                "state": {
                    "heat": 0.2,
                    "strength": 0.18,
                    "trend": "emerging",
                    "change_explanation": "模型主题抽取未通过，按单条证据生成低可信调试话题。",
                },
                "watch_items": [],
            }
        )
    return {
        "topics": topics,
        "discarded_evidence": [],
        "quality_notes": [
            "Low-quality rule fallback; do not promote to stable topic state without review.",
            f"Fallback reason: {reason}",
        ],
    }


def _build_payload_from_topics(
    *,
    root: Path,
    evidence: list[dict[str, Any]],
    parsed: dict[str, Any],
    source_refs: dict[str, Any],
    provider_status: str,
    extraction_method: str,
    generated_at: str,
    top: int,
    input_evidence_count: int | None = None,
    prompt_char_count: int | None = None,
    prompt_byte_count: int | None = None,
    prompt_total_char_count: int | None = None,
    prompt_total_byte_count: int | None = None,
    llm_batch_count: int = 1,
    llm_retry_count: int = 0,
    llm_parse_error: str = "",
) -> dict[str, Any]:
    by_id = {item["evidence_id"]: item for item in evidence}
    topic_nodes = []
    topic_states = []
    topic_memberships = []
    evidence_links = []
    topic_events = []
    invalid_ref_count = 0
    used_evidence_counter: Counter[str] = Counter()

    for raw in _as_list(parsed.get("topics"))[:top]:
        if not isinstance(raw, dict):
            continue
        title = _clean_text(raw.get("canonical_name") or raw.get("title"), 100)
        if not title:
            continue
        evidence_ids = [str(item) for item in _as_list(raw.get("evidence_ids")) if str(item) in by_id]
        invalid_ref_count += len([item for item in _as_list(raw.get("evidence_ids")) if str(item) not in by_id])
        if not evidence_ids:
            continue
        topic_id = _slug(raw.get("topic_id") or raw.get("concept_id") or title)
        topic_type = _clean_text(raw.get("topic_type"), 40)
        if topic_type not in DRIVER_TYPES:
            topic_type = "market_theme"
        driver_refs = sorted(
            {
                _clean_text(item, 60)
                for item in _as_list(raw.get("driver_refs"))
                if _clean_text(item, 60)
            }
            or {topic_type}
        )
        assets = sorted(
            {
                _clean_text(item, 80)
                for item in _as_list(raw.get("asset_refs"))
                if _clean_text(item, 80)
            }
        )
        aliases = [
            _clean_text(item, 80)
            for item in _as_list(raw.get("aliases"))
            if _clean_text(item, 80)
        ][:12]
        confidence = _bounded(raw.get("confidence"), 0.6)
        state = raw.get("state") if isinstance(raw.get("state"), dict) else {}
        evidence_items = [by_id[eid] for eid in evidence_ids]
        supporting_evidence_count = sum(1 for item in evidence_items if _evidence_weight(item) > 0)
        contextual_evidence_count = sum(1 for item in evidence_items if _evidence_weight(item) <= 0)
        derived_context_count = sum(1 for item in evidence_items if _is_derived_context(item))
        evidence_weight_sum = round(sum(_evidence_weight(item) for item in evidence_items), 3)
        source_ref_count = len(
            {
                (ref.get("ref_type"), ref.get("id"))
                for item in evidence_items
                for ref in item.get("source_refs", [])
            }
        )
        heat = _bounded(state.get("heat"), min(1.0, 0.12 * len(evidence_ids) + 0.02 * source_ref_count))
        strength = _bounded(state.get("strength"), min(1.0, 0.10 * len(evidence_ids) + 0.03 * source_ref_count))
        trend = _clean_text(state.get("trend"), 32)
        if trend not in {"emerging", "strengthening", "stable", "weakening"}:
            trend = "emerging"

        node = {
            "schema_version": "market_topic.v1",
            "topic_id": topic_id,
            "canonical_name": title,
            "node_kind": "market_topic",
            "hotspot_role": bool(raw.get("hotspot_role", True)),
            "driver_role": bool(raw.get("driver_role", True)),
            "topic_type": topic_type,
            "definition": _clean_text(raw.get("definition") or raw.get("reason"), 260),
            "aliases": aliases,
            "linked_logic_node_ids": [],
            "linked_driver_ids": [],
            "driver_refs": driver_refs,
            "asset_refs": assets,
            "status": "candidate",
            "created_at": generated_at,
            "updated_at": generated_at,
            "source_refs": [{"run_id": "", "extraction_method": extraction_method}],
        }
        topic_nodes.append(node)
        topic_states.append(
            {
                "schema_version": "market_topic_state.v1",
                "topic_id": topic_id,
                "as_of": generated_at,
                "heat": round(heat, 3),
                "strength": round(strength, 3),
                "trend": trend,
                "confidence": round(confidence, 3),
                "dominant_assets": assets[:12],
                "driver_refs": driver_refs,
                "supporting_evidence_count": supporting_evidence_count,
                "contextual_evidence_count": contextual_evidence_count,
                "derived_context_count": derived_context_count,
                "evidence_weight_sum": evidence_weight_sum,
                "contradicting_evidence_count": 0,
                "change_explanation": _clean_text(state.get("change_explanation") or raw.get("reason"), 220),
            }
        )
        topic_events.append(
            {
                "event_type": "topic_observed",
                "topic_id": topic_id,
                "at": generated_at,
                "source": "recent_news_topics",
                "evidence_count": len(evidence_ids),
                "supporting_evidence_count": supporting_evidence_count,
                "derived_context_count": derived_context_count,
            }
        )
        for evidence_id in evidence_ids:
            item = by_id[evidence_id]
            membership_role = "primary" if used_evidence_counter[evidence_id] == 0 else "secondary"
            membership_id = _hash_id("TMEM", topic_id, evidence_id, generated_at)
            evidence_weight = _evidence_weight(item)
            relation_to_topic = "updates" if evidence_weight > 0 else "contextualizes"
            stance = "supports" if relation_to_topic == "updates" else "neutral"
            used_evidence_counter[evidence_id] += 1
            topic_memberships.append(
                {
                    "schema_version": "topic_membership.v1",
                    "membership_id": membership_id,
                    "object_id": evidence_id,
                    "object_type": item.get("object_type") or "evidence_candidate",
                    "topic_id": topic_id,
                    "membership_role": membership_role,
                    "membership_score": round(confidence, 3),
                    "relation_to_topic": relation_to_topic,
                    "stance": stance,
                    "effective_time": item.get("publish_time") or generated_at,
                    "assigned_by": "recent_news_topic_agent",
                    "agent_run_id": "",
                    "method": extraction_method,
                    "status": "active",
                    "reason": _clean_text(raw.get("reason") or state.get("change_explanation"), 220),
                    "source_refs": item.get("source_refs") or [],
                    "truth_status": item.get("truth_status") or "unverified",
                    "epistemic_status": item.get("epistemic_status") or "claim",
                    "evidence_weight": evidence_weight,
                }
            )
            evidence_links.append(
                {
                    "schema_version": "market_topic_evidence_link.v1",
                    "topic_id": topic_id,
                    "evidence_id": evidence_id,
                    "membership_id": membership_id,
                    "object_type": item.get("object_type") or "evidence_candidate",
                    "truth_status": item.get("truth_status") or "unverified",
                    "epistemic_status": item.get("epistemic_status") or "claim",
                    "evidence_weight": evidence_weight,
                    "source_type": item.get("source_type"),
                    "source_path": item.get("source_path"),
                    "source_field": item.get("source_field"),
                    "source_refs": item.get("source_refs") or [],
                    "summary": item.get("summary"),
                    "classification": {
                        "method": extraction_method,
                        "provider": provider_status,
                        "confidence": round(confidence, 3),
                        "reason": _clean_text(raw.get("reason") or state.get("change_explanation"), 220),
                    },
                    "generated_at": generated_at,
                }
            )

    evidence_summary = _evidence_weight_summary(
        candidate_evidence=evidence,
        evidence_links=evidence_links,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "candidate",
        "generated_at": generated_at,
        "agent": {"name": "recent_news_topics", "version": AGENT_VERSION},
        "source_refs": source_refs,
        "extraction": {
            "method": extraction_method,
            "provider": provider_status,
            "version": LLM_VERSION,
            "input_evidence_count": input_evidence_count if input_evidence_count is not None else len(evidence),
            "candidate_evidence_count": len(evidence),
            "prompt_char_count": prompt_char_count,
            "prompt_byte_count": prompt_byte_count,
            "prompt_total_char_count": prompt_total_char_count,
            "prompt_total_byte_count": prompt_total_byte_count,
            "llm_batch_count": llm_batch_count,
            "llm_retry_count": llm_retry_count,
            "llm_parse_error": llm_parse_error,
            "accepted_topic_count": len(topic_nodes),
            "topic_membership_count": len(topic_memberships),
            "evidence_link_count": len(evidence_links),
            "used_evidence_count": len(used_evidence_counter),
            "supporting_evidence_count": evidence_summary["supporting_evidence_count"],
            "derived_context_count": evidence_summary["derived_context_count"],
            "evidence_weight_summary": evidence_summary,
            "invalid_ref_count": invalid_ref_count,
            "discarded_evidence_count": len(parsed.get("discarded_evidence") or []),
            "quality_notes": [
                _clean_text(item, 220)
                for item in _as_list(parsed.get("quality_notes"))
                if _clean_text(item, 220)
            ],
        },
        "topic_nodes": topic_nodes,
        "topic_states": topic_states,
        "topic_memberships": topic_memberships,
        "evidence_links": evidence_links,
        "topic_events": topic_events,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 最近新闻主题候选",
        "",
        f"- 生成时间: {payload.get('generated_at')}",
        f"- 抽取方法: {payload.get('extraction', {}).get('method')} / {payload.get('extraction', {}).get('provider')}",
        f"- 主题数: {len(payload.get('topic_nodes') or [])}",
        f"- TopicMembership: {len(payload.get('topic_memberships') or [])}",
        f"- 证据链接: {len(payload.get('evidence_links') or [])}",
        f"- 支持证据: {payload.get('extraction', {}).get('supporting_evidence_count')}",
        f"- 派生上下文: {payload.get('extraction', {}).get('derived_context_count')}",
        f"- 无效证据引用: {payload.get('extraction', {}).get('invalid_ref_count')}",
        "",
    ]
    states = {item.get("topic_id"): item for item in payload.get("topic_states") or []}
    links_by_topic: dict[str, list[dict[str, Any]]] = {}
    for link in payload.get("evidence_links") or []:
        links_by_topic.setdefault(str(link.get("topic_id")), []).append(link)

    for index, node in enumerate(payload.get("topic_nodes") or [], 1):
        state = states.get(node.get("topic_id")) or {}
        assets = "、".join(node.get("asset_refs") or []) or "宏观/跨资产"
        lines.extend(
            [
                f"## {index}. {node.get('canonical_name')}",
                "",
                f"- topic_id: `{node.get('topic_id')}`",
                f"- topic_type: {node.get('topic_type')}",
                f"- 资产: {assets}",
                f"- 状态: {state.get('trend')} / strength {state.get('strength')} / "
                f"heat {state.get('heat')} / confidence {state.get('confidence')}",
                f"- 证据权重: support {state.get('supporting_evidence_count')} / "
                f"context {state.get('derived_context_count')} / "
                f"weight {state.get('evidence_weight_sum')}",
                f"- 定义: {_clean_text(node.get('definition'), 180)}",
                f"- 变化说明: {_clean_text(state.get('change_explanation'), 180)}",
                "- 关键证据:",
            ]
        )
        for link in links_by_topic.get(str(node.get("topic_id")), [])[:4]:
            refs = "；".join(f"{ref.get('ref_type')}:{ref.get('id')}" for ref in link.get("source_refs") or [])
            lines.append(f"  - {_clean_text(link.get('summary'), 180)}")
            if refs:
                lines.append(f"    - refs: {refs}")
        lines.append("")
    return "\n".join(lines)


def publish_recent_news_topics(
    *,
    root: str | Path | None = None,
    half_day_path: str | Path | None = None,
    hourly_path: str | Path | None = None,
    include_hourly: bool = True,
    top: int = DEFAULT_TOPICS,
    llm_evidence_limit: int = DEFAULT_LLM_EVIDENCE_LIMIT,
    llm_batch_size: int = DEFAULT_LLM_BATCH_SIZE,
    llm_batch_workers: int = DEFAULT_LLM_BATCH_WORKERS,
    llm_provider: str | None = None,
    llm_timeout: int = 120,
    require_llm: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    generated_dt = now or datetime.now()
    generated_at = generated_dt.isoformat(sep=" ")
    half_day = Path(half_day_path).expanduser() if half_day_path else _brief_path(root_path, "half_day")
    hourly = Path(hourly_path).expanduser() if hourly_path else _brief_path(root_path, "hourly")

    evidence, half_day_source = _evidence_from_brief(root_path, half_day, "half_day_news_brief")
    source_refs: dict[str, Any] = {"half_day_news_brief": half_day_source}
    if include_hourly:
        hourly_evidence, hourly_source = _evidence_from_brief(root_path, hourly, "hourly_news_brief")
        evidence.extend(hourly_evidence)
        source_refs["hourly_news_brief"] = hourly_source

    if not evidence:
        raise RuntimeError("未找到可用新闻证据，请先生成 half-day/hourly news brief")

    llm_evidence = _select_llm_evidence(evidence, limit=llm_evidence_limit)

    available, provider_status = _llm_available(llm_provider)
    method = "llm"
    llm_batch_count = 1
    llm_retry_count = 0
    llm_parse_error = ""
    prompt_char_count = 0
    prompt_byte_count = 0
    prompt_total_char_count = 0
    prompt_total_byte_count = 0
    if not available:
        if require_llm:
            raise RuntimeError(f"LLM recent news topic extraction required but unavailable: {provider_status}")
        parsed = _fallback_topics(llm_evidence, top=top, reason=provider_status)
        method = "rule_fallback"
    else:
        try:
            parsed, llm_meta = _extract_topic_batches(
                llm_evidence,
                top=top,
                llm_provider=llm_provider,
                llm_timeout=llm_timeout,
                batch_size=llm_batch_size,
                batch_workers=llm_batch_workers,
            )
            llm_batch_count = int(llm_meta.get("batch_count") or 1)
            llm_retry_count = int(llm_meta.get("llm_retry_count") or 0)
            llm_parse_error = _clean_text(llm_meta.get("llm_parse_error"), 220)
            prompt_char_count = int(llm_meta.get("prompt_char_count") or 0)
            prompt_byte_count = int(llm_meta.get("prompt_byte_count") or 0)
            prompt_total_char_count = int(llm_meta.get("prompt_total_char_count") or 0)
            prompt_total_byte_count = int(llm_meta.get("prompt_total_byte_count") or 0)
        except Exception as exc:
            if require_llm:
                raise RuntimeError(f"LLM recent news topic extraction failed: {str(exc)[:300]}") from exc
            provider_status = f"{provider_status}; failed: {str(exc)[:160]}"
            parsed = _fallback_topics(llm_evidence, top=top, reason=str(exc)[:300])
            method = "rule_fallback"

    payload = _build_payload_from_topics(
        root=root_path,
        evidence=llm_evidence,
        parsed=parsed,
        source_refs=source_refs,
        provider_status=provider_status,
        extraction_method=method,
        generated_at=generated_at,
        top=top,
        input_evidence_count=len(evidence),
        prompt_char_count=prompt_char_count,
        prompt_byte_count=prompt_byte_count,
        prompt_total_char_count=prompt_total_char_count,
        prompt_total_byte_count=prompt_total_byte_count,
        llm_batch_count=llm_batch_count,
        llm_retry_count=llm_retry_count,
        llm_parse_error=llm_parse_error,
    )

    date_key = generated_dt.strftime("%Y%m%d")
    yyyy, mm, dd = dated_parts(date_key)
    stamp = generated_dt.strftime("%H%M%S")
    run_id = f"RUN-RECENT-NEWS-TOPICS-{date_key}-{stamp}"
    payload["run_id"] = run_id
    for node in payload["topic_nodes"]:
        node["source_refs"] = [{"run_id": run_id, "extraction_method": method}]
    for state in payload["topic_states"]:
        state["source_run_ids"] = [run_id]
    for membership in payload["topic_memberships"]:
        membership["agent_run_id"] = run_id
    for link in payload["evidence_links"]:
        link["generated_by_run_id"] = run_id
    for event in payload["topic_events"]:
        event["run_id"] = run_id
    extraction = payload["extraction"]

    base = root_path / "agent_workspace" / "candidates" / "market_topics"
    archive_dir = base / yyyy / mm / dd / run_id
    latest_dir = base / "latest"
    run_dir = root_path / "agent_workspace" / "runs" / "market_topics" / yyyy / mm / dd / run_id
    latest_json = latest_dir / "recent-news-topics.json"
    latest_md = latest_dir / "recent-news-topics.md"
    archive_json = archive_dir / "recent-news-topics.json"
    archive_md = archive_dir / "recent-news-topics.md"
    registry_json = latest_dir / "topic-registry.json"
    states_jsonl = latest_dir / "topic-states.jsonl"
    memberships_jsonl = latest_dir / "topic-memberships.jsonl"
    evidence_jsonl = latest_dir / "topic-evidence.jsonl"
    events_jsonl = latest_dir / "topic-events.jsonl"

    markdown = render_markdown(payload)
    write_json(archive_json, payload)
    atomic_write_text(archive_md, markdown)
    latest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive_json, latest_json)
    shutil.copy2(archive_md, latest_md)
    write_json(
        registry_json,
        {
            "schema_version": "market_topic_registry.v1",
            "status": "candidate",
            "generated_at": generated_at,
            "run_id": run_id,
            "topics": payload["topic_nodes"],
        },
    )
    atomic_write_text(states_jsonl, "\n".join(json.dumps(item, ensure_ascii=False) for item in payload["topic_states"]) + "\n")
    atomic_write_text(
        memberships_jsonl,
        "\n".join(json.dumps(item, ensure_ascii=False) for item in payload["topic_memberships"]) + "\n",
    )
    atomic_write_text(evidence_jsonl, "\n".join(json.dumps(item, ensure_ascii=False) for item in payload["evidence_links"]) + "\n")
    atomic_write_text(events_jsonl, "\n".join(json.dumps(item, ensure_ascii=False) for item in payload["topic_events"]) + "\n")

    manifest = {
        "schema_version": "recent_news_topics_run_manifest.v1",
        "status": "succeeded",
        "run_id": run_id,
        "run_type": "recent_news_topics",
        "started_at": utc_now_iso(),
        "finished_at": utc_now_iso(),
        "inputs": source_refs,
        "outputs": {
            "latest_json": _relative(latest_json, root_path),
            "latest_markdown": _relative(latest_md, root_path),
            "topic_registry": _relative(registry_json, root_path),
            "topic_states_jsonl": _relative(states_jsonl, root_path),
            "topic_memberships_jsonl": _relative(memberships_jsonl, root_path),
            "topic_evidence_jsonl": _relative(evidence_jsonl, root_path),
            "topic_events_jsonl": _relative(events_jsonl, root_path),
            "archive_json": _relative(archive_json, root_path),
            "archive_markdown": _relative(archive_md, root_path),
        },
        "model_refs": [
            {
                "task": "recent_news_market_topic_extraction",
                "method": method,
                "provider": provider_status,
                "version": LLM_VERSION,
            }
        ],
        "metrics": {
            "topic_count": len(payload["topic_nodes"]),
            "topic_membership_count": extraction["topic_membership_count"],
            "evidence_link_count": extraction["evidence_link_count"],
            "supporting_evidence_count": extraction["supporting_evidence_count"],
            "derived_context_count": extraction["derived_context_count"],
            "invalid_ref_count": extraction["invalid_ref_count"],
            "evidence_weight_summary": extraction["evidence_weight_summary"],
        },
        "requires_review": True,
        "generator": {
            "project": "quanta_agents",
            "module": "quanta_agents.market_themes.recent_news_topics",
            "version": AGENT_VERSION,
        },
    }
    write_json(run_dir / "manifest.json", manifest)

    return {
        "run_id": run_id,
        "payload": payload,
        "latest_json": str(latest_json),
        "latest_markdown": str(latest_md),
        "registry_json": str(registry_json),
        "states_jsonl": str(states_jsonl),
        "memberships_jsonl": str(memberships_jsonl),
        "evidence_jsonl": str(evidence_jsonl),
        "events_jsonl": str(events_jsonl),
        "run_manifest": str(run_dir / "manifest.json"),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build recent news MarketTopic candidates.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--half-day-path", help="Explicit half-day news brief JSON path.")
    parser.add_argument("--hourly-path", help="Explicit hourly news brief JSON path.")
    parser.add_argument("--no-hourly", action="store_true", help="Use half-day brief only.")
    parser.add_argument("--top", type=int, default=DEFAULT_TOPICS)
    parser.add_argument("--llm-evidence-limit", type=int, default=DEFAULT_LLM_EVIDENCE_LIMIT)
    parser.add_argument("--llm-batch-size", type=int, default=DEFAULT_LLM_BATCH_SIZE)
    parser.add_argument("--llm-batch-workers", type=int, default=DEFAULT_LLM_BATCH_WORKERS)
    parser.add_argument("--llm-provider", help="LLM provider, e.g. deepseek or m3.")
    parser.add_argument("--llm-timeout", type=int, default=120)
    parser.add_argument("--require-llm", action="store_true")
    args = parser.parse_args(argv)

    result = publish_recent_news_topics(
        root=args.quanta_root,
        half_day_path=args.half_day_path,
        hourly_path=args.hourly_path,
        include_hourly=not args.no_hourly,
        top=args.top,
        llm_evidence_limit=args.llm_evidence_limit,
        llm_batch_size=args.llm_batch_size,
        llm_batch_workers=args.llm_batch_workers,
        llm_provider=args.llm_provider,
        llm_timeout=args.llm_timeout,
        require_llm=args.require_llm,
    )
    payload = result["payload"]
    print(f"run_id: {result['run_id']}")
    print(f"latest_json: {result['latest_json']}")
    print(f"latest_markdown: {result['latest_markdown']}")
    print(f"topic_count: {len(payload['topic_nodes'])}")
    print(f"method: {payload['extraction']['method']} / {payload['extraction']['provider']}")
    print(f"invalid_ref_count: {payload['extraction']['invalid_ref_count']}")
    print(f"supporting_evidence_count: {payload['extraction']['supporting_evidence_count']}")
    print(f"derived_context_count: {payload['extraction']['derived_context_count']}")
    for node, state in zip(payload["topic_nodes"][:8], payload["topic_states"][:8], strict=False):
        print(
            f"- {node['canonical_name']} | {node['topic_id']} | "
            f"{state['trend']} strength={state['strength']} "
            f"support={state['supporting_evidence_count']} context={state['derived_context_count']}"
        )


if __name__ == "__main__":
    main()
