from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from quanta_agents.signal_mapping.theme_anchor_matcher import (
    ThemeAnchorIndex,
    load_theme_anchor_index,
    theme_anchor_ref,
)

from . import config, db, dictionary, filters, llm


IMPORTANT_WEIGHT = 2.0
_TAG_RE = re.compile(r"<[^>]+>")
_ROUNDUP_RE = re.compile(r"金十数据整理|每日.+要闻|重要新闻汇总|市场要闻速递|动态汇总|一览")


def _display(flash: dict[str, Any], limit: int = 80) -> str:
    text = (flash.get("title") or flash.get("content") or "").strip()
    text = _TAG_RE.sub("", text).replace("&nbsp;", " ").strip()
    return text[:limit]


def _text(flash: dict[str, Any]) -> str:
    return f"{flash.get('title') or ''} {flash.get('content') or ''}".strip()


def _is_roundup(text: str) -> bool:
    return bool(_ROUNDUP_RE.search(text))


def _bucket_hits(text: str) -> list[tuple[str, str, str]]:
    hits = dictionary.classify(text)
    varieties = [hit for hit in hits if hit[2] == "variety"]
    macros = [hit for hit in hits if hit[2] == "macro"]
    sectors = [hit for hit in hits if hit[2] == "sector"]
    categories = [hit for hit in hits if hit[2] == "category"]
    if varieties:
        return varieties + macros
    if macros:
        return macros
    if sectors:
        return sectors[:1]
    return categories[:1]


def _heat_weight(flash: dict[str, Any]) -> float:
    text = _text(flash)
    important = int(flash.get("important") or 0)
    weight = 1.0 + (IMPORTANT_WEIGHT - 1) * important
    if _is_roundup(text):
        weight *= 0.35
    if filters.is_market_move(text) and not filters.has_signal(text):
        weight *= 0.3
    return max(0.1, weight)


def _item_rank(flash: dict[str, Any]) -> tuple[int, int, int, int, str]:
    text = _text(flash)
    return (
        0 if _is_roundup(text) else 1,
        1 if filters.has_signal(text) else 0,
        0 if filters.is_market_move(text) and not filters.has_signal(text) else 1,
        int(flash.get("important") or 0),
        str(flash.get("publish_time") or ""),
    )


def _theme_anchor_context(
    bucket: dict[str, Any],
    naming: dict[str, Any],
    titles: list[str],
) -> str:
    return " ".join(
        str(item or "")
        for item in [
            bucket.get("label"),
            bucket.get("kind"),
            naming.get("theme"),
            naming.get("summary"),
            naming.get("logic"),
            " ".join(titles[:8]),
        ]
    )


def _anchor_theme(
    bucket: dict[str, Any],
    naming: dict[str, Any],
    titles: list[str],
    theme_index: ThemeAnchorIndex,
) -> dict[str, Any]:
    asset_labels = [str(bucket.get("label") or ""), *(str(item) for item in bucket.get("varieties") or [])]
    match = theme_index.best_match(
        _theme_anchor_context(bucket, naming, titles),
        asset_labels=asset_labels,
    )
    if not match:
        return {
            "theme_anchor_refs": [],
            "theme_anchor_match_method": "no_theme_anchor_match",
            "anchoring_status": "unanchored_theme_candidate",
            "theme_type": "",
            "anchored_theme_title": "",
        }
    return {
        "theme_anchor_refs": [theme_anchor_ref(match)],
        "theme_anchor_match_method": match["match_method"],
        "anchoring_status": "anchored_theme",
        "theme_type": match.get("theme_type") or "",
        "anchored_theme_title": match.get("title") or "",
    }


def _dedupe_theme_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for ref in refs:
        ref_id = str(ref.get("id") or "")
        if not ref_id or ref_id in seen:
            continue
        seen.add(ref_id)
        out.append(ref)
    return out


def _window(date: str | None, hours: int) -> tuple[datetime, datetime, str]:
    if date:
        start = datetime.strptime(date, "%Y-%m-%d")
        return start, start + timedelta(days=1), "day"
    end = db.latest_time() or datetime.now()
    return end - timedelta(hours=hours), end + timedelta(seconds=1), "rolling"


