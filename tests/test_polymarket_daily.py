from __future__ import annotations

from pathlib import Path

from quanta_agents.core.io import read_json
from quanta_agents.polymarket_daily import analysis, export


def _market(
    market_id: str,
    question: str,
    *,
    volume_24h: str = "1000",
    volume_1w: str = "5000",
    liquidity: str = "2000",
    change_1d: str = "0.02",
    created_at: str = "2026-06-18T00:00:00Z",
    tags: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "id": market_id,
        "conditionId": f"0x{market_id}",
        "question": question,
        "slug": f"market-{market_id}",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.61","0.39"]',
        "volume": "12000",
        "volume24hr": volume_24h,
        "volume1wk": volume_1w,
        "liquidityNum": liquidity,
        "oneDayPriceChange": change_1d,
        "bestBid": "0.6",
        "bestAsk": "0.62",
        "lastTradePrice": "0.61",
        "spread": "0.02",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "createdAt": created_at,
        "updatedAt": "2026-06-18T12:00:00Z",
        "events": [
            {
                "id": f"event-{market_id}",
                "title": "Crypto market",
                "slug": f"event-{market_id}",
                "volume24hr": volume_24h,
                "commentCount": 12,
                "tags": tags or [{"label": "Crypto", "slug": "crypto"}],
            }
        ],
    }


def _snapshot() -> dict[str, object]:
    btc = _market("1", "Will Bitcoin hit $120k in June?", volume_24h="250000", change_1d="0.04")
    sports = _market(
        "2",
        "Will Spain win the 2026 FIFA World Cup?",
        volume_24h="500000",
        liquidity="300000",
        change_1d="-0.01",
        tags=[{"label": "Sports", "slug": "sports"}],
    )
    return {
        "schema_version": "polymarket_cli_snapshot.v1",
        "source": "polymarket_cli",
        "cli": "/tmp/polymarket",
        "collected_at": "2026-06-18T12:00:00+00:00",
        "limit_per_pool": 10,
        "pools": [
            {
                "pool": "volume_24h",
                "command": ["polymarket"],
                "stderr": "",
                "item_count": 2,
                "payload": [btc, sports],
            },
            {
                "pool": "liquidity",
                "command": ["polymarket"],
                "stderr": "",
                "item_count": 1,
                "payload": [sports],
            },
        ],
        "errors": [],
    }


def test_normalize_market_parses_binary_prices_and_category() -> None:
    normalized = analysis.normalize_market(
        _market("1", "Will Bitcoin hit $120k in June?"),
        ["volume_24h"],
    )

    assert normalized["market_id"] == "1"
    assert normalized["primary_outcome"] == "Yes"
    assert normalized["primary_outcome_zh"] == "是"
    assert normalized["primary_probability"] == 0.61
    assert normalized["category"] == "crypto"
    assert normalized["category_zh"] == "加密资产"
    assert normalized["question_zh"] == "6月比特币会达到 120k 美元吗？"
    assert normalized["hotspot_score"] > 0
    assert "24h成交额 $1,000" in normalized["hotspot_reasons"][0]


def test_build_hotspots_dedupes_pools_and_keeps_leaderboards() -> None:
    hotspots = analysis.build_hotspots(_snapshot(), top=2, focus="all")

    assert hotspots["stats"]["unique_markets"] == 2
    assert hotspots["stats"]["focused_markets"] == 2
    assert len(hotspots["hotspots"]) == 2
    assert hotspots["leaderboards"]["volume_24h"][0]["market_id"] == "2"
    categories = {item["category"] for item in hotspots["category_summary"]}
    assert {"crypto", "sports"}.issubset(categories)


