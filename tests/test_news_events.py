from __future__ import annotations

from pathlib import Path

from quanta_agents.core.io import write_json
from quanta_agents.repositories.catalog_repository import CatalogRepository
from quanta_agents.news_events.repository import NewsEventRepository
from quanta_agents.news_events.service import run_news_event_batch


def _write_news_event_fixtures(root: Path) -> None:
    write_json(
        root / "gold/reference_data/assets/futures_assets.v1.json",
        {
            "schema_version": "asset_taxonomy.v1",
            "assets": [
                {
                    "asset_id": "FUT-SC",
                    "canonical_name": "原油",
                    "commodity_code": "SC",
                    "aliases": ["原油", "油价", "霍尔木兹", "霍尔木兹海峡", "OPEC"],
                },
                {
                    "asset_id": "FUT-AU",
                    "canonical_name": "黄金",
                    "commodity_code": "AU",
                    "aliases": ["黄金", "金价", "避险"],
                },
                {
                    "asset_id": "FUT-CU",
                    "canonical_name": "铜",
                    "commodity_code": "CU",
                    "aliases": ["铜", "沪铜", "铜价"],
                },
            ],
        },
    )
    framework_dir = root / "data_lake/active_knowledge/research_frameworks/commodities"
    framework_dir.mkdir(parents=True, exist_ok=True)
    for filename, asset_id, name in [
        ("futures.INE.crude_oil.json", "FUT-SC", "原油"),
        ("futures.SHFE.gold.json", "FUT-AU", "黄金"),
        ("futures.SHFE.copper.json", "FUT-CU", "铜"),
    ]:
        write_json(
            framework_dir / filename,
            {
                "schema_version": "research_framework.v1",
                "artifact_type": "research_framework",
                "framework_id": f"fw_{asset_id}",
                "asset_id": asset_id,
                "standard_name": name,
                "generic_name": name,
                "status": "active",
                "core_dimensions": [
                    {
                        "dimension_id": "geo",
                        "dimension_name": "地缘政治",
                        "dimension_type": "geopolitics",
                        "typical_events": ["霍尔木兹封锁", "中东冲突", "通航恢复"],
                    },
                    {
                        "dimension_id": "macro",
                        "dimension_name": "宏观",
                        "dimension_type": "macro",
                        "typical_events": ["美联储降息", "美元走弱"],
                    },
                ],
            },
        )


def _batch(root: Path, flashes: list[dict], *, run_id: str = "RUN-TEST", use_llm: bool = False):
    _write_news_event_fixtures(root)
    return run_news_event_batch(
        root=root,
        flashes=flashes,
        use_llm=use_llm,
        catalog_path=root / "indexes/news_event_catalog/news_events.sqlite3",
        run_id=run_id,
    )


def test_news_id_and_sqlite_upserts_are_idempotent(tmp_path: Path) -> None:
    flashes = [
        {
            "id": 1,
            "flash_id": "flash-1",
            "publish_time": "2026-06-22 09:00:00",
            "important": 1,
            "channel": "jin10",
            "title": "伊朗可能封锁霍尔木兹海峡，原油市场关注中东局势",
            "content": "消息人士称相关讨论仍处于传闻阶段。",
            "url": "https://example.com/a?utm_source=x",
        }
    ]
    first = _batch(tmp_path, flashes, run_id="RUN-IDEMPOTENT")
    second = _batch(tmp_path, flashes, run_id="RUN-IDEMPOTENT")
    repo = NewsEventRepository(tmp_path, db_path=tmp_path / "indexes/news_event_catalog/news_events.sqlite3")

    assert first["normalized_news"][0]["news_id"] == second["normalized_news"][0]["news_id"]
    assert repo.table_counts()["news_item"] == 1
    assert repo.table_counts()["canonical_event"] == 1
    assert repo.table_counts()["event_mention"] == 1