def snapshot(
    date: str | None = None,
    hours: int = 24,
    top: int = 18,
    name_llm: bool = True,
    *,
    root: str | None = None,
    theme_anchor_path: str | None = None,
) -> dict[str, Any]:
    start, end, mode = _window(date, hours)
    raw_flashes = db.fetch_flashes(start, end)
    flashes = [flash for flash in raw_flashes if filters.keep_flash(flash)]
    theme_index = load_theme_anchor_index(root, candidate_path=theme_anchor_path)

    buckets: dict[str, dict[str, Any]] = {}
    for flash in flashes:
        text = _text(flash)
        for key, label, kind in _bucket_hits(text):
            bucket = buckets.setdefault(
                key,
                {
                    "key": key,
                    "label": label,
                    "kind": kind,
                    "flash_count": 0,
                    "important_count": 0,
                    "heat": 0.0,
                    "varieties": set(),
                    "items": [],
                    "buckets_per_hour": {},
                },
            )
            important = int(flash.get("important") or 0)
            bucket["flash_count"] += 1
            bucket["important_count"] += important
            bucket["heat"] += _heat_weight(flash)
            if kind == "variety":
                bucket["varieties"].add(label)
            bucket["items"].append(flash)
            hour = (flash.get("publish_time") or "")[:13]
            bucket["buckets_per_hour"][hour] = bucket["buckets_per_hour"].get(hour, 0) + 1

    ranked = sorted(buckets.values(), key=lambda item: item["heat"], reverse=True)[:top]
    themes = []
    for bucket in ranked:
        items_sorted = sorted(
            bucket["items"],
            key=_item_rank,
            reverse=True,
        )
        titles = [text for text in (_display(item, 60) for item in items_sorted) if text]
        top_flashes = [
            {
                "text": _display(item, 100),
                "url": item.get("url"),
                "publish_time": item.get("publish_time"),
                "important": int(item.get("important") or 0),
            }
            for item in items_sorted[:10]
        ]
        naming = (
            llm.name_bucket(bucket["label"], titles, titles[0] if titles else bucket["label"])
            if name_llm
            else {"theme": bucket["label"], "summary": titles[0][:40] if titles else "", "logic": "", "source": "off"}
        )
        anchor = _anchor_theme(bucket, naming, titles, theme_index)
        free_theme = naming["theme"]
        display_theme = anchor["anchored_theme_title"] or free_theme
        themes.append(
            {
                "key": bucket["key"],
                "label": bucket["label"],
                "kind": bucket["kind"],
                "theme": display_theme,
                "free_theme": free_theme,
                "summary": naming["summary"],
                "logic": naming.get("logic") or naming["summary"],
                "naming_source": naming["source"],
                "theme_type": anchor["theme_type"],
                "theme_anchor_refs": anchor["theme_anchor_refs"],
                "theme_anchor_match_method": anchor["theme_anchor_match_method"],
                "anchoring_status": anchor["anchoring_status"],
                "heat": round(bucket["heat"], 1),
                "flash_count": bucket["flash_count"],
                "important_count": bucket["important_count"],
                "varieties": sorted(bucket["varieties"]),
                "hourly": bucket["buckets_per_hour"],
                "top_flashes": top_flashes,
            }
        )

    important_stream = [
        {
            "text": _display(flash, 100),
            "url": flash.get("url"),
            "publish_time": flash.get("publish_time"),
            "channel": flash.get("channel"),
        }
        for flash in sorted(
            [item for item in flashes if int(item.get("important") or 0)],
            key=_item_rank,
            reverse=True,
        )[:30]
    ]
    synthesis = _synthesize(themes) if name_llm else []

    return {
        "window": {"start": start.isoformat(sep=" "), "end": end.isoformat(sep=" "), "mode": mode, "hours": hours},
        "stats": {
            "flash_total": len(flashes),
            "flash_raw": len(raw_flashes),
            "noise_filtered": len(raw_flashes) - len(flashes),
            "important_total": sum(int(flash.get("important") or 0) for flash in flashes),
            "theme_count": len(synthesis) or len(ranked),
            "bucket_count": len(ranked),
            "variety_covered": len({variety for bucket in ranked for variety in bucket["varieties"]}),
            "theme_anchor_source_count": theme_index.anchor_count,
            "anchored_theme_count": sum(1 for theme in themes if theme["anchoring_status"] == "anchored_theme"),
            "unanchored_theme_count": sum(
                1 for theme in themes if theme["anchoring_status"] == "unanchored_theme_candidate"
            ),
        },
        "theme_anchor_context": {
            "candidate_ids": theme_index.candidate_ids,
            "source_paths": theme_index.source_paths,
            "anchor_count": theme_index.anchor_count,
            "policy": "candidate_theme_anchors_are_review_required_not_gold",
        },
        "synthesis": synthesis,
        "themes": themes,
        "important_stream": important_stream,
        "generated_at": datetime.now().isoformat(sep=" "),
    }


