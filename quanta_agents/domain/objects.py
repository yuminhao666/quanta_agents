from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quanta_agents.domain.enums import LifecycleStatus, ResearchObjectType, SourceType, TruthStatus
from quanta_agents.domain.ids import research_object_id


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ResearchObject(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    object_id: str = ""
    object_type: ResearchObjectType
    title: str
    content_uri: str | None = None
    content_hash: str | None = None
    source_type: SourceType
    source_id: str | None = None
    published_at: datetime | None = None
    event_time: datetime | None = None
    recorded_at: datetime = Field(default_factory=utc_now)
    truth_status: TruthStatus = TruthStatus.UNVERIFIED
    lifecycle_status: LifecycleStatus = LifecycleStatus.CANDIDATE
    schema_version: str = "research_object.v1"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def _title_required(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("title is required")
        return value

    def model_post_init(self, __context: Any) -> None:
        if not self.object_id:
            self.object_id = research_object_id(
                str(self.object_type),
                source_type=str(self.source_type),
                source_id=self.source_id,
                content_hash_value=self.content_hash,
                content_uri=self.content_uri,
                event_time=self.event_time,
            )
