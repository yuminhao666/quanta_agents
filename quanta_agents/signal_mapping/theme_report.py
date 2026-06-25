from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import dated_parts, read_json, relative_to_root, utc_now_iso, write_json
from quanta_agents.core.llm_client import chat, get_provider
from quanta_agents.core.llm_json import parse_json_object, strip_fences
from quanta_agents.core.taxonomy import AssetTaxonomy, load_asset_taxonomy
from quanta_agents.opinion_radar import db, filters
from quanta_agents.research_reports.wechat_evidence import _is_market_observation
from quanta_agents.signal_mapping.incremental_state import apply_incremental_update
from quanta_agents.signal_mapping.mapper import (
    _asset_ref,
    _clean_text,
    _confidence_label,
    _event_layers,
    _hash_id,
    _source_ref,
    _strength_from_score,
    validate_signal_theme_objects,
)


THEME_REPORT_VERSION = "theme_report_maintenance.v1"
LLM_NORMALIZATION_VERSION = "theme_report_llm_normalizer.v3"
DEFAULT_WORK_ORDER_ID = "WO-DEV-20260620-013"

PER_REPORT_RELATIVE = "agent_workspace/candidates/futures_daily_single_report_analysis"
DEFAULT_SINGLE_REPORT_RUN_ID = "WECHAT-PROFILE-V1"

LLM_THEME_TYPES = {
    "geopolitics",
    "macro",
    "supply_demand",
    "inventory",
    "policy",
    "weather",
    "logistics",
    "fundamental_validation",
    "other",
}

FIELD_PRIORITY = (
    "key_events",
    "supply_demand",
    "key_data",
    "bullish_factors",
    "bearish_factors",
    "price_forecast",
)

PRICE_OR_TECHNICAL_HINTS = (
    "主力合约",
    "夜盘",
    "收盘",
    "收涨",
    "收跌",
    "涨幅",
    "跌幅",
    "盘面",
    "技术面",
    "压力位",
    "支撑位",
    "均线",
    "MACD",
    "KDJ",
    "布林",
    "挂单",
    "期价",
    "报收",
    "收在",
    "点附近",
    "期权",
    "隐含波动率",
    "ETF持仓",
    "交易热度",
    "技术路径",
)

LOW_SIGNAL_MARKET_NOTICE_PATTERNS = (
    r"金十图示：.*(黄金|白银)ETF持仓报告",
    r"(黄金|白银)ETF持仓报告",
    r"(ETF持仓|持仓报告)",
    r"SPDR Gold Trust持仓",
    r"iShares Silver Trust持仓",
    r"挂单报告：",
    r"(黄金|白银)[^。；;]{0,80}(看涨期权|看跌期权|空单|多单|押注)",
    r"(沪金|沪银|黄金|白银|金银)[^。；;]{0,80}(期权|隐含波动率)",
    r"(LOF|基金)[^。；;]{0,120}(停牌|溢价|申购|赎回)",
    r"金饰价格",
    r"上海黄金交易所[^。；;]{0,80}(交易行情|市场行情)",
    r"(黄金|白银)投资者周报",
    r"技术刘Pro",
    r"(点击查看|正在直播|Live分析|栏目更新|大有作V|交易小妙招|Hi，今天怎么看)",
    r"Gold Series",
    r"黄金窗口期",
    r"白银有色",
)

THEME_RULES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("geopolitics", "地缘政治与通航风险", "geopolitics", ("伊朗", "中东", "霍尔木兹", "战争", "冲突", "袭击", "制裁", "停火", "通航", "封锁")),
    (
        "safe_haven_gold",
        "避险需求与央行购金",
        "macro",
        ("避险", "央行购金", "央行买金", "购金", "买金", "黄金储备", "黄金换", "实际利率", "增持黄金", "减持黄金", "出售黄金", "变现黄金"),
    ),
    (
        "positioning",
        "资金持仓与交易情绪",
        "fundamental_validation",
        ("CFTC", "COMEX", "投机者", "净多头", "净空头", "仓位", "资金介入", "资金流", "持仓减少", "持仓增加"),
    ),
    (
        "macro_rates",
        "美联储与利率预期",
        "macro",
        (
            "美联储",
            "利率",
            "加息",
            "降息",
            "点阵图",
            "PCE",
            "CPI",
            "通胀",
            "美元",
            "收益率",
            "美债",
            "大类资产",
        ),
    ),
    ("inventory", "库存变化与仓单验证", "inventory", ("库存", "仓单", "库容", "去库", "累库")),
    ("supply", "供应扰动与产量变化", "supply_demand", ("供应", "供给", "产量", "产能", "减产", "增产", "复产", "检修", "停产", "进口", "出口", "到港", "发运")),
    ("demand", "需求开工与终端消费", "supply_demand", ("需求", "消费", "开工", "订单", "终端", "成交", "表需", "补库")),
    ("policy", "政策监管与产业约束", "policy", ("政策", "监管", "关税", "收储", "抛储", "配额", "出口管制", "反内卷", "会议")),
    ("weather", "天气扰动与作物生长", "weather", ("天气", "降雨", "干旱", "台风", "洪涝", "气温", "播种", "单产")),
    ("cost_profit", "成本利润与估值修复", "fundamental_validation", ("利润", "成本", "加工费", "压榨", "亏损", "估值", "基差")),
    ("logistics", "物流运输与港口变化", "logistics", ("航运", "运费", "港口", "VLCC", "海运", "船", "集装箱")),
    ("price_validation", "盘面价格与交易验证", "price_validation", ("涨停", "跌停", "收涨", "收跌", "涨幅", "跌幅", "震荡", "支撑", "压力", "均线", "MACD")),
)

REPORT_FIELD_THEME_HINTS = {
    "supply_demand": ("supply_demand", "供需结构跟踪", "supply_demand"),
    "key_data": ("data_validation", "关键数据验证", "fundamental_validation"),
    "key_events": ("event_update", "重要事件更新", "fundamental_validation"),
    "price_forecast": ("price_validation", "价格预期与交易验证", "price_validation"),
}


def _canonical_date(value: str) -> str:
    text = value.replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError("date must be YYYYMMDD or YYYY-MM-DD")
    return text


def _date_range(start_date: str, end_date: str) -> list[str]:
    start_key = _canonical_date(start_date)
    end_key = _canonical_date(end_date)
    start = datetime.strptime(start_key, "%Y%m%d")
    end = datetime.strptime(end_key, "%Y%m%d")
    if start > end:
        raise ValueError("start date must be before or equal to end date")
    out: list[str] = []
    current = start
    while current <= end:
        out.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return out


def _date_iso(date_key: str) -> str:
    return f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"


def _bounded(value: Any, default: float = 0.45) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(0.0, min(1.0, number)), 3)


def _direction_from_score(score: Any) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "unknown"
    if value >= 1.0:
        return "bullish"
    if value <= -1.0:
        return "bearish"
    return "neutral"


def _theme_rule(text: str, field_name: str = "") -> tuple[str, str, str]:
    if re.search(r"(央行|储备|官方|国家)[^。；;]{0,40}(持有|增持|减持|出售)[^。；;]{0,30}黄金", text):
        return "safe_haven_gold", "避险需求与央行购金", "macro"
    for key, title, theme_type, keywords in THEME_RULES:
        if any(word in text for word in keywords):
            return key, title, theme_type
    if field_name in REPORT_FIELD_THEME_HINTS:
        return REPORT_FIELD_THEME_HINTS[field_name]
    return "other", "其他逻辑跟踪", "other"


def _canonical_theme_id(asset_label: str, theme_key: str, title: str) -> str:
    return _hash_id("THSTATE-CANON", asset_label, theme_key, title)


def _theme_title(asset_label: str, title: str) -> str:
    return f"{asset_label}：{title}" if asset_label else title


def _source_date_from_report(report: dict[str, Any], fallback: str) -> str:
    date = str(report.get("date") or fallback).replace("-", "")[:8]
    return date if len(date) == 8 and date.isdigit() else fallback


def _per_report_files(root: Path, date_key: str, run_id: str) -> list[Path]:
    yyyy, mm, dd = dated_parts(date_key)
    base = root / PER_REPORT_RELATIVE / yyyy / mm / dd / run_id / "per_report"
    return sorted(base.glob("*/*.json"))


def _report_text_items(detail: dict[str, Any], *, max_items_per_field: int = 4) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for field in FIELD_PRIORITY:
        values = detail.get(field)
        if not isinstance(values, list):
            continue
        for value in values[:max_items_per_field]:
            text = _clean_text(value, limit=420)
            if text:
                out.append((field, text))
    return out


def _report_confidence(field: str, text: str, detail: dict[str, Any]) -> float:
    base = 0.62 if field in {"key_events", "supply_demand", "key_data"} else 0.42
    if _is_market_observation(text) or field == "price_forecast":
        base -= 0.16
    evidence_count = float(detail.get("evidence_count") or 1)
    return _bounded(base + min(0.14, evidence_count * 0.008), default=base)