def test_publish_polymarket_daily_writes_quanta_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(export, "collect_market_pools", lambda **_: _snapshot())

    result = export.publish_polymarket_daily(
        root=tmp_path,
        report_date="20260618",
        limit=10,
        top=2,
        focus="all",
        use_llm=False,
    )

    report_path = Path(result["paths"]["daily_report_md"])
    latest_path = Path(result["paths"]["latest_report_md"])
    run_manifest_path = Path(result["paths"]["run_manifest"])
    market_index_path = Path(result["paths"]["market_index"])

    assert report_path.exists()
    assert latest_path.exists()
    assert "Polymarket 市场日报" in report_path.read_text(encoding="utf-8")
    assert read_json(run_manifest_path)["run_type"] == "polymarket_daily"
    raw_manifest = read_json(Path(result["paths"]["raw_manifest"]))
    assert raw_manifest["source_type"] == "prediction_market_snapshot"
    assert len(market_index_path.read_text(encoding="utf-8").splitlines()) == 2


def test_second_same_day_publish_defers_earlier_polymarket_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    stamps = iter(["010000", "020000"])
    monkeypatch.setattr(export, "_stamp", lambda: next(stamps))
    monkeypatch.setattr(export, "collect_market_pools", lambda **_: _snapshot())

    first = export.publish_polymarket_daily(
        root=tmp_path,
        report_date="20260618",
        limit=10,
        top=2,
        focus="all",
        use_llm=False,
    )
    second = export.publish_polymarket_daily(
        root=tmp_path,
        report_date="20260618",
        limit=10,
        top=2,
        focus="all",
        use_llm=False,
    )

    manifest = read_json(Path(second["paths"]["review_package"]) / "manifest.json")
    assert manifest["candidate_ids"] == [second["candidate_id"]]
    assert manifest["deferred_candidate_ids"] == [first["candidate_id"]]
    assert manifest["deferred_candidates"][0]["candidate_id"] == first["candidate_id"]
    assert manifest["deferred_candidates"][0]["superseded_by_candidate_id"] == second["candidate_id"]
    assert manifest["deferred_candidates"][0]["status"] == "superseded_by_newer_same_day_run"


def test_build_hotspots_default_focus_excludes_sports() -> None:
    hotspots = analysis.build_hotspots(_snapshot(), top=5)

    assert hotspots["focus"]["enabled"] is True
    assert hotspots["stats"]["unique_markets"] == 2
    assert hotspots["stats"]["focused_markets"] == 1
    assert hotspots["hotspots"][0]["category"] == "crypto"


def test_category_matching_does_not_match_keyword_substrings() -> None:
    market = _market(
        "3",
        "Will the highest temperature in Warsaw be 24°C on June 18?",
        tags=[{"label": "Weather", "slug": "weather"}],
    )
    market["events"][0]["title"] = "Weather market"
    normalized = analysis.normalize_market(market)

    assert normalized["category"] == "weather"


def test_question_translation_handles_common_macro_political_patterns() -> None:
    assert analysis.translate_question_zh(
        "Will Khamenei post 0-4 posts from June 12 to June 19, 2026?"
    ) == "哈梅内伊会在2026年6月12日至6月19日发布 0-4 条帖子吗？"
    assert analysis.translate_question_zh(
        "Will the Fed increase interest rates by 25 bps after the July 2026 meeting?"
    ) == "2026年7月会议后，美联储会加息 25 bps 吗？"
    assert analysis.translate_question_zh(
        "Iran agrees to end enrichment of uranium by June 30?"
    ) == "伊朗会在6月30日前同意停止铀浓缩吗？"
    assert analysis.translate_question_zh(
        "Will the price of Solana be between $70 and $80 on June 20?"
    ) == "6月20日索拉纳价格会在 70 至 80 美元之间吗？"
    assert analysis.translate_question_zh(
        "Bitcoin Up or Down - June 20, 5AM ET"
    ) == "6月20日，5AM 美东时间比特币上涨还是下跌？"
    assert analysis.translate_question_zh(
        "Will the next diplomatic US-Iran meeting be in Switzerland?"
    ) == "下一次美伊外交会谈会在瑞士举行吗？"
