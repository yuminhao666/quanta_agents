from __future__ import annotations

from pathlib import Path

from quanta_agents.core.io import read_json, write_json
from quanta_agents.signal_mapping import (
    build_polymarket_event_definition_signal,
    map_futures_brief_candidates,
    map_research_evidence_candidates,
    run_signal_theme_mapping,
)


CREATED_AT = "2026-06-20T00:00:00+00:00"


def _assert_signal_shape(signal: dict) -> None:
    assert signal["schema_version"] == "research_signal.v1"
    assert signal["signal_id"].startswith("SIG-")
    assert signal["status"] == "candidate"
    assert signal["source_role"] in {
        "news",
        "research_report",
        "market_data",
        "fundamental_data",
        "web_info",
        "human",
        "agent",
    }
    assert signal["source_ref"]["ref_type"]
    assert signal["signal_kind"] in {
        "thesis_support",
        "thesis_conflict",
        "theme_update",
        "event_definition",
        "price_validation",
        "fundamental_validation",
        "sentiment_observation",
        "framework_mapping",
        "human_judgement",
        "agent_observation",
    }
    assert isinstance(signal["asset_refs"], list)
    assert isinstance(signal["theme_refs"], list)
    assert isinstance(signal["framework_node_refs"], list)
    assert 0 <= signal["strength"] <= 1
    assert 0 <= signal["confidence"] <= 1
    assert set(signal["event_definition_layers"]) == {
        "fact_layer",
        "political_layer",
        "time_window_layer",
        "settlement_rule_layer",
    }
    assert isinstance(signal["human_review_required"], bool)


def _assert_theme_shape(theme: dict) -> None:
    assert theme["schema_version"] == "theme_anchor.v1"
    assert theme["theme_anchor_id"].startswith("THA-")
    assert theme["status"] == "candidate"
    assert theme["title"]
    assert theme["source_roles"]
    assert isinstance(theme["source_refs"], list)
    assert isinstance(theme["asset_refs"], list)
    assert set(theme["event_definition_layers"]) == {
        "fact_layer",
        "political_layer",
        "time_window_layer",
        "settlement_rule_layer",
    }
    assert theme["lifecycle"]["review_state"] == "machine_candidate"


def _brief_anchor() -> dict:
    return {
        "schema_version": "brief_thesis_anchor.v1",
        "status": "candidate",
        "report_date": "20260618",
        "generated_at": CREATED_AT,
        "source": "market_brief_and_commodity_summary_baseline",
        "baseline_policy": "market brief is teacher baseline",
        "assets": {
            "原油": {
                "anchor_id": "BRANCH-OIL-001",
                "asset": "原油",
                "asset_ref": "FUT-SC",
                "direction": "bearish",
                "direction_label": "偏空",
                "source_sentiment_score": -3,
                "thesis_title": "原油偏空主线：霍尔木兹通航恢复",
                "main_thesis": "美伊协议落地后霍尔木兹通航恢复，地缘风险溢价回吐，原油主线偏空。",
                "key_evidence": [
                    {
                        "evidence_id": "BRFEV-OIL-001",
                        "source_field": "bearish_factors",
                        "direction": "bearish",
                        "text": "霍尔木兹海峡恢复通航，地缘风险溢价回吐",
                    }
                ],
                "tracking_items": ["跟踪60天协议观察期"],
                "risk_items": ["若协议执行反复，地缘溢价可能重估"],
                "invalidation_conditions": ["若协议执行反复，地缘溢价可能重估"],
            }
        },
    }


