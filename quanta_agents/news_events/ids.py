from __future__ import annotations

import hashlib
import re
from typing import Any


def stable_hash(*parts: Any, length: int = 16) -> str:
    """Return a deterministic uppercase digest for business IDs."""
    material = "||".join(str(part or "") for part in parts)
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:length].upper()


def stable_sha256(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _slug(value: Any, *, max_len: int = 48) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(value or "").strip())
    text = re.sub(r"-+", "-", text).strip("-")
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 9]}-{stable_hash(text, length=8)}"


def stable_news_id(
    source_system: str,
    source_row_id: str | int | None,
    publish_time: str,
    content_hash: str,
) -> str:
    source = _slug(source_system or "news", max_len=24).upper()
    row = _slug(source_row_id)
    if row:
        return f"NEWS-{source}-{row}"
    return f"NEWS-{source}-{stable_hash(publish_time, content_hash)}"


def mention_id(source_news_id: str, canonical_summary: str) -> str:
    return f"MENTION-{stable_hash(source_news_id, canonical_summary)}"


def event_id_from_signature(signature: dict[str, Any], *, event_type: str, event_time: str | None = None) -> str:
    return "EVENT-" + stable_hash(
        event_type,
        "|".join(str(item) for item in signature.get("subject") or []),
        signature.get("action") or "",
        "|".join(str(item) for item in signature.get("object") or []),
        "|".join(str(item) for item in signature.get("location") or []),
        signature.get("event_stage") or "",
        str(event_time or "")[:10],
    )


def relation_id(source_event_id: str, relation: str, target_event_id: str) -> str:
    return f"EREL-{stable_hash(source_event_id, relation, target_event_id)}"


def syndication_group_id(representative_news_id: str, content_hash: str) -> str:
    return f"SYN-{stable_hash(representative_news_id, content_hash)}"


def asset_link_id(event_id: str, asset_id: str) -> str:
    return f"EAL-{stable_hash(event_id, asset_id)}"


def framework_link_id(event_id: str, asset_id: str, node_id: str) -> str:
    return f"EFNL-{stable_hash(event_id, asset_id, node_id)}"


def stable_topic_id(canonical_title: str, topic_type: str = "event_topic") -> str:
    return f"TOPIC-{stable_hash(topic_type, canonical_title)}"


def topic_membership_id(object_id: str, topic_id: str, relation_to_topic: str) -> str:
    return f"MEM-{stable_hash(object_id, topic_id, relation_to_topic)}"


def topic_alias_id(topic_id: str, alias: str) -> str:
    return f"TALIAS-{stable_hash(topic_id, alias)}"
