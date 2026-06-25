from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from quanta_agents.domain.ids import content_hash, relation_id, research_object_id, stable_hash
from quanta_agents.repositories.catalog_repository import CatalogRepository


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _time_text(value: Any, *, fallback: str | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback or _now_iso()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return fallback or _now_iso()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat(timespec="seconds")


def _object(
    object_id: str,
    object_type: str,
    title: str,
    *,
    source_type: str,
    source_id: str | None = None,
    content_uri: str | None = None,
    content_hash_value: str | None = None,
    published_at: Any = None,
    event_time: Any = None,
    truth_status: str = "unverified",
    lifecycle_status: str = "candidate",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now_iso()
    return {
        "object_id": object_id,
        "object_type": object_type,
        "title": str(title or object_id)[:280],
        "content_uri": content_uri,
        "content_hash": content_hash_value,
        "source_type": source_type,
        "source_id": source_id,
        "published_at": _time_text(published_at, fallback=now) if published_at else None,
        "event_time": _time_text(event_time, fallback=now) if event_time else None,
        "recorded_at": now,
        "truth_status": truth_status,
        "lifecycle_status": lifecycle_status,
        "schema_version": "research_object.v1",
        "metadata": metadata or {},
    }


def _relation(
    from_object_id: str,
    relation_type: str,
    to_object_id: str,
    *,
    confidence: float,
    polarity: int | None = None,
    effective_from: Any = None,
    evidence_object_id: str | None = None,
    agent_run_id: str | None = None,
    status: str = "candidate",
) -> dict[str, Any]:
    return {
        "relation_id": relation_id(
            relation_type,
            from_object_id,
            to_object_id,
            evidence_object_id=evidence_object_id,
            agent_run_id=agent_run_id,
        ),
        "from_object_id": from_object_id,
        "relation_type": relation_type,
        "to_object_id": to_object_id,
        "polarity": polarity,
        "confidence": max(0.0, min(1.0, float(confidence or 0.0))),
        "effective_from": _time_text(effective_from) if effective_from else None,
        "effective_to": None,
        "evidence_object_id": evidence_object_id,
        "agent_run_id": agent_run_id,
        "status": status,
    }


def _document_object_id(item: dict[str, Any]) -> str:
    digest = item.get("content_hash") or content_hash(item)
    return research_object_id(
        "document",
        source_type="news",
        source_id=str(item.get("news_id") or item.get("source_row_id") or ""),
        content_hash_value=digest,
        content_uri=item.get("url") or item.get("canonical_url"),
    )


def _topic_relation_polarity(relation_to_topic: str) -> int:
    return -1 if relation_to_topic == "contradicts" else 1


def _event_relation_type(value: str) -> str:
    return {
        "SAME_EVENT": "SAME_EVENT_AS",
        "UPDATE": "UPDATES",
        "UPDATES": "UPDATES",
        "CONFIRM": "SUPPORTS",
        "CONFIRMS": "SUPPORTS",
        "CONTRADICT": "CONTRADICTS",
        "CONTRADICTS": "CONTRADICTS",
        "RETRACT": "RETRACTS",
        "RETRACTS": "RETRACTS",
        "SUPERSEDES": "SUPERSEDES",
    }.get(str(value or "").upper(), str(value or "RELATED_TO").upper())


def _topic_row(topic: dict[str, Any]) -> dict[str, Any]:
    return {
        "topic_id": topic["topic_id"],
        "canonical_title": topic.get("canonical_title") or topic["topic_id"],
        "topic_type": topic.get("topic_type") or "event_topic",
        "description": topic.get("definition") or "",
        "first_seen_at": _time_text(topic.get("first_seen_at")),
        "last_active_at": _time_text(topic.get("last_active_at") or topic.get("first_seen_at")),
        "lifecycle_state": topic.get("lifecycle_state") or "candidate",
        "heat_score": float(topic.get("heat_score") or 0.0),
        "credibility_score": float(topic.get("credibility_score") or 0.0),
        "source_diversity": float(topic.get("source_diversity") or 0.0),
        "contradiction_score": float(topic.get("contradiction_score") or 0.0),
        "status": topic.get("status") or "candidate",
        "version": int(topic.get("version") or 1),
        "metadata": {
            "source": "news_events",
            "core_entities": topic.get("core_entities") or [],
            "core_event_types": topic.get("core_event_types") or [],
            "logic_node_refs": [
                {"id": node_id, "label": node_id}
                for node_id in (topic.get("core_logic_node_ids") or [])
            ],
            "included_scope": topic.get("included_scope") or [],
            "excluded_scope": topic.get("excluded_scope") or [],
        },
    }


def register_news_event_batch_result(
    repository: CatalogRepository,
    result: dict[str, Any],
    *,
    manifest_uri: str | None = None,
) -> dict[str, Any]:
    """Project a news_events batch into the generic ResearchObject catalog.

    The news_events sidecar remains the owner of extraction-specific tables.
    This adapter only creates reviewable catalog rows and explicit relations so
    other agents can query event/topic/asset/node links without parsing JSONL.
    """
    run_manifest = result.get("run_manifest") if isinstance(result.get("run_manifest"), dict) else {}
    run_id = str(result.get("run_id") or run_manifest.get("run_id") or "")
    news_by_id = {
        str(item.get("news_id")): item
        for item in result.get("normalized_news", [])
        if isinstance(item, dict) and item.get("news_id")
    }
    mentions_by_id = {
        str(mention.get("mention_id")): mention
        for mention in result.get("event_mentions", [])
        if isinstance(mention, dict) and mention.get("mention_id")
    }
    events_by_id = {
        str(event.get("event_id")): event
        for event in result.get("canonical_events", [])
        if isinstance(event, dict) and event.get("event_id")
    }
    topics_by_id = {
        str(topic.get("topic_id")): topic
        for topic in result.get("persistent_topics", [])
        if isinstance(topic, dict) and topic.get("topic_id")
    }

    objects: dict[str, dict[str, Any]] = {}
    relations: dict[str, dict[str, Any]] = {}
    topics: dict[str, dict[str, Any]] = {}
    topic_aliases: dict[str, dict[str, Any]] = {}
    topic_memberships: dict[str, dict[str, Any]] = {}
    event_assets: dict[str, dict[str, Any]] = {}
    event_nodes: dict[str, dict[str, Any]] = {}
    event_topics: dict[str, dict[str, Any]] = {}

    if run_id:
        objects[run_id] = _object(
            run_id,
            "agent_run",
            run_id,
            source_type="agent",
            source_id=run_id,
            published_at=run_manifest.get("started_at"),
            event_time=run_manifest.get("started_at"),
            truth_status="not_applicable",
            metadata={"independent_evidence_weight": 0, "run_type": run_manifest.get("run_type")},
        )

    document_ids: dict[str, str] = {}
    for item in news_by_id.values():
        object_id = _document_object_id(item)
        document_ids[item["news_id"]] = object_id
        objects[object_id] = _object(
            object_id,
            "document",
            item.get("title") or item.get("normalized_text") or item["news_id"],
            source_type="news",
            source_id=item["news_id"],
            content_uri=item.get("url") or item.get("canonical_url"),
            content_hash_value=item.get("content_hash") or content_hash(item),
            published_at=item.get("publish_time"),
            event_time=item.get("publish_time"),
            lifecycle_status=(item.get("filter_decision") or {}).get("decision") or "candidate",
            metadata={
                "source_system": item.get("source_system"),
                "source_row_id": item.get("source_row_id"),
                "channel": item.get("channel"),
                "important": item.get("important"),
                "source_ref": item.get("source_ref") or {},
            },
        )

    for mention in mentions_by_id.values():
        source_news_id = str(mention.get("source_news_id") or "")
        source_object_id = document_ids.get(source_news_id)
        mention_id = mention["mention_id"]
        objects[mention_id] = _object(
            mention_id,
            "event_mention",
            mention.get("canonical_summary") or mention_id,
            source_type="news",
            source_id=source_news_id or mention_id,
            published_at=(news_by_id.get(source_news_id) or {}).get("publish_time"),
            event_time=mention.get("event_time"),
            truth_status=mention.get("truth_status") or "unverified",
            lifecycle_status="candidate",
            metadata={
                "source": "news_events",
                "event_type": mention.get("event_type"),
                "event_stage": mention.get("event_stage"),
                "extraction_method": mention.get("extraction_method"),
                "extraction_confidence": mention.get("extraction_confidence"),
                "syndication_group_id": mention.get("syndication_group_id"),
                "evidence_quote": mention.get("evidence_quote"),
            },
        )
        if source_object_id:
            relation = _relation(
                mention_id,
                "DERIVED_FROM",
                source_object_id,
                confidence=float(mention.get("extraction_confidence") or 0.5),
                effective_from=mention.get("event_time") or (news_by_id.get(source_news_id) or {}).get("publish_time"),
                evidence_object_id=source_object_id,
                agent_run_id=run_id or None,
            )
            relations[relation["relation_id"]] = relation

    for event in events_by_id.values():
        event_id = event["event_id"]
        objects[event_id] = _object(
            event_id,
            "event",
            event.get("canonical_summary") or event_id,
            source_type="news",
            source_id=event_id,
            published_at=event.get("first_seen_at"),
            event_time=event.get("event_time") or event.get("first_seen_at") or event.get("last_seen_at"),
            truth_status=event.get("truth_status") or "unverified",
            lifecycle_status=event.get("status") or "candidate",
            metadata={
                "source": "news_events",
                "event_type": event.get("event_type"),
                "lifecycle_state": event.get("lifecycle_state"),
                "market_attention": event.get("market_attention"),
                "source_count": event.get("source_count"),
                "independent_source_count": event.get("independent_source_count"),
                "mention_ids": event.get("mention_ids") or [],
                "syndication_group_ids": event.get("syndication_group_ids") or [],
                "core_signature": event.get("core_signature") or {},
            },
        )
        for mention_id in event.get("mention_ids") or []:
            mention = mentions_by_id.get(str(mention_id)) or {}
            if mention_id not in objects:
                continue
            relation = _relation(
                event_id,
                "DERIVED_FROM",
                str(mention_id),
                confidence=float(mention.get("extraction_confidence") or 0.7),
                effective_from=event.get("event_time") or event.get("first_seen_at"),
                evidence_object_id=str(mention_id),
                agent_run_id=run_id or None,
            )
            relations[relation["relation_id"]] = relation

    for topic in topics_by_id.values():
        topic_id = topic["topic_id"]
        topics[topic_id] = _topic_row(topic)
        objects[topic_id] = _object(
            topic_id,
            "topic",
            topic.get("canonical_title") or topic_id,
            source_type="agent",
            source_id=topic_id,
            published_at=topic.get("first_seen_at"),
            event_time=topic.get("last_active_at") or topic.get("first_seen_at"),
            truth_status="not_applicable",
            lifecycle_status=topic.get("status") or "candidate",
            metadata={"source": "news_events", "independent_evidence_weight": 0},
        )

    for alias in result.get("topic_aliases", []):
        if not isinstance(alias, dict) or not alias.get("alias_id"):
            continue
        topic_aliases[alias["alias_id"]] = {
            "alias_id": alias["alias_id"],
            "topic_id": alias["topic_id"],
            "alias": alias.get("alias") or "",
            "language": alias.get("language"),
            "status": alias.get("status") or "candidate",
        }

    for membership in result.get("topic_memberships", []):
        if not isinstance(membership, dict):
            continue
        event_id = str(membership.get("object_id") or "")
        topic_id = str(membership.get("topic_id") or "")
        if not event_id or not topic_id:
            continue
        topic_memberships[membership["membership_id"]] = {
            "membership_id": membership["membership_id"],
            "object_id": event_id,
            "topic_id": topic_id,
            "membership_role": membership.get("membership_role") or "primary",
            "relation_to_topic": membership.get("relation_to_topic") or "contextualizes",
            "membership_score": float(membership.get("membership_score") or 0.0),
            "assigned_by": membership.get("assigned_by") or "rule",
            "agent_run_id": membership.get("agent_run_id") or run_id or None,
            "effective_time": _time_text(membership.get("effective_time")),
            "status": membership.get("status") or "candidate",
            "metadata": {"source": "news_events", "reason": membership.get("reason")},
        }
        event_topics[stable_hash("ETMEM", event_id, topic_id)] = {
            "membership_id": stable_hash("ETMEM", event_id, topic_id),
            "event_id": event_id,
            "topic_id": topic_id,
            "confidence": float(membership.get("membership_score") or 0.0),
            "status": membership.get("status") or "candidate",
            "metadata": {"source": "news_events", "membership_id": membership["membership_id"]},
        }
        relation = _relation(
            event_id,
            "BELONGS_TO_TOPIC",
            topic_id,
            confidence=float(membership.get("membership_score") or 0.0),
            polarity=_topic_relation_polarity(str(membership.get("relation_to_topic") or "")),
            effective_from=membership.get("effective_time"),
            evidence_object_id=event_id,
            agent_run_id=run_id or None,
        )
        relations[relation["relation_id"]] = relation

    for link in result.get("event_asset_links", []):
        if not isinstance(link, dict):
            continue
        event_id = str(link.get("event_id") or "")
        asset_id = str(link.get("asset_id") or "")
        if not event_id or not asset_id:
            continue
        asset_label = str(link.get("asset_label") or asset_id)
        objects.setdefault(
            asset_id,
            _object(
                asset_id,
                "asset",
                asset_label,
                source_type="agent",
                source_id=asset_id,
                truth_status="not_applicable",
                lifecycle_status="active",
                metadata={"source": "news_events", "independent_evidence_weight": 0},
            ),
        )
        event_assets[link["link_id"]] = {
            "link_id": link["link_id"],
            "event_id": event_id,
            "asset_id": asset_id,
            "asset_label": asset_label,
            "polarity": link.get("polarity"),
            "confidence": float(link.get("confidence") or 0.0),
            "status": "candidate",
            "metadata": {
                "source": "news_events",
                "relation": link.get("relation"),
                "mapping_method": link.get("mapping_method"),
            },
        }
        relation = _relation(
            event_id,
            "AFFECTS_ASSET",
            asset_id,
            confidence=float(link.get("confidence") or 0.0),
            effective_from=(events_by_id.get(event_id) or {}).get("event_time"),
            evidence_object_id=event_id,
            agent_run_id=run_id or None,
        )
        relations[relation["relation_id"]] = relation

    for link in result.get("event_framework_node_links", []):
        if not isinstance(link, dict):
            continue
        event_id = str(link.get("event_id") or "")
        node_id = str(link.get("node_id") or "")
        if not event_id or not node_id:
            continue
        node_label = str(link.get("node_label") or node_id)
        objects.setdefault(
            node_id,
            _object(
                node_id,
                "logic_node",
                node_label,
                source_type="agent",
                source_id=node_id,
                truth_status="not_applicable",
                lifecycle_status="active",
                metadata={
                    "source": "news_events",
                    "framework_id": link.get("framework_id"),
                    "asset_id": link.get("asset_id"),
                    "dimension_label": link.get("dimension_label"),
                    "independent_evidence_weight": 0,
                },
            ),
        )
        event_nodes[link["link_id"]] = {
            "activation_id": link["link_id"],
            "event_id": event_id,
            "logic_node_id": node_id,
            "logic_node_label": node_label,
            "asset_id": link.get("asset_id"),
            "confidence": float(link.get("confidence") or 0.0),
            "status": "candidate",
            "metadata": {
                "source": "news_events",
                "framework_id": link.get("framework_id"),
                "dimension_label": link.get("dimension_label"),
                "mapping_method": link.get("mapping_method"),
            },
        }
        relation = _relation(
            event_id,
            "MAPS_TO_NODE",
            node_id,
            confidence=float(link.get("confidence") or 0.0),
            effective_from=(events_by_id.get(event_id) or {}).get("event_time"),
            evidence_object_id=event_id,
            agent_run_id=run_id or None,
        )
        relations[relation["relation_id"]] = relation

    for relation_row in result.get("event_relations", []):
        if not isinstance(relation_row, dict):
            continue
        source_event_id = str(relation_row.get("source_event_id") or "")
        target_event_id = str(relation_row.get("target_event_id") or "")
        if not source_event_id or not target_event_id:
            continue
        relation = _relation(
            source_event_id,
            _event_relation_type(str(relation_row.get("relation") or "")),
            target_event_id,
            confidence=float(relation_row.get("confidence") or 0.0),
            effective_from=(events_by_id.get(source_event_id) or {}).get("event_time"),
            evidence_object_id=source_event_id,
            agent_run_id=run_id or None,
        )
        relations[relation["relation_id"]] = relation

    agent_runs = []
    if run_id:
        agent_runs.append(
            {
                "run_id": run_id,
                "run_type": run_manifest.get("run_type") or "news_event_batch",
                "source_id": run_id,
                "status": run_manifest.get("status") or "succeeded",
                "started_at": _time_text(run_manifest.get("started_at")),
                "finished_at": _time_text(run_manifest.get("finished_at")) if run_manifest.get("finished_at") else None,
                "manifest_uri": manifest_uri,
                "input_refs": run_manifest.get("inputs") or [],
                "output_refs": run_manifest.get("outputs") or [],
                "errors": result.get("errors") or [],
                "metadata": {
                    "source": "news_events",
                    "metrics": result.get("metrics") or {},
                    "catalog_path": run_manifest.get("catalog_path"),
                },
            }
        )

    counts = repository.upsert_many(
        objects=objects.values(),
        relations=relations.values(),
        events=[
            {
                "event_id": event["event_id"],
                "canonical_summary": event.get("canonical_summary") or event["event_id"],
                "event_type": event.get("event_type") or "other",
                "event_time": _time_text(event.get("event_time") or event.get("first_seen_at") or event.get("last_seen_at")),
                "truth_status": event.get("truth_status") or "unverified",
                "market_attention": float(event.get("market_attention") or 0.0),
                "entities": event.get("core_entities") or [],
                "status": event.get("status") or "candidate",
                "source_type": "news",
                "source_id": event["event_id"],
                "metadata": {"source": "news_events", "mention_ids": event.get("mention_ids") or []},
            }
            for event in events_by_id.values()
        ],
        event_assets=event_assets.values(),
        event_nodes=event_nodes.values(),
        event_topics=event_topics.values(),
        topics=topics.values(),
        topic_aliases=topic_aliases.values(),
        topic_memberships=topic_memberships.values(),
        agent_runs=agent_runs,
    )
    return {
        "status": "succeeded",
        "catalog_path": str(repository.db_path),
        "counts": counts,
        "object_count": len(objects),
        "relation_count": len(relations),
    }
