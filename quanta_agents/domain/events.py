from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quanta_agents.domain.enums import EventType, LinkStatus, TruthStatus
from quanta_agents.domain.ids import canonical_event_id, relation_id


class CanonicalEvent(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    event_id: str = ""
    canonical_summary: str
    event_type: EventType = EventType.OTHER
    event_time: datetime
    truth_status: TruthStatus = TruthStatus.UNVERIFIED
    market_attention: float = 0.0
    entities: list[str] = Field(default_factory=list)
    status: LinkStatus = LinkStatus.CANDIDATE
    source_type: str = "news"
    source_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("market_attention")
    @classmethod
    def _bounded_attention(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def model_post_init(self, __context: Any) -> None:
        if not self.event_id:
            self.event_id = canonical_event_id(
                source_type=self.source_type,
                source_id=self.source_id,
                canonical_summary=self.canonical_summary,
                event_time=self.event_time,
            )


class EventSourceLink(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    link_id: str = ""
    event_id: str
    source_object_id: str
    source_id: str | None = None
    confidence: float = 0.0
    status: LinkStatus = LinkStatus.CANDIDATE
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.link_id:
            self.link_id = relation_id("EVENT_SOURCE", self.event_id, self.source_object_id)


class EventAssetLink(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    link_id: str = ""
    event_id: str
    asset_id: str
    asset_label: str
    polarity: int | None = None
    confidence: float = 0.0
    status: LinkStatus = LinkStatus.CANDIDATE
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.link_id:
            self.link_id = relation_id("EVENT_ASSET", self.event_id, self.asset_id)


class EventNodeActivation(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    activation_id: str = ""
    event_id: str
    logic_node_id: str
    logic_node_label: str | None = None
    asset_id: str | None = None
    confidence: float = 0.0
    status: LinkStatus = LinkStatus.CANDIDATE
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.activation_id:
            self.activation_id = relation_id("EVENT_NODE", self.event_id, self.logic_node_id)


class EventTopicMembership(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    membership_id: str = ""
    event_id: str
    topic_id: str
    confidence: float = 0.0
    status: LinkStatus = LinkStatus.CANDIDATE
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.membership_id:
            self.membership_id = relation_id("EVENT_TOPIC", self.event_id, self.topic_id)
