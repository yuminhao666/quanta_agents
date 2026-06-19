from __future__ import annotations

import json

from quanta_agents.core.io import write_json
from quanta_agents.research_reports import wechat_evidence
from quanta_agents.research_reports.wechat_evidence import build_wechat_research_evidence


def _write_taxonomy(path):
    path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-SC",
                        "canonical_name": "原油",
                        "commodity_code": "SC",
                        "aliases": ["原油", "SC原油", "霍尔木兹"],
                    },
                    {
                        "asset_id": "FUT-RB",
                        "canonical_name": "螺纹钢",
                        "commodity_code": "RB",
                        "aliases": ["螺纹钢", "螺纹"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_frameworks(root):
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
                        "typical_events": ["海峡封锁/通航恢复", "停火"],
                    },
                    {
                        "dimension_id": "inventory",
                        "dimension_name": "库存",
                        "dimension_type": "inventory",
                        "typical_indicators": ["原油库存", "EIA库存"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (framework_dir / "futures.SHFE.rebar.json").write_text(
        json.dumps(
            {
                "schema_version": "research_framework.v1",
                "artifact_type": "research_framework",
                "framework_id": "fw_futures_shfe_rebar",
                "asset_id": "futures.SHFE.rebar",
                "standard_name": "螺纹钢",
                "generic_name": "螺纹钢",
                "status": "active",
                "core_dimensions": [
                    {
                        "dimension_id": "demand",
                        "dimension_name": "需求",
                        "dimension_type": "demand",
                        "typical_indicators": ["建材成交", "表需"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_article(root):
    article_dir = root / "raw_objects/web_pages/hzzhqx_wechat/测试期货/2026/06/18/RAW-HZZHQX-WECHAT-TEST"
    article_dir.mkdir(parents=True)
    write_json(
        article_dir / "raw_manifest.json",
        {
            "raw_id": "RAW-HZZHQX-WECHAT-TEST",
            "title": "测试期货日报",
            "source_account": "测试期货",
            "published_at": "2026-06-18 08:30:00",
            "source_url": "https://example.com/report",
        },
    )
    (article_dir / "extracted_text.txt").write_text(
        "\n".join(
            [
                "01",
                "原油",
                "霍尔木兹海峡通航恢复，地缘风险溢价回吐，原油供应担忧下降，短期油价承压。",
                "同时美国原油库存下降，为油价底部提供一定支撑。",
                "02",
                "螺纹钢",
                "螺纹钢需求继续走弱，建材成交清淡，淡季库存累积，基本面偏空。",
                "免责声明 本报告不构成投资建议。",
            ]
        ),
        encoding="utf-8",
    )


def _write_prior_run(root):
    run_dir = root / "agent_workspace/candidates/futures_daily_raw_runs/2026/06/16/RUN-TEST-RAW-DAILY"
    write_json(
        run_dir / "trade_thesis.json",
        {
            "assets": {
                "原油": {"decision_score": -4.0, "framework_score": -5.0, "main_trade_thesis": "原油地缘溢价回吐，偏空。"},
                "螺纹钢": {"decision_score": -3.0, "framework_score": -3.5, "main_trade_thesis": "螺纹钢需求偏弱。"},
            }
        },
    )
    write_json(
        run_dir / "dimension_scores.json",
        {
            "assets": {
                "原油": {"dimensions": [{"dimension_label": "地缘政治", "direction_score": -6.0}]},
                "螺纹钢": {"dimensions": [{"dimension_label": "需求", "direction_score": -5.0}]},
            }
        },
    )
    return run_dir


def test_build_wechat_research_evidence_maps_reports_to_framework(tmp_path, monkeypatch):
    taxonomy_path = tmp_path / "taxonomy.json"
    _write_taxonomy(taxonomy_path)
    _write_frameworks(tmp_path)
    _write_article(tmp_path)
    run_dir = _write_prior_run(tmp_path)
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))

    result = build_wechat_research_evidence(tmp_path, date="20260618", logic_run=run_dir)

    assert result["stats"]["article_count"] == 1
    assert result["stats"]["asset_count"] == 2
    assert result["assets"]["原油"]["dimensions"]
    assert result["assets"]["原油"]["research_conclusion"]["relation_to_prior"] in {"supports", "mixed"}
    assert result["assets"]["螺纹钢"]["research_conclusion"]["updated_bias"] in {"偏空", "小幅偏空"}
    assert all("text" in item for item in result["evidence"])


def test_wechat_research_evidence_can_use_llm_assessment(tmp_path, monkeypatch):
    taxonomy_path = tmp_path / "taxonomy.json"
    _write_taxonomy(taxonomy_path)
    _write_frameworks(tmp_path)
    _write_article(tmp_path)
    run_dir = _write_prior_run(tmp_path)
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    monkeypatch.setattr(wechat_evidence, "_llm_available", lambda provider: (True, ""))

    def fake_chat(prompt: str, *, max_tokens: int = 900, temperature: float = 0.2, timeout: int = 80, provider=None, env_prefix="QUANTA_AGENT_LLM"):
        assert "微信研报证据" in prompt
        return json.dumps(
            {
                "relation_to_prior": "supports",
                "updated_bias": "小幅偏空",
                "main_logic_update": "地缘溢价回吐继续验证原油偏空主线。",
                "score_adjustment": -0.5,
                "dimension_updates": [
                    {
                        "dimension_label": "地缘政治",
                        "status": "confirmed",
                        "importance_score": 8,
                        "net_direction": "bearish",
                        "merged_summary": "通航恢复削弱地缘溢价。",
                    }
                ],
                "new_framework_candidates": [],
                "tracking_points": ["库存变化"],
                "quality_notes": [],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(wechat_evidence, "chat", fake_chat)

    result = build_wechat_research_evidence(
        tmp_path,
        date="20260618",
        logic_run=run_dir,
        use_llm_assessment=True,
        llm_provider="m3",
        llm_asset_limit=1,
    )

    assessments = [
        payload.get("llm_assessment")
        for payload in result["assets"].values()
        if isinstance(payload.get("llm_assessment"), dict)
    ]
    assert any(item.get("method") == "llm" for item in assessments)
    assert result["stats"]["llm_assessment_count"] == 1
