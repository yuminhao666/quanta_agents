from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from quanta_agents.asset_event_state.mapper import EventMapper
from quanta_agents.asset_event_state.models import (
    EVENT_SCHEMA_VERSION,
    SIGNAL_KEYS,
    clean_text,
    hash_id,
    normalize_asset,
    normalize_confidence,
    normalize_event_type,
    normalize_iso,
    normalize_source_type,
    normalize_signal_vector,
    utc_now_iso,
)
from quanta_agents.core.io import relative_to_root
from quanta_agents.core.llm_json import parse_json_object


EXTRACTOR_VERSION = "event_extractor_service.v1"

BULLISH_TERMS = (
    "上涨",
    "走强",
    "利多",
    "反弹",
    "短缺",
    "减产",
    "罢工",
    "停产",
    "库存下降",
    "去库",
    "补库",
    "降息",
    "弱美元",
    "需求改善",
    "bullish",
    "rally",
)

BEARISH_TERMS = (
    "下跌",
    "走弱",
    "利空",
    "回落",
    "过剩",
    "增产",
    "复产",
    "库存增加",
    "累库",
    "加息",
    "强美元",
    "需求疲弱",
    "bearish",
    "selloff",
)


class EventExtractor:
    def __init__(self, mapper: EventMapper, *, quanta_root: str | Path | None = None):
        self.mapper = mapper
        self.quanta_root = Path(quanta_root).expanduser() if quanta_root else None

    def extract_events(self, payload: dict[str, Any], *, use_llm: bool = False) -> list[dict[str, Any]]:
        candidates = self._structured_candidates(payload)
        if not candidates and use_llm:
            candidates = self._extract_with_llm(payload)
        if not candidates:
            candidates = [self._rule_candidate(payload)]

        events = [self._canonical_event(candidate, payload) for candidate in candidates]
        if not events:
            raise ValueError("no canonical events were extracted")
        return events

    def _structured_candidates(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if isinstance(payload.get("events"), list):
            return [item for item in payload["events"] if isinstance(item, dict)]
        if isinstance(payload.get("event"), dict):
            return [payload["event"]]
        if all(key in payload for key in ("summary", "signal_vector", "source_id")):
            return [payload]
        return []

    def _extract_with_llm(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        text = clean_text(payload.get("text") or payload.get("content") or "")
        if not text:
            return []
        try:
            from quanta_agents.core.llm_client import chat_messages
        except Exception:
            return []

        request = {
            "source_id": payload.get("source_id") or payload.get("raw_id") or "",
            "timestamp": payload.get("timestamp") or "",
            "text": text[:5000],
            "allowed_assets": ["Copper", "Gold", "Oil", "Macro", "Other"],
            "allowed_event_types": ["policy", "macro", "supply", "demand", "sentiment", "data", "research"],
            "required_output": {
                "events": [
                    {
                        "asset": "Copper",
                        "event_type": "policy",
                        "summary": "一句话事件",
                        "key_facts": ["fact"],
                        "signal_vector": {
                            "price": 0.0,
                            "supply": 0.0,
                            "demand": 0.0,
                            "macro": 0.0,
                            "sentiment": 0.0,
                        },
                        "confidence": 0.0,
                    }
                ]
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 quanta_data Event Canonical Layer 的抽取器。只输出 JSON 对象，"
                    "禁止输出最终投研报告。所有判断必须落到 canonical events。"
                ),
            },
            {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
        ]
        try:
            response = chat_messages(
                messages,
                max_tokens=1600,
                temperature=0.1,
                timeout=90,
                env_prefix="QUANTA_ASSET_EVENT_LLM",
            )
            parsed = parse_json_object(response)
        except Exception:
            return []
        events = parsed.get("events")
        return [item for item in events if isinstance(item, dict)] if isinstance(events, list) else []

    def _rule_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = clean_text(payload.get("text") or payload.get("content") or payload.get("summary"))
        if not text:
            raise ValueError("text/content/summary is required for event extraction")
        asset = self.mapper.classify_asset(text, payload.get("asset"))
        event_type = self._event_type_hint(payload, text)
        return {
            "asset": asset,
            "event_type": event_type,
            "summary": self._summary(text),
            "key_facts": self._key_facts(text),
            "signal_vector": self._estimate_signal_vector(text, asset=asset, event_type=event_type),
            "confidence": self._estimate_confidence(text, asset=asset, event_type=event_type),
        }

    def _canonical_event(self, candidate: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        text = clean_text(
            candidate.get("text")
            or payload.get("text")
            or payload.get("content")
            or candidate.get("summary")
            or "",
            limit=8000,
        )
        asset = self.mapper.classify_asset(text, candidate.get("asset") or payload.get("asset"))
        event_type = self._event_type_hint({**payload, **candidate}, text)
        timestamp = normalize_iso(candidate.get("timestamp") or payload.get("timestamp") or utc_now_iso())
        update_time = normalize_iso(candidate.get("update_time") or payload.get("update_time") or utc_now_iso())
        effective_time = normalize_iso(candidate.get("effective_time") or payload.get("effective_time") or timestamp)
        source_id = self._source_id(candidate, payload, text)
        source_type = normalize_source_type(
            candidate.get("source_type")
            or payload.get("source_type")
            or candidate.get("source_name")
            or payload.get("source_name")
        )
        signal_vector = normalize_signal_vector(candidate.get("signal_vector"))
        if not any(abs(signal_vector[key]) > 0 for key in SIGNAL_KEYS):
            signal_vector = self._estimate_signal_vector(text, asset=asset, event_type=event_type)

        key_facts = candidate.get("key_facts")
        if not isinstance(key_facts, list):
            key_facts = self._key_facts(text)
        key_facts = [clean_text(item, limit=240) for item in key_facts if clean_text(item)][:8]

        node_mappings = self.mapper.map_nodes(asset=asset, event_type=event_type, text=text)
        impact_nodes = candidate.get("impact_nodes")
        if not isinstance(impact_nodes, list) or not impact_nodes:
            impact_nodes = [item["node"] for item in node_mappings]
        impact_nodes = [clean_text(item) for item in impact_nodes if clean_text(item)][:8]
        if not impact_nodes:
            impact_nodes = [node_mappings[0]["node"]]

        source_ref = self._source_ref(source_id, payload)
        default_confidence = self._estimate_confidence(text, asset=asset, event_type=event_type)
        event_id = candidate.get("event_id") or hash_id(
            "EV",
            source_id,
            timestamp[:10],
            asset,
            event_type,
            clean_text(candidate.get("summary") or text, limit=500),
            length=14,
        )
        return {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event_id": str(event_id),
            "asset": normalize_asset(asset),
            "event_type": normalize_event_type(event_type),
            "summary": clean_text(candidate.get("summary") or self._summary(text), limit=500),
            "key_facts": key_facts,
            "signal_vector": signal_vector,
            "impact_nodes": impact_nodes,
            "node_mappings": node_mappings,
            "confidence": normalize_confidence(candidate.get("confidence"), default_confidence),
            "source_type": source_type,
            "source_id": source_id,
            "source_ref": source_ref,
            "timestamp": timestamp,
            "event_time": timestamp,
            "update_time": update_time,
            "effective_time": effective_time,
            "created_at": utc_now_iso(),
            "extractor_version": EXTRACTOR_VERSION,
            "raw_payload": {
                "source_type": clean_text(payload.get("source_type")),
                "raw_id": clean_text(payload.get("raw_id") or payload.get("source_id")),
                "extraction_mode": "structured" if candidate is not payload else "direct",
            },
        }

    def _source_id(self, candidate: dict[str, Any], payload: dict[str, Any], text: str) -> str:
        value = (
            candidate.get("source_id")
            or payload.get("source_id")
            or payload.get("raw_id")
            or payload.get("id")
            or ""
        )
        if clean_text(value):
            return clean_text(value)
        return hash_id("RAW-SYN", text, length=12)

    def _event_type_hint(self, payload: dict[str, Any], text: str) -> str:
        explicit = clean_text(payload.get("event_type"))
        if explicit:
            return self.mapper.classify_event_type(text, explicit)
        source_type = normalize_source_type(payload.get("source_type") or payload.get("source_name"))
        if source_type == "research":
            return "research"
        if source_type == "data":
            return "data"
        return self.mapper.classify_event_type(text, "")

    def _source_ref(self, source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        path_value = clean_text(payload.get("source_path") or payload.get("path"))
        path = path_value
        if path_value and self.quanta_root:
            path = relative_to_root(Path(path_value).expanduser(), self.quanta_root)
        return {
            "ref_type": clean_text(payload.get("source_ref_type") or "raw"),
            "source_name": clean_text(payload.get("source_name") or payload.get("source_type") or "quanta_data"),
            "id": source_id,
            "path": path,
            "url": clean_text(payload.get("url")),
        }

    def _summary(self, text: str) -> str:
        parts = re.split(r"(?<=[。！？.!?])\s*", clean_text(text))
        return clean_text(parts[0] if parts else text, limit=180)

    def _key_facts(self, text: str) -> list[str]:
        parts = [clean_text(part, limit=240) for part in re.split(r"[。！？!?；;\n]+", clean_text(text))]
        return [part for part in parts if len(part) >= 4][:4] or ([clean_text(text, limit=180)] if text else [])

    def _estimate_signal_vector(self, text: str, *, asset: str, event_type: str) -> dict[str, float]:
        normalized_text = text.lower()
        signal = {key: 0.0 for key in SIGNAL_KEYS}
        bullish_hits = sum(1 for term in BULLISH_TERMS if term.lower() in normalized_text)
        bearish_hits = sum(1 for term in BEARISH_TERMS if term.lower() in normalized_text)
        directional_bias = max(-1.0, min(1.0, (bullish_hits - bearish_hits) * 0.25))

        if event_type == "supply":
            if any(term in normalized_text for term in ("减产", "罢工", "停产", "短缺", "supply disruption")):
                signal["supply"] = -0.6
                signal["price"] = 0.55
            elif any(term in normalized_text for term in ("增产", "复产", "供应增加", "supply growth")):
                signal["supply"] = 0.5
                signal["price"] = -0.35
            else:
                signal["supply"] = directional_bias or 0.2
        elif event_type == "demand":
            signal["demand"] = directional_bias or 0.35
            signal["price"] = directional_bias or 0.25
        elif event_type == "macro":
            signal["macro"] = directional_bias or 0.25
            signal["price"] = directional_bias * 0.7
        elif event_type == "policy":
            signal["macro"] = 0.25 if directional_bias >= 0 else -0.25
            signal["price"] = directional_bias or 0.25
        elif event_type == "data":
            signal["price"] = directional_bias
            signal["supply"] = directional_bias * -0.4 if any(term in normalized_text for term in ("库存", "仓单")) else 0.0
            signal["sentiment"] = directional_bias * 0.35
        elif event_type == "research":
            signal["price"] = directional_bias or 0.2
            signal["sentiment"] = directional_bias or 0.2
        else:
            signal["sentiment"] = directional_bias
            signal["price"] = directional_bias * 0.8

        if "库存下降" in normalized_text or "去库" in normalized_text:
            signal["supply"] = min(signal["supply"], -0.35)
            signal["price"] = max(signal["price"], 0.35)
        if "库存增加" in normalized_text or "累库" in normalized_text:
            signal["supply"] = max(signal["supply"], 0.35)
            signal["price"] = min(signal["price"], -0.25)
        if "中国" in normalized_text or "国内" in normalized_text:
            signal["demand"] = signal["demand"] or directional_bias or 0.2
        if asset == "Macro" and event_type == "macro":
            signal["macro"] = signal["macro"] or 0.3
        return {key: round(max(-1.0, min(1.0, value)), 4) for key, value in signal.items()}

    def _estimate_confidence(self, text: str, *, asset: str, event_type: str) -> float:
        confidence = 0.45
        if asset != "Other":
            confidence += 0.18
        if event_type != "sentiment":
            confidence += 0.12
        if len(text) >= 60:
            confidence += 0.08
        return round(min(0.9, confidence), 4)
