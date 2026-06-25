from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from quanta_agents.core.io import read_json, write_json
from quanta_agents.market_themes import recent_news_topics


def _seed_brief(root):
    brief_path = root / "agent_workspace/candidates/news_brief/half_day/latest/half-day-news-brief.json"
    write_json(
        brief_path,
        {
            "schema_version": "half_day_news_brief.v1",
            "generated_at": "2026-06-21T12:00:00",
            "title": "过去半日新闻汇总",
            "time_window": {"start_time": "2026-06-21 00:00:00", "end_time": "2026-06-21 12:00:00"},
            "stats": {"flash_count": 2},
            "top_flashes": [
                {
                    "flash_id": "flash-1",
                    "publish_time": "2026-06-21 10:00:00",
                    "important": 1,
                    "summary": "美伊谈判进入关键阶段，霍尔木兹海峡通行仍待确认。",
                }
            ],
            "llm_brief": {
                "key_news": [
                    {
                        "text": "美伊瑞士谈判进入关键阶段，霍尔木兹通行仍存在分歧。",
                        "source_refs": [{"ref_type": "flash", "id": "flash-1"}],
                    }
                ],
                "watch_items": [
                    {
                        "text": "继续观察美伊谈判结果和霍尔木兹通行恢复。",
                        "source_refs": [{"ref_type": "flash", "id": "flash-1"}],
                    }
                ],
            },
        },
    )
    return brief_path


