from __future__ import annotations

from datetime import datetime
import json

from quanta_agents.opinion_radar import service


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