def test_exact_and_near_duplicates_form_one_syndication_group(tmp_path: Path) -> None:
    flashes = [
        {
            "flash_id": "dup-a",
            "publish_time": "2026-06-22 09:00:00",
            "important": 1,
            "channel": "media-a",
            "title": "伊朗可能封锁霍尔木兹海峡",
            "content": "消息人士称伊朗可能封锁霍尔木兹海峡，相关讨论仍处于传闻阶段。",
        },
        {
            "flash_id": "dup-b",
            "publish_time": "2026-06-22 09:01:00",
            "important": 1,
            "channel": "media-b",
            "title": "市场关注霍尔木兹海峡封锁风险",
            "content": "消息人士称伊朗可能封锁霍尔木兹海峡，相关讨论仍处于传闻阶段。",
        },
    ]
    result = _batch(tmp_path, flashes)

    assert result["metrics"]["syndication_group_count"] == 1
    assert result["syndication_groups"][0]["independent_source_count"] == 1
    assert {member["duplicate_type"] for member in result["syndication_members"]} >= {
        "representative",
        "near_duplicate",
    }


def test_multi_asset_event_is_one_canonical_event_with_three_asset_links(tmp_path: Path) -> None:
    result = _batch(
        tmp_path,
        [
            {
                "flash_id": "multi-asset",
                "publish_time": "2026-06-22 09:00:00",
                "important": 1,
                "title": "伊朗可能封锁霍尔木兹海峡，原油、黄金和铜市场关注中东局势",
                "content": "交易员关注通航不确定性对大宗商品风险偏好的影响。",
            }
        ],
    )

    assert result["metrics"]["canonical_event_count"] == 1
    assert result["metrics"]["multi_asset_event_count"] == 1
    assert {link["asset_label"] for link in result["event_asset_links"]} >= {"原油", "黄金", "铜"}
    assert len({event["event_id"] for event in result["canonical_events"]}) == 1


def test_same_event_mentions_merge_across_media(tmp_path: Path) -> None:
    result = _batch(
        tmp_path,
        [
            {
                "flash_id": "same-a",
                "publish_time": "2026-06-22 09:00:00",
                "important": 1,
                "channel": "media-a",
                "title": "伊朗可能封锁霍尔木兹海峡",
                "content": "消息人士称相关讨论仍处于传闻阶段。",
            },
            {
                "flash_id": "same-b",
                "publish_time": "2026-06-22 09:10:00",
                "important": 1,
                "channel": "media-b",
                "title": "媒体称伊朗可能封锁霍尔木兹海峡",
                "content": "市场继续关注霍尔木兹海峡通行风险。",
            },
        ],
    )

    assert result["metrics"]["canonical_event_count"] == 1
    assert result["canonical_events"][0]["source_count"] == 2
    assert result["metrics"]["same_event_count"] >= 1


def test_denial_creates_contradict_relation_not_same_event(tmp_path: Path) -> None:
    result = _batch(
        tmp_path,
        [
            {
                "flash_id": "rumor",
                "publish_time": "2026-06-22 09:00:00",
                "important": 1,
                "title": "伊朗可能封锁霍尔木兹海峡",
                "content": "消息人士称相关讨论仍处于传闻阶段。",
            },
            {
                "flash_id": "denial",
                "publish_time": "2026-06-22 10:00:00",
                "important": 1,
                "title": "伊朗官方否认封锁霍尔木兹海峡",
                "content": "伊朗官方称相关封锁消息不属实。",
            },
        ],
    )

    assert result["metrics"]["canonical_event_count"] == 2
    assert {relation["relation"] for relation in result["event_relations"]} == {"CONTRADICT"}
    assert result["metrics"]["contradict_count"] == 1


