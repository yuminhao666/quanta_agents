from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Any


DEFAULT_ID_LENGTH = 20


def clean_id_part(value: Any) -> str:
    """Normalize noisy runtime values before they enter stable ID material."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text


def stable_json(value: Any) -> str:
    """Canonical JSON used by every Quanta object ID and relation ID."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(prefix: str, *parts: Any, length: int = DEFAULT_ID_LENGTH) -> str:
    material = [clean_id_part(part) for part in parts]
    digest = hashlib.sha1(stable_json(material).encode("utf-8")).hexdigest()[:length].upper()
    return f"{prefix}-{digest}"


def content_hash(content: Any) -> str:
    """Hash content without tying identity to local file names or display titles."""
    return hashlib.sha256(stable_json(content).encode("utf-8")).hexdigest()


def research_object_id(
    object_type: str,
    *,
    source_type: str,
    source_id: str | None = None,
    content_hash_value: str | None = None,
    content_uri: str | None = None,
    event_time: Any = None,
) -> str:
    return stable_hash(
        "OBJ",
        object_type,
        source_type,
        source_id or "",
        content_hash_value or "",
        content_uri or "",
        event_time or "",
    )


def relation_id(
    relation_type: str,
    from_object_id: str,
    to_object_id: str,
    *,
    evidence_object_id: str | None = None,
    agent_run_id: str | None = None,
) -> str:
    return stable_hash(
        "REL",
        relation_type,
        from_object_id,
        to_object_id,
        evidence_object_id or "",
        agent_run_id or "",
    )


def canonical_event_id(
    *,
    source_type: str,
    source_id: str | None,
    canonical_summary: str,
    event_time: Any,
) -> str:
    # source_id is preferred when present; summary/time keeps legacy news rows stable.
    return stable_hash("EVENT", source_type, source_id or "", canonical_summary, event_time)


def topic_id(canonical_title: str, *, topic_type: str = "market_theme") -> str:
    return stable_hash("TOPIC", topic_type, canonical_title)


def topic_membership_id(object_id: str, topic_id_value: str, relation_to_topic: str) -> str:
    return stable_hash("TMEM", object_id, topic_id_value, relation_to_topic)


def agent_run_id(run_type: str, source_id: str | None, started_at: Any = None) -> str:
    return stable_hash("RUNOBJ", run_type, source_id or "", started_at or "")
