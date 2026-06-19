from __future__ import annotations

import json

from quanta_agents.futures_daily import logic_chain
from quanta_agents.futures_daily.logic_chain import build_logic_chain_bundle


def test_build_logic_chain_bundle_scores_dimensions_and_candidates():
    summary = {
        "date": "20260618",
        "sentiment_scores": {"原油": -4},
        "source_report_hash": "hash-1",
        "analysis_date": "2026-06-18T00:00:00",
        "detailed_analysis": {
            "原油": {
                "original_sources": [{"row_id": "r1"}, {"row_id": "r2"}],
            }
        },
    }
    alignment = {
        "frameworks_used": {
            "原油": {
                "framework_id": "fw_futures_ine_crude_oil",
                "asset_id": "futures.INE.crude_oil",
            }
        },
        "alignments": [
            {
                "factor_id": "f1",
                "asset": "原油",
                "source_field": "bearish_factors",
                "direction": "bearish",
                "text": "霍尔木兹海峡重新开放，地缘风险溢价回吐",
                "framework_node": {
                    "node_id": "futures_ine_crude_oil_geopolitics",
                    "label": "原油/地缘政治",
                },
                "match": {"method": "framework_dimension", "confidence": 0.86},
                "status": "auto_linked",
            },
            {
                "factor_id": "f2",
                "asset": "原油",
                "source_field": "price_forecast",
                "direction": "forecast",
                "text": "绝对价格面临偏空情绪",
                "framework_node": {"node_id": "default::未归类", "label": "未归类"},
                "match": {"method": "default_dimension", "confidence": 0.2},
                "status": "pending_review",
            },
            {
                "factor_id": "f3",
                "asset": "原油",
                "source_field": "key_data",
                "direction": "neutral",
                "text": "SC夜盘下跌1.96%",
                "framework_node": {"node_id": "default::未归类", "label": "未归类"},
                "match": {"method": "default_dimension", "confidence": 0.2},
                "status": "pending_review",
            },
        ],
    }

    bundle = build_logic_chain_bundle(summary, alignment)

    oil_scores = bundle["dimension_scores"]["assets"]["原油"]
    assert oil_scores["framework_score"] < 0
    assert oil_scores["dimensions"][0]["dimension_label"] == "地缘政治"
    oil_thesis = bundle["trade_thesis"]["assets"]["原油"]
    assert oil_thesis["recommendation"] in {"偏空观察", "小幅偏空"}
    assert "维度：" not in oil_thesis["main_trade_thesis"]
    assert len(oil_thesis["all_dimensions"]) == oil_scores["dimension_count"]
    assert oil_thesis["all_dimensions"][0]["effective_weight_pct"] > 0
    assert "weighted_contribution" in oil_thesis["all_dimensions"][0]
    assert bundle["trade_thesis"]["weighting_method"]["method"] == "deduped_evidence_netting_with_conflict_control"
    assert bundle["logic_chains"]["assets"]["原油"]["dimension_overview"] == oil_thesis["all_dimensions"]
    assert bundle["logic_chains"]["assets"]["原油"]["logic_chains"]
    assert bundle["logic_summary"]["separated_from_market_review"] is True
    assert bundle["logic_summary"]["source"] == "logic_chain_agent"
    assert "market_review" not in json.dumps(bundle["logic_summary"]["source_files"], ensure_ascii=False)
    assert bundle["logic_summary"]["important_events_summary"]
    assert bundle["logic_summary"]["important_logic_summary"]
    candidates = bundle["framework_update_candidates"]["candidates"]
    assert candidates
    assert candidates[0]["candidate_type"] == "new_dimension"


