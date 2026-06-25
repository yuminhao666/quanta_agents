from __future__ import annotations

from quanta_agents.core.io import read_json, write_json
from quanta_agents.knowledge_fabric import run_knowledge_fabric_closure


def _theme_anchor_payload() -> dict:
    return {
        "schema_version": "theme_anchor_candidate_set.v1",
        "status": "candidate",
        "candidate_id": "CAND-THEME-ANCHOR-HORMUZ",
        "theme_anchors": [
            {
                "schema_version": "theme_anchor.v1",
                "theme_anchor_id": "THA-HORMUZ",
                "status": "candidate",
                "created_at": "2026-06-22T00:00:00+00:00",
                "title": "霍尔木兹航运风险",
                "description": "霍尔木兹通航与中东地缘风险。",
                "anchor_kind": "research_report_repeated_theme",
                "theme_type": "geopolitics",
                "source_roles": ["research_report"],
                "source_refs": [],
                "asset_refs": [{"id": "FUT-SC", "label": "原油", "ref_type": "asset"}],
                "framework_node_refs": [{"id": "geo", "label": "地缘政治", "ref_type": "framework_node"}],
                "event_definition_layers": {
                    "fact_layer": "霍尔木兹通航与地缘风险变化。",
                    "political_layer": "中东局势。",
                    "time_window_layer": "2026-06-22",
                    "settlement_rule_layer": None,
                },
                "signal_refs": [],
                "evidence_refs": [],
                "lifecycle": {
                    "current_phase": "tracking",
                    "first_seen_at": "2026-06-22",
                    "last_seen_at": "2026-06-22",
                    "support_count": 1,
                    "conflict_count": 0,
                    "review_state": "machine_candidate",
                },
                "promotion_policy": "review_required",
            }
        ],
    }


def _research_signal_payload() -> dict:
    return {
        "schema_version": "research_signal_candidate_set.v1",
        "status": "candidate",
        "candidate_id": "CAND-SIGNAL-MAP-HORMUZ",
        "signals": [
            {
                "schema_version": "research_signal.v1",
                "signal_id": "SIG-RREP-HORMUZ",
                "status": "candidate",
                "created_at": "2026-06-22T00:00:00+00:00",
                "source_role": "research_report",
                "source_ref": {"ref_type": "evidence_capsule", "source_name": "hzzhqx_wechat", "id": "EVID-HORMUZ"},
                "signal_kind": "theme_update",
                "asset_refs": [{"id": "FUT-SC", "label": "原油", "ref_type": "asset"}],
                "theme_refs": [{"id": "THA-HORMUZ", "label": "霍尔木兹航运风险", "ref_type": "theme"}],
                "framework_node_refs": [{"id": "geo", "label": "地缘政治", "ref_type": "framework_node"}],
                "direction": "bullish",
                "strength": 0.6,
                "confidence": 0.7,
                "confidence_label": "medium_confidence_signal",
                "time_window": {"start": "2026-06-22", "end": "2026-06-22", "horizon": "daily"},
                "event_definition_layers": {
                    "fact_layer": "研报提示霍尔木兹通航仍有不确定性。",
                    "political_layer": "中东局势。",
                    "time_window_layer": "2026-06-22",
                    "settlement_rule_layer": None,
                },
                "evidence_refs": [{"evidence_id": "EVID-HORMUZ", "path": "evidence/EVID-HORMUZ.json"}],
                "conflict_refs": [],
                "human_review_required": True,
            }
        ],
    }


def _evidence_unit() -> dict:
    return {
        "schema_version": "research_report_evidence_unit.v1",
        "evidence_id": "EVID-HORMUZ",
        "generated_at": "2026-06-22T00:00:00+00:00",
        "source": {
            "source_system": "hzzhqx_wechat",
            "source_type": "wechat_article",
            "published_at": "2026-06-22 09:00:00",
        },
        "canonical_ref": {"document_id": "CAN-HORMUZ", "chunk_id": "CCHUNK-HORMUZ"},
        "claim": {
            "text": "研报提示霍尔木兹通航仍有不确定性，原油风险溢价可能重新抬升。",
            "claim_type": "research_report_statement",
            "direction": "bullish",
            "direction_score": 0.8,
            "scoring_role": "fundamental_evidence",
        },
        "snippet": {"text": "研报提示霍尔木兹通航仍有不确定性。"},
        "asset": {"name": "原油", "asset_id": "FUT-SC", "commodity_code": "SC"},
        "quality": {"confidence": 0.7, "human_review_required": True},
    }


def _news_logic_payload() -> dict:
    return {
        "schema_version": "news_logic_radar.v1",
        "status": "candidate",
        "generated_at": "2026-06-22T01:00:00+00:00",
        "events": [
            {
                "event_id": "NLOGIC-HORMUZ-OIL",
                "flash_id": "FLASH-HORMUZ",
                "asset": "原油",
                "asset_id": "FUT-SC",
                "publish_time": "2026-06-22 10:00:00",
                "text": "霍尔木兹海峡通航仍存不确定性，市场关注中东局势。",
                "direction_score": 1.0,
                "heat": 4.0,
                "framework_node": {"node_id": "geo", "label": "地缘政治", "dimension_label": "地缘政治"},
                "match": {"confidence": 0.8},
                "theme_anchor_refs": [
                    {
                        "id": "THA-HORMUZ",
                        "label": "霍尔木兹航运风险",
                        "ref_type": "theme_anchor",
                        "theme_type": "geopolitics",
                        "asset_refs": [{"id": "FUT-SC", "label": "原油", "ref_type": "asset"}],
                        "match_score": 0.9,
                        "match_method": "theme_anchor_title_alias",
                    }
                ],
                "consistency": {"status": "supports_thesis", "reason": "新闻与研报主题同向。"},
            }
        ],
    }


def test_knowledge_fabric_closure_converges_news_event_and_atomic_claim(tmp_path):
    theme_path = tmp_path / "agent_workspace/candidates/theme_anchor/2026/06/22/CAND/theme_anchors.json"
    signal_path = tmp_path / "agent_workspace/candidates/signal_map/2026/06/22/CAND/research_signals.json"
    evidence_path = tmp_path / "evidence_store/evidence_capsules/research_reports/hzzhqx_wechat/2026/06/22/EVID-HORMUZ.json"
    news_path = tmp_path / "agent_workspace/candidates/opinion_radar/news_logic/latest/news-logic.json"
    write_json(theme_path, _theme_anchor_payload())
    write_json(signal_path, _research_signal_payload())
    write_json(evidence_path, _evidence_unit())
    write_json(news_path, _news_logic_payload())

    result = run_knowledge_fabric_closure(
        tmp_path,
        news_logic_path=news_path,
        theme_anchor_paths=[theme_path],
        research_signal_paths=[signal_path],
        evidence_paths=[evidence_path],
        date="20260622",
    )

    payload = result["payload"]
    assert result["stats"]["canonical_event_count"] == 1
    assert result["stats"]["atomic_claim_count"] == 1
    assert result["stats"]["converged_topic_count"] == 1
    topic = payload["persistent_topics"][0]
    assert topic["canonical_event_ids"]
    assert topic["atomic_claim_ids"]
    object_types = {item["object_type"] for item in payload["topic_memberships"]}
    assert {"canonical_event", "atomic_claim"} <= object_types
    assert read_json(tmp_path / result["paths"]["knowledge_fabric"])["schema_version"] == "knowledge_fabric_closure.v1"
