from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .event_matching import score_mention_to_event


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def retrieve_candidate_events(
    mention: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    retrieval = policy.get("retrieval") or {}
    lookback_days = int(retrieval.get("lookback_days") or 7)
    top_k = int(retrieval.get("top_k") or 20)
    mention_time = _parse_time(mention.get("event_time"))
    cutoff = mention_time - timedelta(days=lookback_days) if mention_time else None
    candidates = []
    for event in events:
        if event.get("event_type") != mention.get("event_type"):
            signature = event.get("core_signature") or {}
            if not set(mention.get("entities") or []) & set(event.get("core_entities") or []) and not set(
                mention.get("location") or []
            ) & set(signature.get("location") or []):
                continue
        event_time = _parse_time(event.get("last_seen_at") or event.get("event_time"))
        if cutoff and event_time and event_time < cutoff:
            continue
        score = score_mention_to_event(mention, event, policy)
        if score["score"] <= 0.05:
            continue
        candidate = dict(event)
        candidate["_retrieval_score"] = score["score"]
        candidates.append(candidate)
    return sorted(candidates, key=lambda item: item.get("_retrieval_score") or 0.0, reverse=True)[:top_k]
