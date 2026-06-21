from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanta_agents.adapters.news_logic_adapter import register_news_logic_payload
from quanta_agents.adapters.report_adapter import register_report
from quanta_agents.adapters.research_signal_adapter import register_research_signal_payload
from quanta_agents.adapters.run_manifest_adapter import register_run_manifest
from quanta_agents.adapters.theme_anchor_adapter import register_theme_anchor_payload
from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import atomic_write_text, dated_parts, read_json, relative_to_root, write_json
from quanta_agents.repositories import CatalogRepository


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _repository(args: argparse.Namespace) -> CatalogRepository:
    return CatalogRepository(
        args.root or None,
        db_path=args.catalog_path or None,
        dry_run=getattr(args, "dry_run", False),
    )


def init_catalog(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Initialize the Quanta Research Object Catalog SQLite database.")
    parser.add_argument("--root", default="", help="quanta_data root.")
    parser.add_argument("--catalog-path", default="", help="Override QUANTA_OBJECT_CATALOG_PATH.")
    args = parser.parse_args(argv)
    repo = _repository(args)
    repo.migrate()
    _json_print({"status": "initialized", "catalog_path": str(repo.db_path), "schema_version": 1})


def catalog_status(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Show Research Object Catalog status and table counts.")
    parser.add_argument("--root", default="", help="quanta_data root.")
    parser.add_argument("--catalog-path", default="", help="Override QUANTA_OBJECT_CATALOG_PATH.")
    args = parser.parse_args(argv)
    repo = _repository(args)
    _json_print({"catalog_path": str(repo.db_path), "counts": repo.table_counts()})


def _register_payload(repo: CatalogRepository, kind: str, payload: dict[str, Any], *, args: argparse.Namespace) -> dict[str, Any]:
    if kind == "news_logic":
        return register_news_logic_payload(repo, payload)
    if kind == "research_signal":
        return register_research_signal_payload(repo, payload)
    if kind == "theme_anchor":
        return register_theme_anchor_payload(repo, payload)
    if kind == "run_manifest":
        return register_run_manifest(repo, payload, manifest_uri=args.path)
    if kind == "report":
        return register_report(
            repo,
            payload,
            report_id=args.report_id or None,
            content_uri=args.path,
            dependency_object_ids=args.dependency_id or [],
            agent_run_id=args.agent_run_id or None,
        )
    raise ValueError(f"unsupported kind: {kind}")


def catalog_register(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Register one existing artifact into the sidecar Object Catalog.")
    parser.add_argument("--root", default="", help="quanta_data root.")
    parser.add_argument("--catalog-path", default="", help="Override QUANTA_OBJECT_CATALOG_PATH.")
    parser.add_argument("--kind", required=True, choices=["news_logic", "research_signal", "theme_anchor", "report", "run_manifest"])
    parser.add_argument("--path", required=True, help="JSON artifact path.")
    parser.add_argument("--report-id", default="", help="Report id when --kind report.")
    parser.add_argument("--dependency-id", action="append", default=[], help="Dependency object id for --kind report.")
    parser.add_argument("--agent-run-id", default="", help="Agent run id for report dependencies.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and summarize without writing to SQLite.")
    args = parser.parse_args(argv)
    repo = _repository(args)
    payload = read_json(Path(args.path).expanduser())
    if not isinstance(payload, dict):
        raise ValueError("--path must contain a JSON object")
    result = _register_payload(repo, args.kind, payload, args=args)
    _json_print({"status": result.get("status", "succeeded"), "dry_run": args.dry_run, "result": result})


def _candidate_files(root: Path, limit: int) -> list[tuple[str, Path]]:
    patterns = [
        ("theme_anchor", "agent_workspace/candidates/theme_anchor/*/*/*/CAND-THEME-ANCHOR-*/theme_anchors.json"),
        ("research_signal", "agent_workspace/candidates/signal_map/*/*/*/CAND-SIGNAL-MAP-*/research_signals.json"),
        ("news_logic", "agent_workspace/candidates/opinion_radar/news_logic/latest/news-logic.json"),
        ("run_manifest", "agent_workspace/runs/*/*/*/*/run_manifest.json"),
    ]
    out: list[tuple[str, Path]] = []
    for kind, pattern in patterns:
        for path in sorted(root.glob(pattern)):
            out.append((kind, path))
            if len(out) >= limit:
                return out
    return out


def catalog_backfill(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Backfill a small batch of existing artifacts into Object Catalog.")
    parser.add_argument("--root", default="", help="quanta_data root.")
    parser.add_argument("--catalog-path", default="", help="Override QUANTA_OBJECT_CATALOG_PATH.")
    parser.add_argument("--limit", type=int, default=20, help="Small-batch limit; no full historical scan by default.")
    parser.add_argument("--dry-run", action="store_true", help="List work without writing to SQLite.")
    args = parser.parse_args(argv)
    root = quanta_data_root(args.root or None)
    repo = _repository(args)
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    date_key = _now().strftime("%Y%m%d")
    yyyy, mm, dd = dated_parts(date_key)
    run_dir = root / "agent_workspace/runs/object_catalog" / yyyy / mm / dd / f"RUN-CATALOG-BACKFILL-{stamp}"
    processed = []
    errors = []
    for kind, path in _candidate_files(root, max(1, args.limit)):
        try:
            payload = read_json(path)
            if not isinstance(payload, dict):
                raise ValueError("artifact is not a JSON object")
            result = _register_payload(repo, kind, payload, args=argparse.Namespace(path=str(path), report_id="", dependency_id=[], agent_run_id=""))
            processed.append({"kind": kind, "path": relative_to_root(path, root), "result": result})
        except Exception as exc:
            errors.append({"kind": kind, "path": relative_to_root(path, root), "error": str(exc)})
    manifest = {
        "schema_version": "object_catalog_backfill_run.v1",
        "status": "succeeded" if not errors else "partial",
        "run_id": run_dir.name,
        "generated_at": _now().isoformat(timespec="seconds"),
        "dry_run": args.dry_run,
        "catalog_path": str(repo.db_path),
        "processed_count": len(processed),
        "error_count": len(errors),
        "processed": processed,
    }
    if not args.dry_run:
        write_json(run_dir / "run_manifest.json", manifest)
        atomic_write_text(run_dir / "errors.jsonl", "\n".join(json.dumps(item, ensure_ascii=False) for item in errors))
    _json_print(manifest | {"errors": errors})


def topic_list(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="List persistent Object Catalog topics.")
    parser.add_argument("--root", default="", help="quanta_data root.")
    parser.add_argument("--catalog-path", default="", help="Override QUANTA_OBJECT_CATALOG_PATH.")
    parser.add_argument("--query", default="", help="Optional title/description query.")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    repo = _repository(args)
    _json_print({"topics": repo.list_topics(query=args.query or None, limit=args.limit)})


def topic_show(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Show a persistent topic, aliases, and recent memberships.")
    parser.add_argument("topic_id")
    parser.add_argument("--root", default="", help="quanta_data root.")
    parser.add_argument("--catalog-path", default="", help="Override QUANTA_OBJECT_CATALOG_PATH.")
    args = parser.parse_args(argv)
    repo = _repository(args)
    _json_print(
        {
            "topic": repo.get_topic(args.topic_id),
            "aliases": repo.topic_aliases(args.topic_id),
            "memberships": repo.topic_memberships(args.topic_id, limit=50),
        }
    )


def topic_timeline(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Show topic state timeline.")
    parser.add_argument("topic_id")
    parser.add_argument("--root", default="", help="quanta_data root.")
    parser.add_argument("--catalog-path", default="", help="Override QUANTA_OBJECT_CATALOG_PATH.")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    repo = _repository(args)
    _json_print({"topic_id": args.topic_id, "timeline": repo.topic_timeline(args.topic_id, limit=args.limit)})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Quanta Object Catalog CLI dispatcher.")
    parser.add_argument("command", choices=["init", "status", "register", "backfill", "topic-list", "topic-show", "topic-timeline"])
    args, rest = parser.parse_known_args(argv)
    {
        "init": init_catalog,
        "status": catalog_status,
        "register": catalog_register,
        "backfill": catalog_backfill,
        "topic-list": topic_list,
        "topic-show": topic_show,
        "topic-timeline": topic_timeline,
    }[args.command](rest)


if __name__ == "__main__":
    main()
