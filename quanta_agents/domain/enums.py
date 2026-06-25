from __future__ import annotations

from enum import StrEnum


class ResearchObjectType(StrEnum):
    DOCUMENT = "document"
    EVENT = "event"
    CLAIM = "claim"
    EVIDENCE = "evidence"
    OBSERVATION = "observation"
    SIGNAL = "signal"
    TOPIC = "topic"
    REPORT = "report"
    AGENT_RUN = "agent_run"
    LOGIC_NODE = "logic_node"
    LOGIC_EDGE = "logic_edge"
    STATE_SNAPSHOT = "state_snapshot"


class SourceType(StrEnum):
    NEWS = "news"
    RESEARCH_REPORT = "research_report"
    ANNUAL_REPORT = "annual_report"
    MARKET_DATA = "market_data"
    FUNDAMENTAL_DATA = "fundamental_data"
    POLYMARKET = "polymarket"
    HUMAN = "human"
    AGENT = "agent"


class TruthStatus(StrEnum):
    UNVERIFIED = "unverified"
    PARTIALLY_CONFIRMED = "partially_confirmed"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    RETRACTED = "retracted"
    NOT_APPLICABLE = "not_applicable"


class LifecycleStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class RelationType(StrEnum):
    DERIVED_FROM = "DERIVED_FROM"
    GENERATED_FROM = "GENERATED_FROM"
    GENERATED_BY = "GENERATED_BY"
    MENTIONS = "MENTIONS"
    ABOUT_ASSET = "ABOUT_ASSET"
    MAPS_TO_NODE = "MAPS_TO_NODE"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    ACTIVATES = "ACTIVATES"
    VALIDATES = "VALIDATES"
    UPDATES = "UPDATES"
    SUPERSEDES = "SUPERSEDES"
    SAME_EVENT_AS = "SAME_EVENT_AS"
    BELONGS_TO_TOPIC = "BELONGS_TO_TOPIC"
    AFFECTS_ASSET = "AFFECTS_ASSET"


class EventType(StrEnum):
    GEOPOLITICS = "geopolitics"
    POLICY = "policy"
    MACRO = "macro"
    SUPPLY = "supply"
    DEMAND = "demand"
    INVENTORY = "inventory"
    COST = "cost"
    LOGISTICS = "logistics"
    MARKET = "market"
    OTHER = "other"


class TopicType(StrEnum):
    MARKET_THEME = "market_theme"
    EVENT_TOPIC = "event_topic"
    ASSET_THEME = "asset_theme"
    MACRO_THEME = "macro_theme"


class TopicLifecycleState(StrEnum):
    CANDIDATE = "candidate"
    EMERGING = "emerging"
    ACTIVE = "active"
    STRENGTHENING = "strengthening"
    MATURE = "mature"
    FADING = "fading"
    DISPUTED = "disputed"
    ARCHIVED = "archived"
    REACTIVATED = "reactivated"


class TopicMembershipRole(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    CONTEXTUAL = "contextual"


class TopicRelation(StrEnum):
    CREATES = "creates"
    UPDATES = "updates"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    VALIDATES = "validates"
    CONTEXTUALIZES = "contextualizes"


class AssignmentMethod(StrEnum):
    RULE = "rule"
    LLM = "llm"
    HUMAN = "human"


class LinkStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    REJECTED = "rejected"