def test_recent_news_topics_uses_llm_evidence_ids(tmp_path, monkeypatch):
    _seed_brief(tmp_path)
    monkeypatch.setattr(recent_news_topics, "_llm_available", lambda provider: (True, "fake-llm:model"))

    def fake_chat(prompt: str, **_: object) -> str:
        start = prompt.index("```json") + len("```json")
        end = prompt.index("```", start)
        pack = json.loads(prompt[start:end])
        assert "source_refs" not in pack["signals"][0]
        evidence_id = pack["signals"][0]["id"]
        return json.dumps(
            {
                "topics": [
                    {
                        "topic_id": "MKT_TOPIC_US_IRAN_HORMUZ",
                        "canonical_name": "美伊谈判与霍尔木兹通行",
                        "topic_type": "geopolitics",
                        "definition": "美伊谈判和霍尔木兹通行变化形成的地缘风险主题。",
                        "aliases": ["霍尔木兹通行"],
                        "hotspot_role": True,
                        "driver_role": True,
                        "driver_refs": ["geopolitics"],
                        "asset_refs": ["原油"],
                        "evidence_ids": [evidence_id, "missing-id"],
                        "confidence": 0.91,
                        "state": {
                            "heat": 0.6,
                            "strength": 0.5,
                            "trend": "emerging",
                            "change_explanation": "新闻集中指向美伊谈判。",
                        },
                        "watch_items": ["观察谈判结果"],
                    }
                ],
                "discarded_evidence": [],
                "quality_notes": [],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(recent_news_topics, "chat", fake_chat)
    result = recent_news_topics.publish_recent_news_topics(
        root=tmp_path,
        include_hourly=False,
        require_llm=True,
        now=datetime(2026, 6, 21, 12, 0, 0),
    )

    payload = result["payload"]
    assert payload["extraction"]["method"] == "llm"
    assert payload["extraction"]["input_evidence_count"] == 3
    assert payload["extraction"]["candidate_evidence_count"] == 3
    assert payload["extraction"]["prompt_char_count"] > 0
    assert payload["extraction"]["invalid_ref_count"] == 1
    assert payload["extraction"]["topic_membership_count"] == 1
    assert payload["extraction"]["supporting_evidence_count"] == 0
    assert payload["extraction"]["derived_context_count"] == 1
    assert payload["extraction"]["evidence_weight_summary"] == {
        "candidate_evidence_count": 3,
        "candidate_positive_weight_count": 0,
        "candidate_zero_weight_count": 3,
        "used_evidence_count": 1,
        "supporting_evidence_count": 0,
        "contextual_evidence_count": 1,
        "derived_context_count": 1,
        "total_evidence_weight": 0.0,
    }
    assert payload["topic_nodes"][0]["topic_id"] == "MKT_TOPIC_US_IRAN_HORMUZ"
    assert payload["topic_states"][0]["strength"] == 0.5
    assert payload["topic_states"][0]["supporting_evidence_count"] == 0
    assert payload["topic_states"][0]["derived_context_count"] == 1
    assert payload["topic_states"][0]["evidence_weight_sum"] == 0.0
    assert payload["topic_memberships"][0]["object_type"] == "event_report"
    assert payload["topic_memberships"][0]["topic_id"] == "MKT_TOPIC_US_IRAN_HORMUZ"
    assert payload["topic_memberships"][0]["object_id"] == payload["evidence_links"][0]["evidence_id"]
    assert payload["topic_memberships"][0]["relation_to_topic"] == "contextualizes"
    assert payload["topic_memberships"][0]["stance"] == "neutral"
    assert payload["topic_memberships"][0]["evidence_weight"] == 0.0
    assert payload["evidence_links"][0]["membership_id"] == payload["topic_memberships"][0]["membership_id"]
    assert payload["evidence_links"][0]["evidence_weight"] == 0.0
    assert payload["evidence_links"][0]["source_refs"] == [{"ref_type": "flash", "id": "flash-1"}]
    assert read_json(tmp_path / "agent_workspace/candidates/market_topics/latest/topic-registry.json")[
        "topics"
    ]
    manifest = read_json(Path(result["run_manifest"]))
    assert manifest["metrics"]["supporting_evidence_count"] == 0
    assert manifest["metrics"]["derived_context_count"] == 1
    assert manifest["metrics"]["evidence_weight_summary"] == payload["extraction"][
        "evidence_weight_summary"
    ]
    assert (tmp_path / "agent_workspace/candidates/market_topics/latest/topic-states.jsonl").exists()
    assert (tmp_path / "agent_workspace/candidates/market_topics/latest/topic-memberships.jsonl").exists()
    assert (tmp_path / "agent_workspace/candidates/market_topics/latest/topic-evidence.jsonl").exists()
    assert (tmp_path / "agent_workspace/candidates/market_topics/latest/topic-events.jsonl").exists()


def test_recent_news_topics_weight_summary_separates_support_from_context(tmp_path):
    generated_at = "2026-06-21 12:00:00"
    evidence = [
        {
            "evidence_id": "OBS-1",
            "source_type": "inventory_feed",
            "source_path": "canonical/observations/inventory.jsonl",
            "source_field": "rows[0]",
            "source_role": "observation",
            "object_type": "observation",
            "truth_status": "observed",
            "epistemic_status": "observation",
            "evidence_weight": 1.0,
            "summary": "美国原油库存下降。",
            "asset_refs": ["原油"],
            "source_refs": [{"ref_type": "observation", "id": "OBS-1"}],
            "publish_time": generated_at,
        },
        {
            "evidence_id": "NTEV-DERIVED",
            "source_type": "half_day_news_brief",
            "source_path": "agent_workspace/candidates/news_brief/half_day/latest/half-day-news-brief.json",
            "source_field": "llm_brief.key_news[0]",
            "source_role": "key_news",
            "object_type": "derived_summary",
            "truth_status": "unverified",
            "epistemic_status": "derived",
            "evidence_weight": 0.0,
            "summary": "半日简报总结原油库存变化。",
            "asset_refs": ["原油"],
            "source_refs": [{"ref_type": "flash", "id": "flash-1"}],
            "publish_time": generated_at,
        },
    ]
    payload = recent_news_topics._build_payload_from_topics(
        root=tmp_path,
        evidence=evidence,
        parsed={
            "topics": [
                {
                    "topic_id": "MKT_TOPIC_US_CRUDE_INVENTORY",
                    "canonical_name": "美国原油库存变化",
                    "topic_type": "inventory",
                    "evidence_ids": ["OBS-1", "NTEV-DERIVED"],
                    "confidence": 0.8,
                    "state": {
                        "heat": 0.7,
                        "strength": 0.6,
                        "trend": "strengthening",
                        "change_explanation": "库存数据和新闻上下文共同指向库存主题。",
                    },
                }
            ],
            "discarded_evidence": [],
            "quality_notes": [],
        },
        source_refs={},
        provider_status="test-provider",
        extraction_method="unit_test",
        generated_at=generated_at,
        top=4,
        input_evidence_count=2,
    )

    extraction = payload["extraction"]
    state = payload["topic_states"][0]
    memberships = {item["object_id"]: item for item in payload["topic_memberships"]}

    assert extraction["supporting_evidence_count"] == 1
    assert extraction["derived_context_count"] == 1
    assert extraction["evidence_weight_summary"]["total_evidence_weight"] == 1.0
    assert state["supporting_evidence_count"] == 1
    assert state["contextual_evidence_count"] == 1
    assert state["derived_context_count"] == 1
    assert state["evidence_weight_sum"] == 1.0
    assert memberships["OBS-1"]["relation_to_topic"] == "updates"
    assert memberships["OBS-1"]["stance"] == "supports"
    assert memberships["NTEV-DERIVED"]["relation_to_topic"] == "contextualizes"
    assert memberships["NTEV-DERIVED"]["stance"] == "neutral"


def test_recent_news_topics_limits_llm_evidence_candidates(tmp_path, monkeypatch):
    _seed_brief(tmp_path)
    monkeypatch.setattr(recent_news_topics, "_llm_available", lambda provider: (True, "fake-llm:model"))

    seen_pack = {}

    def fake_chat(prompt: str, **_: object) -> str:
        start = prompt.index("```json") + len("```json")
        end = prompt.index("```", start)
        pack = json.loads(prompt[start:end])
        seen_pack.update(pack)
        evidence_id = pack["signals"][0]["id"]
        return json.dumps(
            {
                "topics": [
                    {
                        "topic_id": "MKT_TOPIC_US_IRAN_HORMUZ",
                        "canonical_name": "美伊谈判与霍尔木兹通行",
                        "evidence_ids": [evidence_id],
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(recent_news_topics, "chat", fake_chat)
    result = recent_news_topics.publish_recent_news_topics(
        root=tmp_path,
        include_hourly=False,
        require_llm=True,
        llm_evidence_limit=2,
        now=datetime(2026, 6, 21, 12, 0, 0),
    )

    assert len(seen_pack["signals"]) == 2
    assert result["payload"]["extraction"]["input_evidence_count"] == 3
    assert result["payload"]["extraction"]["candidate_evidence_count"] == 2


def test_recent_news_topics_repairs_malformed_llm_output(tmp_path, monkeypatch):
    _seed_brief(tmp_path)
    monkeypatch.setattr(recent_news_topics, "_llm_available", lambda provider: (True, "fake-llm:model"))
    calls = []

    def fake_chat(prompt: str, **_: object) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps({"items": [{"title": "格式漂移"}]}, ensure_ascii=False)
        return json.dumps(
            {
                "topics": [
                    {
                        "topic_id": "MKT_TOPIC_US_IRAN_HORMUZ",
                        "canonical_name": "美伊谈判与霍尔木兹通行",
                        "evidence_ids": ["NTEV-MISSING"],
                    }
                ],
                "quality_notes": ["repaired"],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(recent_news_topics, "chat", fake_chat)
    result = recent_news_topics.publish_recent_news_topics(
        root=tmp_path,
        include_hourly=False,
        require_llm=True,
        now=datetime(2026, 6, 21, 12, 0, 0),
    )

    extraction = result["payload"]["extraction"]
    assert len(calls) == 2
    assert "JSON 修复器" in calls[1]
    assert extraction["llm_retry_count"] == 1
    assert extraction["llm_parse_error"] == "LLM output missing topics list; keys=items"


def test_recent_news_topics_batches_and_merges_llm_calls(tmp_path, monkeypatch):
    _seed_brief(tmp_path)
    monkeypatch.setattr(recent_news_topics, "_llm_available", lambda provider: (True, "fake-llm:model"))
    calls = []

    def fake_chat(prompt: str, **_: object) -> str:
        calls.append(prompt)
        start = prompt.index("```json") + len("```json")
        end = prompt.index("```", start)
        pack = json.loads(prompt[start:end])
        if "candidate_topics" in pack:
            topics = pack["candidate_topics"]
            return json.dumps({"topics": topics}, ensure_ascii=False)
        evidence_id = pack["signals"][0]["id"]
        return json.dumps(
            {
                "topics": [
                    {
                        "topic_id": f"MKT_TOPIC_BATCH_{len(calls)}",
                        "canonical_name": f"批次主题{len(calls)}",
                        "evidence_ids": [evidence_id],
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(recent_news_topics, "chat", fake_chat)
    result = recent_news_topics.publish_recent_news_topics(
        root=tmp_path,
        include_hourly=False,
        require_llm=True,
        llm_batch_size=2,
        llm_batch_workers=1,
        now=datetime(2026, 6, 21, 12, 0, 0),
    )

    extraction = result["payload"]["extraction"]
    assert len(calls) == 3
    assert extraction["llm_batch_count"] == 2
    assert extraction["prompt_total_char_count"] >= extraction["prompt_char_count"]


def test_recent_news_topics_extracts_graph_trigger_candidates(tmp_path):
    brief_path = _seed_brief(tmp_path)
    payload = read_json(brief_path)
    payload["graph_trigger_candidates"] = [
        {
            "event_id": "event-1",
            "flash_id": "flash-1",
            "publish_time": "2026-06-21 10:00:00",
            "asset": "原油",
            "dimension_label": "地缘供应风险",
            "direction_score": 0.7,
            "heat": 1.2,
            "consistency_status": "new_signal",
            "text": "霍尔木兹通行风险上升，原油供应风险溢价抬升。",
        }
    ]
    write_json(brief_path, payload)

    evidence, source_info = recent_news_topics._evidence_from_brief(
        tmp_path,
        brief_path,
        "half_day_news_brief",
    )

    assert source_info["structured_counts"]["graph_trigger_candidates"] == 1
    top_flash = next(item for item in evidence if item["source_role"] == "top_flash")
    assert top_flash["evidence_weight"] == 0.0
    assert top_flash["asset_refs"] == ["原油"]
    assert top_flash["source_refs"] == [
        {"ref_type": "flash", "id": "flash-1"},
        {"ref_type": "event", "id": "event-1"},
    ]
    derived = next(item for item in evidence if item["source_role"] == "key_news")
    assert derived["object_type"] == "derived_summary"
    assert derived["evidence_weight"] == 0.0
    pack = recent_news_topics._candidate_pack([top_flash])
    assert pack["signals"][0]["ev"][0]["id"] == "event-1"
    assert "source_refs" not in pack["signals"][0]


def test_recent_news_topics_can_require_llm(tmp_path, monkeypatch):
    _seed_brief(tmp_path)
    monkeypatch.setattr(recent_news_topics, "_llm_available", lambda provider: (False, "missing key"))

    with pytest.raises(RuntimeError, match="required but unavailable"):
        recent_news_topics.publish_recent_news_topics(
            root=tmp_path,
            include_hourly=False,
            require_llm=True,
            now=datetime(2026, 6, 21, 12, 0, 0),
        )


def test_recent_news_topics_accepts_common_llm_topic_aliases():
    parsed = recent_news_topics._parse_response(
        json.dumps({"market_topics": [{"topic_id": "MKT_TOPIC_A", "canonical_name": "主题A"}]})
    )
    assert parsed["topics"][0]["topic_id"] == "MKT_TOPIC_A"

    nested = recent_news_topics._parse_response(
        json.dumps({"result": {"topic_candidates": [{"topic_id": "MKT_TOPIC_B"}]}})
    )
    assert nested["topics"][0]["topic_id"] == "MKT_TOPIC_B"

    with_prefix = recent_news_topics._parse_response(
        '说明 {"note":"not the payload"}\n```json\n{"topics":[{"topic_id":"MKT_TOPIC_C"}]}\n```'
    )
    assert with_prefix["topics"][0]["topic_id"] == "MKT_TOPIC_C"