def test_topic_persists_across_days_without_recent_theme_candidate_files(tmp_path: Path) -> None:
    day_one = _batch(
        tmp_path,
        [
            {
                "flash_id": "topic-day-1",
                "publish_time": "2026-06-21 09:00:00",
                "important": 1,
                "title": "伊朗可能封锁霍尔木兹海峡",
                "content": "市场关注中东通航风险。",
            }
        ],
        run_id="RUN-TOPIC-1",
    )
    day_two = _batch(
        tmp_path,
        [
            {
                "flash_id": "topic-day-2",
                "publish_time": "2026-06-22 09:00:00",
                "important": 1,
                "title": "伊朗官方继续回应霍尔木兹海峡通行问题",
                "content": "中东通航风险仍是市场关注焦点。",
            }
        ],
        run_id="RUN-TOPIC-2",
    )
    repo = NewsEventRepository(tmp_path, db_path=tmp_path / "indexes/news_event_catalog/news_events.sqlite3")

    assert day_one["topic_memberships"][0]["topic_id"] == day_two["topic_memberships"][0]["topic_id"]
    assert repo.table_counts()["persistent_topic"] == 1


def test_rule_created_topic_is_capped_and_reviewable(tmp_path: Path) -> None:
    result = _batch(
        tmp_path,
        [
            {
                "flash_id": "rule-topic-review",
                "publish_time": "2026-06-22 09:00:00",
                "important": 1,
                "title": "伊朗可能封锁霍尔木兹海峡",
                "content": "市场关注中东通航风险。",
            }
        ],
        run_id="RUN-RULE-TOPIC-REVIEW",
    )
    membership = result["topic_memberships"][0]
    review_types = {item.get("review_type") for item in result["review_queue"]}

    assert membership["assigned_by"] == "rule"
    assert membership["relation_to_topic"] == "creates"
    assert membership["membership_score"] == 0.55
    assert "rule_created_topic_candidate" in review_types
    assert result["metrics"]["review_queue_count"] == len(result["review_queue"])


def test_low_confidence_future_calendar_mentions_enter_review_queue(tmp_path: Path, monkeypatch) -> None:
    from quanta_agents.news_events import service

    def fake_extract(news_items, **_kwargs):
        news = news_items[0]
        return [
            {
                "mention_id": "MENTION-FUTURE-CALENDAR",
                "source_news_id": news["news_id"],
                "subject": ["EIA"],
                "action": "公布",
                "object": ["原油库存数据"],
                "location": ["美国"],
                "event_type": "inventory",
                "event_stage": "announced",
                "modality": "reported",
                "event_time": "2027-01-01T00:00:00+00:00",
                "entities": ["EIA", "美国"],
                "canonical_summary": "十分钟后公布EIA原油库存数据",
                "truth_status": "unverified",
                "market_attention": 0.4,
                "extraction_confidence": 0.0,
                "evidence_quote": "十分钟后公布EIA原油库存数据",
                "created_at": "2026-06-22T01:00:00+00:00",
                "extraction_method": "llm",
                "syndication_group_id": None,
            }
        ], []

    monkeypatch.setattr(service, "extract_event_mentions_batch", fake_extract)
    result = _batch(
        tmp_path,
        [
            {
                "flash_id": "future-calendar",
                "publish_time": "2026-06-22 09:00:00",
                "important": 1,
                "title": "十分钟后公布EIA原油库存数据",
                "content": "市场等待库存数据发布。",
            }
        ],
        run_id="RUN-FUTURE-CALENDAR-REVIEW",
        use_llm=True,
    )
    review_types = {item.get("review_type") for item in result["review_queue"]}

    assert {
        "low_extraction_confidence",
        "future_event_time",
        "calendar_notice_needs_release_time",
    } <= review_types


def test_llm_failure_uses_rule_fallback_without_crashing(tmp_path: Path, monkeypatch) -> None:
    from quanta_agents.news_events import extractor

    def broken_chat(*args, **kwargs):
        raise RuntimeError("no key")

    monkeypatch.setattr(extractor, "chat", broken_chat)
    result = _batch(
        tmp_path,
        [
            {
                "flash_id": "fallback",
                "publish_time": "2026-06-22 09:00:00",
                "important": 1,
                "title": "伊朗可能封锁霍尔木兹海峡",
                "content": "消息人士称相关讨论仍处于传闻阶段。",
            }
        ],
        use_llm=True,
    )

    assert result["event_mentions"][0]["extraction_method"] == "rule_fallback"
    assert result["metrics"]["llm_call_failure_count"] == 1
    assert result["metrics"]["canonical_event_count"] == 1


