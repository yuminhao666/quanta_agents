from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from quanta_agents.core.io import utc_now_iso
from quanta_agents.core.llm_client import chat
from quanta_agents.core.llm_json import parse_json_object


CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "finance",
        (
            "stock",
            "stocks",
            "share",
            "shares",
            "equity",
            "etf",
            "ipo",
            "earnings",
            "treasury",
            "yield",
            "bond",
            "nasdaq",
            "dow jones",
            "s&p",
            "spx",
            "russell",
            "vix",
            "tesla",
            "tsla",
            "nvidia",
            "nvda",
            "apple",
            "aapl",
            "microsoft",
            "msft",
            "dollar",
            "yen",
            "euro",
            "gold",
            "silver",
            "oil",
            "wti",
            "brent",
        ),
    ),
    (
        "sports",
        (
            "sports",
            "fifa",
            "world cup",
            "soccer",
            "football",
            "nba",
            "nfl",
            "mlb",
            "nhl",
            "ufc",
            "tennis",
            "formula 1",
            "f1",
        ),
    ),
    (
        "crypto",
        (
            "crypto",
            "bitcoin",
            "btc",
            "ethereum",
            "eth",
            "solana",
            "xrp",
            "bnb",
            "doge",
            "token",
            "blockchain",
        ),
    ),
    (
        "politics",
        (
            "politics",
            "election",
            "president",
            "trump",
            "biden",
            "vance",
            "jd vance",
            "senate",
            "congress",
            "white house",
            "supreme court",
            "tariff",
        ),
    ),
    (
        "geopolitics",
        (
            "ukraine",
            "russia",
            "china",
            "taiwan",
            "israel",
            "iran",
            "khamenei",
            "ayatollah",
            "gaza",
            "war",
            "ceasefire",
            "hamas",
            "hezbollah",
            "nato",
            "invasion",
            "nuclear",
            "sanction",
            "troops",
        ),
    ),
    (
        "macro",
        (
            "fed",
            "fomc",
            "inflation",
            "cpi",
            "gdp",
            "recession",
            "rate cut",
            "rate hike",
            "unemployment",
            "oil",
            "gold",
        ),
    ),
    (
        "weather",
        (
            "weather",
            "temperature",
            "rain",
            "snow",
            "hurricane",
            "earthquake",
            "wind",
        ),
    ),
    (
        "culture",
        (
            "album",
            "movie",
            "gta",
            "grammy",
            "oscar",
            "taylor swift",
            "rihanna",
            "playboi carti",
            "youtube",
        ),
    ),
    (
        "tech",
        (
            "openai",
            "ai",
            "apple",
            "tesla",
            "spacex",
            "nvidia",
            "google",
            "microsoft",
            "meta",
            "xai",
        ),
    ),
)

FOCUS_PRESETS: dict[str, set[str]] = {
    "finance": {"finance", "crypto"},
    "politics": {"politics", "geopolitics"},
    "macro": {"macro", "finance", "crypto"},
    "geopolitics": {"geopolitics"},
    "crypto": {"crypto"},
    "default": {"finance", "crypto", "macro", "politics", "geopolitics"},
}

CATEGORY_ZH = {
    "finance": "金融",
    "sports": "体育",
    "crypto": "加密资产",
    "politics": "政治",
    "geopolitics": "地缘政治",
    "macro": "宏观",
    "weather": "天气",
    "culture": "文化娱乐",
    "tech": "科技",
    "other": "其他",
}

OUTCOME_ZH = {
    "Yes": "是",
    "No": "否",
    "Up": "上涨",
    "Down": "下跌",
}

PHRASE_ZH = (
    ("US-Iran", "美伊"),
    ("US x Iran", "美国与伊朗"),
    ("United States", "美国"),
    ("United States of America", "美国"),
    ("Iran", "伊朗"),
    ("Israel", "以色列"),
    ("Hezbollah", "真主党"),
    ("Lebanon", "黎巴嫩"),
    ("Switzerland", "瑞士"),
    ("Khamenei", "哈梅内伊"),
    ("Ayatollah", "阿亚图拉"),
    ("JD Vance", "JD Vance"),
    ("Steve Witkoff", "Steve Witkoff"),
    ("Shehbaz Sharif", "夏巴兹·谢里夫"),
    ("Trump", "特朗普"),
    ("Bitcoin", "比特币"),
    ("Ethereum", "以太坊"),
    ("Solana", "索拉纳"),
    ("BNB", "BNB"),
    ("XRP", "XRP"),
    ("Gold", "黄金"),
    ("Carnival", "嘉年华邮轮"),
    ("Ivan Cepeda Castro", "伊万·塞佩达·卡斯特罗"),
    ("Colombian", "哥伦比亚"),
    ("S&P 500", "标普500"),
    ("diplomatic meeting", "外交会谈"),
    ("Signing Ceremony", "签署仪式"),
    ("Fed interest rates", "美联储利率"),
    ("interest rates", "利率"),
    ("approval rating", "支持率"),
    ("quarterly earnings", "季度业绩"),
    ("presidential election", "总统选举"),
    ("permanent peace deal", "永久和平协议"),
    ("airspace", "领空"),
    ("end enrichment of uranium", "停止铀浓缩"),
    ("enrichment of uranium", "铀浓缩"),
    ("withdraw troops", "撤军"),
    ("Iranian region", "伊朗地区"),
)


