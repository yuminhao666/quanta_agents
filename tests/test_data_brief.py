from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from quanta_agents.futures_daily import data_brief
from quanta_agents.futures_daily.data_brief import DataPoint


def test_map_indicators_to_framework_binds_alpha_dictionary_to_dimensions(monkeypatch, tmp_path):
    data_dict = {
        "[能源化工/石油沥青|石油沥青] 沥青社会库存 (万吨)": {
            "data_name": "[能源化工/石油沥青|石油沥青] 沥青社会库存 (万吨)",
            "category": "能源化工",
            "variety": "石油沥青",
            "sheet": "石油沥青",
            "raw_name": "沥青社会库存",
            "unit": "万吨",
            "row_count": 300,
            "distinct_dates": 300,
            "first_dt": "2024-01-01",
            "last_dt": "2026-06-18",
        },
        "[能源化工/石油沥青|石油沥青] 沥青厂开工率 (%)": {
            "data_name": "[能源化工/石油沥青|石油沥青] 沥青厂开工率 (%)",
            "category": "能源化工",
            "variety": "石油沥青",
            "sheet": "石油沥青",
            "raw_name": "沥青厂开工率",
            "unit": "%",
            "row_count": 300,
            "distinct_dates": 300,
            "first_dt": "2024-01-01",
            "last_dt": "2026-06-18",
        },
        "[能源化工/PTA|PTA] PTA社会库存 (万吨)": {
            "data_name": "[能源化工/PTA|PTA] PTA社会库存 (万吨)",
            "category": "能源化工",
            "variety": "PTA",
            "sheet": "PTA",
            "raw_name": "PTA社会库存",
            "unit": "万吨",
            "row_count": 300,
            "distinct_dates": 300,
            "first_dt": "2024-01-01",
            "last_dt": "2026-06-18",
        },
    }
    data_dict_path = tmp_path / "data_dict.json"
    data_dict_path.write_text(json.dumps(data_dict, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        data_brief,
        "load_framework_dimensions",
        lambda asset_name, root=None: (
            {"canonical_name": "石油沥青", "asset_id": "FUT-BU", "aliases": ["沥青", "石油沥青"]},
            [
                {
                    "dimension_id": "bu_inventory",
                    "dimension_label": "基本面/炼厂与社会总库存",
                    "dimension_name": "炼厂与社会总库存",
                    "dimension_type": "inventory",
                    "terms": ["社会库存", "厂库库存"],
                },
                {
                    "dimension_id": "bu_supply",
                    "dimension_label": "基本面/国内炼厂开工与排产",
                    "dimension_name": "国内炼厂开工与排产",
                    "dimension_type": "supply",
                    "terms": ["沥青厂开工率", "排产"],
                },
            ],
        ),
    )

    result = data_brief.map_indicators_to_framework(
        "石油沥青",
        root=tmp_path,
        data_dict_path=data_dict_path,
        max_series_per_dimension=2,
    )

    assert result["asset"] == "石油沥青"
    assert result["stats"]["candidate_series_count"] == 2
    assert result["stats"]["covered_dimension_count"] == 2
    inventory = result["dimensions"][0]
    assert inventory["coverage"] == "covered"
    assert inventory["mapped_series"][0]["raw_name"] == "沥青社会库存"


def test_build_commodity_data_brief_combines_logic_and_indicator_history(monkeypatch, tmp_path):
    mapping = {
        "stats": {"mapped_series_count": 1},
        "dimensions": [
            {
                "dimension_id": "bu_inventory",
                "dimension_label": "基本面/炼厂与社会总库存",
                "dimension_type": "inventory",
                "coverage": "covered",
                "mapped_series": [
                    {
                        "data_name": "沥青社会库存",
                        "raw_name": "沥青社会库存",
                        "unit": "万吨",
                        "row_count": 10,
                        "last_dt": "2026-06-18",
                    }
                ],
            }
        ],
    }
    points = [
        DataPoint(date(2026, 5, 18), Decimal("130"), "万吨"),
        DataPoint(date(2026, 6, 11), Decimal("124"), "万吨"),
        DataPoint(date(2026, 6, 18), Decimal("120"), "万吨"),
    ]
    run_dir = tmp_path / "RUN-TEST"
    run_dir.mkdir()
    monkeypatch.setattr(data_brief, "map_indicators_to_framework", lambda *a, **kw: mapping)
    monkeypatch.setattr(data_brief, "fetch_recent_history", lambda names, **kw: ({"沥青社会库存": points}, []))
    monkeypatch.setattr(
        data_brief,
        "_load_trade_thesis",
        lambda run_dir, asset: {
            "main_trade_thesis": "石油沥青基本面小幅偏多，库存去化对价格形成支撑。",
            "recommendation": "小幅偏多",
            "decision_score": 2.4,
        },
    )
    monkeypatch.setattr(data_brief, "resolve_data_dict_path", lambda path=None: tmp_path / "data_dict.json")

    result = data_brief.build_commodity_data_brief(
        "石油沥青",
        root=tmp_path,
        run_dir=run_dir,
        use_llm=False,
        source_mode="logic_chain",
    )

    assert result["schema_version"] == "commodity_data_brief.v1"
    assert result["logic_context"]["recommendation"] == "小幅偏多"
    assert result["summary"]["chart_series_count"] == 1
    assert result["dimension_data_cards"][0]["data_signal_score"] > 0
    assert result["chart_series"][0]["points"][-1]["value"] == 120.0
