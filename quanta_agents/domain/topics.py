from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quanta_agents.domain.enums import (
    AssignmentMethod,
    LinkStatus,
    TopicLifecycleState,
    TopicMembershipRole,
    TopicRelation,
    TopicType,
)
from quanta_agents.domain.ids import stable_hash, topic_id, topic_membership_id


class Topic(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    topic_id: str = ""
    canonical_title: str
    topic_type: TopicType = TopicType.MARKET_THEME
    description: str = ""
    first_seen_at: datetime
    last_active_at: datetime
    lifecycle_state: TopicLifecycleState = TopicLifecycleState.CANDIDATE
    heat_score: float = 0.0
    credibility_score: float = 0.0
    source_diversity: float = 0.0
    contradiction_score: float = 0.0
    status: LinkStatus = LinkStatus.CANDIDATE
    version: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("heat_score", "credibility_score", "source_diversity", "contradiction_score")
    @classmethod
    def _bounded_score(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def model_post_init(self, __context: Any) -> None:
        if not self.topic_id:
            self.topic_id = topic_id(self.canonical_title, topic_type=str(self.topic_type))


class TopicAlias(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    alias_id: str = ""
    topic_id: str
    alias: str
    language: str | None = None
    status: LinkStatus = LinkStatus.CANDIDATE

    def model_post_init(self, __context: Any) -> None:
        if not self.alias_id:
            self.alias_id = stable_hash("TALIAS", self.topic_id, self.alias)


class TopicMembership(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    membership_id: str = ""
    object_id: str
    topic_id: str
    membership_role: TopicMembershipRole = TopicMembershipRole.CONTEXTUAL
    relation_to_topic: TopicRelation = TopicRelation.CONTEXTUALIZES
    membership_score: float = 0.0
    assigned_by: AssignmentMethod = AssignmentMethod.RULE
    agent_run_id: str | None = None
    effective_time: datetime
    status: LinkStatus = LinkStatus.CANDIDATE
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("membership_score")
    @classmethod
    def _bounded_membership_score(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def model_post_init(self, __context: Any) -> None:
        if not self.membership_id:
            self.membership_id = topic_membership_id(
                self.object_id,
                self.topic_id,
                str(self.relation_to_topic),
            )


class TopicStateSnapshot(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    snapshot_id: str = ""
    topic_id: str
    snapshot_time: datetime
    lifecycle_state: TopicLifecycleState
    heat_score: float = 0.0
    credibility_score: float = 0.0
    source_diversity: float = 0.0
    contradiction_score: float = 0.0
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.snapshot_id:
            self.snapshot_id = stable_hash("TSNAP", self.topic_id, self.snapshot_time)


class TopicChangeLog(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    change_id: str = ""
    topic_id: str
    change_type: str
    changed_at: datetime
    old_state: dict[str, Any] = Field(default_factory=dict)
    new_state: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    requires_review: bool = True

    def model_post_init(self, __context: Any) -> None:
        if not self.change_id:
            self.change_id = stable_hash("TCHG", self.topic_id, self.change_type, self.changed_at, self.reason)
