from __future__ import annotations

from datetime import datetime

from quanta_agents.opinion_radar.report import (
    build_market_radar_report,
    publish_market_radar_report,
    render_market_radar_markdown,
)


def _radar_payload():
    return {
        "schema_version": "opinion_radar.v1",
        "generated_at": "2026-06-18 10:00:00",
        "default_window": "24",
        "windows": {
            "24": {
                "window": {"hours": 24, "mode": "rolling"},
                "stats": {"flash_total": 20, "important_total": 4},
                "themes": [
                    {
                        "theme": "铜库存去化",
                        "summary": "交易所库存继续下降，需求韧性被讨论。",
                        "heat": 9.0,
                        "flash_count": 5,
                        "important_count": 1,
                        "varieties": ["铜"],
                        "top_flashes": [
                            {
                                "publish_time": "2026-06-18 09:00:00",
                                "text": "铜库存继续下降。",
                            }
                        ],
                    },
                    {
                        "theme": "原油地缘风险缓和",
                        "summary": "航运恢复压低风险溢价。",
                        "heat": 7.0,
                        "flash_count": 4,
                        "important_count": 1,
                        "varieties": ["原油"],
                        "top_flashes": [],
                    },
                ],
                "important_stream": [],
            }
        },
    }


def _news_logic_payload():
    return {
        "schema_version": "news_logic_radar.v1",
        "generated_at": "2026-06-18 10:01:00",
        "logic_context": {"run_dir": "/tmp/RUN-20260618-RAW-DAILY"},
        "assets": {
            "铜": {
                "event_count": 3,
                "important_count": 1,
                "heat": 8.5,
                "news_direction_score": 0.8,
                "consistency_counts": {"supports_thesis": 3},
                "linked_trade_thesis": {
                    "framework_score": 4.2,
                    "recommendation": "偏多观察",
                    "main_trade_thesis": "铜库存去化和需求韧性支撑偏多主线。",
                },
                "llm_assessment": {
                    "method": "llm",
                    "relation_to_report": "supports",
                    "news_summary": "库存继续下降。",
                    "logic_impact": "新闻继续确认铜偏多主线。",
                    "updated_bias": "偏多",
                    "key_confirmations": ["库存下降"],
                    "key_conflicts": [],
                    "new_variables": [],
                    "tracking_points": ["下游补库"],
                    "quality_notes": [],
                },
                "dimensions": [
                    {
                        "dimension_label": "库存",
                        "event_count": 3,
                        "important_count": 1,
                        "heat": 8.5,
                        "news_direction_score": 0.8,
                        "consistency_counts": {"supports_thesis": 3},
                        "top_events": [
                            {
                                "event_id": "n1",
                                "publish_time": "2026-06-18 09:00:00",
                                "text": "铜库存继续下降。",
                                "heat": 2.0,
                                "direction_score": 1.0,
                                "consistency": {"status": "supports_thesis"},
                            }
                        ],
                    }
                ],
            },
            "原油": {
                "event_count": 2,
                "important_count": 1,
                "heat": 6.0,
                "news_direction_score": -1.0,
                "consistency_counts": {"thesis_conflict": 2},
                "linked_trade_thesis": {
                    "framework_score": 5.0,
                    "recommendation": "偏多观察",
                    "main_trade_thesis": "原油地缘风险偏紧支撑偏多主线。",
                },
                "llm_assessment": {
                    "method": "llm",
                    "relation_to_report": "conflicts",
                    "news_summary": "航运恢复，风险溢价回落。",
                    "logic_impact": "新闻削弱原油地缘偏多主线。",
                    "updated_bias": "中性",
                    "key_confirmations": [],
                    "key_conflicts": ["航运恢复"],
                    "new_variables": [],
                    "tracking_points": ["航运量"],
                    "quality_notes": [],
                },
                "dimensions": [
                    {
                        "dimension_label": "地缘政治",
                        "event_count": 2,
                        "important_count": 1,
                        "heat": 6.0,
                        "news_direction_score": -1.0,
                        "consistency_counts": {"thesis_conflict": 2},
                        "top_events": [
                            {
                                "event_id": "n2",
                                "publish_time": "2026-06-18 09:30:00",
                                "text": "霍尔木兹航运恢复，原油风险溢价回吐。",
                                "heat": 3.0,
                                "direction_score": -1.0,
                                "consistency": {"status": "thesis_conflict"},
                            }
                        ],
                    }
                ],
            },
        },
    }