def resolve_focus_categories(focus: str | list[str] | tuple[str, ...] | None) -> set[str] | None:
    """Resolve user-facing focus areas to normalized market categories."""
    if focus is None:
        return set(FOCUS_PRESETS["default"])
    if isinstance(focus, str):
        items = [item.strip().lower() for item in focus.split(",") if item.strip()]
    else:
        items = [str(item).strip().lower() for item in focus if str(item).strip()]
    if not items or "all" in items or "*" in items:
        return None
    categories: set[str] = set()
    for item in items:
        categories.update(FOCUS_PRESETS.get(item, {item}))
    return categories


def translate_outcome_zh(value: Any) -> str:
    text = str(value or "").strip()
    return OUTCOME_ZH.get(text, text)


def _translate_phrases(value: str) -> str:
    text = value
    for source, target in PHRASE_ZH:
        text = re.sub(re.escape(source), target, text, flags=re.IGNORECASE)
    return text


def _translate_date(value: str) -> str:
    text = value.strip()
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?", text)
    if not match:
        return text
    month_map = {
        "january": "1月",
        "february": "2月",
        "march": "3月",
        "april": "4月",
        "may": "5月",
        "june": "6月",
        "july": "7月",
        "august": "8月",
        "september": "9月",
        "october": "10月",
        "november": "11月",
        "december": "12月",
    }
    month = month_map.get(match.group(1).lower())
    if not month:
        return text
    day = f"{int(match.group(2))}日"
    year = f"{match.group(3)}年" if match.group(3) else ""
    return f"{year}{month}{day}"


def _translate_dateish(value: str) -> str:
    text = value.strip()
    month_only_map = {
        "january": "1月",
        "february": "2月",
        "march": "3月",
        "april": "4月",
        "may": "5月",
        "june": "6月",
        "july": "7月",
        "august": "8月",
        "september": "9月",
        "october": "10月",
        "november": "11月",
        "december": "12月",
    }
    if text.lower() in month_only_map:
        return month_only_map[text.lower()]
    month_year = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", text)
    if month_year:
        month = _translate_date(f"{month_year.group(1)} 1")
        return f"{month_year.group(2)}年{month.removesuffix('1日')}"
    range_match = re.fullmatch(
        r"([A-Za-z]+)\s+(\d{1,2})-(\d{1,2})(?:,\s*(\d{4}))?",
        text,
    )
    if range_match:
        month = range_match.group(1)
        start = range_match.group(2)
        end = range_match.group(3)
        year = range_match.group(4)
        prefix = f"{year}年" if year else ""
        return f"{prefix}{_translate_date(f'{month} {start}')}至{_translate_date(f'{month} {end}')}"
    text = re.sub(
        r"([A-Za-z]+)\s+\d{1,2}(?:,\s*\d{4})?",
        lambda match: _translate_date(match.group(0)),
        text,
    )
    return text


def _translate_direction_window(value: str) -> str:
    text = value.strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*-\s*", "，", text)
    text = _translate_dateish(text)
    text = text.replace(",", "，")
    text = re.sub(r"，\s+", "，", text)
    text = re.sub(r"\bAM\b", "AM", text, flags=re.IGNORECASE)
    text = re.sub(r"\bPM\b", "PM", text, flags=re.IGNORECASE)
    text = text.replace("ET", "美东时间")
    return text


