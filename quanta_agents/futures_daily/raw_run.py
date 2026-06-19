from __future__ import annotations

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import dated_parts, sha256_bytes, utc_now_iso, write_json
from quanta_agents.core.llm_client import chat
from quanta_agents.core.llm_json import parse_json_object
from quanta_agents.core.taxonomy import load_asset_taxonomy

from .framework_alignment import build_framework_alignment
from .logic_chain import build_logic_chain_bundle
from .raw_ingest import raw_folder_for_date


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _chat(prompt: str, *, max_tokens: int = 900, temperature: float = 0.2, timeout: int = 80) -> str:
    return chat(prompt, max_tokens=max_tokens, temperature=temperature, timeout=timeout)


def _load_rows(raw_folder: Path) -> list[dict[str, Any]]:
    rows_path = raw_folder / "rows.jsonl"
    if not rows_path.exists():
        raise FileNotFoundError(f"日报原文目录缺少 rows.jsonl：{rows_path}")
    rows = []
    for line in rows_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _text(row: dict[str, Any]) -> str:
    return f"{row.get('title') or ''}\n{row.get('content') or ''}".strip()


def _sentences(text: str) -> list[str]:
    chunks = re.split(r"(?<=[。！？；;])\s*|\n+", text)
    return [chunk.strip() for chunk in chunks if len(chunk.strip()) >= 8]


NO_EVIDENCE_PATTERNS = (
    "原文未提供",
    "原文无",
    "原文没有",
    "未提供",
    "未给出",
    "未出现",
    "没有给出",
    "没有足够",
    "缺少足够",
    "缺乏对应",
    "无法形成有效判断",
    "不构成基本面预测",
    "仅有盘面",
    "仅为价格表现",
    "不计入基本面",
)


def _is_placeholder_factor(text: Any) -> bool:
    value = str(text or "").strip()
    if not value:
        return True
    return any(pattern in value for pattern in NO_EVIDENCE_PATTERNS)


def _clean_factor_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    out = []
    for item in values:
        text = str(item or "").strip()
        if text and not _is_placeholder_factor(text):
            out.append(text)
    return out


def _bounded_float(value: Any, *, lower: float, upper: float, default: float = 0.0) -> float:
    try:
        number = float(str(value).strip().replace("%", ""))
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _source_identity(item: dict[str, Any]) -> dict[str, str]:
    return {
        "row_id": str(item.get("row_id") or ""),
        "org_name": str(item.get("org_name") or ""),
        "title": str(item.get("title") or ""),
    }


def _audit_source_index(entry: dict[str, Any], snippets: list[dict[str, Any]]) -> int | None:
    index = _int_or_none(entry.get("source_index"))
    if index and 1 <= index <= len(snippets):
        return index
    row_id = str(entry.get("row_id") or "").strip()
    if row_id:
        for idx, item in enumerate(snippets, 1):
            if str(item.get("row_id") or "").strip() == row_id:
                return idx
    return None


def _weighted_score_audit(
    parsed: dict[str, Any],
    snippets: list[dict[str, Any]],
    *,
    holistic_score: float,
) -> tuple[dict[str, Any], float]:
    raw_entries = parsed.get("source_score_audit") or parsed.get("score_audit") or []
    if not isinstance(raw_entries, list):
        raw_entries = []

    by_index: dict[int, dict[str, Any]] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        source_index = _audit_source_index(entry, snippets)
        if source_index is None:
            continue
        score = _bounded_float(
            entry.get("report_sentiment_score", entry.get("sentiment_score")),
            lower=-10.0,
            upper=10.0,
        )
        influence = _bounded_float(
            entry.get("influence_score", entry.get("weight")),
            lower=0.0,
            upper=1.0,
        )
        by_index[source_index] = {
            "source_index": source_index,
            "report_sentiment_score": round(score, 2),
            "influence_score": round(influence, 4),
            "evidence_summary": str(entry.get("evidence_summary") or entry.get("summary") or "").strip(),
            "score_reason": str(entry.get("score_reason") or entry.get("reason") or "").strip(),
            "covered_by_llm": True,
        }

    source_scores = []
    total_score = 0.0
    total_weight = 0.0
    for idx, item in enumerate(snippets, 1):
        row = {
            "source_index": idx,
            **_source_identity(item),
            "report_sentiment_score": 0.0,
            "influence_score": 0.0,
            "evidence_summary": "",
            "score_reason": "M3 未返回该来源的单篇审核，按零权重处理。",
            "covered_by_llm": False,
        }
        if idx in by_index:
            row.update(by_index[idx])
        score = float(row["report_sentiment_score"])
        weight = float(row["influence_score"])
        total_score += score * weight
        total_weight += weight
        source_scores.append(row)

    if total_weight > 0:
        weighted_score = max(-10.0, min(10.0, total_score / total_weight))
        score_method = "source_influence_weighted_average"
    else:
        weighted_score = holistic_score
        score_method = "holistic_score_fallback_no_positive_source_weight"

    weighted_score = round(weighted_score, 2)
    audit = {
        "method": score_method,
        "formula": "sum(report_sentiment_score * influence_score) / sum(influence_score)",
        "source_count": len(snippets),
        "covered_source_count": len(by_index),
        "positive_weight_source_count": sum(1 for row in source_scores if float(row["influence_score"]) > 0),
        "total_influence_weight": round(total_weight, 4),
        "original_scores": [row["report_sentiment_score"] for row in source_scores],
        "influence_scores": [row["influence_score"] for row in source_scores],
        "weighted_sentiment_score": weighted_score,
        "holistic_sentiment_score": round(holistic_score, 2),
        "source_scores": source_scores,
    }
    return audit, weighted_score


