from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from quanta_agents.asset_event_state.models import (
    THEME_NARRATIVE_SCHEMA_VERSION,
    clean_text,
    hash_id,
    utc_now_iso,
)
from quanta_agents.core.llm_json import parse_json_object


class ThemeNarrativeService:
    """Generates the only report-facing narrative layer from driver states."""

    def generate_for_assets(
        self,
        drivers: list[dict[str, Any]],
        *,
        use_llm: bool = False,
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for driver in drivers:
            asset = clean_text(driver.get("asset")) or "Other"
            grouped[asset].append(driver)
        return [
            self.generate(asset=asset, drivers=asset_drivers, use_llm=use_llm)
            for asset, asset_drivers in sorted(grouped.items())
            if asset_drivers
        ]

    def generate(
        self,
        *,
        asset: str,
        drivers: list[dict[str, Any]],
        use_llm: bool = False,
    ) -> dict[str, Any]:
        self._validate_drivers(drivers)
        selected = self._rank_drivers(drivers)
        if use_llm:
            narrative = self._generate_with_llm(asset=asset, drivers=selected)
            if narrative:
                return narrative
        return self._generate_rule_based(asset=asset, drivers=selected)

    def _validate_drivers(self, drivers: list[dict[str, Any]]) -> None:
        if not isinstance(drivers, list) or not drivers:
            raise ValueError("theme narrative requires at least one driver")
        for driver in drivers:
            if not driver.get("driver_id"):
                raise ValueError("driver_id is required for narrative input")
            if not isinstance(driver.get("state"), dict):
                raise ValueError("driver.state is required for narrative input")
            if not isinstance(driver.get("timeline"), list):
                raise ValueError("driver.timeline is required for narrative input")

    def _generate_with_llm(self, *, asset: str, drivers: list[dict[str, Any]]) -> dict[str, Any] | None:
        try:
            from quanta_agents.core.llm_client import chat_messages
        except Exception:
            return None

        driver_input = [self._driver_input(driver) for driver in drivers]
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 quanta Theme/Narrative Layer。只能基于 Driver State Machine 输出 JSON，"
                    "禁止基于 raw data 或单个 event 直接下结论。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "asset": asset,
                        "drivers": driver_input,
                        "required_output": {
                            "market_mainline": "当前市场主线",
                            "driver_changes": ["哪些 driver 在增强/衰退/反转"],
                            "logic_chain": ["driver -> event timeline -> signal -> risk"],
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
                env_prefix="QUANTA_DRIVER_NARRATIVE_LLM",
            )
            parsed = parse_json_object(response)
        except Exception:
            return None
        return self._wrap(asset=asset, drivers=drivers, payload=parsed, method="llm_driver_state_only")

    def _generate_rule_based(self, *, asset: str, drivers: list[dict[str, Any]]) -> dict[str, Any]:
        top = drivers[:5]
        strongest = top[0]
        state = strongest.get("state") or {}
        driver_changes = [
            f"{driver.get('driver_key')} strength={self._strength(driver):.2f} trend={(driver.get('state') or {}).get('trend')}"
            for driver in top
        ]
        logic_chain: list[str] = []
        for driver in top[:3]:
            latest_events = driver.get("timeline", [])[-3:]
            event_bits = [
                f"{item.get('event_id')}({float(item.get('impact') or 0.0):+.2f})"
                for item in latest_events
                if item.get("event_id")
            ]
            logic_chain.append(
                f"{driver.get('driver_key')} -> {' / '.join(event_bits) or 'no events'} -> "
                f"strength {self._strength(driver):.2f}"
            )
        payload = {
            "market_mainline": (
                f"{asset} 当前主线由 {strongest.get('driver_key')} 主导，"
                f"强度 {float(state.get('strength') or 0.0):.2f}，趋势 {state.get('trend')}。"
            ),
            "driver_changes": driver_changes,
            "logic_chain": logic_chain,
            "risk_changes": self._risk_changes(top),
            "future_paths": [
                "同向事件继续进入 driver timeline 时，driver strength 将增强。",
                "反向事件进入同一 driver 时，driver 将转入 reversing 或 weakening。",
                "缺少新增事件时，decay 会降低 driver strength。",
            ],
        }
        return self._wrap(asset=asset, drivers=drivers, payload=payload, method="rule_driver_state_only")

    def _wrap(
        self,
        *,
        asset: str,
        drivers: list[dict[str, Any]],
        payload: dict[str, Any],
        method: str,
    ) -> dict[str, Any]:
        generated_at = utc_now_iso()
        driver_ids = [str(driver.get("driver_id")) for driver in drivers if driver.get("driver_id")]
        event_ids = [
            str(item.get("event_id"))
            for driver in drivers
            for item in driver.get("timeline", [])
            if isinstance(item, dict) and item.get("event_id")
        ]
        return {
            "schema_version": THEME_NARRATIVE_SCHEMA_VERSION,
            "narrative_id": hash_id("DNAR", asset, ",".join(driver_ids), generated_at, length=16),
            "asset": clean_text(asset) or "Other",
            "driver_ids": driver_ids,
            "generated_at": generated_at,
            "method": method,
            "source_policy": "driver_state_only_no_raw_bypass",
            "market_mainline": clean_text(payload.get("market_mainline"), limit=500),
            "driver_changes": self._string_list(payload.get("driver_changes") or payload.get("drivers")),
            "logic_chain": self._string_list(payload.get("logic_chain")),
            "risk_changes": self._string_list(payload.get("risk_changes")),
            "future_paths": self._string_list(payload.get("future_paths")),
            "traceability": {
                "driver_ids": driver_ids,
                "event_ids": event_ids,
                "path": "raw -> event_table -> driver_state_table -> theme_narrative_outputs",
            },
        }

    def _rank_drivers(self, drivers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            drivers,
            key=lambda item: (
                -abs(self._strength(item)),
                str((item.get("state") or {}).get("trend") or ""),
                str(item.get("driver_id") or ""),
            ),
        )

    def _driver_input(self, driver: dict[str, Any]) -> dict[str, Any]:
        return {
            "driver_id": driver.get("driver_id"),
            "asset": driver.get("asset"),
            "driver_key": driver.get("driver_key"),
            "state": driver.get("state"),
            "direction": driver.get("direction"),
            "timeline": driver.get("timeline"),
            "supporting_events": driver.get("supporting_events"),
            "contradicting_events": driver.get("contradicting_events"),
            "last_transition": driver.get("last_transition"),
        }

    def _strength(self, driver: dict[str, Any]) -> float:
        try:
            return float((driver.get("state") or {}).get("strength") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _risk_changes(self, drivers: list[dict[str, Any]]) -> list[str]:
        reversing = [driver for driver in drivers if (driver.get("state") or {}).get("trend") == "reversing"]
        weakening = [driver for driver in drivers if (driver.get("state") or {}).get("trend") == "weakening"]
        strengthening = [
            driver for driver in drivers if (driver.get("state") or {}).get("trend") == "strengthening"
        ]
        risks: list[str] = []
        if reversing:
            risks.append("存在 driver 反转，原有主线一致性下降。")
        if weakening:
            risks.append("部分 driver 正在衰退，趋势延续性下降。")
        if strengthening:
            risks.append("增强中的 driver 可能推高拥挤交易和预期过度定价风险。")
        return risks or ["driver 仍处低强度状态，需要更多事件确认。"]

    def _string_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [clean_text(item, limit=500) for item in value if clean_text(item)]
        text = clean_text(value)
        return [text] if text else []
