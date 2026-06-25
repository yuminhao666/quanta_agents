from __future__ import annotations

import json

from quanta_agents.core.io import read_json, write_json
from quanta_agents.core.taxonomy import load_asset_taxonomy
from quanta_agents.research_reports.single_report_store import (
    build_single_report_profile,
    run_wechat_single_report_store,
)
from quanta_agents.research_reports.wechat_evidence import load_wechat_articles


def _write_taxonomy(root):
    path = root / "gold/reference_data/assets/futures_assets.v1.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-SI",
                        "canonical_name": "工业硅",
                        "commodity_code": "SI",
                        "category": "有色金属",
                        "aliases": ["工业硅", "SI"],
                    },
                    {
                        "asset_id": "FUT-LC",
                        "canonical_name": "碳酸锂",
                        "commodity_code": "LC",
                        "category": "有色金属",
                        "aliases": ["碳酸锂", "LC"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_raw_article(root):
    raw_dir = root / "raw_objects/web_pages/hzzhqx_wechat/测试期货/2026/04/20/RAW-HZZHQX-WECHAT-PROFILE"
    raw_dir.mkdir(parents=True)
    write_json(
        raw_dir / "raw_manifest.json",
        {
            "raw_id": "RAW-HZZHQX-WECHAT-PROFILE",
            "title": "测试有色及新能源策略早餐",
            "source_account": "测试期货",
            "published_at": "2026-04-20 08:03:00",
            "source_url": "https://example.com/wechat/profile",
        },
    )
    (raw_dir / "extracted_text.txt").write_text(
        "\n".join(
            [
                "品种：工业硅",
                "日内观点：低位波动，中期观点：偏弱运行，运行区间：8400-8800。",
                "供应方面，3月份工业硅产量在33万吨附近，环比增加19.7%。",
                "需求方面，多晶硅受高库存及需求疲软影响，4月排产环比下降8.09%。",
                "基本面偏空，压制工业硅价格。",
                "品种：碳酸锂",
                "日内观点：偏强运行，运行区间：17万-18万。",
                "库存方面，碳酸锂总库存为64795吨，环比增加6442吨，但依然处于低位。",
                "宏观氛围有所缓和，提振锂价。",
            ]
        ),
        encoding="utf-8",
    )


def test_build_single_report_profile_keeps_old_commodity_report_fields(tmp_path):
    _write_taxonomy(tmp_path)
    _write_raw_article(tmp_path)
    taxonomy = load_asset_taxonomy(tmp_path, required=True)
    article = load_wechat_articles(tmp_path, date="20260420")[0]

    profile = build_single_report_profile(article, taxonomy=taxonomy, root=tmp_path)

    assert profile["schema_version"] == "wechat_single_report_profile.v1"
    assert profile["status"] == "structured"
    assert profile["sentiment_scores"]["工业硅"] < 0
    assert profile["detailed_analysis"]["工业硅"]["commodity"] == "工业硅"
    assert profile["detailed_analysis"]["工业硅"]["bearish_factors"]
    assert profile["detailed_analysis"]["工业硅"]["key_data"]
    assert profile["detailed_analysis"]["工业硅"]["supply_demand"]
    assert profile["detailed_analysis"]["工业硅"]["price_forecast"]
    assert profile["detailed_analysis"]["碳酸锂"]["bullish_factors"]


def test_run_wechat_single_report_store_writes_and_reuses_profiles(tmp_path):
    _write_taxonomy(tmp_path)
    _write_raw_article(tmp_path)

    first = run_wechat_single_report_store(
        tmp_path,
        start_date="20260420",
        end_date="20260420",
    )
    second = run_wechat_single_report_store(
        tmp_path,
        start_date="20260420",
        end_date="20260420",
    )

    assert first["status"] == "succeeded"
    assert first["stats"]["profile_created_count"] == 1
    assert second["stats"]["profile_reused_count"] == 1

    profile_path = tmp_path / first["profile_refs"][0]["path"]
    profile = read_json(profile_path)
    assert profile["metadata"]["raw_id"] == "RAW-HZZHQX-WECHAT-PROFILE"
    assert profile["lineage"]["input_refs"]

    index_path = tmp_path / first["paths"]["profile_index"]
    index = read_json(index_path)
    assert index["stats"]["profile_count"] == 1
    assert index["asset_summary"]


def test_run_wechat_single_report_store_writes_legacy_candidate_cache(tmp_path):
    _write_taxonomy(tmp_path)
    _write_raw_article(tmp_path)

    result = run_wechat_single_report_store(
        tmp_path,
        start_date="20260420",
        end_date="20260420",
        write_legacy_candidate_cache=True,
        legacy_run_id="WECHAT-PROFILE-TEST",
    )

    assert result["stats"]["legacy_candidate_cache_count"] == 1
    legacy_path = tmp_path / result["legacy_candidate_cache_refs"][0]["path"]
    legacy = read_json(legacy_path)
    assert legacy_path.parts[-4:] == (
        "WECHAT-PROFILE-TEST",
        "per_report",
        "测试期货",
        legacy_path.name,
    )
    assert legacy["analyzer_type"] == "commodity"
    assert legacy["org_name"] == "测试期货"
    assert legacy["date"] == "20260420"
    assert legacy["sentiment_scores"]["工业硅"] < 0
    assert legacy["detailed_analysis"]["工业硅"]["item"] == "工业硅"
    assert legacy["detailed_analysis"]["工业硅"]["commodity"] == "工业硅"
    assert legacy["detailed_analysis"]["工业硅"]["source_report_hash"] == legacy["source_report_hash"]
    assert legacy["_metadata"]["cache_status"] == "profile_export"

    day_index = read_json(
        tmp_path
        / "agent_workspace/candidates/futures_daily_single_report_analysis/2026/04/20/WECHAT-PROFILE-TEST/profile_index.json"
    )
    assert day_index["profile_count"] == 1