def _fallback_analysis(asset: str, snippets: list[dict[str, Any]]) -> dict[str, Any]:
    bullish_words = ("支撑", "偏强", "上行", "改善", "去库", "减产", "供应扰动", "短缺")
    bearish_words = ("压制", "偏弱", "下行", "累库", "过剩", "需求疲弱", "增产")
    data_words = ("库存", "产量", "开工", "进口", "出口", "利润", "基差", "%", "万吨", "美元")
    event_words = ("政策", "会议", "事故", "检修", "罢工", "关税", "制裁", "地缘", "OPEC")
    market_words = ("夜盘", "日盘", "收盘", "收涨", "收跌", "盘面", "主力合约", "涨幅", "跌幅", "涨超", "跌超", "压力位", "支撑位", "技术面")
    fundamental_words = data_words + event_words + ("供给", "供应", "需求", "消费", "终端", "订单", "产能", "装置", "到港")
    all_sentences = []
    for item in snippets:
        all_sentences.extend(_sentences(item["text"]))
    all_sentences = all_sentences[:80]

    def is_market_only(sentence: str) -> bool:
        has_market = any(word in sentence for word in market_words)
        has_fundamental = any(word in sentence for word in fundamental_words)
        return has_market and not has_fundamental

    def pick(words: tuple[str, ...], limit: int) -> list[str]:
        return [sentence for sentence in all_sentences if not is_market_only(sentence) and any(word in sentence for word in words)][:limit]

    bullish = pick(bullish_words, 5)
    bearish = pick(bearish_words, 5)
    key_data = pick(data_words, 5)
    key_events = pick(event_words, 5)
    market_observations = [sentence for sentence in all_sentences if is_market_only(sentence)][:5]
    score = max(-10, min(10, len(bullish) - len(bearish)))
    source_scores = [
        {
            "source_index": idx,
            **_source_identity(item),
            "report_sentiment_score": score,
            "influence_score": 1.0 if idx == 1 else 0.0,
            "evidence_summary": "规则后备评分基于关键词命中，未做单篇 LLM 审核。",
            "score_reason": "rule_fallback",
            "covered_by_llm": False,
        }
        for idx, item in enumerate(snippets, 1)
    ]
    return {
        "commodity": asset,
        "fundamental_summary": "",
        "bullish_factors": bullish,
        "bearish_factors": bearish,
        "key_data": key_data,
        "key_events": key_events,
        "supply_demand": (bullish + bearish)[:4],
        "market_observations": market_observations,
        "price_forecast": [],
        "sentiment_score": score,
        "holistic_sentiment_score": score,
        "weighted_sentiment_score": score,
        "score_audit": {
            "method": "rule_fallback",
            "formula": "keyword_count_bullish_minus_bearish",
            "source_count": len(snippets),
            "covered_source_count": 0,
            "positive_weight_source_count": 1 if snippets else 0,
            "total_influence_weight": 1.0 if snippets else 0.0,
            "original_scores": [row["report_sentiment_score"] for row in source_scores],
            "influence_scores": [row["influence_score"] for row in source_scores],
            "weighted_sentiment_score": score,
            "holistic_sentiment_score": score,
            "source_scores": source_scores,
        },
        "analysis_method": "rule_fallback",
    }


