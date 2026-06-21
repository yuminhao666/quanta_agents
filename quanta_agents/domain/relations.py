from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from quanta_agents.domain.enums import LinkStatus, RelationType
from quanta_agents.domain.ids import relation_id


class ObjectRelation(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    relation_id: str = ""
    from_object_id: str
    relation_type: RelationType
    to_object_id: str
    polarity: int | None = None
    confidence: float = 0.0
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    evidence_object_id: str | None = None
    agent_run_id: str | None = None
    status: LinkStatus = LinkStatus.CANDIDATE

    @field_validator("polarity")
    @classmethod
    def _validate_polarity(cls, value: int | None) -> int | None:
        if value is not None and value not in {-1, 0, 1}:
            raise ValueError("polarity must be -1, 0, 1, or null")
        return value

    @field_validator("confidence")
    @classmethod
    def _bounded_confidence(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def model_post_init(self, __context: object) -> None:
        if not self.relation_id:
            self.relation_id = relation_id(
                str(self.relation_type),
                self.from_object_id,
                self.to_object_id,
                evidence_object_id=self.evidence_object_id,
                agent_run_id=self.agent_run_id,
            )