def test_event_matching_normalizes_mixed_timezone_event_times() -> None:
    from quanta_agents.news_events.event_matching import score_mention_to_event

    score = score_mention_to_event(
        {
            "subject": ["伊朗"],
            "action": "否认",
            "object": ["封锁霍尔木兹海峡"],
            "location": ["霍尔木兹海峡"],
            "event_type": "geopolitics",
            "event_time": "2026-06-22T10:00:00+08:00",
            "entities": ["伊朗", "霍尔木兹海峡"],
            "canonical_summary": "伊朗否认封锁霍尔木兹海峡",
            "event_stage": "denial",
        },
        {
            "event_type": "geopolitics",
            "event_time": "2026-06-22 01:30:00",
            "last_seen_at": "2026-06-22 01:30:00",
            "core_entities": ["伊朗", "霍尔木兹海峡"],
            "canonical_summary": "伊朗可能封锁霍尔木兹海峡",
            "core_signature": {
                "subject": ["伊朗"],
                "action": "封锁",
                "object": ["霍尔木兹海峡"],
                "location": ["霍尔木兹海峡"],
                "event_stage": "rumor",
            },
        },
        {},
    )

    assert score["components"]["time_proximity"] == 1.0


def test_legacy_news_logic_compatible_view_keeps_asset_rows(tmp_path: Path) -> None:
    result = _batch(
        tmp_path,
        [
            {
                "flash_id": "legacy-view",
                "publish_time": "2026-06-22 09:00:00",
                "important": 1,
                "title": "伊朗可能封锁霍尔木兹海峡，原油、黄金和铜市场关注中东局势",
                "content": "交易员关注通航不确定性。",
            }
        ],
    )
    rows = result["legacy_news_logic_events"]

    assert {row["asset"] for row in rows} >= {"原油", "黄金", "铜"}
    assert all(row["canonical_event_id"] == result["canonical_events"][0]["event_id"] for row in rows)
    assert {"event_id", "flash_id", "asset", "framework_node", "match", "consistency"} <= set(rows[0])


def test_news_event_batch_projects_explicit_relations_to_object_catalog(tmp_path: Path, monkeypatch) -> None:
    catalog_path = tmp_path / "indexes/object_catalog/quanta_catalog.sqlite3"
    monkeypatch.setenv("QUANTA_OBJECT_CATALOG_ENABLED", "true")
    monkeypatch.setenv("QUANTA_OBJECT_CATALOG_PATH", str(catalog_path))
    result = _batch(
        tmp_path,
        [
            {
                "flash_id": "catalog-projection",
                "publish_time": "2026-06-22 09:00:00",
                "important": 1,
                "title": "伊朗可能封锁霍尔木兹海峡，原油、黄金和铜市场关注中东局势",
                "content": "交易员关注通航不确定性对大宗商品风险偏好的影响。",
            }
        ],
        run_id="RUN-CATALOG-PROJECTION",
    )

    projection = result["object_catalog_projection"]
    repo = CatalogRepository(tmp_path, db_path=catalog_path, initialize=False)
    event_id = result["canonical_events"][0]["event_id"]
    topic_id = result["topic_memberships"][0]["topic_id"]
    trace = repo.trace_lineage(event_id)
    relation_types = {relation["relation_type"] for relation in trace["relations"]}
    object_types = {obj["object_type"] for obj in trace["objects"]}

    assert projection["status"] == "succeeded"
    assert repo.table_counts()["research_object"] >= 9
    assert {"event", "event_mention", "document", "topic", "asset", "logic_node"} <= object_types
    assert {"DERIVED_FROM", "BELONGS_TO_TOPIC", "AFFECTS_ASSET", "MAPS_TO_NODE"} <= relation_types
    assert repo.get_topic(topic_id)["canonical_title"]
    assert len(repo.event_asset_links(event_id)) >= 3
    assert Path(result["run_dir"], "event_framework_node_links.jsonl").exists()