def _analyze_asset(
    asset: str,
    snippets: list[dict[str, Any]],
    *,
    use_llm: bool,
) -> dict[str, Any]:
    if not use_llm:
        return _fallback_analysis(asset, snippets)
    source_text = "\n\n".join(
        (
            f"【来源{idx}】row_id={item.get('row_id') or ''}｜机构={item['org_name']}｜标题={item['title']}\n"
            f"{item.get('text') or ''}"
        )
        for idx, item in enumerate(snippets, 1)
    )
    prompt = f"""你是商品期货研究员。请只基于下面研报原文，提取「{asset}」的结构化分析。

严格输出 JSON，不要 Markdown：
{{
  "commodity": "{asset}",
  "fundamental_summary": "只基于基本面证据的详细概括，说明主线、反向因素和待验证点",
  "bullish_factors": ["基本面利多因素。写详细：指标/事件 + 数据/时间/口径 + 影响路径；不要写行情涨跌或技术位"],
  "bearish_factors": ["基本面利空因素。写详细：指标/事件 + 数据/时间/口径 + 影响路径；不要写行情涨跌或技术位"],
  "key_data": ["关键基本面数据。尽量保留数值、环比/同比、单位、地区、时间、口径和含义"],
  "key_events": ["关键事件。说明事件本身、发生时间、影响的供给/需求/库存/成本/政策链条"],
  "supply_demand": ["供需/库存/成本/利润/进出口等基本面变化。写成完整句，保留逻辑链"],
  "market_observations": ["行情/盘面/价格涨跌/技术位/成交持仓等交易信息，只记录，不作为基本面利多利空依据"],
  "price_forecast": ["作者的价格/策略判断。必须说明该判断依赖的基本面依据；若只是技术位或盘面，不要放入基本面字段"],
  "holistic_sentiment_score": 0.0,
  "sentiment_score": 0.0
}}

要求：
1. holistic_sentiment_score 和 sentiment_score 取 -10 到 10，允许小数；正数看多，负数看空；只能反映基本面方向，不能因为期价上涨/下跌、盘面情绪、技术位、主力合约表现而加减分。
2. 基本面字段只允许使用供给、需求、库存、成本利润、进口出口、政策、天气、装置、开工、产业链传导等证据。
3. 行情涨跌、价格区间、技术压力/支撑位、成交持仓、盘面强弱，统一放入 market_observations 或 price_forecast，不得混入 bullish_factors / bearish_factors / supply_demand。
4. 写得详细一些，不要只写短语。每条尽量包含“事实 + 数据/时间/口径 + 对该品种基本面的影响方向 + 是否仍需验证”。
5. 不要编造，原文没有就用空数组；不要把外部常识补进来。
6. 去重、合并相似表述；同一事实在多个位置出现时保留信息最完整的一条。
7. 这里只给出所有来源合并后的整体判断；单篇报告分数和影响力会在另一道报告级审核中计算。
8. fundamental_summary 控制在 220 字以内；每个数组最多 4 条，每条不超过 120 字。

研报原文：
{source_text}
"""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            parsed = parse_json_object(_chat(prompt, max_tokens=4200, timeout=180))
            parsed["commodity"] = asset
            parsed["analysis_method"] = "llm"
            parsed["llm_attempts"] = attempt + 1
            holistic_score = _bounded_float(
                parsed.get("holistic_sentiment_score", parsed.get("sentiment_score")),
                lower=-10.0,
                upper=10.0,
            )
            score_audit, weighted_score = _weighted_score_audit(parsed, snippets, holistic_score=holistic_score)
            parsed["holistic_sentiment_score"] = round(holistic_score, 2)
            parsed["weighted_sentiment_score"] = weighted_score
            parsed["score_audit"] = score_audit
            parsed["sentiment_score"] = weighted_score
            parsed["fundamental_summary"] = str(parsed.get("fundamental_summary") or "")
            for field in [
                "bullish_factors",
                "bearish_factors",
                "key_data",
                "key_events",
                "supply_demand",
                "market_observations",
                "price_forecast",
            ]:
                parsed[field] = _clean_factor_list(parsed.get(field))
            return parsed
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))

    fallback = _fallback_analysis(asset, snippets)
    fallback["llm_error"] = str(last_error) if last_error else "unknown llm error"
    fallback["llm_attempts"] = 3
    return fallback


