from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from quanta_agents.domain import ResearchObject, Topic, TopicAlias
from quanta_agents.domain.enums import LifecycleStatus, ResearchObjectType, SourceType, TopicLifecycleState, TopicType
from quanta_agents.domain.ids import content_hash, research_object_id, topic_id as make_topic_id
from quanta_agents.repositories.catalog_repository import CatalogRepository


def _parse_time(value: Any) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    if text:
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _topic_type(theme: dict[str, Any]) -> str:
    theme_type = str(theme.get("theme_type") or "").lower()
    if theme_type in {"macro", "geopolitics", "policy"}:
        return TopicType.MACRO_THEME.value
    if (theme.get("asset_refs") or []) and len(theme.get("asset_refs") or []) == 1:
        return TopicType.ASSET_THEME.value
    return TopicType.MARKET_THEME.value


def topic_from_theme_anchor(theme: dict[str, Any]) -> tuple[Topic, list[TopicAlias], ResearchObject]:
    title = str(theme.get("title") or theme.get("canonical_title") or theme.get("theme_anchor_id") or "").strip()
    topic_type = _topic_type(theme)
    lifecycle = theme.get("lifecycle") if isinstance(theme.get("lifecycle"), dict) else {}
    first_seen = _parse_time(lifecycle.get("first_seen_at") or theme.get("created_at"))
    last_seen = _parse_time(lifecycle.get("last_seen_at") or theme.get("updated_at") or theme.get("created_at"))
    metadata = {
        "legacy_theme_anchor_id": theme.get("theme_anchor_id"),
        "anchor_kind": theme.get("anchor_kind"),
        "theme_type": theme.get("theme_type"),
        "source_roles": theme.get("source_roles") if isinstance(theme.get("source_roles"), list) else [],
        "source_refs": theme.get("source_refs") if isinstance(theme.get("source_refs"), list) else [],
        "asset_refs": theme.get("asset_refs") if isinstance(theme.get("asset_refs"), list) else [],
        "logic_node_refs": theme.get("framework_node_refs") if isinstance(theme.get("framework_node_refs"), list) else [],
        "lifecycle": lifecycle,
        "event_chain": (theme.get("event_definition_layers") or {}).get("fact_layer")
        if isinstance(theme.get("event_definition_layers"), dict)
        else "",
    }
    new_topic_id = make_topic_id(title, topic_type=topic_type)
    topic = Topic(
        topic_id=new_topic_id,
        canonical_title=title,
        topic_type=topic_type,
        description=str(theme.get("description") or ""),
        first_seen_at=first_seen,
        last_active_at=last_seen,
        lifecycle_state=TopicLifecycleState.CANDIDATE,
        heat_score=min(1.0, float(lifecycle.get("support_count") or 0) / 10.0),
        status=str(theme.get("status") or "candidate"),
        metadata=metadata,
    )
    aliases = [
        TopicAlias(topic_id=topic.topic_id, alias=alias)
        for alias in dict.fromkeys([title, *(theme.get("aliases") or [])])
        if alias
    ]
    digest = content_hash(theme)
    obj = ResearchObject(
        object_id=research_object_id(
            ResearchObjectType.TOPIC.value,
            source_type=SourceType.AGENT.value,
            source_id=str(theme.get("theme_anchor_id") or topic.topic_id),
            content_hash_value=digest,
        ),
        object_type=ResearchObjectType.TOPIC,
        title=title,
        content_hash=digest,
        source_type=SourceType.AGENT,
        source_id=str(theme.get("theme_anchor_id") or topic.topic_id),
        published_at=first_seen,
        lifecycle_status=LifecycleStatus.CANDIDATE,
        truth_status="not_applicable",
        metadata={**metadata, "independent_evidence_weight": 0},
    )
    return topic, aliases, obj


def register_theme_anchor_payload(repository: CatalogRepository, payload: dict[str, Any]) -> dict[str, Any]:
    themes = payload.get("theme_anchors") if isinstance(payload.get("theme_anchors"), list) else []
    topics: list[Topic] = []
    aliases: list[TopicAlias] = []
    objects: list[ResearchObject] = []
    for theme in themes:
        if not isinstance(theme, dict):
            continue
        topic, topic_aliases, obj = topic_from_theme_anchor(theme)
        topics.append(topic)
        aliases.extend(topic_aliases)
        objects.append(obj)
    counts = repository.upsert_many(topics=topics, topic_aliases=aliases, objects=objects)
    return {"status": "succeeded", "topic_count": len(topics), "counts": counts}
