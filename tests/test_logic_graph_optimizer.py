from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from quanta_agents.logic_graph_optimizer.runner import run_optimizer


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _source_node(asset: str, asset_id: str, suffix: str, evidence_count: int) -> dict:
    return {
        "logic_node_id": f"LGN-{asset_id}-{suffix}",
        "human_label": f"海外供应偏多",
        "asset": asset,
        "asset_id": asset_id,
        "dimension_label": "基本面/供给/海外供应",
        "dimension_type": "supply",
        "predicate": "海外供应",
        "direction": "bullish",
        "state": "strengthening",
        "strength": 0.5,
        "confidence": 0.8,
        "support_evidence_count": evidence_count,
        "conflict_evidence_count": 1,
        "neutral_evidence_count": 0,
        "source_count": 2,
        "evidence_count": evidence_count,
        "first_seen": "2026-04-10",
        "last_seen": "2026-06-10",
        "evidence_refs": [
            {
                "evidence_id": f"EVID-{asset_id}-{suffix}",
                "profile_id": f"PROFILE-{asset_id}",
                "article_id": f"RAW-{asset_id}",
            }
        ],
    }


def test_run_optimizer_writes_candidate_review_artifacts(tmp_path: Path) -> None:
    latest = tmp_path / "agent_workspace/candidates/research_logic_graph/latest"
    nodes = [
        _source_node("原油", "FUT-SC", "1", 10),
        _source_node("铜", "FUT-CU", "1", 9),
        _source_node("铝", "FUT-AL", "1", 8),
    ]
    edges = [
        {
            "edge_id": f"LEDGE-{idx}",
            "from": node["logic_node_id"],
            "to": f"DRV:{node['asset_id']}:supply",
            "edge_type": "logic_to_driver_candidate",
            "weight": 0.5,
            "polarity": 1,
            "delay": "days",
            "confidence": 0.8,
            "evidence_count": node["evidence_count"],
            "status": "candidate",
        }
        for idx, node in enumerate(nodes)
    ]
    common = {
        "schema_version": "research_logic_graph.v1",
        "run_id": "RUN-SOURCE",
        "generated_at": "2026-06-21T00:00:00+00:00",
        "start_date": "20260401",
        "end_date": "20260621",
    }
    _write_json(latest / "logic-nodes.json", {**common, "nodes": nodes})
    _write_json(latest / "logic-edges.json", {**common, "edges": edges})
    _write_json(latest / "temporal-episodes.json", {**common, "episodes": []})
    _write_json(latest / "driver-trigger-candidates.json", {**common, "driver_trigger_candidates": []})
    _write_json(latest / "run_manifest.json", {"run_id": "RUN-SOURCE"})

    result = run_optimizer(
        tmp_path,
        assets=("原油", "铜", "铝"),
        benchmark_size_per_asset=1,
        now=datetime(2026, 6, 21, 1, 0, 0, tzinfo=timezone.utc),
    )

    run_dir = Path(result["run_dir"])
    comparison = json.loads((run_dir / "metrics_comparison.json").read_text(encoding="utf-8"))
    node_queue = (run_dir / "review_queue/node_review_queue.jsonl").read_text(encoding="utf-8")

    assert result["decision"] == "review"
    assert comparison["deltas"]["state_separated_node_rate"] > 0
    assert (run_dir / "benchmark/benchmark_samples.jsonl").exists()
    assert "海外供应" in node_queue
    assert not (tmp_path / "gold").exists()