def test_dimension_scores_merge_duplicate_cross_field_evidence():
    summary = {
        "date": "20260618",
        "sentiment_scores": {"天然橡胶": 2},
        "detailed_analysis": {"天然橡胶": {"original_sources": [{"row_id": "r1"}]}},
    }
    alignment = {
        "frameworks_used": {
            "天然橡胶": {
                "framework_id": "fw_futures_shfe_rubber",
                "asset_id": "futures.SHFE.rubber",
            }
        },
        "alignments": [
            {
                "factor_id": "f1",
                "asset": "天然橡胶",
                "source_field": "bullish_factors",
                "direction": "bullish",
                "text": "青岛地区天胶保税和一般贸易合计库存量68.16万吨，环比减少1.52万吨，降幅2.18%",
                "framework_node": {"node_id": "rubber_inventory", "label": "天然橡胶/库存"},
                "match": {"method": "framework_dimension", "confidence": 0.72},
                "status": "auto_linked",
            },
            {
                "factor_id": "f2",
                "asset": "天然橡胶",
                "source_field": "key_data",
                "direction": "neutral",
                "text": "青岛地区天胶保税和一般贸易合计库存量68.16万吨，环比减少1.52万吨，降幅2.18%",
                "framework_node": {"node_id": "rubber_inventory", "label": "天然橡胶/库存"},
                "match": {"method": "framework_dimension", "confidence": 0.72},
                "status": "auto_linked",
            },
            {
                "factor_id": "f3",
                "asset": "天然橡胶",
                "source_field": "supply_demand",
                "direction": "neutral",
                "text": "国内贸易环节去库",
                "framework_node": {"node_id": "rubber_inventory", "label": "天然橡胶/库存"},
                "match": {"method": "framework_dimension", "confidence": 0.72},
                "status": "auto_linked",
            },
            {
                "factor_id": "f4",
                "asset": "天然橡胶",
                "source_field": "bullish_factors",
                "direction": "bullish",
                "text": "国内贸易环节去库，天胶企稳",
                "framework_node": {"node_id": "rubber_inventory", "label": "天然橡胶/库存"},
                "match": {"method": "framework_dimension", "confidence": 0.72},
                "status": "auto_linked",
            },
        ],
    }

    bundle = build_logic_chain_bundle(summary, alignment)
    dimension = bundle["dimension_scores"]["assets"]["天然橡胶"]["dimensions"][0]

    assert dimension["evidence_count"] == 4
    assert dimension["deduped_evidence_count"] == 2
    assert dimension["duplicate_count"] == 2
    assert all(group["support_level"] == "cross_field" for group in dimension["evidence_groups"])
    primary = bundle["trade_thesis"]["assets"]["天然橡胶"]["primary_dimensions"][0]
    assert primary["key_evidence"][0]["merged_count"] == 2
    all_dimensions = bundle["trade_thesis"]["assets"]["天然橡胶"]["all_dimensions"]
    assert len(all_dimensions) == 1
    assert all_dimensions[0]["dimension_label"] == "库存"
    assert all_dimensions[0]["deduped_evidence_count"] == 2


def test_market_observations_do_not_drive_fundamental_score():
    summary = {
        "date": "20260618",
        "sentiment_scores": {"铜": 0},
        "detailed_analysis": {"铜": {"original_sources": [{"row_id": "r1"}]}},
    }
    alignment = {
        "frameworks_used": {
            "铜": {
                "framework_id": "fw_futures_shfe_cu",
                "asset_id": "futures.SHFE.cu",
            }
        },
        "alignments": [
            {
                "factor_id": "f1",
                "asset": "铜",
                "source_field": "price_forecast",
                "direction": "forecast",
                "text": "沪铜主力合约收跌，盘面承压，技术面下行",
                "framework_node": {"node_id": "cu_trading", "label": "沪铜/价格与交易"},
                "match": {"method": "framework_dimension", "confidence": 0.8},
                "status": "auto_linked",
            },
            {
                "factor_id": "f2",
                "asset": "铜",
                "source_field": "key_data",
                "direction": "neutral",
                "text": "沪铜夜盘下跌1.5%",
                "framework_node": {"node_id": "cu_trading", "label": "沪铜/价格与交易"},
                "match": {"method": "framework_dimension", "confidence": 0.8},
                "status": "auto_linked",
            },
        ],
    }

    bundle = build_logic_chain_bundle(summary, alignment)
    scores = bundle["dimension_scores"]["assets"]["铜"]
    dimension = scores["dimensions"][0]

    assert scores["framework_score"] == 0
    assert dimension["direction_score"] == 0
    assert dimension["importance_score"] == 0
    assert dimension["market_observation_count"] == 2
    assert dimension["scored_evidence_count"] == 0
    assert all(group["scoring_role"] == "market_observation" for group in dimension["evidence_groups"])


