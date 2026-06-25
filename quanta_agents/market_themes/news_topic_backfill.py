from __future__ import annotations

import argparse
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import dated_parts, relative_to_root, utc_now_iso, write_json
from quanta_agents.market_themes import recent_news_topics, topic_evolution
from quanta_agents.opinion_radar import db, filters


SCHEMA_VERSION = "news_topic_backfill.v1"
AGENT_VERSION = "0.1.0"

_TAG_RE = re.compile(r"<[^>]+>")
_ROUNDUP_RE = re.compile(r"金十数据整理|每日.+要闻|重要新闻汇总|市场要闻速递|动态汇总|一览")


def _clean_text(value: Any, limit: int | None = None) -> str:
    text = str(value or "").strip()
    text = _TAG_RE.sub("", text).replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        return text[: max(limit - 1, 0)].rstrip() + "…"
    return text


def _flash_text(flash: dict[str, Any], limit: int | None = None) -> str:
    return _clean_text(flash.get("title") or flash.get("content") or "", limit)


def _flash_id(flash: dict[str, Any]) -> str:
    return _clean_text(flash.get("flash_id") or flash.get("id"), 120)


def _is_roundup(flash: dict[str, Any]) -> bool:
    return bool(_ROUNDUP_RE.search(_flash_text(flash)))


def _rank_flash(flash: dict[str, Any]) -> tuple[int, int, int, str]:
    text = _flash_text(flash)
    return (
        int(flash.get("important") or 0),
        0 if _is_roundup(flash) else 1,
        1 if filters.has_signal(text) else 0,
        str(flash.get("publish_time") or ""),
    )


