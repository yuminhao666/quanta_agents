from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from quanta_agents.domain import Topic, TopicAlias, TopicMembership, TopicStateSnapshot
from quanta_agents.domain.enums import (
    AssignmentMethod,
    LinkStatus,
    TopicLifecycleState,
    TopicMembershipRole,
    TopicRelation,
    TopicType,
)
from quanta_agents.domain.ids import topic_id as make_topic_id
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


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_+-]{1,}", text or "")
        if len(token.strip()) >= 2
    }


def _overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(min(len(left), len(right)), 1)


def _time_proximity(last_active_at: Any, effective_time: Any) -> float:
    left = _parse_time(last_active_at)
    right = _parse_time(effective_time)
    days = abs((right - left).total_seconds()) / 86400
    return max(0.0, min(1.0, math.exp(-days / 45.0)))


class TopicService:
    def __init__(self, repository: CatalogRepository):
        self.repository = repository

    def ensure_topic(
        self,
        canonical_title: str,
        *,
        topic_type: str = TopicType.MARKET_THEME.value,
        description: str = "",
        first_seen_at: Any | None = None,
        last_active_at: Any | None = None,
        aliases: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = LinkStatus.CANDIDATE.value,
    ) -> str:
        now = datetime.now(timezone.utc)
        first_seen = _parse_time(first_seen_at) if first_seen_at else now
        last_active = _parse_time(last_active_at) if last_active_at else first_seen
        topic = Topic(
            topic_id=make_topic_id(canonical_title, topic_type=topic_type),
            canonical_title=canonical_title,
            topic_type=topic_type,
            description=description,
            first_seen_at=first_seen,
            last_active_at=last_active,
            lifecycle_state=TopicLifecycleState.CANDIDATE,
            status=status,
            metadata=metadata or {},
        )
        aliases = [canonical_title, *(aliases or [])]
        self.repository.upsert_many(
            topics=[topic],
            topic_aliases=[
                TopicAlias(topic_id=topic.topic_id, alias=alias)
                for alias in dict.fromkeys(alias for alias in aliases if alias)
            ],
        )
        return topic.topic_id

    def find_topic_matches(
        self,
        text: str,
        *,
        asset_refs: list[dict[str, Any]] | None = None,
        logic_node_refs: list[dict[str, Any]] | None = None,
        entities: list[str] | None = None,
        effective_time: Any | None = None,
        limit: int = 12,
        min_score: float = 0.38,
    ) -> list[dict[str, Any]]:
        asset_refs = asset_refs or []
        logic_node_refs = logic_node_refs or []
        entities = entities or []
        text_tokens = _tokens(text)
        entity_tokens = _tokens(" ".join(entities))
        asset_tokens = _tokens(" ".join(str(item.get("label") or item.get("id") or "") for item in asset_refs))
        node_tokens = _tokens(" ".join(str(item.get("label") or item.get("id") or "") for item in logic_node_refs))
        matches: list[dict[str, Any]] = []

        for topic in self.repository.list_topics(limit=1000):
            metadata = json.loads(topic.get("metadata_json") or "{}")
            aliases = [row["alias"] for row in self.repository.topic_aliases(topic["topic_id"])]
            topic_text = " ".join(
                [
                    str(topic.get("canonical_title") or ""),
                    str(topic.get("description") or ""),
                    " ".join(aliases),
                    " ".join(metadata.get("entities") or []),
                    " ".join(str(item.get("label") or item.get("id") or "") for item in metadata.get("asset_refs") or []),
                    " ".join(str(item.get("label") or item.get("id") or "") for item in metadata.get("logic_node_refs") or []),
                ]
            )
            topic_tokens = _tokens(topic_text)
            topic_entity_tokens = _tokens(" ".join(metadata.get("entities") or []))
            topic_asset_tokens = _tokens(
                " ".join(str(item.get("label") or item.get("id") or "") for item in metadata.get("asset_refs") or [])
            )
            topic_node_tokens = _tokens(
                " ".join(str(item.get("label") or item.get("id") or "") for item in metadata.get("logic_node_refs") or [])
            )
            entity_overlap = _overlap(entity_tokens or text_tokens, topic_entity_tokens or topic_tokens)
            semantic_similarity = _overlap(text_tokens, topic_tokens)
            logic_node_overlap = _overlap(node_tokens, topic_node_tokens)
            event_chain_similarity = _overlap(text_tokens, _tokens(str(metadata.get("event_chain") or topic.get("description") or "")))
            time_proximity = _time_proximity(topic.get("last_active_at"), effective_time or datetime.now(timezone.utc))
            asset_overlap = _overlap(asset_tokens, topic_asset_tokens)
            score = round(
                0.25 * entity_overlap
                + 0.20 * semantic_similarity
                + 0.20 * logic_node_overlap
                + 0.15 * event_chain_similarity
                + 0.10 * time_proximity
                + 0.10 * asset_overlap,
                4,
            )
            if score < min_score:
                continue
            matches.append(
                {
                    "topic_id": topic["topic_id"],
                    "canonical_title": topic["canonical_title"],
                    "topic_type": topic["topic_type"],
                    "description": topic["description"],
                    "lifecycle_state": topic["lifecycle_state"],
                    "last_active_at": topic["last_active_at"],
                    "metadata": metadata,
                    "topic_score": score,
                    "score_breakdown": {
                        "entity_overlap": round(entity_overlap, 4),
                        "semantic_similarity": round(semantic_similarity, 4),
                        "logic_node_overlap": round(logic_node_overlap, 4),
                        "event_chain_similarity": round(event_chain_similarity, 4),
                        "time_proximity": round(time_proximity, 4),
                        "asset_overlap": round(asset_overlap, 4),
                    },
                }
            )
        matches.sort(key=lambda item: item["topic_score"], reverse=True)
        return matches[:limit]

    def assign_membership(
        self,
        object_id: str,
        topic_id: str,
        *,
        score: float,
        role: str = TopicMembershipRole.CONTEXTUAL.value,
        relation_to_topic: str = TopicRelation.CONTEXTUALIZES.value,
        assigned_by: str = AssignmentMethod.RULE.value,
        agent_run_id: str | None = None,
        effective_time: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        membership = TopicMembership(
            object_id=object_id,
            topic_id=topic_id,
            membership_role=role,
            relation_to_topic=relation_to_topic,
            membership_score=score,
            assigned_by=assigned_by,
            agent_run_id=agent_run_id,
            effective_time=_parse_time(effective_time),
            metadata=metadata or {},
        )
        self.repository.upsert_topic_membership(membership)
        self.refresh_topic_scores(topic_id)
        return membership.membership_id

    def refresh_topic_scores(self, topic_id: str) -> dict[str, Any]:
        topic_row = self.repository.get_topic(topic_id)
        if not topic_row:
            return {}
        stats = self.repository.topic_source_stats(topic_id)
        metadata = json.loads(topic_row.get("metadata_json") or "{}")
        topic = Topic(
            topic_id=topic_row["topic_id"],
            canonical_title=topic_row["canonical_title"],
            topic_type=topic_row["topic_type"],
            description=topic_row["description"],
            first_seen_at=_parse_time(topic_row["first_seen_at"]),
            last_active_at=_parse_time(topic_row["last_active_at"]),
            lifecycle_state=topic_row["lifecycle_state"],
            heat_score=float(topic_row["heat_score"] or 0.0),
            credibility_score=stats["credibility_score"],
            source_diversity=stats["source_diversity"],
            contradiction_score=stats["contradiction_score"],
            status=topic_row["status"],
            version=int(topic_row["version"] or 1),
            metadata=metadata,
        )
        snapshot = TopicStateSnapshot(
            topic_id=topic.topic_id,
            snapshot_time=datetime.now(timezone.utc),
            lifecycle_state=topic.lifecycle_state,
            heat_score=topic.heat_score,
            credibility_score=topic.credibility_score,
            source_diversity=topic.source_diversity,
            contradiction_score=topic.contradiction_score,
            summary=f"{topic.canonical_title} source_diversity={topic.source_diversity:.2f}",
        )
        self.repository.upsert_many(topics=[topic], topic_snapshots=[snapshot])
        return stats
