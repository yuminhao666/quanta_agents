from __future__ import annotations

from datetime import datetime, timezone

from quanta_agents.adapters.news_logic_adapter import register_news_logic_payload
from quanta_agents.adapters.research_signal_adapter import register_research_signal_payload
from quanta_agents.adapters.theme_anchor_adapter import register_theme_anchor_payload
from quanta_agents.domain import ObjectRelation, ResearchObject, Topic, TopicMembership
from quanta_agents.domain.enums import (
    AssignmentMethod,
    LifecycleStatus,
    RelationType,
    ResearchObjectType,
    SourceType,
    TopicLifecycleState,
    TopicMembershipRole,
    TopicRelation,
)
from quanta_agents.repositories.catalog_repository import CatalogRepository
from quanta_agents.services.topic_service import TopicService
from quanta_agents.signal_mapping.theme_anchor_matcher import load_theme_anchor_index


def _repo(tmp_path) -> CatalogRepository:
    return CatalogRepository(tmp_path, db_path=tmp_path / "indexes/object_catalog/quanta_catalog.sqlite3")


def test_news_logic_cross_asset_event_is_one_canonical_event(tmp_path):
    repo = _repo(tmp_path)
    payload = {
        "schema_version": "news_logic_radar.v1",
        "events": [
            {
                "event_id": f"NLOGIC-{asset}",
                "flash_id": "FLASH-HORMUZ-1",
                "asset": asset,
                "asset_id": asset_id,
                "publish_time": "2026-06-21 09:00:00",
                "text": "伊朗称霍尔木兹海峡通行存在不确定性，市场关注中东局势。",
                "direction_score": direction,
                "heat": 4.0,
                "framework_node": {"node_id": f"{asset_id}.geopolitics", "label": "地缘政治"},
                "match": {"confidence": 0.8},
            }
            for asset, asset_id, direction in [
                ("原油", "FUT-SC", 1.0),
                ("黄金", "FUT-AU", 1.0),
                ("铜", "FUT-CU", -0.2),
            ]
        ],
    }

    first = register_news_logic_payload(repo, payload)
    second = register_news_logic_payload(repo, payload)

    assert first["canonical_event_count"] == 1
    assert first["canonical_event_ids"] == second["canonical_event_ids"]
    event_id = first["canonical_event_ids"][0]
    assert repo.table_counts()["canonical_event"] == 1
    assert len(repo.event_asset_links(event_id)) == 3
    assert {row["asset_label"] for row in repo.event_asset_links(event_id)} == {"原油", "黄金", "铜"}


