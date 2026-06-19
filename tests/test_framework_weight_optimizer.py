from __future__ import annotations

from quanta_agents.core.framework_weight_optimizer import build_framework_weight_optimization
from quanta_agents.core.io import write_json


def _write_registry(root) -> None:
    write_json(
        root / "agent_workspace/candidates/analysis_framework/latest/analysis-framework-registry.json",
        {
            "dimensions": [
                {
                    "asset": "纯苯",
                    "asset_id": "FUT-BZ",
                    "framework_id": "FWK-纯苯",
                    "dimension_id": "bz_supply",
                    "dimension_label": "国内装置开工与检修",
                    "dimension_type": "supply",
                    "default_weight": 0.18,
                },
                {
                    "asset": "纯苯",
                    "asset_id": "FUT-BZ",
                    "framework_id": "FWK-纯苯",
                    "dimension_id": "bz_inventory",
                    "dimension_label": "港口库存",
                    "dimension_type": "inventory",
                    "default_weight": 0.15,
                },
            ]
        },
    )


def _write_run(root, date_key: str, run_id: str, supply_score: float, inventory_score: float):
    run = root / f"agent_workspace/candidates/futures_daily_raw_runs/{date_key[:4]}/{date_key[4:6]}/{date_key[6:8]}/{run_id}"
    write_json(run / "manifest.json", {"date": date_key})
    write_json(
        run / "trade_thesis.json",
        {
            "assets": {
                "纯苯": {
                    "framework_score": supply_score + inventory_score,
                    "score_divergence": {"status": "aligned"},
                }
            }
        },
    )
    write_json(
        run / "dimension_scores.json",
        {
            "assets": {
                "纯苯": {
                    "dimensions": [
                        {
                            "dimension_id": "bz_supply",
                            "dimension_label": "国内装置开工与检修",
                            "dimension_type": "supply",
                            "direction_score": supply_score,
                            "effective_weight": 0.28,
                            "weighted_contribution": supply_score * 0.28,
                            "directional_consensus": 0.42,
                            "conflict_level": "medium",
                            "conflict_ratio": 0.32,
                            "scored_evidence_count": 5,
                        },
                        {
                            "dimension_id": "bz_inventory",
                            "dimension_label": "港口库存",
                            "dimension_type": "inventory",
                            "direction_score": inventory_score,
                            "effective_weight": 0.24,
                            "weighted_contribution": inventory_score * 0.24,
                            "directional_consensus": 1.0,
                            "conflict_level": "none",
                            "conflict_ratio": 0.0,
                            "scored_evidence_count": 4,
                        },
                    ]
                }
            }
        },
    )
    return run


def test_framework_weight_optimizer_builds_review_candidates(tmp_path):
    _write_registry(tmp_path)
    _write_run(tmp_path, "20260616", "RUN-20260616-TEST-RAW-DAILY", 2.0, 3.0)
    current = _write_run(tmp_path, "20260617", "RUN-20260617-TEST-RAW-DAILY", 2.5, 3.5)

    result = build_framework_weight_optimization(current, tmp_path, history_limit=5, use_llm=False)
    candidates = result["candidates"]
    by_dimension = {item["dimension_id"]: item for item in candidates}

    assert result["stats"]["asset_count"] == 1
    assert by_dimension["bz_supply"]["action"] == "split_dimension_or_lower_dynamic_weight"
    assert by_dimension["bz_inventory"]["action"] in {"raise_base_weight", "maintain_with_dynamic_adjustment"}
    assert by_dimension["bz_supply"]["recommended_adjustment"]["dynamic_multiplier"] < 1
    assert result["llm_review"]["status"] == "skipped"
