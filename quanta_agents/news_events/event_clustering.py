from __future__ import annotations

from typing import Any

from quanta_agents.core.io import utc_now_iso

from .ids import event_id_from_signature, relation_id


def signature_from_mention(mention: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject": mention.get("subject") or [],
        "action": mention.get("action") or "",
        "object": mention.get("object") or [],
        "location": mention.get("location") or [],
        "event_stage": mention.get("event_stage") or "observation",
    }


def create_event_from_mention(mention: dict[str, Any]) -> dict[str, Any]:
    now = utc_now_iso()
    signature = signature_from_mention(mention)
    event_id = event_id_from_signature(
        signature,
        event_type=str(mention.get("event_type") or "other"),
        event_time=mention.get("event_time"),
    )
    group_id = mention.get("syndication_group_id")
    mention_id = mention["mention_id"]
    return {
        "event_id": event_id,
        "canonical_summary": mention.get("canonical_summary") or "",
        "event_type": mention.get("event_type") or "other",
        "event_time": mention.get("event_time"),
        "first_seen_at": mention.get("event_time") or now,
        "last_seen_at": mention.get("event_time") or now,
        "truth_status": mention.get("truth_status") or "unverified",
        "market_attention": float(mention.get("market_attention") or 0.0),
        "lifecycle_state": "new",
        "core_entities": mention.get("entities") or [],
        "core_signature": signature,
        "mention_ids": [mention_id],
        "supporting_mention_ids": [mention_id],
        "contradicting_mention_ids": [],
        "source_count": 1,
        "independent_source_count": 1 if group_id else 1,
        "status": "candidate",
        "created_at": now,
        "updated_at": now,
        "syndication_group_ids": [group_id] if group_id else [],
        "representative_mentions": [mention],
    }


def merge_mention_into_event(event: dict[str, Any], mention: dict[str, Any]) -> dict[str, Any]:
    updated = dict(event)
    mention_ids = list(dict.fromkeys([*(updated.get("mention_ids") or []), mention["mention_id"]]))
    group_ids = list(dict.fromkeys([*(updated.get("syndication_group_ids") or []), mention.get("syndication_group_id")]))
    group_ids = [item for item in group_ids if item]
    updated["mention_ids"] = mention_ids
    updated["supporting_mention_ids"] = list(
        dict.fromkeys([*(updated.get("supporting_mention_ids") or []), mention["mention_id"]])
    )
    updated["source_count"] = len(mention_ids)
    updated["independent_source_count"] = max(1, len(group_ids))
    updated["syndication_group_ids"] = group_ids
    updated["last_seen_at"] = max(str(updated.get("last_seen_at") or ""), str(mention.get("event_time") or ""))
    updated["market_attention"] = round(max(float(updated.get("market_attention") or 0.0), float(mention.get("market_attention") or 0.0)), 3)
    if updated.get("lifecycle_state") == "new" and len(mention_ids) > 1:
        updated["lifecycle_state"] = "developing"
    representatives = list(updated.get("representative_mentions") or [])
    if len(representatives) < 3 and mention["mention_id"] not in {row.get("mention_id") for row in representatives}:
        representatives.append(mention)
    updated["representative_mentions"] = representatives[:3]
    updated["updated_at"] = utc_now_iso()
    return updated


def create_related_event(
    mention: dict[str, Any],
    *,
    relation: str,
    target_event_id: str,
    confidence: float,
    reason: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    event = create_event_from_mention(mention)
    if relation == "CONTRADICT":
        event["lifecycle_state"] = "disputed"
        event["contradicting_mention_ids"] = [mention["mention_id"]]
        event["supporting_mention_ids"] = []
    if relation == "RETRACT":
        event["lifecycle_state"] = "resolved"
    relation_row = {
        "relation_id": relation_id(event["event_id"], relation, target_event_id),
        "source_event_id": event["event_id"],
        "relation": relation,
        "target_event_id": target_event_id,
        "confidence": round(float(confidence or 0.0), 3),
        "reason": reason,
        "created_at": utc_now_iso(),
    }
    return event, relation_row
