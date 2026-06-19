from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import dated_parts, relative_to_root, utc_now_iso, write_json

from . import config, db, news_logic, report, service


DEFAULT_WINDOWS = (24, 48, 72)


def _parse_windows(raw: str | None) -> list[int]:
    if not raw:
        return list(DEFAULT_WINDOWS)
    values = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        value = int(text)
        if value <= 0:
            raise ValueError("windows must be positive hours")
        values.append(value)
    return values or list(DEFAULT_WINDOWS)


def build_payload(
    *,
    windows: list[int] | tuple[int, ...] = DEFAULT_WINDOWS,
    use_llm: bool = True,
    top: int = 18,
    timeline_days: int = 7,
    timeline_top: int = 8,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = now or datetime.now()
    snapshots = {}
    for hours in windows:
        print(f"  计算窗口 {hours}h{'（LLM命名）' if use_llm else ''} ...")
        snapshots[str(hours)] = service.snapshot(hours=hours, top=top, name_llm=use_llm)
    print("  计算时间线 ...")
    timeline = service.timeline(days=timeline_days, top=timeline_top)
    health = db.ping()
    return {
        "schema_version": "opinion_radar.v1",
        "status": "candidate",
        "generated_at": generated_at.isoformat(sep=" "),
        "source_max_time": health.get("max_time"),
        "flash_total_db": health.get("count"),
        "llm": {
            "enabled": config.LLM_ENABLED and use_llm,
            "provider": config.RADAR_LLM_PROVIDER,
            "model": config.LLM_MODEL,
        },
        "default_window": str(windows[0]),
        "windows": snapshots,
        "timeline": timeline,
        "health": health,
    }


def export_to_quanta(
    *,
    root: str | Path | None = None,
    windows: list[int] | tuple[int, ...] = DEFAULT_WINDOWS,
    use_llm: bool = True,
    top: int = 18,
    timeline_days: int = 7,
    timeline_top: int = 8,
    include_news_logic: bool = True,
    include_report: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = now or datetime.now()
    payload = build_payload(
        windows=windows,
        use_llm=use_llm,
        top=top,
        timeline_days=timeline_days,
        timeline_top=timeline_top,
        now=generated_at,
    )
    root_path = quanta_data_root(root)
    news_logic_result = None
    if include_news_logic:
        try:
            news_logic_result = news_logic.publish_news_logic_radar(
                root_path,
                hours=max(windows) if windows else 24,
                use_llm_assessment=use_llm,
            )
            payload["news_logic"] = {
                "latest": relative_to_root(Path(news_logic_result["latest"]), root_path),
                "archive": relative_to_root(Path(news_logic_result["archive"]), root_path),
                "stats": news_logic_result["payload"]["stats"],
                "logic_context": news_logic_result["payload"].get("logic_context"),
            }
        except Exception as exc:
            payload["news_logic"] = {"error": str(exc)}

    date_key = generated_at.strftime("%Y%m%d")
    yyyy, mm, dd = dated_parts(date_key)
    stamp = generated_at.strftime("%H%M%S")
    base = root_path / "agent_workspace" / "candidates" / "opinion_radar"
    latest_path = base / "latest" / "radar.json"
    archive_path = base / yyyy / mm / dd / f"radar-{stamp}.json"
    run_id = f"RUN-{date_key}-{stamp}-OPINION-RADAR"
    run_manifest_path = (
        root_path / "agent_workspace" / "runs" / yyyy / mm / dd / run_id / "manifest.json"
    )

    report_result = None
    if include_report:
        try:
            report_result = report.publish_market_radar_report(
                root_path,
                radar_payload=payload,
                news_logic_payload=(news_logic_result or {}).get("payload")
                if news_logic_result
                else None,
                now=generated_at,
            )
            payload["market_report"] = {
                "latest_json": relative_to_root(Path(report_result["latest_json"]), root_path),
                "latest_markdown": relative_to_root(
                    Path(report_result["latest_markdown"]), root_path
                ),
                "archive_json": relative_to_root(Path(report_result["archive_json"]), root_path),
                "archive_markdown": relative_to_root(
                    Path(report_result["archive_markdown"]), root_path
                ),
                "summary": report_result["payload"].get("summary") or {},
            }
        except Exception as exc:
            payload["market_report"] = {"error": str(exc)}

    write_json(latest_path, payload)
    write_json(archive_path, payload)
    run_manifest = {
        "run_id": run_id,
        "run_type": "opinion_radar_export",
        "status": "succeeded",
        "started_at": utc_now_iso(),
        "finished_at": utc_now_iso(),
        "inputs": [
            {
                "source_type": "mysql",
                "database": config.MYSQL["database"],
                "table": config.FLASH_TABLE,
                "health": payload["health"],
            }
        ],
        "outputs": [
            {"artifact_type": "opinion_radar", "path": relative_to_root(latest_path, root_path)},
            {
                "artifact_type": "opinion_radar_archive",
                "path": relative_to_root(archive_path, root_path),
            },
        ],
        "requires_review": True,
        "generator": {"project": "quanta_agents", "module": "quanta_agents.opinion_radar"},
    }
    if report_result:
        run_manifest["outputs"].extend(
            [
                {
                    "artifact_type": "market_sentiment_radar_report",
                    "path": relative_to_root(Path(report_result["latest_json"]), root_path),
                },
                {
                    "artifact_type": "market_sentiment_radar_report_markdown",
                    "path": relative_to_root(Path(report_result["latest_markdown"]), root_path),
                },
            ]
        )
    write_json(run_manifest_path, run_manifest)

    return {
        "latest": str(latest_path),
        "archive": str(archive_path),
        "run_manifest": str(run_manifest_path),
        "run_id": run_id,
        "payload": payload,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Compute opinion radar and export it into quanta_data."
    )
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM naming and synthesis.")
    parser.add_argument(
        "--windows",
        help="Comma-separated rolling windows in hours, e.g. 24,48,72.",
    )
    parser.add_argument("--top", type=int, default=18, help="Top buckets per snapshot.")
    parser.add_argument("--timeline-days", type=int, default=7)
    parser.add_argument("--timeline-top", type=int, default=8)
    parser.add_argument(
        "--skip-news-logic",
        action="store_true",
        help="Do not build news-to-framework logic radar.",
    )
    parser.add_argument(
        "--skip-report",
        action="store_true",
        help="Do not build market radar report.",
    )
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    args = parser.parse_args(argv)

    result = export_to_quanta(
        root=args.quanta_root,
        windows=_parse_windows(args.windows),
        use_llm=not args.no_llm,
        top=args.top,
        timeline_days=args.timeline_days,
        timeline_top=args.timeline_top,
        include_news_logic=not args.skip_news_logic,
        include_report=not args.skip_report,
    )
    print("-" * 56)
    for hours, snapshot in result["payload"]["windows"].items():
        stats = snapshot["stats"]
        print(
            f"  {hours}h: 快讯{stats['flash_total']} "
            f"重要{stats['important_total']} 主题{stats['theme_count']}"
        )
    print(f"导出 → {result['latest']}")


if __name__ == "__main__":
    main()