def test_persistent_topic_registry_survives_without_recent_candidate_files(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    payload = {
        "theme_anchors": [
            {
                "schema_version": "theme_anchor.v1",
                "theme_anchor_id": "THA-HORMUZ-OLD",
                "status": "candidate",
                "created_at": "2026-06-18T00:00:00+00:00",
                "title": "美伊谈判与霍尔木兹通行不确定",
                "description": "伊朗、霍尔木兹、中东通航风险反复出现的长期话题。",
                "theme_type": "geopolitics",
                "source_roles": ["research_report"],
                "asset_refs": [{"id": "FUT-SC", "label": "原油", "ref_type": "asset"}],
                "aliases": ["霍尔木兹通行风险"],
                "lifecycle": {
                    "first_seen_at": "2026-06-18T00:00:00+00:00",
                    "last_seen_at": "2026-06-18T00:00:00+00:00",
                    "support_count": 2,
                },
            }
        ]
    }
    register_theme_anchor_payload(repo, payload)
    register_theme_anchor_payload(repo, payload)
    assert repo.table_counts()["topic"] == 1

    monkeypatch.setenv("QUANTA_OBJECT_CATALOG_ENABLED", "true")
    monkeypatch.setenv("QUANTA_OBJECT_CATALOG_PATH", str(repo.db_path))
    index = load_theme_anchor_index(tmp_path, max_files=3)
    match = index.best_match("伊朗与美国谈判后，霍尔木兹通行量和中东局势仍有分歧。")

    assert index.candidate_ids == ["persistent_topic_registry"]
    assert match is not None
    assert match["theme_anchor_id"].startswith("TOPIC-")
    assert match["title"] == "美伊谈判与霍尔木兹通行不确定"


def test_catalog_upserts_are_idempotent_for_signals_relations_and_memberships(tmp_path):
    repo = _repo(tmp_path)
    payload = {
        "signals": [
            {
                "schema_version": "research_signal.v1",
                "signal_id": "SIG-TEST-1",
                "source_role": "research_report",
                "signal_kind": "theme_update",
                "created_at": "2026-06-21T00:00:00+00:00",
                "notes": "研报称霍尔木兹通行风险仍需跟踪。",
                "confidence": 0.7,
                "strength": 0.6,
                "direction": "bullish",
                "time_window": {"start": "2026-06-21T00:00:00+00:00"},
                "asset_refs": [{"id": "FUT-SC", "label": "原油", "ref_type": "asset"}],
                "theme_refs": [{"id": "THA-HORMUZ", "label": "霍尔木兹通行风险", "ref_type": "theme"}],
                "evidence_refs": [{"evidence_id": "EVID-1", "path": "evidence/EVID-1.json"}],
            }
        ]
    }

    register_research_signal_payload(repo, payload)
    counts_after_first = repo.table_counts()
    register_research_signal_payload(repo, payload)
    counts_after_second = repo.table_counts()

    assert counts_after_second["research_object"] == counts_after_first["research_object"]
    assert counts_after_second["object_relation"] == counts_after_first["object_relation"]
    assert counts_after_second["topic_membership"] == counts_after_first["topic_membership"]


def test_agent_reports_do_not_increase_topic_source_diversity_or_credibility(tmp_path):
    repo = _repo(tmp_path)
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    topic = Topic(
        canonical_title="霍尔木兹通行风险",
        topic_type="market_theme",
        description="",
        first_seen_at=now,
        last_active_at=now,
        lifecycle_state=TopicLifecycleState.CANDIDATE,
    )
    evidence = ResearchObject(
        object_type=ResearchObjectType.EVIDENCE,
        title="原始研报证据",
        source_type=SourceType.RESEARCH_REPORT,
        source_id="EVID-RAW",
        event_time=now,
    )
    report_1 = ResearchObject(
        object_type=ResearchObjectType.REPORT,
        title="Agent 日报 1",
        source_type=SourceType.AGENT,
        source_id="RPT-1",
        event_time=now,
        lifecycle_status=LifecycleStatus.CANDIDATE,
        metadata={"independent_evidence_weight": 0},
    )
    report_2 = ResearchObject(
        object_type=ResearchObjectType.REPORT,
        title="Agent 日报 2",
        source_type=SourceType.AGENT,
        source_id="RPT-2",
        event_time=now,
        lifecycle_status=LifecycleStatus.CANDIDATE,
        metadata={"independent_evidence_weight": 0},
    )
    repo.upsert_many(
        topics=[topic],
        objects=[evidence],
        topic_memberships=[
            TopicMembership(
                object_id=evidence.object_id,
                topic_id=topic.topic_id,
                membership_role=TopicMembershipRole.PRIMARY,
                relation_to_topic=TopicRelation.SUPPORTS,
                membership_score=0.9,
                assigned_by=AssignmentMethod.RULE,
                effective_time=now,
            )
        ],
    )
    before = TopicService(repo).refresh_topic_scores(topic.topic_id)
    repo.upsert_many(
        objects=[report_1, report_2],
        topic_memberships=[
            TopicMembership(
                object_id=report_1.object_id,
                topic_id=topic.topic_id,
                relation_to_topic=TopicRelation.SUPPORTS,
                membership_score=1.0,
                assigned_by=AssignmentMethod.RULE,
                effective_time=now,
            ),
            TopicMembership(
                object_id=report_2.object_id,
                topic_id=topic.topic_id,
                relation_to_topic=TopicRelation.SUPPORTS,
                membership_score=1.0,
                assigned_by=AssignmentMethod.RULE,
                effective_time=now,
            ),
        ],
    )
    after = TopicService(repo).refresh_topic_scores(topic.topic_id)

    assert before["independent_source_types"] == ["research_report"]
    assert after["independent_source_types"] == ["research_report"]
    assert after["source_diversity"] == before["source_diversity"]
    assert after["credibility_score"] == before["credibility_score"]


def test_trace_raw_to_report_and_agent_run_lineage(tmp_path):
    repo = _repo(tmp_path)
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    raw = ResearchObject(
        object_type=ResearchObjectType.DOCUMENT,
        title="raw document",
        source_type=SourceType.NEWS,
        source_id="RAW-1",
        event_time=now,
    )
    evidence = ResearchObject(
        object_type=ResearchObjectType.EVIDENCE,
        title="evidence",
        source_type=SourceType.RESEARCH_REPORT,
        source_id="EVID-1",
        event_time=now,
    )
    signal = ResearchObject(
        object_type=ResearchObjectType.SIGNAL,
        title="research signal",
        source_type=SourceType.RESEARCH_REPORT,
        source_id="SIG-1",
        event_time=now,
    )
    topic_obj = ResearchObject(
        object_id="TOPIC-LINEAGE",
        object_type=ResearchObjectType.TOPIC,
        title="topic",
        source_type=SourceType.HUMAN,
        source_id="TOPIC-LINEAGE",
        event_time=now,
    )
    report = ResearchObject(
        object_type=ResearchObjectType.REPORT,
        title="agent report",
        source_type=SourceType.AGENT,
        source_id="RPT-1",
        event_time=now,
        metadata={"independent_evidence_weight": 0},
    )
    run = ResearchObject(
        object_type=ResearchObjectType.AGENT_RUN,
        title="RUN-1",
        source_type=SourceType.AGENT,
        source_id="RUN-1",
        event_time=now,
        metadata={"independent_evidence_weight": 0},
    )
    relations = [
        ObjectRelation(
            from_object_id=evidence.object_id,
            relation_type=RelationType.DERIVED_FROM,
            to_object_id=raw.object_id,
            confidence=1.0,
        ),
        ObjectRelation(
            from_object_id=signal.object_id,
            relation_type=RelationType.DERIVED_FROM,
            to_object_id=evidence.object_id,
            confidence=1.0,
        ),
        ObjectRelation(
            from_object_id=signal.object_id,
            relation_type=RelationType.BELONGS_TO_TOPIC,
            to_object_id=topic_obj.object_id,
            confidence=0.8,
        ),
        ObjectRelation(
            from_object_id=report.object_id,
            relation_type=RelationType.GENERATED_FROM,
            to_object_id=signal.object_id,
            confidence=1.0,
        ),
        ObjectRelation(
            from_object_id=report.object_id,
            relation_type=RelationType.GENERATED_BY,
            to_object_id=run.object_id,
            confidence=1.0,
        ),
    ]
    repo.upsert_many(objects=[raw, evidence, signal, topic_obj, report, run], relations=relations)

    trace = repo.trace_lineage(report.object_id)
    assert {raw.object_id, evidence.object_id, signal.object_id, topic_obj.object_id, report.object_id, run.object_id} <= set(
        trace["object_ids"]
    )
    assert {row["relation_type"] for row in trace["relations"]} >= {
        "DERIVED_FROM",
        "BELONGS_TO_TOPIC",
        "GENERATED_FROM",
        "GENERATED_BY",
    }
