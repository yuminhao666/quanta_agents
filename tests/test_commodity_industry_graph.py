from __future__ import annotations

import json
from pathlib import Path

from quanta_agents.commodity_industry_graph import (
    build_commodity_industry_graph,
    publish_commodity_industry_graph,
)
from quanta_agents.core.io import read_json


def _write_framework(path: Path, *, code: str, name: str, dimensions: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "research_framework.v1",
                "artifact_type": "research_framework",
                "framework_id": f"fw_{code.lower()}",
                "asset_id": f"futures.TEST.{code.lower()}",
                "standard_name": name,
                "generic_name": name,
                "commodity_code": code,
                "status": "candidate",
                "core_dimensions": dimensions,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_build_commodity_industry_graph_uses_framework_dimensions(tmp_path: Path, monkeypatch) -> None:
    taxonomy_path = tmp_path / "futures_assets.v1.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-CU",
                        "asset_class": "futures",
                        "canonical_name": "铜",
                        "commodity_code": "CU",
                        "sector": "有色",
                        "category": "有色金属",
                        "aliases": ["沪铜"],
                    },
                    {
                        "asset_id": "FUT-AO",
                        "asset_class": "futures",
                        "canonical_name": "氧化铝",
                        "commodity_code": "AO",
                        "sector": "有色",
                        "category": "有色金属",
                    },
                    {
                        "asset_id": "FUT-AL",
                        "asset_class": "futures",
                        "canonical_name": "铝",
                        "commodity_code": "AL",
                        "sector": "有色",
                        "category": "有色金属",
                    },
                    {
                        "asset_id": "FUT-IF",
                        "asset_class": "futures",
                        "canonical_name": "沪深300股指",
                        "commodity_code": "IF",
                        "sector": "金融期货",
                        "category": "金融期货",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    framework_dir = tmp_path / "agent_workspace/candidates/frameworks/2026/06/16"
    framework_dir.mkdir(parents=True)
    _write_framework(
        framework_dir / "FWK-CU-20260616.json",
        code="CU",
        name="铜",
        dimensions=[
            {
                "dimension_id": "cu_supply",
                "dimension_name": "供给",
                "dimension_type": "supply",
                "typical_indicators": ["矿山产出"],
                "typical_events": ["矿山减产"],
            },
            {
                "dimension_id": "cu_demand",
                "dimension_name": "需求",
                "dimension_type": "demand",
                "typical_indicators": ["电网订单"],
                "typical_events": ["下游补库"],
            },
        ],
    )
    _write_framework(
        framework_dir / "FWK-AO-20260616.json",
        code="AO",
        name="氧化铝",
        dimensions=[
            {
                "dimension_id": "ao_cost",
                "dimension_name": "成本",
                "dimension_type": "cost",
                "typical_indicators": ["烧碱价格"],
            }
        ],
    )
    _write_framework(
        framework_dir / "FWK-AL-20260616.json",
        code="AL",
        name="铝",
        dimensions=[
            {
                "dimension_id": "al_cost",
                "dimension_name": "成本利润",
                "dimension_type": "cost_profit",
                "typical_indicators": ["氧化铝价格"],
            }
        ],
    )
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))

    graph = build_commodity_industry_graph(tmp_path, max_hops=3)

    assert graph["schema_version"] == "commodity_industry_graph.v1"
    assert graph["stats"]["chain_count"] >= 1
    assert graph["stats"]["node_type_counts"]["asset"] == 3
    assert graph["stats"]["node_type_counts"]["framework_dimension"] >= 3
    assert not any(node.get("label") == "沪深300股指" for node in graph["nodes"])
    assert any(node["node_id"] == "DIM:FUT-CU:supply" for node in graph["nodes"])
    assert any(edge["edge_type"] == "industry_transmission" for edge in graph["edges"])
    assert graph["test_calculation"]["stats"]["path_count"] > 0
    assert any(path["event_id"] == "TEST-CU-SUPPLY-001" for path in graph["test_calculation"]["paths"])


def test_publish_commodity_industry_graph_writes_latest_candidate(tmp_path: Path, monkeypatch) -> None:
    taxonomy_path = tmp_path / "futures_assets.v1.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-SC",
                        "asset_class": "futures",
                        "canonical_name": "原油",
                        "commodity_code": "SC",
                        "sector": "能化",
                        "category": "能源化工",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    framework_dir = tmp_path / "agent_workspace/candidates/frameworks/2026/06/16"
    framework_dir.mkdir(parents=True)
    _write_framework(
        framework_dir / "FWK-SC-20260616.json",
        code="SC",
        name="原油",
        dimensions=[
            {
                "dimension_id": "sc_cost",
                "dimension_name": "成本",
                "dimension_type": "cost",
                "typical_indicators": ["EIA库存"],
            }
        ],
    )
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))

    result = publish_commodity_industry_graph(tmp_path, max_hops=2)

    assert result["graph"].startswith("agent_workspace/candidates/industry_graph/")
    latest = tmp_path / result["latest_graph"]
    assert latest.exists()
    payload = read_json(latest)
    assert payload["stats"]["node_count"] >= 1
    assert payload["test_calculation"]["stats"]["test_event_count"] == 4
