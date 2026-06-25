from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from quanta_agents.domain import (
    CanonicalEvent,
    EventAssetLink,
    EventNodeActivation,
    EventTopicMembership,
    ResearchObject,
)
from quanta_agents.domain.enums import EventType, ResearchObjectType, SourceType, TruthStatus
from quanta_agents.domain.ids import content_hash, research_object_id
from quanta_agents.repositories.catalog_repository import CatalogRepository
from quanta_agents.services.event_service import EventService
from quanta_agents.services.topic_service import TopicService


def _parse_time(value: Any) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    if text:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _clean_summary(text: str) -> str:
    summary = re.sub(r"\s+", " ", str(text or "")).strip()
    # Keep event facts neutral; asset-specific impact lives in EventAssetLink.
    summary = re.sub(r"(利多|利空|支撑|压制)(原油|黄金|铜|沪铜|白银|股指)?", "", summary)
    return summary[:260].strip(" ，,。") or "news logic event"


def _event_type(text: str) -> str:
    rules = (
        (EventType.GEOPOLITICS.value, ("霍尔木兹", "伊朗", "中东", "地缘", "冲突", "制裁", "战争")),
        (EventType.POLICY.value, ("政策", "监管", "关税", "财政", "央行")),
        (EventType.MACRO.value, ("美联储", "利率", "通胀", "美元", "PMI")),
        (EventType.SUPPLY.value, ("供应", "供给", "产量", "减产", "增产", "OPEC")),
        (EventType.DEMAND.value, ("需求", "消费", "订单", "开工")),
        (EventType.INVENTORY.value, ("库存", "仓单", "去库", "累库")),
        (EventType.LOGISTICS.value, ("航运", "通航", "港口", "运费", "发运")),
        (EventType.MARKET.value, ("收涨", "收跌", "涨幅", "跌幅", "主力合约")),
    )
    for event_type, words in rules:
        if any(word in text for word in words):
            return event_type
    return EventType.OTHER.value


def _polarity(value: Any) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 0.05:
        return 1
    if number < -0.05:
        return -1
    return 0


def _source_object(event: dict[str, Any], summary: str) -> ResearchObject:
    source_id = str(event.get("flash_id") or event.get("source_id") or event.get("event_id") or "")
    digest = content_hash({"source_id": source_id, "text": event.get("text") or summary, "url": event.get("url")})
    return ResearchObject(
        object_id=research_object_id(
            ResearchObjectType.DOCUMENT.value,
            source_type=SourceType.NEWS.value,
            source_id=source_id,
            content_hash_value=digest,
        ),
        object_type=ResearchObjectType.DOCUMENT,
        title=summary,
        content_uri=event.get("url"),
        content_hash=digest,
        source_type=SourceType.NEWS,
        source_id=source_id,
        published_at=_parse_time(event.get("publish_time")),
        metadata={"channel": event.get("channel"), "legacy_event_id": event.get("event_id")},
    )


def register_news_logic_payload(repository: CatalogRepository, payload: dict[str, Any]) -> dict[str, Any]:
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if not isinstance(event, dict):
            continue
        flash_id = str(event.get("flash_id") or event.get("source_id") or event.get("text") or event.get("event_id"))
        grouped[flash_id].append(event)

    event_service = EventService(repository)
    topic_service = TopicService(repository)
    registered: list[str] = []
    for flash_id, rows in grouped.items():
        first = rows[0]
        summary = _clean_summary(str(first.get("text") or first.get("summary") or ""))
        source_obj = _source_object(first, summary)
        canonical_event = CanonicalEvent(
            canonical_summary=summary,
            event_type=_event_type(summary),
            event_time=_parse_time(first.get("publish_time")),
            truth_status=TruthStatus.UNVERIFIED,
            market_attention=min(1.0, max(float(row.get("heat") or 0.0) for row in rows) / 10.0),
            entities=list(
                dict.fromkeys(
                    str(row.get("asset") or "")
                    for row in rows
                    if str(row.get("asset") or "").strip()
                )
            ),
            source_type="news",
            source_id=flash_id,
            metadata={"legacy_event_ids": [row.get("event_id") for row in rows]},
        )
        asset_links: list[EventAssetLink] = []
        node_links: list[EventNodeActivation] = []
        topic_links: list[EventTopicMembership] = []
        for row in rows:
            asset_id = str(row.get("asset_id") or row.get("asset") or "").strip()
            asset_label = str(row.get("asset") or asset_id or "unknown").strip()
            if asset_id:
                asset_links.append(
                    EventAssetLink(
                        event_id=canonical_event.event_id,
                        asset_id=asset_id,
                        asset_label=asset_label,
                        polarity=_polarity(row.get("direction_score")),
                        confidence=float((row.get("match") or {}).get("confidence") or 0.45),
                        metadata={"legacy_event_id": row.get("event_id")},
                    )
                )
            node = row.get("framework_node") if isinstance(row.get("framework_node"), dict) else {}
            node_id = str(node.get("node_id") or "").strip()
            if node_id:
                node_links.append(
                    EventNodeActivation(
                        event_id=canonical_event.event_id,
                        logic_node_id=node_id,
                        logic_node_label=str(node.get("label") or node.get("dimension_label") or ""),
                        asset_id=asset_id or None,
                        confidence=float((row.get("match") or {}).get("confidence") or 0.45),
                        metadata={"legacy_event_id": row.get("event_id")},
                    )
                )
            for ref in row.get("theme_anchor_refs") or []:
                if not isinstance(ref, dict):
                    continue
                label = str(ref.get("label") or ref.get("id") or "").strip()
                if not label:
                    continue
                topic_id = topic_service.ensure_topic(
                    label,
                    aliases=[str(ref.get("id") or "")],
                    first_seen_at=canonical_event.event_time,
                    last_active_at=canonical_event.event_time,
                    metadata={"source": "news_logic_adapter", "theme_anchor_ref": ref},
                )
                topic_links.append(
                    EventTopicMembership(
                        event_id=canonical_event.event_id,
                        topic_id=topic_id,
                        confidence=float(ref.get("match_score") or 0.5),
                        metadata={"legacy_event_id": row.get("event_id")},
                    )
                )
        event_service.register_event(
            canonical_event,
            source_object=source_obj,
            asset_links=asset_links,
            node_activations=node_links,
            topic_memberships=topic_links,
        )
        registered.append(canonical_event.event_id)
    return {"status": "succeeded", "canonical_event_ids": registered, "canonical_event_count": len(registered)}