def _synthesize(themes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    briefs = [{"label": theme["label"], "theme": theme["theme"], "summary": theme["summary"], "heat": theme["heat"]} for theme in themes]
    merged = llm.synthesize(briefs)
    if not merged:
        return []
    out = []
    for item in merged:
        members = [themes[index - 1] for index in item["members"] if 1 <= index <= len(themes)]
        if not members:
            continue
        flashes: list[dict[str, Any]] = []
        seen_urls = set()
        for member in sorted(members, key=lambda value: value["important_count"], reverse=True):
            for flash in member["top_flashes"]:
                url = flash.get("url") or flash["text"]
                if url not in seen_urls:
                    seen_urls.add(url)
                    flashes.append(flash)
        theme_refs = _dedupe_theme_refs(
            [
                ref
                for member in members
                for ref in (member.get("theme_anchor_refs") or [])
                if isinstance(ref, dict)
            ]
        )
        primary_ref = theme_refs[0] if theme_refs else {}
        free_title = item["title"]
        out.append(
            {
                "title": primary_ref.get("label") or free_title,
                "free_title": free_title,
                "summary": item["summary"],
                "heat": round(sum(value["heat"] for value in members), 1),
                "flash_count": sum(value["flash_count"] for value in members),
                "important_count": sum(value["important_count"] for value in members),
                "members": [value["label"] for value in members],
                "kinds": sorted({value["kind"] for value in members}),
                "varieties": sorted({variety for value in members for variety in value["varieties"]}),
                "top_flashes": flashes[:8],
                "theme_type": primary_ref.get("theme_type") or "",
                "theme_anchor_refs": theme_refs,
                "theme_anchor_match_method": "synthesis_member_theme_anchor" if theme_refs else "no_theme_anchor_match",
                "anchoring_status": "anchored_theme" if theme_refs else "unanchored_theme_candidate",
            }
        )
    return sorted(out, key=lambda value: value["heat"], reverse=True)


def timeline(days: int = 7, top: int = 8) -> dict[str, Any]:
    end = db.latest_time() or datetime.now()
    start = (end - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    flashes = [flash for flash in db.fetch_flashes(start, end + timedelta(seconds=1)) if filters.keep_flash(flash)]
    day_keys = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days + 1)]

    series: dict[str, dict[str, Any]] = {}
    for flash in flashes:
        day = (flash.get("publish_time") or "")[:10]
        text = _text(flash)
        for key, label, kind in _bucket_hits(text):
            item = series.setdefault(key, {"key": key, "label": label, "kind": kind, "total": 0, "daily": {d: 0 for d in day_keys}})
            item["total"] += 1
            if day in item["daily"]:
                item["daily"][day] += 1
    ranked = sorted(series.values(), key=lambda item: item["total"], reverse=True)[:top]
    return {
        "days": day_keys,
        "series": [
            {
                "key": item["key"],
                "label": item["label"],
                "kind": item["kind"],
                "total": item["total"],
                "counts": [item["daily"][day] for day in day_keys],
            }
            for item in ranked
        ],
        "generated_at": datetime.now().isoformat(sep=" "),
    }


def health() -> dict[str, Any]:
    info = db.ping()
    info["llm_enabled"] = config.LLM_ENABLED
    info["llm_provider"] = config.RADAR_LLM_PROVIDER
    info["llm_model"] = config.LLM_MODEL if config.LLM_ENABLED else ""
    return info
