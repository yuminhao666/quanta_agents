from __future__ import annotations

import json
from pathlib import Path

from quanta_agents.core.io import read_json, write_json
from quanta_agents.signal_mapping import theme_report
from quanta_agents.signal_mapping.theme_report import (
    _is_low_signal_market_notice,
    _is_price_or_technical_text,
    _theme_rule,
    run_theme_report_maintenance,
)


def _write_taxonomy(root: Path) -> None:
    path = root / "gold/reference_data/assets/futures_assets.v1.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-SC",
                        "canonical_name": "原油",
                        "commodity_code": "SC",
                        "aliases": ["原油", "SC", "霍尔木兹"],
                    },
                    {"asset_id": "FUT-AU", "canonical_name": "黄金", "commodity_code": "AU", "aliases": ["黄金", "沪金"]},
                    {"asset_id": "FUT-AG", "canonical_name": "白银", "commodity_code": "AG", "aliases": ["白银", "沪银"]},
                ],
                "macro_buckets": [
                    {"bucket_id": "geopolitics", "label": "地缘政治", "keywords": ["地缘", "伊朗", "霍尔木兹"]}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_schema(root: Path) -> None:
    schema_dir = root / "configs/schemas"
    schema_dir.mkdir(parents=True)
    permissive = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}
    write_json(schema_dir / "research_signal.v1.schema.json", permissive)
    write_json(schema_dir / "theme_anchor.v1.schema.json", permissive)


def _write_per_report(root: Path, date: str, raw_id: str, text: str) -> None:
    yyyy, mm, dd = date[:4], date[4:6], date[6:8]
    path = (
        root
        / "agent_workspace/candidates/futures_daily_single_report_analysis"
        / yyyy
        / mm
        / dd
        / "WECHAT-PROFILE-V1/per_report/测试期货"
        / f"{date}_测试期货_{raw_id}_原油日报.json"
    )
    write_json(
        path,
        {
            "title": "原油日报",
            "org_name": "测试期货",
            "date": date,
            "sentiment_scores": {"原油": -3},
            "detailed_analysis": {
                "原油": {
                    "item": "原油",
                    "commodity": "原油",
                    "bearish_factors": [text],
                    "bullish_factors": [],
                    "key_data": ["美国原油库存下降200万桶。"],
                    "key_events": ["霍尔木兹海峡通航恢复。"],
                    "supply_demand": ["供应担忧下降，浮仓库存释放。"],
                    "price_forecast": ["短期弱势震荡。"],
                    "sentiment_score": -3,
                    "evidence_count": 4,
                }
            },
            "source_report_hash": raw_id,
            "analysis_date": "2026-04-18T00:00:00",
            "analyzer_type": "commodity",
            "_metadata": {"row_id": raw_id, "raw": {"source_url": "https://example.com"}},
        },
    )


def test_theme_report_maintenance_merges_per_report_themes_incrementally(tmp_path):
    _write_taxonomy(tmp_path)
    _write_schema(tmp_path)
    _write_per_report(tmp_path, "20260418", "RAW-1", "霍尔木兹海峡通航恢复，地缘风险溢价回吐。")
    _write_per_report(tmp_path, "20260419", "RAW-2", "霍尔木兹海峡通航恢复，原油供应担忧下降。")

    result = run_theme_report_maintenance(
        tmp_path,
        start_date="20260418",
        end_date="20260419",
        include_news=False,
        write_outputs=True,
    )

    assert result["stats"]["signal_count"] >= 2
    assert result["stats"]["theme_count"] >= 1
    top = result["theme_report"]["top_themes"][0]
    assert "原油" in top["title"]
    assert top["first_seen_at"] == "2026-04-18"
    assert top["last_seen_at"] == "2026-04-19"
    report_path = tmp_path / result["paths"]["theme_report_json"]
    assert read_json(report_path)["top_themes"]


