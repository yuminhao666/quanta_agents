from __future__ import annotations

import argparse
import hashlib
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.hard_timeout import hard_timeout
from quanta_agents.core.io import (
    atomic_write_text,
    dated_parts,
    relative_to_root,
    stable_json_dumps,
    utc_now_iso,
    write_json,
)
from quanta_agents.core.llm_client import chat, get_provider
from quanta_agents.core.llm_json import parse_json_object
from quanta_agents.opinion_radar import config, db, filters, news_logic


AGENT_NAME = "hourly_news_brief"
AGENT_VERSION = "0.1.0"
LLM_ENV_PREFIX = "QUANTA_HOURLY_NEWS_BRIEF_LLM"
EVENT_FIRST_RE = re.compile(
    r"回应|称|表示|宣布|决定|批准|出台|发布|公布|签署|协议|会谈|谈判|恢复|"
    r"拘留|刑拘|辞职|违法|调查|出口管制|制裁|关税|库存|产量|出货量|"
    r"EIA|API|IFO|ZEW|信通院|外交部|央行|美联储|OPEC|伊朗|稀土"
)
HIGH_VALUE_EVENT_RE = re.compile(
    r"稀土|出口管制|日籍员工|外交部|技术会谈|技术磋商|库存降至|战略石油储备|"
    r"手机出货量|刑事犯罪|刑事拘留|再贷款|操作指引"
)
MARKET_PREFIX_RE = re.compile(
    r"^[^，。；]{1,48}(?:跌幅扩大至?[\d.]*%?|涨幅扩大至?[\d.]*%?|短线下挫|短线拉升)[，,]\s*"
    r"(?=(消息称|报道称|据|因|受))"
)
ROUNDUP_RE = re.compile(r"金十数据整理|每日市场要闻回顾|重要新闻汇总|一览")

PERIODS: dict[str, dict[str, str]] = {
    "hourly": {
        "slug": "hourly",
        "label": "过去一小时",
        "schema_version": "hourly_news_brief.v1",
        "run_prefix": "RUN-HOURLY-NEWS-BRIEF",
        "candidate_prefix": "CAND-NEWS-BRIEF-HOURLY",
        "artifact_base": "hourly-news-brief",
        "run_folder": "hourly_news_brief",
    },
    "half_day": {
        "slug": "half_day",
        "label": "过去半日",
        "schema_version": "half_day_news_brief.v1",
        "run_prefix": "RUN-HALF-DAY-NEWS-BRIEF",
        "candidate_prefix": "CAND-NEWS-BRIEF-HALF-DAY",
        "artifact_base": "half-day-news-brief",
        "run_folder": "half_day_news_brief",
    },
}


def _normalize_period(period: str | None) -> str:
    text = str(period or "hourly").strip().lower().replace("-", "_")
    if text in {"halfday", "half_day", "12h", "12_hour"}:
        return "half_day"
    return "hourly"


def _period(period: str | None) -> dict[str, str]:
    return PERIODS[_normalize_period(period)]


def _hash_id(prefix: str, *parts: Any) -> str:
    raw = "||".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12].upper()}"


def _clean_text(value: Any, limit: int | None = None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"<[^>]+>", "", text).replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        return text[: max(limit - 1, 0)].rstrip() + "…"
    return text


def _flash_text(flash: dict[str, Any], *, limit: int | None = 220) -> str:
    return _clean_text(f"{flash.get('title') or ''} {flash.get('content') or ''}", limit)


def _flash_time(flash: dict[str, Any]) -> str:
    value = flash.get("publish_time") or flash.get("time") or flash.get("created_at") or ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def _flash_key(flash: dict[str, Any]) -> str:
    text = _flash_text(flash, limit=None)
    return str(flash.get("flash_id") or flash.get("id") or _hash_id("NEWS", _flash_time(flash), text))


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


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _window_label(start: datetime, end: datetime) -> dict[str, str]:
    return {
        "start_time": start.isoformat(sep=" "),
        "end_time": end.isoformat(sep=" "),
        "timezone": "local_runtime",
    }


def _compact_flash(flash: dict[str, Any], *, mapped_event_count: int = 0) -> dict[str, Any]:
    return {
        "flash_id": _flash_key(flash),
        "publish_time": _flash_time(flash),
        "important": int(flash.get("important") or 0),
        "channel": flash.get("channel") or "",
        "title": _clean_text(flash.get("title"), 120),
        "summary": _flash_text(flash, limit=260),
        "url": flash.get("url") or "",
        "kept_by_news_filter": filters.keep_flash(flash),
        "mapped_event_count": mapped_event_count,
    }


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    framework_node = event.get("framework_node") or {}
    match = event.get("match") or {}
    consistency = event.get("consistency") or {}
    return {
        "event_id": event.get("event_id") or "",
        "flash_id": event.get("flash_id") or "",
        "publish_time": event.get("publish_time") or "",
        "asset": event.get("asset") or "",
        "asset_id": event.get("asset_id") or "",
        "framework_node_id": framework_node.get("node_id") or "",
        "dimension_label": framework_node.get("dimension_label") or "",
        "direction_score": event.get("direction_score"),
        "heat": event.get("heat"),
        "match_confidence": match.get("confidence"),
        "consistency_status": consistency.get("status") or "",
        "consistency_reason": consistency.get("reason") or "",
        "anchoring_status": event.get("anchoring_status") or "",
        "theme_anchor_refs": event.get("theme_anchor_refs") or [],
        "text": _clean_text(event.get("text"), 240),
    }


def _compact_dimension(row: dict[str, Any]) -> dict[str, Any]:
    top_events = [_compact_event(event) for event in (row.get("top_events") or [])[:3]]
    return {
        "dimension_label": row.get("dimension_label") or "",
        "event_count": int(row.get("event_count") or 0),
        "important_count": int(row.get("important_count") or 0),
        "heat": row.get("heat"),
        "news_direction_score": row.get("news_direction_score"),
        "consistency_counts": row.get("consistency_counts") or {},
        "anchoring_status": row.get("anchoring_status") or "",
        "top_events": top_events,
    }


def _compact_asset(asset: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset": asset,
        "event_count": int(payload.get("event_count") or 0),
        "important_count": int(payload.get("important_count") or 0),
        "heat": payload.get("heat"),
        "news_direction_score": payload.get("news_direction_score"),
        "consistency_counts": payload.get("consistency_counts") or {},
        "anchoring_status": payload.get("anchoring_status") or "",
        "top_dimensions": [
            _compact_dimension(item) for item in (payload.get("dimensions") or [])[:5]
        ],
    }


