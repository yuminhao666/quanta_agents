from __future__ import annotations

import json
from typing import Any

from quanta_agents.asset_event_state.models import (
    NARRATIVE_SCHEMA_VERSION,
    clean_text,
    hash_id,
    utc_now_iso,
)
from quanta_agents.core.llm_json import parse_json_object


class NarrativeService:
    """Legacy cluster narrative helper.

    New report generation must use ThemeNarrativeService, which only accepts
    Driver State Machine outputs. This helper is retained for old cluster
    compatibility and must be explicitly enabled by callers.
    """

    def generate(
        self,
        cluster: dict[str, Any],
        *,
        use_llm: bool = False,
        allow_legacy_cluster: bool = False,
    ) -> dict[str, Any]:
        if not allow_legacy_cluster:
            raise RuntimeError("cluster narrative is legacy; use ThemeNarrativeService with driver states")
        self._validate_cluster_input(cluster)
        if use_llm:
            narrative = self._generate_with_llm(cluster)
            if narrative:
                return narrative
        return self._generate_rule_based(cluster)

    def _validate_cluster_input(self, cluster: dict[str, Any]) -> None:
        if not isinstance(cluster, dict):
            raise ValueError("narrative input must be a cluster object")
        if not cluster.get("cluster_id"):
            raise ValueError("cluster_id is required")
        if not isinstance(cluster.get("events"), list):
            raise ValueError("cluster.events is required")
        if not isinstance(cluster.get("net_signal"), dict):
            raise ValueError("cluster.net_signal is required")

    def _generate_with_llm(self, cluster: dict[str, Any]) -> dict[str, Any] | None:
        try:
            from quanta_agents.core.llm_client import chat_messages
        except Exception:
            return None

        cluster_input = self._cluster_input(cluster)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是投研 Narrative Layer。只能基于输入 cluster 输出 JSON，"
                    "禁止引用 cluster 外部 raw 原文，禁止跳过 event layer。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "cluster": cluster_input,
                        "required_output": {
                            "market_mainline": "当前市场在交易什么",
                            "drivers": ["驱动来源"],
                            "logic_chain": ["事件 -> 节点 -> 信号 -> 价格/风险"],
                            "risk_changes": ["风险变化"],
                            "future_paths": ["未来路径"],
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            response = chat_messages(
                messages,
                max_tokens=1400,
                temperature=0.2,
                timeout=90,
                env_prefix="QUANTA_ASSET_EVENT_NARRATIVE_LLM",
            )
            parsed = parse_json_object(response)
        except Exception:
            return None
        return self._wrap_narrative(cluster, parsed, method="llm_cluster_only")

    def _generate_rule_based(self, cluster: dict[str, Any]) -> dict[str, Any]:
        net_signal = cluster.get("net_signal") or {}
        asset = clean_text(cluster.get("asset")) or "Other"
        theme = clean_text(cluster.get("theme"), limit=140)
        dominant_signal = self._dominant_signal(net_signal)
        event_details = [item for item in cluster.get("event_details", []) if isinstance(item, dict)]
        drivers = sorted({clean_text(item.get("event_type")) for item in event_details if clean_text(item.get("event_type"))})
        impact_nodes = sorted(
            {
                clean_text(node)
                for item in event_details
                for node in (item.get("impact_nodes") or [])
                if clean_text(node)
            }
        )
        payload = {
            "market_mainline": f"{asset} 当前主线集中在 {theme}，净信号偏向 {dominant_signal}。",
            "drivers": drivers or [clean_text(cluster.get("theme"))],
            "logic_chain": [
                f"{len(event_details)} 个 canonical event 聚合为 {cluster.get('cluster_id')}",
                f"影响节点：{', '.join(impact_nodes[:5]) if impact_nodes else '未识别节点'}",
                f"净信号：{json.dumps(net_signal, ensure_ascii=False)}",
                f"状态机：{cluster.get('status')}（{cluster.get('state_reason', '')}）",
            ],
            "risk_changes": self._risk_changes(cluster),
            "future_paths": [
                "若同向事件继续进入 72h 窗口，cluster 将保持 strengthening。",
                "若价格/供需/宏观信号方向翻转，cluster 将转入 reversing。",
                "若新增事件强度下降或时间衰减占主导，cluster 将转入 fading。",
            ],
        }
        return self._wrap_narrative(cluster, payload, method="rule_cluster_only")

    def _wrap_narrative(self, cluster: dict[str, Any], payload: dict[str, Any], *, method: str) -> dict[str, Any]:
        generated_at = utc_now_iso()
        return {
            "schema_version": NARRATIVE_SCHEMA_VERSION,
            "narrative_id": hash_id("NAR", cluster.get("cluster_id"), generated_at, length=16),
            "cluster_id": cluster["cluster_id"],
            "asset": clean_text(cluster.get("asset")),
            "generated_at": generated_at,
            "method": method,
            "source_policy": "cluster_only_no_raw_bypass",
            "market_mainline": clean_text(payload.get("market_mainline"), limit=500),
            "drivers": self._string_list(payload.get("drivers")),
            "logic_chain": self._string_list(payload.get("logic_chain")),
            "risk_changes": self._string_list(payload.get("risk_changes")),
            "future_paths": self._string_list(payload.get("future_paths")),
            "traceability": {
                "cluster_id": cluster["cluster_id"],
                "event_ids": [str(item) for item in cluster.get("events", [])],
            },
        }

    def _cluster_input(self, cluster: dict[str, Any]) -> dict[str, Any]:
        return {
            "cluster_id": cluster.get("cluster_id"),
            "asset": cluster.get("asset"),
            "theme": cluster.get("theme"),
            "events": cluster.get("events"),
            "event_summaries": [
                {
                    "event_id": event.get("event_id"),
                    "event_type": event.get("event_type"),
                    "summary": event.get("summary"),
                    "signal_vector": event.get("signal_vector"),
                    "impact_nodes": event.get("impact_nodes"),
                    "timestamp": event.get("timestamp"),
                    "confidence": event.get("confidence"),
                }
                for event in cluster.get("event_details", [])
                if isinstance(event, dict)
            ],
            "net_signal": cluster.get("net_signal"),
            "status": cluster.get("status"),
            "state_reason": cluster.get("state_reason"),
            "time_window": cluster.get("time_window"),
        }

    def _dominant_signal(self, net_signal: dict[str, Any]) -> str:
        if not net_signal:
            return "neutral"
        key, value = max(net_signal.items(), key=lambda item: abs(float(item[1] or 0.0)))
        numeric = float(value or 0.0)
        if numeric > 0.05:
            return f"{key}+"
        if numeric < -0.05:
            return f"{key}-"
        return "neutral"

    def _risk_changes(self, cluster: dict[str, Any]) -> list[str]:
        status = clean_text(cluster.get("status"))
        if status == "reversing":
            return ["主线方向发生反转，原有交易假设需要复核。"]
        if status == "strengthening":
            return ["同向事件增加，拥挤交易和预期过度定价风险上升。"]
        if status == "fading":
            return ["新增证据边际走弱，趋势延续风险下降但假突破风险上升。"]
        return ["事件仍处形成期，需要等待更多同向证据确认。"]

    def _string_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [clean_text(item, limit=500) for item in value if clean_text(item)]
        text = clean_text(value)
        return [text] if text else []
