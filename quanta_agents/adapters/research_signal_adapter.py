from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from quanta_agents.domain import ObjectRelation, ResearchObject, TopicMembership
from quanta_agents.domain.enums import (
    AssignmentMethod,
    LifecycleStatus,
    RelationType,
    ResearchObjectType,
    SourceType,
    TopicMembershipRole,
    TopicRelation,
)
from quanta_agents.domain.ids import content_hash, research_object_id
from quanta_agents.repositories.catalog_repository import CatalogRepository
from quanta_agents.services.topic_service import TopicService


SOURCE_ROLE_MAP = {
    "news": SourceType.NEWS.value,
    "research_report": SourceType.RESEARCH_REPORT.value,
    "market_data": SourceType.MARKET_DATA.value,
    "fundamental_data": SourceType.FUNDAMENTAL_DATA.value,
    "web_info": SourceType.POLYMARKET.value,
    "human": SourceType.HUMAN.value,
    "agent": SourceType.AGENT.value,
}


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


def _signal_time(signal: dict[str, Any]) -> datetime:
    window = signal.get("time_window") if isinstance(signal.get("time_window"), dict) else {}
    return _parse_time(window.get("start") or signal.get("created_at"))


def object_from_research_signal(signal: dict[str, Any]) -> ResearchObject:
    source_type = SOURCE_ROLE_MAP.get(str(signal.get("source_role") or ""), SourceType.AGENT.value)
    digest = content_hash(signal)
    metadata = {
        "signal_kind": signal.get("signal_kind"),
        "direction": signal.get("direction"),
        "strength": signal.get("strength"),
        "confidence": signal.get("confidence"),
        "asset_refs": signal.get("asset_refs") if isinstance(signal.get("asset_refs"), list) else [],
        "theme_refs": signal.get("theme_refs") if isinstance(signal.get("theme_refs"), list) else [],
    }
    if source_type == SourceType.AGENT.value:
        metadata["independent_evidence_weight"] = 0
    return ResearchObject(
        object_id=research_object_id(
            ResearchObjectType.SIGNAL.value,
            source_type=source_type,
            source_id=str(signal.get("signal_id") or ""),
            content_hash_value=digest,
            event_time=_signal_time(signal),
        ),
        object_type=ResearchObjectType.SIGNAL,
        title=str(signal.get("notes") or signal.get("signal_id") or "research signal")[:180],
        content_hash=digest,
        source_type=source_type,
        source_id=str(signal.get("signal_id") or ""),
        published_at=_parse_time(signal.get("created_at")),
        event_time=_signal_time(signal),
        lifecycle_status=LifecycleStatus.CANDIDATE,
        metadata=metadata,
    )


def register_research_signal_payload(repository: CatalogRepository, payload: dict[str, Any]) -> dict[str, Any]:
    signals = payload.get("signals") if isinstance(payload.get("signals"), list) else []
    topic_service = TopicService(repository)
    objects: list[ResearchObject] = []
    memberships: list[TopicMembership] = []
    relations: list[ObjectRelation] = []
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        signal_obj = object_from_research_signal(signal)
        objects.append(signal_obj)
        for ref in signal.get("theme_refs") or []:
            if not isinstance(ref, dict):
                continue
            topic_title = str(ref.get("label") or ref.get("id") or "").strip()
            if not topic_title:
                continue
            topic_id = topic_service.ensure_topic(
                topic_title,
                aliases=[str(ref.get("id") or "")],
                first_seen_at=signal_obj.event_time,
                last_active_at=signal_obj.event_time,
                metadata={"source": "research_signal_adapter", "theme_ref": ref, "asset_refs": signal.get("asset_refs") or []},
            )
            memberships.append(
                TopicMembership(
                    object_id=signal_obj.object_id,
                    topic_id=topic_id,
                    membership_role=TopicMembershipRole.CONTEXTUAL,
                    relation_to_topic=TopicRelation.SUPPORTS
                    if signal.get("signal_kind") != "thesis_conflict"
                    else TopicRelation.CONTRADICTS,
                    membership_score=float(signal.get("confidence") or 0.4),
                    assigned_by=AssignmentMethod.RULE,
                    effective_time=signal_obj.event_time,
                    metadata={"source_signal_id": signal.get("signal_id")},
                )
            )
        for evidence in signal.get("evidence_refs") or []:
            if not isinstance(evidence, dict) or not evidence.get("evidence_id"):
                continue
            evidence_obj = ResearchObject(
                object_type=ResearchObjectType.EVIDENCE,
                title=str(evidence.get("snippet_ref") or evidence.get("evidence_id")),
                content_uri=evidence.get("path"),
                source_type=SourceType.RESEARCH_REPORT
                if signal_obj.source_type == SourceType.RESEARCH_REPORT.value
                else SourceType.AGENT,
                source_id=str(evidence.get("evidence_id")),
                event_time=signal_obj.event_time,
                metadata={"evidence_ref": evidence},
            )
            objects.append(evidence_obj)
            relations.append(
                ObjectRelation(
                    from_object_id=signal_obj.object_id,
                    relation_type=RelationType.DERIVED_FROM,
                    to_object_id=evidence_obj.object_id,
                    confidence=float(signal.get("confidence") or 0.5),
                )
            )
    counts = repository.upsert_many(objects=objects, topic_memberships=memberships, relations=relations)
    return {"status": "succeeded", "signal_count": len(objects), "counts": counts}