def _events_by_flash(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(str(event.get("flash_id") or ""), []).append(event)
    return grouped


def _source_refs_text(refs: list[dict[str, Any]]) -> str:
    labels = []
    for ref in refs:
        ref_type = str(ref.get("ref_type") or "")
        ref_id = str(ref.get("id") or "")
        if not ref_type or not ref_id:
            continue
        prefix = "快讯" if ref_type == "flash" else "事件" if ref_type == "event" else ref_type
        labels.append(f"{prefix}:{ref_id}")
    return "；".join(labels)


def _is_reportable_flash(flash: dict[str, Any]) -> bool:
    return filters.keep_news_brief_flash(flash)


def _market_filter_reason(flash: dict[str, Any]) -> str:
    text = _flash_text(flash, limit=None)
    if filters.is_market_update_noise(text):
        return "market_update_noise"
    if not filters.keep_flash(flash):
        return "low_signal_noise"
    return ""


def _candidate_rank(candidate: dict[str, Any]) -> tuple[int, int, str]:
    text = _clean_text(candidate.get("text"), limit=None)
    score = int(candidate.get("importance") or 0) * 20 + int(candidate.get("mapped_event_count") or 0)
    if int(candidate.get("importance") or 0) and not int(candidate.get("mapped_event_count") or 0):
        score += 6
    if EVENT_FIRST_RE.search(text):
        score += 10
    if HIGH_VALUE_EVENT_RE.search(text):
        score += 12
    if MARKET_PREFIX_RE.search(text):
        score -= 6
    if ROUNDUP_RE.search(text):
        score -= 6
    if filters.is_market_update_noise(text):
        score -= 100
    if "将于" in text and "公布" in text:
        score -= 12
    return (score, int(candidate.get("importance") or 0), str(candidate.get("publish_time") or ""))


def _story_signature(text: str) -> str:
    lower = text.lower()
    if ("eia" in lower or "能源信息署" in text) and ("cushing" in lower or "库欣" in text):
        return "eia_cushing_crude_inventory"
    if ("eia" in lower or "能源信息署" in text) and ("spr" in lower or "战略石油储备" in text):
        return "eia_spr_crude_inventory"
    if "巴基斯坦" in text and "伊朗" in text and "会谈" in text:
        return "pakistan_us_iran_talks"
    if ("鲁比奥" in text or "美国国务卿" in text) and "伊朗" in text and ("磋商" in text or "谈判" in text or "会谈" in text):
        return "us_iran_technical_talks"
    if "日籍员工" in text and "稀土" in text:
        return "china_japan_rare_earth_detention"
    if "信通院" in text and "手机出货量" in text:
        return "caict_handset_shipments"
    if "中创环保" in text and "刑事拘留" in text:
        return "zhongchuang_env_chair_detention"
    normalized = re.sub(r"\W+", "", lower)
    return normalized[:80] or _hash_id("STORYSIG", text)


def _chinese_char_count(text: str) -> int:
    return sum(1 for char in text if "\u4e00" <= char <= "\u9fff")


def _merge_source_refs(*ref_lists: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for refs in ref_lists:
        for ref in refs or []:
            ref_type = str(ref.get("ref_type") or "")
            ref_id = str(ref.get("id") or "")
            key = (ref_type, ref_id)
            if ref_type and ref_id and key not in seen:
                seen.add(key)
                out.append({"ref_type": ref_type, "id": ref_id})
    return out


def _story_display_text(text: Any, *, limit: int = 300) -> str:
    clean = _clean_text(text, limit=None)
    clean = MARKET_PREFIX_RE.sub("", clean).strip()
    return _clean_text(clean, limit)


def _add_story_candidate(
    candidates: list[dict[str, Any]],
    *,
    publish_time: Any,
    text: Any,
    importance: int = 0,
    mapped_event_count: int = 0,
    asset_refs: list[str] | None = None,
    dimension_refs: list[str] | None = None,
    source_refs: list[dict[str, str]] | None = None,
) -> None:
    clean_text = _story_display_text(text, limit=300)
    refs = source_refs or []
    if not clean_text or not refs:
        return
    candidates.append(
        {
            "candidate_id": _hash_id("NSTORY", publish_time, clean_text, refs),
            "publish_time": str(publish_time or ""),
            "text": clean_text,
            "importance": importance,
            "mapped_event_count": mapped_event_count,
            "asset_refs": asset_refs or [],
            "dimension_refs": dimension_refs or [],
            "source_refs": refs,
        }
    )


def _event_story_candidates(payload: dict[str, Any], *, limit: int = 18) -> list[dict[str, Any]]:
    events_by_flash: dict[str, list[dict[str, Any]]] = {}
    for event in payload.get("graph_trigger_candidates") or []:
        flash_id = str(event.get("flash_id") or "")
        if flash_id:
            events_by_flash.setdefault(flash_id, []).append(event)

    candidates: list[dict[str, Any]] = []
    seen_events: set[str] = set()
    seen_flashes: set[str] = set()
    for flash in ((payload.get("alerts") or {}).get("unmapped_important_news") or []):
        flash_id = str(flash.get("flash_id") or "")
        if not flash_id:
            continue
        seen_flashes.add(flash_id)
        _add_story_candidate(
            candidates,
            publish_time=flash.get("publish_time"),
            text=flash.get("summary"),
            importance=int(flash.get("important") or 0),
            mapped_event_count=0,
            source_refs=[{"ref_type": "flash", "id": flash_id}],
        )
    for flash in payload.get("top_flashes") or []:
        flash_id = str(flash.get("flash_id") or "")
        if flash_id in seen_flashes:
            continue
        seen_flashes.add(flash_id)
        events = events_by_flash.get(flash_id, [])
        source_refs = [{"ref_type": "flash", "id": flash_id}] if flash_id else []
        for event in events[:3]:
            event_id = str(event.get("event_id") or "")
            if event_id:
                source_refs.append({"ref_type": "event", "id": event_id})
                seen_events.add(event_id)
        _add_story_candidate(
            candidates,
            publish_time=flash.get("publish_time"),
            text=flash.get("summary"),
            importance=int(flash.get("important") or 0),
            mapped_event_count=len(events),
            asset_refs=sorted({str(event.get("asset") or "") for event in events if event.get("asset")}),
            dimension_refs=sorted(
                {str(event.get("dimension_label") or "") for event in events if event.get("dimension_label")}
            ),
            source_refs=source_refs,
        )

    for event in payload.get("graph_trigger_candidates") or []:
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in seen_events:
            continue
        source_refs = [{"ref_type": "event", "id": event_id}]
        flash_id = str(event.get("flash_id") or "")
        if flash_id:
            source_refs.insert(0, {"ref_type": "flash", "id": flash_id})
        _add_story_candidate(
            candidates,
            publish_time=event.get("publish_time"),
            text=event.get("text"),
            importance=0,
            mapped_event_count=1,
            asset_refs=[event.get("asset")] if event.get("asset") else [],
            dimension_refs=[event.get("dimension_label")] if event.get("dimension_label") else [],
            source_refs=source_refs,
        )

    ranked = sorted(candidates, key=_candidate_rank, reverse=True)
    return ranked[:limit]


def _rule_key_news_items(payload: dict[str, Any], *, limit: int = 6) -> list[dict[str, Any]]:
    by_signature: dict[str, dict[str, Any]] = {}
    for candidate in _event_story_candidates(payload, limit=max(limit * 3, limit)):
        refs = candidate.get("source_refs") or []
        text = _clean_text(candidate.get("text"), 360)
        if filters.is_market_update_noise(text):
            continue
        if not text or not refs:
            continue
        signature = _story_signature(text)
        existing = by_signature.get(signature)
        if existing:
            existing_text = str(existing.get("text") or "")
            if _chinese_char_count(text) > _chinese_char_count(existing_text):
                existing["text"] = text
            existing["source_refs"] = _merge_source_refs(existing.get("source_refs") or [], refs)
        else:
            by_signature[signature] = {"text": text, "source_refs": refs}
    return list(by_signature.values())[:limit]


def _rule_watch_items(payload: dict[str, Any], *, limit: int = 3) -> list[dict[str, Any]]:
    items = []
    for candidate in _event_story_candidates(payload, limit=limit):
        refs = candidate.get("source_refs") or []
        text = _clean_text(candidate.get("text"), 160)
        if filters.is_market_update_noise(text):
            continue
        if text and refs:
            items.append(
                {
                    "text": f"跟踪该事件是否出现官方确认、执行细节或反向澄清：{text}",
                    "source_refs": refs,
                }
            )
    return items


def _paragraph_sentence(text: Any, *, limit: int = 92) -> str:
    clean = _story_display_text(text, limit=180)
    clean = re.sub(r"^【([^】]{4,60})】.*", r"\1", clean)
    clean = clean.split("。")[0].split("；")[0].strip()
    clean = re.sub(r"金十数据\d+月\d+日讯，?", "", clean)
    clean = re.sub(r"据多家外媒\d+日报道，?", "", clean)
    return _clean_text(clean, limit)


def _rule_key_news_paragraph(payload: dict[str, Any], *, limit: int = 4) -> dict[str, Any] | None:
    items = _rule_key_news_items(payload, limit=limit)
    if not items:
        return None
    texts = [_paragraph_sentence(item.get("text")) for item in items if item.get("text")]
    texts = [text for text in texts if text]
    refs = _merge_source_refs(*(item.get("source_refs") or [] for item in items))
    if not texts or not refs:
        return None
    return {
        "text": "过去半日，" + "；".join(texts) + "。",
        "source_refs": refs,
    }


def _brief_title(start: datetime, end: datetime, *, period: str = "hourly") -> str:
    config_row = _period(period)
    noun = "新闻简报" if config_row["slug"] == "hourly" else "新闻汇总"
    return f"{config_row['label']}{noun}（{start:%Y-%m-%d %H:%M} - {end:%H:%M}）"


def _llm_evidence_pack(payload: dict[str, Any]) -> dict[str, Any]:
    period_slug = str((payload.get("period") or {}).get("slug") or "hourly")
    flash_limit = 36 if period_slug == "half_day" else 14
    event_limit = 48 if period_slug == "half_day" else 30
    return {
        "time_window": payload.get("time_window") or {},
        "stats": payload.get("stats") or {},
        "pre_filter": (payload.get("source") or {}).get("news_brief_filter") or {},
        "important_event_candidates": _event_story_candidates(payload, limit=18 if period_slug == "half_day" else 10),
        "top_flashes": [
            {
                "flash_id": item.get("flash_id"),
                "publish_time": item.get("publish_time"),
                "important": item.get("important"),
                "summary": item.get("summary"),
                "mapped_event_count": item.get("mapped_event_count"),
                "source_refs": [{"ref_type": "flash", "id": item.get("flash_id")}]
                if item.get("flash_id")
                else [],
            }
            for item in (payload.get("top_flashes") or [])[:flash_limit]
        ],
        "assets": [
            {
                "asset": item.get("asset"),
                "event_count": item.get("event_count"),
                "heat": item.get("heat"),
                "news_direction_score": item.get("news_direction_score"),
                "consistency_counts": item.get("consistency_counts"),
                "top_dimensions": [
                    {
                        "dimension_label": dim.get("dimension_label"),
                        "event_count": dim.get("event_count"),
                        "news_direction_score": dim.get("news_direction_score"),
                        "heat": dim.get("heat"),
                    }
                    for dim in (item.get("top_dimensions") or [])[:3]
                ],
            }
            for item in (payload.get("assets") or [])[:10]
        ],
        "graph_trigger_candidates": [
            {
                "event_id": item.get("event_id"),
                "flash_id": item.get("flash_id"),
                "publish_time": item.get("publish_time"),
                "asset": item.get("asset"),
                "dimension_label": item.get("dimension_label"),
                "direction_score": item.get("direction_score"),
                "heat": item.get("heat"),
                "consistency_status": item.get("consistency_status"),
                "text": item.get("text"),
            }
            for item in (payload.get("graph_trigger_candidates") or [])[:event_limit]
        ],
        "unmapped_important_news": (payload.get("alerts") or {}).get("unmapped_important_news") or [],
    }


def _fallback_narrative(payload: dict[str, Any], *, method: str, error: str = "") -> dict[str, Any]:
    key_news = _rule_key_news_items(payload)
    paragraph = _rule_key_news_paragraph(payload)
    watch_items = _rule_watch_items(payload)
    refs = paragraph["source_refs"] if paragraph else key_news[0]["source_refs"] if key_news else []
    summary = (payload.get("summary") or {}).get("headline") or "本窗口暂无可总结新闻。"
    return {
        "schema_version": "hourly_news_brief_narrative.v1",
        "method": method,
        "provider": "",
        "model": "",
        "generated_at": utc_now_iso(),
        "citation_policy_status": "fallback_rule_refs" if refs else "no_source_items",
        "overview": [
            {
                "text": summary,
                "source_refs": refs,
            }
        ],
        "key_news_paragraph": paragraph,
        "key_news": key_news,
        "asset_notes": [],
        "watch_items": watch_items,
        "graph_notes": [],
        "discarded_uncited_items": [],
        "quality_notes": [error] if error else [],
    }


def _normalize_source_refs(
    refs: Any,
    *,
    allowed_flash_ids: set[str],
    allowed_event_ids: set[str],
) -> list[dict[str, str]]:
    if not isinstance(refs, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        if isinstance(ref, str):
            ref_type = ""
            ref_id = ref.strip()
        elif isinstance(ref, dict):
            ref_type = str(ref.get("ref_type") or ref.get("type") or "").strip()
            ref_id = str(
                ref.get("id")
                or ref.get("source_id")
                or ref.get("flash_id")
                or ref.get("event_id")
                or ""
            ).strip()
        else:
            continue
        ref_id = ref_id.removeprefix("快讯:").removeprefix("flash:").removeprefix("flash_id:")
        ref_id = ref_id.removeprefix("事件:").removeprefix("event:").removeprefix("event_id:")
        if not ref_type:
            if ref_id in allowed_flash_ids:
                ref_type = "flash"
            elif ref_id in allowed_event_ids:
                ref_type = "event"
        if ref_type not in {"flash", "event"} or not ref_id:
            continue
        if ref_type == "flash" and ref_id not in allowed_flash_ids:
            continue
        if ref_type == "event" and ref_id not in allowed_event_ids:
            continue
        key = (ref_type, ref_id)
        if key in seen:
            continue
        seen.add(key)
        out.append({"ref_type": ref_type, "id": ref_id})
    return out


def _normalize_llm_items(
    items: Any,
    *,
    allowed_flash_ids: set[str],
    allowed_event_ids: set[str],
    discarded: list[dict[str, Any]],
    extra_keys: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if isinstance(item, str):
            row = {"text": item, "source_refs": []}
        elif isinstance(item, dict):
            row = dict(item)
        else:
            continue
        text = _clean_text(row.get("text") or row.get("summary") or row.get("point"), 420)
        refs = _normalize_source_refs(
            row.get("source_refs"),
            allowed_flash_ids=allowed_flash_ids,
            allowed_event_ids=allowed_event_ids,
        )
        if not text:
            continue
        if not refs:
            discarded.append({"text": text, "reason": "missing_or_invalid_source_refs"})
            continue
        clean = {"text": text, "source_refs": refs}
        for key in extra_keys:
            value = row.get(key)
            if value not in (None, ""):
                clean[key] = _clean_text(value, 120) if isinstance(value, str) else value
        out.append(clean)
    return out


def _normalize_llm_paragraph(
    item: Any,
    *,
    allowed_flash_ids: set[str],
    allowed_event_ids: set[str],
    discarded: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if isinstance(item, str):
        row = {"text": item, "source_refs": []}
    elif isinstance(item, dict):
        row = dict(item)
    else:
        return None
    text = _clean_text(row.get("text") or row.get("summary") or row.get("paragraph"), 900)
    refs = _normalize_source_refs(
        row.get("source_refs"),
        allowed_flash_ids=allowed_flash_ids,
        allowed_event_ids=allowed_event_ids,
    )
    if not text:
        return None
    if not refs:
        discarded.append({"text": text, "reason": "missing_or_invalid_source_refs"})
        return None
    return {"text": text, "source_refs": refs}


def _generate_focused_llm_paragraph(
    payload: dict[str, Any],
    *,
    allowed_flash_ids: set[str],
    allowed_event_ids: set[str],
    provider: str | None,
    timeout: int,
    discarded: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    candidates = _event_story_candidates(payload, limit=10)
    if not candidates:
        return None, ""
    prompt = (
        "你是 Quanta 新闻简报 Agent。请只基于 important_event_candidates 生成一段给普通用户看的重点新闻正文。\n"
        "要求：\n"
        "1. 只输出 JSON object，不要 Markdown。\n"
        "2. 字段必须是 key_news_paragraph: {text, source_refs}。\n"
        "3. text 写一段 180-320 字中文，合并同类事件，不要分点，不要复制长标题。\n"
        "4. 不写行情涨跌、报价、收盘、技术面评论；只写政策、库存、供应、地缘、监管、产业、公司事件。\n"
        "5. source_refs 必须从候选里的 source_refs 原样复制，至少 2 条。\n\n"
        "JSON 结构：\n"
        '{"key_news_paragraph":{"text":"一段重点新闻正文","source_refs":[...]}}\n\n'
        "important_event_candidates:\n"
        + stable_json_dumps(candidates, indent=2)
    )
    with hard_timeout(timeout + 5, "Focused LLM news paragraph hard timeout"):
        raw_response = chat(
            prompt,
            max_tokens=1100,
            temperature=0.2,
            timeout=timeout,
            provider=provider,
            env_prefix=LLM_ENV_PREFIX,
        )
    parsed = parse_json_object(raw_response)
    paragraph = _normalize_llm_paragraph(
        parsed.get("key_news_paragraph") or parsed,
        allowed_flash_ids=allowed_flash_ids,
        allowed_event_ids=allowed_event_ids,
        discarded=discarded,
    )
    return paragraph, _hash_id("LLMRESP", raw_response)


def generate_llm_news_brief(
    payload: dict[str, Any],
    *,
    provider: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    allowed_flash_ids = {
        str(item.get("flash_id") or "")
        for item in (payload.get("top_flashes") or [])
        if item.get("flash_id")
    }
    allowed_flash_ids.update(
        str(item.get("flash_id") or "")
        for item in ((payload.get("alerts") or {}).get("unmapped_important_news") or [])
        if item.get("flash_id")
    )
    allowed_event_ids = {
        str(item.get("event_id") or "")
        for item in (payload.get("graph_trigger_candidates") or [])
        if item.get("event_id")
    }
    allowed_flash_ids.update(
        str(item.get("flash_id") or "")
        for item in (payload.get("graph_trigger_candidates") or [])
        if item.get("flash_id")
    )
    for asset in payload.get("assets") or []:
        for dimension in asset.get("top_dimensions") or []:
            allowed_flash_ids.update(
                str(item.get("flash_id") or "")
                for item in dimension.get("top_events") or []
                if item.get("flash_id")
            )
    if not allowed_flash_ids and not allowed_event_ids:
        return _fallback_narrative(payload, method="rule_no_source_items")

    provider_meta = get_provider(provider, env_prefix=LLM_ENV_PREFIX)
    evidence_pack = _llm_evidence_pack(payload)
    period_label = str((payload.get("period") or {}).get("label") or "过去一小时")
    prompt = (
        f"你是 Quanta 新闻简报 Agent。请把 evidence_pack 改写成适合人类阅读的{period_label}新闻汇总。\n"
        "写作目标：过滤行情类资讯，优先梳理窗口内发生的现实重要事件，做合并同类项和事件链聚类，并提示值得继续跟踪的动向；"
        "不要把每条快讯逐条翻译成列表。\n"
        "严格要求：\n"
        "1. 只能使用 evidence_pack 中出现的信息，禁止补充外部事实、价格目标、交易建议或仓位建议。\n"
        "2. key_news_paragraph 是最重要字段，必须输出；它是给普通用户看的正文，只写一段 180-360 字重点新闻，不要分点。\n"
        "3. key_news 只写现实事件：政策、央行、库存、供应、地缘、监管、产业、公司或官方表态。\n"
        "4. 禁止把价格涨跌、收盘播报、现报、跌破/站上、主力合约涨跌、技术面/多空评论、基差/报价数据更新写入 key_news_paragraph 或 key_news；"
        "这些属于 MarketObservation，不是新闻事件。\n"
        "5. 优先从 evidence_pack.important_event_candidates 选择重点事件，并把同一事件链合并成一条。\n"
        "6. key_news_paragraph 必须带 source_refs，至少引用 2 条 flash 或 event；每一个 overview/key_news/asset_notes/watch_items/graph_notes 条目也必须带 source_refs。\n"
        "7. source_refs 只能从 evidence_pack 中已有的 source_refs 复制 flash_id 或 event_id，格式为 "
        '{"ref_type":"flash","id":"..."} 或 {"ref_type":"event","id":"..."}。\n'
        "8. watch_items 用来提示后续最值得关注的动向、确认点或分歧点，不要写泛泛而谈的观察。\n"
        "9. asset_notes 是可选栏目：只有 evidence_pack.assets 存在且新闻和资产/维度映射清楚时才写；"
        "证据不足时返回空数组，不要为了填栏目硬写资产观察。\n"
        "10. graph_notes 是可选栏目：只有 graph_trigger_candidates 存在且触发关系清楚时才写；"
        "证据不足时返回空数组。\n"
        "11. 输出必须是 JSON object，不要 Markdown。\n\n"
        "JSON 结构：\n"
        "{\n"
        '  "key_news_paragraph":{"text":"一段重点新闻正文","source_refs":[...]},\n'
        '  "overview":[{"text":"概括本窗口的主线和分歧","source_refs":[...]}],\n'
        '  "key_news":[{"text":"聚类后的重点新闻或事件链","source_refs":[...]}],\n'
        '  "asset_notes":[{"asset":"铜","text":"可选：资产相关观察","source_refs":[...]}],\n'
        '  "watch_items":[{"text":"值得继续跟踪的动向或确认点","source_refs":[...]}],\n'
        '  "graph_notes":[{"text":"可选：可触发的图谱节点/维度","source_refs":[...]}],\n'
        '  "quality_notes":["口径限制或需要复核处"]\n'
        "}\n\n"
        "evidence_pack:\n"
        + stable_json_dumps(evidence_pack, indent=2)
    )
    with hard_timeout(timeout + 5, "LLM news brief generation hard timeout"):
        raw_response = chat(
            prompt,
            max_tokens=2200,
            temperature=0.2,
            timeout=timeout,
            provider=provider,
            env_prefix=LLM_ENV_PREFIX,
        )
    parsed = parse_json_object(raw_response)
    discarded: list[dict[str, Any]] = []
    narrative = {
        "schema_version": "hourly_news_brief_narrative.v1",
        "method": "llm",
        "provider": provider_meta.name,
        "model": provider_meta.model,
        "generated_at": utc_now_iso(),
        "citation_policy_status": "all_items_cited",
        "overview": _normalize_llm_items(
            parsed.get("overview"),
            allowed_flash_ids=allowed_flash_ids,
            allowed_event_ids=allowed_event_ids,
            discarded=discarded,
        ),
        "key_news_paragraph": _normalize_llm_paragraph(
            parsed.get("key_news_paragraph")
            or parsed.get("key_news_text")
            or parsed.get("paragraph")
            or parsed.get("summary_paragraph"),
            allowed_flash_ids=allowed_flash_ids,
            allowed_event_ids=allowed_event_ids,
            discarded=discarded,
        ),
        "key_news": _normalize_llm_items(
            parsed.get("key_news"),
            allowed_flash_ids=allowed_flash_ids,
            allowed_event_ids=allowed_event_ids,
            discarded=discarded,
        ),
        "asset_notes": _normalize_llm_items(
            parsed.get("asset_notes"),
            allowed_flash_ids=allowed_flash_ids,
            allowed_event_ids=allowed_event_ids,
            discarded=discarded,
            extra_keys=("asset",),
        ),
        "watch_items": _normalize_llm_items(
            parsed.get("watch_items"),
            allowed_flash_ids=allowed_flash_ids,
            allowed_event_ids=allowed_event_ids,
            discarded=discarded,
        ),
        "graph_notes": _normalize_llm_items(
            parsed.get("graph_notes"),
            allowed_flash_ids=allowed_flash_ids,
            allowed_event_ids=allowed_event_ids,
            discarded=discarded,
        ),
        "discarded_uncited_items": discarded,
        "quality_notes": parsed.get("quality_notes") if isinstance(parsed.get("quality_notes"), list) else [],
        "raw_response_hash": _hash_id("LLMRESP", raw_response),
    }
    if not (payload.get("assets") or []) and narrative["asset_notes"]:
        for item in narrative["asset_notes"]:
            discarded.append({"text": item.get("text"), "reason": "no_structured_asset_mapping"})
        narrative["asset_notes"] = []
    if not (payload.get("graph_trigger_candidates") or []) and narrative["graph_notes"]:
        for item in narrative["graph_notes"]:
            discarded.append({"text": item.get("text"), "reason": "no_graph_trigger_candidate"})
        narrative["graph_notes"] = []
    if not narrative["key_news"]:
        rule_key_news = _rule_key_news_items(payload)
        if rule_key_news:
            narrative["key_news"] = rule_key_news
            narrative["quality_notes"].append("LLM 未生成可用重点事件，已用规则事件候选补齐。")
    if not narrative.get("key_news_paragraph"):
        focused_discarded: list[dict[str, Any]] = []
        try:
            paragraph, paragraph_hash = _generate_focused_llm_paragraph(
                payload,
                allowed_flash_ids=allowed_flash_ids,
                allowed_event_ids=allowed_event_ids,
                provider=provider,
                timeout=min(timeout, 90),
                discarded=focused_discarded,
            )
            if paragraph:
                narrative["key_news_paragraph"] = paragraph
                narrative["paragraph_response_hash"] = paragraph_hash
        except Exception as exc:
            narrative["quality_notes"].append(f"focused paragraph generation failed: {str(exc)[:120]}")
        if focused_discarded:
            discarded.extend(focused_discarded)
        paragraph = narrative.get("key_news_paragraph") or _rule_key_news_paragraph(payload)
        if paragraph:
            narrative["key_news_paragraph"] = paragraph
            if not narrative.get("paragraph_response_hash"):
                narrative["quality_notes"].append("LLM 未生成可用重点新闻段落，已用规则事件候选补齐。")
    if not narrative["watch_items"]:
        rule_watch_items = _rule_watch_items(payload)
        if rule_watch_items:
            narrative["watch_items"] = rule_watch_items
            narrative["quality_notes"].append("LLM 未生成可用后续关注，已用规则事件候选补齐。")
    if discarded:
        narrative["citation_policy_status"] = "discarded_uncited_items"
    if not any(narrative.get(key) for key in ("overview", "key_news", "asset_notes", "watch_items", "graph_notes")):
        fallback = _fallback_narrative(
            payload,
            method="llm_failed_citation_validation",
            error="LLM output had no usable cited items.",
        )
        fallback["provider"] = provider_meta.name
        fallback["model"] = provider_meta.model
        fallback["discarded_uncited_items"] = discarded
        fallback["raw_response_hash"] = narrative["raw_response_hash"]
        return fallback
    return narrative


def build_hourly_news_brief(
    flashes: list[dict[str, Any]],
    root: str | Path | None = None,
    *,
    start_time: datetime,
    end_time: datetime,
    period: str = "hourly",
    logic_run: str | Path | None = None,
    theme_anchor_path: str | Path | None = None,
    use_llm_brief: bool = True,
    llm_brief_provider: str | None = None,
    llm_brief_timeout: int = 120,
    use_llm_assessment: bool = False,
    llm_asset_limit: int = 40,
    top_flash_limit: int = 16,
    top_asset_limit: int = 20,
    top_event_limit: int = 80,
    unmapped_important_limit: int = 8,
) -> dict[str, Any]:
    """Build a traceable news intermediate artifact.

    The brief is deliberately not a final market conclusion. It records the requested
    news window, the rule/optional-LLM news_logic mapping, and graph-trigger candidates
    that later state-machine agents can replay from event ids and flash ids.
    """
    root_path = quanta_data_root(root)
    period_cfg = _period(period)
    date_key = end_time.strftime("%Y%m%d")
    safe_flashes = [_json_safe(flash) for flash in flashes]
    filtered_flashes = [
        flash for flash in safe_flashes if not _is_reportable_flash(flash)
    ]
    filtered_market_updates = [
        flash for flash in filtered_flashes if _market_filter_reason(flash) == "market_update_noise"
    ]
    reportable_flashes = [
        flash for flash in safe_flashes if _is_reportable_flash(flash)
    ]
    logic_payload = news_logic.build_news_logic_radar(
        reportable_flashes,
        root_path,
        logic_run=logic_run,
        date_key=date_key,
        theme_anchor_path=theme_anchor_path,
        use_llm_assessment=use_llm_assessment,
        llm_asset_limit=llm_asset_limit,
    )
    events = list(logic_payload.get("events") or [])
    event_groups = _events_by_flash(events)
    kept_flashes = [flash for flash in reportable_flashes if filters.keep_flash(flash)]
    top_flashes = sorted(
        kept_flashes,
        key=lambda item: (
            int(item.get("important") or 0),
            len(event_groups.get(_flash_key(item), [])),
            _flash_time(item),
        ),
        reverse=True,
    )[:top_flash_limit]
    assets = [
        _compact_asset(asset, payload)
        for asset, payload in sorted(
            (logic_payload.get("assets") or {}).items(),
            key=lambda item: float((item[1] or {}).get("heat") or 0),
            reverse=True,
        )[:top_asset_limit]
    ]
    compact_events = [_compact_event(event) for event in events[:top_event_limit]]
    mapped_flash_ids = {str(event.get("flash_id") or "") for event in events}
    important_flashes = [
        _compact_flash(flash, mapped_event_count=len(event_groups.get(_flash_key(flash), [])))
        for flash in top_flashes
        if int(flash.get("important") or 0)
    ]
    unmapped_important = [
        _compact_flash(flash, mapped_event_count=0)
        for flash in kept_flashes
        if int(flash.get("important") or 0) and _flash_key(flash) not in mapped_flash_ids
    ][:unmapped_important_limit]
    consistency_counts = Counter(
        str((event.get("consistency") or {}).get("status") or "") for event in events
    )
    payload = {
        "schema_version": period_cfg["schema_version"],
        "status": "machine_published_intermediate",
        "generated_at": utc_now_iso(),
        "title": _brief_title(start_time, end_time, period=period_cfg["slug"]),
        "period": {
            "slug": period_cfg["slug"],
            "label": period_cfg["label"],
            "hours": round((end_time - start_time).total_seconds() / 3600, 3),
        },
        "source_role": "news",
        "source_type": "mysql_flash",
        "time_window": _window_label(start_time, end_time),
        "source": {
            "database": config.MYSQL.get("database") or "",
            "table": config.FLASH_TABLE,
            "query_policy": "publish_time >= start_time and publish_time < end_time",
            "news_brief_filter": {
                "policy": "exclude_market_update_noise_from_human_brief",
                "raw_flash_count": len(safe_flashes),
                "reportable_flash_count": len(reportable_flashes),
                "filtered_flash_count": len(filtered_flashes),
                "filtered_market_update_count": len(filtered_market_updates),
                "note": "价格涨跌、收盘播报、现报、技术/多空评论和报价数据更新不进入半日报重点事件。",
            },
        },
        "stats": {
            "raw_flash_count": len(safe_flashes),
            "kept_flash_count": len(kept_flashes),
            "reportable_flash_count": len(reportable_flashes),
            "filtered_flash_count": len(filtered_flashes),
            "filtered_market_update_count": len(filtered_market_updates),
            "mapped_event_count": len(events),
            "mapped_flash_count": len(mapped_flash_ids),
            "asset_count": len(logic_payload.get("assets") or {}),
            "important_flash_count": sum(1 for flash in safe_flashes if int(flash.get("important") or 0)),
            "reportable_important_flash_count": sum(
                1 for flash in reportable_flashes if int(flash.get("important") or 0)
            ),
            "filtered_important_market_update_count": sum(
                1 for flash in filtered_market_updates if int(flash.get("important") or 0)
            ),
            "consistency_counts": dict(consistency_counts),
            "news_logic_stats": logic_payload.get("stats") or {},
        },
        "summary": {
            "headline": _summary_headline(
                safe_flashes,
                kept_flashes,
                assets,
                compact_events,
                period_label=period_cfg["label"],
            ),
            "top_assets": [asset["asset"] for asset in assets[:8]],
            "dominant_consistency": consistency_counts.most_common(1)[0][0]
            if consistency_counts
            else "insufficient",
        },
        "top_flashes": [
            _compact_flash(flash, mapped_event_count=len(event_groups.get(_flash_key(flash), [])))
            for flash in top_flashes
        ],
        "assets": assets,
        "graph_trigger_candidates": compact_events,
        "alerts": {
            "important_news": important_flashes[:8],
            "unmapped_important_news": unmapped_important,
            "filtered_market_updates": [
                _compact_flash(flash, mapped_event_count=0)
                for flash in filtered_market_updates[:8]
            ],
            "high_heat_assets": [
                asset for asset in assets if float(asset.get("heat") or 0) >= 1.0
            ][:8],
        },
        "logic_ref": {
            "schema_version": logic_payload.get("schema_version"),
            "logic_context": logic_payload.get("logic_context") or {},
            "analysis_framework_ref": logic_payload.get("analysis_framework_ref") or {},
            "theme_anchor_context": logic_payload.get("theme_anchor_context") or {},
            "semantic_layer": logic_payload.get("semantic_layer") or {},
        },
        "llm_brief": {},
        "constraints": [
            "该产物是新闻中间结构化产物，不直接生成投资结论。",
            "图谱触发候选必须继续通过 asset_event_state / causal graph 进入 driver state。",
            "所有可计算影响均保留 event_id、flash_id、framework_node_id 以便回放。",
            "人类可读叙事由模型基于 evidence_pack 生成；未携带有效 source_refs 的模型文字不会进入正式报告。",
        ],
        "next_step": {
            "recommended_agent": "quanta-asset-event-state",
            "input_role": "news_intermediate",
            "expected_action": "convert news_logic events into canonical events, then update graph and driver state",
        },
    }
    if use_llm_brief:
        try:
            payload["llm_brief"] = generate_llm_news_brief(
                payload,
                provider=llm_brief_provider,
                timeout=llm_brief_timeout,
            )
        except Exception as exc:
            payload["llm_brief"] = _fallback_narrative(
                payload,
                method="llm_failed",
                error=f"LLM news brief generation failed: {exc}",
            )
    else:
        payload["llm_brief"] = _fallback_narrative(payload, method="rule_disabled")
    payload["_runtime"] = {"news_logic_payload": logic_payload}
    return payload


def _summary_headline(
    raw_flashes: list[dict[str, Any]],
    kept_flashes: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    period_label: str = "过去一小时",
) -> str:
    if not raw_flashes:
        return f"{period_label}未读取到快讯。"
    if not kept_flashes:
        return f"{period_label}读取到 {len(raw_flashes)} 条快讯，过滤后暂无可进入新闻逻辑层的事件。"
    if not events:
        return f"{period_label}保留 {len(kept_flashes)} 条快讯，但暂未映射到商品资产框架。"
    names = "、".join(asset["asset"] for asset in assets[:5] if asset.get("asset"))
    return (
        f"{period_label}保留 {len(kept_flashes)} 条快讯，映射出 {len(events)} 个图谱触发候选"
        + (f"，主要涉及 {names}。" if names else "。")
    )


def render_hourly_news_brief_markdown(payload: dict[str, Any]) -> str:
    stats = payload.get("stats") or {}
    window = payload.get("time_window") or {}
    narrative = payload.get("llm_brief") or {}
    filtered_count = int(stats.get("filtered_market_update_count") or 0)
    lines = [
        f"# {payload.get('title') or '过去一小时新闻简报'}",
        "",
        f"- 窗口：{window.get('start_time')} 至 {window.get('end_time')}（{window.get('timezone')}）",
        f"- 生成时间：{payload.get('generated_at')}",
        f"- 快讯：raw {stats.get('raw_flash_count', 0)} / reportable {stats.get('reportable_flash_count', stats.get('kept_flash_count', 0))} / filtered market {filtered_count}",
        f"- 模型：{narrative.get('provider') or 'fallback'} / {narrative.get('model') or narrative.get('method') or 'rule'}",
        "- 定位：窗口内重要新闻事件梳理；行情播报已从正文中过滤，不构成投资建议。",
        "",
        "## 概览",
        "",
    ]
    overview_items = narrative.get("overview") or []
    if overview_items:
        for item in overview_items:
            refs = _source_refs_text(item.get("source_refs") or [])
            lines.append(f"- {item.get('text')}" + (f"（证据：{refs}）" if refs else ""))
    else:
        lines.append(str((payload.get("summary") or {}).get("headline") or ""))

    paragraph = narrative.get("key_news_paragraph") if isinstance(narrative.get("key_news_paragraph"), dict) else None
    lines.extend(["", "## 重点新闻", ""])
    if paragraph and paragraph.get("text"):
        refs = _source_refs_text(paragraph.get("source_refs") or [])
        lines.append(str(paragraph.get("text")) + (f"（证据：{refs}）" if refs else ""))
    else:
        lines.append("- 暂无。")

    def append_narrative_section(title: str, items: list[dict[str, Any]], *, asset_prefix: bool = False) -> None:
        lines.extend(["", f"## {title}", ""])
        if not items:
            lines.append("- 暂无。")
            return
        for item in items:
            refs = _source_refs_text(item.get("source_refs") or [])
            prefix = f"{item.get('asset')}：" if asset_prefix and item.get("asset") else ""
            lines.append(f"- {prefix}{item.get('text')}" + (f"（证据：{refs}）" if refs else ""))

    append_narrative_section("事件要点", narrative.get("key_news") or [])
    append_narrative_section("品种观察", narrative.get("asset_notes") or [], asset_prefix=True)
    append_narrative_section("后续跟踪", narrative.get("watch_items") or [])

    quality_notes = narrative.get("quality_notes") or []
    discarded = narrative.get("discarded_uncited_items") or []
    if quality_notes or discarded or filtered_count:
        lines.extend(["", "## 溯源质量", ""])
        if filtered_count:
            lines.append(f"- 已从正文中过滤 {filtered_count} 条行情播报、报价/收盘或技术面评论。")
        for note in quality_notes[:5]:
            lines.append(f"- {note}")
        if discarded:
            lines.append(f"- 模型生成了 {len(discarded)} 条缺少有效引用的内容，已从正式简报中剔除。")

    cited_flash_ids = {
        str(ref.get("id") or "")
        for item in (narrative.get("key_news") or []) + (narrative.get("watch_items") or [])
        for ref in (item.get("source_refs") or [])
        if ref.get("ref_type") == "flash" and ref.get("id")
    }
    alerts = payload.get("alerts") or {}
    all_top_flashes = (payload.get("top_flashes") or []) + (alerts.get("unmapped_important_news") or [])
    seen_source_flashes: set[str] = set()
    all_top_flashes = [
        flash
        for flash in all_top_flashes
        if not (
            str(flash.get("flash_id") or "") in seen_source_flashes
            or seen_source_flashes.add(str(flash.get("flash_id") or ""))
        )
    ]
    top_flashes = [
        flash for flash in all_top_flashes if str(flash.get("flash_id") or "") in cited_flash_ids
    ] or all_top_flashes[:8]
    lines.extend(["", "## 来源摘录", ""])
    if top_flashes:
        for flash in top_flashes[:12]:
            mapped = flash.get("mapped_event_count", 0)
            lines.append(
                f"- [{flash.get('publish_time')}] {flash.get('summary')}"
                + (f"（关联事件 {mapped} 个）" if mapped else "")
            )
    else:
        lines.append("- 本窗口暂无可展示的重要事件来源。")

    unmapped = alerts.get("unmapped_important_news") or []
    unmapped = [flash for flash in unmapped if str(flash.get("flash_id") or "") not in cited_flash_ids]
    if unmapped:
        lines.extend(["", "## 待复核", ""])
        for flash in unmapped[:6]:
            lines.append(f"- {flash.get('summary')} ({flash.get('flash_id')})")
    return "\n".join(lines).rstrip() + "\n"


def _candidate_manifest(
    *,
    candidate_id: str,
    run_id: str,
    created_at: str,
    title: str,
    raw_flashes_ref: str,
    json_ref: str,
    markdown_ref: str,
    news_logic_ref: str,
    asset_scope: list[str],
    importance_score: float,
    model_name: str,
    period_slug: str,
    period_label: str,
) -> dict[str, Any]:
    return {
        "schema_version": "candidate_manifest.v1",
        "candidate_id": candidate_id,
        "candidate_type": "opinion_radar",
        "lifecycle_state": "machine_published",
        "created_at": created_at,
        "updated_at": created_at,
        "generated_by": {
            "project": "quanta_agents",
            "run_id": run_id,
            "agent_name": AGENT_NAME,
            "model": model_name,
        },
        "title": title,
        "asset_scope": asset_scope,
        "theme_scope": [],
        "claim_summary": f"{period_label} news intermediate artifact for graph-trigger candidate generation.",
        "content_refs": [
            {"ref_type": f"{period_slug}_news_brief_json", "path": json_ref, "id": candidate_id},
            {"ref_type": f"{period_slug}_news_brief_markdown", "path": markdown_ref, "id": candidate_id},
            {"ref_type": "news_logic_snapshot", "path": news_logic_ref, "id": run_id},
        ],
        "evidence_refs": [
            {
                "evidence_id": f"{run_id}-RAW-FLASHES",
                "path": raw_flashes_ref,
                "stance": "source",
                "strength": "medium",
                "snippet_ref": None,
            }
        ],
        "prompt_pack_ref": None,
        "confidence_weight": None,
        "novelty_score": None,
        "importance_score": importance_score,
        "conflicts": [],
        "relation_to_existing": [],
        "human_review_required": True,
        "promotion_policy": "display_only",
        "expires_at": None,
        "review_refs": [],
    }


def _run_manifest(
    *,
    run_id: str,
    created_at: str,
    completed_at: str,
    status: str,
    root_path: Path,
    run_dir: Path,
    raw_flashes_path: Path,
    brief_json_path: Path,
    brief_md_path: Path,
    news_logic_path: Path,
    candidate_manifest_path: Path,
    candidate_json_path: Path,
    errors: list[dict[str, str]],
    model_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "agent_run_manifest.v1",
        "run_id": run_id,
        "run_type": "market_summary",
        "status": status,
        "created_at": created_at,
        "completed_at": completed_at,
        "trading_date": None,
        "owner_project": "quanta_agents",
        "agent": {
            "name": AGENT_NAME,
            "version": AGENT_VERSION,
            "git_commit": None,
            "runtime": "python",
        },
        "environment_ref": {
            "quanta_data_root_env": "GJ_QUANTA_DATA_ROOT",
            "platform_api_env": "GJ_PLATFORM_API_BASE",
            "server_role": "pipeline_agent",
        },
        "input_refs": [
            {
                "ref_type": "external",
                "id": "opinion_radar_flash_mysql",
                "path": f"mysql://{config.MYSQL.get('database') or ''}.{config.FLASH_TABLE}",
                "hash": None,
                "lineage_id": None,
            }
        ],
        "output_refs": [
            {
                "ref_type": "log",
                "id": f"{run_id}-RAW-FLASHES",
                "path": relative_to_root(raw_flashes_path, root_path),
                "hash": None,
                "lineage_id": None,
            },
            {
                "ref_type": "log",
                "id": f"{run_id}-NEWS-LOGIC",
                "path": relative_to_root(news_logic_path, root_path),
                "hash": None,
                "lineage_id": None,
            },
            {
                "ref_type": "candidate",
                "id": run_id,
                "path": relative_to_root(brief_json_path, root_path),
                "hash": None,
                "lineage_id": None,
            },
            {
                "ref_type": "candidate",
                "id": run_id,
                "path": relative_to_root(brief_md_path, root_path),
                "hash": None,
                "lineage_id": None,
            },
        ],
        "prompt_pack_ref": None,
        "evidence_refs": [],
        "candidate_refs": [
            {
                "ref_type": "candidate",
                "id": run_id,
                "path": relative_to_root(candidate_manifest_path, root_path),
                "hash": None,
                "lineage_id": None,
            },
            {
                "ref_type": "candidate",
                "id": run_id,
                "path": relative_to_root(candidate_json_path, root_path),
                "hash": None,
                "lineage_id": None,
            },
        ],
        "review_package_ref": None,
        "model_refs": model_refs,
        "config_refs": [],
        "logs": [
            {
                "ref_type": "log",
                "id": f"{run_id}-DIR",
                "path": relative_to_root(run_dir, root_path),
                "hash": None,
                "lineage_id": None,
            }
        ],
        "human_review_required": True,
        "promotion_target": "machine_published",
        "errors": errors,
    }


def publish_hourly_news_brief(
    root: str | Path | None = None,
    *,
    hours: float = 1.0,
    period: str = "hourly",
    end_time: datetime | None = None,
    use_latest_data_time: bool = False,
    logic_run: str | Path | None = None,
    theme_anchor_path: str | Path | None = None,
    flashes: list[dict[str, Any]] | None = None,
    use_llm_brief: bool = True,
    llm_brief_provider: str | None = None,
    llm_brief_timeout: int = 120,
    use_llm_assessment: bool = False,
    llm_asset_limit: int = 40,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    period_cfg = _period(period)
    if end_time is None and use_latest_data_time:
        end_time = db.latest_time()
    end = end_time or datetime.now()
    start = end - timedelta(hours=hours)
    created_at = utc_now_iso()
    errors: list[dict[str, str]] = []
    raw_flashes = flashes if flashes is not None else db.fetch_flashes(start, end)
    date_key = end.strftime("%Y%m%d")
    yyyy, mm, dd = dated_parts(date_key)
    stamp = end.strftime("%Y%m%d-%H%M%S")
    run_id = f"{period_cfg['run_prefix']}-{stamp}"
    candidate_id = f"{period_cfg['candidate_prefix']}-{stamp}"
    run_dir = (
        root_path
        / "agent_workspace"
        / "runs"
        / "news"
        / period_cfg["run_folder"]
        / yyyy
        / mm
        / dd
        / run_id
    )
    candidate_dir = (
        root_path
        / "agent_workspace"
        / "candidates"
        / "news_brief"
        / period_cfg["slug"]
        / yyyy
        / mm
        / dd
        / candidate_id
    )
    latest_dir = root_path / "agent_workspace" / "candidates" / "news_brief" / period_cfg["slug"] / "latest"
    raw_flashes_path = run_dir / "raw_flashes.json"
    news_logic_path = run_dir / "news_logic.json"
    artifact_base = period_cfg["artifact_base"]
    brief_json_path = run_dir / f"{artifact_base}.json"
    brief_md_path = run_dir / f"{artifact_base}.md"
    candidate_json_path = candidate_dir / f"{artifact_base}.json"
    candidate_md_path = candidate_dir / f"{artifact_base}.md"
    candidate_manifest_path = candidate_dir / "manifest.json"
    latest_json_path = latest_dir / f"{artifact_base}.json"
    latest_md_path = latest_dir / f"{artifact_base}.md"
    latest_manifest_path = latest_dir / "manifest.json"

    logic_payload: dict[str, Any] = {}
    try:
        payload = build_hourly_news_brief(
            raw_flashes,
            root_path,
            start_time=start,
            end_time=end,
            period=period_cfg["slug"],
            logic_run=logic_run,
            theme_anchor_path=theme_anchor_path,
            use_llm_brief=use_llm_brief,
            llm_brief_provider=llm_brief_provider,
            llm_brief_timeout=llm_brief_timeout,
            use_llm_assessment=use_llm_assessment,
            llm_asset_limit=llm_asset_limit,
            top_flash_limit=40 if period_cfg["slug"] == "half_day" else 16,
            top_event_limit=120 if period_cfg["slug"] == "half_day" else 80,
            unmapped_important_limit=24 if period_cfg["slug"] == "half_day" else 8,
        )
        runtime_payload = payload.pop("_runtime", {}) if isinstance(payload.get("_runtime"), dict) else {}
        logic_payload = runtime_payload.get("news_logic_payload") or {}
        markdown = render_hourly_news_brief_markdown(payload)
        llm_brief = payload.get("llm_brief") or {}
        if use_llm_brief and str(llm_brief.get("method") or "").startswith("llm_failed"):
            errors.append(
                {
                    "code": "LLMBriefGeneration",
                    "message": "; ".join(str(item) for item in (llm_brief.get("quality_notes") or []))
                    or "LLM brief generation failed.",
                }
            )
            status = "partial"
        else:
            status = "succeeded"
    except Exception as exc:
        errors.append({"code": exc.__class__.__name__, "message": str(exc)})
        status = "failed"
        payload = {
            "schema_version": period_cfg["schema_version"],
            "status": "failed",
            "generated_at": utc_now_iso(),
            "title": _brief_title(start, end, period=period_cfg["slug"]),
            "period": {
                "slug": period_cfg["slug"],
                "label": period_cfg["label"],
                "hours": hours,
            },
            "source_role": "news",
            "time_window": _window_label(start, end),
            "stats": {"raw_flash_count": len(raw_flashes)},
            "errors": errors,
        }
        markdown = render_hourly_news_brief_markdown(payload)

    write_json(raw_flashes_path, [_json_safe(flash) for flash in raw_flashes])
    write_json(news_logic_path, logic_payload)
    write_json(brief_json_path, payload)
    atomic_write_text(brief_md_path, markdown)
    write_json(candidate_json_path, payload)
    atomic_write_text(candidate_md_path, markdown)
    write_json(latest_json_path, payload)
    atomic_write_text(latest_md_path, markdown)

    stats = payload.get("stats") or {}
    asset_scope = [str(item.get("asset") or "") for item in (payload.get("assets") or []) if item.get("asset")]
    importance_score = min(1.0, float(stats.get("mapped_event_count") or 0) / 20.0)
    llm_brief = payload.get("llm_brief") or {}
    model_name = (
        f"{llm_brief.get('provider')}/{llm_brief.get('model')}"
        if llm_brief.get("provider") and llm_brief.get("model")
        else str(llm_brief.get("method") or "rule_fallback")
    )
    manifest = _candidate_manifest(
        candidate_id=candidate_id,
        run_id=run_id,
        created_at=created_at,
        title=payload.get("title") or _brief_title(start, end, period=period_cfg["slug"]),
        raw_flashes_ref=relative_to_root(raw_flashes_path, root_path),
        json_ref=relative_to_root(candidate_json_path, root_path),
        markdown_ref=relative_to_root(candidate_md_path, root_path),
        news_logic_ref=relative_to_root(news_logic_path, root_path),
        asset_scope=asset_scope,
        importance_score=round(importance_score, 3),
        model_name=model_name,
        period_slug=period_cfg["slug"],
        period_label=period_cfg["label"],
    )
    write_json(candidate_manifest_path, manifest)
    write_json(latest_manifest_path, manifest)
    completed_at = utc_now_iso()
    run_manifest = _run_manifest(
        run_id=run_id,
        created_at=created_at,
        completed_at=completed_at,
        status=status,
        root_path=root_path,
        run_dir=run_dir,
        raw_flashes_path=raw_flashes_path,
        brief_json_path=brief_json_path,
        brief_md_path=brief_md_path,
        news_logic_path=news_logic_path,
        candidate_manifest_path=candidate_manifest_path,
        candidate_json_path=candidate_json_path,
        errors=errors,
        model_refs=[
            {
                "provider": str(llm_brief.get("provider") or "none"),
                "model": str(llm_brief.get("model") or llm_brief.get("method") or "none"),
                "prompt_hash": None,
                "parameters": {
                    "task": f"{period_cfg['slug']}_news_brief_narrative",
                    "citation_policy_status": llm_brief.get("citation_policy_status"),
                    "raw_response_hash": llm_brief.get("raw_response_hash"),
                },
            }
        ]
        if llm_brief
        else [],
    )
    run_manifest_path = run_dir / "run_manifest.json"
    write_json(run_manifest_path, run_manifest)
    run_summary = {
        "schema_version": f"{period_cfg['slug']}_news_brief_run_summary.v1",
        "run_id": run_id,
        "candidate_id": candidate_id,
        "status": status,
        "time_window": _window_label(start, end),
        "stats": stats,
        "paths": {
            "run_manifest": relative_to_root(run_manifest_path, root_path),
            "run_brief_json": relative_to_root(brief_json_path, root_path),
            "run_brief_markdown": relative_to_root(brief_md_path, root_path),
            "candidate_manifest": relative_to_root(candidate_manifest_path, root_path),
            "candidate_brief_json": relative_to_root(candidate_json_path, root_path),
            "latest_brief_json": relative_to_root(latest_json_path, root_path),
            "latest_brief_markdown": relative_to_root(latest_md_path, root_path),
        },
        "errors": errors,
    }
    write_json(run_dir / "run_summary.json", run_summary)
    return {
        "run_id": run_id,
        "candidate_id": candidate_id,
        "status": status,
        "payload": payload,
        "manifest": manifest,
        "run_manifest": run_manifest,
        "paths": {
            "run_dir": str(run_dir),
            "run_manifest": str(run_manifest_path),
            "run_brief_json": str(brief_json_path),
            "run_brief_markdown": str(brief_md_path),
            "candidate_manifest": str(candidate_manifest_path),
            "candidate_brief_json": str(candidate_json_path),
            "candidate_brief_markdown": str(candidate_md_path),
            "latest_brief_json": str(latest_json_path),
            "latest_brief_markdown": str(latest_md_path),
            "latest_manifest": str(latest_manifest_path),
        },
        "markdown": markdown,
        "errors": errors,
    }


def _main(argv: list[str] | None = None, *, default_period: str = "hourly") -> None:
    parser = argparse.ArgumentParser(description="Generate a traceable Quanta news brief.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--hours", type=float, help="Window length in hours.")
    parser.add_argument(
        "--period",
        choices=["hourly", "half-day", "half_day"],
        default=default_period,
        help="Output cadence and latest directory.",
    )
    parser.add_argument("--end-time", help="Window end time, ISO or YYYY-MM-DD HH:MM:SS.")
    parser.add_argument(
        "--use-latest-data-time",
        action="store_true",
        help="End the window at MAX(publish_time) instead of wall-clock now.",
    )
    parser.add_argument("--logic-run", help="Path to futures daily run dir with trade_thesis.json.")
    parser.add_argument("--theme-anchor-path", help="Path to a theme_anchor candidate set or directory.")
    parser.add_argument(
        "--no-llm-brief",
        action="store_true",
        help="Disable model-written human narrative and use rule fallback only.",
    )
    parser.add_argument("--llm-brief-provider", help="Override LLM provider for the narrative brief.")
    parser.add_argument("--llm-brief-timeout", type=int, default=120, help="Narrative LLM timeout in seconds.")
    parser.add_argument("--llm-assessment", action="store_true", help="Enable optional LLM asset assessment.")
    parser.add_argument("--llm-asset-limit", type=int, default=40, help="Maximum assets assessed by LLM.")
    args = parser.parse_args(argv)
    end_time = _parse_datetime(args.end_time) if args.end_time else None
    period = _normalize_period(args.period)
    hours = args.hours if args.hours is not None else (12.0 if period == "half_day" else 1.0)
    result = publish_hourly_news_brief(
        args.quanta_root,
        hours=hours,
        period=period,
        end_time=end_time,
        use_latest_data_time=args.use_latest_data_time,
        logic_run=args.logic_run,
        theme_anchor_path=args.theme_anchor_path,
        use_llm_brief=not args.no_llm_brief,
        llm_brief_provider=args.llm_brief_provider,
        llm_brief_timeout=args.llm_brief_timeout,
        use_llm_assessment=args.llm_assessment,
        llm_asset_limit=args.llm_asset_limit,
    )
    stats = result["payload"].get("stats") or {}
    label = (_period(period)["label"] + "新闻汇总") if period == "half_day" else "小时新闻简报"
    print(f"{label} → {result['paths']['latest_brief_markdown']}")
    print(f"run_id={result['run_id']} candidate_id={result['candidate_id']} status={result['status']}")
    print(stable_json_dumps(stats, indent=2))
    if result["errors"]:
        print(stable_json_dumps({"errors": result["errors"]}, indent=2))


def main(argv: list[str] | None = None) -> None:
    _main(argv, default_period="hourly")


def half_day_main(argv: list[str] | None = None) -> None:
    _main(argv, default_period="half-day")


if __name__ == "__main__":
    main()