def test_placeholder_no_evidence_links_are_ignored():
    summary = {
        "date": "20260618",
        "sentiment_scores": {"锌": 0},
        "detailed_analysis": {"锌": {"original_sources": [{"row_id": "r1"}]}},
    }
    alignment = {
        "frameworks_used": {"锌": {"framework_id": "fw_futures_shfe_zn", "asset_id": "futures.SHFE.zn"}},
        "alignments": [
            {
                "factor_id": "f1",
                "asset": "锌",
                "source_field": "bullish_factors",
                "direction": "bullish",
                "text": "原文未提供锌的基本面利多证据。",
                "framework_node": {"node_id": "default::未归类", "label": "未归类"},
                "match": {"method": "default_dimension", "confidence": 0.2},
                "status": "pending_review",
            },
            {
                "factor_id": "f2",
                "asset": "锌",
                "source_field": "key_data",
                "direction": "neutral",
                "text": "LME期锌上涨，仅为价格表现，缺乏对应库存、持仓或现货升贴水信息。",
                "framework_node": {"node_id": "zn_inventory", "label": "锌/库存"},
                "match": {"method": "framework_dimension", "confidence": 0.8},
                "status": "auto_linked",
            },
        ],
    }

    bundle = build_logic_chain_bundle(summary, alignment)

    assert "锌" not in bundle["dimension_scores"]["assets"]
    assert "锌" not in bundle["trade_thesis"]["assets"]
    assert "锌" not in bundle["logic_chains"]["assets"]


def test_llm_impact_direction_overrides_neutral_source_field_for_scoring():
    summary = {
        "date": "20260618",
        "sentiment_scores": {"铜": 0},
        "detailed_analysis": {"铜": {"original_sources": [{"row_id": "r1"}]}},
    }
    alignment = {
        "frameworks_used": {"铜": {"framework_id": "fw_futures_shfe_cu", "asset_id": "futures.SHFE.cu"}},
        "alignments": [
            {
                "factor_id": "f1",
                "asset": "铜",
                "source_field": "supply_demand",
                "direction": "neutral",
                "llm_impact_direction": "bearish",
                "text": "国内消费指标环比有走弱的初步迹象",
                "framework_node": {"node_id": "cu_demand", "label": "沪铜/需求"},
                "match": {"method": "llm_framework_dimension", "confidence": 0.88},
                "status": "auto_linked",
            }
        ],
    }

    bundle = build_logic_chain_bundle(summary, alignment)
    scores = bundle["dimension_scores"]["assets"]["铜"]
    dimension = scores["dimensions"][0]

    assert scores["framework_score"] < 0
    assert dimension["direction_score"] < 0
    assert dimension["negative_factors"][0]["direction"] == "bearish"


