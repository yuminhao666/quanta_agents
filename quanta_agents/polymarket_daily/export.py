from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import (
    dated_parts,
    read_json,
    relative_to_root,
    sha256_bytes,
    stable_json_dumps,
    utc_now_iso,
    write_json,
)

from .analysis import (
    build_daily_report,
    build_hotspots,
    render_markdown_report,
    resolve_focus_categories,
)
from .collector import collect_market_pools


def _date_key(value: str | None = None) -> str:
    if not value:
        return datetime.now().strftime("%Y%m%d")
    clean = value.strip().replace("-", "")
    if len(clean) != 8 or not clean.isdigit():
        raise ValueError("date must be YYYYMMDD or YYYY-MM-DD")
    return clean


def _stamp() -> str:
    return datetime.now().strftime("%H%M%S")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{Path.cwd().name}.{datetime.now().timestamp()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _json_bytes(data: Any) -> bytes:
    return stable_json_dumps(data, indent=2).encode("utf-8")


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _deferred_same_day_candidates(
    *,
    root_path: Path,
    date_root: Path,
    current_candidate_id: str,
    current_run_id: str,
    superseded_at: str,
) -> list[dict[str, Any]]:
    deferred: list[dict[str, Any]] = []
    if not date_root.exists():
        return deferred

    for manifest_path in sorted(date_root.glob("CAND-POLYMARKET-*/manifest.json")):
        payload = _read_json_object(manifest_path)
        if payload is None:
            continue

        candidate_id = str(payload.get("candidate_id") or manifest_path.parent.name).strip()
        if not candidate_id or candidate_id == current_candidate_id:
            continue

        deferred.append(
            {
                "candidate_id": candidate_id,
                "run_id": str(payload.get("run_id") or "").strip(),
                "status": "superseded_by_newer_same_day_run",
                "reason": "newer_same_day_polymarket_publish",
                "superseded_by_candidate_id": current_candidate_id,
                "superseded_by_run_id": current_run_id,
                "superseded_at": superseded_at,
                "manifest_path": relative_to_root(manifest_path, root_path),
            }
        )

    return deferred


def _raw_manifest(
    *,
    root_path: Path,
    run_id: str,
    date_key: str,
    raw_dir: Path,
    raw_snapshot: dict[str, Any],
) -> dict[str, Any]:
    raw_bytes = _json_bytes(raw_snapshot)
    return {
        "schema_version": "raw_manifest.polymarket_cli_snapshot.v1",
        "raw_id": run_id,
        "source_path": relative_to_root(raw_dir, root_path),
        "source_uri": "polymarket://cli/markets",
        "source_name": "polymarket",
        "source_display_name": "Polymarket",
        "source_type": "prediction_market_snapshot",
        "published_at": raw_snapshot.get("collected_at"),
        "source_time": raw_snapshot.get("collected_at"),
        "ingested_at": utc_now_iso(),
        "crawled_at": raw_snapshot.get("collected_at"),
        "fetch_status": "success" if not raw_snapshot.get("errors") else "partial_success",
        "processing_status": "raw_saved",
        "permission_status": "public_cli_output",
        "permission_scope": "public_polymarket_market_data",
        "sha256": sha256_bytes(raw_bytes),
        "hash": {
            "source_sha256": sha256_bytes(raw_bytes),
            "dedupe_key": sha256_bytes(f"polymarket|{date_key}|{run_id}".encode("utf-8")),
        },
        "lineage": {
            "parent_ids": [],
            "source_adapter": "polymarket_cli.v1",
            "cli": raw_snapshot.get("cli"),
            "commands": [pool.get("command") for pool in raw_snapshot.get("pools") or []],
        },
        "artifacts": {
            "snapshot": "snapshot.json",
            "pools_dir": "pools",
        },
        "summary": {
            "date": date_key,
            "pool_count": len(raw_snapshot.get("pools") or []),
            "errors": raw_snapshot.get("errors") or [],
        },
    }


def publish_polymarket_daily(
    *,
    root: str | Path | None = None,
    cli: str | Path | None = None,
    report_date: str | None = None,
    limit: int = 80,
    top: int = 25,
    focus: str | list[str] | tuple[str, ...] | None = "finance,politics,macro",
    use_llm: bool = True,
    timeout: int = 90,
) -> dict[str, Any]:
    started_at = utc_now_iso()
    date_key = _date_key(report_date)
    yyyy, mm, dd = dated_parts(date_key)
    stamp = _stamp()
    run_id = f"RUN-{date_key}-{stamp}-POLYMARKET-DAILY"
    candidate_id = f"CAND-POLYMARKET-{date_key}-{stamp}"
    review_package_id = f"RP-{date_key}-POLYMARKET"
    root_path = quanta_data_root(root)

    raw_snapshot = collect_market_pools(cli=cli, limit=limit, timeout=timeout)
    hotspots = build_hotspots(raw_snapshot, top=top, focus=focus)
    hotspots["date"] = date_key
    hotspots["run_id"] = run_id
    report = build_daily_report(hotspots, report_date=date_key, use_llm=use_llm)
    report["run_id"] = run_id
    report["candidate_id"] = candidate_id
    markdown = render_markdown_report(report, hotspots)

    raw_dir = root_path / "raw_objects" / "market_data" / "polymarket_cli" / yyyy / mm / dd / run_id
    raw_snapshot_path = raw_dir / "snapshot.json"
    write_json(raw_snapshot_path, raw_snapshot)
    pools_dir = raw_dir / "pools"
    for pool in raw_snapshot.get("pools") or []:
        pool_name = str(pool.get("pool") or "pool")
        write_json(pools_dir / f"{pool_name}.json", pool.get("payload"))
    raw_manifest = _raw_manifest(
        root_path=root_path,
        run_id=run_id,
        date_key=date_key,
        raw_dir=raw_dir,
        raw_snapshot=raw_snapshot,
    )
    manifest_by_source = (
        root_path / "raw_manifests" / "by_source" / "polymarket_cli" / f"{run_id}.json"
    )
    manifest_by_date = root_path / "raw_manifests" / "by_date" / yyyy / mm / dd / f"{run_id}.json"
    write_json(manifest_by_source, raw_manifest)
    write_json(manifest_by_date, raw_manifest)

    candidate_dir = (
        root_path
        / "agent_workspace"
        / "candidates"
        / "polymarket_daily"
        / yyyy
        / mm
        / dd
        / candidate_id
    )
    hotspots_path = candidate_dir / "hotspots.json"
    report_json_path = candidate_dir / "daily_report.json"
    report_md_path = candidate_dir / "daily_report.md"
    candidate_manifest_path = candidate_dir / "manifest.json"
    write_json(hotspots_path, hotspots)
    write_json(report_json_path, report)
    _write_text(report_md_path, markdown)

    latest_dir = root_path / "agent_workspace" / "candidates" / "polymarket_daily" / "latest"
    latest_hotspots = latest_dir / "hotspots.json"
    latest_report_json = latest_dir / "daily_report.json"
    latest_report_md = latest_dir / "daily_report.md"
    latest_manifest = latest_dir / "manifest.json"
    write_json(latest_hotspots, hotspots)
    write_json(latest_report_json, report)
    _write_text(latest_report_md, markdown)

    review_dir = (
        root_path / "agent_workspace" / "review_packages" / yyyy / mm / dd / review_package_id
    )
    review_manifest_path = review_dir / "manifest.json"
    review_report_path = review_dir / "daily_report.md"
    review_hotspots_path = review_dir / "hotspots.json"
    _write_text(review_report_path, markdown)
    write_json(review_hotspots_path, hotspots)

    candidate_manifest = {
        "schema_version": "polymarket_daily_candidate_manifest.v1",
        "status": "candidate",
        "candidate_id": candidate_id,
        "run_id": run_id,
        "date": date_key,
        "generated_at": utc_now_iso(),
        "raw_manifest": relative_to_root(manifest_by_source, root_path),
        "outputs": {
            "hotspots": relative_to_root(hotspots_path, root_path),
            "daily_report_json": relative_to_root(report_json_path, root_path),
            "daily_report_md": relative_to_root(report_md_path, root_path),
        },
        "latest": {
            "hotspots": relative_to_root(latest_hotspots, root_path),
            "daily_report_json": relative_to_root(latest_report_json, root_path),
            "daily_report_md": relative_to_root(latest_report_md, root_path),
        },
        "requires_review": True,
    }
    write_json(candidate_manifest_path, candidate_manifest)
    write_json(latest_manifest, candidate_manifest)

    synced_at = utc_now_iso()
    deferred_candidates = _deferred_same_day_candidates(
        root_path=root_path,
        date_root=candidate_dir.parent,
        current_candidate_id=candidate_id,
        current_run_id=run_id,
        superseded_at=synced_at,
    )
    review_manifest = {
        "review_package_id": review_package_id,
        "status": "pending_review",
        "report_date": date_key,
        "asset_class": "prediction_market",
        "candidate_ids": [candidate_id],
        "deferred_candidate_ids": [item["candidate_id"] for item in deferred_candidates],
        "deferred_candidates": deferred_candidates,
        "source_files": [
            {
                "role": "daily_report_md",
                "path": relative_to_root(review_report_path, root_path),
                "sha256": sha256_bytes(markdown.encode("utf-8")),
            },
            {
                "role": "hotspots",
                "path": relative_to_root(review_hotspots_path, root_path),
                "sha256": sha256_bytes(_json_bytes(hotspots)),
            },
        ],
        "input_refs": [
            {"role": "raw_manifest", "path": relative_to_root(manifest_by_source, root_path)}
        ],
        "generator": {"project": "quanta_agents", "module": "quanta_agents.polymarket_daily"},
        "synced_at": synced_at,
    }
    write_json(review_manifest_path, review_manifest)

    market_index_rows = []
    for market in hotspots.get("hotspots") or []:
        market_index_rows.append(
            {
                "run_id": run_id,
                "date": date_key,
                "generated_at": hotspots.get("generated_at"),
                "market_id": market.get("market_id"),
                "condition_id": market.get("condition_id"),
                "question": market.get("question"),
                "category": market.get("category"),
                "url": market.get("url"),
                "primary_outcome": market.get("primary_outcome"),
                "primary_probability": market.get("primary_probability"),
                "volume_24h": market.get("volume_24h"),
                "liquidity": market.get("liquidity"),
                "price_change_1d": market.get("price_change_1d"),
                "hotspot_score": market.get("hotspot_score"),
            }
        )
    market_index_path = root_path / "indexes" / "polymarket" / "market_hotspots.jsonl"
    report_index_path = root_path / "indexes" / "polymarket" / "daily_reports.jsonl"
    _write_jsonl(market_index_path, market_index_rows)
    _write_jsonl(
        report_index_path,
        [
            {
                "run_id": run_id,
                "date": date_key,
                "candidate_id": candidate_id,
                "report_path": relative_to_root(report_md_path, root_path),
                "hotspots_path": relative_to_root(hotspots_path, root_path),
                "analysis_method": report.get("analysis_method"),
                "generated_at": report.get("generated_at"),
            }
        ],
    )

    run_manifest_path = (
        root_path / "agent_workspace" / "runs" / yyyy / mm / dd / run_id / "manifest.json"
    )
    run_manifest = {
        "run_id": run_id,
        "run_type": "polymarket_daily",
        "status": "succeeded",
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "inputs": [
            {
                "source_type": "polymarket_cli",
                "cli": raw_snapshot.get("cli"),
                "raw_manifest": relative_to_root(manifest_by_source, root_path),
                "limit_per_pool": limit,
                "focus": {
                    "requested": focus,
                    "categories": (
                        sorted(resolve_focus_categories(focus))
                        if resolve_focus_categories(focus) is not None
                        else ["all"]
                    ),
                },
            }
        ],
        "outputs": [
            {
                "artifact_type": "polymarket_hotspots",
                "path": relative_to_root(hotspots_path, root_path),
            },
            {
                "artifact_type": "polymarket_daily_report",
                "path": relative_to_root(report_md_path, root_path),
            },
            {"artifact_type": "review_package", "path": relative_to_root(review_dir, root_path)},
            {
                "artifact_type": "market_hotspot_index",
                "path": relative_to_root(market_index_path, root_path),
            },
        ],
        "requires_review": True,
        "generator": {"project": "quanta_agents", "module": "quanta_agents.polymarket_daily"},
    }
    write_json(run_manifest_path, run_manifest)

    return {
        "date": date_key,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "review_package_id": review_package_id,
        "paths": {
            "raw_snapshot": str(raw_snapshot_path),
            "raw_manifest": str(manifest_by_source),
            "hotspots": str(hotspots_path),
            "daily_report_json": str(report_json_path),
            "daily_report_md": str(report_md_path),
            "latest_report_md": str(latest_report_md),
            "review_package": str(review_dir),
            "run_manifest": str(run_manifest_path),
            "market_index": str(market_index_path),
            "report_index": str(report_index_path),
        },
        "source_paths": {
            "hotspots": relative_to_root(hotspots_path, root_path),
            "daily_report_md": relative_to_root(report_md_path, root_path),
            "latest_report_md": relative_to_root(latest_report_md, root_path),
            "review_package": relative_to_root(review_dir, root_path),
        },
        "hotspots": hotspots,
        "report": report,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Collect Polymarket hotspots and publish a daily report into quanta_data."
    )
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--cli", help="Path to polymarket CLI. Defaults to POLYMARKET_CLI or PATH.")
    parser.add_argument("--date", help="Report date, YYYYMMDD or YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--limit", type=int, default=80, help="Markets per CLI sorted pool.")
    parser.add_argument("--top", type=int, default=25, help="Hotspots to keep in report payload.")
    parser.add_argument(
        "--focus",
        default="finance,politics,macro",
        help=(
            "Comma-separated focus areas/categories. Default: finance,politics,macro. "
            "Use 'all' to disable filtering."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=90,
        help="Per-command CLI timeout in seconds.",
    )
    parser.add_argument("--no-llm", action="store_true", help="Use rule-based report only.")
    args = parser.parse_args(argv)

    result = publish_polymarket_daily(
        root=args.quanta_root,
        cli=args.cli,
        report_date=args.date,
        limit=args.limit,
        top=args.top,
        focus=args.focus,
        use_llm=not args.no_llm,
        timeout=args.timeout,
    )
    stats = result["hotspots"]["stats"]
    print(f"Polymarket 日报完成：{result['date']} run={result['run_id']}")
    print(
        f"样本市场 {stats['unique_markets']} 个，"
        f"聚焦后 {stats['focused_markets']} 个，热点 {stats['hotspot_count']} 个"
    )
    print(f"日报 → {result['paths']['daily_report_md']}")
    print(f"latest → {result['paths']['latest_report_md']}")


if __name__ == "__main__":
    main()
