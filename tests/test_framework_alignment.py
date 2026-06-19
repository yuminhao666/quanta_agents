from __future__ import annotations

import json

from quanta_agents.futures_daily import framework_alignment
from quanta_agents.futures_daily.framework_alignment import build_framework_alignment


def test_build_framework_alignment_uses_taxonomy_and_clusters(monkeypatch, tmp_path):
    taxonomy_path = tmp_path / "taxonomy.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-CU",
                        "canonical_name": "铜",
                        "commodity_code": "CU",
                        "category": "有色金属",
                        "sector": "有色",
                        "aliases": ["铜", "沪铜"],
                    },
                    {
                        "asset_id": "FUT-AU",
                        "canonical_name": "黄金",
                        "commodity_code": "AU",
                        "category": "贵金属",
                        "sector": "贵金属",
                        "aliases": ["黄金", "沪金"],
                    },
                ],
                "macro_buckets": [
                    {"bucket_id": "usd_fx", "label": "美元与汇率", "keywords": ["美元", "美元指数"]},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    summary = {
        "date": "20260617",
        "detailed_analysis": {
            "铜": {
                "bullish_factors": ["矿端供应扰动支撑铜价"],
                "bearish_factors": ["美元走强压制有色金属"],
            },
            "黄金": {
                "bullish_factors": ["美元走弱提振黄金配置需求"],
                "bearish_factors": [],
            },
        },
    }

    result = build_framework_alignment(summary, tmp_path)

    assert result["alignments"]
    labels = {cluster["label"] for cluster in result["factor_clusters"]}
    assert "美元与汇率" in labels
    assert result["logic_timeline_events"]


def test_build_framework_alignment_uses_active_commodity_framework(monkeypatch, tmp_path):
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
                        "aliases": ["原油"],
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
                        "dimension_id": "futures_ine_crude_oil_geopolitics",
                        "dimension_name": "地缘政治",
                        "dimension_type": "geopolitics",
                        "typical_indicators": ["中东冲突风险", "海峡通行量"],
                        "typical_events": ["海峡封锁/通航恢复"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    summary = {
        "date": "20260617",
        "detailed_analysis": {
            "原油": {
                "bearish_factors": ["霍尔木兹海峡重新开放，地缘风险溢价回吐"],
            },
        },
    }

    result = build_framework_alignment(summary, tmp_path)

    assert result["frameworks_used"]["原油"]["framework_id"] == "fw_futures_ine_crude_oil"
    link = result["alignments"][0]
    assert link["framework_node"]["label"] == "原油/地缘政治"
    assert link["match"]["method"].startswith("framework")


def test_build_framework_alignment_uses_candidate_framework_named_by_commodity(monkeypatch, tmp_path):
    taxonomy_path = tmp_path / "taxonomy.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-RU",
                        "canonical_name": "天然橡胶",
                        "commodity_code": "RU",
                        "aliases": ["天然橡胶", "橡胶"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    framework_dir = tmp_path / "agent_workspace/candidates/frameworks/2026/06/16"
    framework_dir.mkdir(parents=True)
    (framework_dir / "FWK-天然橡胶-20260616.json").write_text(
        json.dumps(
            {
                "schema_version": "framework_candidate.v1",
                "candidate_id": "FWK-天然橡胶-20260616",
                "candidate_type": "commodity_framework",
                "commodity": "天然橡胶",
                "commodity_code": "天然橡胶",
                "status": "candidate",
                "tree": [
                    {
                        "node_id": "rubber_inventory",
                        "name": "库存",
                        "path": ["基本面", "库存"],
                        "kind": "leaf",
                        "indicators": [
                            {
                                "key": "青岛地区天胶保税和一般贸易库存",
                                "display": "青岛库存",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    summary = {
        "date": "20260617",
        "detailed_analysis": {
            "橡胶": {
                "bullish_factors": ["青岛地区天胶保税和一般贸易库存环比下降，贸易环节去库"],
            },
        },
    }

    result = build_framework_alignment(summary, tmp_path)

    assert result["frameworks_used"]["天然橡胶"]["framework_id"] == "FWK-天然橡胶-20260616"
    link = result["alignments"][0]
    assert link["framework_node"]["label"] == "基本面/库存"
    assert link["match"]["method"].startswith("framework")


def test_build_framework_alignment_can_refine_mapping_with_llm(monkeypatch, tmp_path):
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
                        "aliases": ["原油"],
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
                        "typical_indicators": ["地缘风险溢价"],
                    },
                    {
                        "dimension_id": "inventory",
                        "dimension_name": "库存",
                        "dimension_type": "inventory",
                        "typical_indicators": ["EIA库存"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_chat(prompt: str, *, max_tokens: int = 900, temperature: float = 0.2, timeout: int = 80) -> str:
        assert "关键词匹配只是候选" in prompt
        payload = json.loads(prompt[prompt.rfind('{"asset"') :])
        factor_id = payload["factors"][0]["factor_id"]
        return json.dumps(
            {
                "mappings": [
                    {
                        "factor_id": factor_id,
                        "node_id": "geo",
                        "confidence": 0.91,
                        "impact_direction": "bearish",
                        "reason": "航运恢复对应地缘风险溢价回吐。",
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    monkeypatch.setattr(framework_alignment, "chat", fake_chat)
    summary = {
        "date": "20260617",
        "detailed_analysis": {
            "原油": {
                "bearish_factors": ["霍尔木兹海峡航运恢复，原油风险溢价继续回吐"],
            },
        },
    }

    result = build_framework_alignment(summary, tmp_path, use_llm_refinement=True)
    link = result["alignments"][0]

    assert result["mapping_method"] == "rule_candidates_plus_llm_refinement"
    assert link["framework_node"]["node_id"] == "geo"
    assert link["match"]["method"] == "llm_framework_dimension"
    assert link["llm_impact_direction"] == "bearish"