def test_conflicting_dimension_evidence_is_dampened_and_chain_trigger_matches_net_direction():
    summary = {
        "date": "20260618",
        "sentiment_scores": {"纯苯": -3.8},
        "detailed_analysis": {"纯苯": {"original_sources": [{"row_id": "r1"}, {"row_id": "r2"}]}},
    }
    node = {"node_id": "bz_supply", "label": "纯苯/国内装置开工与检修"}
    common = {
        "asset": "纯苯",
        "framework_node": node,
        "match": {"method": "llm_framework_dimension", "confidence": 0.9},
        "status": "auto_linked",
    }
    alignment = {
        "frameworks_used": {"纯苯": {"framework_id": "FWK-纯苯-20260616", "asset_id": "candidate.纯苯"}},
        "alignments": [
            {
                **common,
                "factor_id": "bearish-1",
                "source_field": "key_data",
                "direction": "neutral",
                "llm_impact_direction": "bearish",
                "text": "纯苯开工率上升至70.36%，环比上升0.62%",
            },
            {
                **common,
                "factor_id": "bearish-2",
                "source_field": "bearish_factors",
                "direction": "bearish",
                "text": "加氢苯开工率环比+0.21%至63.97%",
            },
            {
                **common,
                "factor_id": "bullish-1",
                "source_field": "bullish_factors",
                "direction": "bullish",
                "text": "国内石油苯装置低开工持续，供需偏紧平衡持续",
            },
            {
                **common,
                "factor_id": "bullish-2",
                "source_field": "key_events",
                "direction": "event",
                "llm_impact_direction": "bullish",
                "text": "中金石化及海南炼化陆续开始检修",
            },
        ],
    }

    bundle = build_logic_chain_bundle(summary, alignment)
    asset_scores = bundle["dimension_scores"]["assets"]["纯苯"]
    dimension = asset_scores["dimensions"][0]
    chain = bundle["logic_chains"]["assets"]["纯苯"]["logic_chains"][0]

    assert abs(dimension["direction_score"]) < 6
    assert dimension["conflict_level"] in {"medium", "high"}
    assert dimension["directional_consensus"] < 0.6
    if dimension["direction_score"] > 0:
        assert "开工率上升" not in chain["logic_chain"][0]["text"]
        assert chain["counter_evidence"]