def _normalize_report_asset_scores(
    row: dict[str, Any],
    assets: list[str],
    parsed: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    raw_entries = parsed.get("asset_scores") or parsed.get("scores") or []
    if not isinstance(raw_entries, list):
        raw_entries = []

    by_asset: dict[str, dict[str, Any]] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        asset = str(entry.get("commodity") or entry.get("asset") or entry.get("item") or "").strip()
        if asset not in assets:
            continue
        score = _bounded_float(
            entry.get("report_sentiment_score", entry.get("sentiment_score")),
            lower=-10.0,
            upper=10.0,
        )
        influence = _bounded_float(
            entry.get("influence_score", entry.get("weight")),
            lower=0.0,
            upper=1.0,
        )
        by_asset[asset] = {
            **_source_identity(row),
            "report_sentiment_score": round(score, 2),
            "influence_score": round(influence, 4),
            "evidence_summary": str(entry.get("evidence_summary") or entry.get("summary") or "").strip(),
            "score_reason": str(entry.get("score_reason") or entry.get("reason") or "").strip(),
            "covered_by_llm": True,
        }

    normalized = {}
    for asset in assets:
        normalized[asset] = by_asset.get(
            asset,
            {
                **_source_identity(row),
                "report_sentiment_score": 0.0,
                "influence_score": 0.0,
                "evidence_summary": "",
                "score_reason": "M3 未返回该品种的报告级审核，按零权重处理。",
                "covered_by_llm": False,
            },
        )
    return normalized


def _rule_report_asset_scores(row: dict[str, Any], assets: list[str]) -> dict[str, dict[str, Any]]:
    text = _text(row)
    normalized = {}
    for asset in assets:
        analysis = _fallback_analysis(
            asset,
            [{"row_id": row.get("row_id"), "org_name": row.get("org_name"), "title": row.get("title"), "text": text}],
        )
        score = float(analysis.get("sentiment_score") or 0)
        normalized[asset] = {
            **_source_identity(row),
            "report_sentiment_score": round(score, 2),
            "influence_score": 1.0 if score else 0.0,
            "evidence_summary": "规则后备评分基于关键词命中。",
            "score_reason": "rule_fallback",
            "covered_by_llm": False,
        }
    return normalized


def _dedupe_texts(values: list[str], limit: int) -> list[str]:
    seen = set()
    out = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _audit_factor(entry: dict[str, Any]) -> str:
    evidence = str(entry.get("evidence_summary") or "").strip()
    reason = str(entry.get("score_reason") or "").strip()
    if evidence and reason and reason not in evidence:
        return f"{evidence}；{reason}"
    return evidence or reason


def _looks_like_data(text: str) -> bool:
    return bool(re.search(r"\d|%|万吨|万桶|元/吨|美元|基差|库存|开工|产量|进口|出口|利润|负荷|仓单", text))


def _looks_like_event(text: str) -> bool:
    return any(word in text for word in ("政策", "会议", "协议", "制裁", "关税", "检修", "复产", "停产", "事故", "天气", "降雨", "地缘"))


def _synthesize_analysis_from_score_audit(asset: str, analysis: dict[str, Any]) -> None:
    audit = analysis.get("score_audit") or {}
    source_scores = audit.get("source_scores") or []
    if not isinstance(source_scores, list) or not source_scores:
        return

    def rank_key(entry: dict[str, Any]) -> float:
        return abs(float(entry.get("report_sentiment_score") or 0)) * float(entry.get("influence_score") or 0)

    positive = sorted(
        [entry for entry in source_scores if float(entry.get("report_sentiment_score") or 0) > 0 and float(entry.get("influence_score") or 0) > 0],
        key=rank_key,
        reverse=True,
    )
    negative = sorted(
        [entry for entry in source_scores if float(entry.get("report_sentiment_score") or 0) < 0 and float(entry.get("influence_score") or 0) > 0],
        key=rank_key,
        reverse=True,
    )
    ranked = sorted(
        [entry for entry in source_scores if float(entry.get("influence_score") or 0) > 0],
        key=rank_key,
        reverse=True,
    )

    bullish = _dedupe_texts([_audit_factor(entry) for entry in positive], 4)
    bearish = _dedupe_texts([_audit_factor(entry) for entry in negative], 4)
    key_data = _dedupe_texts([_audit_factor(entry) for entry in ranked if _looks_like_data(_audit_factor(entry))], 4)
    key_events = _dedupe_texts([_audit_factor(entry) for entry in ranked if _looks_like_event(_audit_factor(entry))], 4)
    supply_demand = _dedupe_texts([_audit_factor(entry) for entry in ranked], 4)

    neutral_notes = _dedupe_texts([_audit_factor(entry) for entry in ranked if float(entry.get("report_sentiment_score") or 0) == 0], 4)
    analysis["bullish_factors"] = bullish
    analysis["bearish_factors"] = bearish
    analysis["key_data"] = key_data
    analysis["key_events"] = key_events
    analysis["supply_demand"] = supply_demand or neutral_notes
    analysis["market_observations"] = []
    analysis["price_forecast"] = []
    score = float(analysis.get("sentiment_score") or 0)
    bias = "偏多" if score > 1 else "偏空" if score < -1 else "中性"
    top_pos = bullish[0] if bullish else "利多证据不突出"
    top_neg = bearish[0] if bearish else "利空证据不突出"
    if bullish or bearish:
        summary_text = (
            f"{asset}报告级加权审核得分{score:.2f}，整体{bias}。"
            f"主要利多：{top_pos}；主要利空：{top_neg}。"
        )
    else:
        note = neutral_notes[0] if neutral_notes else "报告级审核未识别出可形成方向的基本面证据"
        summary_text = f"{asset}报告级加权审核得分{score:.2f}，整体中性。{note}"
    analysis["fundamental_summary"] = summary_text[:220]
    previous_method = str(analysis.get("analysis_method") or "")
    if previous_method != "llm":
        analysis["analysis_method"] = "llm_report_weighted_audit_synthesis"
        analysis["asset_extraction_fallback_method"] = previous_method or "unknown"


def _analyze_report_asset_scores(
    row: dict[str, Any],
    assets: list[str],
    *,
    use_llm: bool,
) -> dict[str, dict[str, Any]]:
    assets = list(dict.fromkeys(assets))
    if not assets:
        return {}
    if not use_llm:
        return _rule_report_asset_scores(row, assets)

    assets_json = json.dumps(assets, ensure_ascii=False)
    text = _text(row)
    prompt = f"""你是商品期货研报审核员。请按旧版 commodity_report 的报告级审核口径，对一篇研报中涉及的品种逐一评分。

给定品种列表：
{assets_json}

严格输出 JSON，不要 Markdown：
{{
  "asset_scores": [
    {{
      "commodity": "品种名称，必须来自给定品种列表",
      "report_sentiment_score": 0.0,
      "influence_score": 0.0,
      "evidence_summary": "本篇研报关于该品种的核心基本面证据",
      "score_reason": "为什么给出该方向和影响力"
    }}
  ]
}}

要求：
1. asset_scores 必须覆盖给定品种列表中的每一个品种，不能漏项。
2. report_sentiment_score 取 -10 到 10，允许小数；正数看多，负数看空；只能反映本篇研报提供的基本面方向。
3. influence_score 取 0 到 1，表示本篇研报对该品种当日结论的影响力：直接讨论该品种、基本面证据密集、数据新且结论明确则高；只是顺带提及、只有行情/技术位/价格涨跌或缺少基本面则低。
4. 不能因为期价上涨/下跌、盘面情绪、技术位、主力合约表现而加减分；这类信息只能降低基本面影响力。
5. 如果本篇只是行情/标题/品种列表中提到该品种，没有有效基本面证据，report_sentiment_score 填 0，influence_score 填 0 到 0.1。
6. evidence_summary 和 score_reason 各控制在 60 字以内，不要编造原文没有的信息。

研报全文：
row_id={row.get("row_id") or ""}｜机构={row.get("org_name") or ""}｜标题={row.get("title") or ""}
{text}
"""
    last_error: Exception | None = None
    max_tokens = min(10000, max(2200, 800 + len(assets) * 220))
    for attempt in range(3):
        try:
            parsed = parse_json_object(_chat(prompt, max_tokens=max_tokens, timeout=180))
            return _normalize_report_asset_scores(row, assets, parsed)
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))

    fallback = _rule_report_asset_scores(row, assets)
    for entry in fallback.values():
        entry["llm_error"] = str(last_error) if last_error else "unknown llm error"
        entry["llm_attempts"] = 3
    return fallback