def test_market_radar_report_maps_news_logic_into_investment_updates():
    report = build_market_radar_report(
        _radar_payload(),
        _news_logic_payload(),
        generated_at="2026-06-18T10:02:00Z",
    )
    rows = {row["asset"]: row for row in report["logic_validation"]["assets"]}

    assert report["schema_version"] == "market_sentiment_radar_report.v1"
    assert report["market_pulse"]["top_themes"][0]["title"] == "铜库存去化"
    assert rows["铜"]["revision_action"] == "reinforce_thesis"
    assert rows["铜"]["verification_status"] == "validated"
    assert rows["原油"]["revision_action"] == "revise_or_downgrade_thesis"
    assert rows["原油"]["verification_status"] == "conflict"
    assert report["alerts"][0]["asset"] == "原油"

    markdown = render_market_radar_markdown(report)
    assert "研报主线验证" in markdown
    assert "原油" in markdown
    assert "修正/降级主线" in markdown


def test_rule_report_uses_news_evidence_instead_of_stale_report_thesis():
    payload = {
        "schema_version": "news_logic_radar.v1",
        "generated_at": "2026-06-19 04:00:00",
        "logic_context": {"run_dir": "/tmp/RUN-20260616-RAW-DAILY"},
        "assets": {
            "黄金": {
                "event_count": 3,
                "important_count": 2,
                "heat": 9.0,
                "news_direction_score": -0.5,
                "consistency_counts": {"thesis_conflict": 1, "supports_thesis": 1},
                "linked_trade_thesis": {
                    "framework_score": 2.0,
                    "recommendation": "小幅偏多",
                    "main_trade_thesis": "黄金小幅偏多，需等待6月FOMC会议及后续通胀就业数据验证。",
                },
                "dimensions": [
                    {
                        "dimension_label": "美联储降息路径与人事",
                        "event_count": 2,
                        "important_count": 2,
                        "heat": 5.0,
                        "news_direction_score": -1.0,
                        "consistency_counts": {"thesis_conflict": 1},
                        "top_events": [
                            {
                                "event_id": "g1",
                                "publish_time": "2026-06-18 20:30:00",
                                "text": "美联储点阵图显示明显鹰派倾向，市场重新定价加息路径。",
                                "heat": 3.0,
                                "direction_score": -1.0,
                                "consistency": {"status": "thesis_conflict"},
                            }
                        ],
                    },
                    {
                        "dimension_label": "央行购金",
                        "event_count": 1,
                        "important_count": 0,
                        "heat": 2.0,
                        "news_direction_score": 1.0,
                        "consistency_counts": {"supports_thesis": 1},
                        "top_events": [
                            {
                                "event_id": "g2",
                                "publish_time": "2026-06-18 21:00:00",
                                "text": "世界黄金协会调查显示更多央行计划增持黄金储备。",
                                "heat": 2.0,
                                "direction_score": 1.0,
                                "consistency": {"status": "supports_thesis"},
                            }
                        ],
                    },
                ],
            }
        },
    }

    report = build_market_radar_report({}, payload, generated_at="2026-06-19T04:01:00Z")
    gold = report["logic_validation"]["assets"][0]

    assert gold["relation_to_report"] == "mixed"
    assert "需等待6月FOMC会议" not in gold["updated_logic"]
    assert "点阵图" in gold["updated_logic"]
    assert "主线降为条件性判断" in gold["updated_logic"]


def test_publish_market_radar_report_writes_json_and_markdown(tmp_path):
    result = publish_market_radar_report(
        tmp_path,
        radar_payload=_radar_payload(),
        news_logic_payload=_news_logic_payload(),
        now=datetime(2026, 6, 18, 10, 2, 3),
    )

    latest_json = (
        tmp_path
        / "agent_workspace/candidates/opinion_radar/reports/latest/market-radar-report.json"
    )
    latest_md = (
        tmp_path
        / "agent_workspace/candidates/opinion_radar/reports/latest/market-radar-report.md"
    )
    archive_json = (
        tmp_path
        / (
            "agent_workspace/candidates/opinion_radar/reports/2026/06/18/"
            "market-radar-report-100203.json"
        )
    )

    assert result["latest_json"] == str(latest_json)
    assert latest_json.exists()
    assert latest_md.exists()
    assert archive_json.exists()
    assert "市场舆情雷达报告" in latest_md.read_text(encoding="utf-8")
