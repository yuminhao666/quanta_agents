from __future__ import annotations

import sqlite3
from pathlib import Path

from quanta_agents.asset_event_state import (
    AssetEventStore,
    CausalPropagationEngine,
    ClusteringEngine,
    DriverStateMachine,
    EventExtractor,
    GraphLayerService,
    EventMapper,
    NarrativeService,
    run_asset_event_pipeline,
    structured_cluster_score,
)
from quanta_agents.core.io import read_json, write_json


def _payload(text: str, *, source_id: str, timestamp: str) -> dict:
    return {
        "source_id": source_id,
        "source_type": "unit_test",
        "timestamp": timestamp,
        "text": text,
    }


def test_event_extractor_maps_copper_event_to_schema_node(tmp_path: Path) -> None:
    mapper = EventMapper(tmp_path)
    extractor = EventExtractor(mapper, quanta_root=tmp_path)

    events = extractor.extract_events(
        _payload(
            "美国讨论铜进口关税，市场担心供应链收紧，铜价上涨预期增强。",
            source_id="raw.news.1",
            timestamp="2026-06-20T08:00:00+00:00",
        )
    )

    event = events[0]
    assert event["asset"] == "Copper"
    assert event["event_type"] == "policy"
    assert event["impact_nodes"]
    assert event["source_id"] == "raw.news.1"
    assert any(node.startswith("Copper.schema.") for node in event["impact_nodes"])
    assert event["event_type"] in {"policy", "macro", "supply", "demand", "sentiment", "data", "research"}
    assert event["event_time"] == "2026-06-20T08:00:00+00:00"
    assert event["effective_time"] == "2026-06-20T08:00:00+00:00"
    assert event["update_time"]


def test_legacy_positioning_hint_converges_to_sentiment_event(tmp_path: Path) -> None:
    mapper = EventMapper(tmp_path)
    extractor = EventExtractor(mapper, quanta_root=tmp_path)

    event = extractor.extract_events(
        {
            "source_id": "legacy.signal.1",
            "source_type": "incremental_state",
            "timestamp": "2026-06-20T08:00:00+00:00",
            "asset": "Copper",
            "event_type": "positioning",
            "summary": "铜多头持仓增加，市场情绪走强。",
            "signal_vector": {"price": 0.4, "sentiment": 0.5},
        }
    )[0]

    assert event["event_type"] == "sentiment"


def test_research_and_data_sources_use_single_event_schema(tmp_path: Path) -> None:
    mapper = EventMapper(tmp_path)
    extractor = EventExtractor(mapper, quanta_root=tmp_path)

    research_event = extractor.extract_events(
        {
            "source_id": "wechat.report.1",
            "source_type": "research",
            "timestamp": "2026-06-20T08:00:00+00:00",
            "asset": "COPPER",
            "summary": "研报观点认为铜精矿加工费继续承压，矿端偏紧仍需跟踪。",
        }
    )[0]
    data_event = extractor.extract_events(
        {
            "source_id": "mysql.dzq.1",
            "source_type": "data",
            "timestamp": "2026-06-20T09:00:00+00:00",
            "asset": "Copper",
            "summary": "LME铜库存（日度） 最新值下降，库存数据继续去化。",
        }
    )[0]

    assert research_event["schema_version"] == "asset_canonical_event.v1"
    assert data_event["schema_version"] == "asset_canonical_event.v1"
    assert research_event["event_type"] == "research"
    assert data_event["event_type"] == "data"
    assert research_event["source_type"] == "research"
    assert data_event["source_type"] == "data"
    assert research_event["asset"] == "Copper"


def test_clustering_uses_structured_score_and_state() -> None:
    mapper = EventMapper()
    extractor = EventExtractor(mapper)
    first = extractor.extract_events(
        _payload(
            "美国铜关税预期升温，进口成本上升，铜价上涨。",
            source_id="raw.news.1",
            timestamp="2026-06-20T08:00:00+00:00",
        )
    )[0]
    second = extractor.extract_events(
        _payload(
            "美国铜关税讨论继续发酵，投资者上调铜价风险溢价。",
            source_id="raw.news.2",
            timestamp="2026-06-20T18:00:00+00:00",
        )
    )[0]

    score = structured_cluster_score(second, first)
    assert score["asset_match"] == 1.0
    assert score["driver_match"] == 1.0
    assert score["score"] >= 0.68

    clusters = ClusteringEngine().cluster_events([first, second])
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster["status"] == "strengthening"
    assert cluster["traceability"]["embedding_used"] is False
    assert "0.4*asset_match" in cluster["traceability"]["clustering_rule"]


