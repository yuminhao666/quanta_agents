from __future__ import annotations

from datetime import datetime

from quanta_agents.core.io import read_json, write_json
from quanta_agents.market_themes.topic_evolution import publish_topic_evolution


def _write_topic_run(root, date_path, run_id, generated_at, strength, topic_id="MKT_TOPIC_A"):
    run_dir = root / "agent_workspace/candidates/market_topics" / date_path / run_id
    write_json(
        run_dir / "recent-news-topics.json",
        {
            "schema_version": "recent_news_market_topics.v1",
            "run_id": run_id,
            "generated_at": generated_at,
            "topic_nodes": [
                {
                    "schema_version": "market_topic.v1",
                    "topic_id": topic_id,
                    "canonical_name": "美伊谈判与霍尔木兹通行",
                    "topic_type": "geopolitics",
                    "driver_refs": ["geopolitics"],
                    "asset_refs": ["原油"],
                }
            ],
            "topic_states": [
                {
                    "schema_version": "market_topic_state.v1",
                    "topic_id": topic_id,
                    "as_of": generated_at,
                    "heat": strength + 0.1,
                    "strength": strength,
                    "confidence": 0.8,
                    "trend": "strengthening",
                    "dominant_assets": ["原油"],
                    "driver_refs": ["geopolitics"],
                    "change_explanation": "谈判和通行消息继续发酵。",
                }
            ],
            "topic_memberships": [
                {
                    "schema_version": "topic_membership.v1",
                    "membership_id": f"TMEM-{run_id}",
                    "object_id": f"EV-{run_id}",
                    "object_type": "event_report",
                    "topic_id": topic_id,
                    "membership_role": "primary",
                    "membership_score": 0.8,
                    "relation_to_topic": "updates",
                    "stance": "supports",
                    "effective_time": generated_at,
                    "source_refs": [{"ref_type": "flash", "id": run_id}],
                    "truth_status": "unverified",
                    "epistemic_status": "event_report",
                    "evidence_weight": 1.0,
                }
            ],
            "evidence_links": [
                {
                    "schema_version": "market_topic_evidence_link.v1",
                    "topic_id": topic_id,
                    "evidence_id": f"EV-{run_id}",
                    "membership_id": f"TMEM-{run_id}",
                    "summary": "霍尔木兹通行和美伊谈判出现新进展。",
                    "source_refs": [{"ref_type": "flash", "id": run_id}],
                }
            ],
        },
    )


def test_topic_evolution_builds_timeline_and_read_model(tmp_path):
    _write_topic_run(
        tmp_path,
        "2026/06/21",
        "RUN-RECENT-NEWS-TOPICS-20260621-120000",
        "2026-06-21 12:00:00",
        0.4,
    )
    _write_topic_run(
        tmp_path,
        "2026/06/22",
        "RUN-RECENT-NEWS-TOPICS-20260622-120000",
        "2026-06-22 12:00:00",
        0.7,
    )

    result = publish_topic_evolution(
        root=tmp_path,
        now=datetime(2026, 6, 22, 13, 0, 0),
    )

    payload = result["payload"]
    topic = payload["topics"][0]
    assert payload["source_run_count"] == 2
    assert topic["topic_id"] == "MKT_TOPIC_A"
    assert topic["lifecycle_state"] == "strengthening"
    assert topic["timeline"][1]["delta"]["strength"] == 0.3
    assert topic["timeline"][1]["new_evidence_count"] == 1
    assert topic["timeline"][0]["primary_evidence"][0]["summary"] == "霍尔木兹通行和美伊谈判出现新进展。"
    assert any(event["event_type"] == "state_updated" for event in topic["events"])
    assert read_json(
        tmp_path
        / "agent_workspace/candidates/market_topics/latest/topic-evolution/MKT_TOPIC_A.json"
    )["topic_id"] == "MKT_TOPIC_A"
    assert (tmp_path / "agent_workspace/candidates/market_topics/latest/topic-evolution.md").exists()
    assert payload["graph"]["nodes"]
    assert payload["graph"]["edges"]


def test_topic_evolution_marks_missing_topic_as_fading(tmp_path):
    _write_topic_run(
        tmp_path,
        "2026/06/21",
        "RUN-RECENT-NEWS-TOPICS-20260621-120000",
        "2026-06-21 12:00:00",
        0.4,
        topic_id="MKT_TOPIC_ONLY_FIRST_RUN",
    )
    _write_topic_run(
        tmp_path,
        "2026/06/22",
        "RUN-RECENT-NEWS-TOPICS-20260622-120000",
        "2026-06-22 12:00:00",
        0.8,
        topic_id="MKT_TOPIC_OTHER",
    )

    payload = publish_topic_evolution(root=tmp_path)["payload"]
    by_id = {topic["topic_id"]: topic for topic in payload["topics"]}
    fading = by_id["MKT_TOPIC_ONLY_FIRST_RUN"]

    assert fading["lifecycle_state"] == "fading"
    assert fading["missing_after_seen_count"] == 1
    assert fading["timeline"][-1]["observed"] is False
    assert any(event["event_type"] == "topic_not_observed" for event in fading["events"])


def test_topic_evolution_can_filter_source_run_ids(tmp_path):
    _write_topic_run(
        tmp_path,
        "2026/06/21",
        "RUN-RECENT-NEWS-TOPICS-20260621-120000",
        "2026-06-21 12:00:00",
        0.4,
        topic_id="MKT_TOPIC_INCLUDED",
    )
    _write_topic_run(
        tmp_path,
        "2026/06/22",
        "RUN-RECENT-NEWS-TOPICS-20260622-120000",
        "2026-06-22 12:00:00",
        0.7,
        topic_id="MKT_TOPIC_EXCLUDED",
    )

    payload = publish_topic_evolution(
        root=tmp_path,
        run_ids=["RUN-RECENT-NEWS-TOPICS-20260621-120000"],
    )["payload"]

    assert payload["source_run_count"] == 1
    assert [topic["topic_id"] for topic in payload["topics"]] == ["MKT_TOPIC_INCLUDED"]


def test_topic_evolution_can_write_run_scoped_archive(tmp_path):
    _write_topic_run(
        tmp_path,
        "2026/06/21",
        "RUN-RECENT-NEWS-TOPICS-20260621-120000",
        "2026-06-21 12:00:00",
        0.4,
    )

    output_dir = tmp_path / "agent_workspace/runs/market_topics/news_market_update/RUN-1"
    result = publish_topic_evolution(
        root=tmp_path,
        output_dir=output_dir,
        run_ids=["RUN-RECENT-NEWS-TOPICS-20260621-120000"],
    )

    archive = read_json(output_dir / "topic-evolution-read-model.json")
    assert result["archive_json"] == str(output_dir / "topic-evolution-read-model.json")
    assert archive["source_run_count"] == 1
    assert archive["topics"][0]["topic_id"] == "MKT_TOPIC_A"
    assert (output_dir / "topic-evolution.md").exists()
    assert (output_dir / "topic-evolution/MKT_TOPIC_A.json").exists()
