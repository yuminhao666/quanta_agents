from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from quanta_agents.core.io import utc_now_iso
from quanta_agents.core.taxonomy import load_asset_taxonomy
from quanta_agents.opinion_radar import filters

from .ids import stable_news_id, stable_sha256
from .models import FilterDecision, NewsItem


_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm"}


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = _TAG_RE.sub(" ", text).replace("\u3000", " ")
    return _SPACE_RE.sub(" ", text).strip()


def normalized_news_text(title: Any, content: Any) -> str:
    return clean_text(f"{title or ''} {content or ''}")


def normalize_time(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                pass
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat(timespec="seconds")


def canonicalize_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    ]
    netloc = parts.netloc.lower()
    path = re.sub(r"/+$", "", parts.path) or parts.path
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))


def source_row_id(flash: dict[str, Any]) -> str:
    value = flash.get("flash_id") or flash.get("id") or flash.get("source_row_id") or ""
    return str(value).strip()


def standardize_news_item(
    flash: dict[str, Any],
    *,
    source_system: str = "jin10_flash",
    ingested_at: str | None = None,
) -> dict[str, Any]:
    title = clean_text(flash.get("title"))
    content = clean_text(flash.get("content"))
    normalized_text = normalized_news_text(title, content)
    publish_time = normalize_time(flash.get("publish_time") or flash.get("time") or flash.get("created_at"))
    canonical_url = canonicalize_url(flash.get("url"))
    content_hash = stable_sha256(normalized_text)
    row_id = source_row_id(flash)
    item = NewsItem(
        news_id=stable_news_id(source_system, row_id, publish_time, content_hash),
        source_system=source_system,
        source_row_id=row_id,
        title=title,
        content=content,
        normalized_text=normalized_text,
        publish_time=publish_time,
        ingested_at=ingested_at or utc_now_iso(),
        channel=clean_text(flash.get("channel")) or None,
        important=int(flash.get("important") or 0),
        url=str(flash.get("url") or "").strip() or None,
        canonical_url=canonical_url,
        content_hash=content_hash,
        source_ref={
            "source_system": source_system,
            "id": flash.get("id"),
            "flash_id": flash.get("flash_id"),
            "url": flash.get("url"),
            "channel": flash.get("channel"),
        },
    ).to_dict()
    item["raw"] = dict(flash)
    return item


def filter_news_item(news_item: dict[str, Any], *, root: str | None = None) -> dict[str, Any]:
    text = news_item.get("normalized_text") or ""
    reason_codes: list[str] = []
    if not text:
        return FilterDecision(news_item["news_id"], "filtered_out", ["empty_text"]).to_dict()
    if re.search(r"广告|推广|开户|返佣", text):
        return FilterDecision(news_item["news_id"], "filtered_out", ["advertisement"]).to_dict()
    if filters.has_signal(text):
        reason_codes.append("signal_keyword")
    if int(news_item.get("important") or 0):
        reason_codes.append("important")
    if filters.is_market_move(text) and not filters.has_signal(text):
        return FilterDecision(news_item["news_id"], "filtered_out", ["pure_price_tick"]).to_dict()

    asset_candidates = []
    try:
        taxonomy = load_asset_taxonomy(root)
        asset_candidates = [
            {"key": hit.key, "label": hit.label, "kind": hit.kind, "asset_id": hit.asset_id}
            for hit in taxonomy.classify(text)
            if hit.kind in {"variety", "macro", "sector", "category"}
        ]
    except Exception:
        asset_candidates = []

    if int(news_item.get("important") or 0):
        decision = "priority"
    elif reason_codes or asset_candidates:
        decision = "relevant"
    else:
        decision = "candidate"
        reason_codes.append("non_price_text")
    return FilterDecision(
        news_id=news_item["news_id"],
        decision=decision,
        reason_codes=reason_codes,
        asset_candidates=asset_candidates,
    ).to_dict()


def normalize_and_filter(
    flashes: list[dict[str, Any]],
    *,
    source_system: str = "jin10_flash",
    root: str | None = None,
) -> list[dict[str, Any]]:
    out = []
    ingested_at = utc_now_iso()
    for flash in flashes:
        item = standardize_news_item(flash, source_system=source_system, ingested_at=ingested_at)
        item["filter_decision"] = filter_news_item(item, root=root)
        out.append(item)
    return out
