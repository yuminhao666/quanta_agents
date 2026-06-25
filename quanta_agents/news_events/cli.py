from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root

from .repository import NewsEventRepository, default_catalog_path
from .service import run_news_event_batch


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def init_catalog(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Initialize the news event SQLite sidecar catalog.")
    parser.add_argument("--root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--catalog-path", help="Override QUANTA_NEWS_EVENT_CATALOG_PATH.")
    args = parser.parse_args(argv)
    repo = NewsEventRepository(args.root, db_path=args.catalog_path)
    _print_json({"ok": True, "catalog_path": str(repo.db_path), "counts": repo.table_counts()})


def batch(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a small news event sidecar batch.")
    parser.add_argument("--date", help="YYYY-MM-DD. If omitted, use latest rolling window.")
    parser.add_argument("--hours", type=int, default=24, help="Rolling window hours when --date is omitted.")
    parser.add_argument("--limit", type=int, help="Max raw flashes to process.")
    parser.add_argument("--no-llm", action="store_true", help="Use deterministic fallback only.")
    parser.add_argument("--llm-batch-size", type=int, default=8, help="News items per LLM extraction call.")
    parser.add_argument("--llm-workers", type=int, default=1, help="Concurrent LLM extraction workers.")
    parser.add_argument("--dry-run", action="store_true", help="Write artifacts but do not mutate SQLite.")
    parser.add_argument("--root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--catalog-path", help="Override QUANTA_NEWS_EVENT_CATALOG_PATH.")
    args = parser.parse_args(argv)
    result = run_news_event_batch(
        root=args.root,
        date=args.date,
        hours=args.hours,
        limit=args.limit,
        use_llm=not args.no_llm,
        dry_run=args.dry_run,
        catalog_path=args.catalog_path,
        llm_batch_size=args.llm_batch_size,
        llm_workers=args.llm_workers,
    )
    print(f"新闻事件 Sidecar → {result['run_dir']}")
    _print_json(result["metrics"])


def list_events(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="List canonical news events.")
    parser.add_argument("--root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--catalog-path", help="Override QUANTA_NEWS_EVENT_CATALOG_PATH.")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    repo = NewsEventRepository(args.root, db_path=args.catalog_path, initialize=False)
    rows = repo.list_canonical_events(limit=args.limit)
    _print_json(rows)


def show_event(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Show a canonical event with links.")
    parser.add_argument("event_id")
    parser.add_argument("--root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--catalog-path", help="Override QUANTA_NEWS_EVENT_CATALOG_PATH.")
    args = parser.parse_args(argv)
    repo = NewsEventRepository(args.root, db_path=args.catalog_path, initialize=False)
    event = repo.get_canonical_event(args.event_id)
    if not event:
        raise SystemExit(f"event not found: {args.event_id}")
    _print_json(
        {
            "event": event,
            "asset_links": repo.event_asset_links(args.event_id),
            "framework_links": repo.event_framework_links(args.event_id),
            "relations": repo.event_relations(args.event_id),
        }
    )


def list_topics(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="List persistent news topics.")
    parser.add_argument("--root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--catalog-path", help="Override QUANTA_NEWS_EVENT_CATALOG_PATH.")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    repo = NewsEventRepository(args.root, db_path=args.catalog_path, initialize=False)
    _print_json(repo.list_topics(limit=args.limit))


def show_topic(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Show a persistent news topic.")
    parser.add_argument("topic_id")
    parser.add_argument("--root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--catalog-path", help="Override QUANTA_NEWS_EVENT_CATALOG_PATH.")
    args = parser.parse_args(argv)
    repo = NewsEventRepository(args.root, db_path=args.catalog_path, initialize=False)
    topic = repo.get_topic(args.topic_id)
    if not topic:
        raise SystemExit(f"topic not found: {args.topic_id}")
    _print_json({"topic": topic, "memberships": repo.topic_memberships(args.topic_id)})


def backfill(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a conservative small news event backfill.")
    parser.add_argument("--date", help="YYYY-MM-DD. If omitted, use latest rolling window.")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--limit", type=int, default=200, help="Small default; no full-history default.")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--llm-batch-size", type=int, default=8)
    parser.add_argument("--llm-workers", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--catalog-path", help="Override QUANTA_NEWS_EVENT_CATALOG_PATH.")
    args = parser.parse_args(argv)
    result = run_news_event_batch(
        root=args.root,
        date=args.date,
        hours=args.hours,
        limit=args.limit,
        use_llm=not args.no_llm,
        dry_run=args.dry_run,
        catalog_path=args.catalog_path,
        llm_batch_size=args.llm_batch_size,
        llm_workers=args.llm_workers,
    )
    print(f"新闻事件 Backfill → {result['run_dir']}")
    _print_json(result["metrics"])


def status(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Show news event sidecar status.")
    parser.add_argument("--root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--catalog-path", help="Override QUANTA_NEWS_EVENT_CATALOG_PATH.")
    args = parser.parse_args(argv)
    root = quanta_data_root(args.root)
    path = Path(args.catalog_path).expanduser() if args.catalog_path else default_catalog_path(root)
    repo = NewsEventRepository(root, db_path=path, initialize=path.exists())
    _print_json({"catalog_path": str(path), "exists": path.exists(), "counts": repo.table_counts()})


def main(argv: list[str] | None = None) -> None:
    batch(argv)


if __name__ == "__main__":
    main()
