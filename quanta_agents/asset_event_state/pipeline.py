from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanta_agents.asset_event_state.adapters import EventCanonicalAdapter, load_adapter_payloads
from quanta_agents.asset_event_state.driver import DriverStateMachine
from quanta_agents.asset_event_state.extractor import EventExtractor
from quanta_agents.asset_event_state.graph import CausalPropagationEngine, GraphLayerService
from quanta_agents.asset_event_state.mapper import EventMapper
from quanta_agents.asset_event_state.store import AssetEventStore
from quanta_agents.asset_event_state.theme import ThemeNarrativeService
from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import dated_parts, relative_to_root, utc_now_iso, write_json


DEFAULT_WORK_ORDER_ID = "WO-DEV-20260620-ASSET-EVENT-STATE"


def _sanitize_work_order(value: str) -> str:
    return re.sub(r"[^A-Z0-9_-]+", "-", value.upper()).strip("-") or "WO"


def _write_latest(
    root: Path,
    events: list[dict[str, Any]],
    drivers: list[dict[str, Any]],
    narratives: list[dict[str, Any]],
) -> dict[str, str]:
    latest_dir = root / "agent_workspace" / "candidates" / "asset_event_state" / "latest"
    events_path = latest_dir / "events.json"
    drivers_path = latest_dir / "drivers.json"
    narratives_path = latest_dir / "narratives.json"
    write_json(events_path, {"schema_version": "asset_event_set.v1", "events": events})
    write_json(drivers_path, {"schema_version": "asset_driver_state_set.v1", "drivers": drivers})
    write_json(narratives_path, {"schema_version": "asset_driver_narrative_set.v1", "narratives": narratives})
    return {
        "latest_events": relative_to_root(events_path, root),
        "latest_drivers": relative_to_root(drivers_path, root),
        "latest_narratives": relative_to_root(narratives_path, root),
    }


