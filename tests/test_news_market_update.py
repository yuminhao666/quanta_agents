from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from quanta_agents.core.io import read_json, write_json
from quanta_agents.market_themes import news_market_update


def _large_isolated_payload(size: int) -> dict:
    return {"blob": "x" * size}


def _fake_evolution_payload(run_ids: list[str]) -> dict:
    return {
        "schema_version": "topic_evolution_read_model.v1",
        "source_run_count": len(run_ids),
        "topic_count": 1 if run_ids else 0,
        "topics": [
            {
                "topic_id": "MKT_TOPIC_US_IRAN_HORMUZ",
                "canonical_name": "美伊谈判与霍尔木兹通行",
                "lifecycle_state": "strengthening",
                "observation_count": len(run_ids),
                "timeline": [
                    {
                        "run_id": run_ids[-1],
                        "as_of": "2026-06-10 11:59:59",
                        "observed": True,
                        "state": {
                            "heat": 0.8,
                            "strength": 0.7,
                            "confidence": 0.82,
                            "trend": "strengthening",
                            "dominant_assets": ["原油"],
                            "driver_refs": ["geopolitics"],
                            "change_explanation": "中东地缘和通航消息继续影响风险溢价。",
                        },
                        "primary_evidence": [
                            {
                                "object_id": "EV-1",
                                "summary": "霍尔木兹通行和美伊谈判出现新进展。",
                                "source_refs": [{"ref_type": "flash", "id": "flash-1"}],
                                "truth_status": "unverified",
                                "evidence_weight": 1.0,
                            }
                        ],
                    }
                ],
            }
        ]
        if run_ids
        else [],
    }


def test_run_isolated_reads_large_payload_before_join():
    result = news_market_update._run_isolated(
        _large_isolated_payload,
        {"size": 2_000_000},
        timeout_seconds=5,
    )

    assert len(result["blob"]) == 2_000_000


def test_news_market_update_runs_half_day_topic_evolution_and_mainlines(tmp_path, monkeypatch):
    monkeypatch.setattr(
        news_market_update.db,
        "latest_time",
        lambda: datetime(2026, 6, 10, 12, 0, 0),
    )
    monkeypatch.setattr(
        news_market_update.db,
        "fetch_flashes",
        lambda start, end: [
            {
                "flash_id": f"flash-{start:%Y%m%d%H}",
                "publish_time": start.isoformat(sep=" "),
                "important": 1,
                "content": "美伊谈判和霍尔木兹通行出现新消息。",
            }
        ],
    )

    brief_calls = []

    def fake_publish_brief(root, **kwargs):
        end = kwargs["end_time"]
        run_id = f"RUN-HALF-DAY-NEWS-BRIEF-{end:%Y%m%d-%H%M%S}"
        path = Path(root) / f"{run_id}.json"
        write_json(
            path,
            {
                "schema_version": "half_day_news_brief.v1",
                "stats": {"raw_flash_count": 1, "kept_flash_count": 1, "mapped_event_count": 1},
                "llm_brief": {"method": "llm", "citation_policy_status": "all_items_cited"},
            },
        )
        brief_calls.append(kwargs)
        return {
            "run_id": run_id,
            "status": "succeeded",
            "payload": read_json(path),
            "paths": {"run_brief_json": str(path), "run_brief_markdown": str(path.with_suffix(".md"))},
        }

    def fake_publish_topics(**kwargs):
        run_id = f"RUN-RECENT-NEWS-TOPICS-{kwargs['now']:%Y%m%d-%H%M%S}"
        return {
            "run_id": run_id,
            "latest_json": str(Path(kwargs["root"]) / "latest-topic.json"),
            "payload": {
                "topic_nodes": [{"topic_id": "MKT_TOPIC_US_IRAN_HORMUZ"}],
                "topic_memberships": [{"membership_id": "TMEM-1"}],
                "extraction": {"method": "llm", "provider": "fake", "invalid_ref_count": 0},
            },
        }

    def fake_publish_evolution(**kwargs):
        root = Path(kwargs["root"])
        latest = root / "agent_workspace/candidates/market_topics/latest/topic-evolution-read-model.json"
        markdown = root / "agent_workspace/candidates/market_topics/latest/topic-evolution.md"
        payload = _fake_evolution_payload(kwargs["run_ids"])
        write_json(latest, payload)
        return {"latest_json": str(latest), "latest_markdown": str(markdown), "payload": payload}

    monkeypatch.setattr(news_market_update.news_brief, "publish_hourly_news_brief", fake_publish_brief)
    monkeypatch.setattr(news_market_update.recent_news_topics, "publish_recent_news_topics", fake_publish_topics)
    monkeypatch.setattr(news_market_update.topic_evolution, "publish_topic_evolution", fake_publish_evolution)

    result = news_market_update.run_news_market_update(
        root=tmp_path,
        days=1,
        window_hours=12,
        use_llm_brief=True,
        topic_top=3,
        topic_evidence_limit=7,
        topic_batch_size=5,
        topic_batch_workers=2,
        use_llm_mainlines=False,
        isolate_model_calls=False,
    )

    manifest = result["manifest"]
    assert manifest["status"] == "succeeded"
    assert manifest["window_count"] == 2
    assert manifest["topic_run_count"] == 2
    assert manifest["parameters"]["topic_evidence_limit"] == 7
    assert manifest["parameters"]["topic_batch_size"] == 5
    assert manifest["parameters"]["topic_batch_workers"] == 2
    assert manifest["totals"]["raw_flash_count"] == 2
    assert len(brief_calls) == 2
    assert read_json(Path(result["latest_manifest_path"]))["run_id"] == result["run_id"]
    mainlines = read_json(Path(result["news_mainlines_json"]))
    assert mainlines["synthesis"]["method"] == "rule_fallback"
    assert mainlines["generated_by_run_id"] == result["run_id"]
    assert mainlines["mainlines"][0]["topic_ids"] == ["MKT_TOPIC_US_IRAN_HORMUZ"]
    assert mainlines["derived_report"]["schema_version"] == "derived_report.v1"
    assert mainlines["derived_report"]["generated_by_run_id"] == result["run_id"]
    assert mainlines["derived_report"]["evidence_weight"] == 0.0
    assert mainlines["derived_report"]["input_topic_ids"] == ["MKT_TOPIC_US_IRAN_HORMUZ"]
    assert mainlines["derived_report"]["input_evidence_ids"] == ["EV-1"]
    dependencies = [
        json.loads(line)
        for line in Path(result["news_mainlines_report_dependencies"]).read_text(encoding="utf-8").splitlines()
    ]
    dependency_roles = {item["dependency_role"] for item in dependencies}
    assert {"source_topic_run", "topic_context", "mainline_evidence"} <= dependency_roles
    assert any(item["input_object_id"] == "EV-1" for item in dependencies)
    assert read_json(Path(result["news_mainlines_derived_report"]))["report_id"] == mainlines["derived_report"]["report_id"]
    assert "news_mainlines_report_dependencies" in manifest["outputs"]


def test_topic_evolution_empty_run_ids_stays_empty(tmp_path):
    result = news_market_update.topic_evolution.publish_topic_evolution(
        root=tmp_path,
        run_ids=[],
    )
    payload = result["payload"]
    assert payload["source_run_count"] == 0
    assert payload["topic_count"] == 0
