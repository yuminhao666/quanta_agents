from __future__ import annotations

import json

from quanta_agents.core.io import write_json
from quanta_agents.opinion_radar import news_logic
from quanta_agents.opinion_radar.news_logic import build_news_logic_radar


def test_build_news_logic_radar_maps_flash_to_framework_and_thesis(tmp_path, monkeypatch):
    taxonomy_path = tmp_path / "taxonomy.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-SC",
                        "canonical_name": "原油",
                        "commodity_code": "SC",
                        "aliases": ["原油", "霍尔木兹"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    framework_dir = tmp_path / "data_lake/active_knowledge/research_frameworks/commodities"
    framework_dir.mkdir(parents=True)
    (framework_dir / "futures.INE.crude_oil.json").write_text(
        json.dumps(
            {
                "schema_version": "research_framework.v1",
                "artifact_type": "research_framework",
                "framework_id": "fw_futures_ine_crude_oil",
                "asset_id": "futures.INE.crude_oil",
                "standard_name": "原油",
                "generic_name": "原油",
                "status": "active",
                "core_dimensions": [
                    {
                        "dimension_id": "geo",
                        "dimension_name": "地缘政治",
                        "dimension_type": "geopolitics",
                        "typical_indicators": ["地缘风险溢价", "海峡通行量"],
                        "typical_events": ["海峡封锁/通航恢复"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    run_dir = tmp_path / "agent_workspace/candidates/futures_daily_raw_runs/2026/06/18/RUN-TEST-RAW-DAILY"
    write_json(
        run_dir / "trade_thesis.json",
        {
            "assets": {
                "原油": {
                    "framework_score": -6.0,
                    "main_trade_thesis": "原油地缘风险溢价回吐，偏空。",
                }
            }
        },
    )
    write_json(
        run_dir / "dimension_scores.json",
        {
            "assets": {
                "原油": {
                    "dimensions": [
                        {
                            "dimension_label": "地缘政治",
                            "direction_score": -8.0,
                        }
                    ]
                }
            }
        },
    )
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    flashes = [
        {
            "flash_id": "n1",
            "publish_time": "2026-06-18 09:00:00",
            "important": 1,
            "title": "霍尔木兹海峡航运恢复，原油风险溢价继续回吐",
            "content": "中东局势缓和，原油供应担忧下降。",
        }
    ]

    result = build_news_logic_radar(flashes, tmp_path, logic_run=run_dir, date_key="20260618")

    assert result["stats"]["mapped_event_count"] == 1
    event = result["events"][0]
    assert event["asset"] == "原油"
    assert event["framework_node"]["dimension_label"] == "地缘政治"
    assert event["consistency"]["status"] == "supports_thesis"


def test_publish_news_logic_rolling_window_uses_latest_available_logic_run(tmp_path, monkeypatch):
    taxonomy_path = tmp_path / "taxonomy.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-SC",
                        "canonical_name": "原油",
                        "commodity_code": "SC",
                        "aliases": ["原油", "霍尔木兹"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    framework_dir = tmp_path / "data_lake/active_knowledge/research_frameworks/commodities"
    framework_dir.mkdir(parents=True)
    (framework_dir / "futures.INE.crude_oil.json").write_text(
        json.dumps(
            {
                "schema_version": "research_framework.v1",
                "artifact_type": "research_framework",
                "framework_id": "fw_futures_ine_crude_oil",
                "asset_id": "futures.INE.crude_oil",
                "standard_name": "原油",
                "generic_name": "原油",
                "status": "active",
                "core_dimensions": [
                    {
                        "dimension_id": "geo",
                        "dimension_name": "地缘政治",
                        "dimension_type": "geopolitics",
                        "typical_indicators": ["地缘风险溢价", "海峡通行量"],
                        "typical_events": ["海峡封锁/通航恢复"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    run_dir = tmp_path / "agent_workspace/candidates/futures_daily_raw_runs/2026/06/16/RUN-TEST-RAW-DAILY"
    write_json(
        run_dir / "trade_thesis.json",
        {"assets": {"原油": {"framework_score": -6.0, "main_trade_thesis": "原油地缘风险溢价回吐，偏空。"}}},
    )
    write_json(
        run_dir / "dimension_scores.json",
        {"assets": {"原油": {"dimensions": [{"dimension_label": "地缘政治", "direction_score": -8.0}]}}},
    )
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    monkeypatch.setattr(
        news_logic,
        "fetch_flashes_for_window",
        lambda date=None, hours=24: [
            {
                "flash_id": "n1",
                "publish_time": "2026-06-18 09:00:00",
                "important": 1,
                "title": "霍尔木兹海峡航运恢复，原油风险溢价继续回吐",
                "content": "中东局势缓和，原油供应担忧下降。",
            }
        ],
    )

    result = news_logic.publish_news_logic_radar(tmp_path, date=None, hours=24, use_llm_assessment=False)

    assert result["payload"]["logic_context"]["run_dir"] == str(run_dir)
    assert result["payload"]["events"][0]["consistency"]["status"] == "supports_thesis"


def test_news_logic_can_use_llm_asset_assessment(tmp_path, monkeypatch):
    taxonomy_path = tmp_path / "taxonomy.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-AU",
                        "canonical_name": "黄金",
                        "commodity_code": "AU",
                        "aliases": ["黄金", "现货黄金"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    framework_dir = tmp_path / "data_lake/active_knowledge/research_frameworks/commodities"
    framework_dir.mkdir(parents=True)
    (framework_dir / "futures.SHFE.gold.json").write_text(
        json.dumps(
            {
                "schema_version": "research_framework.v1",
                "artifact_type": "research_framework",
                "framework_id": "fw_futures_shfe_gold",
                "asset_id": "futures.SHFE.gold",
                "standard_name": "黄金",
                "generic_name": "黄金",
                "status": "active",
                "core_dimensions": [
                    {
                        "dimension_id": "usd",
                        "dimension_name": "美元与汇率",
                        "dimension_type": "macro",
                        "typical_indicators": ["美元指数", "美债收益率"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    run_dir = tmp_path / "agent_workspace/candidates/futures_daily_raw_runs/2026/06/18/RUN-TEST-RAW-DAILY"
    write_json(
        run_dir / "trade_thesis.json",
        {"assets": {"黄金": {"framework_score": 4.0, "main_trade_thesis": "黄金受降息预期支撑，偏多。"}}},
    )
    write_json(
        run_dir / "dimension_scores.json",
        {"assets": {"黄金": {"dimensions": [{"dimension_label": "美元与汇率", "direction_score": 5.0}]}}},
    )

    def fake_chat(prompt: str, *, max_tokens: int = 900, temperature: float = 0.2, timeout: int = 80) -> str:
        assert "关键词方向和规则一致性只是参考" in prompt
        return json.dumps(
            {
                "relation_to_report": "supports",
                "news_summary": "美元走弱强化黄金降息交易。",
                "logic_impact": "确认研报偏多主线。",
                "updated_bias": "偏多",
                "key_confirmations": ["美元走弱"],
                "key_conflicts": [],
                "new_variables": [],
                "tracking_points": ["美债收益率"],
                "quality_notes": [],
            },
            ensure_ascii=False,
        )

    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    monkeypatch.setattr(news_logic, "chat", fake_chat)
    result = build_news_logic_radar(
        [
            {
                "flash_id": "g1",
                "publish_time": "2026-06-18 10:00:00",
                "important": 1,
                "title": "现货黄金上涨，美元指数走弱",
                "content": "市场交易美联储降息预期。",
            }
        ],
        tmp_path,
        logic_run=run_dir,
        use_llm_assessment=True,
    )

    assessment = result["assets"]["黄金"]["llm_assessment"]
    assert assessment["method"] == "llm"
    assert assessment["relation_to_report"] == "supports"
    assert result["stats"]["llm_assessment_count"] == 1
