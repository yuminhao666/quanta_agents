from __future__ import annotations

from typing import Any

from quanta_agents.domain import (
    CanonicalEvent,
    EventAssetLink,
    EventNodeActivation,
    EventSourceLink,
    EventTopicMembership,
    ObjectRelation,
    ResearchObject,
)
from quanta_agents.domain.enums import RelationType, ResearchObjectType, SourceType
from quanta_agents.repositories.catalog_repository import CatalogRepository


class EventService:
    def __init__(self, repository: CatalogRepository):
        self.repository = repository

    def register_event(
        self,
        event: CanonicalEvent,
        *,
        source_object: ResearchObject | None = None,
        asset_links: list[EventAssetLink] | None = None,
        node_activations: list[EventNodeActivation] | None = None,
        topic_memberships: list[EventTopicMembership] | None = None,
    ) -> dict[str, Any]:
        event_object = ResearchObject(
            object_id=event.event_id,
            object_type=ResearchObjectType.EVENT,
            title=event.canonical_summary,
            source_type=SourceType.NEWS if event.source_type == "news" else SourceType.AGENT,
            source_id=event.source_id,
            event_time=event.event_time,
            truth_status=event.truth_status,
            lifecycle_status=event.status,
            metadata={"event_type": event.event_type, **event.metadata},
        )
        event_sources = []
        relations = []
        objects = [event_object]
        if source_object is not None:
            objects.append(source_object)
            event_sources.append(
                EventSourceLink(
                    event_id=event.event_id,
                    source_object_id=source_object.object_id,
                    source_id=source_object.source_id,
                    confidence=0.95,
                )
            )
            relations.append(
                ObjectRelation(
                    from_object_id=event.event_id,
                    relation_type=RelationType.DERIVED_FROM,
                    to_object_id=source_object.object_id,
                    confidence=0.95,
                )
            )
        result = self.repository.upsert_many(
            objects=objects,
            relations=relations,
            events=[event],
            event_sources=event_sources,
            event_assets=asset_links or [],
            event_nodes=node_activations or [],
            event_topics=topic_memberships or [],
        )
        return {"event_id": event.event_id, "counts": result}