def _benchmark_map() -> dict:
    return {
        "schema_version": "brief_logic_benchmark_map.v1",
        "status": "candidate",
        "report_date": "20260618",
        "generated_at": CREATED_AT,
        "primary_display_contract": "market_brief_and_commodity_summary",
        "logic_chain_role": "incremental_signal_evidence_skeleton",
        "assets": {
            "原油": {
                "asset": "原油",
                "asset_ref": "FUT-SC",
                "anchor_id": "BRANCH-OIL-001",
                "asset_relation": "has_conflicts_needing_review",
                "stats": {
                    "logic_chain_count": 1,
                    "inherited_signal_count": 1,
                    "new_signal_count": 0,
                    "conflict_signal_count": 1,
                    "pending_observation_count": 0,
                },
                "chain_maps": [
                    {
                        "chain_id": "CHAIN-OIL-GEO",
                        "asset": "原油",
                        "dimension_id": "oil.geopolitics",
                        "dimension_label": "地缘政治",
                        "dimension_score": -2.4,
                        "benchmark_relation": "mixed_conflict_needs_review",
                        "inherited_signals": [
                            {
                                "signal_id": "brief-bearish",
                                "relation": "inherits_brief_anchor",
                                "direction": "bearish",
                                "source_field": "bearish_factors",
                                "text": "霍尔木兹海峡恢复通航，地缘风险溢价回吐",
                                "confidence": 0.88,
                            }
                        ],
                        "new_signals": [],
                        "conflict_signals": [
                            {
                                "signal_id": "realtime-bullish",
                                "relation": "conflicts_with_brief_anchor",
                                "direction": "bullish",
                                "source_field": "bullish_factors",
                                "text": "协议执行出现反复，美国警告可能恢复军事打击",
                                "confidence": 0.7,
                            }
                        ],
                        "pending_observations": [],
                        "quality_gate": "logic_chain_incremental_layer_only",
                    }
                ],
            }
        },
    }


def _canonical_document() -> dict:
    return {
        "schema_version": "canonical_research_report_document.v1",
        "document_id": "CAN-RREP-WECHAT-TEST",
        "source_system": "hzzhqx_wechat",
        "content_hash": "hash-canonical",
        "metadata": {"published_at": "2026-06-18 09:48:23"},
    }


def _research_evidence(direction: str = "bearish") -> dict:
    return {
        "schema_version": "research_report_evidence_unit.v1",
        "artifact_type": "evidence_unit",
        "evidence_id": "EVID-RREP-WECHAT-TEST",
        "status": "candidate",
        "generated_at": CREATED_AT,
        "content_hash": "hash-evidence",
        "source": {
            "source_system": "hzzhqx_wechat",
            "source_type": "wechat_article",
            "source_account": "测试期货",
            "title": "测试早参",
            "published_at": "2026-06-18 09:48:23",
            "canonical_url": "https://example.com/wechat",
        },
        "canonical_ref": {
            "document_id": "CAN-RREP-WECHAT-TEST",
            "path": "canonical_documents/research_reports/hzzhqx_wechat/2026/06/18/CAN-RREP-WECHAT-TEST.json",
            "chunk_id": "CCHUNK-TEST",
        },
        "claim": {
            "text": "美联储点阵图转鹰，加息风险显著上升，股指资金面承压。",
            "claim_type": "research_report_statement",
            "direction": direction,
            "direction_score": -0.8 if direction == "bearish" else 0.8,
            "scoring_role": "fundamental_evidence",
        },
        "snippet": {
            "text": "美联储点阵图转鹰，加息风险显著上升，股指资金面承压。",
            "section_heading": "美联储加息概率提升",
        },
        "asset": {
            "name": "沪深300股指",
            "asset_id": "FUT-IF",
            "commodity_code": "IF",
            "category": "金融期货",
            "sector": "金融期货",
        },
        "quality": {
            "confidence": 0.55,
            "human_review_required": True,
        },
    }


def test_futures_baseline_and_benchmark_generate_signal_and_theme_candidates() -> None:
    signals, themes = map_futures_brief_candidates(
        _brief_anchor(),
        _benchmark_map(),
        brief_anchor_path="agent_workspace/candidates/futures_daily_raw_runs/2026/06/18/RUN/brief_thesis_anchor.json",
        benchmark_map_path="agent_workspace/candidates/futures_daily_raw_runs/2026/06/18/RUN/brief_logic_benchmark_map.json",
        created_at=CREATED_AT,
    )

    assert len(signals) == 3
    assert len(themes) == 1
    theme = themes[0]
    assert theme["anchor_kind"] == "market_brief_thesis"
    assert theme["theme_type"] == "geopolitics"
    assert theme["lifecycle"]["conflict_count"] == 1
    assert len(theme["signal_refs"]) == 3

    conflict = next(signal for signal in signals if signal["signal_kind"] == "thesis_conflict")
    assert conflict["conflict_refs"]
    assert conflict["framework_node_refs"][0]["label"] == "地缘政治"
    for signal in signals:
        _assert_signal_shape(signal)
    _assert_theme_shape(theme)


