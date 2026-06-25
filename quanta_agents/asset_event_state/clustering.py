from __future__ import annotations

import math
from collections import Counter
from datetime import timedelta
from typing import Any

from quanta_agents.asset_event_state.models import (
    CLUSTER_SCHEMA_VERSION,
    DEFAULT_CLUSTER_WINDOW_HOURS,
    SIGNAL_KEYS,
    clean_text,
    hash_id,
    normalize_signal_vector,
    parse_iso_datetime,
    same_direction,
    utc_now_iso,
)
from quanta_agents.asset_event_state.state_manager import ClusterStateManager


RELATED_DRIVER_GROUPS = (
    {"policy", "macro"},
    {"supply", "demand"},
)


def asset_match(left: dict[str, Any], right: dict[str, Any]) -> float:
    if left.get("asset") == right.get("asset"):
        return 1.0
    if "Macro" in {left.get("asset"), right.get("asset")}:
        return 0.35
    return 0.0


def driver_match(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_type = str(left.get("event_type") or "")
    right_type = str(right.get("event_type") or "")
    if left_type == right_type:
        return 1.0
    if any({left_type, right_type} <= group for group in RELATED_DRIVER_GROUPS):
        return 0.55
    left_nodes = set(left.get("impact_nodes") or [])
    right_nodes = set(right.get("impact_nodes") or [])
    if left_nodes and right_nodes and left_nodes.intersection(right_nodes):
        return 0.45
    return 0.0


def signal_alignment(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_vector = normalize_signal_vector(left.get("signal_vector"))
    right_vector = normalize_signal_vector(right.get("signal_vector"))
    left_values = [left_vector[key] for key in SIGNAL_KEYS]
    right_values = [right_vector[key] for key in SIGNAL_KEYS]
    left_norm = math.sqrt(sum(value * value for value in left_values))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    if left_norm <= 0 or right_norm <= 0:
        return 0.5
    cosine = sum(l * r for l, r in zip(left_values, right_values)) / (left_norm * right_norm)
    return round(max(0.0, min(1.0, (cosine + 1.0) / 2.0)), 4)


def time_decay(left: dict[str, Any], right: dict[str, Any], *, window_hours: int) -> float:
    left_time = parse_iso_datetime(left.get("timestamp"))
    right_time = parse_iso_datetime(right.get("timestamp"))
    delta_hours = abs((left_time - right_time).total_seconds()) / 3600.0
    if delta_hours > window_hours:
        return 0.0
    return round(max(0.0, 1.0 - delta_hours / window_hours), 4)


def structured_cluster_score(
    event: dict[str, Any],
    reference_event: dict[str, Any],
    *,
    window_hours: int = DEFAULT_CLUSTER_WINDOW_HOURS,
) -> dict[str, float]:
    components = {
        "asset_match": asset_match(event, reference_event),
        "driver_match": driver_match(event, reference_event),
        "signal_alignment": signal_alignment(event, reference_event),
        "time_decay": time_decay(event, reference_event, window_hours=window_hours),
    }
    score = (
        0.4 * components["asset_match"]
        + 0.25 * components["driver_match"]
        + 0.2 * components["signal_alignment"]
        + 0.15 * components["time_decay"]
    )
    components["score"] = round(score, 4)
    return components


def passes_dedup_gate(
    event: dict[str, Any],
    reference_event: dict[str, Any],
    *,
    window_hours: int = DEFAULT_CLUSTER_WINDOW_HOURS,
) -> bool:
    if event.get("asset") != reference_event.get("asset"):
        return False
    if driver_match(event, reference_event) < 0.55:
        return False
    if not same_direction(
        normalize_signal_vector(event.get("signal_vector")),
        normalize_signal_vector(reference_event.get("signal_vector")),
    ):
        return False
    return time_decay(event, reference_event, window_hours=window_hours) > 0


class ClusteringEngine:
    def __init__(
        self,
        *,
        window_hours: int = DEFAULT_CLUSTER_WINDOW_HOURS,
        score_threshold: float = 0.68,
        state_manager: ClusterStateManager | None = None,
    ):
        self.window_hours = window_hours
        self.score_threshold = score_threshold
        self.state_manager = state_manager or ClusterStateManager()

    def cluster_events(
        self,
        events: list[dict[str, Any]],
        *,
        previous_clusters: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        previous_by_id = {str(cluster.get("cluster_id")): cluster for cluster in previous_clusters or []}
        working_clusters: list[dict[str, Any]] = []

        for event in sorted(events, key=lambda item: str(item.get("timestamp") or "")):
            best_cluster: dict[str, Any] | None = None
            best_score: dict[str, float] | None = None
            for cluster in working_clusters:
                reference = self._reference_event(cluster)
                if not reference or not passes_dedup_gate(event, reference, window_hours=self.window_hours):
                    continue
                score = structured_cluster_score(event, reference, window_hours=self.window_hours)
                if score["score"] >= self.score_threshold and (
                    best_score is None or score["score"] > best_score["score"]
                ):
                    best_cluster = cluster
                    best_score = score

            if best_cluster is None or best_score is None:
                working_clusters.append(self._new_cluster(event))
                continue

            event_id = str(event["event_id"])
            if event_id not in best_cluster["events"]:
                best_cluster["events"].append(event_id)
                best_cluster["event_details"].append(event)
            best_cluster.setdefault("assignment_scores", {})[event_id] = best_score

        return [
            self.state_manager.with_status(
                self._finalize_cluster(cluster),
                previous_cluster=previous_by_id.get(str(cluster.get("cluster_id"))),
            )
            for cluster in working_clusters
        ]

    def _new_cluster(self, event: dict[str, Any]) -> dict[str, Any]:
        timestamp = str(event.get("timestamp") or utc_now_iso())
        cluster_id = hash_id(
            "CL",
            event.get("asset"),
            event.get("event_type"),
            timestamp[:10],
            event.get("summary"),
            length=14,
        )
        return {
            "schema_version": CLUSTER_SCHEMA_VERSION,
            "cluster_id": cluster_id,
            "asset": event.get("asset", "Other"),
            "theme": self._theme([event]),
            "events": [event["event_id"]],
            "event_details": [event],
            "assignment_scores": {event["event_id"]: {"score": 1.0, "bootstrap": 1.0}},
            "time_window": f"{self.window_hours}h",
            "created_at": utc_now_iso(),
        }

    def _finalize_cluster(self, cluster: dict[str, Any]) -> dict[str, Any]:
        event_details = sorted(cluster.get("event_details", []), key=lambda item: str(item.get("timestamp") or ""))
        cluster = dict(cluster)
        cluster["event_details"] = event_details
        cluster["theme"] = self._theme(event_details)
        cluster["net_signal"] = self._net_signal(event_details)
        cluster["confidence"] = self._confidence(event_details)
        cluster["first_event_at"] = str(event_details[0].get("timestamp") or "") if event_details else ""
        cluster["last_event_at"] = str(event_details[-1].get("timestamp") or "") if event_details else ""
        cluster["traceability"] = {
            "raw_to_event_to_cluster": [
                {
                    "source_id": event.get("source_id", ""),
                    "event_id": event.get("event_id", ""),
                    "cluster_id": cluster["cluster_id"],
                }
                for event in event_details
            ],
            "clustering_rule": (
                "score = 0.4*asset_match + 0.25*driver_match "
                "+ 0.2*signal_alignment + 0.15*time_decay"
            ),
            "embedding_used": False,
        }
        return cluster

    def _reference_event(self, cluster: dict[str, Any]) -> dict[str, Any] | None:
        event_details = cluster.get("event_details")
        if not isinstance(event_details, list) or not event_details:
            return None
        return sorted(event_details, key=lambda item: str(item.get("timestamp") or ""))[-1]

    def _theme(self, events: list[dict[str, Any]]) -> str:
        if not events:
            return "empty event cluster"
        asset = clean_text(events[0].get("asset")) or "Other"
        event_type = Counter(clean_text(event.get("event_type")) for event in events).most_common(1)[0][0]
        summaries = [clean_text(event.get("summary"), limit=80) for event in events if clean_text(event.get("summary"))]
        if not summaries:
            return f"{asset} {event_type} event cluster"
        return f"{asset} {event_type}: {summaries[0]}"

    def _net_signal(self, events: list[dict[str, Any]]) -> dict[str, float]:
        totals = {key: 0.0 for key in SIGNAL_KEYS}
        total_weight = 0.0
        for event in events:
            vector = normalize_signal_vector(event.get("signal_vector"))
            weight = max(0.01, float(event.get("confidence") or 0.5))
            for key in SIGNAL_KEYS:
                totals[key] += vector[key] * weight
            total_weight += weight
        if total_weight <= 0:
            return totals
        return {key: round(value / total_weight, 4) for key, value in totals.items()}

    def _confidence(self, events: list[dict[str, Any]]) -> float:
        if not events:
            return 0.0
        avg = sum(float(event.get("confidence") or 0.0) for event in events) / len(events)
        support_bonus = min(0.2, 0.05 * max(0, len(events) - 1))
        return round(min(1.0, avg + support_bonus), 4)
