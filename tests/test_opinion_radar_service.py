from __future__ import annotations

from datetime import datetime
import json

from quanta_agents.opinion_radar import service


def _write_theme_anchor_set(tmp_path):
    path = (
        tmp_path
        / "agent_workspace/candidates/theme_anchor/2026/06/18/"
        "CAND-THEME-ANCHOR-TEST/theme_anchors.json"
    )
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "theme_anchor_candidate_set.v1",
                "status": "candidate",
                "candidate_id": "CAND-THEME-ANCHOR-TEST",
                "theme_anchors": [
                    {
                        "schema_version": "theme_anchor.v1",
                        "theme_anchor_id": "THA-CU-INVENTORY",
                        "status": "candidate",
                        "title": "铜库存去化",
                        "description": "沪铜库存去化和仓单下降，是期市速递/研报反复跟踪的供应需求验证主题。",
                        "anchor_kind": "research_report_repeated_theme",
                        "theme_type": "inventory",
                        "source_roles": ["research_report", "agent"],
                        "asset_refs": [{"id": "FUT-CU", "label": "铜", "ref_type": "asset"}],
                        "source_refs": [],
                        "aliases": ["沪铜库存去化"],
                        "event_definition_layers": {
                            "fact_layer": "沪铜库存去化和仓单下降。",
                            "political_layer": None,
                            "time_window_layer": "2026-06-18 daily baseline",
                            "settlement_rule_layer": None,
                        },
                        "lifecycle": {
                            "support_count": 3,
                            "conflict_count": 0,
                            "review_state": "machine_candidate",
                        },
                        "promotion_policy": "review_required",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_gold_silver_anchor_set(tmp_path):
    path = (
        tmp_path
        / "agent_workspace/candidates/theme_anchor/2026/06/18/"
        "CAND-THEME-ANCHOR-GOLD/theme_anchors.json"
    )
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "theme_anchor_candidate_set.v1",
                "status": "candidate",
                "candidate_id": "CAND-THEME-ANCHOR-GOLD",
                "theme_anchors": [
                    {
                        "schema_version": "theme_anchor.v1",
                        "theme_anchor_id": "THA-GOLD-SILVER",
                        "status": "candidate",
                        "title": "黄金/白银",
                        "description": "黄金和白银价格验证主题。",
                        "anchor_kind": "agent_cluster_candidate",
                        "theme_type": "price_validation",
                        "source_roles": ["research_report"],
                        "asset_refs": [{"id": "FUT-AU", "label": "黄金", "ref_type": "asset"}],
                        "source_refs": [],
                        "aliases": ["黄金"],
                        "event_definition_layers": {
                            "fact_layer": "黄金与白银价格验证。",
                            "political_layer": None,
                            "time_window_layer": "2026-06-18 daily baseline",
                            "settlement_rule_layer": None,
                        },
                        "lifecycle": {"review_state": "machine_candidate"},
                        "promotion_policy": "review_required",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_snapshot_buckets_flashes_without_llm(monkeypatch, tmp_path):
    taxonomy_path = tmp_path / "futures_assets.v1.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-CU",
                        "canonical_name": "铜",
                        "category": "有色金属",
                        "sector": "有色",
                        "aliases": ["铜", "沪铜"],
                    },
                    {
                        "asset_id": "FUT-SC",
                        "canonical_name": "原油",
                        "category": "能源化工",
                        "sector": "能化",
                        "aliases": ["原油", "OPEC"],
                    },
                ],
                "macro_buckets": [
                    {"bucket_id": "fed_rate", "label": "美联储与利率", "keywords": ["美联储", "降息"]},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    service.dictionary._taxonomy.cache_clear()

    flashes = [
        {
            "id": 1,
            "flash_id": "a",
            "publish_time": "2026-06-17 09:00:00",
            "important": 1,
            "channel": "jin10",
            "title": "美联储降息预期升温，铜库存下降",
            "content": "",
            "url": "https://example.test/a",
        },
        {
            "id": 2,
            "flash_id": "b",
            "publish_time": "2026-06-17 10:00:00",
            "important": 0,
            "channel": "jin10",
            "title": "原油主力合约涨超2%",
            "content": "",
            "url": "https://example.test/b",
        },
        {
            "id": 3,
            "flash_id": "c",
            "publish_time": "2026-06-17 11:00:00",
            "important": 0,
            "channel": "jin10",
            "title": "OPEC讨论增产计划，原油供应预期变化",
            "content": "",
            "url": "https://example.test/c",
        },
    ]
    monkeypatch.setattr(service.db, "latest_time", lambda: datetime(2026, 6, 17, 12, 0, 0))
    monkeypatch.setattr(service.db, "fetch_flashes", lambda start, end: flashes)

    snapshot = service.snapshot(hours=24, top=10, name_llm=False)

    assert snapshot["stats"]["flash_raw"] == 3
    assert snapshot["stats"]["noise_filtered"] == 1
    labels = {theme["label"] for theme in snapshot["themes"]}
    assert "铜" in labels
    assert "美联储与利率" in labels
    assert "原油" in labels


def test_snapshot_prefers_theme_anchor_before_free_cluster(monkeypatch, tmp_path):
    taxonomy_path = tmp_path / "futures_assets.v1.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-CU",
                        "canonical_name": "铜",
                        "category": "有色金属",
                        "sector": "有色",
                        "aliases": ["铜", "沪铜"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    theme_path = _write_theme_anchor_set(tmp_path)
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    service.dictionary._taxonomy.cache_clear()
    monkeypatch.setattr(service.db, "latest_time", lambda: datetime(2026, 6, 18, 12, 0, 0))
    monkeypatch.setattr(
        service.db,
        "fetch_flashes",
        lambda start, end: [
            {
                "flash_id": "cu-anchor",
                "publish_time": "2026-06-18 09:00:00",
                "important": 1,
                "title": "沪铜库存去化，仓单下降支撑铜价",
                "content": "交易所库存继续下降。",
            }
        ],
    )

    snapshot = service.snapshot(
        hours=24,
        top=10,
        name_llm=False,
        root=tmp_path,
        theme_anchor_path=str(theme_path),
    )

    theme = snapshot["themes"][0]
    assert theme["theme"] == "铜库存去化"
    assert theme["free_theme"] == "铜"
    assert theme["anchoring_status"] == "anchored_theme"
    assert theme["theme_anchor_refs"][0]["id"] == "THA-CU-INVENTORY"
    assert snapshot["stats"]["anchored_theme_count"] == 1


def test_snapshot_marks_unanchored_theme_candidate(monkeypatch, tmp_path):
    taxonomy_path = tmp_path / "futures_assets.v1.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-CU",
                        "canonical_name": "铜",
                        "category": "有色金属",
                        "sector": "有色",
                        "aliases": ["铜", "沪铜"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    theme_path = _write_theme_anchor_set(tmp_path)
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    service.dictionary._taxonomy.cache_clear()
    monkeypatch.setattr(service.db, "latest_time", lambda: datetime(2026, 6, 18, 12, 0, 0))
    monkeypatch.setattr(
        service.db,
        "fetch_flashes",
        lambda start, end: [
            {
                "flash_id": "cu-new",
                "publish_time": "2026-06-18 09:00:00",
                "important": 1,
                "title": "沪铜出口订单改善，海外需求出现新变化",
                "content": "该主题暂未出现在候选锚点中。",
            }
        ],
    )

    snapshot = service.snapshot(
        hours=24,
        top=10,
        name_llm=False,
        root=tmp_path,
        theme_anchor_path=str(theme_path),
    )

    theme = snapshot["themes"][0]
    assert theme["theme"] == "铜"
    assert theme["anchoring_status"] == "unanchored_theme_candidate"
    assert theme["theme_anchor_refs"] == []
    assert snapshot["stats"]["unanchored_theme_count"] == 1


def test_snapshot_keeps_macro_bucket_title_when_asset_anchor_matches_news_text(monkeypatch, tmp_path):
    taxonomy_path = tmp_path / "futures_assets.v1.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-AU",
                        "canonical_name": "黄金",
                        "category": "贵金属",
                        "sector": "有色",
                        "aliases": ["黄金"],
                    }
                ],
                "macro_buckets": [
                    {"bucket_id": "fed_rate", "label": "美联储与利率", "keywords": ["美联储", "加息"]},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    theme_path = _write_gold_silver_anchor_set(tmp_path)
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    service.dictionary._taxonomy.cache_clear()
    monkeypatch.setattr(service.db, "latest_time", lambda: datetime(2026, 6, 18, 12, 0, 0))
    monkeypatch.setattr(
        service.db,
        "fetch_flashes",
        lambda start, end: [
            {
                "flash_id": "fed-gold",
                "publish_time": "2026-06-18 09:00:00",
                "important": 1,
                "title": "市场定价显示，美联储加息押注上升，黄金承压",
                "content": "",
            }
        ],
    )

    snapshot = service.snapshot(
        hours=24,
        top=10,
        name_llm=False,
        root=tmp_path,
        theme_anchor_path=str(theme_path),
    )

    macro = next(theme for theme in snapshot["themes"] if theme["label"] == "美联储与利率")
    gold = next(theme for theme in snapshot["themes"] if theme["label"] == "黄金")
    assert macro["theme"] == "美联储与利率"
    assert macro["theme_anchor_refs"] == []
    assert macro["anchoring_status"] == "unanchored_theme_candidate"
    assert gold["theme"] == "黄金"
    assert gold["anchored_theme_title"] == "黄金/白银"
    assert gold["theme_anchor_refs"][0]["id"] == "THA-GOLD-SILVER"


def test_snapshot_rejects_variety_anchor_when_anchor_asset_differs(monkeypatch, tmp_path):
    taxonomy_path = tmp_path / "futures_assets.v1.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-CU",
                        "canonical_name": "铜",
                        "category": "有色金属",
                        "sector": "有色",
                        "aliases": ["铜"],
                    },
                    {
                        "asset_id": "FUT-AU",
                        "canonical_name": "黄金",
                        "category": "贵金属",
                        "sector": "有色",
                        "aliases": ["黄金"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    theme_path = _write_gold_silver_anchor_set(tmp_path)
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    service.dictionary._taxonomy.cache_clear()
    monkeypatch.setattr(service.db, "latest_time", lambda: datetime(2026, 6, 18, 12, 0, 0))
    monkeypatch.setattr(
        service.db,
        "fetch_flashes",
        lambda start, end: [
            {
                "flash_id": "cu-gold-switch",
                "publish_time": "2026-06-18 09:00:00",
                "important": 1,
                "title": "DeepTalk：铜与黄金，一场悄然发生的大切换",
                "content": "",
            }
        ],
    )

    snapshot = service.snapshot(
        hours=24,
        top=10,
        name_llm=False,
        root=tmp_path,
        theme_anchor_path=str(theme_path),
    )

    copper = next(theme for theme in snapshot["themes"] if theme["label"] == "铜")
    assert copper["theme"] == "铜"
    assert copper["theme_anchor_refs"] == []
    assert copper["anchoring_status"] == "unanchored_theme_candidate"


def test_snapshot_prefers_variety_bucket_and_avoids_usd_quote_noise(monkeypatch, tmp_path):
    taxonomy_path = tmp_path / "futures_assets.v1.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-SC",
                        "canonical_name": "原油",
                        "category": "能源化工",
                        "sector": "能化",
                        "aliases": ["原油", "WTI"],
                    }
                ],
                "macro_buckets": [
                    {
                        "bucket_id": "usd_fx",
                        "label": "美元与汇率",
                        "keywords": ["美元指数", "美元", "汇率"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    service.dictionary._taxonomy.cache_clear()
    flashes = [
        {
            "id": 1,
            "flash_id": "oil",
            "publish_time": "2026-06-18 09:00:00",
            "important": 1,
            "title": "WTI原油现报79美元/桶，EIA库存下降",
            "content": "",
        }
    ]
    monkeypatch.setattr(service.db, "latest_time", lambda: datetime(2026, 6, 18, 12, 0, 0))
    monkeypatch.setattr(service.db, "fetch_flashes", lambda start, end: flashes)

    snapshot = service.snapshot(hours=24, top=10, name_llm=False)
    labels = {theme["label"] for theme in snapshot["themes"]}

    assert "原油" in labels
    assert "能化" not in labels
    assert "能源化工" not in labels
    assert "美元与汇率" not in labels
