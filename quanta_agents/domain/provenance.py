from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from quanta_agents.domain.ids import agent_run_id, stable_hash


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentRun(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    run_id: str = ""
    run_type: str
    source_id: str | None = None
    status: str = "succeeded"
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    manifest_uri: str | None = None
    input_refs: list[dict[str, Any]] = Field(default_factory=list)
    output_refs: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.run_id:
            self.run_id = agent_run_id(self.run_type, self.source_id, self.started_at)


class ReportDependency(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    dependency_id: str = ""
    report_object_id: str
    dependency_object_id: str
    dependency_type: str = "GENERATED_FROM"
    agent_run_id: str | None = None
    evidence_weight: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.dependency_id:
            self.dependency_id = stable_hash(
                "RDEP",
                self.report_object_id,
                self.dependency_object_id,
                self.dependency_type,
                self.agent_run_id or "",
            )