def run_asset_event_pipeline(
    root: str | Path | None = None,
    *,
    input_paths: list[str | Path] | None = None,
    payload: dict[str, Any] | None = None,
    date_key: str | None = None,
    work_order_id: str = DEFAULT_WORK_ORDER_ID,
    use_llm_extractor: bool = False,
    use_llm_narrative: bool = False,
    write_latest: bool = False,
    write_outputs: bool = True,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    generated_at = utc_now_iso()
    date_value = (date_key or datetime.now(timezone.utc).strftime("%Y%m%d")).replace("-", "")
    yyyy, mm, dd = dated_parts(date_value)
    stamp = datetime.now(timezone.utc).strftime("%H%M%S%f")
    run_id = f"RUN-{_sanitize_work_order(work_order_id)}-ASSET-EVENT-STATE-{stamp}"

    mapper = EventMapper(root_path)
    extractor = EventExtractor(mapper, quanta_root=root_path)
    adapter = EventCanonicalAdapter(extractor)
    store = AssetEventStore(root_path)
    driver_engine = DriverStateMachine()
    graph_service = GraphLayerService()
    causal_engine = CausalPropagationEngine()
    narrative_service = ThemeNarrativeService()

    payloads = load_adapter_payloads(input_paths or [], payload)
    if not payloads:
        raise ValueError("at least one input payload is required")

    extracted_events: list[dict[str, Any]] = []
    for item in payloads:
        extracted_events.extend(adapter.to_events(item, use_llm=use_llm_extractor))

    stored_events = store.insert_events(extracted_events)
    graph_events = graph_service.build_graph_events(stored_events)
    stored_graph_events = store.record_graph_events(graph_events)
    causal_paths = causal_engine.propagate(stored_events)
    stored_causal_paths = store.insert_causal_paths(causal_paths)
    events_by_id = {event["event_id"]: event for event in stored_events}
    signals = causal_engine.signals_from_paths(stored_causal_paths, events_by_id)
    stored_signals = store.insert_signals(signals)
    previous_drivers = store.list_drivers(limit=5000)
    drivers = driver_engine.update_from_signals(stored_signals, previous_drivers=previous_drivers)
    stored_drivers = store.upsert_drivers(drivers)
    stored_event_ids = [event["event_id"] for event in stored_events]
    new_event_ids = set(stored_event_ids)
    stored_events = [
        store.get_event(event_id) or event
        for event_id, event in zip(stored_event_ids, stored_events, strict=False)
    ]
    changed_drivers = [
        driver
        for driver in stored_drivers
        if new_event_ids.intersection(
            {str(item.get("event_id")) for item in driver.get("timeline", []) if isinstance(item, dict)}
        )
    ]
    narratives = []
    for narrative in narrative_service.generate_for_assets(changed_drivers, use_llm=use_llm_narrative):
        source_drivers = [
            driver for driver in changed_drivers if driver.get("driver_id") in set(narrative.get("driver_ids", []))
        ]
        narratives.append(store.insert_theme_narrative(narrative, source_drivers))

    input_refs = [
        {"ref_type": "input", "source_name": "asset_event_pipeline_payload", "path": str(path)}
        for path in input_paths or []
    ]

    paths: dict[str, str] = {"event_db": store.database_ref()}
    if write_outputs:
        candidate_dir = (
            root_path
            / "agent_workspace"
            / "candidates"
            / "asset_event_state"
            / yyyy
            / mm
            / dd
            / f"CAND-ASSET-EVENT-STATE-{date_value}-{stamp}"
        )
        run_dir = root_path / "agent_workspace" / "runs" / "asset_event_state" / yyyy / mm / dd / run_id
        events_path = candidate_dir / "events.json"
        graph_events_path = candidate_dir / "graph_events.json"
        causal_paths_path = candidate_dir / "causal_paths.json"
        signals_path = candidate_dir / "signals.json"
        drivers_path = candidate_dir / "drivers.json"
        narratives_path = candidate_dir / "narratives.json"
        manifest_path = candidate_dir / "manifest.json"
        run_manifest_path = run_dir / "run_manifest.json"

        write_json(events_path, {"schema_version": "asset_event_set.v1", "events": stored_events})
        write_json(
            graph_events_path,
            {"schema_version": "asset_graph_event_set.v1", "graph_events": stored_graph_events},
        )
        write_json(
            causal_paths_path,
            {"schema_version": "asset_causal_path_set.v1", "causal_paths": stored_causal_paths},
        )
        write_json(signals_path, {"schema_version": "asset_signal_update_set.v1", "signals": stored_signals})
        write_json(drivers_path, {"schema_version": "asset_driver_state_set.v1", "drivers": changed_drivers})
        write_json(narratives_path, {"schema_version": "asset_driver_narrative_set.v1", "narratives": narratives})
        manifest = {
            "schema_version": "asset_event_state_candidate_manifest.v1",
            "status": "candidate",
            "candidate_id": candidate_dir.name,
            "run_id": run_id,
            "date": date_value,
            "generated_at": generated_at,
            "work_order_id": work_order_id,
            "input_refs": input_refs,
            "outputs": {
                "events": relative_to_root(events_path, root_path),
                "graph_events": relative_to_root(graph_events_path, root_path),
                "causal_paths": relative_to_root(causal_paths_path, root_path),
                "signals": relative_to_root(signals_path, root_path),
                "drivers": relative_to_root(drivers_path, root_path),
                "narratives": relative_to_root(narratives_path, root_path),
                "event_db": store.database_ref(),
            },
            "stats": {
                "input_count": len(payloads),
                "event_count": len(stored_events),
                "graph_event_count": len(stored_graph_events),
                "causal_path_count": len(stored_causal_paths),
                "signal_count": len(stored_signals),
                "changed_driver_count": len(changed_drivers),
                "narrative_count": len(narratives),
            },
            "constraints": {
                "single_event_schema": "asset_canonical_event.v1",
                "event_sourcing_required": True,
                "graph_layers": ["event", "industry", "causal"],
                "driver_update_source": "signal_updates_only",
                "causal_path_traceability": True,
                "legacy_event_schemas_allowed": False,
                "driver_state_machine_required": True,
                "raw_tables_modified": False,
                "embedding_only_clustering": False,
                "narrative_source": "driver_state_only",
                "raw_to_event_to_driver_traceability": True,
                "gold_write": False,
            },
            "requires_review": True,
        }
        write_json(manifest_path, manifest)
        write_json(
            run_manifest_path,
            {
                "schema_version": "asset_event_state_run.v1",
                "status": "succeeded",
                "run_id": run_id,
                "run_type": "asset_event_state_pipeline",
                "date": date_value,
                "generated_at": generated_at,
                "work_order_id": work_order_id,
                "input_refs": input_refs,
                "output_refs": [
                    {
                        "ref_type": "candidate",
                        "source_name": "asset_event_state",
                        "id": candidate_dir.name,
                        "path": relative_to_root(manifest_path, root_path),
                    },
                    {"ref_type": "index", "source_name": "asset_event_state_sqlite", "path": store.database_ref()},
                ],
                "stats": manifest["stats"],
                "human_review_required": True,
            },
        )
        paths.update(
            {
                "candidate_dir": relative_to_root(candidate_dir, root_path),
                "manifest": relative_to_root(manifest_path, root_path),
                "events": relative_to_root(events_path, root_path),
                "graph_events": relative_to_root(graph_events_path, root_path),
                "causal_paths": relative_to_root(causal_paths_path, root_path),
                "signals": relative_to_root(signals_path, root_path),
                "drivers": relative_to_root(drivers_path, root_path),
                "narratives": relative_to_root(narratives_path, root_path),
                "run_manifest": relative_to_root(run_manifest_path, root_path),
            }
        )
        if write_latest:
            paths.update(_write_latest(root_path, stored_events, changed_drivers, narratives))

    return {
        "status": "succeeded",
        "run_id": run_id,
        "date": date_value,
        "events": stored_events,
        "drivers": changed_drivers,
        "narratives": narratives,
        "paths": paths,
        "stats": {
            "input_count": len(payloads),
            "event_count": len(stored_events),
            "graph_event_count": len(stored_graph_events),
            "causal_path_count": len(stored_causal_paths),
            "signal_count": len(stored_signals),
            "changed_driver_count": len(changed_drivers),
            "narrative_count": len(narratives),
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run quanta_data canonical event, driver state machine, and driver narrative pipeline."
    )
    parser.add_argument("--root", default="", help="quanta_data root. Defaults to GJ_QUANTA_DATA_ROOT discovery.")
    parser.add_argument("--input", action="append", default=[], help="JSON payload path; object/list/items[] accepted.")
    parser.add_argument("--date", default="", help="YYYYMMDD candidate date.")
    parser.add_argument("--work-order-id", default=DEFAULT_WORK_ORDER_ID)
    parser.add_argument("--use-llm-extractor", action="store_true")
    parser.add_argument("--use-llm-narrative", action="store_true")
    parser.add_argument("--write-latest", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Build in memory and DB without candidate files.")
    args = parser.parse_args(argv)
    result = run_asset_event_pipeline(
        args.root or None,
        input_paths=args.input,
        date_key=args.date or None,
        work_order_id=args.work_order_id,
        use_llm_extractor=args.use_llm_extractor,
        use_llm_narrative=args.use_llm_narrative,
        write_latest=args.write_latest,
        write_outputs=not args.dry_run,
    )
    printable = {key: value for key, value in result.items() if key not in {"events", "clusters", "narratives"}}
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