def test_theme_report_maintenance_adds_grouped_news(tmp_path, monkeypatch):
    _write_taxonomy(tmp_path)
    _write_schema(tmp_path)
    _write_per_report(tmp_path, "20260418", "RAW-1", "霍尔木兹海峡通航恢复，地缘风险溢价回吐。")

    def fake_fetch(start, end):
        return [
            {
                "id": 1,
                "flash_id": "NEWS-1",
                "publish_time": "2026-04-18 10:00:00",
                "important": 1,
                "title": "",
                "content": "伊朗表示霍尔木兹海峡通航存在反复风险，原油市场关注地缘冲突。",
                "url": "https://example.com/news",
            }
        ]

    monkeypatch.setattr(theme_report.db, "fetch_flashes", fake_fetch)

    result = run_theme_report_maintenance(
        tmp_path,
        start_date="20260418",
        end_date="20260418",
        include_news=True,
        write_outputs=False,
    )

    assert result["theme_report"]["coverage"]["daily_stats"][0]["raw_news_count"] == 1
    assert result["theme_report"]["coverage"]["daily_stats"][0]["news_group_count"] >= 1
    assert result["stats"]["event_definition_signals"] >= 1


def test_theme_report_noise_filters_and_macro_theme_rules():
    assert _is_low_signal_market_notice("国投白银LOF提示二级市场溢价风险并临时停牌。")
    assert _is_low_signal_market_notice("技术刘Pro：黄金交易热度集中，点击查看。")
    assert _is_price_or_technical_text("本周豆油 Y2609小幅下跌运行，目前处于颈线压制。")
    assert not _is_price_or_technical_text("国内豆油库存量较上周减少0.69万吨。")
    assert _theme_rule("中国央行连续第17个月增持黄金。") == ("safe_haven_gold", "避险需求与央行购金", "macro")
    assert _theme_rule("CFTC数据显示COMEX黄金投机者净多头头寸增加。") == (
        "positioning",
        "资金持仓与交易情绪",
        "fundamental_validation",
    )


def test_theme_report_llm_normalization_controls_keep_drop_and_title(tmp_path, monkeypatch):
    _write_taxonomy(tmp_path)
    _write_schema(tmp_path)

    def fake_fetch(start, end):
        return [
            {
                "id": 1,
                "flash_id": "NEWS-LOF",
                "important": 0,
                "title": "",
                "content": "国投白银LOF提示二级市场溢价风险并临时停牌。",
            },
            {
                "id": 2,
                "flash_id": "NEWS-GOLD",
                "important": 1,
                "title": "",
                "content": "中国央行连续第17个月增持黄金，黄金储备继续上升。",
            },
        ]

    def fake_chat(prompt, **kwargs):
        items = json.loads(prompt.split("候选主题：\n", 1)[1])
        normalized = []
        for item in items:
            text = " ".join(item.get("sample_texts") or [])
            if "LOF" in text:
                normalized.append(
                    {
                        "id": item["id"],
                        "action": "drop",
                        "canonical_title_zh": "",
                        "theme_type": "other",
                        "canonical_key": "低信号基金停牌通知",
                        "confidence": 0.91,
                        "reason": "基金停牌和溢价风险提示，不是商品主题主线。",
                    }
                )
            else:
                normalized.append(
                    {
                        "id": item["id"],
                        "action": "keep",
                        "canonical_title_zh": "黄金：央行购金与储备安全",
                        "theme_type": "macro",
                        "canonical_key": "黄金央行购金储备安全",
                        "summary_zh": "中国央行持续增持黄金，市场关注储备安全和避险需求。",
                        "confidence": 0.86,
                        "reason": "央行购金是可持续追踪的宏观主题。",
                    }
                )
        return json.dumps({"items": normalized}, ensure_ascii=False)

    monkeypatch.setattr(theme_report.db, "fetch_flashes", fake_fetch)
    monkeypatch.setattr(theme_report, "_llm_available", lambda provider: (True, "fake-llm"))
    monkeypatch.setattr(theme_report, "chat", fake_chat)

    result = run_theme_report_maintenance(
        tmp_path,
        start_date="20260418",
        end_date="20260418",
        include_news=True,
        use_llm_normalization=True,
        require_llm=True,
        write_outputs=True,
    )

    llm_stats = result["stats"]["llm_normalization"]
    assert llm_stats["enabled"] is True
    assert llm_stats["llm_dropped_signal_count"] >= 1
    assert any(item["title"] == "黄金：央行购金与储备安全" for item in result["theme_report"]["top_themes"])
    signal_set = read_json(tmp_path / result["paths"]["research_signals"])
    signal_notes = [signal.get("notes") for signal in signal_set["signals"]]
    assert "中国央行持续增持黄金，市场关注储备安全和避险需求。" in signal_notes
