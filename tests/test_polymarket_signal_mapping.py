from __future__ import annotations

from pathlib import Path

import pytest

from quanta_agents.core.io import read_json, write_json
from quanta_agents.signal_mapping import (
    map_polymarket_hotspots_to_signals,
    run_polymarket_signal_mapping,
    validate_signal_theme_objects,
)
from quanta_agents.signal_mapping.theme_anchor_matcher import load_theme_anchor_index


CREATED_AT = "2026-06-20T00:00:00+00:00"


def _polymarket_hotspots() -> dict:
    return {
        "schema_version": "polymarket_hotspots.v1",
        "status": "candidate",
        "generated_at": "2026-06-19T07:41:23+00:00",
        "source": {"source_type": "polymarket_cli", "collected_at": "2026-06-19T07:41:23+00:00"},
        "hotspots": [
            {
                "market_id": "2511144",
                "question": "US x Iran diplomatic meeting by June 21, 2026?",
                "question_zh": "美国与伊朗会在2026年6月21日前举行外交会谈吗？",
                "url": "https://polymarket.com/event/us-x-iran-diplomatic-meeting-by-329",
                "category": "geopolitics",
                "category_zh": "地缘政治",
                "end_date": "2026-06-21T00:00:00Z",
                "primary_outcome": "Yes",
                "primary_probability": 0.156,
                "best_bid": 0.14,
                "best_ask": 0.172,
                "spread": 0.032,
                "last_trade_price": 0.131,
                "liquidity": 48909.07,
                "hotspot_reasons": ["24h成交额 $374,625", "1日概率变化 -71.4pct"],
                "event": {
                    "id": "371649",
                    "title": "US x Iran diplomatic meeting by...?",
                    "title_zh": "美国与伊朗 外交会谈 by...?",
                },
            },
            {
                "market_id": "2593852",
                "question": "Bitcoin Up or Down - June 19, 3:30AM-3:45AM ET",
                "question_zh": "比特币 Up or Down - June 19, 3:30AM-3:45AM ET",
                "url": "https://polymarket.com/event/btc-updown-15m-1781854200",
                "category": "crypto",
                "category_zh": "加密资产",
                "end_date": "2026-06-19T07:45:00Z",
                "primary_outcome": "Down",
                "primary_probability": 0.996,
                "spread": 0.008,
                "liquidity": 21355.96,
                "hotspot_reasons": ["24h成交额 $29,082", "新上线市场"],
            },
        ],
    }


def _theme_anchor_payload() -> dict:
    return {
        "schema_version": "theme_anchor_candidate_set.v1",
        "status": "candidate",
        "candidate_id": "CAND-THEME-ANCHOR-TEST",
        "generated_at": CREATED_AT,
        "theme_anchors": [
            {
                "schema_version": "theme_anchor.v1",
                "theme_anchor_id": "THA-OIL-IRAN-HORMUZ",
                "status": "candidate",
                "created_at": CREATED_AT,
                "updated_at": None,
                "title": "美伊协议与霍尔木兹通航",
                "description": "伊朗、中东地缘和霍尔木兹通航变化影响原油风险溢价。",
                "anchor_kind": "research_report_repeated_theme",
                "theme_type": "geopolitics",
                "source_roles": ["research_report"],
                "source_refs": [],
                "asset_refs": [{"id": "FUT-SC", "label": "原油", "ref_type": "asset"}],
                "framework_node_refs": [],
                "event_definition_layers": {
                    "fact_layer": "伊朗、中东地缘和霍尔木兹通航变化影响原油风险溢价。",
                    "political_layer": "伊朗、中东地缘和霍尔木兹通航变化影响原油风险溢价。",
                    "time_window_layer": "2026-06-18 research-report observation window",
                    "settlement_rule_layer": None,
                },
                "signal_refs": [],
                "evidence_refs": [],
                "polymarket_refs": [],
                "aliases": ["美伊", "伊朗", "霍尔木兹", "中东地缘"],
                "lifecycle": {
                    "current_phase": "tracking",
                    "first_seen_at": "2026-06-18",
                    "last_seen_at": "2026-06-18",
                    "support_count": 2,
                    "conflict_count": 0,
                    "review_state": "machine_candidate",
                },
                "promotion_policy": "review_required",
            }
        ],
    }


def _write_theme_anchor_set(root: Path) -> Path:
    path = (
        root
        / "agent_workspace/candidates/theme_anchor/2026/06/18/"
        "CAND-THEME-ANCHOR-TEST/theme_anchors.json"
    )
    write_json(path, _theme_anchor_payload())
    return path