def test_cluster_narrative_is_not_allowed_by_default() -> None:
    try:
        NarrativeService().generate({"cluster_id": "CL-1", "events": [], "net_signal": {}})
    except RuntimeError as exc:
        assert "use ThemeNarrativeService" in str(exc)
    else:
        raise AssertionError("cluster narrative should be legacy-only")


def test_driver_state_machine_tracks_support_and_contradiction() -> None:
    mapper = EventMapper()
    extractor = EventExtractor(mapper)
    first = extractor.extract_events(
        _payload(
            "美国铜关税预期升温，进口成本上升，铜价上涨。",
            source_id="raw.news.1",
            timestamp="2026-06-20T08:00:00+00:00",
        )
    )[0]
    second = extractor.extract_events(
        _payload(
            "美国取消铜关税讨论，进口成本预期回落，铜价下跌。",
            source_id="raw.news.2",
            timestamp="2026-06-20T18:00:00+00:00",
        )
    )[0]

    drivers = DriverStateMachine().update_drivers([first, second])
    driver = drivers[0]
    assert driver["driver_id"].startswith("DRV-")
    assert driver["supporting_events"] == [first["event_id"]]
    assert driver["contradicting_events"] == [second["event_id"]]
    assert driver["state"]["trend"] == "reversing"
    assert -1.0 <= driver["state"]["strength"] <= 1.0


def test_causal_propagation_generates_traceable_signal() -> None:
    mapper = EventMapper()
    extractor = EventExtractor(mapper)
    event = extractor.extract_events(
        {
            "source_id": "raw.data.1",
            "source_type": "data",
            "timestamp": "2026-06-20T08:00:00+00:00",
            "asset": "Copper",
            "event_type": "data",
            "summary": "LME铜库存下降，库存数据继续去化。",
            "signal_vector": {"price": 0.4, "supply": -0.5, "demand": 0.0, "macro": 0.0, "sentiment": 0.2},
            "impact_nodes": ["Copper.schema.Data"],
            "confidence": 0.8,
        }
    )[0]

    graph_events = GraphLayerService().build_graph_events([event])
    paths = CausalPropagationEngine().propagate([event])
    signals = CausalPropagationEngine().signals_from_paths(paths, {event["event_id"]: event})

    assert {item["graph_type"] for item in graph_events} == {"event", "industry", "causal"}
    assert any(path["path_type"] == "propagated" for path in paths)
    assert all(signal["event_ids"] == [event["event_id"]] for signal in signals)
    assert all(signal["causal_path_ids"] for signal in signals)
    assert all(signal["driver_id"].startswith("DRV-") for signal in signals)


def test_driver_state_machine_updates_from_signals() -> None:
    signals = [
        {
            "signal_id": "SIG-1",
            "asset": "Copper",
            "driver_id": "DRV-COPPER-SUPPLY",
            "driver_key": "Copper.schema.Supply",
            "direction": 1,
            "magnitude": 0.5,
            "confidence": 0.8,
            "event_ids": ["EV-1"],
            "causal_path_ids": ["CPATH-1"],
            "effective_time": "2026-06-20T08:00:00+00:00",
            "traceability": {"source_id": "raw.news.1"},
        },
        {
            "signal_id": "SIG-2",
            "asset": "Copper",
            "driver_id": "DRV-COPPER-SUPPLY",
            "driver_key": "Copper.schema.Supply",
            "direction": -1,
            "magnitude": 0.7,
            "confidence": 0.8,
            "event_ids": ["EV-2"],
            "causal_path_ids": ["CPATH-2"],
            "effective_time": "2026-06-20T09:00:00+00:00",
            "traceability": {"source_id": "raw.data.1"},
        },
    ]

    driver = DriverStateMachine().update_from_signals(signals)[0]

    assert driver["driver_id"] == "DRV-COPPER-SUPPLY"
    assert driver["supporting_signals"] == ["SIG-1"]
    assert driver["contradicting_signals"] == ["SIG-2"]
    assert driver["state"]["trend"] == "reversing"
    assert driver["traceability"]["event_to_signal_to_driver"][0]["causal_path_ids"] == ["CPATH-1"]


