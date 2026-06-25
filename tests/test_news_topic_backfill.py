from __future__ import annotations

from datetime import datetime
from pathlib import Path

from quanta_agents.core.io import read_json
from quanta_agents.market_themes import news_topic_backfill


def test_news_topic_backfill_builds_window_briefs_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(news_topic_backfill.db, "latest_time", lambda: datetime(2026, 6, 10, 12, 0, 0))

    def fake_fetch(start, end):
        return [
            {
                "id": f"{start:%Y%m%d}-1",
                "flash_id": f"flash-{start:%Y%m%d}-1",
                "publish_time": start.isoformat(sep=" "),
                "important": 1,
                "title": "",
                "content": "美伊谈判与霍尔木兹通行出现新进展，原油风险溢价变化。",
                "url": "https://example.test/1",
            },
            {
                "id": f"{start:%Y%m%d}-2",
                "flash_id": f"flash-{start:%Y%m%d}-2",
                "publish_time": start.isoformat(sep=" "),
                "important": 0,
                "title": "",
                "content": "普通市场涨跌快讯。",
                "url": "https://example.test/2",
            },
        ]

    published = []

    def fake_publish_recent_news_topics(**kwargs):
        brief_path = Path(kwargs["half_day_path"])
        published.append(read_json(brief_path))
        run_id = f"RUN-RECENT-NEWS-TOPICS-{kwargs['now']:%Y%m%d-%H%M%S}"
        return {
            "run_id": run_id,
            "payload": {
                "topic_nodes": [{"topic_id": "MKT_TOPIC_TEST"}],
                "topic_memberships": [{"membership_id": "TMEM-1"}],
                "extraction": {"invalid_ref_count": 0, "method": "llm"},
            },
        }

    def fake_publish_topic_evolution(**kwargs):
        root = Path(kwargs["root"])
        latest = root / "agent_workspace/candidates/market_topics/latest/topic-evolution-read-model.json"
        markdown = root / "agent_workspace/candidates/market_topics/latest/topic-evolution.md"
        return {"latest_json": str(latest), "latest_markdown": str(markdown), "payload": {}}

    monkeypatch.setattr(news_topic_backfill.db, "fetch_flashes", fake_fetch)
    monkeypatch.setattr(news_topic_backfill.filters, "keep_flash", lambda flash: True)
    monkeypatch.setattr(
        news_topic_backfill.recent_news_topics,
        "publish_recent_news_topics",
        fake_publish_recent_news_topics,
    )
    monkeypatch.setattr(
        news_topic_backfill.topic_evolution,
        "publish_topic_evolution",
        fake_publish_topic_evolution,
    )

    result = news_topic_backfill.run_news_topic_backfill(
        root=tmp_path,
        days=6,
        window_days=3,
        flash_limit=1,
        topics_per_window=2,
        llm_provider="fake",
    )

    manifest = result["manifest"]
    assert manifest["status"] == "succeeded"
    assert manifest["window_count"] == 3
    assert manifest["topic_run_count"] == 3
    assert manifest["totals"]["raw_flash_count"] == 6
    assert manifest["totals"]["selected_flash_count"] == 3
    assert len(published) == 3
    assert published[0]["stats"]["selected_flash_count"] == 1
    assert published[0]["top_flashes"][0]["flash_id"].startswith("flash-")
    assert read_json(Path(result["latest_manifest_path"]))["run_id"] == result["run_id"]


def test_news_topic_backfill_records_window_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(news_topic_backfill.db, "latest_time", lambda: datetime(2026, 6, 10, 12, 0, 0))

    calls = {"count": 0}

    def fake_fetch(_start, _end):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("db window failed")
        return [
            {
                "id": "2",
                "flash_id": "flash-2",
                "publish_time": "2026-06-10 00:00:00",
                "important": 1,
                "content": "美伊谈判继续。",
            }
        ]

    monkeypatch.setattr(news_topic_backfill.db, "fetch_flashes", fake_fetch)
    monkeypatch.setattr(news_topic_backfill.filters, "keep_flash", lambda flash: True)
    monkeypatch.setattr(
        news_topic_backfill.recent_news_topics,
        "publish_recent_news_topics",
        lambda **kwargs: {
            "run_id": "RUN-RECENT-NEWS-TOPICS-OK",
            "payload": {
                "topic_nodes": [],
                "topic_memberships": [],
                "extraction": {"invalid_ref_count": 0, "method": "llm"},
            },
        },
    )
    monkeypatch.setattr(
        news_topic_backfill.topic_evolution,
        "publish_topic_evolution",
        lambda **kwargs: {
            "latest_json": str(Path(kwargs["root"]) / "evolution.json"),
            "latest_markdown": str(Path(kwargs["root"]) / "evolution.md"),
            "payload": {},
        },
    )

    manifest = news_topic_backfill.run_news_topic_backfill(
        root=tmp_path,
        days=2,
        window_days=1,
        flash_limit=2,
        continue_on_error=True,
    )["manifest"]

    assert manifest["status"] == "partial"
    assert len(manifest["errors"]) == 1
    assert manifest["topic_run_count"] == 2