def _is_price_or_technical_text(text: str) -> bool:
    """Identify market-only observations before they pollute fundamental theme maintenance."""
    if _is_market_observation(text):
        return True
    hint_hits = sum(1 for word in PRICE_OR_TECHNICAL_HINTS if word in text)
    has_price_action = bool(re.search(r"(涨|跌|收于|报收|突破|回踩|反弹|回落|震荡|盘整|%|点附近)", text))
    fundamental_hits = sum(1 for _, _, _, keywords in THEME_RULES if any(word in text for word in keywords))
    price_path = bool(
        re.search(r"(期价|价格|指数|现货)[^。；;]{0,36}(上涨|下跌|走高|走低|回落|反弹|震荡|收在|报收|调高|调低)", text)
        or re.search(r"\d+(?:\.\d+)?\s*点附近", text)
        or re.search(r"(最高|最低)\s*\d+(?:\.\d+)?", text)
        or re.search(r"(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{3,4}(?![A-Za-z0-9])[^。；;]{0,60}(上涨|下跌|回落|反弹|震荡|运行)", text)
        or re.search(r"\d+(?:\.\d+)?\s*(?:美分|元|点)[^。；;]{0,40}(上涨|下跌|上升|下降|回落|震荡)", text)
    )
    if price_path and fundamental_hits <= 1:
        return True
    return hint_hits >= 1 and has_price_action and fundamental_hits <= 1


def _is_low_signal_market_notice(text: str) -> bool:
    """Filter repeated market notices that create noisy topics without adding logic-chain content."""
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in LOW_SIGNAL_MARKET_NOTICE_PATTERNS)


def _asset_reference(asset_label: str, detail: dict[str, Any], taxonomy: AssetTaxonomy | None = None) -> dict[str, Any]:
    asset = detail.get("asset") if isinstance(detail.get("asset"), dict) else {}
    canonical_label = taxonomy.canonical_name(asset_label) if taxonomy else None
    label = canonical_label or asset_label
    asset_id = asset.get("asset_id") or asset.get("commodity_code")
    if taxonomy and not asset_id:
        for hit in taxonomy.classify(label):
            if hit.kind == "variety" and hit.label == label:
                asset_id = hit.asset_id
                break
    return _asset_ref(label, asset_id)


def _report_source_ref(report: dict[str, Any], report_path: Path, *, root: Path) -> dict[str, Any]:
    return _source_ref(
        "candidate",
        "futures_daily_single_report_analysis",
        artifact_id=str((report.get("_metadata") or {}).get("row_id") or report_path.stem),
        path=report_path,
        root=root,
        content_hash=str(report.get("source_report_hash") or ""),
        url=((report.get("_metadata") or {}).get("raw") or {}).get("source_url"),
    )


