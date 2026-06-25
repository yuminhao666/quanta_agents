from __future__ import annotations

import json
from datetime import datetime

import pytest

from quanta_agents.core.io import write_json
from quanta_agents.market_themes import state as market_theme_state
from quanta_agents.market_themes.state import (
    _concept_from_text,
    _summary_segments,
    build_market_theme_state,
    publish_market_theme_state,
)


def _seed_inputs(root):
    run_dir = (
        root
        / "agent_workspace"
        / "candidates"
        / "futures_daily_raw_runs"
        / "2026"
        / "06"
        / "21"
        / "RUN-TEST-DAILY"
    )
    write_json(
        run_dir / "manifest.json",
        {
            "status": "candidate",
            "generated_at": "2026-06-21T08:00:00",
            "outputs": {
                "summary": "20260621_commodity_summary.json",
                "market_review": "20260621_commodity_marketreview.json",
                "logic_chains": "logic_chains.json",
                "brief_thesis_anchor": "brief_thesis_anchor.json",
                "logic_summary": "logic_summary.json",
            },
        },
    )
    write_json(
        run_dir / "20260621_commodity_summary.json",
        {
            "date": "20260621",
            "detailed_analysis": {
                "原油": {
                    "key_events": [
                        "美伊瑞士谈判推进，但霍尔木兹海峡通行不确定仍扰动原油风险溢价。"
                    ]
                },
                "黄金": {
                    "key_events": ["美联储点阵图偏鹰，美元指数走强压制黄金估值。"]
                },
            },
        },
    )
    write_json(
        run_dir / "20260621_commodity_marketreview.json",
        {
            "market_events_summary": "1. 美伊瑞士谈判推进，但霍尔木兹海峡通行不确定仍扰动风险溢价。2. 美联储点阵图偏鹰，美元指数走强。",
            "market_logic_summary": "宏观与地缘共振偏空：美联储鹰派立场及中东局势缓和形成共性压力。库存累积与去库压力仍需跟踪。",
        },
    )
    write_json(
        run_dir / "brief_thesis_anchor.json",
        {
            "assets": {
                "原油": {
                    "anchor_id": "BRANCH-OIL",
                    "direction": "bullish",
                    "direction_label": "偏多",
                    "thesis_title": "原油地缘风险主线：美伊谈判与霍尔木兹通行不确定",
                }
            }
        },
    )
    write_json(
        run_dir / "logic_summary.json",
        {
            "important_events_summary": "美伊谈判和霍尔木兹通行是本日期市速递重要事件。",
            "important_logic_summary": "主线一：宏观与地缘共振偏空。主线二：库存累积与去库压力。",
        },
    )
    radar_path = root / "agent_workspace" / "candidates" / "opinion_radar" / "latest" / "radar.json"
    write_json(
        radar_path,
        {
            "generated_at": "2026-06-21T10:00:00",
            "default_window": "24",
            "windows": {
                "24": {
                    "themes": [
                        {
                            "key": "M::geopolitics",
                            "label": "地缘政治",
                            "kind": "macro",
                            "theme": "地缘政治",
                            "summary": "伊朗与美国在瑞士谈判，霍尔木兹通行仍待确认。",
                            "heat": 100,
                            "important_count": 3,
                            "top_flashes": [],
                        }
                    ],
                    "synthesis": [],
                }
            },
        },
    )
    brief_path = (
        root
        / "agent_workspace"
        / "candidates"
        / "news_brief"
        / "half_day"
        / "latest"
        / "half-day-news-brief.json"
    )
    write_json(
        brief_path,
        {
            "generated_at": "2026-06-21T12:00:00",
            "title": "半日舆情汇总",
            "llm_brief": {
                "key_news": [
                    {
                        "text": "美伊瑞士谈判进入关键阶段，霍尔木兹海峡通行量存在分歧。",
                        "source_refs": [{"ref_type": "flash", "id": "F1"}],
                    }
                ],
                "watch_items": [
                    {
                        "text": "继续观察美伊谈判结果及霍尔木兹通行恢复情况。",
                        "source_refs": [{"ref_type": "flash", "id": "F2"}],
                    }
                ],
            },
        },
    )
    return run_dir


def test_build_market_theme_state_defaults_to_futures_summary_only(tmp_path):
    run_dir = _seed_inputs(tmp_path)
    payload = build_market_theme_state(
        root=tmp_path,
        futures_run_dir=run_dir,
        date_key="20260621",
        use_llm=False,
        now=datetime(2026, 6, 21, 12, 0, 0),
    )

    assert payload["schema_version"] == "market_theme_state.v1"
    assert payload["themes"]
    top = payload["themes"][0]
    assert top["theme_id"] == "MKT-THEME-MACRO-US_IRAN_HORMUZ"
    assert set(top["evidence"]["source_type_counts"]) == {"futures_daily"}
    assert top["scope"] == "macro"
    assert top["asset_refs"] == []


