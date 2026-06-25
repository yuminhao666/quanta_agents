from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from quanta_agents.core.io import read_json, write_json
from quanta_agents.news_brief import hourly


def _prepare_crude_context(root: Path, monkeypatch) -> Path:
    taxonomy_path = root / "taxonomy.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-SC",
                        "canonical_name": "原油",
                        "commodity_code": "SC",
                        "aliases": ["原油", "霍尔木兹", "OPEC"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    framework_dir = root / "data_lake/active_knowledge/research_frameworks/commodities"
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
    run_dir = root / "agent_workspace/candidates/futures_daily_raw_runs/2026/06/21/RUN-TEST-RAW-DAILY"
    write_json(
        run_dir / "trade_thesis.json",
        {"assets": {"原油": {"framework_score": -6.0, "main_trade_thesis": "原油地缘风险溢价回吐，偏空。"}}},
    )
    write_json(
        run_dir / "dimension_scores.json",
        {"assets": {"原油": {"dimensions": [{"dimension_label": "地缘政治", "direction_score": -8.0}]}}},
    )
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    return run_dir


def _flashes() -> list[dict[str, object]]:
    return [
        {
            "flash_id": "flash-oil-1",
            "publish_time": "2026-06-21 09:20:00",
            "important": 1,
            "channel": "news",
            "title": "霍尔木兹海峡航运恢复，原油风险溢价继续回吐",
            "content": "中东局势缓和，原油供应担忧下降。",
            "url": "https://example.test/news/1",
        }
    ]


def test_hourly_news_brief_uses_llm_with_source_refs(tmp_path, monkeypatch):
    run_dir = _prepare_crude_context(tmp_path, monkeypatch)

    def fake_chat(prompt: str, **_: object) -> str:
        assert "source_refs" in prompt
        assert "flash-oil-1" in prompt
        assert "过滤行情类资讯" in prompt
        assert "现实重要事件" in prompt
        assert "asset_notes 是可选栏目" in prompt
        return json.dumps(
            {
                "overview": [
                    {
                        "text": "过去一小时，原油相关快讯集中在霍尔木兹通航恢复。",
                        "source_refs": [{"ref_type": "flash", "id": "flash-oil-1"}],
                    }
                ],
                "key_news_paragraph": {
                    "text": "过去一小时，原油相关新闻集中在霍尔木兹通航恢复，地缘风险溢价叙事降温。",
                    "source_refs": [{"ref_type": "flash", "id": "flash-oil-1"}],
                },
                "key_news": [
                    {
                        "text": "霍尔木兹航运恢复削弱地缘风险溢价叙事。",
                        "source_refs": [{"ref_type": "event", "id": "NLOGIC-PLACEHOLDER"}],
                    }
                ],
                "asset_notes": [],
                "watch_items": [
                    {
                        "text": "继续跟踪航运恢复是否反复。",
                        "source_refs": ["flash-oil-1"],
                    }
                ],
                "graph_notes": [],
                "quality_notes": ["仅基于本小时快讯窗口。"],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(hourly, "chat", fake_chat)
    payload = hourly.build_hourly_news_brief(
        _flashes(),
        tmp_path,
        start_time=datetime(2026, 6, 21, 9, 0, 0),
        end_time=datetime(2026, 6, 21, 10, 0, 0),
        logic_run=run_dir,
    )

    event_id = payload["graph_trigger_candidates"][0]["event_id"]
    assert event_id
    assert payload["llm_brief"]["method"] == "llm"
    assert payload["llm_brief"]["overview"][0]["source_refs"] == [
        {"ref_type": "flash", "id": "flash-oil-1"}
    ]
    assert payload["llm_brief"]["key_news_paragraph"]["text"].startswith("过去一小时")
    assert payload["llm_brief"]["discarded_uncited_items"][0]["reason"] == "missing_or_invalid_source_refs"

    markdown = hourly.render_hourly_news_brief_markdown(payload)
    assert "重点新闻" in markdown
    assert "事件要点" in markdown
    assert "快讯:flash-oil-1" in markdown
    assert "NLOGIC-PLACEHOLDER" not in markdown


def test_publish_hourly_news_brief_writes_traceable_candidate(tmp_path, monkeypatch):
    run_dir = _prepare_crude_context(tmp_path, monkeypatch)

    def fake_chat(prompt: str, **_: object) -> str:
        assert "flash-oil-1" in prompt
        return json.dumps(
            {
                "overview": [
                    {
                        "text": "原油新闻窗口有一条重要地缘相关快讯。",
                        "source_refs": [{"ref_type": "flash", "id": "flash-oil-1"}],
                    }
                ],
                "key_news_paragraph": {
                    "text": "原油窗口的重点新闻是霍尔木兹通航恢复。",
                    "source_refs": [{"ref_type": "flash", "id": "flash-oil-1"}],
                },
                "key_news": [],
                "asset_notes": [
                    {
                        "asset": "原油",
                        "text": "快讯被映射到原油地缘政治维度。",
                        "source_refs": [{"ref_type": "flash", "id": "flash-oil-1"}],
                    }
                ],
                "watch_items": [],
                "graph_notes": [],
                "quality_notes": [],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(hourly, "chat", fake_chat)
    result = hourly.publish_hourly_news_brief(
        tmp_path,
        end_time=datetime(2026, 6, 21, 10, 0, 0),
        logic_run=run_dir,
        flashes=_flashes(),
    )

    assert result["status"] == "succeeded"
    latest_json = Path(result["paths"]["latest_brief_json"])
    latest_md = Path(result["paths"]["latest_brief_markdown"])
    manifest = read_json(Path(result["paths"]["candidate_manifest"]))
    run_manifest = read_json(Path(result["paths"]["run_manifest"]))
    assert latest_json.exists()
    assert latest_md.exists()
    assert manifest["schema_version"] == "candidate_manifest.v1"
    assert manifest["candidate_type"] == "opinion_radar"
    assert manifest["evidence_refs"][0]["path"].endswith("raw_flashes.json")
    assert manifest["generated_by"]["model"] != "rule_fallback"
    assert run_manifest["schema_version"] == "agent_run_manifest.v1"
    assert run_manifest["model_refs"][0]["parameters"]["task"] == "hourly_news_brief_narrative"


def test_publish_half_day_news_brief_uses_half_day_latest_paths(tmp_path, monkeypatch):
    run_dir = _prepare_crude_context(tmp_path, monkeypatch)

    def fake_chat(prompt: str, **_: object) -> str:
        assert "过去半日" in prompt
        return json.dumps(
            {
                "overview": [
                    {
                        "text": "过去半日原油新闻窗口有一条可追溯快讯。",
                        "source_refs": [{"ref_type": "flash", "id": "flash-oil-1"}],
                    }
                ],
                "key_news_paragraph": {
                    "text": "过去半日原油新闻窗口有一条可追溯快讯。",
                    "source_refs": [{"ref_type": "flash", "id": "flash-oil-1"}],
                },
                "key_news": [],
                "asset_notes": [],
                "watch_items": [],
                "graph_notes": [],
                "quality_notes": [],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(hourly, "chat", fake_chat)
    result = hourly.publish_hourly_news_brief(
        tmp_path,
        hours=12,
        period="half-day",
        end_time=datetime(2026, 6, 21, 10, 0, 0),
        logic_run=run_dir,
        flashes=_flashes(),
    )

    payload = read_json(Path(result["paths"]["latest_brief_json"]))
    manifest = read_json(Path(result["paths"]["candidate_manifest"]))
    run_manifest = read_json(Path(result["paths"]["run_manifest"]))
    assert result["run_id"].startswith("RUN-HALF-DAY-NEWS-BRIEF-")
    assert result["candidate_id"].startswith("CAND-NEWS-BRIEF-HALF-DAY-")
    assert "/news_brief/half_day/latest/half-day-news-brief.json" in result["paths"]["latest_brief_json"]
    assert payload["schema_version"] == "half_day_news_brief.v1"
    assert payload["period"]["slug"] == "half_day"
    assert "过去半日" in payload["title"]
    assert manifest["content_refs"][0]["ref_type"] == "half_day_news_brief_json"
    assert run_manifest["model_refs"][0]["parameters"]["task"] == "half_day_news_brief_narrative"


def test_half_day_brief_filters_market_updates_and_keeps_event_fallback(tmp_path, monkeypatch):
    run_dir = _prepare_crude_context(tmp_path, monkeypatch)

    flashes = [
        {
            "flash_id": "flash-price-1",
            "publish_time": "2026-06-25 01:51:18",
            "important": 1,
            "title": "现货白银日内暴跌7.00%，现报57.23美元/盎司",
            "content": "现货钯金日内大跌6.00%。",
        },
        {
            "flash_id": "flash-event-1",
            "publish_time": "2026-06-25 00:18:00",
            "important": 1,
            "title": "中方回应拘留2名日籍员工",
            "content": "涉及稀土相关产品出口违法问题。",
        },
        {
            "flash_id": "flash-oil-1",
            "publish_time": "2026-06-24 23:20:00",
            "important": 1,
            "title": "霍尔木兹海峡航运恢复，原油供应担忧下降",
            "content": "中东局势缓和，原油风险溢价回吐。",
        },
    ]

    def fake_chat(prompt: str, **_: object) -> str:
        assert "flash-price-1" not in prompt
        assert "flash-event-1" in prompt
        assert "important_event_candidates" in prompt
        return json.dumps(
            {
                "overview": [],
                "key_news": [],
                "asset_notes": [],
                "watch_items": [],
                "graph_notes": [],
                "quality_notes": [],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(hourly, "chat", fake_chat)
    payload = hourly.build_hourly_news_brief(
        flashes,
        tmp_path,
        start_time=datetime(2026, 6, 24, 15, 0, 0),
        end_time=datetime(2026, 6, 25, 3, 0, 0),
        period="half-day",
        logic_run=run_dir,
    )

    assert payload["stats"]["raw_flash_count"] == 3
    assert payload["stats"]["reportable_flash_count"] == 2
    assert payload["stats"]["filtered_market_update_count"] == 1
    assert all("现货白银" not in item["summary"] for item in payload["top_flashes"])
    assert payload["llm_brief"]["key_news_paragraph"]["text"]
    assert payload["llm_brief"]["key_news"]
    assert any("中方回应" in item["text"] or "霍尔木兹" in item["text"] for item in payload["llm_brief"]["key_news"])

    markdown = hourly.render_hourly_news_brief_markdown(payload)
    assert "重点新闻" in markdown
    assert "事件要点" in markdown
    assert "已从正文中过滤 1 条行情播报" in markdown
    assert "现货白银日内暴跌" not in markdown
    assert "结构化资产触发" not in markdown
