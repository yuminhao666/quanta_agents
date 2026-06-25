from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any


EVENT_SCHEMA_VERSION = "asset_canonical_event.v1"
DRIVER_SCHEMA_VERSION = "asset_driver_state.v1"
THEME_NARRATIVE_SCHEMA_VERSION = "asset_driver_narrative.v1"
CLUSTER_SCHEMA_VERSION = "asset_event_cluster.v1"
NARRATIVE_SCHEMA_VERSION = "asset_event_narrative.v1"

CANONICAL_ASSETS = {"Copper", "Gold", "Oil", "Macro", "Other"}
ASSET_ALIASES = {
    "COPPER": "Copper",
    "CU": "Copper",
    "GOLD": "Gold",
    "AU": "Gold",
    "OIL": "Oil",
    "CRUDE": "Oil",
    "MACRO": "Macro",
}
EVENT_TYPES = {"policy", "macro", "supply", "demand", "sentiment", "data", "research"}
SOURCE_TYPES = {"news", "research", "data", "polymarket", "human", "legacy_agent_output", "unknown"}
LEGACY_EVENT_TYPE_ALIASES = {"positioning": "sentiment"}
SIGNAL_KEYS = ("price", "supply", "demand", "macro", "sentiment")
DRIVER_TRENDS = {"strengthening", "weakening", "stable", "reversing"}
GRAPH_TYPES = {"event", "industry", "causal"}
CLUSTER_STATUSES = {"emerging", "strengthening", "fading", "reversing"}
DEFAULT_CLUSTER_WINDOW_HOURS = 72


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_id(prefix: str, *parts: Any, length: int = 16) -> str:
    raw = "||".join(str(part or "") for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length].upper()
    return f"{prefix}-{digest}"


def clean_text(value: Any, *, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit is not None and len(text) > limit:
        return text[:limit].rstrip(" ,，;；") + "..."
    return text


def parse_iso_datetime(value: Any) -> datetime:
    text = clean_text(value)
    if not text:
        return datetime.now(timezone.utc)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_iso(value: Any) -> str:
    return parse_iso_datetime(value).isoformat(timespec="seconds")


def date_key(value: Any | None = None) -> str:
    text = clean_text(value)
    if re.fullmatch(r"\d{8}", text):
        return text
    if text:
        match = re.search(r"(20\d{2})[-/年.]?([01]\d)[-/月.]?([0-3]\d)", text)
        if match:
            return "".join(match.groups())
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def clamp_float(value: Any, minimum: float, maximum: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def normalize_confidence(value: Any, default: float = 0.5) -> float:
    return round(clamp_float(value, 0.0, 1.0, default), 4)


def normalize_asset(value: Any) -> str:
    asset = clean_text(value)
    if asset.upper() in ASSET_ALIASES:
        return ASSET_ALIASES[asset.upper()]
    return asset if asset in CANONICAL_ASSETS else "Other"


def normalize_event_type(value: Any) -> str:
    event_type = clean_text(value).lower()
    event_type = LEGACY_EVENT_TYPE_ALIASES.get(event_type, event_type)
    return event_type if event_type in EVENT_TYPES else "sentiment"


def normalize_source_type(value: Any) -> str:
    source_type = clean_text(value).lower()
    if "opinion_radar" in source_type or "news" in source_type or "flash" in source_type:
        return "news"
    if "research" in source_type or "wechat" in source_type or "report" in source_type:
        return "research"
    if "data_agent" in source_type or "dzq_data" in source_type or "fundamental" in source_type:
        return "data"
    if "polymarket" in source_type:
        return "polymarket"
    if source_type in {"research_report", "wechat_research", "report"}:
        return "research"
    if source_type in {"fundamental_data", "mysql", "indicator"}:
        return "data"
    if source_type in {"prediction_market", "poly"}:
        return "polymarket"
    if source_type in SOURCE_TYPES:
        return source_type
    return "unknown"


def normalize_signal_vector(value: Any) -> dict[str, float]:
    raw = value if isinstance(value, dict) else {}
    return {key: round(clamp_float(raw.get(key), -1.0, 1.0), 4) for key in SIGNAL_KEYS}


def signal_magnitude(vector: dict[str, float]) -> float:
    return sum(abs(float(vector.get(key, 0.0))) for key in SIGNAL_KEYS) / len(SIGNAL_KEYS)


def directional_value(vector: dict[str, float]) -> float:
    price = float(vector.get("price", 0.0))
    if abs(price) >= 0.05:
        return price
    ranked = sorted((abs(float(vector.get(key, 0.0))), float(vector.get(key, 0.0))) for key in SIGNAL_KEYS)
    return ranked[-1][1] if ranked else 0.0


def same_direction(left: dict[str, float], right: dict[str, float]) -> bool:
    l_value = directional_value(left)
    r_value = directional_value(right)
    if abs(l_value) < 0.05 or abs(r_value) < 0.05:
        return True
    return l_value * r_value > 0
