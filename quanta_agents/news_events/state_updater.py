from __future__ import annotations

from typing import Any

from quanta_agents.core.io import utc_now_iso

from .ids import stable_hash


def topic_state_snapshot(topic: dict[str, Any], *, summary: str = "") -> dict[str, Any]:
    snapshot_time = utc_now_iso()
    return {
        "snapshot_id": f"TSNAP-{stable_hash(topic.get('topic_id'), snapshot_time)}",
        "topic_id": topic["topic_id"],
        "snapshot_time": snapshot_time,
        "lifecycle_state": topic.get("lifecycle_state") or "candidate",
        "heat_score": float(topic.get("heat_score") or 0.0),
        "credibility_score": float(topic.get("credibility_score") or 0.0),
        "summary": summary or topic.get("definition") or topic.get("canonical_title") or "",
    }


def build_topic_state_snapshots(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build reviewable topic snapshots without mutating active knowledge."""
    return [topic_state_snapshot(topic) for topic in topics]
