from __future__ import annotations

import json

from quanta_agents.futures_daily import raw_run


def test_llm_prompt_separates_fundamentals_from_market_observations(monkeypatch):
    captured = {}

    def fake_chat(prompt: str, *, max_tokens: int = 900, temperature: float = 0.2, timeout: int = 80) -> str:
        captured["prompt"] = prompt
        captured["max_tokens"] = max_tokens
        return json.dumps(
            {
                "commodity": "铜",
                "fundamental_summary": "库存下降支撑基本面，但盘面信息只作观察。",
                "bullish_factors": ["库存下降，反映供需边际改善"],
                "bearish_factors": [],
                "key_data": ["库存环比下降1万吨"],
                "key_events": [],
                "supply_demand": ["库存下降支撑供需结构"],
                "market_observations": ["沪铜主力合约收跌"],
                "price_forecast": ["价格判断需等待库存继续验证"],
                "sentiment_score": 2,
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(raw_run, "_chat", fake_chat)

    result = raw_run._analyze_asset(
        "铜",
        [{"org_name": "测试机构", "title": "铜日报", "text": "沪铜主力合约收跌。库存环比下降1万吨。"}],
        use_llm=True,
    )

    assert captured["max_tokens"] == 4200
    assert "market_observations" in captured["prompt"]
    assert "不能因为期价上涨/下跌" in captured["prompt"]
    assert "不得混入 bullish_factors / bearish_factors / supply_demand" in captured["prompt"]
    assert result["market_observations"] == ["沪铜主力合约收跌"]
    assert result["sentiment_score"] == 2


def test_llm_result_uses_source_influence_weighted_score(monkeypatch):
    def fake_chat(prompt: str, *, max_tokens: int = 900, temperature: float = 0.2, timeout: int = 80) -> str:
        return json.dumps(
            {
                "commodity": "铜",
                "fundamental_summary": "高权重来源偏多，低权重来源偏空。",
                "bullish_factors": ["库存下降支撑供需结构"],
                "bearish_factors": ["进口到货增加形成部分压制"],
                "key_data": [],
                "key_events": [],
                "supply_demand": [],
                "market_observations": [],
                "price_forecast": [],
                "holistic_sentiment_score": 8,
                "source_score_audit": [
                    {
                        "source_index": 1,
                        "row_id": "a",
                        "report_sentiment_score": 6,
                        "influence_score": 0.8,
                        "evidence_summary": "库存下降",
                        "score_reason": "直接讨论铜基本面",
                    },
                    {
                        "source_index": 2,
                        "row_id": "b",
                        "report_sentiment_score": -4,
                        "influence_score": 0.2,
                        "evidence_summary": "进口到货增加",
                        "score_reason": "证据较少",
                    },
                ],
                "sentiment_score": 8,
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(raw_run, "_chat", fake_chat)

    result = raw_run._analyze_asset(
        "铜",
        [
            {"row_id": "a", "org_name": "机构A", "title": "铜日报A", "text": "库存下降。"},
            {"row_id": "b", "org_name": "机构B", "title": "铜日报B", "text": "进口到货增加。"},
        ],
        use_llm=True,
    )

    assert result["holistic_sentiment_score"] == 8
    assert result["weighted_sentiment_score"] == 4.0
    assert result["sentiment_score"] == 4.0
    assert result["score_audit"]["original_scores"] == [6.0, -4.0]
    assert result["score_audit"]["influence_scores"] == [0.8, 0.2]


def test_llm_extraction_drops_placeholder_no_evidence_factors(monkeypatch):
    def fake_chat(prompt: str, *, max_tokens: int = 900, temperature: float = 0.2, timeout: int = 80) -> str:
        return json.dumps(
            {
                "commodity": "锌",
                "fundamental_summary": "原文没有提供有效锌基本面。",
                "bullish_factors": ["原文未提供锌的基本面利多证据。", "库存下降支撑锌价"],
                "bearish_factors": ["原文未给出锌的基本面利空因素"],
                "key_data": ["LME期锌上涨，仅为价格表现"],
                "key_events": [],
                "supply_demand": ["缺乏对应库存或供需信息"],
                "market_observations": ["LME期锌上涨"],
                "price_forecast": [],
                "sentiment_score": 0,
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(raw_run, "_chat", fake_chat)

    result = raw_run._analyze_asset(
        "锌",
        [{"org_name": "测试机构", "title": "锌日报", "text": "LME期锌上涨。库存下降支撑锌价。"}],
        use_llm=True,
    )

    assert result["bullish_factors"] == ["库存下降支撑锌价"]
    assert result["bearish_factors"] == []
    assert result["key_data"] == []
    assert result["supply_demand"] == []


def test_rule_fallback_keeps_market_only_text_out_of_fundamental_factors():
    result = raw_run._fallback_analysis(
        "铜",
        [
            {
                "org_name": "测试机构",
                "title": "铜日报",
                "text": "沪铜主力合约收跌，盘面承压。库存环比下降1万吨，去库支撑基本面。",
            }
        ],
    )

    assert "沪铜主力合约收跌，盘面承压。" in result["market_observations"]
    assert all("主力合约收跌" not in item for item in result["bearish_factors"])
