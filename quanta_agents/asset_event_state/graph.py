from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from quanta_agents.asset_event_state.models import (
    clean_text,
    directional_value,
    hash_id,
    normalize_asset,
    normalize_confidence,
    normalize_source_type,
    normalize_signal_vector,
    signal_magnitude,
    utc_now_iso,
)


DEFAULT_CAUSAL_EDGES: list[dict[str, Any]] = [
    {
        "from": "Macro.schema.Liquidity",
        "to": "Copper.schema.Macro",
        "weight": 0.78,
        "polarity": 1,
        "delay": "hours",
        "confidence": 0.82,
        "assumption": "宏观流动性变化会先影响铜的金融属性定价。",
    },
    {
        "from": "Copper.schema.Policy",
        "to": "Copper.schema.Macro",
        "weight": 0.72,
        "polarity": 1,
        "delay": "hours",
        "confidence": 0.74,
        "assumption": "铜相关政策或关税预期会传导到宏观与跨市定价。",
    },
    {
        "from": "Copper.schema.Supply",
        "to": "Copper.schema.Sentiment",
        "weight": 0.64,
        "polarity": 1,
        "delay": "hours",
        "confidence": 0.68,
        "assumption": "供应扰动通常会先改变市场情绪和风险溢价。",
    },
    {
        "from": "Copper.schema.Data",
        "to": "Copper.schema.Supply",
        "weight": 0.7,
        "polarity": 1,
        "delay": "hours",
        "confidence": 0.72,
        "assumption": "库存、升贴水、持仓等数据可以作为供需状态的观察证据。",
    },
    {
        "from": "Copper.schema.Research",
        "to": "Copper.schema.Sentiment",
        "weight": 0.45,
        "polarity": 1,
        "delay": "days",
        "confidence": 0.58,
        "assumption": "研报观点影响认知状态，但权重低于可验证数据。",
    },
    {
        "from": "Copper.schema.Demand",
        "to": "Copper.schema.Sentiment",
        "weight": 0.58,
        "polarity": 1,
        "delay": "days",
        "confidence": 0.62,
        "assumption": "需求预期会传导到价格情绪，但需要后续数据验证。",
    },
]