def test_trade_thesis_can_use_llm_for_smooth_mainline(monkeypatch):
    def fake_chat(prompt: str, *, max_tokens: int = 900, temperature: float = 0.2, timeout: int = 80) -> str:
        assert "不要机械拼接字段名" in prompt
        assert "结构化证据" in prompt
        return json.dumps(
            {
                "main_trade_thesis": "原油基本面主线偏空，核心在于霍尔木兹海峡恢复通航后，前期地缘风险溢价回吐，供应扰动预期明显降温。后续需要跟踪协议落地和航运恢复节奏。",
                "core_drivers": ["地缘风险溢价回吐"],
                "constraints": [],
                "tracking_points": ["协议落地和航运恢复节奏"],
                "quality_notes": [],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(logic_chain, "chat", fake_chat)
    summary = {
        "date": "20260618",
        "sentiment_scores": {"原油": -4},
        "detailed_analysis": {"原油": {"original_sources": [{"row_id": "r1"}]}},
    }
    alignment = {
        "frameworks_used": {"原油": {"framework_id": "fw_futures_ine_crude_oil", "asset_id": "futures.INE.crude_oil"}},
        "alignments": [
            {
                "factor_id": "f1",
                "asset": "原油",
                "source_field": "bearish_factors",
                "direction": "bearish",
                "text": "霍尔木兹海峡恢复通航，前期地缘风险溢价回吐",
                "framework_node": {"node_id": "geo", "label": "原油/地缘政治"},
                "match": {"method": "framework_dimension", "confidence": 0.86},
                "status": "auto_linked",
            }
        ],
    }

    bundle = logic_chain.build_logic_chain_bundle(summary, alignment, use_llm_for_thesis=True)
    thesis = bundle["trade_thesis"]["assets"]["原油"]

    assert thesis["thesis_generation"]["method"] == "llm"
    assert thesis["main_trade_thesis"].startswith("原油基本面主线偏空")


def test_trade_thesis_llm_parser_accepts_json_with_surrounding_text(monkeypatch):
    def fake_chat(prompt: str, *, max_tokens: int = 900, temperature: float = 0.2, timeout: int = 80) -> str:
        return (
            "下面是结果：\n"
            '{"main_trade_thesis":"铜基本面偏空，需求走弱是主线。","core_drivers":["需求走弱"],'
            '"constraints":["低价补货"],"tracking_points":["消费持续性"],"quality_notes":[]}\n'
            "供参考"
        )

    monkeypatch.setattr(logic_chain, "chat", fake_chat)
    summary = {
        "date": "20260618",
        "sentiment_scores": {"铜": -3},
        "detailed_analysis": {"铜": {"original_sources": [{"row_id": "r1"}]}},
    }
    alignment = {
        "frameworks_used": {"铜": {"framework_id": "fw_futures_shfe_cu", "asset_id": "futures.SHFE.cu"}},
        "alignments": [
            {
                "factor_id": "f1",
                "asset": "铜",
                "source_field": "bearish_factors",
                "direction": "bearish",
                "text": "国内消费指标环比有走弱的初步迹象",
                "framework_node": {"node_id": "demand", "label": "沪铜/需求"},
                "match": {"method": "framework_dimension", "confidence": 0.72},
                "status": "auto_linked",
            }
        ],
    }

    bundle = logic_chain.build_logic_chain_bundle(summary, alignment, use_llm_for_thesis=True)

    assert bundle["trade_thesis"]["assets"]["铜"]["main_trade_thesis"] == "铜基本面偏空，需求走弱是主线。"


def test_overlong_llm_trade_thesis_falls_back_to_compact_mainline(monkeypatch):
    def fake_chat(prompt: str, *, max_tokens: int = 900, temperature: float = 0.2, timeout: int = 80) -> str:
        repeated = (
            "石油沥青基本面主线小幅偏多。核心支撑来自国内炼厂开工与排产方面，"
            "维度：支撑因素：沥青厂开工率处六年同期最低位；支撑因素：排产下降；"
            "炼厂与社会总库存方面，维度：支撑因素：厂库与社会库存双双去化；"
            "燃料油进口与消费税监管方面，支撑因素：华南库存偏低；"
            "主要约束是俄乌与中东局势方面，压制因素：原油成本端支撑下滑；"
            "后续重点跟踪国内炼厂开工与排产方面，2026年5月沥青总排产量127.9万吨；"
            "炼厂与社会总库存方面，54家厂库库存77.8万吨；104家社会库120.5万吨。"
        ) * 2
        return json.dumps(
            {
                "main_trade_thesis": repeated,
                "core_drivers": ["低开工和排产下降"],
                "constraints": ["成本端支撑回落"],
                "tracking_points": ["开工和库存"],
                "quality_notes": [],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(logic_chain, "chat", fake_chat)
    summary = {
        "date": "20260618",
        "sentiment_scores": {"石油沥青": 2},
        "detailed_analysis": {"石油沥青": {"original_sources": [{"row_id": "r1"}]}},
    }
    alignment = {
        "frameworks_used": {"石油沥青": {"framework_id": "fw_bu", "asset_id": "futures.SHFE.bu"}},
        "alignments": [
            {
                "factor_id": "f1",
                "asset": "石油沥青",
                "source_field": "bullish_factors",
                "direction": "bullish",
                "text": "沥青厂开工率处六年同期最低位，截至6月16日开工率14.0%，现货资源偏紧",
                "framework_node": {"node_id": "supply", "label": "石油沥青/国内炼厂开工与排产"},
                "match": {"method": "framework_dimension", "confidence": 0.86},
                "status": "auto_linked",
            },
            {
                "factor_id": "f2",
                "asset": "石油沥青",
                "source_field": "bearish_factors",
                "direction": "bearish",
                "text": "美伊签署谅解备忘录，原油成本端支撑下滑，地缘溢价回落冲击估值",
                "framework_node": {"node_id": "geo", "label": "石油沥青/俄乌与中东局势"},
                "match": {"method": "framework_dimension", "confidence": 0.82},
                "status": "auto_linked",
            },
        ],
    }

    bundle = logic_chain.build_logic_chain_bundle(summary, alignment, use_llm_for_thesis=True)
    thesis = bundle["trade_thesis"]["assets"]["石油沥青"]

    assert len(thesis["main_trade_thesis"]) < 620
    assert "..." not in thesis["main_trade_thesis"]
    assert "维度：" not in thesis["main_trade_thesis"]
    assert "支撑因素：" not in thesis["main_trade_thesis"]
    assert "LLM主线过长" in "；".join(thesis["thesis_generation"]["quality_notes"])
