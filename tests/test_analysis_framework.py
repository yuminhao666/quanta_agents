from __future__ import annotations

import json

from quanta_agents.core.analysis_framework import build_analysis_framework_registry, normalize_dimension_type
from quanta_agents.core.frameworks import iter_leaf_nodes, load_framework_for_asset


def test_build_analysis_framework_registry_separates_dimensions_and_terms(tmp_path, monkeypatch):
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
                "commodity": "天然橡胶",
                "commodity_code": "天然橡胶",
                "status": "candidate",
                "tree": [
                    {
                        "node_id": "rubber_inventory",
                        "name": "国内港口与保税区库存",
                        "path": ["基本面", "库存", "国内港口与保税区库存"],
                        "kind": "leaf",
                        "indicators": [
                            {
                                "key": "青岛地区天胶保税和一般贸易库存",
                                "display": "青岛天胶库存",
                                "variants": ["青岛保税区库存"],
                            }
                        ],
                        "events": [
                            {"text": "贸易环节去库"},
                        ],
                        "claims": [
                            {"statement": "库存持续去化通常对天然橡胶价格形成支撑。"},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))

    asset = {
        "asset_id": "FUT-RU",
        "canonical_name": "天然橡胶",
        "commodity_code": "RU",
        "aliases": ["天然橡胶", "橡胶"],
    }
    framework = load_framework_for_asset(asset, tmp_path)
    leaves = iter_leaf_nodes(framework or {})
    assert leaves[0]["typical_indicators"] == ["青岛地区天胶保税和一般贸易库存", "青岛天胶库存", "青岛保税区库存"]
    assert leaves[0]["typical_events"] == ["贸易环节去库"]

    bundle = build_analysis_framework_registry(tmp_path)
    registry = bundle["registry"]
    catalog = bundle["catalog"]

    assert registry["stats"]["asset_count"] == 1
    assert registry["stats"]["asset_with_framework_count"] == 1
    assert registry["stats"]["dimension_count"] == 1
    assert registry["dimensions"][0]["dimension_type"] == "inventory"
    assert registry["dimensions"][0]["term_counts"] == {"indicator": 3, "event": 1, "claim": 1}
    assert catalog["stats"]["term_type_counts"] == {"indicator": 3, "event": 1, "claim": 1}
    assert {term["term_type"] for term in catalog["terms"]} == {"indicator", "event", "claim"}


def test_normalize_dimension_type_keeps_commodity_specific_categories():
    assert normalize_dimension_type("seasonal") == "seasonality"
    assert normalize_dimension_type("disease") == "disease"
    assert normalize_dimension_type("capacity_cycle") == "capacity_cycle"
