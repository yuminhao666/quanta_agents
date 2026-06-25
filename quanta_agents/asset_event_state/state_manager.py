from __future__ import annotations

from typing import Any

from quanta_agents.asset_event_state.models import directional_value, signal_magnitude, utc_now_iso


class ClusterStateManager:
    """Maintains the event-cluster lifecycle without asking an LLM to decide state."""

    def assign_status(
        self,
        cluster: dict[str, Any],
        *,
        previous_cluster: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        event_details = [item for item in cluster.get("event_details", []) if isinstance(item, dict)]
        if not event_details:
            return "emerging", "no events attached"
        if len(event_details) == 1:
            return "emerging", "first canonical event in cluster"

        ordered = sorted(event_details, key=lambda item: str(item.get("timestamp") or ""))
        midpoint = max(1, len(ordered) // 2)
        earlier = self._average_signal(ordered[:midpoint])
        later = self._average_signal(ordered[midpoint:])
        earlier_direction = directional_value(earlier)
        later_direction = directional_value(later)
        earlier_strength = signal_magnitude(earlier)
        later_strength = signal_magnitude(later)

        if abs(earlier_direction) >= 0.15 and abs(later_direction) >= 0.15 and earlier_direction * later_direction < 0:
            return "reversing", "recent events flip the dominant signal direction"
        if later_strength >= max(0.12, earlier_strength * 1.15):
            return "strengthening", "recent weighted signal magnitude is increasing"
        if later_strength <= earlier_strength * 0.65:
            return "fading", "recent weighted signal magnitude is fading"

        previous_status = str((previous_cluster or {}).get("status") or "")
        if previous_status == "strengthening":
            return "strengthening", "cluster remains reinforced versus previous state"
        return "strengthening", "multiple same-direction events are attached"

    def with_status(
        self,
        cluster: dict[str, Any],
        *,
        previous_cluster: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status, reason = self.assign_status(cluster, previous_cluster=previous_cluster)
        result = dict(cluster)
        result["status"] = status
        result["state_reason"] = reason
        result["state_updated_at"] = utc_now_iso()
        return result

    def _average_signal(self, events: list[dict[str, Any]]) -> dict[str, float]:
        keys = ("price", "supply", "demand", "macro", "sentiment")
        totals = {key: 0.0 for key in keys}
        total_weight = 0.0
        for event in events:
            weight = max(0.01, float(event.get("confidence") or 0.5))
            vector = event.get("signal_vector") if isinstance(event.get("signal_vector"), dict) else {}
            for key in keys:
                totals[key] += float(vector.get(key, 0.0)) * weight
            total_weight += weight
        if total_weight <= 0:
            return totals
        return {key: round(value / total_weight, 4) for key, value in totals.items()}
