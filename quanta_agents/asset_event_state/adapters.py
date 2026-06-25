from __future__ import annotations

from pathlib import Path
from typing import Any

from quanta_agents.asset_event_state.extractor import EventExtractor
from quanta_agents.asset_event_state.models import (
    clean_text,
    hash_id,
    normalize_event_type,
    normalize_source_type,
    normalize_signal_vector,
)


COLLECTION_KEYS = ("events", "evidence", "signals", "hotspots", "items")


class EventCanonicalAdapter:
    """Converges legacy agent outputs into the single canonical Event schema.

    Existing pipelines are left intact. Their outputs become source payloads for
    this adapter, which normalizes them before the EventExtractor persists them.
    """

    def __init__(self, extractor: EventExtractor):
        self.extractor = extractor

    def to_events(self, payload: dict[str, Any], *, use_llm: bool = False) -> list[dict[str, Any]]:
        candidates = self._candidate_payloads(payload)
        if not candidates:
            return self.extractor.extract_events(payload, use_llm=use_llm)

        events: list[dict[str, Any]] = []
        for candidate in candidates:
            events.extend(self.extractor.extract_events(candidate, use_llm=use_llm))
        return events

    def _candidate_payloads(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if self._is_canonical_like(payload):
            return [payload]

        source_schema = clean_text(payload.get("schema_version") or payload.get("source_type"))
        parent_context = {
            "schema_version": source_schema,
            "generated_at": clean_text(payload.get("generated_at") or payload.get("date")),
            "source_path": clean_text(payload.get("source_path") or payload.get("path")),
            "source_name": self._source_name(source_schema),
        }
        items = self._collect_items(payload)
        return [
            self._normalize_item(item, parent_context=parent_context)
            for item in items
            if isinstance(item, dict) and self._item_has_content(item)
        ]

    def _collect_items(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        for key in COLLECTION_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                collected.extend(item for item in value if isinstance(item, dict))

        assets = payload.get("assets")
        if isinstance(assets, list):
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                for key in COLLECTION_KEYS:
                    value = asset.get(key)
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict):
                                merged = dict(item)
                                merged.setdefault("asset", asset.get("asset") or asset.get("asset_name"))
                                collected.append(merged)
        elif isinstance(assets, dict):
            for asset_name, asset_payload in assets.items():
                if not isinstance(asset_payload, dict):
                    continue
                for key in COLLECTION_KEYS:
                    value = asset_payload.get(key)
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict):
                                merged = dict(item)
                                merged.setdefault("asset", asset_name)
                                collected.append(merged)

        if collected:
            return collected
        return [payload] if self._item_has_content(payload) else []

    def _normalize_item(self, item: dict[str, Any], *, parent_context: dict[str, str]) -> dict[str, Any]:
        text = self._text(item)
        source_name = parent_context.get("source_name") or clean_text(item.get("source_name")) or "legacy_agent_output"
        source_id = self._source_id(item, source_name=source_name, text=text)
        signal_vector = normalize_signal_vector(item.get("signal_vector"))
        if not any(abs(value) > 0 for value in signal_vector.values()):
            signal_vector = self._signal_from_legacy_fields(item)

        impact_nodes = self._impact_nodes(item)
        event_type = self._event_type({**item, "source_name": source_name})
        return {
            "source_id": source_id,
            "source_type": normalize_source_type(source_name),
            "source_name": source_name,
            "source_path": clean_text(item.get("source_path") or parent_context.get("source_path")),
            "source_ref_type": "legacy_agent_output",
            "timestamp": self._timestamp(item, parent_context),
            "asset": item.get("asset") or item.get("asset_name"),
            "event_type": event_type,
            "summary": clean_text(item.get("summary") or item.get("title") or text, limit=240),
            "text": text,
            "key_facts": self._key_facts(item, text),
            "signal_vector": signal_vector,
            "impact_nodes": impact_nodes,
            "confidence": item.get("confidence")
            or item.get("score")
            or item.get("weight")
            or item.get("heat")
            or 0.55,
            "raw_id": source_id,
            "url": clean_text(item.get("url")),
            "legacy_context": {
                "schema_version": parent_context.get("schema_version"),
                "legacy_event_id": clean_text(item.get("event_id")),
                "legacy_signal_id": clean_text(item.get("signal_id")),
                "legacy_theme_id": clean_text(item.get("theme_id")),
            },
        }

    def _is_canonical_like(self, payload: dict[str, Any]) -> bool:
        return all(key in payload for key in ("summary", "signal_vector", "source_id"))

    def _item_has_content(self, item: dict[str, Any]) -> bool:
        return bool(self._text(item) or clean_text(item.get("summary")) or clean_text(item.get("title")))

    def _text(self, item: dict[str, Any]) -> str:
        parts = [
            item.get("text"),
            item.get("content"),
            item.get("summary"),
            item.get("title_zh"),
            item.get("title"),
            item.get("question"),
            item.get("market_mainline"),
        ]
        return clean_text(" ".join(clean_text(part) for part in parts if clean_text(part)), limit=8000)

    def _source_name(self, schema_version: str) -> str:
        if "news_logic" in schema_version or "opinion_radar" in schema_version:
            return "opinion_radar.news_logic"
        if "wechat" in schema_version or "research_evidence" in schema_version:
            return "research_reports.wechat_evidence"
        if "polymarket" in schema_version:
            return "polymarket_daily.hotspots"
        if "incremental" in schema_version:
            return "signal_mapping.incremental_state"
        if "research_signal" in schema_version:
            return "signal_mapping.research_signal"
        return schema_version or "legacy_agent_output"

    def _source_id(self, item: dict[str, Any], *, source_name: str, text: str) -> str:
        for key in (
            "source_id",
            "event_id",
            "signal_id",
            "evidence_id",
            "market_id",
            "condition_id",
            "id",
            "flash_id",
            "article_id",
            "theme_id",
        ):
            value = clean_text(item.get(key))
            if value:
                return f"{source_name}.{value}"
        return hash_id("RAW-LEGACY", source_name, text, length=14)

    def _timestamp(self, item: dict[str, Any], parent_context: dict[str, str]) -> str:
        for key in ("timestamp", "publish_time", "published_at", "updated_at", "generated_at", "date"):
            value = clean_text(item.get(key))
            if value:
                return value
        return parent_context.get("generated_at") or ""

    def _event_type(self, item: dict[str, Any]) -> str:
        explicit = clean_text(item.get("event_type") or item.get("driver_type"))
        if explicit:
            return normalize_event_type(explicit)
        source_type = normalize_source_type(item.get("source_type") or item.get("source_name"))
        if source_type == "research":
            return "research"
        if source_type == "data":
            return "data"
        text = clean_text(
            " ".join(
                clean_text(item.get(key))
                for key in ("source_field", "dimension", "framework_node", "node_id", "theme_type", "evidence_type")
                if clean_text(item.get(key))
            )
        ).lower()
        if any(term in text for term in ("policy", "政策", "关税", "监管")):
            return "policy"
        if any(term in text for term in ("macro", "宏观", "利率", "通胀")):
            return "macro"
        if any(term in text for term in ("supply", "供应", "库存", "矿", "产量")):
            return "supply"
        if any(term in text for term in ("demand", "需求", "消费")):
            return "demand"
        return "sentiment"

    def _signal_from_legacy_fields(self, item: dict[str, Any]) -> dict[str, float]:
        direction = self._numeric_direction(item)
        vector = {key: 0.0 for key in ("price", "supply", "demand", "macro", "sentiment")}
        event_type = self._event_type(item)
        if event_type == "supply":
            vector["supply"] = round(-direction * 0.5, 4)
            vector["price"] = round(direction * 0.5, 4)
        elif event_type == "demand":
            vector["demand"] = round(direction * 0.55, 4)
            vector["price"] = round(direction * 0.4, 4)
        elif event_type == "macro":
            vector["macro"] = round(direction * 0.55, 4)
            vector["price"] = round(direction * 0.35, 4)
        else:
            vector["sentiment"] = round(direction * 0.55, 4)
            vector["price"] = round(direction * 0.35, 4)
        return vector

    def _numeric_direction(self, item: dict[str, Any]) -> float:
        for key in ("direction_score", "score", "impact", "weight", "probability_change", "price_change_1d"):
            try:
                value = float(item.get(key))
            except (TypeError, ValueError):
                continue
            if abs(value) > 1:
                value = value / 100.0
            return max(-1.0, min(1.0, value))
        direction = clean_text(item.get("direction") or item.get("bias") or item.get("consistency")).lower()
        if any(term in direction for term in ("bull", "support", "positive", "利多", "上行", "增强")):
            return 0.6
        if any(term in direction for term in ("bear", "conflict", "negative", "利空", "下行", "减弱")):
            return -0.6
        text = self._text(item)
        if any(term in text for term in ("上涨", "利多", "增强", "走强", "改善")):
            return 0.45
        if any(term in text for term in ("下跌", "利空", "减弱", "走弱", "恶化")):
            return -0.45
        return 0.2

    def _impact_nodes(self, item: dict[str, Any]) -> list[str]:
        nodes: list[str] = []
        value = item.get("impact_nodes")
        if isinstance(value, list):
            nodes.extend(clean_text(node) for node in value if clean_text(node))
        for key in ("framework_node", "node_id", "schema_node"):
            value = clean_text(item.get(key))
            if value:
                nodes.append(value)
        return list(dict.fromkeys(nodes))[:8]

    def _key_facts(self, item: dict[str, Any], text: str) -> list[str]:
        value = item.get("key_facts")
        if isinstance(value, list):
            return [clean_text(part, limit=240) for part in value if clean_text(part)][:8]
        facts = item.get("facts")
        if isinstance(facts, list):
            return [clean_text(part, limit=240) for part in facts if clean_text(part)][:8]
        return [clean_text(text, limit=240)] if text else []


def load_adapter_payloads(paths: list[str | Path], inline_payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    from quanta_agents.core.io import read_json

    payloads: list[dict[str, Any]] = []
    if inline_payload:
        payloads.append(inline_payload)
    for path in paths:
        data = read_json(Path(path).expanduser())
        if isinstance(data, list):
            payloads.extend(item for item in data if isinstance(item, dict))
        elif isinstance(data, dict):
            payloads.append(data)
        else:
            raise ValueError(f"input path must contain JSON object or list: {path}")
    return payloads
