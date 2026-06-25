from __future__ import annotations

import re
from typing import Any


_MOVE_RE = re.compile(
    r"涨停|跌停|涨幅|跌幅|涨超|跌超|大涨|大跌|收涨|收跌|收平|高开|低开|平开|跳空|"
    r"拉升|跳水|封板|飘红|翻绿|续创|创(?:阶段|历史|年内)?新(?:高|低)|刷新.{0,4}(?:高点|低点)|"
    r"站上|跌破|失守|回吐|领涨|领跌|主力合约.{0,6}(?:涨|跌)|夜盘|日盘|盘中|开盘|收盘|"
    r"结算价|现报|最新报|报收"
)
_PCT_RE = re.compile(r"[涨跌]\s*[\d.]+\s*%|[涨跌]\s*\d|[+-]?[\d.]+\s*%")
_PRICE_SUBJECT_RE = re.compile(
    r"现货|期货|主力合约|合约|金价|银价|油价|黄金|白银|钯金|铜|铝|镍|原油|"
    r"沪金|沪银|沪铜|沪铝|SC原油|Brent|WTI|Gold|Silver|Murban|crude|"
    r"spot|futures|contract|ounce|barrel|official selling price|OSP",
    re.IGNORECASE,
)
_PRICE_ACTION_EN_RE = re.compile(
    r"\b(falls?|fell|drops?|dropped|rises?|rose|jumps?|jumped|settles?|settled|"
    r"closes?|closed|trades?|traded|breaks?|broke)\b.{0,80}"
    r"(\bbelow\b|\babove\b|%|dollars?|ounce|barrel|points?)",
    re.IGNORECASE,
)
_MARKET_COMMENTARY_RE = re.compile(
    r"空头|多头|获利了结|反弹|技术位|支撑位|压力位|表现偏弱|表现偏强|"
    r"羸弱|风险溢价.{0,6}(回吐|消退)|走势|跌多涨少|涨多跌少|"
    r"基差数据更新|期货基差数据|收盘播报"
)
_QUOTE_LINE_RE = re.compile(
    r"截至.{0,12}收盘|日内|现报|报收|收于|跌破|站上|主力合约|夜盘|"
    r"每桶|官方售价|\bfalls?\s+below\b|\bsettles?\b|\bspot\b|"
    r"\bofficial selling price\b|\bOSP\b|\bper barrel\b|\$\d",
    re.IGNORECASE,
)
_PREVIEW_RE = re.compile(
    r"将于.{0,12}(公布|发布)|即将公布|due in \d+ minutes|will be released|to be released",
    re.IGNORECASE,
)
_REAL_EVENT_RE = re.compile(
    r"库存|产量|供应|需求|OPEC|EIA|API|制裁|关税|出口管制|禁运|配额|"
    r"减产|增产|限产|停产|复产|检修|投产|事故|爆炸|罢工|停工|"
    r"停火|袭击|战争|冲突|封锁|通航|航运恢复|会谈|谈判|否认|"
    r"批准|出台|政策|计划|目标|调查|违法|拘留|刑拘|辞职|签署|协议|"
    r"发布|公布|MLF|LPR|CPI|PPI|PCE|GDP|PMI|非农|失业|就业|初请|"
    r"社融|信贷|M2|进口|出口|会议|声明|讲话|纪要|决议"
)
_SIGNAL_KW: tuple[str, ...] = (
    "数据",
    "公布",
    "发布",
    "录得",
    "预估",
    "前值",
    "初值",
    "终值",
    "CPI",
    "PPI",
    "PCE",
    "GDP",
    "PMI",
    "非农",
    "失业",
    "就业",
    "初请",
    "社融",
    "信贷",
    "M2",
    "进口",
    "出口",
    "贸易",
    "库存",
    "EIA",
    "API",
    "产量",
    "供应",
    "需求",
    "开工率",
    "决议",
    "会议",
    "声明",
    "讲话",
    "纪要",
    "点阵图",
    "议息",
    "降息",
    "加息",
    "降准",
    "LPR",
    "利率",
    "缩表",
    "扩表",
    "评级",
    "签署",
    "协议",
    "制裁",
    "关税",
    "出口管制",
    "禁运",
    "配额",
    "减产",
    "增产",
    "限产",
    "停产",
    "复产",
    "检修",
    "投产",
    "事故",
    "爆炸",
    "罢工",
    "停工",
    "停火",
    "袭击",
    "战争",
    "冲突",
    "封锁",
    "批准",
    "出台",
    "政策",
    "计划",
    "目标",
    "突发",
    "紧急",
    "预警",
)


def _text_of(flash: dict[str, Any]) -> str:
    return f"{flash.get('title') or ''} {flash.get('content') or ''}"


def is_market_move(text: str) -> bool:
    return bool(text and (_MOVE_RE.search(text) or _PCT_RE.search(text)))


def is_market_update_noise(text: str) -> bool:
    """Return True for quote-board style market updates, not real-world events.

    Half-day news briefs are read by humans as event summaries. Price moves, close
    recaps, quote levels and technical-position commentary should become
    MarketObservation later, but they should not occupy the news-event narrative.
    """
    if not text:
        return False
    price_subject = bool(_PRICE_SUBJECT_RE.search(text))
    quote_line = bool(_QUOTE_LINE_RE.search(text) or _PRICE_ACTION_EN_RE.search(text))
    market_commentary = bool(_MARKET_COMMENTARY_RE.search(text) and price_subject)
    if not (is_market_move(text) or quote_line or market_commentary):
        return False
    # Keep event-first news where the price phrase is secondary to a real event.
    if _REAL_EVENT_RE.search(text) and not quote_line:
        return False
    return price_subject or quote_line or market_commentary


def has_signal(text: str) -> bool:
    return any(keyword in text for keyword in _SIGNAL_KW)


def keep_flash(flash: dict[str, Any]) -> bool:
    text = _text_of(flash)
    if has_signal(text):
        return True
    if int(flash.get("important") or 0):
        return True
    return not is_market_move(text)


def keep_news_brief_flash(flash: dict[str, Any]) -> bool:
    text = _text_of(flash)
    if _PREVIEW_RE.search(text):
        return False
    if is_market_update_noise(text):
        return False
    return keep_flash(flash)