def test_pipeline_writes_event_driver_tables_and_candidate_manifest(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    write_json(
        input_path,
        {
            "items": [
                _payload(
                    "美国铜关税预期升温，进口成本上升，铜价上涨。",
                    source_id="raw.flash.1",
                    timestamp="2026-06-20T08:00:00+00:00",
                ),
                _payload(
                    "美国铜关税讨论继续发酵，投资者上调铜价风险溢价。",
                    source_id="raw.flash.2",
                    timestamp="2026-06-20T12:00:00+00:00",
                ),
            ]
        },
    )

    result = run_asset_event_pipeline(
        tmp_path,
        input_paths=[input_path],
        date_key="20260620",
        work_order_id="WO-TEST-ASSET-EVENT",
    )

    assert result["status"] == "succeeded"
    assert result["stats"]["event_count"] == 2
    assert result["stats"]["graph_event_count"] >= 6
    assert result["stats"]["causal_path_count"] >= 2
    assert result["stats"]["signal_count"] >= 2
    assert result["stats"]["changed_driver_count"] >= 1
    manifest = read_json(tmp_path / result["paths"]["manifest"])
    assert manifest["constraints"]["raw_tables_modified"] is False
    assert manifest["constraints"]["embedding_only_clustering"] is False
    assert manifest["constraints"]["single_event_schema"] == "asset_canonical_event.v1"
    assert manifest["constraints"]["event_sourcing_required"] is True
    assert manifest["constraints"]["graph_layers"] == ["event", "industry", "causal"]
    assert manifest["constraints"]["driver_update_source"] == "signal_updates_only"
    assert manifest["constraints"]["driver_state_machine_required"] is True
    assert manifest["constraints"]["narrative_source"] == "driver_state_only"

    db_path = tmp_path / result["paths"]["event_db"]
    with sqlite3.connect(db_path) as conn:
        event_count = conn.execute("SELECT COUNT(*) FROM event_table").fetchone()[0]
        event_log_count = conn.execute("SELECT COUNT(*) FROM event_log WHERE stream_type = 'event'").fetchone()[0]
        graph_log_count = conn.execute("SELECT COUNT(*) FROM graph_event_log").fetchone()[0]
        causal_path_count = conn.execute("SELECT COUNT(*) FROM causal_path_table").fetchone()[0]
        signal_count = conn.execute("SELECT COUNT(*) FROM signal_table").fetchone()[0]
        mapping_count = conn.execute("SELECT COUNT(*) FROM event_node_mapping").fetchone()[0]
        driver_count = conn.execute("SELECT COUNT(*) FROM driver_state_table").fetchone()[0]
        driver_event_count = conn.execute("SELECT COUNT(*) FROM driver_events").fetchone()[0]
        narrative_count = conn.execute("SELECT COUNT(*) FROM theme_narrative_outputs").fetchone()[0]
    assert event_count == 2
    assert event_log_count == 2
    assert graph_log_count >= 6
    assert causal_path_count >= 2
    assert signal_count >= 2
    assert mapping_count >= 2
    assert driver_count >= 1
    assert driver_event_count >= 2
    assert narrative_count >= 1

    store = AssetEventStore(tmp_path, db_path=db_path)
    replayed = store.replay_events(asset="Copper")
    assert [event["event_id"] for event in replayed] == [event["event_id"] for event in result["events"]]


def test_pipeline_adapts_news_logic_output_to_canonical_events(tmp_path: Path) -> None:
    input_path = tmp_path / "news_logic.json"
    write_json(
        input_path,
        {
            "schema_version": "news_logic_radar.v1",
            "generated_at": "2026-06-20T08:00:00+00:00",
            "events": [
                {
                    "event_id": "NEWS-1",
                    "flash_id": "flash-1",
                    "asset": "Copper",
                    "publish_time": "2026-06-20T08:00:00+00:00",
                    "text": "美国铜关税预期升温，铜价上涨。",
                    "framework_node": "Copper.schema.Policy",
                    "direction_score": 0.8,
                }
            ],
        },
    )

    result = run_asset_event_pipeline(
        tmp_path,
        input_paths=[input_path],
        date_key="20260620",
        work_order_id="WO-TEST-NEWS-ADAPTER",
    )

    event = result["events"][0]
    assert event["schema_version"] == "asset_canonical_event.v1"
    assert event["asset"] == "Copper"
    assert event["source_id"].startswith("opinion_radar.news_logic.")
    assert event["driver_id"]