def test_polymarket_hotspots_map_to_low_confidence_research_signals(tmp_path: Path) -> None:
    theme_path = _write_theme_anchor_set(tmp_path)
    index = load_theme_anchor_index(tmp_path, candidate_path=theme_path)

    signals, matches = map_polymarket_hotspots_to_signals(
        _polymarket_hotspots(),
        source_path="agent_workspace/candidates/polymarket_daily/latest/hotspots.json",
        created_at=CREATED_AT,
        theme_index=index,
    )

    assert len(signals) == 2
    anchored = signals[0]
    assert anchored["source_role"] == "web_info"
    assert anchored["signal_kind"] == "event_definition"
    assert anchored["confidence_label"] == "low_confidence_signal"
    assert anchored["confidence"] == 0.25
    assert anchored["theme_refs"] == [
        {"id": "THA-OIL-IRAN-HORMUZ", "label": "美伊协议与霍尔木兹通航", "ref_type": "theme"}
    ]
    assert anchored["asset_refs"][0]["id"] == "FUT-SC"
    assert anchored["market_observation"]["price"] == 0.156
    assert anchored["market_observation"]["probability"] is None
    assert anchored["market_observation"]["spread"] == 0.032
    assert anchored["market_observation"]["liquidity_label"] == "medium"
    assert anchored["event_definition_layers"]["settlement_rule_layer"]
    assert "not as truth probability" in anchored["notes"]
    assert matches[0]["anchoring_status"] == "anchored"

    unanchored = signals[1]
    assert unanchored["theme_refs"] == []
    assert unanchored["asset_refs"][0]["id"] == "CRYPTO-BTC"
    assert "unanchored_theme_candidate" in unanchored["notes"]
    assert matches[1]["anchoring_status"] == "unanchored_theme_candidate"


def test_polymarket_signal_schema_validation_uses_research_signal_contract(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    schema_root = Path("/Volumes/数字大脑/quanta_data")
    if not (schema_root / "configs/schemas/research_signal.v1.schema.json").exists():
        pytest.skip("quanta_data schema root is not available")

    theme_path = _write_theme_anchor_set(tmp_path)
    index = load_theme_anchor_index(tmp_path, candidate_path=theme_path)
    signals, _ = map_polymarket_hotspots_to_signals(
        _polymarket_hotspots(),
        source_path="agent_workspace/candidates/polymarket_daily/latest/hotspots.json",
        created_at=CREATED_AT,
        theme_index=index,
    )

    result = validate_signal_theme_objects(schema_root, signals=signals, themes=[])
    assert result["schema_validation"] == "passed"
    assert result["signal_count"] == 2


def test_run_polymarket_signal_mapping_writes_signal_map_candidate(tmp_path: Path) -> None:
    hotspots_path = tmp_path / "agent_workspace/candidates/polymarket_daily/latest/hotspots.json"
    manifest_path = tmp_path / "agent_workspace/candidates/polymarket_daily/latest/manifest.json"
    theme_path = _write_theme_anchor_set(tmp_path)
    write_json(hotspots_path, _polymarket_hotspots())
    write_json(
        manifest_path,
        {
            "schema_version": "polymarket_daily_candidate_manifest.v1",
            "candidate_id": "CAND-POLYMARKET-TEST",
            "date": "20260619",
            "outputs": {"hotspots": "agent_workspace/candidates/polymarket_daily/latest/hotspots.json"},
        },
    )

    result = run_polymarket_signal_mapping(
        tmp_path,
        hotspots_path=hotspots_path,
        manifest_path=manifest_path,
        theme_anchor_path=theme_path,
        work_order_id="WO-DEV-20260620-011",
        validate=False,
    )

    assert result["status"] == "succeeded"
    assert result["signal_count"] == 2
    assert result["anchored_count"] == 1
    assert result["unanchored_count"] == 1
    payload = read_json(tmp_path / result["paths"]["research_signals"])
    manifest = read_json(tmp_path / result["paths"]["signal_manifest"])
    run_manifest = read_json(tmp_path / result["paths"]["run_manifest"])
    assert payload["mapping_summary"]["price_semantics"].endswith("market_observation.probability is always null.")
    assert payload["theme_anchor_matches"][0]["theme_anchor_id"] == "THA-OIL-IRAN-HORMUZ"
    assert manifest["source_workflow"] == "polymarket_daily"
    assert run_manifest["run_type"] == "polymarket_signal_mapper"
