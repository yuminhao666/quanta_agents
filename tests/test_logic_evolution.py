from __future__ import annotations

from quanta_agents.core.io import write_json
from quanta_agents.futures_daily.logic_evolution import build_logic_evolution


def _write_run(path, date_key: str, score: float, dims: list[dict], thesis: str) -> None:
    write_json(path / "manifest.json", {"date": date_key})
    write_json(path / "logic_chains.json", {"assets": {}})
    write_json(
        path / "trade_thesis.json",
        {
            "assets": {
                "铜": {
                    "asset": "铜",
                    "framework_score": score,
                    "recommendation": "偏多观察" if score > 0 else "偏空观察",
                    "main_trade_thesis": thesis,
                }
            }
        },
    )
    write_json(
        path / "dimension_scores.json",
        {
            "assets": {
                "铜": {
                    "framework_score": score,
                    "dimensions": dims,
                }
            }
        },
    )


def test_build_logic_evolution_detects_validation_reversal_and_new_logic(tmp_path):
    previous = tmp_path / "agent_workspace/candidates/futures_daily_raw_runs/2026/06/16/RUN-20260616-TEST-RAW-DAILY"
    current = tmp_path / "agent_workspace/candidates/futures_daily_raw_runs/2026/06/17/RUN-20260617-TEST-RAW-DAILY"
    _write_run(
        previous,
        "20260616",
        3.0,
        [
            {
                "dimension_id": "cu_demand",
                "dimension_label": "需求",
                "direction_score": 2.0,
                "deduped_evidence_count": 2,
                "dimension_synthesis": "需求改善支撑铜价。",
            },
            {
                "dimension_id": "cu_inventory",
                "dimension_label": "库存",
                "direction_score": 1.0,
                "deduped_evidence_count": 1,
                "dimension_synthesis": "库存小幅去化。",
            },
        ],
        "铜需求改善、库存去化，偏多。",
    )
    _write_run(
        current,
        "20260617",
        -2.0,
        [
            {
                "dimension_id": "cu_demand",
                "dimension_label": "需求",
                "direction_score": -2.5,
                "deduped_evidence_count": 3,
                "dimension_synthesis": "高价抑制下游消费，需求转弱。",
            },
            {
                "dimension_id": "cu_inventory",
                "dimension_label": "库存",
                "direction_score": 2.0,
                "deduped_evidence_count": 2,
                "dimension_synthesis": "库存继续去化。",
            },
            {
                "dimension_id": "cu_policy",
                "dimension_label": "政策",
                "direction_score": 1.2,
                "deduped_evidence_count": 1,
                "dimension_synthesis": "政策预期新增支撑。",
            },
        ],
        "铜需求转弱压制价格，偏空。",
    )

    result = build_logic_evolution(current, previous, tmp_path, use_llm=False)
    copper = result["assets"][0]
    by_dimension = {row["dimension_id"]: row for row in copper["dimension_evolution"]}

    assert copper["overall_status"] == "reversal"
    assert by_dimension["cu_demand"]["evolution_status"] == "reversal"
    assert by_dimension["cu_inventory"]["evolution_status"] == "further_validated"
    assert by_dimension["cu_policy"]["evolution_status"] == "new_logic"
    assert result["stats"]["dimension_status_counts"]["reversal"] == 1
    assert result["timeline_events"]
