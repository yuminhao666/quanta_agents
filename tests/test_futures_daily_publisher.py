from __future__ import annotations

import json

from quanta_agents.futures_daily.publisher import publish_report_paths


def test_publish_report_paths_writes_gj_chainplatform_contract(tmp_path):
    summary = {
        "title": "综合commodity分析报告",
        "org_name": "合并报告",
        "date": "20260605",
        "sentiment_scores": {"铜": 2.5, "原油": -1.5},
        "detailed_analysis": {
            "铜": {
                "commodity": "铜",
                "bullish_factors": ["矿端偏紧"],
                "bearish_factors": [],
                "key_data": ["库存下降"],
                "key_events": ["进口关税扰动"],
                "sentiment_score": 2.5,
            },
            "原油": {
                "commodity": "原油",
                "bullish_factors": [],
                "bearish_factors": ["风险溢价回落"],
                "key_data": [],
                "key_events": ["停火谈判"],
                "sentiment_score": -1.5,
            },
        },
        "source_report_hash": "demo",
        "analysis_date": "2026-06-05T09:00:00",
        "analyzer_type": "commodity",
    }
    market_review = {
        "market_events_summary": "地缘与关税扰动。",
        "market_logic_summary": "供给扰动与宏观压力并存。",
        "timestamp": "2026-06-05T09:10:00",
    }
    summary_path = tmp_path / "20260605_commodity_summary.json"
    review_path = tmp_path / "20260605_commodity_marketreview.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    review_path.write_text(json.dumps(market_review, ensure_ascii=False), encoding="utf-8")

    root = tmp_path / "quanta_data"
    result = publish_report_paths(summary_path=summary_path, market_review_path=review_path, root=root)

    assert result["date"] == "20260605"
    assert (
        root
        / "agent_workspace/candidates/market_brief/2026/06/05/"
        / "CAND-MBRIEF-20260605-COMMODITY/20260605_commodity_marketreview.json"
    ).exists()
    summary_out = (
        root
        / "agent_workspace/candidates/asset_analysis/2026/06/05/"
        / "CAND-ANALYSIS-20260605-COMMODITY/20260605_commodity_summary.json"
    )
    assert json.loads(summary_out.read_text(encoding="utf-8"))["sentiment_scores"]["铜"] == 2.5
    assert (root / "agent_workspace/review_packages/2026/06/05/RP-20260605-COMMODITY/preview.html").exists()
    manifest = json.loads(
        (root / "agent_workspace/review_packages/2026/06/05/RP-20260605-COMMODITY/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["status"] == "pending_review"
    assert manifest["candidate_ids"] == ["CAND-MBRIEF-20260605-COMMODITY", "CAND-ANALYSIS-20260605-COMMODITY"]