def test_research_evidence_generates_signal_and_theme_anchor() -> None:
    signals, themes = map_research_evidence_candidates(
        _canonical_document(),
        [_research_evidence()],
        canonical_path="canonical_documents/research_reports/hzzhqx_wechat/2026/06/18/CAN-RREP-WECHAT-TEST.json",
        evidence_paths={
            "EVID-RREP-WECHAT-TEST": "evidence_store/evidence_capsules/research_reports/hzzhqx_wechat/2026/06/18/EVID-RREP-WECHAT-TEST.json"
        },
        created_at=CREATED_AT,
    )

    assert len(signals) == 1
    assert len(themes) == 1
    signal = signals[0]
    theme = themes[0]
    assert signal["source_role"] == "research_report"
    assert signal["source_ref"]["ref_type"] == "evidence_capsule"
    assert signal["direction"] == "bearish"
    assert signal["theme_refs"][0]["id"] == theme["theme_anchor_id"]
    assert theme["source_roles"] == ["research_report"]
    assert theme["theme_type"] == "macro"
    assert theme["lifecycle"]["support_count"] == 1
    _assert_signal_shape(signal)
    _assert_theme_shape(theme)


def test_noisy_research_heading_is_mapped_to_controlled_theme_title() -> None:
    noisy = _research_evidence()
    noisy["snippet"]["section_heading"] = ""
    noisy["claim"]["text"] = "编辑日期：2026/6/18 资讯速递 宏观要闻 美联储点阵图转鹰，加息风险显著上升。"

    _, themes = map_research_evidence_candidates(
        _canonical_document(),
        [noisy],
        canonical_path="canonical_documents/research_reports/hzzhqx_wechat/2026/06/18/CAN-RREP-WECHAT-TEST.json",
        evidence_paths={
            "EVID-RREP-WECHAT-TEST": "evidence_store/evidence_capsules/research_reports/hzzhqx_wechat/2026/06/18/EVID-RREP-WECHAT-TEST.json"
        },
        created_at=CREATED_AT,
    )

    assert themes[0]["title"] == "美联储政策转鹰"
    assert not themes[0]["title"].startswith("编辑日期")


def test_run_signal_theme_mapping_writes_candidate_sets(tmp_path: Path) -> None:
    canonical_path = tmp_path / "canonical_documents/research_reports/hzzhqx_wechat/2026/06/18/CAN-RREP-WECHAT-TEST.json"
    evidence_path = tmp_path / "evidence_store/evidence_capsules/research_reports/hzzhqx_wechat/2026/06/18/EVID-RREP-WECHAT-TEST.json"
    write_json(canonical_path, _canonical_document())
    write_json(evidence_path, _research_evidence())

    result = run_signal_theme_mapping(
        tmp_path,
        canonical_document_path=canonical_path,
        evidence_dir=evidence_path.parent,
        report_date="20260618",
        validate=False,
    )

    assert result["status"] == "succeeded"
    assert result["signal_count"] == 1
    assert result["theme_anchor_count"] == 1
    signal_payload = read_json(tmp_path / result["paths"]["research_signals"])
    theme_payload = read_json(tmp_path / result["paths"]["theme_anchors"])
    run_manifest = read_json(tmp_path / result["paths"]["run_manifest"])
    assert signal_payload["signals"][0]["schema_version"] == "research_signal.v1"
    assert theme_payload["theme_anchors"][0]["schema_version"] == "theme_anchor.v1"
    assert run_manifest["run_type"] == "signal_theme_mapper"
    assert run_manifest["human_review_required"] is True


def test_polymarket_hook_remains_low_confidence_event_definition() -> None:
    signal = build_polymarket_event_definition_signal(
        market_id="oil-event-1",
        question="Will the Strait of Hormuz remain open through June 2026?",
        price=0.61,
        spread=0.03,
        liquidity=12000,
        liquidity_label="medium",
        settlement_rule="Resolve according to the market's published open/closed shipping-lane criteria.",
        time_window={"start": "2026-06-20", "end": "2026-06-30", "horizon": "event_window"},
        source_path="agent_workspace/candidates/polymarket_daily/2026/06/19/CAND/manifest.json",
    )

    assert signal["source_role"] == "web_info"
    assert signal["signal_kind"] == "event_definition"
    assert signal["confidence_label"] == "low_confidence_signal"
    assert signal["market_observation"]["probability"] is None
    assert signal["market_observation"]["price"] == 0.61
    assert signal["event_definition_layers"]["settlement_rule_layer"]
    _assert_signal_shape(signal)
