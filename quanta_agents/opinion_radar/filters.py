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


def has_signal(text: str) -> bool:
    return any(keyword in text for keyword in _SIGNAL_KW)


def keep_flash(flash: dict[str, Any]) -> bool:
    text = _text_of(flash)
    if has_signal(text):
        return True
    if int(flash.get("important") or 0):
        return True
    return not is_market_move(text)