def translate_question_zh(question: str) -> str:
    text = question.strip()
    text = text.rstrip()

    match = re.fullmatch(r"Exact Score:\s*(.+?)\?", text, flags=re.IGNORECASE)
    if match:
        return f"精确比分：{_translate_phrases(match.group(1))}？"

    match = re.fullmatch(
        r"Will\s+(.+?)\s+post\s+(.+?)\s+posts\s+from\s+(.+?)\s+to\s+(.+?)\?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        subject = _translate_phrases(match.group(1))
        count = match.group(2)
        start_raw = match.group(3)
        end_raw = match.group(4)
        start = _translate_dateish(start_raw)
        end = _translate_dateish(end_raw)
        year_match = re.search(r",\s*(\d{4})$", end_raw)
        if year_match and not re.search(r"\d{4}", start_raw):
            year = year_match.group(1)
            start = f"{year}年{start}"
            end = end.removeprefix(f"{year}年")
        return f"{subject}会在{start}至{end}发布 {count} 条帖子吗？"

    match = re.fullmatch(
        r"US x Iran diplomatic meeting by (.+?)\?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return f"美国与伊朗会在{_translate_dateish(match.group(1))}前举行外交会谈吗？"

    match = re.fullmatch(
        r"Iran agrees to end enrichment of uranium by (.+?)\?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return f"伊朗会在{_translate_dateish(match.group(1))}前同意停止铀浓缩吗？"

    match = re.fullmatch(
        r"Will the next diplomatic US-Iran meeting be in (.+?)\?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return f"下一次美伊外交会谈会在{_translate_phrases(match.group(1))}举行吗？"

    match = re.fullmatch(
        r"Will\s+(.+?)\s+attend the US-Iran Signing Ceremony\?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return f"{_translate_phrases(match.group(1))}会出席美伊签署仪式吗？"

    match = re.fullmatch(
        r"Will the price of (.+?) be above \$(.+?) on (.+?)\?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        asset = _translate_phrases(match.group(1))
        price = match.group(2)
        date = _translate_dateish(match.group(3))
        return f"{date}{asset}价格会高于 {price} 美元吗？"

    match = re.fullmatch(
        r"Will the price of (.+?) be between \$(.+?) and \$(.+?) on (.+?)\?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        asset = _translate_phrases(match.group(1))
        low = match.group(2)
        high = match.group(3)
        date = _translate_dateish(match.group(4))
        return f"{date}{asset}价格会在 {low} 至 {high} 美元之间吗？"

    match = re.fullmatch(
        r"Will\s+(.+?)\s+hit \$(.+?) in (.+?)\?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        asset = _translate_phrases(match.group(1))
        price = match.group(2)
        date = _translate_dateish(match.group(3))
        return f"{date}{asset}会达到 {price} 美元吗？"

    match = re.fullmatch(
        r"Will\s+(.+?)\s+dip to \$(.+?)\s+(.+?)\?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        asset = _translate_phrases(match.group(1))
        price = match.group(2)
        date = _translate_dateish(match.group(3))
        return f"{date}期间{asset}会跌至 {price} 美元吗？"

    match = re.fullmatch(r"(.+?) Opens Up or Down on (.+?)\?", text, flags=re.IGNORECASE)
    if match:
        asset = _translate_phrases(match.group(1))
        date = _translate_dateish(match.group(2))
        return f"{date}{asset}开盘上涨还是下跌？"

    match = re.fullmatch(r"(.+?) Up or Down on (.+?)\?", text, flags=re.IGNORECASE)
    if match:
        asset = _translate_phrases(match.group(1))
        date = _translate_dateish(match.group(2))
        return f"{date}{asset}上涨还是下跌？"

    match = re.fullmatch(r"(.+?) Up or Down\s*-\s*(.+)", text, flags=re.IGNORECASE)
    if match:
        asset = _translate_phrases(match.group(1))
        window = _translate_direction_window(match.group(2))
        return f"{window}{asset}上涨还是下跌？"

    match = re.fullmatch(r"(.+?) Opens Up or Down\s*-\s*(.+)", text, flags=re.IGNORECASE)
    if match:
        asset = _translate_phrases(match.group(1))
        window = _translate_direction_window(match.group(2))
        return f"{window}{asset}开盘上涨还是下跌？"

    match = re.fullmatch(
        r"Will there be no change in Fed interest rates after the (.+?) meeting\?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        date = _translate_dateish(match.group(1))
        return f"{date}会议后，美联储利率会维持不变吗？"

    match = re.fullmatch(
        r"Will the Fed (increase|raise|decrease|cut) interest rates by (.+?) "
        r"after the (.+?) meeting\?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        direction = match.group(1).lower()
        action = "加息" if direction in {"increase", "raise"} else "降息"
        amount = match.group(2)
        date = _translate_dateish(match.group(3))
        return f"{date}会议后，美联储会{action} {amount} 吗？"

    match = re.fullmatch(
        r"Will\s+(.+?)'?\s*['’]s approval rating be between (.+?) and (.+?) on (.+?)\?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        person = _translate_phrases(match.group(1))
        low = match.group(2)
        high = match.group(3)
        date = _translate_dateish(match.group(4))
        return f"{date}{person}支持率会在 {low} 至 {high} 之间吗？"

    match = re.fullmatch(r"Will\s+(.+?)\s+beat quarterly earnings\?", text, flags=re.IGNORECASE)
    if match:
        company = _translate_phrases(match.group(1))
        return f"{company}季度业绩会超预期吗？"

    match = re.fullmatch(
        r"Will\s+(.+?)\s+agree to withdraw troops from the Iranian region by (.+?)\?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        actor = _translate_phrases(match.group(1))
        date = _translate_dateish(match.group(2))
        return f"{actor}会在{date}前同意从伊朗地区撤军吗？"

    match = re.fullmatch(r"(.+?) x (.+?) permanent peace deal by (.+?)\?", text, flags=re.IGNORECASE)
    if match:
        side_a = _translate_phrases(match.group(1))
        side_b = _translate_phrases(match.group(2))
        date = _translate_dateish(match.group(3))
        return f"{side_a}与{side_b}会在{date}前达成永久和平协议吗？"

    match = re.fullmatch(r"Will\s+(.+?)\s+close its airspace by (.+?)\?", text, flags=re.IGNORECASE)
    if match:
        actor = _translate_phrases(match.group(1))
        date = _translate_dateish(match.group(2))
        return f"{actor}会在{date}前关闭领空吗？"

    match = re.fullmatch(r"(.+?) closes its airspace by (.+?)\?", text, flags=re.IGNORECASE)
    if match:
        actor = _translate_phrases(match.group(1))
        date = _translate_dateish(match.group(2))
        return f"{actor}会在{date}前关闭领空吗？"

    match = re.fullmatch(r"(.+?) withdraws from (.+?) by (.+?)\?", text, flags=re.IGNORECASE)
    if match:
        actor = _translate_phrases(match.group(1))
        place = _translate_phrases(match.group(2))
        date = _translate_dateish(match.group(3))
        return f"{actor}会在{date}前从{place}撤出吗？"

    match = re.fullmatch(
        r"Will\s+(.+?)\s+win the (.+?) presidential election\?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        person = _translate_phrases(match.group(1))
        election = _translate_phrases(match.group(2))
        return f"{person}会赢得{election}总统选举吗？"

    match = re.fullmatch(
        r"Will\s+(.+?)\s+by\s+(.+?)\?",
        text,
        flags=re.IGNORECASE,
    )
    if match and re.search(r"[A-Za-z]+\s+\d{1,2}(?:,\s*\d{4})?$", match.group(2)):
        phrase = _translate_phrases(match.group(1))
        date = _translate_dateish(match.group(2))
        return f"{phrase}会在{date}前发生吗？"

    match = re.fullmatch(r"Will\s+(.+?)\?", text, flags=re.IGNORECASE)
    if match:
        phrase = _translate_phrases(match.group(1))
        return f"{phrase}会发生吗？"

    return _translate_phrases(text)


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _parse_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [item.strip() for item in value.split(",") if item.strip()]
        return parsed if isinstance(parsed, list) else []
    return []


def _first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    return {}


def _tags_from(value: Any) -> list[str]:
    tags = []
    for item in _parse_list(value):
        if isinstance(item, dict):
            label = item.get("label") or item.get("slug") or item.get("name")
            if label:
                tags.append(str(label))
        elif item:
            tags.append(str(item))
    return tags


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _days_since(value: Any) -> float | None:
    parsed = _parse_dt(value)
    if parsed is None:
        return None
    elapsed = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    return max(0.0, elapsed.total_seconds() / 86400)


def _infer_category(text: str, tags: list[str]) -> str:
    haystack = " ".join([text, *tags]).lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(_contains_keyword(haystack, keyword) for keyword in keywords):
            return category
    return "other"


def _contains_keyword(haystack: str, keyword: str) -> bool:
    value = keyword.strip().lower()
    if not value:
        return False
    pattern = rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def _slug_url(slug: str | None, event_slug: str | None) -> str:
    if event_slug:
        return f"https://polymarket.com/event/{event_slug}"
    if slug:
        return f"https://polymarket.com/market/{slug}"
    return "https://polymarket.com/"


def _primary_probability(outcomes: list[Any], prices: list[Any]) -> dict[str, Any]:
    rows = []
    for index, outcome in enumerate(outcomes):
        price = _as_float(prices[index], default=0.0) if index < len(prices) else 0.0
        rows.append({"outcome": str(outcome), "price": price})
    if not rows:
        return {"outcome": "", "probability": None, "outcome_prices": []}
    yes = next((row for row in rows if row["outcome"].strip().lower() == "yes"), None)
    primary = yes or max(rows, key=lambda row: row["price"])
    return {
        "outcome": primary["outcome"],
        "probability": primary["price"],
        "outcome_prices": rows,
    }


def normalize_market(raw: dict[str, Any], pool_hits: list[str] | None = None) -> dict[str, Any]:
    event = _first_dict(raw.get("events"))
    outcomes = _parse_list(raw.get("outcomes"))
    prices = _parse_list(raw.get("outcomePrices"))
    probability = _primary_probability(outcomes, prices)
    tags = [*_tags_from(raw.get("tags")), *_tags_from(event.get("tags"))]
    question = str(raw.get("question") or raw.get("title") or "").strip()
    event_title = str(event.get("title") or "").strip()
    category = _infer_category(f"{question} {event_title}", tags)

    one_day_change = _as_float(raw.get("oneDayPriceChange"), 0.0)
    one_hour_change = _as_float(raw.get("oneHourPriceChange"), 0.0)
    volume_24h = _as_float(raw.get("volume24hr") or raw.get("volume24hrClob"), 0.0)
    volume_1w = _as_float(raw.get("volume1wk") or raw.get("volume1wkClob"), 0.0)
    liquidity = _as_float(
        raw.get("liquidityNum") or raw.get("liquidity") or raw.get("liquidityClob"),
        0.0,
    )
    total_volume = _as_float(raw.get("volumeNum") or raw.get("volume"), 0.0)
    days_old = _days_since(raw.get("createdAt"))
    recency_score = max(0.0, 6.0 - (days_old or 999.0)) if days_old is not None else 0.0
    score = (
        math.log1p(max(volume_24h, 0.0)) * 8.0
        + math.log1p(max(volume_1w, 0.0)) * 2.0
        + math.log1p(max(liquidity, 0.0)) * 1.2
        + abs(one_day_change) * 150.0
        + abs(one_hour_change) * 80.0
        + recency_score
    )

    return {
        "market_id": str(raw.get("id") or raw.get("conditionId") or raw.get("slug") or ""),
        "condition_id": str(raw.get("conditionId") or ""),
        "question": question,
        "question_zh": translate_question_zh(question),
        "slug": str(raw.get("slug") or ""),
        "url": _slug_url(str(raw.get("slug") or ""), str(event.get("slug") or "")),
        "event": {
            "id": str(event.get("id") or ""),
            "title": event_title,
            "title_zh": _translate_phrases(event_title),
            "slug": str(event.get("slug") or ""),
            "volume_24h": _as_float(event.get("volume24hr"), 0.0),
            "comment_count": int(_as_float(event.get("commentCount"), 0.0)),
        },
        "category": category,
        "category_zh": CATEGORY_ZH.get(category, category),
        "tags": sorted(set(tags)),
        "end_date": raw.get("endDate") or raw.get("endDateIso"),
        "created_at": raw.get("createdAt"),
        "updated_at": raw.get("updatedAt"),
        "active": _as_bool(raw.get("active")),
        "closed": _as_bool(raw.get("closed")),
        "accepting_orders": _as_bool(raw.get("acceptingOrders")),
        "primary_outcome": probability["outcome"],
        "primary_outcome_zh": translate_outcome_zh(probability["outcome"]),
        "primary_probability": probability["probability"],
        "outcome_prices": probability["outcome_prices"],
        "best_bid": _as_float(raw.get("bestBid"), 0.0),
        "best_ask": _as_float(raw.get("bestAsk"), 0.0),
        "last_trade_price": _as_float(raw.get("lastTradePrice"), 0.0),
        "spread": _as_float(raw.get("spread"), 0.0),
        "volume": total_volume,
        "volume_24h": volume_24h,
        "volume_1w": volume_1w,
        "volume_1m": _as_float(raw.get("volume1mo") or raw.get("volume1moClob"), 0.0),
        "liquidity": liquidity,
        "open_interest": _as_float(event.get("openInterest"), 0.0),
        "price_change_1h": one_hour_change,
        "price_change_1d": one_day_change,
        "price_change_1w": _as_float(raw.get("oneWeekPriceChange"), 0.0),
        "pool_hits": sorted(set(pool_hits or [])),
        "hotspot_score": round(score, 4),
        "hotspot_reasons": hotspot_reasons(
            volume_24h=volume_24h,
            volume_1w=volume_1w,
            liquidity=liquidity,
            price_change_1d=one_day_change,
            created_at=raw.get("createdAt"),
            pool_hits=pool_hits or [],
        ),
    }


def hotspot_reasons(
    *,
    volume_24h: float,
    volume_1w: float,
    liquidity: float,
    price_change_1d: float,
    created_at: Any,
    pool_hits: list[str],
) -> list[str]:
    reasons: list[str] = []
    if volume_24h > 0:
        reasons.append(f"24h成交额 ${volume_24h:,.0f}")
    if volume_1w > 0 and "volume_1w" in pool_hits:
        reasons.append(f"7日成交额 ${volume_1w:,.0f}")
    if liquidity > 0 and "liquidity" in pool_hits:
        reasons.append(f"流动性 ${liquidity:,.0f}")
    if price_change_1d:
        reasons.append(f"1日概率变化 {price_change_1d * 100:+.1f}pct")
    days_old = _days_since(created_at)
    if days_old is not None and days_old <= 2:
        reasons.append("新上线市场")
    return reasons[:5]


def build_hotspots(
    raw_snapshot: dict[str, Any],
    *,
    top: int = 25,
    focus: str | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    raw_by_id: dict[str, dict[str, Any]] = {}
    pool_hits: dict[str, list[str]] = {}
    for pool in raw_snapshot.get("pools") or []:
        pool_name = str(pool.get("pool") or "")
        payload = pool.get("payload")
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or item.get("conditionId") or item.get("slug") or "")
            if not key:
                continue
            raw_by_id.setdefault(key, item)
            pool_hits.setdefault(key, []).append(pool_name)

    all_markets = [normalize_market(raw, pool_hits.get(key, [])) for key, raw in raw_by_id.items()]
    all_markets = [
        market
        for market in all_markets
        if market["active"] is not False and market["closed"] is not True
    ]
    focus_categories = resolve_focus_categories(focus)
    if focus_categories is None:
        markets = all_markets
    else:
        markets = [market for market in all_markets if market["category"] in focus_categories]
    ranked = sorted(markets, key=lambda item: item["hotspot_score"], reverse=True)
    top_markets = ranked[:top]

    category_rows = []
    by_category: dict[str, list[dict[str, Any]]] = {}
    for market in ranked:
        by_category.setdefault(market["category"], []).append(market)
    for category, rows in by_category.items():
        category_rows.append(
            {
                "category": category,
                "category_zh": CATEGORY_ZH.get(category, category),
                "market_count": len(rows),
                "volume_24h": round(sum(item["volume_24h"] for item in rows), 2),
                "liquidity": round(sum(item["liquidity"] for item in rows), 2),
                "top_market": rows[0]["question"],
                "top_market_zh": rows[0].get("question_zh") or rows[0]["question"],
                "top_market_id": rows[0]["market_id"],
            }
        )
    category_rows.sort(key=lambda item: item["volume_24h"], reverse=True)

    leaderboards = {
        "volume_24h": sorted(markets, key=lambda item: item["volume_24h"], reverse=True)[:top],
        "price_moves_1d": sorted(
            markets,
            key=lambda item: abs(item["price_change_1d"]),
            reverse=True,
        )[:top],
        "liquidity": sorted(markets, key=lambda item: item["liquidity"], reverse=True)[:top],
        "new_markets": sorted(
            markets,
            key=lambda item: str(item.get("created_at") or ""),
            reverse=True,
        )[:top],
    }

    return {
        "schema_version": "polymarket_hotspots.v1",
        "status": "candidate",
        "generated_at": utc_now_iso(),
        "source": {
            "source_type": raw_snapshot.get("source"),
            "cli": raw_snapshot.get("cli"),
            "collected_at": raw_snapshot.get("collected_at"),
            "pool_count": len(raw_snapshot.get("pools") or []),
            "errors": raw_snapshot.get("errors") or [],
        },
        "stats": {
            "unique_markets": len(all_markets),
            "focused_markets": len(markets),
            "excluded_by_focus": len(all_markets) - len(markets),
            "hotspot_count": len(top_markets),
            "category_count": len(category_rows),
            "total_volume_24h_in_sample": round(sum(item["volume_24h"] for item in markets), 2),
        },
        "focus": {
            "enabled": focus_categories is not None,
            "requested": focus,
            "categories": sorted(focus_categories) if focus_categories is not None else ["all"],
        },
        "scoring_policy": {
            "formula": (
                "log(volume_24h)*8 + log(volume_1w)*2 + log(liquidity)*1.2 "
                "+ abs(1d_change)*150 + abs(1h_change)*80 + recency"
            ),
            "note": "Score is for triage only; it is not an investment signal.",
        },
        "hotspots": top_markets,
        "leaderboards": leaderboards,
        "category_summary": category_rows,
    }


def _fmt_money(value: Any) -> str:
    number = _as_float(value, 0.0)
    if number >= 1_000_000:
        return f"${number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"${number / 1_000:.1f}K"
    return f"${number:.0f}"


def _fmt_prob(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{_as_float(value, 0.0) * 100:.1f}%"


def _display_question(item: dict[str, Any]) -> str:
    return str(item.get("question_zh") or item.get("question") or "")


def _fallback_report(hotspots: dict[str, Any], *, report_date: str) -> dict[str, Any]:
    markets = hotspots.get("hotspots") or []
    categories = hotspots.get("category_summary") or []
    top = markets[:5]
    summary_bullets = []
    if top:
        summary_bullets.append(
            "样本内最热市场是"
            f"「{_display_question(top[0])}」，24h成交额{_fmt_money(top[0]['volume_24h'])}，"
            f"主结果概率{_fmt_prob(top[0]['primary_probability'])}。"
        )
    if categories:
        leading = ", ".join(
            f"{item.get('category_zh') or item['category']}({_fmt_money(item['volume_24h'])})"
            for item in categories[:4]
        )
        summary_bullets.append(f"按24h成交额看，热点集中在 {leading}。")
    movers = (hotspots.get("leaderboards") or {}).get("price_moves_1d") or []
    movers = [item for item in movers if abs(item.get("price_change_1d") or 0) > 0][:3]
    if movers:
        summary_bullets.append(
            "概率变化较大的市场包括："
            + "；".join(
                f"{_display_question(item)}({item['price_change_1d'] * 100:+.1f}pct)"
                for item in movers
            )
            + "。"
        )
    if not summary_bullets:
        summary_bullets.append("本次 CLI 样本未发现足够活跃的可排序市场，需要扩大采样或检查数据源状态。")

    sections = [
        {
            "title": "市场热点",
            "bullets": [
                f"{_display_question(item)}：24h成交{_fmt_money(item['volume_24h'])}，"
                f"流动性{_fmt_money(item['liquidity'])}，"
                f"{item.get('primary_outcome_zh') or item['primary_outcome']}概率"
                f"{_fmt_prob(item['primary_probability'])}，"
                f"驱动：{', '.join(item['hotspot_reasons']) or '样本评分靠前'}。"
                for item in top
            ],
        },
        {
            "title": "板块分布",
            "bullets": [
                f"{item.get('category_zh') or item['category']}：样本市场{item['market_count']}个，"
                f"24h成交{_fmt_money(item['volume_24h'])}，"
                f"代表市场「{item.get('top_market_zh') or item['top_market']}」。"
                for item in categories[:6]
            ],
        },
        {
            "title": "需要复核",
            "bullets": [
                "Polymarket 概率是交易价格，不等同于真实概率；低流动性或宽价差市场尤其需要人工复核。",
                "本日报只基于 Polymarket CLI 返回的市场、成交、流动性和价格变化字段生成，未自动引入外部新闻证据。",
            ],
        },
    ]
    return {
        "schema_version": "polymarket_daily_report.v1",
        "status": "candidate",
        "title": f"{report_date} Polymarket 市场日报",
        "date": report_date,
        "generated_at": utc_now_iso(),
        "language": "zh-CN",
        "analysis_method": "rule_fallback",
        "summary_bullets": summary_bullets,
        "sections": sections,
        "risk_notes": [
            "预测市场价格可能受流动性、做市激励、事件临近和交易者结构影响。",
            "该产物是机器生成候选日报，进入 gold 或正式观点前需要人工审核。",
        ],
    }


def build_daily_report(
    hotspots: dict[str, Any],
    *,
    report_date: str,
    use_llm: bool = True,
) -> dict[str, Any]:
    fallback = _fallback_report(hotspots, report_date=report_date)
    if not use_llm:
        return fallback

    compact = []
    for item in (hotspots.get("hotspots") or [])[:24]:
        compact.append(
            {
                "question": item.get("question"),
                "question_zh": item.get("question_zh"),
                "category": item.get("category"),
                "category_zh": item.get("category_zh"),
                "primary_outcome": item.get("primary_outcome"),
                "primary_outcome_zh": item.get("primary_outcome_zh"),
                "primary_probability": item.get("primary_probability"),
                "volume_24h": item.get("volume_24h"),
                "volume_1w": item.get("volume_1w"),
                "liquidity": item.get("liquidity"),
                "price_change_1d": item.get("price_change_1d"),
                "spread": item.get("spread"),
                "reasons": item.get("hotspot_reasons"),
                "url": item.get("url"),
            }
        )

    prompt_payload = json.dumps(
        {"hotspots": compact, "category_summary": hotspots.get("category_summary")},
        ensure_ascii=False,
    )
    prompt = f"""你是预测市场研究员。请只基于下面 Polymarket CLI 结构化样本，写一份中文市场日报。

严格输出 JSON，不要 Markdown，结构如下：
{{
  "title": "{report_date} Polymarket 市场日报",
  "summary_bullets": ["3-5条高信息密度摘要"],
  "sections": [
    {{"title": "市场热点", "bullets": ["..."]}},
    {{"title": "板块分布", "bullets": ["..."]}},
    {{"title": "概率变化", "bullets": ["..."]}},
    {{"title": "复核清单", "bullets": ["..."]}}
  ],
  "risk_notes": ["..."]
}}

要求：
1. 不要编造外部新闻；只能解释成交、流动性、概率、价差和分类分布。
2. 概率变化用百分点表达，例如 +3.5pct。
3. 明确提示低流动性/宽价差/新市场需要复核。
4. 语言简洁，适合投研晨会快速阅读。

Polymarket 样本：
{prompt_payload}
"""
    try:
        parsed = parse_json_object(chat(prompt, max_tokens=1800, temperature=0.2, timeout=80))
        summary = parsed.get("summary_bullets")
        sections = parsed.get("sections")
        risk_notes = parsed.get("risk_notes")
        report = fallback | {
            "analysis_method": "llm",
            "title": str(parsed.get("title") or fallback["title"]),
            "summary_bullets": (
                summary if isinstance(summary, list) else fallback["summary_bullets"]
            ),
            "sections": sections if isinstance(sections, list) else fallback["sections"],
            "risk_notes": risk_notes if isinstance(risk_notes, list) else fallback["risk_notes"],
        }
        return report
    except Exception as exc:
        fallback["analysis_method"] = "rule_fallback"
        fallback["llm_error"] = str(exc)
        return fallback


def render_markdown_report(report: dict[str, Any], hotspots: dict[str, Any]) -> str:
    lines = [
        f"# {report.get('title') or 'Polymarket 市场日报'}",
        "",
        f"- 日期：{report.get('date') or ''}",
        f"- 生成时间：{report.get('generated_at') or ''}",
        f"- 原始样本市场数：{(hotspots.get('stats') or {}).get('unique_markets', 0)}",
        f"- 聚焦后市场数：{(hotspots.get('stats') or {}).get('focused_markets', 0)}",
        f"- 分析方式：{report.get('analysis_method') or ''}",
        "",
        "## 摘要",
    ]
    for item in report.get("summary_bullets") or []:
        lines.append(f"- {item}")
    for section in report.get("sections") or []:
        title = section.get("title") if isinstance(section, dict) else ""
        bullets = section.get("bullets") if isinstance(section, dict) else []
        lines.extend(["", f"## {title or 'Section'}"])
        for bullet in bullets or []:
            lines.append(f"- {bullet}")
    lines.extend(["", "## Top Markets"])
    for index, item in enumerate((hotspots.get("hotspots") or [])[:10], 1):
        change = _as_float(item.get("price_change_1d"), 0.0) * 100
        lines.append(
            f"{index}. [{_display_question(item)}]({item.get('url')}) | "
            f"{item.get('category_zh') or item.get('category')} | "
            f"24h {_fmt_money(item.get('volume_24h'))} | "
            f"{item.get('primary_outcome_zh') or item.get('primary_outcome')} "
            f"{_fmt_prob(item.get('primary_probability'))} | "
            f"1d {change:+.1f}pct"
        )
    if report.get("risk_notes"):
        lines.extend(["", "## 风险提示"])
        for note in report.get("risk_notes") or []:
            lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def slugify(value: str, fallback: str = "market") -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower())
    text = text.strip("-._")
    return (text or fallback)[:96]
