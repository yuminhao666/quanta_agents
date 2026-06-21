from __future__ import annotations

from typing import Any

from quanta_agents.domain import ObjectRelation, ReportDependency
from quanta_agents.domain.enums import RelationType


def generated_from_relation(
    report_object_id: str,
    dependency_object_id: str,
    *,
    agent_run_id: str | None = None,
    confidence: float = 1.0,
) -> ObjectRelation:
    return ObjectRelation(
        from_object_id=report_object_id,
        relation_type=RelationType.GENERATED_FROM,
        to_object_id=dependency_object_id,
        confidence=confidence,
        agent_run_id=agent_run_id,
    )


def generated_by_relation(report_object_id: str, agent_run_object_id: str, *, confidence: float = 1.0) -> ObjectRelation:
    return ObjectRelation(
        from_object_id=report_object_id,
        relation_type=RelationType.GENERATED_BY,
        to_object_id=agent_run_object_id,
        confidence=confidence,
    )


def report_dependency(
    report_object_id: str,
    dependency_object_id: str,
    *,
    agent_run_id: str | None = None,
    evidence_weight: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> ReportDependency:
    return ReportDependency(
        report_object_id=report_object_id,
        dependency_object_id=dependency_object_id,
        agent_run_id=agent_run_id,
        evidence_weight=evidence_weight,
        metadata=metadata or {},
    )