def _apply_report_score_audit(
    asset: str,
    analysis: dict[str, Any],
    snippets: list[dict[str, Any]],
    report_scores_by_row: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    source_audit = []
    for idx, item in enumerate(snippets, 1):
        row_id = str(item.get("row_id") or "")
        score_entry = dict((report_scores_by_row.get(row_id) or {}).get(asset) or {})
        score_entry.update({"source_index": idx, **_source_identity(item)})
        if "report_sentiment_score" not in score_entry:
            score_entry.update(
                {
                    "report_sentiment_score": 0.0,
                    "influence_score": 0.0,
                    "evidence_summary": "",
                    "score_reason": "没有可用报告级审核，按零权重处理。",
                    "covered_by_llm": False,
                }
            )
        source_audit.append(score_entry)

    holistic_score = _bounded_float(
        analysis.get("holistic_sentiment_score", analysis.get("sentiment_score")),
        lower=-10.0,
        upper=10.0,
    )
    score_audit, weighted_score = _weighted_score_audit(
        {"source_score_audit": source_audit},
        snippets,
        holistic_score=holistic_score,
    )
    analysis["holistic_sentiment_score"] = round(holistic_score, 2)
    analysis["weighted_sentiment_score"] = weighted_score
    analysis["score_audit"] = score_audit
    analysis["sentiment_score"] = weighted_score
    if analysis.get("analysis_method") != "llm":
        _synthesize_analysis_from_score_audit(asset, analysis)
    return analysis


def _market_review(summary: dict[str, Any], *, use_llm: bool) -> dict[str, Any]:
    details = summary.get("detailed_analysis") or {}
    ranked = sorted(
        details.values(),
        key=lambda item: abs(float(item.get("sentiment_score") or 0)),
        reverse=True,
    )
    compact = [
        {
            "commodity": item.get("commodity"),
            "score": item.get("sentiment_score"),
            "fundamental_summary": item.get("fundamental_summary", ""),
            "bullish": item.get("bullish_factors", []),
            "bearish": item.get("bearish_factors", []),
            "events": item.get("key_events", []),
        }
        for item in ranked
    ]
    if use_llm:
        prompt = (
            "请基于下面商品期货研报抽取结果，总结市场关键事件和核心交易逻辑。"
            "注意：核心交易逻辑必须以基本面证据为主，不要用行情涨跌或技术位替代供需、库存、成本、政策等逻辑。"
            "严格输出 JSON："
            '{"market_events_summary":"...","market_logic_summary":"..."}\n'
            + json.dumps(compact, ensure_ascii=False)
        )
        try:
            parsed = parse_json_object(_chat(prompt, max_tokens=1400))
            if isinstance(parsed, dict):
                return {
                    "market_events_summary": str(parsed.get("market_events_summary") or ""),
                    "market_logic_summary": str(parsed.get("market_logic_summary") or ""),
                    "timestamp": datetime.now().isoformat(),
                    "analysis_method": "llm",
                }
        except Exception:
            pass
    event_lines = []
    logic_lines = []
    for item in compact[:8]:
        event_lines.extend(item.get("events") or [])
        if item.get("bullish"):
            logic_lines.append(f"{item['commodity']}偏多：{item['bullish'][0]}")
        if item.get("bearish"):
            logic_lines.append(f"{item['commodity']}偏空：{item['bearish'][0]}")
    return {
        "market_events_summary": "\n".join(event_lines[:8]),
        "market_logic_summary": "\n".join(logic_lines[:10]),
        "timestamp": datetime.now().isoformat(),
        "analysis_method": "rule_fallback",
    }


def run_from_raw_folder(
    raw_folder: str | Path,
    root: str | Path | None = None,
    *,
    use_llm: bool = True,
    max_assets: int | None = None,
    max_workers: int = 1,
    use_llm_postprocess: bool | None = None,
) -> dict[str, Any]:
    raw_path = Path(raw_folder).expanduser()
    manifest = json.loads((raw_path / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "available":
        raise RuntimeError(f"日报原文不可用：{raw_path} status={manifest.get('status')}")
    date_key = str(manifest["date"])
    taxonomy = load_asset_taxonomy(root, required=True)
    rows = _load_rows(raw_path)
    asset_rows: dict[str, list[dict[str, Any]]] = {}
    rows_by_id: dict[str, dict[str, Any]] = {}
    row_assets: dict[str, list[str]] = {}
    for row_index, source_row in enumerate(rows, 1):
        row = {**source_row}
        row_id = str(row.get("row_id") or f"row-{row_index:04d}")
        row["row_id"] = row_id
        rows_by_id[row_id] = row
        text = _text(row)
        hits = [hit for hit in taxonomy.classify(text) if hit.kind == "variety"]
        labels = list(dict.fromkeys(hit.label for hit in hits))
        row_assets[row_id] = labels
        for hit in hits:
            asset_rows.setdefault(hit.label, []).append(
                {"org_name": row.get("org_name"), "title": row.get("title"), "row_id": row.get("row_id"), "text": text}
            )
    ordered_assets = sorted(asset_rows, key=lambda name: len(asset_rows[name]), reverse=True)
    if max_assets and max_assets > 0:
        ordered_assets = ordered_assets[:max_assets]
    selected_assets = set(ordered_assets)
    detailed = {}
    sentiment = {}

    report_jobs = [
        (row_id, rows_by_id[row_id], [asset for asset in assets if asset in selected_assets])
        for row_id, assets in row_assets.items()
        if any(asset in selected_assets for asset in assets)
    ]
    report_scores_by_row: dict[str, dict[str, dict[str, Any]]] = {}

    def audit_one(job: tuple[str, dict[str, Any], list[str]]) -> tuple[str, dict[str, dict[str, Any]]]:
        row_id, row, assets = job
        return row_id, _analyze_report_asset_scores(row, assets, use_llm=use_llm)

    if report_jobs:
        print(f"Report-level weighted audit: {len(report_jobs)} source reports", flush=True)
    if max_workers > 1 and len(report_jobs) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(audit_one, job): job[0] for job in report_jobs}
            completed = 0
            for future in as_completed(futures):
                row_id, audit = future.result()
                report_scores_by_row[row_id] = audit
                completed += 1
                if completed % 10 == 0 or completed == len(futures):
                    print(f"Report-level weighted audit: {completed}/{len(futures)}", flush=True)
    else:
        for index, job in enumerate(report_jobs, 1):
            row_id, audit = audit_one(job)
            report_scores_by_row[row_id] = audit
            if index % 10 == 0 or index == len(report_jobs):
                print(f"Report-level weighted audit: {index}/{len(report_jobs)}", flush=True)

    def analyze_one(asset: str) -> tuple[str, dict[str, Any]]:
        return asset, _analyze_asset(asset, asset_rows[asset], use_llm=use_llm)

    if ordered_assets:
        print(f"Asset full-text extraction: {len(ordered_assets)} assets", flush=True)
    if max_workers > 1 and len(ordered_assets) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(analyze_one, asset): asset for asset in ordered_assets}
            analyzed = {}
            completed = 0
            for future in as_completed(futures):
                asset, analysis = future.result()
                analyzed[asset] = analysis
                completed += 1
                if completed % 5 == 0 or completed == len(futures):
                    print(f"Asset full-text extraction: {completed}/{len(futures)}", flush=True)
        analyses = [(asset, analyzed[asset]) for asset in ordered_assets]
    else:
        analyses = []
        for index, asset in enumerate(ordered_assets, 1):
            analyses.append(analyze_one(asset))
            if index % 5 == 0 or index == len(ordered_assets):
                print(f"Asset full-text extraction: {index}/{len(ordered_assets)}", flush=True)

    for asset, analysis in analyses:
        analysis = _apply_report_score_audit(asset, analysis, asset_rows[asset], report_scores_by_row)
        analysis["original_sources"] = [
            {"row_id": item["row_id"], "org_name": item["org_name"], "title": item["title"]}
            for item in asset_rows[asset]
        ]
        detailed[asset] = analysis
        sentiment[asset] = analysis.get("sentiment_score", 0)

    now = datetime.now().isoformat()
    summary = {
        "title": f"{date_key} 商品期货日报原文抽取结果",
        "org_name": "OSS原始研报",
        "date": date_key,
        "sentiment_scores": sentiment,
        "detailed_analysis": detailed,
        "source_report_hash": sha256_bytes((raw_path / "rows.jsonl").read_bytes()),
        "analysis_date": now,
        "analyzer_type": "commodity",
        "_metadata": {
            "raw_folder": str(raw_path),
            "raw_manifest": manifest,
            "taxonomy_source": str(taxonomy.source_path) if taxonomy.source_path else "",
            "total_assets_detected": len(asset_rows),
            "assets_returned": len(detailed),
            "use_llm": use_llm,
            "use_llm_postprocess": use_llm if use_llm_postprocess is None else use_llm_postprocess,
            "max_workers": max_workers,
            "extraction_policy": {
                "fundamental_score_excludes_market_data": True,
                "market_data_field": "market_observations",
                "detail_level": "expanded_fundamental_evidence_with_data_time_scope_and_logic_path",
                "raw_input_policy": "full_text_all_matching_articles_no_truncation",
                "scoring_method": "source_influence_weighted_average",
                "score_audit_field": "score_audit",
                "display_score_field": "sentiment_score",
            },
        },
    }
    postprocess_llm = use_llm if use_llm_postprocess is None else use_llm_postprocess
    market_review = _market_review(summary, use_llm=use_llm)
    alignment = build_framework_alignment(summary, root, use_llm_refinement=postprocess_llm)
    logic_bundle = build_logic_chain_bundle(summary, alignment, use_llm_for_thesis=postprocess_llm)

    root_path = quanta_data_root(root)
    yyyy, mm, dd = dated_parts(date_key)
    run_id = f"RUN-{date_key}-{datetime.now().strftime('%H%M%S')}-RAW-DAILY"
    run_dir = root_path / "agent_workspace" / "candidates" / "futures_daily_raw_runs" / yyyy / mm / dd / run_id
    write_json(run_dir / f"{date_key}_commodity_summary.json", summary)
    write_json(run_dir / f"{date_key}_commodity_marketreview.json", market_review)
    write_json(run_dir / "framework_alignment.json", alignment)
    write_json(run_dir / "dimension_scores.json", logic_bundle["dimension_scores"])
    write_json(run_dir / "trade_thesis.json", logic_bundle["trade_thesis"])
    write_json(run_dir / "logic_chains.json", logic_bundle["logic_chains"])
    write_json(run_dir / "logic_summary.json", logic_bundle["logic_summary"])
    write_json(run_dir / "framework_update_candidates.json", logic_bundle["framework_update_candidates"])
    write_json(run_dir / "framework_format_review.json", logic_bundle["framework_format_review"])
    write_json(
        run_dir / "manifest.json",
        {
            "schema_version": "futures_daily_raw_run_manifest.v1",
            "status": "candidate",
            "run_id": run_id,
            "date": date_key,
            "raw_folder": str(raw_path),
            "generated_at": utc_now_iso(),
            "outputs": {
                "summary": f"{date_key}_commodity_summary.json",
                "market_review": f"{date_key}_commodity_marketreview.json",
                "framework_alignment": "framework_alignment.json",
                "dimension_scores": "dimension_scores.json",
                "trade_thesis": "trade_thesis.json",
                "logic_chains": "logic_chains.json",
                "logic_summary": "logic_summary.json",
                "framework_update_candidates": "framework_update_candidates.json",
                "framework_format_review": "framework_format_review.json",
            },
        },
    )
    return {
        "run_dir": str(run_dir),
        "summary": summary,
        "market_review": market_review,
        "alignment": alignment,
        "logic_bundle": logic_bundle,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run futures daily extraction from daily original raw folder.")
    parser.add_argument("--raw-folder", help="Path to raw_objects/futures_daily/daily_originals/YYYY/MM/DD.")
    parser.add_argument("--date", help="Report date. Used to locate raw folder when --raw-folder is omitted.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--no-llm", action="store_true", help="Use rule fallback only.")
    parser.add_argument("--no-llm-postprocess", action="store_true", help="Use rule-only framework mapping and thesis generation.")
    parser.add_argument("--max-assets", type=int, help="Maximum assets to analyze. Omit or pass 0 for all assets.")
    parser.add_argument("--max-workers", type=int, default=1, help="Parallel asset-analysis workers.")
    args = parser.parse_args(argv)
    if not args.raw_folder and not args.date:
        parser.error("需要 --raw-folder 或 --date")
    raw_folder = Path(args.raw_folder).expanduser() if args.raw_folder else raw_folder_for_date(args.date.replace("-", ""), args.quanta_root)
    result = run_from_raw_folder(
        raw_folder,
        args.quanta_root,
        use_llm=not args.no_llm,
        max_assets=args.max_assets,
        max_workers=args.max_workers,
        use_llm_postprocess=False if args.no_llm_postprocess else None,
    )
    summary = result["summary"]
    print(f"运行结果目录 → {result['run_dir']}")
    print(f"识别品种 {summary['_metadata']['total_assets_detected']} 个，输出 {summary['_metadata']['assets_returned']} 个")
    strongest = sorted(summary["sentiment_scores"].items(), key=lambda item: item[1], reverse=True)[:5]
    weakest = sorted(summary["sentiment_scores"].items(), key=lambda item: item[1])[:5]
    print("偏多前五 → " + ", ".join(f"{k}:{v}" for k, v in strongest))
    print("偏空前五 → " + ", ".join(f"{k}:{v}" for k, v in weakest))


if __name__ == "__main__":
    main()