def test_build_market_theme_state_can_optionally_link_three_sources(tmp_path):
    run_dir = _seed_inputs(tmp_path)
    payload = build_market_theme_state(
        root=tmp_path,
        futures_run_dir=run_dir,
        date_key="20260621",
        include_radar=True,
        include_half_day_news=True,
        use_llm=False,
        now=datetime(2026, 6, 21, 12, 0, 0),
    )

    top = payload["themes"][0]
    assert {"futures_daily", "opinion_radar", "half_day_news_brief"} <= set(
        top["evidence"]["source_type_counts"]
    )


def test_build_market_theme_state_uses_llm_for_theme_extraction(tmp_path, monkeypatch):
    run_dir = _seed_inputs(tmp_path)
    monkeypatch.setattr(market_theme_state, "_llm_available", lambda provider: (True, "fake-llm:model"))

    def fake_chat(prompt: str, **_: object) -> str:
        assert "market_theme_extraction_and_evidence_classification" in prompt
        start = prompt.index("```json") + len("```json")
        end = prompt.index("```", start)
        pack = json.loads(prompt[start:end])
        evidence = pack["evidence_candidates"]
        iran_ids = [
            item["evidence_id"]
            for item in evidence
            if "霍尔木兹" in item["summary"] or "美伊" in item["summary"]
        ]
        assert iran_ids
        return json.dumps(
            {
                "themes": [
                    {
                        "concept_id": "US_IRAN_HORMUZ",
                        "title": "美伊谈判与霍尔木兹通行不确定",
                        "driver": "geopolitics",
                        "scope": "macro",
                        "asset_refs": [],
                        "evidence_ids": [*iran_ids, "MISSING-EVIDENCE-ID"],
                        "confidence": 0.88,
                        "reason": "多条证据指向同一地缘通行主题。",
                    }
                ],
                "discarded_evidence": [],
                "quality_notes": [],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(market_theme_state, "chat", fake_chat)
    payload = build_market_theme_state(
        root=tmp_path,
        futures_run_dir=run_dir,
        date_key="20260621",
        require_llm=True,
        now=datetime(2026, 6, 21, 12, 0, 0),
    )

    extraction = payload["stats"]["theme_extraction"]
    assert extraction["method"] == "llm"
    assert extraction["status"] == "succeeded"
    assert extraction["invalid_ref_count"] == 1
    top = payload["themes"][0]
    assert top["theme_id"] == "MKT-THEME-MACRO-US_IRAN_HORMUZ"
    assert top["evidence"]["items"][0]["classification_method"] == "llm"
    assert top["evidence"]["items"][0]["llm_extraction"]["confidence"] == 0.88


def test_build_market_theme_state_can_require_llm(tmp_path, monkeypatch):
    run_dir = _seed_inputs(tmp_path)
    monkeypatch.setattr(market_theme_state, "_llm_available", lambda provider: (False, "missing key"))

    with pytest.raises(RuntimeError, match="required but unavailable"):
        build_market_theme_state(
            root=tmp_path,
            futures_run_dir=run_dir,
            date_key="20260621",
            require_llm=True,
            now=datetime(2026, 6, 21, 12, 0, 0),
        )


def test_publish_market_theme_state_writes_latest(tmp_path):
    run_dir = _seed_inputs(tmp_path)
    result = publish_market_theme_state(
        root=tmp_path,
        futures_run_dir=run_dir,
        date_key="20260621",
        use_llm=False,
        now=datetime(2026, 6, 21, 12, 0, 0),
    )

    assert result["payload"]["stats"]["theme_count"] > 0
    assert (tmp_path / "agent_workspace/candidates/market_themes/latest/market-theme-state.json").exists()
    assert (tmp_path / "agent_workspace/candidates/market_themes/latest/market-theme-state.md").exists()
    assert (tmp_path / "agent_workspace/runs/market_themes/2026/06/21/RUN-MARKET-THEME-20260621-120000/manifest.json").exists()


def test_concept_matching_uses_dominant_market_variable():
    assert _concept_from_text("OPEC+宣布7月继续增产18.8万桶/日，供应增长担忧强化")[0] == "OPEC_CRUDE_SUPPLY"
    assert _concept_from_text("铁矿石港口库存1.73亿吨创同期纪录、钢厂盈利率下行，承压明显")[0] == "INVENTORY_WAREHOUSE"
    assert _concept_from_text("澳大利亚气象局宣布强厄尔尼诺已触发，关注棕榈油、棉花、白糖产量扰动")[0] == "WEATHER_CROP_RISK"


def test_concept_matching_rejects_broad_keyword_false_positives():
    concept_id, _, _ = _concept_from_text("国内菜籽库存与到港：美豆因中国采购传闻止跌反弹，巴西大豆出口强劲正值到港高峰")
    assert concept_id != "INVENTORY_WAREHOUSE"

    concept_id, _, _ = _concept_from_text("白糖新旧榨季交替期，巴西压榨进度与印度出口政策博弈加剧")
    assert concept_id != "TRADE_TARIFF_POLICY"


def test_summary_segments_extract_common_factors_from_ranked_logic():
    segments = _summary_segments(
        "偏多主线主要在20号胶(+4.6，主因国内港口与保税区)，共性支撑来自关税与贸易、国内菜籽库存与到港、粗钢与铁水产量。"
    )

    assert "共性支撑来自关税与贸易" in segments
    assert all("20号胶" not in segment for segment in segments)
