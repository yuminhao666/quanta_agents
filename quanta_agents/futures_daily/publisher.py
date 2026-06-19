from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import (
    dated_parts,
    read_json,
    relative_to_root,
    sha256_bytes,
    utc_now_iso,
    write_json,
)

from .html import render_html_report


def _json_bytes_to_dict(data: bytes, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    return parsed


def _normalize_report_date(date: str | None, summary: dict[str, Any]) -> str:
    value = (date or str(summary.get("date") or "")).strip().replace("-", "")
    if len(value) != 8 or not value.isdigit():
        raise ValueError("report date must be YYYYMMDD or be present in summary['date']")
    return value


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def publish_report_files(
    *,
    summary_bytes: bytes,
    market_review_bytes: bytes,
    preview_html_bytes: bytes | None = None,
    report_date: str | None = None,
    root: str | Path | None = None,
    input_refs: list[dict[str, Any]] | None = None,
    generator: dict[str, Any] | None = None,
    synced_by: str = "quanta_agents.futures_daily.publisher",
) -> dict[str, Any]:
    """Publish a commodity report triplet into the gj_chainplatform quanta_data contract."""
    root_path = quanta_data_root(root)
    summary = _json_bytes_to_dict(summary_bytes, "commodity summary")
    market_review = _json_bytes_to_dict(market_review_bytes, "market review")
    date_key = _normalize_report_date(report_date, summary)
    yyyy, mm, dd = dated_parts(date_key)
    now = utc_now_iso()

    if preview_html_bytes is None:
        preview_html_bytes = render_html_report(summary, market_review, date_key, root_path).encode("utf-8")

    aw = root_path / "agent_workspace"
    market_candidate_id = f"CAND-MBRIEF-{date_key}-COMMODITY"
    analysis_candidate_id = f"CAND-ANALYSIS-{date_key}-COMMODITY"
    review_package_id = f"RP-{date_key}-COMMODITY"
    run_id = f"RUN-{date_key}-COMMODITY-IMPORT"

    market_review_path = (
        aw
        / "candidates"
        / "market_brief"
        / yyyy
        / mm
        / dd
        / market_candidate_id
        / f"{date_key}_commodity_marketreview.json"
    )
    summary_path = (
        aw
        / "candidates"
        / "asset_analysis"
        / yyyy
        / mm
        / dd
        / analysis_candidate_id
        / f"{date_key}_commodity_summary.json"
    )
    review_package_dir = aw / "review_packages" / yyyy / mm / dd / review_package_id
    preview_path = review_package_dir / "preview.html"
    rp_market_review_path = review_package_dir / "market_review.json"
    rp_summary_path = review_package_dir / "commodity_summary.json"
    manifest_path = review_package_dir / "manifest.json"
    run_manifest_path = aw / "runs" / yyyy / mm / dd / run_id / "manifest.json"

    for path, data in (
        (market_review_path, market_review_bytes),
        (summary_path, summary_bytes),
        (preview_path, preview_html_bytes),
        (rp_market_review_path, market_review_bytes),
        (rp_summary_path, summary_bytes),
    ):
        _write_bytes(path, data)

    source_files = [
        {
            "role": "market_review",
            "path": relative_to_root(market_review_path, root_path),
            "sha256": sha256_bytes(market_review_bytes),
        },
        {
            "role": "commodity_summary",
            "path": relative_to_root(summary_path, root_path),
            "sha256": sha256_bytes(summary_bytes),
        },
        {
            "role": "preview_html",
            "path": relative_to_root(preview_path, root_path),
            "sha256": sha256_bytes(preview_html_bytes),
        },
    ]
    generator_info = {
        "project": "quanta_agents",
        "module": "quanta_agents.futures_daily",
        "source_project": "commodity_report",
    }
    if generator:
        generator_info.update(generator)

    manifest = {
        "review_package_id": review_package_id,
        "status": "pending_review",
        "report_date": date_key,
        "asset_class": "futures",
        "candidate_ids": [market_candidate_id, analysis_candidate_id],
        "source_files": source_files,
        "input_refs": input_refs or [],
        "generator": generator_info,
        "synced_by": synced_by,
        "synced_at": now,
    }
    write_json(manifest_path, manifest)

    run_manifest = {
        "run_id": run_id,
        "run_type": "futures_daily_import",
        "status": "succeeded",
        "started_at": now,
        "finished_at": now,
        "inputs": input_refs or [],
        "outputs": [
            {"artifact_type": "market_brief", "candidate_id": market_candidate_id, "path": relative_to_root(market_review_path, root_path)},
            {"artifact_type": "asset_analysis", "candidate_id": analysis_candidate_id, "path": relative_to_root(summary_path, root_path)},
            {"artifact_type": "review_package", "review_package_id": review_package_id, "path": relative_to_root(review_package_dir, root_path)},
        ],
        "requires_review": True,
        "generator": generator_info,
    }
    write_json(run_manifest_path, run_manifest)

    return {
        "date": date_key,
        "candidate_ids": [market_candidate_id, analysis_candidate_id],
        "review_package_id": review_package_id,
        "run_id": run_id,
        "paths": {
            "market_review": str(market_review_path),
            "commodity_summary": str(summary_path),
            "review_package": str(review_package_dir),
            "preview_html": str(preview_path),
            "manifest": str(manifest_path),
            "run_manifest": str(run_manifest_path),
        },
        "source_paths": {
            "market_review": relative_to_root(market_review_path, root_path),
            "commodity_summary": relative_to_root(summary_path, root_path),
            "review_package": relative_to_root(review_package_dir, root_path),
        },
    }


def publish_report_paths(
    *,
    summary_path: str | Path,
    market_review_path: str | Path,
    preview_html_path: str | Path | None = None,
    report_date: str | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    summary_file = Path(summary_path).expanduser()
    market_review_file = Path(market_review_path).expanduser()
    preview_file = Path(preview_html_path).expanduser() if preview_html_path else None
    input_refs = [
        {"role": "commodity_summary", "path": str(summary_file), "sha256": sha256_bytes(summary_file.read_bytes())},
        {
            "role": "market_review",
            "path": str(market_review_file),
            "sha256": sha256_bytes(market_review_file.read_bytes()),
        },
    ]
    preview_bytes = None
    if preview_file:
        preview_bytes = preview_file.read_bytes()
        input_refs.append({"role": "preview_html", "path": str(preview_file), "sha256": sha256_bytes(preview_bytes)})

    summary = read_json(summary_file)
    return publish_report_files(
        summary_bytes=summary_file.read_bytes(),
        market_review_bytes=market_review_file.read_bytes(),
        preview_html_bytes=preview_bytes,
        report_date=report_date or str(summary.get("date") or ""),
        root=root,
        input_refs=input_refs,
        synced_by="quanta_agents.futures_daily.importer",
    )
