from __future__ import annotations

from quanta_agents.futures_daily.evidence_framework import (
    apply_dimension_summaries_to_trade_theses,
    build_asset_dimension_summaries,
)


def test_asset_dimension_summary_keeps_multi_source_validation():
    payload = {
        "evidence_nodes": [
            {
                "report_id": "r1",
                "asset": "纯苯",
                "source_field": "bullish_factors",
                "text": "港口库存持续去库至9.5万吨，环比-5%",
                "canonical_text": "港口库存持续去库95万吨环比5",
                "influence_score": 0.8,
                "evidence_weight": 0.8,
                "impact_sign": 1,
                "framework_node": {"node_id": "benzene_inventory", "label": "纯苯/港口库存"},
                "mapping": {"confidence": 0.86},
                "term_match": {"term_type": "indicator", "name": "纯苯港口库存", "confidence": 0.9},
            },
            {
                "report_id": "r2",
                "asset": "纯苯",
                "source_field": "key_data",
                "text": "华东港口商业库存9.5万吨，连续多周去库",
                "canonical_text": "华东港口商业库存95万吨连续多周去库",
                "influence_score": 0.7,
                "evidence_weight": 0.55,
                "impact_sign": 1,
                "framework_node": {"node_id": "benzene_inventory", "label": "纯苯/港口库存"},
                "mapping": {"confidence": 0.8},
                "term_match": {"term_type": "indicator", "name": "纯苯港口库存", "confidence": 0.86},
            },
        ]
    }

    result = build_asset_dimension_summaries(payload)
    dim = result["assets"]["纯苯"]["dimensions"][0]

    assert dim["dimension_label"] == "港口库存"
    assert dim["direction"] == "bullish"
    assert dim["validation_status"] == "multi_source_confirmed"
    assert dim["source_report_count"] == 2
    assert dim["support_evidence"][0]["term_match"]["name"] == "纯苯港口库存"


def test_enhanced_trade_thesis_downgrades_source_framework_conflict():
    trade_theses = {
        "assets": {
            "玉米淀粉": {
                "asset": "玉米淀粉",
                "framework_score": 4.5,
                "decision_score": 4.5,
                "source_sentiment_score": -2,
                "recommendation": "偏多观察",
                "main_trade_thesis": "旧主线。",
            }
        }
    }
    dimension_summaries = {
        "assets": {
            "玉米淀粉": {
                "framework_evidence_score": 4.5,
                "dimension_count": 1,
                "evidence_count": 2,
                "dimensions": [
                    {
                        "dimension_label": "原料成本传导",
                        "direction": "bullish",
                        "weighted_score": 4.5,
                        "dimension_summary": "原料成本传导维度形成支撑",
                        "support_evidence": [{"text": "玉米原料端北方港口库存环比下降27万吨"}],
                        "pressure_evidence": [],
                        "validation_status": "single_source",
                    }
                ],
            }
        }
    }

    result = apply_dimension_summaries_to_trade_theses(trade_theses, dimension_summaries)
    thesis = result["assets"]["玉米淀粉"]

    assert thesis["recommendation"] == "中性/等待确认"
    assert thesis["decision_score"] < 0.8
    assert "方向相反" in thesis["main_trade_thesis"]
    assert thesis["legacy_main_trade_thesis"] == "旧主线。"
