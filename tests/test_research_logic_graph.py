from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from quanta_agents.core.io import read_json
from quanta_agents.research_reports.logic_graph import run_research_logic_graph


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_run_research_logic_graph_writes_traceable_outputs(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "agent_workspace/candidates/analysis_framework/latest/analysis-framework-registry.json",
        {
            "schema_version": "analysis_framework_registry.v1",
            "dimensions": [
                {
                    "dimension_id": "DIM-CU-SUPPLY",
                    "dimension_label": "基本面/供给/海外铜矿",
                    "dimension_type": "supply",
                    "asset": "铜",
                    "asset_id": "FUT-CU",
                    "framework_id": "FWK-CU",
                    "default_weight": 0.18,
                }
            ],
        },
    )
    _write_json(
        tmp_path / "agent_workspace/candidates/analysis_framework/latest/indicator-event-catalog.json",
        {
            "schema_version": "framework_indicator_event_catalog.v1",
            "terms": [
                {
                    "term_id": "TERM-CU-TC",
                    "term_type": "indicator",
                    "name": "铜精矿 TC",
                    "aliases": ["TC", "加工费"],
                    "asset": "铜",
                    "asset_id": "FUT-CU",
                    "dimension_id": "DIM-CU-SUPPLY",
                    "dimension_label": "基本面/供给/海外铜矿",
                    "dimension_type": "supply",
                }
            ],
        },
    )
    _write_json(
        tmp_path
        / "canonical_documents/research_reports/structured_profiles/hzzhqx_wechat/2026/04/01/RREP-PROFILE-CU.json",
        {
            "schema_version": "wechat_single_report_profile.v1",
            "profile_id": "RREP-PROFILE-CU",
            "status": "structured",
            "metadata": {
                "article_id": "RAW-CU",
                "raw_id": "RAW-CU",
                "title": "铜周报",
                "source_account": "测试期货",
                "published_at": "2026-04-01 09:00:00",
            },
            "detailed_analysis": {
                "铜": {
                    "asset": {
                        "name": "铜",
                        "asset_id": "FUT-CU",
                        "commodity_code": "CU",
                        "category": "有色金属",
                        "sector": "有色",
                    }
                }
            },
            "evidence": [
                {
                    "evidence_id": "EVID-CU-1",
                    "asset": "铜",
                    "text": "海外矿端扰动延续，铜精矿 TC 下行，供应偏紧。",
                    "direction": "bullish",
                    "direction_score": 1.0,
                    "scoring_role": "fundamental_evidence",
                    "field_tags": ["bullish_factors", "supply_demand"],
                },
                {
                    "evidence_id": "EVID-CU-2",
                    "asset": "铜",
                    "text": "TC 继续处于低位，冶炼原料偏紧仍支撑铜价。",
                    "direction": "bullish",
                    "direction_score": 1.0,
                    "scoring_role": "fundamental_evidence",
                    "field_tags": ["bullish_factors"],
                },
            ],
        },
    )

    result = run_research_logic_graph(
        tmp_path,
        start_date="20260401",
        end_date="20260402",
        now=datetime(2026, 4, 2, 12, 0, 0, tzinfo=timezone.utc),
    )

    latest_dir = Path(result["absolute_latest_dir"])
    logic_nodes = read_json(latest_dir / "logic-nodes.json")
    triggers = read_json(latest_dir / "driver-trigger-candidates.json")

    assert result["stats"]["profile_count"] == 1
    assert result["stats"]["evidence_signal_count"] == 2
    assert logic_nodes["nodes"][0]["asset_id"] == "FUT-CU"
    assert logic_nodes["nodes"][0]["evidence_refs"][0]["profile_id"] == "RREP-PROFILE-CU"
    assert "TC" in logic_nodes["nodes"][0]["human_label"] or "海外铜矿" in logic_nodes["nodes"][0]["human_label"]
    assert triggers["driver_trigger_candidates"]
