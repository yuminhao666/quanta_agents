from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from quanta_agents.domain import AgentRun, ResearchObject
from quanta_agents.domain.enums import LifecycleStatus, ResearchObjectType, SourceType
from quanta_agents.domain.ids import content_hash, research_object_id
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


def register_run_manifest(
    repository: CatalogRepository,
    manifest: dict[str, Any],
    *,
    manifest_uri: str | None = None,
) -> dict[str, Any]:
    run_id = str(manifest.get("run_id") or manifest.get("candidate_id") or "")
    run_type = str(manifest.get("run_type") or manifest.get("schema_version") or "agent_run")
    started = _parse_time(manifest.get("started_at") or manifest.get("generated_at") or manifest.get("created_at"))
    run = AgentRun(
        run_id=run_id or "",
        run_type=run_type,
        source_id=run_id,
        status=str(manifest.get("status") or "succeeded"),
        started_at=started,
        finished_at=_parse_time(manifest.get("finished_at")) if manifest.get("finished_at") else None,
        manifest_uri=manifest_uri,
        input_refs=manifest.get("input_refs") if isinstance(manifest.get("input_refs"), list) else [],
        output_refs=manifest.get("output_refs") if isinstance(manifest.get("output_refs"), list) else [],
        errors=manifest.get("errors") if isinstance(manifest.get("errors"), list) else [],
        metadata={"work_order_id": manifest.get("work_order_id"), "schema_version": manifest.get("schema_version")},
    )
    digest = content_hash(manifest)
    obj = ResearchObject(
        object_id=research_object_id(
            ResearchObjectType.AGENT_RUN.value,
            source_type=SourceType.AGENT.value,
            source_id=run.run_id,
            content_hash_value=digest,
            content_uri=manifest_uri,
        ),
        object_type=ResearchObjectType.AGENT_RUN,
        title=run.run_id,
        content_uri=manifest_uri,
        content_hash=digest,
        source_type=SourceType.AGENT,
        source_id=run.run_id,
        published_at=started,
        lifecycle_status=LifecycleStatus.CANDIDATE,
        metadata={"independent_evidence_weight": 0, "run_type": run_type},
    )
    counts = repository.upsert_many(agent_runs=[run], objects=[obj])
    return {"status": "succeeded", "run_id": run.run_id, "run_object_id": obj.object_id, "counts": counts}