def _select_top_flashes(flashes: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    ranked = sorted(flashes, key=_rank_flash, reverse=True)
    selected = []
    seen_ids = set()
    seen_texts = set()
    for flash in ranked:
        flash_id = _flash_id(flash)
        text = _flash_text(flash, 420)
        text_key = text[:160]
        if not text or flash_id in seen_ids or text_key in seen_texts:
            continue
        seen_ids.add(flash_id)
        seen_texts.add(text_key)
        selected.append(
            {
                "flash_id": flash_id,
                "publish_time": flash.get("publish_time"),
                "important": int(flash.get("important") or 0),
                "summary": text,
                "title": _clean_text(flash.get("title"), 220),
                "url": flash.get("url") or "",
                "channel": flash.get("channel") or "",
            }
        )
        if len(selected) >= limit:
            break
    return selected


def _window_iter(start: datetime, end: datetime, *, window_days: int) -> list[tuple[datetime, datetime]]:
    windows = []
    cursor = start
    step = timedelta(days=window_days)
    while cursor < end:
        window_end = min(cursor + step, end)
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


def _write_window_brief(
    *,
    root: Path,
    run_dir: Path,
    window_start: datetime,
    window_end: datetime,
    raw_count: int,
    kept_flashes: list[dict[str, Any]],
    selected_flashes: list[dict[str, Any]],
) -> Path:
    brief_path = run_dir / "briefs" / f"news-brief-{window_start:%Y%m%d}-{window_end:%Y%m%d}.json"
    write_json(
        brief_path,
        {
            "schema_version": "backfill_news_brief.v1",
            "generated_at": utc_now_iso(),
            "title": f"历史新闻主题回填 {window_start:%Y-%m-%d} - {window_end:%Y-%m-%d}",
            "time_window": {
                "start_time": window_start.isoformat(sep=" "),
                "end_time": window_end.isoformat(sep=" "),
            },
            "stats": {
                "raw_flash_count": raw_count,
                "kept_flash_count": len(kept_flashes),
                "important_flash_count": sum(int(flash.get("important") or 0) for flash in kept_flashes),
                "selected_flash_count": len(selected_flashes),
            },
            "top_flashes": selected_flashes,
            "source_refs": [
                {
                    "source": "mysql_flash_table",
                    "path": "RADAR_FLASH_TABLE",
                    "window_start": window_start.isoformat(sep=" "),
                    "window_end": window_end.isoformat(sep=" "),
                }
            ],
            "next_step": {
                "input_role": "historical_news_intermediate",
                "consumer": "market_themes.recent_news_topics",
            },
        },
    )
    return brief_path


def run_news_topic_backfill(
    *,
    root: str | Path | None = None,
    days: int = 60,
    window_days: int = 3,
    flash_limit: int = 45,
    topics_per_window: int = 12,
    llm_provider: str | None = None,
    llm_timeout: int = 120,
    require_llm: bool = False,
    end_time: datetime | None = None,
    continue_on_error: bool = True,
) -> dict[str, Any]:
    if days <= 0:
        raise ValueError("days must be positive")
    if window_days <= 0:
        raise ValueError("window_days must be positive")

    root_path = quanta_data_root(root)
    latest = end_time or db.latest_time()
    if latest is None:
        raise RuntimeError("MySQL flash table has no latest publish_time")
    start = latest - timedelta(days=days)
    end = latest + timedelta(seconds=1)
    date_key = latest.strftime("%Y%m%d")
    yyyy, mm, dd = dated_parts(date_key)
    run_id = f"RUN-NEWS-TOPIC-BACKFILL-{start:%Y%m%d}-{latest:%Y%m%d}-{datetime.now():%H%M%S}"
    run_dir = (
        root_path
        / "agent_workspace"
        / "runs"
        / "market_topics"
        / "news_topic_backfill"
        / yyyy
        / mm
        / dd
        / run_id
    )

    window_summaries = []
    topic_run_ids = []
    errors = []
    totals = {
        "raw_flash_count": 0,
        "kept_flash_count": 0,
        "important_flash_count": 0,
        "selected_flash_count": 0,
    }

    for window_start, window_end in _window_iter(start, end, window_days=window_days):
        try:
            raw_flashes = db.fetch_flashes(window_start, window_end)
            kept_flashes = [flash for flash in raw_flashes if filters.keep_flash(flash)]
            selected_flashes = _select_top_flashes(kept_flashes, limit=flash_limit)
            totals["raw_flash_count"] += len(raw_flashes)
            totals["kept_flash_count"] += len(kept_flashes)
            totals["important_flash_count"] += sum(int(flash.get("important") or 0) for flash in kept_flashes)
            totals["selected_flash_count"] += len(selected_flashes)

            brief_path = _write_window_brief(
                root=root_path,
                run_dir=run_dir,
                window_start=window_start,
                window_end=window_end,
                raw_count=len(raw_flashes),
                kept_flashes=kept_flashes,
                selected_flashes=selected_flashes,
            )
            summary: dict[str, Any] = {
                "window_start": window_start.isoformat(sep=" "),
                "window_end": window_end.isoformat(sep=" "),
                "raw_flash_count": len(raw_flashes),
                "kept_flash_count": len(kept_flashes),
                "important_flash_count": sum(int(flash.get("important") or 0) for flash in kept_flashes),
                "selected_flash_count": len(selected_flashes),
                "brief_path": relative_to_root(brief_path, root_path),
                "status": "skipped_empty" if not selected_flashes else "pending",
            }
            if selected_flashes:
                result = recent_news_topics.publish_recent_news_topics(
                    root=root_path,
                    half_day_path=brief_path,
                    include_hourly=False,
                    top=topics_per_window,
                    llm_provider=llm_provider,
                    llm_timeout=llm_timeout,
                    require_llm=require_llm,
                    now=window_end - timedelta(seconds=1),
                )
                payload = result["payload"]
                topic_run_ids.append(result["run_id"])
                summary.update(
                    {
                        "status": "succeeded",
                        "topic_run_id": result["run_id"],
                        "topic_count": len(payload.get("topic_nodes") or []),
                        "membership_count": len(payload.get("topic_memberships") or []),
                        "invalid_ref_count": payload.get("extraction", {}).get("invalid_ref_count"),
                        "method": payload.get("extraction", {}).get("method"),
                    }
                )
            window_summaries.append(summary)
        except Exception as exc:
            error = {
                "window_start": window_start.isoformat(sep=" "),
                "window_end": window_end.isoformat(sep=" "),
                "error": str(exc)[:500],
            }
            errors.append(error)
            window_summaries.append({**error, "status": "failed"})
            if not continue_on_error:
                raise

    evolution_result = topic_evolution.publish_topic_evolution(root=root_path, run_ids=topic_run_ids)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "partial" if errors else "succeeded",
        "run_id": run_id,
        "generator": {
            "name": "news_topic_backfill",
            "version": AGENT_VERSION,
        },
        "started_at": utc_now_iso(),
        "finished_at": utc_now_iso(),
        "parameters": {
            "days": days,
            "window_days": window_days,
            "flash_limit": flash_limit,
            "topics_per_window": topics_per_window,
            "llm_provider": llm_provider or "",
            "require_llm": require_llm,
        },
        "window": {
            "start": start.isoformat(sep=" "),
            "end": latest.isoformat(sep=" "),
        },
        "totals": totals,
        "window_count": len(window_summaries),
        "topic_run_count": len(topic_run_ids),
        "topic_run_ids": topic_run_ids,
        "windows": window_summaries,
        "errors": errors,
        "outputs": {
            "run_dir": relative_to_root(run_dir, root_path),
            "topic_evolution_read_model": relative_to_root(Path(evolution_result["latest_json"]), root_path),
            "topic_evolution_markdown": relative_to_root(Path(evolution_result["latest_markdown"]), root_path),
        },
    }
    write_json(run_dir / "manifest.json", manifest)
    latest_dir = root_path / "agent_workspace" / "candidates" / "market_topics" / "latest"
    write_json(latest_dir / "news-topic-backfill-manifest.json", manifest)
    return {
        "run_id": run_id,
        "manifest": manifest,
        "manifest_path": str(run_dir / "manifest.json"),
        "latest_manifest_path": str(latest_dir / "news-topic-backfill-manifest.json"),
        "topic_evolution_json": evolution_result["latest_json"],
        "topic_evolution_markdown": evolution_result["latest_markdown"],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Backfill MarketTopic extraction from MySQL news flashes.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--window-days", type=int, default=3)
    parser.add_argument("--flash-limit", type=int, default=45)
    parser.add_argument("--topics-per-window", type=int, default=12)
    parser.add_argument("--llm-provider", help="LLM provider, e.g. deepseek or m3.")
    parser.add_argument("--llm-timeout", type=int, default=120)
    parser.add_argument("--require-llm", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    args = parser.parse_args(argv)

    result = run_news_topic_backfill(
        root=args.quanta_root,
        days=args.days,
        window_days=args.window_days,
        flash_limit=args.flash_limit,
        topics_per_window=args.topics_per_window,
        llm_provider=args.llm_provider,
        llm_timeout=args.llm_timeout,
        require_llm=args.require_llm,
        continue_on_error=not args.stop_on_error,
    )
    manifest = result["manifest"]
    print(f"run_id: {result['run_id']}")
    print(f"manifest: {result['manifest_path']}")
    print(f"latest_manifest: {result['latest_manifest_path']}")
    print(f"topic_evolution_json: {result['topic_evolution_json']}")
    print(f"topic_evolution_markdown: {result['topic_evolution_markdown']}")
    print(f"window: {manifest['window']['start']} -> {manifest['window']['end']}")
    print(f"window_count: {manifest['window_count']}")
    print(f"topic_run_count: {manifest['topic_run_count']}")
    print(f"totals: {manifest['totals']}")
    print(f"errors: {len(manifest['errors'])}")


if __name__ == "__main__":
    main()