class GraphLayerService:
    """Builds append-only graph mutations from canonical events.

    The service deliberately returns graph event records instead of mutating an
    in-memory graph. `AssetEventStore.record_graph_events` persists those records
    so graph state can be rebuilt from logs.
    """

    def build_graph_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        now = utc_now_iso()
        for event in events:
            event_id = clean_text(event.get("event_id"))
            if not event_id:
                continue
            records.append(
                self._record(
                    graph_type="event",
                    action="event_observed",
                    event_id=event_id,
                    node_id=event_id,
                    update_time=now,
                    payload={
                        "asset": event.get("asset"),
                        "event_type": event.get("event_type"),
                        "summary": event.get("summary"),
                        "source_id": event.get("source_id"),
                    },
                )
            )
            for node in event.get("impact_nodes") or []:
                node_id = clean_text(node)
                if not node_id:
                    continue
                records.append(
                    self._record(
                        graph_type="industry",
                        action="event_mapped_to_industry_node",
                        event_id=event_id,
                        node_id=node_id,
                        update_time=now,
                        payload={
                            "asset": event.get("asset"),
                            "node_id": node_id,
                            "mapping_basis": "canonical_event.impact_nodes",
                        },
                    )
                )
                records.append(
                    self._record(
                        graph_type="causal",
                        action="causal_node_activated",
                        event_id=event_id,
                        node_id=node_id,
                        update_time=now,
                        payload={
                            "asset": event.get("asset"),
                            "node_id": node_id,
                            "signal_vector": event.get("signal_vector"),
                            "confidence": event.get("confidence"),
                        },
                    )
                )
        return records

    def _record(
        self,
        *,
        graph_type: str,
        action: str,
        event_id: str,
        node_id: str = "",
        edge_from: str = "",
        edge_to: str = "",
        update_time: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        log_id = hash_id("GLOG", graph_type, action, event_id, node_id, edge_from, edge_to, update_time, length=18)
        return {
            "log_id": log_id,
            "graph_type": graph_type,
            "action": action,
            "event_id": event_id,
            "node_id": node_id,
            "edge_from": edge_from,
            "edge_to": edge_to,
            "update_time": update_time,
            "payload": payload,
        }


class CausalPropagationEngine:
    """Propagates node activations into traceable paths and signal updates."""

    def __init__(
        self,
        *,
        edges: list[dict[str, Any]] | None = None,
        max_hops: int = 4,
        depth_decay_base: float = 0.8,
        min_path_confidence: float = 0.05,
    ):
        self.edges = [dict(edge) for edge in (edges or DEFAULT_CAUSAL_EDGES)]
        self.max_hops = max(0, int(max_hops))
        self.depth_decay_base = max(0.0, min(1.0, float(depth_decay_base)))
        self.min_path_confidence = max(0.0, min(1.0, float(min_path_confidence)))
        self.edges_by_from: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in self.edges:
            self.edges_by_from[clean_text(edge.get("from"))].append(edge)

    def propagate(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        paths: list[dict[str, Any]] = []
        for event in events:
            for node in event.get("impact_nodes") or []:
                node_id = clean_text(node)
                if not node_id:
                    continue
                paths.extend(self._paths_from_node(event, node_id))
        return paths

    def signals_from_paths(
        self,
        paths: list[dict[str, Any]],
        events_by_id: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        signals: list[dict[str, Any]] = []
        for path in paths:
            event = events_by_id.get(clean_text(path.get("event_id")), {})
            signal = self._signal_from_path(path, event)
            if signal:
                signals.append(signal)
        return signals

    def _paths_from_node(self, event: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
        event_id = clean_text(event.get("event_id"))
        asset = normalize_asset(event.get("asset"))
        event_confidence = normalize_confidence(event.get("confidence"))
        base_signal = self._base_signal(event)
        effective_time = clean_text(event.get("effective_time") or event.get("timestamp") or utc_now_iso())
        paths: list[dict[str, Any]] = [
            self._path(
                event=event,
                final_node=node_id,
                steps=[],
                path_signal=base_signal,
                path_confidence=event_confidence,
                effective_time=effective_time,
                path_type="observed",
            )
        ]
        queue: deque[tuple[str, list[dict[str, Any]], float, float]] = deque()
        queue.append((node_id, [], base_signal, event_confidence))
        while queue:
            current, steps, signal, confidence = queue.popleft()
            if len(steps) >= self.max_hops:
                continue
            for edge in self.edges_by_from.get(current, []):
                next_node = clean_text(edge.get("to"))
                if not next_node or any(step.get("to_node") == next_node for step in steps):
                    continue
                hop = {
                    "from_node": current,
                    "to_node": next_node,
                    "polarity": int(edge.get("polarity") or 1),
                    "edge_weight": float(edge.get("weight") or 0.0),
                    "confidence": normalize_confidence(edge.get("confidence")),
                    "delay_hint": clean_text(edge.get("delay") or "days"),
                }
                next_steps = [*steps, hop]
                depth_decay = self.depth_decay_base ** max(0, len(next_steps) - 1)
                next_signal = signal * hop["polarity"] * hop["edge_weight"]
                next_confidence = event_confidence * self._edge_confidence_product(next_steps) * depth_decay
                if next_confidence < self.min_path_confidence:
                    continue
                path = self._path(
                    event=event,
                    final_node=next_node,
                    steps=next_steps,
                    path_signal=next_signal,
                    path_confidence=next_confidence,
                    effective_time=effective_time,
                    path_type="propagated",
                )
                paths.append(path)
                queue.append((next_node, next_steps, next_signal, next_confidence))
        return paths

    def _path(
        self,
        *,
        event: dict[str, Any],
        final_node: str,
        steps: list[dict[str, Any]],
        path_signal: float,
        path_confidence: float,
        effective_time: str,
        path_type: str,
    ) -> dict[str, Any]:
        event_id = clean_text(event.get("event_id"))
        path_id = hash_id("CPATH", event_id, final_node, json_dumps_stable(steps), round(path_signal, 6), length=16)
        used_pairs = {(step.get("from_node"), step.get("to_node")) for step in steps}
        return {
            "path_id": path_id,
            "event_id": event_id,
            "target_asset": normalize_asset(event.get("asset")),
            "steps": steps,
            "path_signal": round(max(-1.0, min(1.0, path_signal)), 4),
            "path_confidence": round(normalize_confidence(path_confidence), 4),
            "assumptions": [
                clean_text(edge.get("assumption"))
                for edge in self.edges
                if (edge.get("from"), edge.get("to")) in used_pairs and clean_text(edge.get("assumption"))
            ],
            "alternative_paths": [],
            "final_node": final_node,
            "path_type": path_type,
            "effective_time": effective_time,
            "created_at": utc_now_iso(),
        }

    def _signal_from_path(self, path: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        signal_value = float(path.get("path_signal") or 0.0)
        confidence = normalize_confidence(path.get("path_confidence"))
        if abs(signal_value) < 0.01 or confidence < self.min_path_confidence:
            return {}
        event_id = clean_text(path.get("event_id"))
        final_node = clean_text(path.get("final_node"))
        asset = normalize_asset(path.get("target_asset") or event.get("asset"))
        driver_id = hash_id("DRV", asset, final_node, length=14)
        signal_id = hash_id("SIG", event_id, path.get("path_id"), final_node, round(signal_value, 4), length=16)
        direction = 1 if signal_value > 0 else -1
        return {
            "signal_id": signal_id,
            "asset": asset,
            "framework_node": final_node,
            "driver_id": driver_id,
            "driver_key": final_node,
            "direction": direction,
            "magnitude": round(abs(signal_value), 4),
            "confidence": confidence,
            "source_type": normalize_source_type(event.get("source_type")),
            "event_ids": [event_id],
            "causal_path_ids": [path.get("path_id")],
            "relation": "supports" if direction > 0 else "contradicts",
            "effective_time": clean_text(path.get("effective_time") or event.get("effective_time") or event.get("timestamp")),
            "status": "accepted" if confidence >= 0.25 else "low_confidence",
            "traceability": {
                "source_id": clean_text(event.get("source_id")),
                "event_id": event_id,
                "causal_path_id": path.get("path_id"),
                "hop_count": len(path.get("steps") or []),
            },
        }

    def _base_signal(self, event: dict[str, Any]) -> float:
        vector = normalize_signal_vector(event.get("signal_vector"))
        direction = directional_value(vector)
        magnitude = signal_magnitude(vector)
        if abs(direction) < 0.05:
            direction = 1.0 if magnitude >= 0.05 else 0.0
        return max(-1.0, min(1.0, direction * max(0.05, magnitude)))

    def _edge_confidence_product(self, steps: list[dict[str, Any]]) -> float:
        product = 1.0
        for step in steps:
            product *= normalize_confidence(step.get("confidence"))
        return product


def json_dumps_stable(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
