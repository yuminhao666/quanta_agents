from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from quanta_agents.domain import ObjectRelation, ResearchObject
from quanta_agents.domain.enums import LifecycleStatus, RelationType, ResearchObjectType, SourceType
from quanta_agents.domain.ids import content_hash, research_object_id
from quanta_agents.repositories.catalog_repository import CatalogRepository
from quanta_agents.services.provenance_service import report_dependency


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


def register_report(
    repository: CatalogRepository,
    report_payload: dict[str, Any],
    *,
    report_id: str | None = None,
    content_uri: str | None = None,
    dependency_object_ids: list[str] | None = None,
    agent_run_id: str | None = None,
) -> dict[str, Any]:
    report_id = report_id or str(report_payload.get("report_id") or report_payload.get("candidate_id") or "")
    digest = content_hash(report_payload)
    report_object = ResearchObject(
        object_id=research_object_id(
            ResearchObjectType.REPORT.value,
            source_type=SourceType.AGENT.value,
            source_id=report_id,
            content_hash_value=digest,
            content_uri=content_uri,
        ),
        object_type=ResearchObjectType.REPORT,
        title=str(report_payload.get("title") or report_id or "agent report"),
        content_uri=content_uri,
        content_hash=digest,
        source_type=SourceType.AGENT,
        source_id=report_id,
        published_at=_parse_time(report_payload.get("generated_at") or report_payload.get("created_at")),
        lifecycle_status=LifecycleStatus.CANDIDATE,
        metadata={"independent_evidence_weight": 0, "report_schema_version": report_payload.get("schema_version")},
    )
    dependencies = [
        report_dependency(
            report_object.object_id,
            dependency_id,
            agent_run_id=agent_run_id,
            evidence_weight=0.0,
            metadata={"policy": "agent_report_is_view_not_independent_evidence"},
        )
        for dependency_id in dependency_object_ids or []
    ]
    relations = [
        ObjectRelation(
            from_object_id=report_object.object_id,
            relation_type=RelationType.GENERATED_FROM,
            to_object_id=dependency_id,
            confidence=1.0,
            agent_run_id=agent_run_id,
        )
        for dependency_id in dependency_object_ids or []
    ]
    counts = repository.upsert_many(objects=[report_object], report_dependencies=dependencies, relations=relations)
    return {"status": "succeeded", "report_object_id": report_object.object_id, "counts": counts}
