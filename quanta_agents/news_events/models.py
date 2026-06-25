from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class NewsItem:
    news_id: str
    source_system: str
    source_row_id: str
    title: str
    content: str
    normalized_text: str
    publish_time: str
    ingested_at: str
    channel: str | None
    important: int
    url: str | None
    canonical_url: str | None
    content_hash: str
    source_ref: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FilterDecision:
    news_id: str
    decision: str
    reason_codes: list[str]
    asset_candidates: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SyndicationGroup:
    syndication_group_id: str
    member_news_ids: list[str]
    root_source_id: str | None
    representative_news_id: str
    independent_source_count: int
    duplicate_types: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EventMention:
    mention_id: str
    source_news_id: str
    subject: list[str]
    action: str
    object: list[str]
    location: list[str]
    event_type: str
    event_stage: str
    modality: str
    event_time: str | None
    entities: list[str]
    canonical_summary: str
    truth_status: str
    market_attention: float
    extraction_confidence: float
    evidence_quote: str
    created_at: str
    extraction_method: str = "rule_fallback"
    syndication_group_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CanonicalEvent:
    event_id: str
    canonical_summary: str
    event_type: str
    event_time: str | None
    first_seen_at: str
    last_seen_at: str
    truth_status: str
    market_attention: float
    lifecycle_state: str
    core_entities: list[str]
    core_signature: dict[str, Any]
    mention_ids: list[str]
    supporting_mention_ids: list[str]
    contradicting_mention_ids: list[str]
    source_count: int
    independent_source_count: int
    status: str
    created_at: str
    updated_at: str
    syndication_group_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PersistentTopic:
    topic_id: str
    canonical_title: str
    topic_type: str
    definition: str
    core_entities: list[str]
    core_event_types: list[str]
    core_logic_node_ids: list[str]
    included_scope: list[str]
    excluded_scope: list[str]
    first_seen_at: str
    last_active_at: str
    lifecycle_state: str
    heat_score: float
    credibility_score: float
    status: str
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
