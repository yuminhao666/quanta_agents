from __future__ import annotations

from pathlib import Path

from quanta_agents.core.io import read_json, write_json
from quanta_agents.signal_mapping.incremental_state import (
    apply_incremental_update,
    publish_incremental_state,
)


CREATED_AT = "2026-06-20T00:00:00+00:00"


def _asset_ref() -> dict:
    return {"id": "FUT-SC", "label": "原油", "ref_type": "asset"}


def _framework_ref() -> dict:
    return {"id": "futures_ine_crude_oil_geopolitics", "label": "地缘政治", "ref_type": "framework_node"}


def _theme(theme_id: str, *, title: str = "美伊协议与霍尔木兹通航") -> dict:
    return {
        "schema_version": "theme_anchor.v1",
        "theme_anchor_id": theme_id,
        "status": "candidate",
        "title": title,
        "theme_type": "geopolitics",
        "asset_refs": [_asset_ref()],
        "framework_node_refs": [_framework_ref()],
        "source_refs": [],
        "event_definition_layers": {
            "fact_layer": "美伊协议影响霍尔木兹通航和原油风险溢价。",
            "political_layer": "协议执行存在不确定性。",
            "time_window_layer": "2026-06-20 daily window",
            "settlement_rule_layer": None,
        },
        "lifecycle": {"support_count": 1, "conflict_count": 0, "review_state": "machine_candidate"},
        "promotion_policy": "review_required",
    }


def _signal(
    signal_id: str,
    *,
    theme_id: str,
    kind: str = "theme_update",
    direction: str = "bearish",
    text: str = "美伊协议落地后霍尔木兹通航恢复，原油地缘风险溢价回吐。",
    conflict: bool = False,
) -> dict:
    return {
        "schema_version": "research_signal.v1",
        "signal_id": signal_id,
        "status": "candidate",
        "created_at": CREATED_AT,
        "source_role": "research_report",
        "source_ref": {"ref_type": "evidence_capsule", "source_name": "test", "id": signal_id},
        "signal_kind": "thesis_conflict" if conflict else kind,
        "asset_refs": [_asset_ref()],
        "theme_refs": [{"id": theme_id, "label": "美伊协议与霍尔木兹通航", "ref_type": "theme"}],
        "framework_node_refs": [_framework_ref()],
        "direction": direction,
        "strength": 0.8,
        "confidence": 0.75,
        "confidence_label": "high_confidence_signal",
        "time_window": {"start": "2026-06-20", "end": "2026-06-20", "horizon": "daily"},
        "event_definition_layers": {
            "fact_layer": text,
            "political_layer": "协议执行与双方政治表态相关。",
            "time_window_layer": "2026-06-20 daily window",
            "settlement_rule_layer": None,
        },
        "evidence_refs": [{"evidence_id": signal_id, "path": "evidence.json", "stance": "source"}],
        "source_signal_refs": [],
        "conflict_refs": [{"id": "baseline", "ref_type": "signal"}] if conflict else [],
        "mapping": {"method": "test", "confidence": 0.75},
        "human_review_required": True,
        "promotion_policy": "review_required",
        "notes": text,
    }


def test_incremental_state_merges_same_theme_across_candidate_ids() -> None:
    first = apply_incremental_update(
        None,
        signals=[_signal("SIG-1", theme_id="THA-DAY-1")],
        themes=[_theme("THA-DAY-1")],
        run_id="RUN-1",
        generated_at=CREATED_AT,
    )
    second = apply_incremental_update(
        first,
        signals=[_signal("SIG-2", theme_id="THA-DAY-2")],
        themes=[_theme("THA-DAY-2")],
        run_id="RUN-2",
        generated_at="2026-06-21T00:00:00+00:00",
    )

    assert second["stats"]["theme_count"] == 1
    assert second["stats"]["signal_count"] == 2
    theme_state = next(iter(second["themes"].values()))
    assert theme_state["support_count"] == 2
    assert theme_state["current_phase"] == "tracking"
    assert len(theme_state["event_refs"]) == 1


def test_incremental_state_tracks_conflicts_and_dimension_direction() -> None:
    state = apply_incremental_update(
        None,
        signals=[
            _signal("SIG-SUPPORT", theme_id="THA-OIL", direction="bearish"),
            _signal(
                "SIG-CONFLICT",
                theme_id="THA-OIL",
                direction="bullish",
                text="协议执行出现反复，原油地缘风险溢价重新上升。",
                conflict=True,
            ),
        ],
        themes=[_theme("THA-OIL")],
        run_id="RUN-CONFLICT",
        generated_at=CREATED_AT,
    )

    theme_state = next(iter(state["themes"].values()))
    dimension_state = next(iter(state["dimensions"].values()))
    assert theme_state["current_phase"] == "conflicted"
    assert theme_state["conflict_count"] == 1
    assert dimension_state["signal_count"] == 2
    assert dimension_state["support_count"] == 1
    assert dimension_state["conflict_count"] == 1
    assert abs(dimension_state["direction_score"]) < 0.001


def test_incremental_state_uses_llm_canonical_theme_id_when_available() -> None:
    theme = _theme("THA-POLYMARKET-US-IRAN")
    theme["llm_normalization"] = {
        "canonical_theme_id": "THSTATE-US-IRAN-HORMUZ",
        "canonical_title_zh": "美伊协议执行与霍尔木兹通航",
        "theme_type": "geopolitics",
    }
    signal = _signal("SIG-POLY", theme_id="THA-POLYMARKET-US-IRAN", kind="event_definition")
    signal["source_role"] = "web_info"
    signal["llm_normalization"] = {"canonical_event_id": "EVSTATE-US-IRAN-SIGNING"}

    state = apply_incremental_update(
        None,
        signals=[signal],
        themes=[theme],
        run_id="RUN-LLM-NORMALIZED",
        generated_at=CREATED_AT,
    )

    assert "THSTATE-US-IRAN-HORMUZ" in state["themes"]
    assert "EVSTATE-US-IRAN-SIGNING" in state["events"]
    assert state["themes"]["THSTATE-US-IRAN-HORMUZ"]["title"] == "美伊协议执行与霍尔木兹通航"


def test_publish_incremental_state_writes_candidate_without_latest_by_default(tmp_path: Path) -> None:
    signal_set = tmp_path / "agent_workspace/candidates/signal_map/2026/06/20/CAND/research_signals.json"
    theme_set = tmp_path / "agent_workspace/candidates/theme_anchor/2026/06/20/CAND/theme_anchors.json"
    write_json(signal_set, {"schema_version": "research_signal_candidate_set.v1", "signals": [_signal("SIG-1", theme_id="THA-1")]})
    write_json(theme_set, {"schema_version": "theme_anchor_candidate_set.v1", "theme_anchors": [_theme("THA-1")]})

    result = publish_incremental_state(
        tmp_path,
        signal_paths=[signal_set],
        theme_paths=[theme_set],
        date_key="20260620",
    )

    assert result["status"] == "succeeded"
    assert result["stats"]["theme_count"] == 1
    assert "latest" not in result["paths"]
    state = read_json(tmp_path / result["paths"]["research_state"])
    manifest = read_json(tmp_path / result["paths"]["manifest"])
    assert state["schema_version"] == "incremental_research_state.v1"
    assert manifest["requires_review"] is True
