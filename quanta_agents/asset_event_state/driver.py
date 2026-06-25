from __future__ import annotations

from typing import Any

from quanta_agents.asset_event_state.models import (
    DRIVER_SCHEMA_VERSION,
    SIGNAL_KEYS,
    clamp_float,
    clean_text,
    directional_value,
    hash_id,
    normalize_asset,
    normalize_signal_vector,
    parse_iso_datetime,
    signal_magnitude,
    utc_now_iso,
)


DEFAULT_DRIVER_DECAY_PER_24H = 0.04


class DriverStateMachine:
    """Updates asset drivers from canonical events only.

    The state transition is intentionally deterministic:
    Driver(t) = Driver(t-1) + supporting_event_signal - contradicting_event_signal + decay.
    Decay is represented as a negative contribution proportional to elapsed time.
    """

    def __init__(self, *, decay_per_24h: float = DEFAULT_DRIVER_DECAY_PER_24H):
        self.decay_per_24h = max(0.0, float(decay_per_24h))

    def update_drivers(
        self,
        events: list[dict[str, Any]],
        *,
        previous_drivers: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        drivers = {
            str(driver.get("driver_id")): self._normalize_previous(driver)
            for driver in previous_drivers or []
            if driver.get("driver_id")
        }

        for event in sorted(events, key=lambda item: str(item.get("timestamp") or "")):
            driver_id, driver_key = self.driver_identity(event)
            driver = drivers.get(driver_id) or self._new_driver(event, driver_id=driver_id, driver_key=driver_key)
            drivers[driver_id] = self._apply_event(driver, event)

        return sorted(
            drivers.values(),
            key=lambda item: (
                str(item.get("asset") or ""),
                -abs(float((item.get("state") or {}).get("strength") or 0.0)),
                str(item.get("driver_id") or ""),
            ),
        )

    def update_from_signals(
        self,
        signals: list[dict[str, Any]],
        *,
        previous_drivers: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        drivers = {
            str(driver.get("driver_id")): self._normalize_previous(driver)
            for driver in previous_drivers or []
            if driver.get("driver_id")
        }

        for signal in sorted(signals, key=lambda item: str(item.get("effective_time") or "")):
            driver_id, driver_key = self.signal_driver_identity(signal)
            driver = drivers.get(driver_id) or self._new_driver_from_signal(
                signal,
                driver_id=driver_id,
                driver_key=driver_key,
            )
            drivers[driver_id] = self._apply_signal(driver, signal)

        return sorted(
            drivers.values(),
            key=lambda item: (
                str(item.get("asset") or ""),
                -abs(float((item.get("state") or {}).get("strength") or 0.0)),
                str(item.get("driver_id") or ""),
            ),
        )

    def driver_identity(self, event: dict[str, Any]) -> tuple[str, str]:
        asset = normalize_asset(event.get("asset"))
        impact_nodes = [clean_text(node) for node in event.get("impact_nodes") or [] if clean_text(node)]
        driver_key = impact_nodes[0] if impact_nodes else f"{asset}.schema.{clean_text(event.get('event_type'))}"
        return hash_id("DRV", asset, driver_key, length=14), driver_key

    def signal_driver_identity(self, signal: dict[str, Any]) -> tuple[str, str]:
        asset = normalize_asset(signal.get("asset"))
        driver_key = clean_text(signal.get("driver_key") or signal.get("framework_node"))
        if not driver_key:
            driver_key = f"{asset}.schema.signal"
        return clean_text(signal.get("driver_id")) or hash_id("DRV", asset, driver_key, length=14), driver_key

    def _new_driver(self, event: dict[str, Any], *, driver_id: str, driver_key: str) -> dict[str, Any]:
        now = utc_now_iso()
        return {
            "schema_version": DRIVER_SCHEMA_VERSION,
            "driver_id": driver_id,
            "asset": normalize_asset(event.get("asset")),
            "driver_key": driver_key,
            "state": {"strength": 0.0, "trend": "stable", "confidence": 0.0},
            "direction": 0.0,
            "timeline": [],
            "supporting_events": [],
            "contradicting_events": [],
            "supporting_signals": [],
            "contradicting_signals": [],
            "support_score": 0.0,
            "contradiction_score": 0.0,
            "as_of": clean_text(event.get("timestamp")) or now,
            "change_explanation": "",
            "last_event_at": "",
            "created_at": now,
            "updated_at": now,
            "traceability": {"event_ids": [], "raw_source_ids": []},
        }

    def _new_driver_from_signal(self, signal: dict[str, Any], *, driver_id: str, driver_key: str) -> dict[str, Any]:
        now = utc_now_iso()
        return {
            "schema_version": DRIVER_SCHEMA_VERSION,
            "driver_id": driver_id,
            "asset": normalize_asset(signal.get("asset")),
            "driver_key": driver_key,
            "state": {"strength": 0.0, "trend": "stable", "confidence": 0.0},
            "direction": 0.0,
            "timeline": [],
            "supporting_events": [],
            "contradicting_events": [],
            "supporting_signals": [],
            "contradicting_signals": [],
            "support_score": 0.0,
            "contradiction_score": 0.0,
            "as_of": clean_text(signal.get("effective_time")) or now,
            "change_explanation": "",
            "last_event_at": "",
            "created_at": now,
            "updated_at": now,
            "traceability": {"event_ids": [], "signal_ids": [], "raw_source_ids": []},
        }

    def _normalize_previous(self, driver: dict[str, Any]) -> dict[str, Any]:
        state = driver.get("state") if isinstance(driver.get("state"), dict) else {}
        timeline = driver.get("timeline") if isinstance(driver.get("timeline"), list) else []
        supporting = driver.get("supporting_events") if isinstance(driver.get("supporting_events"), list) else []
        contradicting = (
            driver.get("contradicting_events") if isinstance(driver.get("contradicting_events"), list) else []
        )
        normalized = dict(driver)
        normalized["schema_version"] = DRIVER_SCHEMA_VERSION
        normalized["asset"] = normalize_asset(driver.get("asset"))
        normalized["state"] = {
            "strength": round(clamp_float(state.get("strength"), -1.0, 1.0), 4),
            "trend": clean_text(state.get("trend")) if clean_text(state.get("trend")) else "stable",
            "confidence": round(clamp_float(state.get("confidence"), 0.0, 1.0), 4),
        }
        normalized["direction"] = clamp_float(driver.get("direction"), -1.0, 1.0)
        normalized["timeline"] = [item for item in timeline if isinstance(item, dict)]
        normalized["supporting_events"] = [str(item) for item in supporting if clean_text(item)]
        normalized["contradicting_events"] = [str(item) for item in contradicting if clean_text(item)]
        normalized["supporting_signals"] = [
            str(item) for item in driver.get("supporting_signals", []) if clean_text(item)
        ]
        normalized["contradicting_signals"] = [
            str(item) for item in driver.get("contradicting_signals", []) if clean_text(item)
        ]
        normalized["support_score"] = round(clamp_float(driver.get("support_score"), 0.0, 1.0), 4)
        normalized["contradiction_score"] = round(clamp_float(driver.get("contradiction_score"), 0.0, 1.0), 4)
        normalized.setdefault("traceability", {"event_ids": [], "raw_source_ids": []})
        return normalized

    def _apply_event(self, driver: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        event_id = clean_text(event.get("event_id"))
        if not event_id:
            raise ValueError("driver update requires canonical event_id")
        if event_id in {str(item.get("event_id")) for item in driver.get("timeline", [])}:
            return driver

        timestamp = clean_text(event.get("timestamp")) or utc_now_iso()
        previous_state = driver.get("state") if isinstance(driver.get("state"), dict) else {}
        previous_strength = clamp_float(previous_state.get("strength"), -1.0, 1.0)
        previous_direction = clamp_float(driver.get("direction"), -1.0, 1.0)
        if abs(previous_direction) < 0.05:
            previous_direction = previous_strength
        decayed_strength = self._apply_decay(previous_strength, driver.get("last_event_at"), timestamp)

        impact = self._event_impact(event)
        contradicts = (
            abs(decayed_strength) >= 0.05
            and abs(impact) >= 0.05
            and decayed_strength * impact < 0
        )
        support = 0.0 if contradicts else abs(impact)
        contradiction = abs(impact) if contradicts else 0.0
        next_strength = round(clamp_float(decayed_strength + impact, -1.0, 1.0), 4)
        next_direction = next_strength
        trend = self._trend(previous_strength, next_strength, previous_direction, next_direction, contradicts)
        confidence = self._next_confidence(driver, event.get("confidence"))

        timeline_item = {
            "event_id": event_id,
            "impact": round(impact, 4),
            "timestamp": timestamp,
            "source_id": clean_text(event.get("source_id")),
        }
        driver = dict(driver)
        driver["state"] = {"strength": next_strength, "trend": trend, "confidence": confidence}
        driver["direction"] = round(next_direction, 4)
        driver.setdefault("timeline", []).append(timeline_item)
        if contradicts:
            driver.setdefault("contradicting_events", []).append(event_id)
        else:
            driver.setdefault("supporting_events", []).append(event_id)
        driver["support_score"] = round(clamp_float(driver.get("support_score"), 0.0, 1.0) + support, 4)
        driver["contradiction_score"] = round(
            clamp_float(driver.get("contradiction_score"), 0.0, 1.0) + contradiction,
            4,
        )
        driver["support_score"] = clamp_float(driver["support_score"], 0.0, 1.0)
        driver["contradiction_score"] = clamp_float(driver["contradiction_score"], 0.0, 1.0)
        driver["last_event_at"] = timestamp
        driver["as_of"] = timestamp
        driver["updated_at"] = utc_now_iso()
        driver["change_explanation"] = (
            "driver_strength=clip(previous_strength_after_decay + signed_event_impact, -1, 1); "
            f"previous={previous_strength:.4f}, decayed={decayed_strength:.4f}, impact={impact:.4f}"
        )
        driver["last_transition"] = {
            "formula": "Driver(t)=clip(decay(Driver(t-1))+signed_event_impact, -1, 1)",
            "previous_strength": round(previous_strength, 4),
            "decayed_strength": round(decayed_strength, 4),
            "supporting_event_signal": round(support, 4),
            "contradicting_event_signal": round(contradiction, 4),
            "decay": round(decayed_strength - previous_strength, 4),
            "trend": trend,
        }
        driver["traceability"] = self._traceability(driver, event)
        return driver

    def _apply_signal(self, driver: dict[str, Any], signal: dict[str, Any]) -> dict[str, Any]:
        signal_id = clean_text(signal.get("signal_id"))
        if not signal_id:
            raise ValueError("driver update requires signal_id")
        if signal_id in set(driver.get("supporting_signals", []) + driver.get("contradicting_signals", [])):
            return driver

        timestamp = clean_text(signal.get("effective_time")) or utc_now_iso()
        previous_state = driver.get("state") if isinstance(driver.get("state"), dict) else {}
        previous_strength = clamp_float(previous_state.get("strength"), -1.0, 1.0)
        previous_direction = clamp_float(driver.get("direction"), -1.0, 1.0)
        if abs(previous_direction) < 0.05:
            previous_direction = previous_strength
        decayed_strength = self._apply_decay(previous_strength, driver.get("last_event_at"), timestamp)
        impact = self._signal_impact(signal)
        contradicts = abs(decayed_strength) >= 0.05 and abs(impact) >= 0.05 and decayed_strength * impact < 0
        support = 0.0 if contradicts else abs(impact)
        contradiction = abs(impact) if contradicts else 0.0
        next_strength = round(clamp_float(decayed_strength + impact, -1.0, 1.0), 4)
        next_direction = next_strength
        trend = self._trend(previous_strength, next_strength, previous_direction, next_direction, contradicts)
        confidence = self._next_confidence(driver, signal.get("confidence"))
        event_ids = [clean_text(item) for item in signal.get("event_ids", []) if clean_text(item)]
        source_id = clean_text((signal.get("traceability") or {}).get("source_id"))
        timeline_item = {
            "event_id": event_ids[0] if event_ids else "",
            "signal_id": signal_id,
            "causal_path_ids": [clean_text(item) for item in signal.get("causal_path_ids", []) if clean_text(item)],
            "impact": round(impact, 4),
            "timestamp": timestamp,
            "source_id": source_id,
        }
        driver = dict(driver)
        driver["state"] = {"strength": next_strength, "trend": trend, "confidence": confidence}
        driver["direction"] = round(next_direction, 4)
        driver.setdefault("timeline", []).append(timeline_item)
        if contradicts:
            driver.setdefault("contradicting_signals", []).append(signal_id)
            driver.setdefault("contradicting_events", []).extend(event_ids)
        else:
            driver.setdefault("supporting_signals", []).append(signal_id)
            driver.setdefault("supporting_events", []).extend(event_ids)
        driver["supporting_events"] = list(dict.fromkeys(driver.get("supporting_events", [])))
        driver["contradicting_events"] = list(dict.fromkeys(driver.get("contradicting_events", [])))
        driver["support_score"] = clamp_float(
            round(clamp_float(driver.get("support_score"), 0.0, 1.0) + support, 4),
            0.0,
            1.0,
        )
        driver["contradiction_score"] = clamp_float(
            round(clamp_float(driver.get("contradiction_score"), 0.0, 1.0) + contradiction, 4),
            0.0,
            1.0,
        )
        driver["last_event_at"] = timestamp
        driver["as_of"] = timestamp
        driver["updated_at"] = utc_now_iso()
        driver["change_explanation"] = (
            "driver_strength=clip(previous_strength_after_decay + signed_signal, -1, 1); "
            f"previous={previous_strength:.4f}, decayed={decayed_strength:.4f}, signal={impact:.4f}"
        )
        driver["last_transition"] = {
            "formula": "Driver(t)=clip(decay(Driver(t-1))+signed_signal, -1, 1)",
            "previous_strength": round(previous_strength, 4),
            "decayed_strength": round(decayed_strength, 4),
            "signed_signal": round(impact, 4),
            "supporting_signal": round(support, 4),
            "contradicting_signal": round(contradiction, 4),
            "decay": round(decayed_strength - previous_strength, 4),
            "trend": trend,
        }
        driver["traceability"] = self._signal_traceability(driver)
        return driver

    def _apply_decay(self, strength: float, previous_time: Any, event_time: Any) -> float:
        previous_text = clean_text(previous_time)
        if not previous_text:
            return strength
        elapsed_hours = max(
            0.0,
            (parse_iso_datetime(event_time) - parse_iso_datetime(previous_text)).total_seconds() / 3600.0,
        )
        decay = self.decay_per_24h * (elapsed_hours / 24.0)
        if strength > 0:
            return clamp_float(strength - decay, 0.0, 1.0)
        if strength < 0:
            return clamp_float(strength + decay, -1.0, 0.0)
        return 0.0

    def _event_impact(self, event: dict[str, Any]) -> float:
        vector = normalize_signal_vector(event.get("signal_vector"))
        confidence = clamp_float(event.get("confidence"), 0.0, 1.0, 0.5)
        direction = directional_value(vector)
        magnitude = signal_magnitude(vector)
        if abs(direction) < 0.05:
            direction = magnitude if magnitude >= 0.05 else 0.05
        return clamp_float(direction * confidence, -1.0, 1.0)

    def _signal_impact(self, signal: dict[str, Any]) -> float:
        direction = clamp_float(signal.get("direction"), -1.0, 1.0)
        magnitude = clamp_float(signal.get("magnitude"), 0.0, 1.0)
        confidence = clamp_float(signal.get("confidence"), 0.0, 1.0, 0.5)
        return clamp_float(direction * magnitude * confidence, -1.0, 1.0)

    def _next_direction(self, previous_direction: float, decayed_strength: float, impact: float) -> float:
        total_weight = max(0.0, decayed_strength) + abs(impact)
        if total_weight <= 0:
            return 0.0
        return clamp_float(
            (previous_direction * max(0.0, decayed_strength) + impact) / total_weight,
            -1.0,
            1.0,
        )

    def _trend(
        self,
        previous_strength: float,
        next_strength: float,
        previous_direction: float,
        next_direction: float,
        contradicts: bool,
    ) -> str:
        if (
            contradicts
            or (
                abs(previous_direction) >= 0.1
                and abs(next_direction) >= 0.1
                and previous_direction * next_direction < 0
            )
        ):
            return "reversing"
        if abs(next_strength) >= abs(previous_strength) + 0.03:
            return "strengthening"
        if abs(next_strength) <= abs(previous_strength) - 0.03:
            return "weakening"
        return "stable"

    def _next_confidence(self, driver: dict[str, Any], confidence_value: Any) -> float:
        previous = clamp_float((driver.get("state") or {}).get("confidence"), 0.0, 1.0)
        incoming = clamp_float(confidence_value, 0.0, 1.0, 0.5)
        count = max(0, len(driver.get("timeline", []) or []))
        return round(clamp_float((previous * count + incoming) / (count + 1), 0.0, 1.0), 4)

    def _traceability(self, driver: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        timeline = [item for item in driver.get("timeline", []) if isinstance(item, dict)]
        event_ids = [str(item.get("event_id")) for item in timeline if clean_text(item.get("event_id"))]
        source_ids = [str(item.get("source_id")) for item in timeline if clean_text(item.get("source_id"))]
        raw_to_event_to_driver = [
            {
                "source_id": item.get("source_id", ""),
                "event_id": item.get("event_id", ""),
                "driver_id": driver.get("driver_id", ""),
            }
            for item in timeline
        ]
        if clean_text(event.get("source_id")) and clean_text(event.get("event_id")):
            raw_to_event_to_driver[-1]["source_id"] = clean_text(event.get("source_id"))
        return {
            "event_ids": event_ids,
            "raw_source_ids": source_ids,
            "raw_to_event_to_driver": raw_to_event_to_driver,
        }

    def _signal_traceability(self, driver: dict[str, Any]) -> dict[str, Any]:
        timeline = [item for item in driver.get("timeline", []) if isinstance(item, dict)]
        event_ids = [str(item.get("event_id")) for item in timeline if clean_text(item.get("event_id"))]
        signal_ids = [str(item.get("signal_id")) for item in timeline if clean_text(item.get("signal_id"))]
        source_ids = [str(item.get("source_id")) for item in timeline if clean_text(item.get("source_id"))]
        return {
            "event_ids": list(dict.fromkeys(event_ids)),
            "signal_ids": list(dict.fromkeys(signal_ids)),
            "raw_source_ids": list(dict.fromkeys(source_ids)),
            "event_to_signal_to_driver": [
                {
                    "source_id": item.get("source_id", ""),
                    "event_id": item.get("event_id", ""),
                    "signal_id": item.get("signal_id", ""),
                    "driver_id": driver.get("driver_id", ""),
                    "causal_path_ids": item.get("causal_path_ids", []),
                }
                for item in timeline
            ],
        }