def _dedupe_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for ref in refs:
        key = (
            str(ref.get("ref_type") or ""),
            str(ref.get("source_name") or ""),
            str(ref.get("id") or ""),
            str(ref.get("path") or ref.get("url") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def _report_signal_and_theme(
    report: dict[str, Any],
    report_path: Path,
    *,
    root: Path,
    asset_label: str,
    detail: dict[str, Any],
    field: str,
    text: str,
    date_key: str,
    created_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    theme_key, title_base, theme_type = _theme_rule(text, field)
    title = _theme_title(asset_label, title_base)
    canonical_id = _canonical_theme_id(asset_label, theme_key, title_base)
    theme_id = _hash_id("THA-RREP-PER", canonical_id)
    signal_id = _hash_id(
        "SIG-RREP-PER",
        report.get("source_report_hash"),
        asset_label,
        field,
        text,
    )
    confidence = _report_confidence(field, text, detail)
    direction = _direction_from_score(detail.get("sentiment_score"))
    asset_ref = _asset_reference(asset_label, detail)
    rel_path = relative_to_root(report_path, root)
    source_ref = _report_source_ref(report, report_path, root=root)
    event_layers = _event_layers(
        text,
        political_layer=text if theme_type in {"policy", "geopolitics"} else None,
        time_window_layer=f"{_date_iso(date_key)} research report observation",
    )
    signal = {
        "schema_version": "research_signal.v1",
        "signal_id": signal_id,
        "status": "candidate",
        "created_at": created_at,
        "updated_at": None,
        "source_role": "research_report",
        "source_ref": source_ref,
        "signal_kind": "price_validation" if theme_type == "price_validation" else "theme_update",
        "asset_refs": [asset_ref],
        "theme_refs": [{"id": theme_id, "label": title, "ref_type": "theme"}],
        "framework_node_refs": [],
        "direction": direction,
        "strength": _strength_from_score(detail.get("sentiment_score"), default=confidence),
        "confidence": confidence,
        "confidence_label": _confidence_label(confidence),
        "time_window": {"start": _date_iso(date_key), "end": _date_iso(date_key), "horizon": "daily"},
        "event_definition_layers": event_layers,
        "evidence_refs": [
            {
                "evidence_id": _hash_id("RREP-FIELD", report.get("source_report_hash"), asset_label, field, text),
                "path": rel_path,
                "stance": "source",
                "strength": "medium" if confidence >= 0.45 else "weak",
                "snippet_ref": field,
            }
        ],
        "source_signal_refs": [],
        "conflict_refs": [],
        "mapping": {
            "method": "per_report_json_to_theme_signal.v1",
            "confidence": confidence,
            "mapped_by": THEME_REPORT_VERSION,
            "requires_human_review_reason": "single-report JSON is machine extracted and requires review before promotion.",
        },
        "human_review_required": True,
        "promotion_policy": "review_required",
        "expires_at": None,
        "notes": text,
    }
    theme = {
        "schema_version": "theme_anchor.v1",
        "theme_anchor_id": theme_id,
        "status": "candidate",
        "created_at": created_at,
        "updated_at": None,
        "title": title,
        "description": text,
        "anchor_kind": "research_report_repeated_theme",
        "theme_type": theme_type,
        "source_roles": ["research_report"],
        "source_refs": [source_ref],
        "asset_refs": [asset_ref],
        "framework_node_refs": [],
        "event_definition_layers": event_layers,
        "signal_refs": [{"id": signal_id, "label": text[:80], "ref_type": "signal"}],
        "evidence_refs": signal["evidence_refs"],
        "polymarket_refs": [],
        "aliases": [title_base, asset_label],
        "lifecycle": {
            "current_phase": "new",
            "first_seen_at": _date_iso(date_key),
            "last_seen_at": _date_iso(date_key),
            "support_count": 1,
            "conflict_count": 0,
            "review_state": "machine_candidate",
        },
        "promotion_policy": "review_required",
    }
    return signal, theme


def _report_group_seed(
    *,
    report: dict[str, Any],
    report_path: Path,
    root: Path,
    taxonomy: AssetTaxonomy,
    asset_label: str,
    detail: dict[str, Any],
    field: str,
    text: str,
    date_key: str,
    include_price_validation: bool,
    min_report_confidence: float,
) -> dict[str, Any] | None:
    is_price_text = field == "price_forecast" or _is_price_or_technical_text(text) or _is_low_signal_market_notice(text)
    if is_price_text:
        theme_key, title_base, theme_type = "price_validation", "盘面价格与交易验证", "price_validation"
    else:
        theme_key, title_base, theme_type = _theme_rule(text, field)
    if theme_type == "price_validation" and not include_price_validation:
        return None
    confidence = _report_confidence(field, text, detail)
    if is_price_text:
        confidence = min(confidence, 0.36)
    if confidence < min_report_confidence:
        return None
    rel_path = relative_to_root(report_path, root)
    asset_ref = _asset_reference(asset_label, detail, taxonomy)
    canonical_asset_label = str(asset_ref.get("label") or asset_label)
    return {
        "date_key": date_key,
        "asset_label": canonical_asset_label,
        "asset_ref": asset_ref,
        "theme_key": theme_key,
        "title_base": title_base,
        "title": _theme_title(canonical_asset_label, title_base),
        "theme_type": theme_type,
        "field": field,
        "text": text,
        "confidence": confidence,
        "strength": _strength_from_score(detail.get("sentiment_score"), default=confidence),
        "direction": _direction_from_score(detail.get("sentiment_score")),
        "source_ref": _report_source_ref(report, report_path, root=root),
        "evidence_ref": {
            "evidence_id": _hash_id("RREP-FIELD", report.get("source_report_hash"), canonical_asset_label, field, text),
            "path": rel_path,
            "stance": "source",
            "strength": "medium" if confidence >= 0.45 else "weak",
            "snippet_ref": field,
        },
        "report_hash": str(report.get("source_report_hash") or report_path.stem),
    }


def _report_groups_for_date(
    root: Path,
    taxonomy: AssetTaxonomy,
    date_key: str,
    run_id: str,
    *,
    max_items_per_asset: int,
    include_price_validation: bool,
    min_report_confidence: float,
) -> tuple[dict[tuple[str, str, str], dict[str, Any]], dict[str, int]]:
    """Aggregate per-report fields into daily asset/theme updates before state maintenance."""
    report_files = _per_report_files(root, date_key, run_id)
    stats = Counter({"report_file_count": len(report_files)})
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in report_files:
        report = read_json(path)
        if not isinstance(report, dict):
            stats["report_invalid_json_count"] += 1
            continue
        source_date = _source_date_from_report(report, date_key)
        for asset_label, detail in (report.get("detailed_analysis") or {}).items():
            if not isinstance(detail, dict):
                continue
            stats["report_asset_count"] += 1
            emitted = 0
            seen_texts: set[str] = set()
            for field, text in _report_text_items(detail):
                if emitted >= max_items_per_asset:
                    break
                dedupe_key = re.sub(r"\d+(?:\.\d+)?", "#", text)
                if dedupe_key in seen_texts:
                    stats["report_duplicate_text_count"] += 1
                    continue
                seen_texts.add(dedupe_key)
                seed = _report_group_seed(
                    report=report,
                    report_path=path,
                    root=root,
                    taxonomy=taxonomy,
                    asset_label=str(asset_label),
                    detail=detail,
                    field=field,
                    text=text,
                    date_key=source_date,
                    include_price_validation=include_price_validation,
                    min_report_confidence=min_report_confidence,
                )
                if seed is None:
                    if field == "price_forecast" or _is_price_or_technical_text(text):
                        stats["report_skipped_price_validation_count"] += 1
                    else:
                        stats["report_skipped_low_confidence_count"] += 1
                    continue
                key = (str(seed["asset_label"]), str(seed["theme_key"]), str(seed["title_base"]))
                group = groups.setdefault(
                    key,
                    {
                        "date_key": date_key,
                        "asset_label": seed["asset_label"],
                        "asset_ref": seed["asset_ref"],
                        "theme_key": seed["theme_key"],
                        "title_base": seed["title_base"],
                        "title": seed["title"],
                        "theme_type": seed["theme_type"],
                        "count": 0,
                        "texts": [],
                        "source_refs": [],
                        "evidence_refs": [],
                        "directions": Counter(),
                        "fields": Counter(),
                        "confidence_sum": 0.0,
                        "strength_sum": 0.0,
                        "report_hashes": [],
                    },
                )
                group["count"] += 1
                group["confidence_sum"] += float(seed["confidence"])
                group["strength_sum"] += float(seed["strength"])
                group["directions"][str(seed["direction"])] += 1
                group["fields"][str(seed["field"])] += 1
                if len(group["texts"]) < 8:
                    group["texts"].append(seed["text"])
                if len(group["source_refs"]) < 24:
                    group["source_refs"].append(seed["source_ref"])
                if len(group["evidence_refs"]) < 24:
                    group["evidence_refs"].append(seed["evidence_ref"])
                if len(group["report_hashes"]) < 24:
                    group["report_hashes"].append(seed["report_hash"])
                stats["report_candidate_count"] += 1
                emitted += 1
    stats["report_group_count"] = len(groups)
    return groups, dict(stats)


def _dominant_direction(counter: Counter[str]) -> str:
    bullish = int(counter.get("bullish") or 0)
    bearish = int(counter.get("bearish") or 0)
    if bullish > bearish:
        return "bullish"
    if bearish > bullish:
        return "bearish"
    if bullish and bearish:
        return "mixed"
    if counter:
        return counter.most_common(1)[0][0]
    return "unknown"


def _report_group_to_signal_theme(
    group: dict[str, Any],
    *,
    date_key: str,
    created_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    asset_label = str(group.get("asset_label") or "")
    title_base = str(group.get("title_base") or "研报主题跟踪")
    title = str(group.get("title") or _theme_title(asset_label, title_base))
    theme_type = str(group.get("theme_type") or "other")
    count = max(1, int(group.get("count") or 1))
    canonical_id = _canonical_theme_id(asset_label, str(group.get("theme_key") or ""), title_base)
    theme_id = _hash_id("THA-RREP-GROUP", canonical_id)
    texts = [str(item) for item in group.get("texts") or [] if str(item).strip()]
    text = "；".join(texts[:5])
    confidence = _bounded(float(group.get("confidence_sum") or 0.0) / count + min(0.18, count * 0.018), default=0.45)
    strength = _bounded(float(group.get("strength_sum") or 0.0) / count, default=confidence)
    directions = group.get("directions") if isinstance(group.get("directions"), Counter) else Counter()
    direction = _dominant_direction(directions)
    source_refs = _dedupe_refs([item for item in group.get("source_refs") or [] if isinstance(item, dict)])
    evidence_refs = _dedupe_refs([item for item in group.get("evidence_refs") or [] if isinstance(item, dict)])
    asset_refs = [group["asset_ref"]] if isinstance(group.get("asset_ref"), dict) else []
    event_layers = _event_layers(
        text,
        political_layer=text if theme_type in {"policy", "geopolitics"} else None,
        time_window_layer=f"{_date_iso(date_key)} aggregated research-report observation; count={count}",
    )
    signal_id = _hash_id(
        "SIG-RREP-GROUP",
        date_key,
        asset_label,
        group.get("theme_key"),
        count,
        "|".join(str(item) for item in (group.get("report_hashes") or [])[:8]),
    )
    signal = {
        "schema_version": "research_signal.v1",
        "signal_id": signal_id,
        "status": "candidate",
        "created_at": created_at,
        "updated_at": None,
        "source_role": "research_report",
        "source_ref": source_refs[0]
        if source_refs
        else _source_ref("candidate", "futures_daily_single_report_analysis", artifact_id=f"{date_key}:{asset_label}:{group.get('theme_key')}"),
        "signal_kind": "price_validation" if theme_type == "price_validation" else "theme_update",
        "asset_refs": asset_refs,
        "theme_refs": [{"id": theme_id, "label": title, "ref_type": "theme"}],
        "framework_node_refs": [],
        "direction": direction,
        "strength": strength,
        "confidence": confidence,
        "confidence_label": _confidence_label(confidence),
        "time_window": {"start": _date_iso(date_key), "end": _date_iso(date_key), "horizon": "daily"},
        "event_definition_layers": event_layers,
        "evidence_refs": evidence_refs[:12],
        "source_signal_refs": [],
        "conflict_refs": [],
        "mapping": {
            "method": "daily_report_group_to_theme_signal.v1",
            "confidence": confidence,
            "mapped_by": THEME_REPORT_VERSION,
            "requires_human_review_reason": "daily per-report fields are aggregated deterministically and require review before promotion.",
        },
        "human_review_required": True,
        "promotion_policy": "review_required",
        "expires_at": None,
        "notes": text[:420],
    }
    theme = {
        "schema_version": "theme_anchor.v1",
        "theme_anchor_id": theme_id,
        "status": "candidate",
        "created_at": created_at,
        "updated_at": None,
        "title": title,
        "description": text[:420] or None,
        "anchor_kind": "research_report_repeated_theme",
        "theme_type": theme_type,
        "source_roles": ["research_report"],
        "source_refs": source_refs[:12],
        "asset_refs": asset_refs,
        "framework_node_refs": [],
        "event_definition_layers": event_layers,
        "signal_refs": [{"id": signal_id, "label": title, "ref_type": "signal"}],
        "evidence_refs": evidence_refs[:12],
        "polymarket_refs": [],
        "aliases": [title_base, asset_label],
        "lifecycle": {
            "current_phase": "new",
            "first_seen_at": _date_iso(date_key),
            "last_seen_at": _date_iso(date_key),
            "support_count": count,
            "conflict_count": 0,
            "review_state": "machine_candidate",
        },
        "promotion_policy": "review_required",
    }
    return signal, theme


def report_json_to_signal_theme(
    report_path: Path,
    *,
    root: Path,
    created_at: str,
    max_items_per_asset: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    report = read_json(report_path)
    if not isinstance(report, dict):
        return [], [], {"status": "invalid_json", "path": str(report_path)}
    date_key = _source_date_from_report(report, "19700101")
    signals: list[dict[str, Any]] = []
    themes: list[dict[str, Any]] = []
    for asset_label, detail in (report.get("detailed_analysis") or {}).items():
        if not isinstance(detail, dict):
            continue
        emitted = 0
        seen_texts: set[str] = set()
        for field, text in _report_text_items(detail):
            if emitted >= max_items_per_asset:
                break
            dedupe_key = re.sub(r"\d+(?:\.\d+)?", "#", text)
            if dedupe_key in seen_texts:
                continue
            seen_texts.add(dedupe_key)
            signal, theme = _report_signal_and_theme(
                report,
                report_path,
                root=root,
                asset_label=str(asset_label),
                detail=detail,
                field=field,
                text=text,
                date_key=date_key,
                created_at=created_at,
            )
            signals.append(signal)
            themes.append(theme)
            emitted += 1
    return signals, themes, {"status": "ok", "path": relative_to_root(report_path, root)}


def _flash_text(flash: dict[str, Any]) -> str:
    text = f"{flash.get('title') or ''} {flash.get('content') or ''}".strip()
    text = re.sub(r"<[^>]+>", "", text).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def _news_direction(text: str) -> str:
    positive = ("利多", "支撑", "上涨", "走强", "减产", "去库", "封锁", "降息", "宽松")
    negative = ("利空", "压制", "下跌", "走弱", "增产", "累库", "停火", "加息", "收紧")
    pos = sum(1 for word in positive if word in text)
    neg = sum(1 for word in negative if word in text)
    if pos > neg:
        return "bullish"
    if neg > pos:
        return "bearish"
    return "unknown"


def _news_groups_for_date(
    root: Path,
    taxonomy: AssetTaxonomy,
    date_key: str,
    *,
    max_news_per_day: int,
    include_price_validation: bool,
    include_low_signal_market_notices: bool,
) -> tuple[dict[tuple[str, str, str, str], dict[str, Any]], dict[str, int]]:
    start = datetime.strptime(date_key, "%Y%m%d")
    rows = db.fetch_flashes(start, start + timedelta(days=1))
    stats = Counter({"raw_news_count": len(rows)})
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    kept = 0
    for flash in rows:
        text = _flash_text(flash)
        if not text or not filters.keep_flash(flash):
            continue
        if not include_low_signal_market_notices and _is_low_signal_market_notice(text):
            stats["news_skipped_low_signal_notice_count"] += 1
            continue
        hits = taxonomy.classify(text)
        varieties = [hit for hit in hits if hit.kind == "variety"]
        macros = [hit for hit in hits if hit.kind == "macro"]
        if not varieties and not macros:
            continue
        kept += 1
        if max_news_per_day and kept > max_news_per_day:
            stats["news_cap_exceeded_count"] += 1
            continue
        targets = [(hit.label, hit.asset_id or "", "asset") for hit in varieties[:4]]
        if not targets:
            targets = [(hit.label, "", "macro") for hit in macros[:2]]
        theme_key, title_base, theme_type = _theme_rule(text, "")
        if theme_type == "price_validation" or _is_price_or_technical_text(text):
            if not include_price_validation:
                stats["news_skipped_price_validation_count"] += 1
                continue
        for label, asset_id, target_kind in targets:
            title = _theme_title(label if target_kind == "asset" else "", title_base)
            key = (target_kind, label, theme_key, title)
            group = groups.setdefault(
                key,
                {
                    "target_kind": target_kind,
                    "label": label,
                    "asset_id": asset_id,
                    "theme_key": theme_key,
                    "title": title,
                    "theme_type": theme_type,
                    "count": 0,
                    "important_count": 0,
                    "texts": [],
                    "flash_ids": [],
                    "directions": Counter(),
                },
            )
            group["count"] += 1
            group["important_count"] += int(flash.get("important") or 0)
            group["texts"].append(text[:320])
            group["flash_ids"].append(str(flash.get("flash_id") or flash.get("id") or ""))
            group["directions"][_news_direction(text)] += 1
    stats["mapped_news_count"] = kept - stats["news_cap_exceeded_count"]
    stats["news_group_count"] = len(groups)
    return groups, dict(stats)


def _news_group_to_signal_theme(
    group: dict[str, Any],
    *,
    date_key: str,
    created_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    label = str(group.get("label") or "")
    title = str(group.get("title") or "新闻主题候选")
    theme_type = str(group.get("theme_type") or "other")
    canonical_id = _canonical_theme_id(label, str(group.get("theme_key") or ""), title)
    theme_id = _hash_id("THA-NEWS", canonical_id)
    direction = group["directions"].most_common(1)[0][0] if group.get("directions") else "unknown"
    count = int(group.get("count") or 0)
    important_count = int(group.get("important_count") or 0)
    confidence = _bounded(0.28 + min(0.3, count * 0.012) + min(0.2, important_count * 0.04), default=0.35)
    text = "；".join(str(item) for item in (group.get("texts") or [])[:5])
    source_ref = _source_ref(
        "external",
        "opinion_radar_flash",
        artifact_id=f"{date_key}:{label}:{group.get('theme_key')}",
    )
    asset_refs = []
    if group.get("target_kind") == "asset":
        asset_refs = [_asset_ref(label, group.get("asset_id"))]
    event_layers = _event_layers(
        text,
        political_layer=text if theme_type in {"policy", "geopolitics"} else None,
        time_window_layer=f"{_date_iso(date_key)} news observation; count={count}; important={important_count}",
    )
    signal_id = _hash_id("SIG-NEWS-GROUP", date_key, label, group.get("theme_key"), count, text[:160])
    signal = {
        "schema_version": "research_signal.v1",
        "signal_id": signal_id,
        "status": "candidate",
        "created_at": created_at,
        "updated_at": None,
        "source_role": "news",
        "source_ref": source_ref,
        "signal_kind": "event_definition",
        "asset_refs": asset_refs,
        "theme_refs": [{"id": theme_id, "label": title, "ref_type": "theme"}],
        "framework_node_refs": [],
        "direction": direction,
        "strength": _bounded(0.18 + min(0.6, count * 0.01) + min(0.2, important_count * 0.04), default=0.3),
        "confidence": confidence,
        "confidence_label": _confidence_label(confidence),
        "time_window": {"start": _date_iso(date_key), "end": _date_iso(date_key), "horizon": "daily"},
        "event_definition_layers": event_layers,
        "evidence_refs": [
            {
                "evidence_id": flash_id,
                "path": "mysql://opinion_radar_flash",
                "stance": "source",
                "strength": "weak" if confidence < 0.45 else "medium",
            }
            for flash_id in (group.get("flash_ids") or [])[:12]
        ],
        "source_signal_refs": [],
        "conflict_refs": [],
        "mapping": {
            "method": "news_daily_group_to_theme_signal.v1",
            "confidence": confidence,
            "mapped_by": THEME_REPORT_VERSION,
            "requires_human_review_reason": "news groups are low-confidence event definitions and require review.",
        },
        "human_review_required": True,
        "promotion_policy": "review_required",
        "expires_at": None,
        "notes": text[:420],
    }
    theme = {
        "schema_version": "theme_anchor.v1",
        "theme_anchor_id": theme_id,
        "status": "candidate",
        "created_at": created_at,
        "updated_at": None,
        "title": title,
        "description": text[:420] or None,
        "anchor_kind": "agent_cluster_candidate",
        "theme_type": theme_type,
        "source_roles": ["news"],
        "source_refs": [source_ref],
        "asset_refs": asset_refs,
        "framework_node_refs": [],
        "event_definition_layers": event_layers,
        "signal_refs": [{"id": signal_id, "label": title, "ref_type": "signal"}],
        "evidence_refs": signal["evidence_refs"],
        "polymarket_refs": [],
        "aliases": [title, label],
        "lifecycle": {
            "current_phase": "new",
            "first_seen_at": _date_iso(date_key),
            "last_seen_at": _date_iso(date_key),
            "support_count": 0,
            "conflict_count": 0,
            "review_state": "machine_candidate",
        },
        "promotion_policy": "review_required",
    }
    return signal, theme


def _dedupe_by_id(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        item_id = str(item.get(key) or "")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        out.append(item)
    return out


def _chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    chunk_size = max(1, size)
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]


def _llm_normalization_cache_dir(root: Path) -> Path:
    return root / "agent_workspace" / "cache" / "theme_report_llm_normalization" / LLM_NORMALIZATION_VERSION


def _theme_asset_labels(theme: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for ref in theme.get("asset_refs") or []:
        if isinstance(ref, dict) and ref.get("label"):
            labels.append(str(ref["label"]))
    return list(dict.fromkeys(labels))


def _candidate_cache_key(candidate: dict[str, Any]) -> str:
    raw = json.dumps(
        {
            "title": candidate.get("current_title"),
            "theme_type": candidate.get("current_theme_type"),
            "asset_labels": candidate.get("asset_labels"),
            "source_roles": candidate.get("source_roles"),
            "sample_texts": candidate.get("sample_texts"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return _hash_id("LLM-NORM", raw)


def _build_llm_theme_candidates(
    signals: list[dict[str, Any]],
    themes: list[dict[str, Any]],
    *,
    max_samples_per_theme: int,
) -> list[dict[str, Any]]:
    signals_by_theme: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        for ref in signal.get("theme_refs") or []:
            if isinstance(ref, dict) and ref.get("id"):
                signals_by_theme[str(ref["id"])].append(signal)

    candidates: list[dict[str, Any]] = []
    for theme in themes:
        theme_id = str(theme.get("theme_anchor_id") or "")
        if not theme_id:
            continue
        related = signals_by_theme.get(theme_id, [])
        samples: list[str] = []
        source_roles: list[str] = []
        for signal in related[: max(1, max_samples_per_theme)]:
            note = _clean_text(signal.get("notes"), limit=360)
            if note:
                samples.append(note)
            role = _clean_text(signal.get("source_role"))
            if role:
                source_roles.append(role)
        if not samples:
            desc = _clean_text(theme.get("description"), limit=360)
            if desc:
                samples.append(desc)
        candidates.append(
            {
                "id": theme_id,
                "current_title": _clean_text(theme.get("title"), limit=100),
                "current_theme_type": _clean_text(theme.get("theme_type") or "other"),
                "asset_labels": _theme_asset_labels(theme),
                "source_roles": sorted(set(source_roles or theme.get("source_roles") or [])),
                "sample_texts": samples[:max_samples_per_theme],
            }
        )
    return candidates


def _normalization_prompt(batch: list[dict[str, Any]]) -> str:
    payload = [
        {
            "id": item["id"],
            "current_title": item.get("current_title"),
            "current_theme_type": item.get("current_theme_type"),
            "asset_labels": item.get("asset_labels"),
            "source_roles": item.get("source_roles"),
            "sample_texts": item.get("sample_texts"),
        }
        for item in batch
    ]
    return (
        "你是期货、宏观和预测市场信息处理系统里的主题归一化模型。"
        "请只基于候选里的样本文本做语义判断，不要照抄 current_title。"
        "任务：判断每个候选主题是否值得进入增量主题状态；如果值得，给出稳定中文主题名、主题类型和可合并 key；如果只是行情噪声、ETF/LOF/挂单/技术面/营销直播/点击标题/公司名误伤，就 drop。\n\n"
        "输出必须是严格 JSON 对象，格式：\n"
        "{\n"
        '  "items": [\n'
        '    {"id": "...", "action": "keep|drop", "canonical_title_zh": "...", "theme_type": "geopolitics|macro|supply_demand|inventory|policy|weather|logistics|fundamental_validation|other", "canonical_key": "...", "summary_zh": "...", "confidence": 0.0, "reason": "..."}\n'
        "  ]\n"
        "}\n\n"
        "要求：\n"
        "- canonical_title_zh 必须是中文、短、可展示；品种主题应以“品种：主题”开头，例如“黄金：央行购金与避险需求”。\n"
        "- canonical_key 用稳定中文短语即可，相同事件/主题要给相同 key；不要包含日期和价格点位。\n"
        "- summary_zh 是 1 句中文事实摘要，只保留对主题有用的核心事件/逻辑；必须删除 ETF/LOF/挂单/直播/点击标题/金饰报价/纯价格点位等噪声，不要逐条罗列原文。\n"
        "- drop 的 candidate 也要给 reason，canonical_title_zh 可为空。\n"
        "- 以下内容必须 drop，不能保留为 fundamental_validation：ETF/持仓日报、金饰或品牌金价、LOF停复牌或溢价风险、挂单/期权/筹码分布、纯技术分析、直播/点击查看/营销标题、单纯价格点位或涨跌幅。\n"
        "- 不要生成“黄金：ETF持仓与金饰价格”“白银：ETF持仓”“LOF停牌”这类主题；这些不是可追踪的研究主线。\n"
        "- CFTC/COMEX 等资金持仓只有在样本明确体现投机净持仓变化并可持续跟踪时才 keep，主题名用“资金持仓与交易情绪”。\n"
        "- 地缘政治主题要尽量合并到少数稳定 key，例如“中东冲突与霍尔木兹通航风险”；不要把同一批新闻拆成多个同义主题。\n"
        "- 宏观利率主题要尽量合并到“美联储通胀与利率预期”或“全球央行利率预期”，不要按每个官员/国家重复拆分。\n"
        "- 不要输出 Markdown，不要解释 JSON 外的内容。\n\n"
        f"候选主题：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _llm_available(provider: str) -> tuple[bool, str]:
    try:
        llm = get_provider(provider)
    except Exception as exc:  # pragma: no cover - defensive env failure path.
        return False, str(exc)
    if not llm.api_keys:
        return False, f"missing API key for {llm.name}; set {llm.api_key_hint}"
    return True, llm.name


def _coerce_llm_normalization(raw: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    action = str(raw.get("action") or "keep").strip().lower()
    if action not in {"keep", "drop"}:
        action = "keep"
    theme_type = str(raw.get("theme_type") or candidate.get("current_theme_type") or "other").strip()
    if theme_type not in LLM_THEME_TYPES:
        theme_type = "other"
    title = _clean_text(raw.get("canonical_title_zh"), limit=96)
    if action == "keep" and not title:
        title = _clean_text(candidate.get("current_title"), limit=96) or "未命名主题"
    key = _clean_text(raw.get("canonical_key"), limit=120) or title
    summary = _clean_text(raw.get("summary_zh"), limit=240)
    confidence = _bounded(raw.get("confidence"), default=0.5)
    return {
        "normalization_version": LLM_NORMALIZATION_VERSION,
        "normalization_id": candidate.get("cache_key") or _candidate_cache_key(candidate),
        "source_candidate_id": candidate["id"],
        "action": action,
        "canonical_title_zh": title,
        "theme_type": theme_type,
        "canonical_key": key,
        "summary_zh": summary,
        "confidence": confidence,
        "reason": _clean_text(raw.get("reason"), limit=180),
    }


def _llm_items_from_response(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    if parsed.get("id") and parsed.get("action"):
        return [parsed]
    for key in ("items", "results", "themes", "normalizations", "candidates", "data"):
        value = parsed.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    list_values = [value for value in parsed.values() if isinstance(value, list)]
    if len(list_values) == 1:
        return [item for item in list_values[0] if isinstance(item, dict)]
    raise ValueError(f"LLM output missing items list; keys={list(parsed)[:8]}")


def _parse_llm_items_response(text: str) -> list[dict[str, Any]]:
    stripped = strip_fences(text)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        value = parse_json_object(stripped)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return _llm_items_from_response(value)
    raise ValueError("LLM output is not a JSON object or array")


def _llm_row_id(row: dict[str, Any]) -> str:
    for key in ("id", "candidate_id", "source_candidate_id", "theme_id", "主题ID"):
        value = row.get(key)
        if value:
            return str(value)
    return ""


def _load_or_run_llm_normalizations(
    root: Path,
    candidates: list[dict[str, Any]],
    *,
    provider: str,
    batch_size: int,
    candidate_limit: int,
    require_llm: bool,
    use_cache: bool,
    timeout: int,
    progress: bool,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    available, provider_status = _llm_available(provider)
    if not available:
        if require_llm:
            raise RuntimeError(f"LLM normalization required but unavailable: {provider_status}")
        return {}, {
            "enabled": False,
            "provider": provider,
            "status": "unavailable",
            "reason": provider_status,
            "candidate_count": len(candidates),
        }

    limited = candidates[:candidate_limit] if candidate_limit > 0 else candidates
    selected_ids = {item["id"] for item in limited}
    cache_dir = _llm_normalization_cache_dir(root)
    normalizations: dict[str, dict[str, Any]] = {}
    uncached: list[dict[str, Any]] = []
    cache_hits = 0
    cache_dir.mkdir(parents=True, exist_ok=True)
    for candidate in limited:
        cache_key = _candidate_cache_key(candidate)
        candidate["cache_key"] = cache_key
        cache_path = cache_dir / f"{cache_key}.json"
        if use_cache and cache_path.exists():
            payload = read_json(cache_path)
            if isinstance(payload, dict) and payload.get("source_candidate_id"):
                normalizations[candidate["id"]] = payload
                cache_hits += 1
                continue
        uncached.append(candidate)

    llm_calls = 0
    llm_errors: list[str] = []
    batches = _chunked(uncached, batch_size)
    if progress:
        print(
            f"[theme_report_llm] candidates={len(candidates)} selected={len(limited)} "
            f"cache_hits={cache_hits} uncached={len(uncached)} batches={len(batches)} provider={provider_status}",
            flush=True,
        )
    for batch_index, batch in enumerate(batches, 1):
        if progress:
            print(f"[theme_report_llm] batch {batch_index}/{len(batches)} size={len(batch)}", flush=True)
        try:
            response_text = chat(
                _normalization_prompt(batch),
                provider=provider,
                max_tokens=3600,
                temperature=0.1,
                timeout=timeout,
            )
            rows = _parse_llm_items_response(response_text)
            rows_by_id = {_llm_row_id(row): row for row in rows}
            missing_ids = [candidate["id"] for candidate in batch if candidate["id"] not in rows_by_id]
            if missing_ids:
                missing_candidates = [candidate for candidate in batch if candidate["id"] in missing_ids]
                retry_text = chat(
                    _normalization_prompt(missing_candidates),
                    provider=provider,
                    max_tokens=1200,
                    temperature=0.1,
                    timeout=max(20, min(timeout, 80)),
                )
                retry_rows = _parse_llm_items_response(retry_text)
                rows_by_id.update({_llm_row_id(row): row for row in retry_rows})
                missing_ids = [candidate["id"] for candidate in batch if candidate["id"] not in rows_by_id]
                llm_calls += 1
            if missing_ids:
                # MiniMax occasionally answers a retry batch with only part of
                # the requested ids. In require-LLM mode we prefer paying for
                # narrow single-candidate retries over silently falling back to
                # deterministic titles.
                for candidate in [item for item in batch if item["id"] in missing_ids]:
                    single_text = chat(
                        _normalization_prompt([candidate]),
                        provider=provider,
                        max_tokens=900,
                        temperature=0.1,
                        timeout=max(20, min(timeout, 80)),
                    )
                    single_rows = _parse_llm_items_response(single_text)
                    if len(single_rows) == 1 and not _llm_row_id(single_rows[0]):
                        rows_by_id[candidate["id"]] = {**single_rows[0], "id": candidate["id"]}
                    else:
                        rows_by_id.update({_llm_row_id(row): row for row in single_rows})
                    llm_calls += 1
                missing_ids = [candidate["id"] for candidate in batch if candidate["id"] not in rows_by_id]
            if missing_ids and require_llm:
                raise ValueError(f"LLM output missing {len(missing_ids)} candidate ids; first={missing_ids[:3]}")
            for candidate in batch:
                row = rows_by_id.get(candidate["id"], {})
                normalized = _coerce_llm_normalization(row, candidate)
                normalizations[candidate["id"]] = normalized
                write_json(cache_dir / f"{candidate['cache_key']}.json", normalized)
            llm_calls += 1
        except Exception as exc:
            message = str(exc)[:300]
            llm_errors.append(message)
            if require_llm:
                raise RuntimeError(f"LLM normalization failed: {message}") from exc

    skipped = len(candidates) - len(selected_ids)
    return normalizations, {
        "enabled": True,
        "provider": provider_status,
        "status": "succeeded_with_errors" if llm_errors else "succeeded",
        "candidate_count": len(candidates),
        "selected_candidate_count": len(limited),
        "skipped_by_limit_count": skipped,
        "cache_hit_count": cache_hits,
        "llm_call_count": llm_calls,
        "error_count": len(llm_errors),
        "errors": llm_errors[:5],
        "cache_dir": relative_to_root(cache_dir, root),
    }


def _asset_key_for_normalized_id(theme: dict[str, Any]) -> str:
    labels = _theme_asset_labels(theme)
    return "|".join(labels)


def _apply_llm_summary_to_layers(layers: dict[str, Any] | None, summary: str, *, theme_type: str) -> dict[str, Any] | None:
    """Rewrite display-facing event layers with the LLM-cleaned fact summary.

    The schemas already allow these four event fields, so this keeps the
    normalization output compatible with existing frontend readers while
    avoiding ad-hoc top-level fields.
    """
    if not summary:
        return layers
    updated = {
        "fact_layer": None,
        "political_layer": None,
        "time_window_layer": None,
        "settlement_rule_layer": None,
        **dict(layers or {}),
    }
    updated["fact_layer"] = summary
    if theme_type in {"policy", "geopolitics"}:
        updated["political_layer"] = summary
    return updated


def _apply_llm_normalizations_to_payloads(
    root: Path,
    daily_payloads: list[dict[str, Any]],
    normalizations: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats = Counter()
    if not normalizations:
        return daily_payloads, dict(stats)

    theme_updates: dict[str, dict[str, Any]] = {}
    dropped_ids: set[str] = set()
    for payload in daily_payloads:
        for theme in payload.get("themes") or []:
            old_id = str(theme.get("theme_anchor_id") or "")
            norm = normalizations.get(old_id)
            if not norm:
                continue
            stats["llm_normalized_theme_count"] += 1
            if norm.get("action") == "drop":
                dropped_ids.add(old_id)
                stats["llm_dropped_theme_count"] += 1
                continue
            title = str(norm.get("canonical_title_zh") or theme.get("title") or "")
            theme_type = str(norm.get("theme_type") or theme.get("theme_type") or "other")
            normalized_id = _hash_id("THA-LLM", _asset_key_for_normalized_id(theme), norm.get("canonical_key") or title, theme_type)
            theme_updates[old_id] = {
                "theme_anchor_id": normalized_id,
                "title": title,
                "theme_type": theme_type,
                "normalization": norm,
                "summary_zh": _clean_text(norm.get("summary_zh"), limit=240),
            }

    normalized_payloads: list[dict[str, Any]] = []
    for payload in daily_payloads:
        themes: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        for theme in payload.get("themes") or []:
            old_id = str(theme.get("theme_anchor_id") or "")
            if old_id in dropped_ids:
                continue
            update = theme_updates.get(old_id)
            if update:
                old_title = str(theme.get("title") or "")
                theme = dict(theme)
                norm = update["normalization"]
                norm_id = str(norm.get("normalization_id") or norm.get("source_candidate_id") or old_id)
                summary = update.get("summary_zh") or ""
                theme["theme_anchor_id"] = update["theme_anchor_id"]
                theme["title"] = update["title"]
                theme["theme_type"] = update["theme_type"]
                theme["description"] = summary or theme.get("description") or update["title"]
                theme["event_definition_layers"] = _apply_llm_summary_to_layers(
                    theme.get("event_definition_layers"),
                    summary,
                    theme_type=update["theme_type"],
                )
                theme["aliases"] = list(dict.fromkeys([*(theme.get("aliases") or []), old_title, str(norm.get("canonical_key") or "")]))
                theme["source_refs"] = [
                    *(theme.get("source_refs") or []),
                    _source_ref(
                        "candidate",
                        "theme_report_llm_normalizer",
                        artifact_id=norm_id,
                        path=_llm_normalization_cache_dir(root) / f"{norm_id}.json",
                        root=root,
                    ),
                ]
            themes.append(theme)
        for signal in payload.get("signals") or []:
            refs = signal.get("theme_refs") or []
            ref = refs[0] if refs and isinstance(refs[0], dict) else {}
            old_id = str(ref.get("id") or "")
            if old_id in dropped_ids:
                stats["llm_dropped_signal_count"] += 1
                continue
            update = theme_updates.get(old_id)
            if update:
                signal = dict(signal)
                signal["theme_refs"] = [{"id": update["theme_anchor_id"], "label": update["title"], "ref_type": "theme"}]
                summary = update.get("summary_zh") or ""
                if summary:
                    signal["notes"] = summary
                    signal["event_definition_layers"] = _apply_llm_summary_to_layers(
                        signal.get("event_definition_layers"),
                        summary,
                        theme_type=update["theme_type"],
                    )
                mapping = dict(signal.get("mapping") or {})
                mapping["method"] = f"{mapping.get('method') or 'unknown'}+{LLM_NORMALIZATION_VERSION}"
                mapping["confidence"] = max(float(mapping.get("confidence") or 0), float(update["normalization"].get("confidence") or 0))
                signal["mapping"] = mapping
            signals.append(signal)
        payload = dict(payload)
        payload["themes"] = _dedupe_by_id(themes, "theme_anchor_id")
        payload["signals"] = _dedupe_by_id(signals, "signal_id")
        normalized_payloads.append(payload)
    return normalized_payloads, dict(stats)


def _daily_total(daily_stats: list[dict[str, Any]], key: str) -> int:
    return sum(int(day.get(key) or 0) for day in daily_stats)


def _build_theme_report(
    state: dict[str, Any],
    daily_stats: list[dict[str, Any]],
    *,
    signals: list[dict[str, Any]] | None = None,
    theme_anchors: list[dict[str, Any]] | None = None,
    llm_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    themes = list((state.get("themes") or {}).values())
    ranked = sorted(
        themes,
        key=lambda item: (
            int(item.get("support_count") or 0) + int(item.get("observation_count") or 0),
            str(item.get("last_seen_at") or ""),
        ),
        reverse=True,
    )
    asset_counter: Counter[str] = Counter()
    type_counter: Counter[str] = Counter()
    for theme in themes:
        type_counter[str(theme.get("theme_type") or "other")] += 1
        for ref in theme.get("asset_refs") or []:
            if isinstance(ref, dict):
                asset_counter[str(ref.get("label") or ref.get("id") or "")] += 1
    source_counter = Counter(str(signal.get("source_role") or "unknown") for signal in signals or [])
    kind_counter = Counter(str(signal.get("signal_kind") or "unknown") for signal in signals or [])
    daily_input_totals = {
        "report_file_count": _daily_total(daily_stats, "report_file_count"),
        "report_asset_count": _daily_total(daily_stats, "report_asset_count"),
        "report_candidate_count": _daily_total(daily_stats, "report_candidate_count"),
        "report_group_count": _daily_total(daily_stats, "report_group_count"),
        "report_skipped_price_validation_count": _daily_total(daily_stats, "report_skipped_price_validation_count"),
        "report_skipped_low_confidence_count": _daily_total(daily_stats, "report_skipped_low_confidence_count"),
        "report_duplicate_text_count": _daily_total(daily_stats, "report_duplicate_text_count"),
        "raw_news_count": _daily_total(daily_stats, "raw_news_count"),
        "mapped_news_count": _daily_total(daily_stats, "mapped_news_count"),
        "news_group_count": _daily_total(daily_stats, "news_group_count"),
        "news_skipped_price_validation_count": _daily_total(daily_stats, "news_skipped_price_validation_count"),
        "news_skipped_low_signal_notice_count": _daily_total(daily_stats, "news_skipped_low_signal_notice_count"),
        "news_cap_exceeded_count": _daily_total(daily_stats, "news_cap_exceeded_count"),
        "active_report_day_count": sum(1 for day in daily_stats if int(day.get("report_file_count") or 0) > 0),
    }
    state_stats = state.get("stats") or {}
    return {
        "schema_version": "incremental_theme_report.v1",
        "status": "candidate",
        "generated_at": utc_now_iso(),
        "stats": {
            **state_stats,
            "daily_count": len(daily_stats),
            "theme_type_count": len(type_counter),
            "asset_count": len(asset_counter),
            "candidate_signal_count": len(signals or []),
            "candidate_theme_anchor_count": len(theme_anchors or []),
            "candidate_event_definition_signal_count": kind_counter.get("event_definition", 0),
            "candidate_theme_update_signal_count": kind_counter.get("theme_update", 0),
            "candidate_source_role_counts": dict(source_counter),
            "candidate_signal_kind_counts": dict(kind_counter),
            "daily_input_totals": daily_input_totals,
            "last_update_new_signal_count": state_stats.get("new_signal_count", 0),
            "last_update_event_definition_signal_count": state_stats.get("event_definition_signals", 0),
            "llm_normalization": llm_stats or {"enabled": False},
        },
        "coverage": {
            "daily_stats": daily_stats,
            "news_coverage": "database_grouped_daily",
            "research_report_coverage": "WECHAT-PROFILE-V1 per_report grouped by date/asset/theme",
        },
        "top_themes": [
            {
                "theme_state_id": item.get("theme_state_id"),
                "title": item.get("title"),
                "theme_type": item.get("theme_type"),
                "first_seen_at": item.get("first_seen_at"),
                "last_seen_at": item.get("last_seen_at"),
                "support_count": item.get("support_count"),
                "observation_count": item.get("observation_count"),
                "conflict_count": item.get("conflict_count"),
                "current_phase": item.get("current_phase"),
                "asset_refs": item.get("asset_refs"),
                "source_ref_count": len(item.get("source_refs") or []),
                "signal_ref_count": len(item.get("signal_refs") or []),
            }
            for item in ranked[:80]
        ],
        "asset_summary": [{"asset": asset, "theme_count": count} for asset, count in asset_counter.most_common(50)],
        "theme_type_summary": [{"theme_type": key, "theme_count": count} for key, count in type_counter.most_common()],
        "quality_notes": [
            "LLM normalization is the intended theme keep/drop/merge/title/type stage when enabled.",
            "news inputs are grouped from MySQL flashes; raw news canonical_documents/news is currently empty.",
            "Deterministic labels are only candidate packaging or fallback when LLM normalization is disabled or unavailable.",
        ],
    }


def _markdown_report(report: dict[str, Any]) -> str:
    stats = report.get("stats") or {}
    source_counts = stats.get("candidate_source_role_counts") or {}
    daily_totals = stats.get("daily_input_totals") or {}
    llm_stats = stats.get("llm_normalization") or {}
    lines = [
        "# 增量主题报告候选",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- themes: {stats.get('theme_count')}",
        f"- events: {stats.get('event_count')}",
        f"- signals: {stats.get('candidate_signal_count', stats.get('signal_count'))}",
        f"- research_report_signals: {source_counts.get('research_report', 0)}",
        f"- news_signals: {source_counts.get('news', 0)}",
        f"- report_files: {daily_totals.get('report_file_count', 0)}",
        f"- filtered_price_items: {daily_totals.get('report_skipped_price_validation_count', 0) + daily_totals.get('news_skipped_price_validation_count', 0)}",
        f"- filtered_low_signal_news: {daily_totals.get('news_skipped_low_signal_notice_count', 0)}",
        f"- llm_normalization: {llm_stats.get('status', 'disabled')}",
        f"- llm_normalized_themes: {llm_stats.get('llm_normalized_theme_count', 0)}",
        f"- llm_dropped_signals: {llm_stats.get('llm_dropped_signal_count', 0)}",
        "",
        "## Top Themes",
    ]
    for idx, item in enumerate((report.get("top_themes") or [])[:30], 1):
        assets = "、".join(str(ref.get("label") or "") for ref in item.get("asset_refs") or [] if isinstance(ref, dict))
        lines.append(
            f"{idx}. {item.get('title')} | {item.get('theme_type')} | {item.get('first_seen_at')} -> {item.get('last_seen_at')} | "
            f"signals={item.get('signal_ref_count')} support={item.get('support_count')} obs={item.get('observation_count')} | {assets}"
        )
    lines.extend(["", "## Quality Notes"])
    lines.extend(f"- {note}" for note in report.get("quality_notes") or [])
    return "\n".join(lines) + "\n"


def _output_dir(root: Path, end_date: str, stamp: str) -> Path:
    yyyy, mm, dd = dated_parts(end_date)
    return root / "agent_workspace" / "candidates" / "theme_report_maintenance" / yyyy / mm / dd / f"CAND-THEME-REPORT-{end_date}-{stamp}"


def run_theme_report_maintenance(
    root: str | Path | None = None,
    *,
    start_date: str,
    end_date: str,
    per_report_run_id: str = DEFAULT_SINGLE_REPORT_RUN_ID,
    include_news: bool = True,
    include_price_validation: bool = False,
    include_low_signal_market_notices: bool = False,
    use_llm_normalization: bool = False,
    llm_provider: str = "m3",
    llm_batch_size: int = 24,
    llm_candidate_limit: int = 0,
    llm_sample_per_theme: int = 4,
    llm_timeout: int = 60,
    llm_progress: bool = False,
    require_llm: bool = False,
    no_llm_cache: bool = False,
    max_report_items_per_asset: int = 4,
    min_report_confidence: float = 0.38,
    max_news_per_day: int = 500,
    work_order_id: str = DEFAULT_WORK_ORDER_ID,
    validate: bool = True,
    write_latest: bool = False,
    write_outputs: bool = True,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    taxonomy = load_asset_taxonomy(root_path, required=True)
    created_at = utc_now_iso()
    date_keys = _date_range(start_date, end_date)
    state: dict[str, Any] | None = None
    all_signals: list[dict[str, Any]] = []
    all_themes: list[dict[str, Any]] = []
    daily_stats: list[dict[str, Any]] = []
    daily_payloads: list[dict[str, Any]] = []
    input_refs: list[dict[str, Any]] = []
    effective_include_price_validation = include_price_validation or use_llm_normalization
    effective_include_low_signal_market_notices = include_low_signal_market_notices or use_llm_normalization
    effective_min_report_confidence = 0.0 if use_llm_normalization else min_report_confidence

    for date_key in date_keys:
        daily_signals: list[dict[str, Any]] = []
        daily_themes: list[dict[str, Any]] = []
        report_groups, report_stats = _report_groups_for_date(
            root_path,
            taxonomy,
            date_key,
            per_report_run_id,
            max_items_per_asset=max_report_items_per_asset,
            include_price_validation=effective_include_price_validation,
            min_report_confidence=effective_min_report_confidence,
        )
        for group in report_groups.values():
            signal, theme = _report_group_to_signal_theme(group, date_key=date_key, created_at=created_at)
            daily_signals.append(signal)
            daily_themes.append(theme)
        news_stats: dict[str, int] = {}
        if include_news:
            groups, news_stats = _news_groups_for_date(
                root_path,
                taxonomy,
                date_key,
                max_news_per_day=max_news_per_day,
                include_price_validation=effective_include_price_validation,
                include_low_signal_market_notices=effective_include_low_signal_market_notices,
            )
            for group in groups.values():
                signal, theme = _news_group_to_signal_theme(group, date_key=date_key, created_at=created_at)
                daily_signals.append(signal)
                daily_themes.append(theme)

        daily_signals = _dedupe_by_id(daily_signals, "signal_id")
        daily_themes = _dedupe_by_id(daily_themes, "theme_anchor_id")
        daily_payloads.append(
            {
                "date": date_key,
                "signals": daily_signals,
                "themes": daily_themes,
                "stats": {
                    "date": date_key,
                    **report_stats,
                    **news_stats,
                },
            }
        )

    llm_stats: dict[str, Any] = {"enabled": False}
    if use_llm_normalization:
        raw_signals_for_llm = _dedupe_by_id([signal for payload in daily_payloads for signal in payload["signals"]], "signal_id")
        raw_themes_for_llm = _dedupe_by_id([theme for payload in daily_payloads for theme in payload["themes"]], "theme_anchor_id")
        candidates = _build_llm_theme_candidates(raw_signals_for_llm, raw_themes_for_llm, max_samples_per_theme=llm_sample_per_theme)
        normalizations, llm_stats = _load_or_run_llm_normalizations(
            root_path,
            candidates,
            provider=llm_provider,
            batch_size=llm_batch_size,
            candidate_limit=llm_candidate_limit,
            require_llm=require_llm,
            use_cache=not no_llm_cache,
            timeout=llm_timeout,
            progress=llm_progress,
        )
        daily_payloads, apply_stats = _apply_llm_normalizations_to_payloads(root_path, daily_payloads, normalizations)
        llm_stats = {**llm_stats, **apply_stats}

    for payload in daily_payloads:
        date_key = str(payload["date"])
        daily_signals = _dedupe_by_id(payload["signals"], "signal_id")
        daily_themes = _dedupe_by_id(payload["themes"], "theme_anchor_id")
        state = apply_incremental_update(
            state,
            signals=daily_signals,
            themes=daily_themes,
            run_id=f"{work_order_id}-{date_key}",
            generated_at=created_at,
            input_refs=[],
        )
        all_signals.extend(daily_signals)
        all_themes.extend(daily_themes)
        stats_row = dict(payload["stats"])
        stats_row["signal_count"] = len(daily_signals)
        stats_row["theme_anchor_count"] = len(daily_themes)
        daily_stats.append(stats_row)
    assert state is not None
    all_signals = _dedupe_by_id(all_signals, "signal_id")
    all_themes = _dedupe_by_id(all_themes, "theme_anchor_id")
    validation = {"schema_validation": "not_run"}
    if validate:
        validation = validate_signal_theme_objects(root_path, signals=all_signals, themes=all_themes)
    report = _build_theme_report(state, daily_stats, signals=all_signals, theme_anchors=all_themes, llm_stats=llm_stats)
    report["date_range"] = {"start_date": _canonical_date(start_date), "end_date": _canonical_date(end_date)}
    report["schema_validation"] = validation

    paths: dict[str, str] = {}
    if write_outputs:
        stamp = datetime.now().strftime("%H%M%S%f")
        out_dir = _output_dir(root_path, _canonical_date(end_date), stamp)
        signal_path = out_dir / "research_signals.json"
        theme_path = out_dir / "theme_anchors.json"
        state_path = out_dir / "research_state.json"
        report_json_path = out_dir / "theme_report.json"
        report_md_path = out_dir / "theme_report.md"
        manifest_path = out_dir / "manifest.json"
        run_manifest_path = root_path / "agent_workspace" / "runs" / "theme_report_maintenance" / stamp / "run_manifest.json"
        write_json(
            signal_path,
            {
                "schema_version": "research_signal_candidate_set.v1",
                "status": "candidate",
                "candidate_id": out_dir.name,
                "generated_at": created_at,
                "work_order_id": work_order_id,
                "signals": all_signals,
            },
        )
        write_json(
            theme_path,
            {
                "schema_version": "theme_anchor_candidate_set.v1",
                "status": "candidate",
                "candidate_id": out_dir.name,
                "generated_at": created_at,
                "work_order_id": work_order_id,
                "theme_anchors": all_themes,
            },
        )
        write_json(state_path, state)
        write_json(report_json_path, report)
        report_md_path.parent.mkdir(parents=True, exist_ok=True)
        report_md_path.write_text(_markdown_report(report), encoding="utf-8")
        manifest = {
            "schema_version": "theme_report_maintenance_manifest.v1",
            "status": "candidate",
            "candidate_id": out_dir.name,
            "generated_at": created_at,
            "work_order_id": work_order_id,
            "date_range": report["date_range"],
            "input_refs": input_refs,
            "stats": report["stats"],
            "schema_validation": validation,
            "outputs": {
                "research_signals": relative_to_root(signal_path, root_path),
                "theme_anchors": relative_to_root(theme_path, root_path),
                "research_state": relative_to_root(state_path, root_path),
                "theme_report_json": relative_to_root(report_json_path, root_path),
                "theme_report_markdown": relative_to_root(report_md_path, root_path),
            },
            "human_review_required": True,
        }
        write_json(manifest_path, manifest)
        write_json(
            run_manifest_path,
            {
                "schema_version": "theme_report_maintenance_run.v1",
                "status": "succeeded",
                "run_type": "theme_report_maintenance",
                "generated_at": created_at,
                "work_order_id": work_order_id,
                "manifest": relative_to_root(manifest_path, root_path),
                "stats": report["stats"],
                "human_review_required": True,
            },
        )
        paths = {
            "candidate_dir": relative_to_root(out_dir, root_path),
            "manifest": relative_to_root(manifest_path, root_path),
            "research_signals": relative_to_root(signal_path, root_path),
            "theme_anchors": relative_to_root(theme_path, root_path),
            "research_state": relative_to_root(state_path, root_path),
            "theme_report_json": relative_to_root(report_json_path, root_path),
            "theme_report_markdown": relative_to_root(report_md_path, root_path),
            "run_manifest": relative_to_root(run_manifest_path, root_path),
        }
        if write_latest:
            latest_dir = root_path / "agent_workspace" / "candidates" / "theme_report_maintenance" / "latest"
            write_json(latest_dir / "theme_report.json", report)
            write_json(latest_dir / "research_state.json", state)
            (latest_dir / "theme_report.md").parent.mkdir(parents=True, exist_ok=True)
            (latest_dir / "theme_report.md").write_text(_markdown_report(report), encoding="utf-8")
            paths["latest_theme_report_json"] = relative_to_root(latest_dir / "theme_report.json", root_path)
            paths["latest_research_state"] = relative_to_root(latest_dir / "research_state.json", root_path)
            paths["latest_theme_report_markdown"] = relative_to_root(latest_dir / "theme_report.md", root_path)

    return {
        "status": "succeeded",
        "stats": report["stats"],
        "schema_validation": validation,
        "paths": paths,
        "theme_report": report,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Maintain incremental theme reports from per-report JSON and news.")
    parser.add_argument("--root", default="", help="quanta_data root.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--per-report-run-id", default=DEFAULT_SINGLE_REPORT_RUN_ID)
    parser.add_argument("--no-news", action="store_true")
    parser.add_argument("--include-price-validation", action="store_true")
    parser.add_argument("--include-low-signal-market-notices", action="store_true")
    parser.add_argument("--llm-normalization", action="store_true")
    parser.add_argument("--llm-provider", default="m3")
    parser.add_argument("--llm-batch-size", type=int, default=24)
    parser.add_argument("--llm-candidate-limit", type=int, default=0)
    parser.add_argument("--llm-sample-per-theme", type=int, default=4)
    parser.add_argument("--llm-timeout", type=int, default=60)
    parser.add_argument("--llm-progress", action="store_true")
    parser.add_argument("--require-llm", action="store_true")
    parser.add_argument("--no-llm-cache", action="store_true")
    parser.add_argument("--max-report-items-per-asset", type=int, default=4)
    parser.add_argument("--min-report-confidence", type=float, default=0.38)
    parser.add_argument("--max-news-per-day", type=int, default=500)
    parser.add_argument("--work-order-id", default=DEFAULT_WORK_ORDER_ID)
    parser.add_argument("--no-validate", action="store_true")
    parser.add_argument("--write-latest", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = run_theme_report_maintenance(
        args.root or None,
        start_date=args.start_date,
        end_date=args.end_date,
        per_report_run_id=args.per_report_run_id,
        include_news=not args.no_news,
        include_price_validation=args.include_price_validation,
        include_low_signal_market_notices=args.include_low_signal_market_notices,
        use_llm_normalization=args.llm_normalization,
        llm_provider=args.llm_provider,
        llm_batch_size=args.llm_batch_size,
        llm_candidate_limit=args.llm_candidate_limit,
        llm_sample_per_theme=args.llm_sample_per_theme,
        llm_timeout=args.llm_timeout,
        llm_progress=args.llm_progress,
        require_llm=args.require_llm,
        no_llm_cache=args.no_llm_cache,
        max_report_items_per_asset=args.max_report_items_per_asset,
        min_report_confidence=args.min_report_confidence,
        max_news_per_day=args.max_news_per_day,
        work_order_id=args.work_order_id,
        validate=not args.no_validate,
        write_latest=args.write_latest,
        write_outputs=not args.dry_run,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "theme_report"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
