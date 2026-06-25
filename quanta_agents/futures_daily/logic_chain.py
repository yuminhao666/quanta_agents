from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from quanta_agents.core.io import read_json, utc_now_iso, write_json
from quanta_agents.core.llm_client import chat


DIRECTION_SIGN = {
    "bullish": 1.0,
    "bearish": -1.0,
    "forecast": 0.0,
    "event": 0.0,
    "neutral": 0.0,
}

BULLISH_WORDS = (
    "支撑",
    "偏强",
    "上行",
    "上涨",
    "改善",
    "去库",
    "减产",
    "短缺",
    "收缩",
    "低估",
    "做多",
    "反弹",
    "修复",
    "提振",
)

BEARISH_WORDS = (
    "压制",
    "偏弱",
    "下行",
    "下跌",
    "累库",
    "过剩",
    "疲弱",
    "增产",
    "回吐",
    "承压",
    "走弱",
    "做空",
    "宽松",
)

FIELD_PRIORITY = {
    "bullish_factors": 6,
    "bearish_factors": 6,
    "supply_demand": 5,
    "key_events": 4,
    "key_data": 3,
    "price_forecast": 2,
}

MARKET_OBSERVATION_FIELDS = {"market_observations", "price_forecast"}

MARKET_OBSERVATION_WORDS = (
    "夜盘",
    "日盘",
    "收盘",
    "收涨",
    "收跌",
    "盘面",
    "主力合约",
    "合约",
    "涨幅",
    "跌幅",
    "涨超",
    "跌超",
    "技术面",
    "技术位",
    "压力位",
    "支撑位",
    "均线",
    "成交量",
    "持仓",
    "点位",
)

FUNDAMENTAL_WORDS = (
    "供给",
    "供应",
    "需求",
    "消费",
    "库存",
    "产量",
    "产能",
    "开工",
    "装置",
    "检修",
    "进口",
    "出口",
    "到港",
    "发运",
    "利润",
    "成本",
    "加工费",
    "基差",
    "价差",
    "升贴水",
    "政策",
    "天气",
    "订单",
    "终端",
    "地产",
    "基建",
    "汽车",
    "家电",
)

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

WEIGHTING_METHOD = {
    "schema_version": "dimension_weighting.v2",
    "method": "deduped_evidence_netting_with_conflict_control",
    "direction_score": "先把同维度证据合并为 evidence_groups，再按组净方向计算；多空并存时按 directional_consensus 降权，避免单维度直接满分。",
    "importance_score": "基于去重证据组数量、组置信度、字段交叉验证、来源数量和字段多样性计算；维度内部冲突会降低重要性。",
    "effective_weight": "dimension importance_score / total importance_score，并应用单维度权重上限后重新归一化。",
    "max_dimension_weight": "0.28 when feasible; fewer than four dimensions allow higher cap so weights can sum to 1",
    "weighted_contribution": "direction_score * effective_weight; framework_score is the sum of all dimension weighted_contribution values, clipped to [-10, 10]",
    "link_sign": "bullish=1, bearish=-1; neutral/forecast/event infer weak sign from keywords when possible",
    "evidence_weight": "source_field_weight * status_weight * max(match_confidence, 0.25); duplicates only provide capped confirmation boost after grouping",
    "source_field_weight": {
        "bullish_factors": 1.0,
        "bearish_factors": 1.0,
        "supply_demand": 0.9,
        "price_forecast": 0.7,
        "key_events": 0.65,
        "key_data": 0.45,
        "default": 0.6,
    },
    "status_weight": {"auto_linked": 1.0, "pending_review_or_other": 0.65},
    "source_boost": "min(1.0, 0.35 + 0.08 * report_source_count_for_asset)",
    "field_diversity_boost": "min(1.0, 0.55 + 0.1 * distinct_source_field_count)",
    "conflict_policy": "同一维度内同时有利多和利空证据时，direction_score 按净额/总量和 consensus 降权；logic_chain 触发因素优先选择与维度净方向一致的证据，反向证据单独展示为 counter_evidence。",
    "market_data_policy": "price_forecast and pure market/technical observations are kept for tracking but excluded from fundamental direction_score, importance_score, effective_weight, and framework_score.",
    "note": "当前为研报证据触发的短期基本面权重；active framework 暂未提供长期 default_weight。",
}

SOURCE_FIELD_WEIGHTS = {
    "bullish_factors": 1.0,
    "bearish_factors": 1.0,
    "supply_demand": 0.9,
    "price_forecast": 0.7,
    "key_events": 0.65,
    "key_data": 0.45,
}

MAX_DIMENSION_WEIGHT = 0.28

FRAMEWORK_REVIEW_FINDINGS = [
    {
        "finding_id": "schema_name_mismatch",
        "severity": "medium",
        "summary": "active framework 的实际结构接近 commodity_research_framework.v1，但 schema_version 仍写 research_framework.v1。",
        "recommendation": "后续将品种框架统一到 commodity_research_framework.v1，或更新 research_framework.v1 兼容 core_dimensions。",
    },
    {
        "finding_id": "missing_dimension_weight",
        "severity": "high",
        "summary": "core_dimensions 缺少长期默认权重、短期激活权重和权重上下限。",
        "recommendation": "给每个 dimension 增加 default_weight、weight_bounds、importance_priors，用于把框架映射转成品种评分。",
    },
    {
        "finding_id": "logic_templates_empty",
        "severity": "medium",
        "summary": "logic_templates 字段存在但多数为空，暂时无法表达维度之间的因果链。",
        "recommendation": "沉淀 A -> B -> C 的品种逻辑模板，并关联 dimension_ids、indicator_refs、graph_path_hints。",
    },
    {
        "finding_id": "missing_scoring_rules",
        "severity": "high",
        "summary": "框架没有声明指标或事件对价格方向的 polarity、强度和时效规则。",
        "recommendation": "增加 scoring_rules，区分方向、强度、置信度、时间衰减和来源权重。",
    },
]


def _hash_id(prefix: str, *parts: str) -> str:
    raw = "||".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16].upper()}"


def _clean_label(label: str) -> str:
    text = str(label or "").strip()
    if "/" in text:
        text = text.rsplit("/", 1)[-1].strip()
    return text or "未归类"


def _text_sign(text: str) -> float:
    bullish = sum(1 for word in BULLISH_WORDS if word in text)
    bearish = sum(1 for word in BEARISH_WORDS if word in text)
    if bullish == bearish:
        return 0.0
    return 0.65 if bullish > bearish else -0.65


def _link_direction(link: dict[str, Any]) -> str:
    llm_direction = str(link.get("llm_impact_direction") or "").strip()
    if llm_direction in DIRECTION_SIGN:
        return llm_direction
    return str(link.get("direction") or "")


def _is_market_observation_link(link: dict[str, Any]) -> bool:
    field = str(link.get("source_field") or "")
    if field in MARKET_OBSERVATION_FIELDS:
        return True
    text = str(link.get("text") or "")
    if any(pattern in text for pattern in ("仅有盘面", "仅为价格表现", "行情涨跌", "价格表现")):
        return True
    has_market = any(word in text for word in MARKET_OBSERVATION_WORDS)
    has_fundamental = any(word in text for word in FUNDAMENTAL_WORDS)
    return field == "key_data" and has_market and not has_fundamental


def _is_invalid_evidence_link(link: dict[str, Any]) -> bool:
    text = str(link.get("text") or "").strip()
    if not text:
        return True
    return any(pattern in text for pattern in NO_EVIDENCE_PATTERNS)


def _link_sign(link: dict[str, Any]) -> float:
    if _is_invalid_evidence_link(link):
        return 0.0
    if _is_market_observation_link(link):
        return 0.0
    direction = _link_direction(link)
    base = DIRECTION_SIGN.get(direction, 0.0)
    if base:
        return base
    text_sign = _text_sign(str(link.get("text") or ""))
    field = str(link.get("source_field") or "")
    scale = {
        "supply_demand": 0.75,
        "price_forecast": 0.8,
        "key_events": 0.45,
        "key_data": 0.25,
    }.get(field, 0.4)
    return text_sign * scale


def _source_count(summary: dict[str, Any], asset: str) -> int:
    details = summary.get("detailed_analysis") or {}
    payload = details.get(asset) or {}
    sources = payload.get("original_sources") or []
    return len(sources) if isinstance(sources, list) else 0


def _confidence(link: dict[str, Any]) -> float:
    match = link.get("match") or {}
    try:
        return max(0.0, min(1.0, float(match.get("confidence") or 0)))
    except (TypeError, ValueError):
        return 0.0


def _canonical_text(text: str) -> str:
    raw = str(text or "").strip()
    raw = re.sub(r"\s+", "", raw)
    raw = re.sub(r"[，。；;、：:（）()【】\\[\\]{}<>《》\"'“”‘’]", "", raw)
    raw = raw.replace("天胶", "天然橡胶")
    raw = re.sub(r"(万吨|吨|%|个百分点|美元|元|点)$", "", raw)
    return raw.lower()


def _tokens_for_similarity(text: str) -> set[str]:
    return set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_+-]{1,}|[+-]?\d+(?:\.\d+)?%?", text))


def _similar(a: str, b: str) -> bool:
    ca = _canonical_text(a)
    cb = _canonical_text(b)
    if not ca or not cb:
        return False
    if ca == cb or ca in cb or cb in ca:
        return True
    ta = _tokens_for_similarity(a)
    tb = _tokens_for_similarity(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / max(min(len(ta), len(tb)), 1) >= 0.72


def _best_representative(links: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        links,
        key=lambda link: (
            FIELD_PRIORITY.get(str(link.get("source_field") or ""), 0),
            abs(_link_sign(link)),
            _confidence(link),
            len(str(link.get("text") or "")),
        ),
        reverse=True,
    )[0]


def _direction_label(score: float) -> str:
    if score > 0.25:
        return "bullish"
    if score < -0.25:
        return "bearish"
    return "neutral"


def _group_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    for link in sorted(links, key=lambda item: str(item.get("text") or "")):
        text = str(link.get("text") or "")
        for group in groups:
            if any(_similar(text, str(member.get("text") or "")) for member in group):
                group.append(link)
                break
        else:
            groups.append([link])

    evidence_groups = []
    for members in groups:
        representative = _best_representative(members)
        fields = Counter(str(member.get("source_field") or "") for member in members)
        directions = Counter(_link_direction(member) for member in members)
        weighted = sum(_link_sign(member) * _evidence_weight(member) for member in members)
        weight_sum = sum(_evidence_weight(member) for member in members)
        direction_score = weighted / max(weight_sum, 1e-6)
        evidence_groups.append(
            {
                "group_id": _hash_id(
                    "EGRP",
                    representative.get("asset", ""),
                    str((_dimension_key(representative) or [""])[0]),
                    str(representative.get("text") or ""),
                ),
                "representative_text": representative.get("text"),
                "direction": _direction_label(direction_score),
                "direction_score": round(direction_score, 3),
                "support_level": "cross_field" if len(fields) > 1 else "single_field",
                "scoring_role": "market_observation"
                if all(_is_market_observation_link(member) for member in members)
                else "fundamental_evidence",
                "merged_count": len(members),
                "source_fields": dict(fields),
                "directions": dict(directions),
                "avg_confidence": round(sum(_confidence(member) for member in members) / max(len(members), 1), 3),
                "factor_ids": [member.get("factor_id") for member in members],
                "duplicates": [
                    {
                        "factor_id": member.get("factor_id"),
                        "source_field": member.get("source_field"),
                        "direction": member.get("direction"),
                        "text": member.get("text"),
                    }
                    for member in members
                    if member is not representative
                ],
            }
        )
    evidence_groups.sort(
        key=lambda item: (
            abs(float(item["direction_score"])),
            int(item["merged_count"]),
            float(item["avg_confidence"]),
        ),
        reverse=True,
    )
    return evidence_groups


def _synthesize_dimension(label: str, evidence_groups: list[dict[str, Any]]) -> str:
    if not evidence_groups:
        return f"{label}维度暂无有效证据。"
    fundamental = [
        item
        for item in evidence_groups
        if item.get("scoring_role", "fundamental_evidence") != "market_observation"
    ]
    bullish = [item for item in fundamental if item["direction"] == "bullish"]
    bearish = [item for item in fundamental if item["direction"] == "bearish"]
    neutral = [item for item in fundamental if item["direction"] == "neutral"]
    parts = []
    if bullish and bearish:
        parts.append("多空证据并存，需要按净影响判断")
    if bullish:
        parts.append("支撑因素：" + "；".join(str(item["representative_text"]) for item in bullish[:3]))
    if bearish:
        parts.append("压制因素：" + "；".join(str(item["representative_text"]) for item in bearish[:3]))
    if neutral and not parts:
        parts.append("跟踪信息：" + "；".join(str(item["representative_text"]) for item in neutral[:3]))
    elif neutral:
        parts.append("待跟踪：" + "；".join(str(item["representative_text"]) for item in neutral[:2]))
    verification = [item for item in fundamental if item["support_level"] == "cross_field"]
    if verification:
        parts.append(f"{len(verification)}组证据在多个抽取字段中相互验证")
    return f"{label}维度：" + "。".join(parts) + "。"


def _group_to_factor(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "factor_id": group["group_id"],
        "direction": group["direction"],
        "source_field": ",".join(group["source_fields"].keys()),
        "text": group["representative_text"],
        "confidence": group["avg_confidence"],
        "status": group["support_level"],
        "scoring_role": group.get("scoring_role", "fundamental_evidence"),
        "merged_count": group["merged_count"],
    }


def _source_field_weight(field: str) -> float:
    return SOURCE_FIELD_WEIGHTS.get(field, 0.6)


def _evidence_weight(link: dict[str, Any]) -> float:
    field = str(link.get("source_field") or "")
    field_weight = _source_field_weight(field)
    status_weight = 1.0 if link.get("status") == "auto_linked" else 0.65
    return field_weight * status_weight * max(_confidence(link), 0.25)


def _group_direction_score(group: dict[str, Any]) -> float:
    try:
        return max(-1.0, min(1.0, float(group.get("direction_score") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _group_weight(group: dict[str, Any]) -> float:
    if group.get("scoring_role", "fundamental_evidence") == "market_observation":
        return 0.0
    score = abs(_group_direction_score(group))
    if score <= 0:
        return 0.0
    fields = group.get("source_fields") or {}
    field_weights = [_source_field_weight(str(field)) for field in fields if str(field)]
    field_quality = sum(field_weights) / max(len(field_weights), 1) if field_weights else 0.6
    support_boost = 1.16 if group.get("support_level") == "cross_field" else 1.0
    duplicate_boost = min(1.35, 1.0 + 0.12 * max(0, int(group.get("merged_count") or 1) - 1))
    confidence = max(float(group.get("avg_confidence") or 0.0), 0.25)
    return score * field_quality * support_boost * duplicate_boost * confidence


def _dimension_signal(evidence_groups: list[dict[str, Any]]) -> dict[str, Any]:
    scoring_groups = [
        group
        for group in evidence_groups
        if group.get("scoring_role", "fundamental_evidence") != "market_observation"
        and abs(_group_direction_score(group)) > 0
    ]
    positive_weight = 0.0
    negative_weight = 0.0
    weighted_net = 0.0
    gross_weight = 0.0
    confidence_weighted = 0.0
    for group in scoring_groups:
        direction = _group_direction_score(group)
        weight = _group_weight(group)
        if weight <= 0:
            continue
        gross_weight += weight
        weighted_net += direction * weight
        confidence_weighted += float(group.get("avg_confidence") or 0.0) * weight
        if direction > 0:
            positive_weight += direction * weight
        elif direction < 0:
            negative_weight += abs(direction) * weight

    if gross_weight <= 0:
        return {
            "direction_score": 0.0,
            "positive_weight": 0.0,
            "negative_weight": 0.0,
            "gross_weight": 0.0,
            "net_weight": 0.0,
            "directional_consensus": 0.0,
            "conflict_ratio": 0.0,
            "conflict_level": "none",
            "avg_group_confidence": 0.0,
            "scoring_group_count": 0,
        }

    net_weight = positive_weight - negative_weight
    net_ratio = net_weight / gross_weight
    directional_consensus = abs(net_weight) / gross_weight
    conflict_ratio = min(positive_weight, negative_weight) / gross_weight
    breadth_factor = min(1.0, 0.55 + 0.12 * len(scoring_groups))
    consensus_factor = 0.45 + 0.55 * directional_consensus
    direction_score = 10.0 * net_ratio * consensus_factor * breadth_factor
    if conflict_ratio >= 0.35:
        conflict_level = "high"
    elif conflict_ratio >= 0.18:
        conflict_level = "medium"
    elif conflict_ratio > 0:
        conflict_level = "low"
    else:
        conflict_level = "none"
    return {
        "direction_score": max(-10.0, min(10.0, direction_score)),
        "positive_weight": positive_weight,
        "negative_weight": negative_weight,
        "gross_weight": gross_weight,
        "net_weight": net_weight,
        "directional_consensus": directional_consensus,
        "conflict_ratio": conflict_ratio,
        "conflict_level": conflict_level,
        "avg_group_confidence": confidence_weighted / gross_weight,
        "scoring_group_count": len(scoring_groups),
    }


def _dimension_importance(
    summary: dict[str, Any],
    asset: str,
    scoring_links: list[dict[str, Any]],
    scoring_fields: Counter,
    signal: dict[str, Any],
) -> float:
    scoring_group_count = int(signal.get("scoring_group_count") or 0)
    if not scoring_group_count:
        return 0.0
    avg_confidence = max(float(signal.get("avg_group_confidence") or 0.0), 0.25)
    source_boost = min(1.0, 0.35 + 0.08 * _source_count(summary, asset))
    diversity_boost = min(1.0, 0.55 + 0.1 * len([field for field in scoring_fields if field]))
    confirmation_strength = float(signal.get("gross_weight") or 0.0)
    conflict_penalty = 1.0 - 0.45 * float(signal.get("conflict_ratio") or 0.0)
    return min(
        100.0,
        (scoring_group_count * 7.0 + confirmation_strength * 13.0)
        * avg_confidence
        * source_boost
        * diversity_boost
        * max(conflict_penalty, 0.45),
    )


def _cap_and_normalize_weights(importances: list[float]) -> list[float]:
    if not importances:
        return []
    total = sum(max(value, 0.0) for value in importances)
    if total <= 0:
        return [0.0 for _ in importances]
    raw = [max(value, 0.0) / total for value in importances]
    positive_count = sum(1 for value in raw if value > 0)
    cap = max(MAX_DIMENSION_WEIGHT, 1.0 / max(positive_count, 1))
    capped = [0.0 for _ in raw]
    remaining_indices = {index for index, value in enumerate(raw) if value > 0}
    remaining_total = 1.0
    remaining_raw = sum(raw)
    while remaining_indices and remaining_raw > 1e-12:
        changed = False
        for index in list(remaining_indices):
            weight = raw[index] / remaining_raw * remaining_total
            if weight >= cap:
                capped[index] = cap
                remaining_total -= cap
                remaining_raw -= raw[index]
                remaining_indices.remove(index)
                changed = True
        if not changed:
            for index in remaining_indices:
                capped[index] = raw[index] / remaining_raw * remaining_total
            break
    return capped


def _asset_links(alignment: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in alignment.get("alignments") or []:
        if isinstance(link, dict) and not _is_invalid_evidence_link(link):
            asset = str(link.get("asset") or "").strip()
            if asset:
                grouped[asset].append(link)
    return grouped


def _dimension_key(link: dict[str, Any]) -> tuple[str, str]:
    node = link.get("framework_node") or {}
    node_id = str(node.get("node_id") or "").strip() or "default::未归类"
    label = _clean_label(str(node.get("label") or "未归类"))
    return node_id, label


def _top_factors(links: list[dict[str, Any]], *, sign: float | None = None, limit: int = 5) -> list[dict[str, Any]]:
    ranked = []
    for link in links:
        link_sign = _link_sign(link)
        if sign is not None and (link_sign == 0 or math.copysign(1, link_sign) != math.copysign(1, sign)):
            continue
        ranked.append(
            {
                "factor_id": link.get("factor_id"),
                "direction": _link_direction(link),
                "source_field": link.get("source_field"),
                "text": link.get("text"),
                "confidence": round(_confidence(link), 3),
                "status": link.get("status"),
            }
        )
    ranked.sort(key=lambda item: item["confidence"], reverse=True)
    return ranked[:limit]


def build_dimension_scores(summary: dict[str, Any], alignment: dict[str, Any]) -> dict[str, Any]:
    grouped = _asset_links(alignment)
    assets: dict[str, Any] = {}
    for asset, links in sorted(grouped.items()):
        dimensions: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for link in links:
            dimensions[_dimension_key(link)].append(link)

        dimension_rows = []
        for (node_id, label), dim_links in dimensions.items():
            fields = Counter()
            methods = Counter()
            status = Counter()
            evidence_groups = _group_links(dim_links)
            scoring_links = [link for link in dim_links if not _is_market_observation_link(link)]
            scoring_fields = Counter(str(link.get("source_field") or "") for link in scoring_links)
            for link in dim_links:
                fields[str(link.get("source_field") or "")] += 1
                methods[str((link.get("match") or {}).get("method") or "")] += 1
                status[str(link.get("status") or "")] += 1
            signal = _dimension_signal(evidence_groups)
            avg_confidence = float(signal.get("avg_group_confidence") or 0.0)
            importance = _dimension_importance(summary, asset, scoring_links, scoring_fields, signal)
            direction_score = float(signal.get("direction_score") or 0.0)
            dimension_rows.append(
                {
                    "dimension_id": node_id,
                    "dimension_label": label,
                    "direction_score": round(direction_score, 2),
                    "importance_score": round(importance, 2),
                    "evidence_count": len(dim_links),
                    "scored_evidence_count": len(scoring_links),
                    "market_observation_count": len(dim_links) - len(scoring_links),
                    "avg_confidence": round(avg_confidence, 3),
                    "field_counts": dict(fields),
                    "match_methods": dict(methods),
                    "status_counts": dict(status),
                    "deduped_evidence_count": len(evidence_groups),
                    "duplicate_count": max(0, len(dim_links) - len(evidence_groups)),
                    "positive_evidence_weight": round(float(signal.get("positive_weight") or 0.0), 3),
                    "negative_evidence_weight": round(float(signal.get("negative_weight") or 0.0), 3),
                    "net_evidence_weight": round(float(signal.get("net_weight") or 0.0), 3),
                    "directional_consensus": round(float(signal.get("directional_consensus") or 0.0), 3),
                    "conflict_ratio": round(float(signal.get("conflict_ratio") or 0.0), 3),
                    "conflict_level": signal.get("conflict_level") or "none",
                    "scoring_group_count": signal.get("scoring_group_count") or 0,
                    "evidence_groups": evidence_groups,
                    "dimension_synthesis": _synthesize_dimension(label, evidence_groups),
                    "positive_factors": _top_factors(dim_links, sign=1, limit=4),
                    "negative_factors": _top_factors(dim_links, sign=-1, limit=4),
                    "neutral_factors": _top_factors(
                        [link for link in dim_links if _link_sign(link) == 0],
                        sign=None,
                        limit=4,
                    ),
                }
            )

        capped_weights = _cap_and_normalize_weights([float(row["importance_score"]) for row in dimension_rows])
        for row, effective_weight in zip(dimension_rows, capped_weights):
            row["effective_weight"] = round(effective_weight, 4)
            row["effective_weight_pct"] = round(effective_weight * 100, 2)
            row["weighted_contribution"] = round(float(row["direction_score"]) * effective_weight, 3)
        dimension_rows.sort(
            key=lambda row: (
                abs(float(row.get("weighted_contribution") or 0)),
                abs(float(row["direction_score"])),
                float(row["importance_score"]),
            ),
            reverse=True,
        )
        aggregate_score = sum(float(row["direction_score"]) * float(row["effective_weight"]) for row in dimension_rows)
        source_score = (summary.get("sentiment_scores") or {}).get(asset)
        score_quality = _score_divergence(source_score, aggregate_score)
        assets[asset] = {
            "asset": asset,
            "source_sentiment_score": source_score,
            "framework_score": round(max(-10.0, min(10.0, aggregate_score)), 2),
            "score_quality": score_quality,
            "dimension_count": len(dimension_rows),
            "evidence_count": len(links),
            "dimensions": dimension_rows,
        }

    return {
        "schema_version": "dimension_scores.v1",
        "status": "candidate",
        "report_date": summary.get("date"),
        "generated_at": utc_now_iso(),
        "scoring_note": "framework_score 基于框架维度方向分、证据强度、映射置信度、来源数量和字段多样性综合计算，暂不写回 active framework。",
        "weighting_method": WEIGHTING_METHOD,
        "assets": assets,
    }


def _recommendation(score: float) -> str:
    if score >= 3.0:
        return "偏多观察"
    if score <= -3.0:
        return "偏空观察"
    if score > 0.8:
        return "小幅偏多"
    if score < -0.8:
        return "小幅偏空"
    return "中性/等待确认"


def _decision_score(framework_score: float, divergence: dict[str, Any], high_conflict_count: int) -> float:
    score = framework_score
    if divergence.get("status") == "opposite_direction":
        gap = float(divergence.get("gap") or 0.0)
        score *= 0.15 if gap >= 6 else 0.35
    elif divergence.get("status") == "large_gap":
        score *= 0.85
    if high_conflict_count:
        score *= max(0.65, 1.0 - 0.12 * min(high_conflict_count, 3))
    return max(-10.0, min(10.0, score))


def _score_divergence(source_score: Any, framework_score: float) -> dict[str, Any]:
    try:
        source = float(source_score)
    except (TypeError, ValueError):
        return {"status": "no_source_score", "source_score": source_score, "framework_score": framework_score}
    opposite = source * framework_score < 0 and abs(source) >= 2 and abs(framework_score) >= 2
    gap = abs(source - framework_score)
    status = "opposite_direction" if opposite else "large_gap" if gap >= 5 else "aligned"
    return {
        "status": status,
        "source_score": round(source, 2),
        "framework_score": round(framework_score, 2),
        "gap": round(gap, 2),
    }


def _narrative_bias(score: float) -> str:
    if score >= 3:
        return "偏多"
    if score <= -3:
        return "偏空"
    if score > 0.8:
        return "小幅偏多"
    if score < -0.8:
        return "小幅偏空"
    return "中性，等待更多证据确认"


def _compact_text(text: Any, limit: int = 110) -> str:
    value = re.sub(r"\s+", "", str(text or "").strip())
    value = value.strip("。；;，, ")
    prefixes = ("支撑因素：", "压制因素：", "跟踪信息：", "待跟踪：")
    for prefix in prefixes:
        if value.startswith(prefix):
            value = value[len(prefix) :]
    if len(value) <= limit:
        return value
    cut = value[:limit].rstrip("，,；;、 ")
    return f"{cut}..."


def _concise_fact(text: Any, *, limit: int = 78) -> str:
    value = re.sub(r"\s+", "", str(text or "").strip())
    value = value.strip("。；;，, ")
    prefixes = ("支撑因素：", "压制因素：", "跟踪信息：", "待跟踪：")
    for prefix in prefixes:
        if value.startswith(prefix):
            value = value[len(prefix) :]
    if not value:
        return ""
    clauses = [item.strip("。；;，, ") for item in re.split(r"[；;。！？]", value) if item.strip("。；;，, ")]
    if not clauses:
        clauses = [value]

    def clause_rank(clause: str) -> tuple[int, int]:
        has_number = 1 if re.search(r"\d", clause) else 0
        has_market_word = 1 if any(word in clause for word in ("涨跌", "盘面", "技术", "成交", "持仓")) else 0
        has_fact_word = 1 if any(
            word in clause
            for word in ("截至", "环比", "同比", "库存", "开工", "利润", "排产", "进口", "出口", "复产", "检修", "降", "增")
        ) else 0
        return (has_number + has_fact_word - has_market_word, -clauses.index(clause))

    candidate = max(clauses, key=clause_rank)
    candidate = re.sub(r"（[^）]{13,}）", "", candidate).strip("，,、；; ")
    if len(candidate) <= limit:
        return candidate
    parts = [item.strip("，,、 ") for item in re.split(r"[，,、]", candidate) if item.strip("，,、 ")]
    if not parts:
        return candidate[:limit].rstrip("，,、；; ")
    chosen: list[str] = []
    for part in parts:
        proposal = "，".join([*chosen, part]) if chosen else part
        if len(proposal) <= limit:
            chosen.append(part)
    if chosen:
        return "，".join(chosen)
    return parts[0][:limit].rstrip("，,、；; ")


def _strip_fences(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```[a-zA-Z]*\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _parse_json_object(text: str) -> dict[str, Any]:
    value = _strip_fences(text)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        start = value.find("{")
        while start >= 0:
            try:
                parsed, _ = decoder.raw_decode(value[start:])
                break
            except json.JSONDecodeError:
                start = value.find("{", start + 1)
        else:
            raise
    if not isinstance(parsed, dict):
        raise ValueError("LLM output is not a JSON object")
    return parsed


def _fundamental_groups(row: dict[str, Any], direction: str | None = None) -> list[dict[str, Any]]:
    groups = [
        group
        for group in (row.get("evidence_groups") or [])
        if group.get("scoring_role", "fundamental_evidence") != "market_observation"
    ]
    if direction:
        groups = [group for group in groups if group.get("direction") == direction]
    return groups


def _directional_components(rows: list[dict[str, Any]], direction: str, *, limit: int = 4) -> list[dict[str, Any]]:
    components = []
    for row in rows:
        label = str(row.get("dimension_label") or "未归类")
        groups = _fundamental_groups(row, direction)
        if not groups:
            continue
        evidence = [_compact_text(group.get("representative_text")) for group in groups if group.get("representative_text")]
        if not evidence:
            continue
        components.append(
            {
                "dimension_id": row.get("dimension_id"),
                "dimension_label": label,
                "direction": direction,
                "dimension_score": row.get("direction_score"),
                "effective_weight_pct": row.get("effective_weight_pct"),
                "weighted_contribution": row.get("weighted_contribution"),
                "evidence": evidence[:2],
            }
        )
    components.sort(
        key=lambda item: (
            0 if item["dimension_label"] == "未归类" else 1,
            abs(float(item.get("weighted_contribution") or 0)),
            float(item.get("effective_weight_pct") or 0),
        ),
        reverse=True,
    )
    named = [item for item in components if item["dimension_label"] != "未归类"]
    return (named or components)[:limit]


def _tracking_components(rows: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    components = []
    for row in rows:
        label = str(row.get("dimension_label") or "未归类")
        if label == "未归类":
            continue
        groups = _fundamental_groups(row, "neutral")
        if not groups:
            continue
        evidence = [_compact_text(group.get("representative_text"), limit=90) for group in groups if group.get("representative_text")]
        if evidence:
            components.append(
                {
                    "dimension_id": row.get("dimension_id"),
                    "dimension_label": label,
                    "direction": "neutral",
                    "evidence": evidence[:2],
                }
            )
    return components[:limit]


def _component_phrase(component: dict[str, Any], *, evidence_limit: int = 2, text_limit: int = 110) -> str:
    evidence = "；".join(
        _compact_text(item, limit=text_limit)
        for item in (component.get("evidence") or [])[:evidence_limit]
    )
    label = component.get("dimension_label") or "相关因素"
    return f"{label}方面，{evidence}" if evidence else str(label)


def _join_component_phrases(components: list[dict[str, Any]], *, limit: int = 3) -> str:
    return "；".join(_component_phrase(component) for component in components[:limit])


def _concise_component_phrase(component: dict[str, Any]) -> str:
    label = str(component.get("dimension_label") or "相关因素")
    evidence = component.get("evidence") or []
    if not evidence:
        return label
    fact = _concise_fact(evidence[0], limit=78)
    return f"{label}（{fact}）" if fact else label


def _join_concise_components(components: list[dict[str, Any]], *, limit: int = 2) -> str:
    return "、".join(_concise_component_phrase(component) for component in components[:limit])


def _mainline_components(score: float, rows: list[dict[str, Any]]) -> dict[str, Any]:
    positive = _directional_components(rows, "bullish")
    negative = _directional_components(rows, "bearish")
    tracking = _tracking_components(rows)
    if score > 0.8:
        core = positive
        constraints = negative
    elif score < -0.8:
        core = negative
        constraints = positive
    else:
        core = positive[:2] + negative[:2]
        constraints = []
    return {
        "bias": _narrative_bias(score),
        "core_drivers": core[:3],
        "constraints": constraints[:3],
        "tracking": tracking[:3],
    }


def _compose_main_trade_thesis(asset: str, score: float, components: dict[str, Any]) -> str:
    bias = str(components.get("bias") or _narrative_bias(score))
    core = components.get("core_drivers") or []
    constraints = components.get("constraints") or []
    tracking = components.get("tracking") or []
    sentences = [f"{asset}基本面主线{bias}。"]
    if core:
        driver_label = "核心支撑" if score > 0.8 else "核心压力" if score < -0.8 else "主要分歧"
        sentences.append(f"{driver_label}来自{_join_concise_components(core, limit=2)}。")
    else:
        sentences.append("当前缺少足够的基本面证据形成清晰主线。")
    if constraints:
        constraint_label = "主要约束" if score > 0.8 else "主要支撑或修复线索"
        sentences.append(f"{constraint_label}是{_join_concise_components(constraints, limit=2)}。")
    if tracking:
        tracking_labels = "、".join(str(item.get("dimension_label") or "相关因素") for item in tracking[:3])
        sentences.append(f"后续重点跟踪{tracking_labels}是否继续验证或反转。")
    return "".join(sentences)


def _clean_main_thesis_text(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"^#+\s*主线判断\s*", "", value)
    value = re.sub(r"^主线判断[：:\s]*", "", value)
    value = re.sub(r"\n+", "。", value)
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"(偏多|小幅偏多|中性|小幅偏空|偏空)[。；;，,]*$", "", value).strip("。；;，, ")
    if value and value[-1] not in "。！？":
        value = f"{value}。"
    return value


def _is_poor_main_thesis(text: str) -> bool:
    value = _clean_main_thesis_text(text)
    if len(value) > 620:
        return True
    if value.count("方面") >= 7:
        return True
    if value.count("；") >= 8:
        return True
    repeated_markers = ("支撑因素", "压制因素", "维度：", "待跟踪：")
    return any(marker in value for marker in repeated_markers)


def _llm_dimension_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = []
    for row in rows:
        groups = []
        for group in (row.get("evidence_groups") or [])[:8]:
            groups.append(
                {
                    "text": _compact_text(group.get("representative_text"), limit=180),
                    "direction": group.get("direction"),
                    "support_level": group.get("support_level"),
                    "merged_count": group.get("merged_count"),
                    "scoring_role": group.get("scoring_role", "fundamental_evidence"),
                    "source_fields": group.get("source_fields") or {},
                }
            )
        payload.append(
            {
                "dimension_label": row.get("dimension_label"),
                "direction_score": row.get("direction_score"),
                "importance_score": row.get("importance_score"),
                "effective_weight_pct": row.get("effective_weight_pct"),
                "weighted_contribution": row.get("weighted_contribution"),
                "directional_consensus": row.get("directional_consensus"),
                "conflict_level": row.get("conflict_level"),
                "conflict_ratio": row.get("conflict_ratio"),
                "scored_evidence_count": row.get("scored_evidence_count"),
                "market_observation_count": row.get("market_observation_count"),
                "evidence_groups": groups,
            }
        )
    return payload


def _llm_compose_main_trade_thesis(
    asset: str,
    score: float,
    rows: list[dict[str, Any]],
    components: dict[str, Any],
) -> dict[str, Any]:
    fallback_text = _compose_main_trade_thesis(asset, score, components)
    payload = {
        "asset": asset,
        "framework_score": components.get("framework_score", round(score, 2)),
        "decision_score": round(score, 2),
        "bias": components.get("bias"),
        "score_diagnostics": components.get("score_diagnostics") or {},
        "rule_components": components,
        "dimensions": _llm_dimension_payload(rows),
    }
    prompt = (
        "你是商品期货研究员，请基于结构化证据生成该品种的“主线判断”。\n"
        "严格输出 JSON，不要 Markdown，格式为：\n"
        '{"main_trade_thesis":"自然、通顺、有主次的中文主线判断",'
        '"core_drivers":["核心驱动1","核心驱动2"],'
        '"constraints":["反向约束1"],'
        '"tracking_points":["后续跟踪1"],'
        '"quality_notes":["如有证据冲突或口径问题，在这里说明"]}\n\n'
        "写作要求：\n"
        "1. 不要机械拼接字段名，不要出现“某某维度：支撑因素：”这种套娃表达。\n"
        "2. 先给方向结论，再说明核心驱动、反向约束和需要验证的变量；多空证据并存时要交代主次。\n"
        "3. 如果 score_diagnostics 显示原始分和框架分方向相反，或关键维度 conflict_level 为 high/medium，结论必须降级为谨慎观察，并说明分歧来源。\n"
        "4. 行情涨跌、盘面强弱、技术位、成交持仓只能作为观察或跟踪，不能当作基本面主线依据。\n"
        "5. 只能使用输入证据，不要编造数据；不要把黄金证据无脑替代白银逻辑，除非证据明确说贵金属板块共振。\n"
        "6. 语言要像投研日报，通顺、有逻辑，保留最关键的 1-3 个数据和时间口径；不要罗列所有数据。\n"
        "7. 主线判断控制在 220-420 个中文字符；不要在末尾重复“偏多/小幅偏多”等推荐标签。\n\n"
        "结构化证据：\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        parsed = _parse_json_object(chat(prompt, max_tokens=1800, temperature=0.25, timeout=120))
        main_text = _clean_main_thesis_text(str(parsed.get("main_trade_thesis") or ""))
        if not main_text:
            raise ValueError("LLM thesis output missing main_trade_thesis")
        quality_notes = parsed.get("quality_notes") if isinstance(parsed.get("quality_notes"), list) else []
        if _is_poor_main_thesis(main_text):
            quality_notes = [*quality_notes, "LLM主线过长或重复，已切换为规则压缩版。"]
            main_text = fallback_text
        return {
            "method": "llm",
            "main_trade_thesis": main_text,
            "core_drivers": parsed.get("core_drivers") if isinstance(parsed.get("core_drivers"), list) else [],
            "constraints": parsed.get("constraints") if isinstance(parsed.get("constraints"), list) else [],
            "tracking_points": parsed.get("tracking_points") if isinstance(parsed.get("tracking_points"), list) else [],
            "quality_notes": quality_notes,
        }
    except Exception as exc:
        return {
            "method": "rule_fallback",
            "main_trade_thesis": fallback_text,
            "core_drivers": [_component_phrase(item) for item in (components.get("core_drivers") or [])],
            "constraints": [_component_phrase(item) for item in (components.get("constraints") or [])],
            "tracking_points": [_component_phrase(item) for item in (components.get("tracking") or [])],
            "quality_notes": [f"LLM主线生成失败，已使用规则兜底：{exc}"],
        }


def _watch_points(rows: list[dict[str, Any]]) -> list[str]:
    points = []
    for row in rows:
        label = str(row.get("dimension_label") or "")
        if label == "未归类":
            continue
        points.append(f"跟踪{label}维度是否继续强化或转弱")
    return points[:5]


def _list_texts(value: Any, *, limit: int = 8) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    texts: list[str] = []
    for item in items:
        if isinstance(item, dict):
            text = item.get("text") or item.get("summary") or item.get("title") or item.get("content")
        else:
            text = item
        text = _summary_sentence(text, limit=180)
        if text:
            texts.append(text)
    return texts[:limit]


def _score_from_summary(summary: dict[str, Any], asset: str, detail: dict[str, Any]) -> float:
    for value in (
        detail.get("sentiment_score"),
        (summary.get("sentiment_scores") or {}).get(asset),
    ):
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _brief_anchor_direction(score: float) -> str:
    if score > 0.8:
        return "bullish"
    if score < -0.8:
        return "bearish"
    return "neutral"


def _brief_anchor_direction_label(direction: str) -> str:
    return {
        "bullish": "偏多",
        "bearish": "偏空",
        "neutral": "中性/待验证",
    }.get(direction, "中性/待验证")


def _field_direction(field: str, text: str) -> str:
    if field == "bullish_factors":
        return "bullish"
    if field == "bearish_factors":
        return "bearish"
    sign = _text_sign(text)
    return _direction_for_score(sign)


def _evidence_from_detail(detail: dict[str, Any], *, limit: int = 10) -> list[dict[str, Any]]:
    rows = []
    for field in (
        "bullish_factors",
        "bearish_factors",
        "supply_demand",
        "key_events",
        "key_data",
        "price_forecast",
    ):
        for index, text in enumerate(_list_texts(detail.get(field), limit=limit)):
            rows.append(
                {
                    "evidence_id": _hash_id("BRFEV", str(detail.get("commodity") or ""), field, str(index), text),
                    "source_field": field,
                    "direction": _field_direction(field, text),
                    "text": text,
                }
            )
    return rows[:limit]


def _rank_anchor_evidence(rows: list[dict[str, Any]], anchor_direction: str, *, limit: int = 8) -> list[dict[str, Any]]:
    field_rank = {
        "bullish_factors": 6,
        "bearish_factors": 6,
        "supply_demand": 5,
        "key_events": 4,
        "key_data": 3,
        "price_forecast": 2,
    }
    rows = sorted(
        rows,
        key=lambda row: (
            row.get("direction") == anchor_direction,
            field_rank.get(str(row.get("source_field") or ""), 0),
            len(str(row.get("text") or "")),
        ),
        reverse=True,
    )
    return rows[:limit]


def _tracking_from_detail(detail: dict[str, Any], *, limit: int = 5) -> list[str]:
    texts = []
    for field in ("price_forecast", "market_observations", "key_events", "key_data"):
        texts.extend(_list_texts(detail.get(field), limit=4))
    result = []
    for text in texts:
        fact = _concise_fact(text, limit=90)
        if fact and fact not in result:
            result.append(fact)
    return result[:limit]


def _opposite_direction(direction: str) -> str:
    return "bearish" if direction == "bullish" else "bullish" if direction == "bearish" else ""


def _risk_items_from_evidence(rows: list[dict[str, Any]], anchor_direction: str, *, limit: int = 4) -> list[str]:
    opposite = _opposite_direction(anchor_direction)
    if not opposite:
        return []
    risks = []
    for row in rows:
        if row.get("direction") == opposite:
            text = _concise_fact(row.get("text"), limit=96)
            if text:
                risks.append(f"若{text}继续强化，期市速递主线需要降级或修正")
    return risks[:limit]


def _brief_title(asset: str, direction: str, evidence: list[dict[str, Any]]) -> str:
    label = _brief_anchor_direction_label(direction)
    first = next((item for item in evidence if item.get("direction") == direction), None) or (evidence[0] if evidence else {})
    fact = _concise_fact(first.get("text"), limit=36) if first else ""
    return f"{asset}{label}主线：{fact}" if fact else f"{asset}{label}主线"


def build_brief_thesis_anchor(summary: dict[str, Any]) -> dict[str, Any]:
    assets: dict[str, Any] = {}
    for asset, detail in sorted((summary.get("detailed_analysis") or {}).items()):
        if not isinstance(detail, dict):
            continue
        score = _score_from_summary(summary, str(asset), detail)
        direction = _brief_anchor_direction(score)
        evidence = _rank_anchor_evidence(_evidence_from_detail(detail), direction)
        main_thesis = _summary_sentence(detail.get("fundamental_summary"), limit=360)
        if not main_thesis and evidence:
            main_thesis = "；".join(_concise_fact(item.get("text"), limit=90) for item in evidence[:3] if item.get("text"))
        if not main_thesis:
            continue
        anchor_id = _hash_id("BRANCH", str(summary.get("date")), str(asset), main_thesis)
        tracking_items = _tracking_from_detail(detail)
        risks = _risk_items_from_evidence(evidence, direction)
        assets[str(asset)] = {
            "anchor_id": anchor_id,
            "asset": str(asset),
            "direction": direction,
            "direction_label": _brief_anchor_direction_label(direction),
            "source_sentiment_score": round(score, 2),
            "thesis_title": _brief_title(str(asset), direction, evidence),
            "main_thesis": main_thesis,
            "key_evidence": evidence,
            "tracking_items": tracking_items,
            "risk_items": risks,
            "invalidation_conditions": risks
            or [f"若{str(asset)}新增实时信号与期市速递方向持续冲突，需人工复核主线有效性"],
            "source_fields": sorted({str(item.get("source_field") or "") for item in evidence if item.get("source_field")}),
        }
    return {
        "schema_version": "brief_thesis_anchor.v1",
        "status": "candidate",
        "report_date": summary.get("date"),
        "generated_at": utc_now_iso(),
        "source": "market_brief_and_commodity_summary_baseline",
        "baseline_policy": "market_brief/commodity_summary remains the primary human-readable product; logic_chain is an incremental signal/evidence layer until human acceptance.",
        "assets": assets,
    }


def _anchor_evidence_match(signal_text: str, anchor: dict[str, Any]) -> dict[str, Any] | None:
    for item in anchor.get("key_evidence") or []:
        if _similar(signal_text, str(item.get("text") or "")):
            return item
    if _similar(signal_text, str(anchor.get("main_thesis") or "")):
        return {
            "evidence_id": anchor.get("anchor_id"),
            "source_field": "fundamental_summary",
            "direction": anchor.get("direction"),
            "text": anchor.get("main_thesis"),
        }
    return None


def _signal_row(signal: dict[str, Any], *, relation: str, matched_anchor: dict[str, Any] | None = None) -> dict[str, Any]:
    row = {
        "signal_id": signal.get("factor_id"),
        "relation": relation,
        "direction": signal.get("direction"),
        "source_field": signal.get("source_field"),
        "text": signal.get("text"),
        "confidence": signal.get("confidence"),
        "scoring_role": signal.get("scoring_role"),
        "merged_count": signal.get("merged_count"),
    }
    if matched_anchor:
        row["matched_anchor_evidence_id"] = matched_anchor.get("evidence_id")
        row["matched_anchor_source_field"] = matched_anchor.get("source_field")
    return row


def _classify_chain_against_anchor(chain: dict[str, Any], anchor: dict[str, Any] | None) -> dict[str, Any]:
    if not anchor:
        return {
            "benchmark_relation": "new_signal_without_brief_anchor",
            "inherited_signals": [],
            "new_signals": [
                _signal_row(signal, relation="new_signal_without_brief_anchor")
                for signal in (chain.get("evidence") or [])
                if signal.get("scoring_role") != "market_observation"
            ],
            "conflict_signals": [],
            "pending_observations": [],
        }
    anchor_direction = str(anchor.get("direction") or "neutral")
    opposite = _opposite_direction(anchor_direction)
    inherited = []
    new = []
    conflicts = []
    pending = []
    for signal in chain.get("evidence") or []:
        direction = str(signal.get("direction") or "neutral")
        text = str(signal.get("text") or "")
        matched_anchor = _anchor_evidence_match(text, anchor)
        if signal.get("scoring_role") == "market_observation" or direction == "neutral":
            pending.append(_signal_row(signal, relation="pending_observation", matched_anchor=matched_anchor))
        elif opposite and direction == opposite:
            conflicts.append(_signal_row(signal, relation="conflicts_with_brief_anchor", matched_anchor=matched_anchor))
        elif matched_anchor:
            inherited.append(_signal_row(signal, relation="inherits_brief_anchor", matched_anchor=matched_anchor))
        else:
            new.append(_signal_row(signal, relation="new_supporting_signal"))
    for signal in chain.get("counter_evidence") or []:
        if signal.get("scoring_role") == "market_observation":
            pending.append(_signal_row(signal, relation="pending_counter_observation"))
        else:
            conflicts.append(_signal_row(signal, relation="counter_evidence"))

    conflict_level = str(chain.get("conflict_level") or "none")
    if conflict_level in {"medium", "high"}:
        pending.append(
            {
                "relation": "pending_conflict_review",
                "text": f"{chain.get('dimension_label')}维度内部多空证据冲突为{conflict_level}",
                "dimension_id": chain.get("dimension_id"),
            }
        )

    if conflicts and (inherited or new):
        relation = "mixed_conflict_needs_review"
    elif conflicts:
        relation = "conflicts_with_baseline"
    elif inherited or new:
        relation = "supports_baseline"
    else:
        relation = "pending_observation"
    return {
        "benchmark_relation": relation,
        "inherited_signals": inherited,
        "new_signals": new,
        "conflict_signals": conflicts,
        "pending_observations": pending,
    }


def build_brief_logic_benchmark_map(
    brief_thesis_anchor: dict[str, Any],
    logic_chains: dict[str, Any],
) -> dict[str, Any]:
    anchor_assets = brief_thesis_anchor.get("assets") or {}
    assets: dict[str, Any] = {}
    for asset, payload in sorted((logic_chains.get("assets") or {}).items()):
        if not isinstance(payload, dict):
            continue
        anchor = anchor_assets.get(asset)
        chain_maps = []
        inherited_count = 0
        new_count = 0
        conflict_count = 0
        pending_count = 0
        for chain in payload.get("logic_chains") or []:
            if not isinstance(chain, dict):
                continue
            classified = _classify_chain_against_anchor(chain, anchor)
            inherited_count += len(classified["inherited_signals"])
            new_count += len(classified["new_signals"])
            conflict_count += len(classified["conflict_signals"])
            pending_count += len(classified["pending_observations"])
            chain_maps.append(
                {
                    "chain_id": chain.get("chain_id"),
                    "asset": asset,
                    "dimension_id": chain.get("dimension_id"),
                    "dimension_label": chain.get("dimension_label"),
                    "dimension_score": chain.get("dimension_score"),
                    "benchmark_relation": classified["benchmark_relation"],
                    "inherited_brief_thesis": {
                        "anchor_id": (anchor or {}).get("anchor_id"),
                        "thesis_title": (anchor or {}).get("thesis_title"),
                        "direction": (anchor or {}).get("direction"),
                        "main_thesis": (anchor or {}).get("main_thesis"),
                    }
                    if anchor
                    else None,
                    "inherited_signals": classified["inherited_signals"],
                    "new_signals": classified["new_signals"],
                    "conflict_signals": classified["conflict_signals"],
                    "pending_observations": classified["pending_observations"],
                    "tracking_items": (anchor or {}).get("tracking_items") or [],
                    "quality_gate": "logic_chain_incremental_layer_only",
                }
            )
        if conflict_count:
            asset_relation = "has_conflicts_needing_review"
        elif inherited_count or new_count:
            asset_relation = "supports_or_extends_brief"
        elif pending_count:
            asset_relation = "pending_observation"
        else:
            asset_relation = "no_dynamic_signal"
        assets[asset] = {
            "asset": asset,
            "anchor_id": (anchor or {}).get("anchor_id"),
            "asset_relation": asset_relation,
            "stats": {
                "logic_chain_count": len(chain_maps),
                "inherited_signal_count": inherited_count,
                "new_signal_count": new_count,
                "conflict_signal_count": conflict_count,
                "pending_observation_count": pending_count,
            },
            "chain_maps": chain_maps,
        }
    return {
        "schema_version": "brief_logic_benchmark_map.v1",
        "status": "candidate",
        "report_date": brief_thesis_anchor.get("report_date") or logic_chains.get("report_date"),
        "generated_at": utc_now_iso(),
        "primary_display_contract": "market_brief_and_commodity_summary",
        "logic_chain_role": "incremental_signal_evidence_skeleton",
        "assets": assets,
    }


def _attach_brief_benchmark_map(
    logic_chains: dict[str, Any],
    benchmark_map: dict[str, Any],
) -> dict[str, Any]:
    benchmark_assets = benchmark_map.get("assets") or {}
    for asset, payload in (logic_chains.get("assets") or {}).items():
        if isinstance(payload, dict):
            payload["brief_logic_benchmark_map"] = benchmark_assets.get(asset)
    logic_chains["brief_logic_benchmark_map"] = {
        "schema_version": benchmark_map.get("schema_version"),
        "primary_display_contract": benchmark_map.get("primary_display_contract"),
        "logic_chain_role": benchmark_map.get("logic_chain_role"),
        "asset_count": len(benchmark_assets),
    }
    return logic_chains


def _dimension_direction_view(score: float) -> str:
    if score > 0.25:
        return "支撑"
    if score < -0.25:
        return "压制"
    return "跟踪"


def _dimension_analysis_row(row: dict[str, Any], rank: int, *, evidence_limit: int = 5) -> dict[str, Any]:
    score = float(row.get("direction_score") or 0)
    weight = float(row.get("effective_weight") or 0)
    return {
        "dimension_rank": rank,
        "dimension_id": row["dimension_id"],
        "dimension_label": row["dimension_label"],
        "direction": _dimension_direction_view(score),
        "direction_score": row["direction_score"],
        "importance_score": row["importance_score"],
        "effective_weight": row["effective_weight"],
        "effective_weight_pct": row.get("effective_weight_pct", round(weight * 100, 2)),
        "weighted_contribution": row.get("weighted_contribution", round(score * weight, 3)),
        "evidence_count": row.get("evidence_count"),
        "scored_evidence_count": row.get("scored_evidence_count"),
        "market_observation_count": row.get("market_observation_count"),
        "deduped_evidence_count": row.get("deduped_evidence_count"),
        "duplicate_count": row.get("duplicate_count"),
        "positive_evidence_weight": row.get("positive_evidence_weight"),
        "negative_evidence_weight": row.get("negative_evidence_weight"),
        "net_evidence_weight": row.get("net_evidence_weight"),
        "directional_consensus": row.get("directional_consensus"),
        "conflict_ratio": row.get("conflict_ratio"),
        "conflict_level": row.get("conflict_level"),
        "scoring_group_count": row.get("scoring_group_count"),
        "avg_confidence": row.get("avg_confidence"),
        "field_counts": row.get("field_counts") or {},
        "match_methods": row.get("match_methods") or {},
        "status_counts": row.get("status_counts") or {},
        "dimension_synthesis": row.get("dimension_synthesis"),
        "key_evidence": [
            _group_to_factor(group)
            for group in (row.get("evidence_groups") or [])[:evidence_limit]
        ],
    }


def build_trade_theses(
    summary: dict[str, Any],
    dimension_scores: dict[str, Any],
    *,
    use_llm_for_thesis: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for asset, payload in sorted((dimension_scores.get("assets") or {}).items()):
        rows = payload.get("dimensions") or []
        ranked = sorted(
            rows,
            key=lambda row: (abs(float(row.get("direction_score") or 0)), float(row.get("importance_score") or 0)),
            reverse=True,
        )
        primary = ranked[:3]
        supporting = [row for row in ranked if float(row.get("direction_score") or 0) > 0][:3]
        opposing = [row for row in ranked if float(row.get("direction_score") or 0) < 0][:3]
        score = float(payload.get("framework_score") or 0)
        divergence = payload.get("score_quality") or _score_divergence(payload.get("source_sentiment_score"), score)
        high_conflict_dimensions = [
            row.get("dimension_label")
            for row in ranked
            if row.get("conflict_level") in {"high", "medium"}
        ][:5]
        decision_score = _decision_score(score, divergence, len(high_conflict_dimensions))
        mainline_components = _mainline_components(decision_score, ranked)
        mainline_components["framework_score"] = round(score, 2)
        mainline_components["decision_score"] = round(decision_score, 2)
        mainline_components["score_diagnostics"] = {
            "score_divergence": divergence,
            "high_conflict_dimensions": high_conflict_dimensions,
            "framework_score_interpretation": _narrative_bias(score),
            "decision_score_interpretation": _narrative_bias(decision_score),
        }
        thesis_generation = (
            _llm_compose_main_trade_thesis(asset, decision_score, ranked, mainline_components)
            if use_llm_for_thesis
            else {
                "method": "rule_fallback",
                "main_trade_thesis": _compose_main_trade_thesis(asset, decision_score, mainline_components),
                "core_drivers": [_component_phrase(item) for item in (mainline_components.get("core_drivers") or [])],
                "constraints": [_component_phrase(item) for item in (mainline_components.get("constraints") or [])],
                "tracking_points": [_component_phrase(item) for item in (mainline_components.get("tracking") or [])],
                "quality_notes": [],
            }
        )
        result[asset] = {
            "asset": asset,
            "framework_score": round(score, 2),
            "decision_score": round(decision_score, 2),
            "source_sentiment_score": payload.get("source_sentiment_score"),
            "score_divergence": divergence,
            "score_diagnostics": mainline_components["score_diagnostics"],
            "recommendation": _recommendation(decision_score),
            "recommendation_basis": "decision_score",
            "main_trade_thesis": thesis_generation["main_trade_thesis"],
            "thesis_generation": thesis_generation,
            "mainline_components": mainline_components,
            "primary_dimensions": [
                _dimension_analysis_row(row, index + 1, evidence_limit=3)
                for index, row in enumerate(primary)
            ],
            "all_dimensions": [
                _dimension_analysis_row(row, index + 1, evidence_limit=5)
                for index, row in enumerate(ranked)
            ],
            "supporting_dimensions": [row["dimension_label"] for row in supporting],
            "opposing_dimensions": [row["dimension_label"] for row in opposing],
            "watch_points": _watch_points(primary),
            "invalidation_signals": [
                f"{row['dimension_label']}维度出现反向证据"
                for row in primary
                if row.get("dimension_label") != "未归类"
            ][:5],
        }
    return {
        "schema_version": "trade_thesis.v1",
        "status": "candidate",
        "report_date": summary.get("date"),
        "generated_at": utc_now_iso(),
        "weighting_method": WEIGHTING_METHOD,
        "thesis_generation_method": "llm" if use_llm_for_thesis else "rule_fallback",
        "assets": result,
    }


def _direction_for_score(score: float) -> str:
    if score > 0.25:
        return "bullish"
    if score < -0.25:
        return "bearish"
    return "neutral"


def _select_chain_trigger(grouped_evidence: list[dict[str, Any]], score: float, fallback: Any) -> str:
    target = _direction_for_score(score)
    if target != "neutral":
        for item in grouped_evidence:
            if item.get("direction") == target and item.get("scoring_role") != "market_observation":
                return str(item.get("text") or fallback or "")
    for item in grouped_evidence:
        if item.get("scoring_role") != "market_observation":
            return str(item.get("text") or fallback or "")
    return str(fallback or "")


def _counter_evidence(grouped_evidence: list[dict[str, Any]], score: float, *, limit: int = 3) -> list[dict[str, Any]]:
    target = _direction_for_score(score)
    opposite = "bearish" if target == "bullish" else "bullish" if target == "bearish" else ""
    if not opposite:
        return []
    return [
        item
        for item in grouped_evidence
        if item.get("direction") == opposite and item.get("scoring_role") != "market_observation"
    ][:limit]


def build_logic_chains(
    summary: dict[str, Any],
    alignment: dict[str, Any],
    dimension_scores: dict[str, Any],
    trade_theses: dict[str, Any],
) -> dict[str, Any]:
    chains_by_asset: dict[str, Any] = {}
    thesis_assets = trade_theses.get("assets") or {}
    scores_assets = dimension_scores.get("assets") or {}
    for asset, thesis in sorted(thesis_assets.items()):
        primary = thesis.get("primary_dimensions") or []
        chains = []
        for row in primary:
            dimension_id = row.get("dimension_id")
            grouped_evidence = row.get("key_evidence") or []
            score = float(row.get("direction_score") or 0)
            trigger = _select_chain_trigger(grouped_evidence, score, row.get("dimension_label"))
            effect = "support_price" if score > 0 else "pressure_price" if score < 0 else "uncertain"
            counter = _counter_evidence(grouped_evidence, score)
            chains.append(
                {
                    "chain_id": _hash_id("LCHAIN", str(summary.get("date")), asset, str(dimension_id), str(trigger)),
                    "asset": asset,
                    "dimension_id": dimension_id,
                    "dimension_label": row.get("dimension_label"),
                    "logic_chain": [
                        {"step": "trigger", "text": trigger},
                        {"step": "framework_dimension", "text": row.get("dimension_label")},
                        {"step": "dimension_synthesis", "text": row.get("dimension_synthesis") or ""},
                        {"step": "directional_effect", "text": effect},
                        {"step": "trade_thesis", "text": thesis.get("main_trade_thesis")},
                        {"step": "tracking_signal", "text": f"后续跟踪{row.get('dimension_label')}是否被新闻或数据库数据验证"},
                    ],
                    "evidence": grouped_evidence,
                    "counter_evidence": counter,
                    "conflict_level": row.get("conflict_level"),
                    "directional_consensus": row.get("directional_consensus"),
                    "deduped_evidence_count": row.get("deduped_evidence_count"),
                    "duplicate_count": row.get("duplicate_count"),
                    "dimension_score": row.get("direction_score"),
                    "importance_score": row.get("importance_score"),
                    "confidence": round(
                        sum(float(item.get("confidence") or 0) for item in grouped_evidence[:6]) / max(len(grouped_evidence[:6]), 1),
                        3,
                    )
                    if grouped_evidence
                    else 0.0,
                }
            )
        chains_by_asset[asset] = {
            "asset": asset,
            "framework_score": (scores_assets.get(asset) or {}).get("framework_score"),
            "main_trade_thesis": thesis.get("main_trade_thesis"),
            "dimension_overview": thesis.get("all_dimensions") or [],
            "logic_chains": chains,
        }
    return {
        "schema_version": "logic_chains.v1",
        "status": "candidate",
        "report_date": summary.get("date"),
        "generated_at": utc_now_iso(),
        "assets": chains_by_asset,
    }


def _asset_decision_score(thesis: dict[str, Any]) -> float:
    try:
        return float(thesis.get("decision_score", thesis.get("framework_score", 0)) or 0)
    except (TypeError, ValueError):
        return 0.0


def _summary_direction(score: float) -> str:
    if score > 0.8:
        return "偏多"
    if score < -0.8:
        return "偏空"
    return "中性"


def _summary_sentence(text: Any, *, limit: int = 150) -> str:
    value = re.sub(r"\s+", "", str(text or "").strip())
    value = value.strip("。；;，, ")
    if not value:
        return ""
    clauses = [item.strip("。；;，, ") for item in re.split(r"[；;。！？]", value) if item.strip("。；;，, ")]
    if not clauses:
        return _concise_fact(value, limit=limit)
    chosen: list[str] = []
    for clause in clauses:
        clause = re.sub(r"（[^）]{18,}）", "", clause).strip("，,、；; ")
        if not clause:
            continue
        proposal = "；".join([*chosen, clause]) if chosen else clause
        if len(proposal) <= limit:
            chosen.append(clause)
        if len(chosen) >= 2:
            break
    if chosen:
        return "；".join(chosen)
    return _concise_fact(clauses[0], limit=limit)


def _looks_like_summary_event(text: str) -> bool:
    return any(
        word in text
        for word in (
            "会议",
            "协议",
            "谅解备忘录",
            "霍尔木兹",
            "美联储",
            "加息",
            "降息",
            "关税",
            "政策",
            "制裁",
            "检修",
            "复产",
            "停产",
            "事故",
            "天气",
            "降雨",
            "出口",
            "进口",
            "到港",
            "发运",
            "库存",
            "开工",
            "排产",
        )
    )


def _asset_influence(summary: dict[str, Any], asset: str) -> float:
    details = (summary.get("detailed_analysis") or {}).get(asset) or {}
    scores = details.get("influence_scores") or []
    values = []
    if isinstance(scores, list):
        for score in scores:
            try:
                values.append(max(0.0, min(1.0, float(score))))
            except (TypeError, ValueError):
                continue
    if values:
        return max(values)
    return 0.35 if _source_count(summary, asset) else 0.15


def _cluster_labels_by_factor(alignment: dict[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for cluster in alignment.get("factor_clusters") or []:
        if not isinstance(cluster, dict):
            continue
        label = str(cluster.get("label") or "").strip()
        if not label or label == "未归类":
            continue
        for factor_id in cluster.get("factors") or []:
            labels[str(factor_id)] = label
    return labels


def _topic_label_for_link(link: dict[str, Any], cluster_labels: dict[str, str]) -> str:
    text = str(link.get("text") or "")
    if any(word in text for word in ("美伊", "伊朗", "以色列", "霍尔木兹", "中东", "地缘", "停火", "战争", "俄乌")):
        return "地缘政治"
    if any(word in text for word in ("美联储", "FOMC", "点阵图", "加息", "降息", "利率", "美元指数", "美债")):
        return "美联储与利率"
    if any(word in text for word in ("关税", "贸易", "出口限制", "进口限制", "中加", "加拿大", "豁免", "制裁")):
        return "关税与贸易"
    if any(word in text for word in ("生物柴油", "生柴", "B50", "B40", "B35", "B15")):
        return "生柴政策与生物燃料"
    factor_id = str(link.get("factor_id") or "")
    cluster_label = cluster_labels.get(factor_id)
    if cluster_label:
        return cluster_label
    node = link.get("framework_node") or {}
    label = _clean_label(str(node.get("label") or ""))
    return label if label != "未归类" else "未归类"


def _summary_event_items(
    summary: dict[str, Any],
    alignment: dict[str, Any],
    trade_theses: dict[str, Any],
    *,
    limit: int = 36,
) -> list[dict[str, Any]]:
    thesis_assets = trade_theses.get("assets") or {}
    cluster_labels = _cluster_labels_by_factor(alignment)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for link in alignment.get("alignments") or []:
        if not isinstance(link, dict) or _is_invalid_evidence_link(link) or _is_market_observation_link(link):
            continue
        asset = str(link.get("asset") or "")
        if asset not in thesis_assets:
            continue
        text = _summary_sentence(link.get("text"), limit=150)
        if not text:
            continue
        topic_label = _topic_label_for_link(link, cluster_labels)
        key = (topic_label, _canonical_text(text)[:90])
        if key in seen:
            continue
        seen.add(key)
        source_field = str(link.get("source_field") or "")
        confidence = _confidence(link)
        score = _asset_decision_score(thesis_assets.get(asset) or {})
        influence = _asset_influence(summary, asset)
        field_boost = {
            "key_events": 2.0,
            "supply_demand": 1.45,
            "bullish_factors": 1.25,
            "bearish_factors": 1.25,
            "key_data": 0.95,
            "price_forecast": 0.35,
        }.get(source_field, 0.75)
        event_boost = 0.75 if _looks_like_summary_event(text) else 0.0
        unclassified_penalty = -0.8 if topic_label == "未归类" else 0.0
        priority = field_boost + event_boost + confidence + influence + min(abs(score), 8) / 10 + unclassified_penalty
        node = link.get("framework_node") or {}
        sign = _link_sign(link)
        rows.append(
            {
                "asset": asset,
                "topic_label": topic_label,
                "dimension_label": _clean_label(str(node.get("label") or "")),
                "direction": _direction_for_score(sign),
                "direction_score": round(sign, 3),
                "source_field": source_field,
                "text": text,
                "confidence": round(confidence, 3),
                "asset_influence": round(influence, 3),
                "asset_decision_score": round(score, 2),
                "priority": round(priority, 3),
            }
        )
    rows.sort(key=lambda item: item["priority"], reverse=True)
    return rows[:limit]


def _summary_logic_items(trade_theses: dict[str, Any], *, limit: int = 12) -> list[dict[str, Any]]:
    rows = []
    for asset, thesis in (trade_theses.get("assets") or {}).items():
        score = _asset_decision_score(thesis)
        if not thesis.get("main_trade_thesis"):
            continue
        generation = thesis.get("thesis_generation") or {}
        drivers = generation.get("core_drivers")
        constraints = generation.get("constraints")
        rows.append(
            {
                "asset": asset,
                "decision_score": round(score, 2),
                "framework_score": thesis.get("framework_score"),
                "recommendation": thesis.get("recommendation"),
                "direction": _summary_direction(score),
                "main_trade_thesis": _compact_text(thesis.get("main_trade_thesis"), limit=220),
                "core_drivers": drivers if isinstance(drivers, list) else [],
                "constraints": constraints if isinstance(constraints, list) else [],
                "watch_points": thesis.get("watch_points") if isinstance(thesis.get("watch_points"), list) else [],
                "score_divergence": thesis.get("score_divergence") or {},
                "primary_dimensions": [
                    row.get("dimension_label")
                    for row in (thesis.get("primary_dimensions") or [])[:3]
                    if isinstance(row, dict) and row.get("dimension_label")
                ],
            }
        )
    rows.sort(key=lambda item: abs(float(item.get("decision_score") or 0)), reverse=True)
    return rows[:limit]


def _topic_summaries(event_items: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for item in event_items:
        label = str(item.get("topic_label") or item.get("dimension_label") or "未归类")
        if label == "未归类":
            continue
        bucket = buckets.setdefault(
            label,
            {
                "topic_label": label,
                "assets": Counter(),
                "positive_assets": Counter(),
                "negative_assets": Counter(),
                "evidence": [],
                "net_direction": 0.0,
                "priority": 0.0,
            },
        )
        asset = str(item.get("asset") or "")
        sign = float(item.get("direction_score") or 0)
        priority = float(item.get("priority") or 0)
        if asset:
            bucket["assets"][asset] += 1
            if sign > 0:
                bucket["positive_assets"][asset] += 1
            elif sign < 0:
                bucket["negative_assets"][asset] += 1
        bucket["net_direction"] += sign * max(priority, 0.1)
        bucket["priority"] += priority
        bucket["evidence"].append(item)

    rows = []
    for bucket in buckets.values():
        evidence = sorted(bucket["evidence"], key=lambda item: float(item.get("priority") or 0), reverse=True)
        assets = [asset for asset, _ in bucket["assets"].most_common(6)]
        net = float(bucket["net_direction"])
        direction = "偏多" if net > 0.8 else "偏空" if net < -0.8 else "分化"
        rows.append(
            {
                "topic_label": bucket["topic_label"],
                "direction": direction,
                "net_direction": round(net, 2),
                "asset_count": len(bucket["assets"]),
                "assets": assets,
                "positive_assets": [asset for asset, _ in bucket["positive_assets"].most_common(5)],
                "negative_assets": [asset for asset, _ in bucket["negative_assets"].most_common(5)],
                "top_evidence": [
                    {
                        "asset": item.get("asset"),
                        "direction": item.get("direction"),
                        "text": item.get("text"),
                        "source_field": item.get("source_field"),
                    }
                    for item in evidence[:4]
                ],
                "priority": round(float(bucket["priority"]) + len(bucket["assets"]) * 0.35 + abs(net) * 0.2, 3),
            }
        )
    rows.sort(key=lambda item: item["priority"], reverse=True)
    return rows[:limit]


def _topic_summary_sentence(topic: dict[str, Any]) -> str:
    label = str(topic.get("topic_label") or "相关主题")
    evidence = topic.get("top_evidence") or []
    text = _summary_sentence((evidence[0] or {}).get("text") if evidence else "", limit=96)
    assets = "、".join((topic.get("assets") or [])[:4])
    direction = topic.get("direction") or "分化"
    if not text:
        text = f"{assets}相关证据集中出现" if assets else "相关证据集中出现"
    impact = f"{direction}影响主要落在{assets}" if assets else f"整体呈{direction}影响"
    return f"{label}：{text}，{impact}"


def _logic_asset_phrase(item: dict[str, Any]) -> str:
    dims = [str(value) for value in (item.get("primary_dimensions") or []) if value]
    dim_text = f"，主因{ '、'.join(dims[:2]) }" if dims else ""
    return f"{item['asset']}({item['decision_score']:+.1f}{dim_text})"


def _fallback_logic_summary(
    event_items: list[dict[str, Any]],
    logic_items: list[dict[str, Any]],
    topic_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    if topic_summaries:
        important_events = "；".join(_topic_summary_sentence(topic) for topic in topic_summaries[:5]) + "。"
    elif event_items:
        important_events = "；".join(f"{item['asset']}：{item['text']}" for item in event_items[:5]) + "。"
    else:
        important_events = "当前逻辑链产物中缺少可用于总结的重要事件证据。"

    bullish = [item for item in logic_items if item.get("direction") == "偏多"][:5]
    bearish = [item for item in logic_items if item.get("direction") == "偏空"][:5]
    neutral = [item for item in logic_items if item.get("direction") == "中性"][:3]
    bullish_topics = [topic["topic_label"] for topic in topic_summaries if topic.get("direction") == "偏多"][:3]
    bearish_topics = [topic["topic_label"] for topic in topic_summaries if topic.get("direction") == "偏空"][:3]
    parts = []
    if bearish:
        topic_text = f"，共性压力来自{'、'.join(bearish_topics)}" if bearish_topics else ""
        parts.append("偏空主线更集中在" + "、".join(_logic_asset_phrase(item) for item in bearish) + topic_text)
    if bullish:
        topic_text = f"，共性支撑来自{'、'.join(bullish_topics)}" if bullish_topics else ""
        parts.append("偏多主线主要在" + "、".join(_logic_asset_phrase(item) for item in bullish) + topic_text)
    if neutral:
        parts.append("中性/待验证品种包括" + "、".join(_logic_asset_phrase(item) for item in neutral) + "，需要等待框架维度继续被研报或数据验证")
    important_logic = "；".join(parts) + "。" if parts else "当前尚未形成清晰的品种主线分布。"
    return {
        "important_events_summary": important_events,
        "important_logic_summary": important_logic,
        "quality_notes": ["规则摘要，已先按框架维度和跨品种主题聚合，再生成全日逻辑梳理。"],
    }


def _llm_logic_summary(
    summary: dict[str, Any],
    event_items: list[dict[str, Any]],
    logic_items: list[dict[str, Any]],
    topic_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    fallback = _fallback_logic_summary(event_items, logic_items, topic_summaries)
    payload = {
        "date": summary.get("date"),
        "topic_summaries": topic_summaries[:8],
        "event_items": event_items[:18],
        "logic_items": logic_items[:12],
    }
    prompt = (
        "你是商品期货研究主管，请基于“期市逻辑链 Agent”的结构化产物，生成逻辑链页面专用的"
        "重要事件总结和重要逻辑梳理。\n"
        "注意：这是期市逻辑链页面，不是期市速递。不要引用、复述或模拟 market_review / 期市速递摘要；"
        "只能使用输入的 topic_summaries、event_items 和 logic_items。\n"
        "严格输出 JSON：\n"
        '{"important_events_summary":"按事件/变量变化总结，突出资产、维度、方向和待验证点",'
        '"important_logic_summary":"按交易主线总结多空分布、核心驱动、冲突和后续跟踪",'
        '"quality_notes":["证据缺口或冲突说明"]}\n\n'
        "写作要求：\n"
        "1. 按主题归纳，不要逐条列文件或路径；优先写供需、库存、政策、地缘、宏观、装置、天气、进出口等变量。\n"
        "2. 重要逻辑梳理必须体现“信息提取 -> 映射框架 -> 跨品种汇总”的结果：哪些主题是共性压力/支撑，落在哪些品种。\n"
        "3. 不要让行情涨跌、技术位、盘面情绪成为基本面主线依据；这类内容只可作为风险偏好或跟踪项。\n"
        "4. 两段都要通顺，每段 220-520 个中文字符；保留必要数字，但不要重复同一数据。\n"
        "5. 如果多空证据冲突，要明确写成“主线降级/等待验证/反向约束”。\n\n"
        "结构化产物：\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        parsed = _parse_json_object(chat(prompt, max_tokens=1800, temperature=0.25, timeout=120))
        events = _clean_main_thesis_text(str(parsed.get("important_events_summary") or ""))
        logic = _clean_main_thesis_text(str(parsed.get("important_logic_summary") or ""))
        if not events or not logic:
            raise ValueError("LLM logic summary missing required fields")
        return {
            "important_events_summary": events,
            "important_logic_summary": logic,
            "quality_notes": parsed.get("quality_notes") if isinstance(parsed.get("quality_notes"), list) else [],
        }
    except Exception as exc:
        return {
            **fallback,
            "quality_notes": [*fallback["quality_notes"], f"LLM逻辑摘要生成失败，已使用规则兜底：{exc}"],
        }


def build_logic_summary(
    summary: dict[str, Any],
    alignment: dict[str, Any],
    dimension_scores: dict[str, Any],
    trade_theses: dict[str, Any],
    logic_chains: dict[str, Any],
    *,
    use_llm: bool = False,
) -> dict[str, Any]:
    event_items = _summary_event_items(summary, alignment, trade_theses)
    logic_items = _summary_logic_items(trade_theses)
    topic_summaries = _topic_summaries(event_items)
    synthesis = (
        _llm_logic_summary(summary, event_items, logic_items, topic_summaries)
        if use_llm
        else _fallback_logic_summary(event_items, logic_items, topic_summaries)
    )
    return {
        "schema_version": "futures_daily_logic_summary.v1",
        "status": "candidate",
        "report_date": summary.get("date"),
        "generated_at": utc_now_iso(),
        "source": "logic_chain_agent",
        "separated_from_market_review": True,
        "source_files": {
            "framework_alignment": "framework_alignment.json",
            "dimension_scores": "dimension_scores.json",
            "trade_thesis": "trade_thesis.json",
            "logic_chains": "logic_chains.json",
        },
        "stats": {
            "asset_count": len((trade_theses.get("assets") or {})),
            "dimension_asset_count": len((dimension_scores.get("assets") or {})),
            "logic_chain_count": sum(
                len((payload or {}).get("logic_chains") or [])
                for payload in (logic_chains.get("assets") or {}).values()
                if isinstance(payload, dict)
            ),
            "event_item_count": len(event_items),
            "logic_item_count": len(logic_items),
            "topic_summary_count": len(topic_summaries),
        },
        "important_events_summary": synthesis["important_events_summary"],
        "important_logic_summary": synthesis["important_logic_summary"],
        "topic_summaries": topic_summaries,
        "event_items": event_items,
        "logic_items": logic_items,
        "quality_notes": synthesis.get("quality_notes") or [],
    }


def _candidate_type_for_link(link: dict[str, Any]) -> str:
    label = _clean_label(str((link.get("framework_node") or {}).get("label") or ""))
    field = str(link.get("source_field") or "")
    if label == "未归类":
        return "new_dimension"
    if field in {"key_data", "key_events", "supply_demand"}:
        return "new_indicator"
    return "dimension_update"


def _proposal_payload(asset: str, links: list[dict[str, Any]]) -> dict[str, Any]:
    terms = Counter()
    for link in links:
        for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_+-]{1,}", str(link.get("text") or "")):
            if len(token) >= 2:
                terms[token] += 1
    sample_texts = [str(link.get("text") or "") for link in links[:8]]
    labels = Counter(_clean_label(str((link.get("framework_node") or {}).get("label") or "")) for link in links)
    suggested_label = labels.most_common(1)[0][0] if labels else "待识别维度"
    if suggested_label == "未归类":
        suggested_label = "待识别逻辑维度"
    return {
        "asset": asset,
        "suggested_dimension_name": suggested_label,
        "suggested_keywords": [term for term, _ in terms.most_common(12)],
        "evidence_samples": sample_texts,
        "suggested_action": "补充典型指标/事件或新增逻辑模板，人工审核后再写入 active framework。",
    }


def build_framework_update_candidates(summary: dict[str, Any], alignment: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    frameworks = alignment.get("frameworks_used") or {}
    for link in alignment.get("alignments") or []:
        if not isinstance(link, dict):
            continue
        method = str((link.get("match") or {}).get("method") or "")
        label = _clean_label(str((link.get("framework_node") or {}).get("label") or ""))
        confidence = _confidence(link)
        if label != "未归类" and method.startswith("framework") and confidence >= 0.65:
            continue
        asset = str(link.get("asset") or "")
        candidate_type = _candidate_type_for_link(link)
        grouped[(asset, candidate_type)].append(link)

    candidates = []
    for (asset, candidate_type), links in sorted(grouped.items()):
        if not asset or len(links) < 2:
            continue
        framework = frameworks.get(asset) or {}
        avg_confidence = sum(_confidence(link) for link in links) / max(len(links), 1)
        payload = _proposal_payload(asset, links)
        candidates.append(
            {
                "schema_version": "research_framework_update_candidate.v1",
                "artifact_type": "research_framework_update_candidate",
                "candidate_id": _hash_id("FWKUPD", str(summary.get("date")), asset, candidate_type, payload["suggested_dimension_name"]),
                "target_framework_id": framework.get("framework_id") or "",
                "asset_id": framework.get("asset_id") or "",
                "standard_name": asset,
                "candidate_type": candidate_type,
                "proposal_summary": f"{asset}存在{len(links)}条低置信或未归类证据，建议补充{payload['suggested_dimension_name']}相关框架内容。",
                "proposed_payload": payload,
                "source_report_id": str(summary.get("source_report_hash") or ""),
                "source_extraction_id": str(summary.get("analysis_date") or ""),
                "evidence_quotes": [str(link.get("text") or "") for link in links[:6]],
                "document_recall_refs": [],
                "confidence": round(min(0.9, 0.25 + len(links) * 0.04 + avg_confidence * 0.35), 3),
                "status": "candidate",
                "review_status": "pending_review",
                "created_at": utc_now_iso(),
            }
        )

    return {
        "schema_version": "framework_update_candidates.v1",
        "status": "candidate",
        "report_date": summary.get("date"),
        "generated_at": utc_now_iso(),
        "candidates": candidates,
    }


def build_framework_format_review(alignment: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "framework_format_review.v1",
        "status": "candidate",
        "generated_at": utc_now_iso(),
        "frameworks_reviewed": alignment.get("frameworks_used") or {},
        "findings": FRAMEWORK_REVIEW_FINDINGS,
        "recommended_dimension_extensions": {
            "default_weight": "长期默认权重，用于没有近期证据时的维度基准重要性。",
            "activation_weight": "由近期研报/新闻/数据触发的短期权重，不直接写回长期框架。",
            "scoring_rules": "声明指标 polarity、强度、置信度、时间衰减和来源权重。",
            "logic_templates": "把维度之间的 A -> B -> C 因果链模板沉淀下来。",
            "evolution_policy": "定义候选如何进入人工审核、何时写回 active framework。",
        },
    }


def build_logic_chain_bundle(
    summary: dict[str, Any],
    alignment: dict[str, Any],
    *,
    use_llm_for_thesis: bool = False,
    use_llm_for_summary: bool | None = None,
) -> dict[str, Any]:
    dimension_scores = build_dimension_scores(summary, alignment)
    trade_theses = build_trade_theses(summary, dimension_scores, use_llm_for_thesis=use_llm_for_thesis)
    logic_chains = build_logic_chains(summary, alignment, dimension_scores, trade_theses)
    brief_thesis_anchor = build_brief_thesis_anchor(summary)
    brief_logic_benchmark_map = build_brief_logic_benchmark_map(brief_thesis_anchor, logic_chains)
    logic_chains = _attach_brief_benchmark_map(logic_chains, brief_logic_benchmark_map)
    summary_llm = use_llm_for_thesis if use_llm_for_summary is None else use_llm_for_summary
    logic_summary = build_logic_summary(
        summary,
        alignment,
        dimension_scores,
        trade_theses,
        logic_chains,
        use_llm=summary_llm,
    )
    update_candidates = build_framework_update_candidates(summary, alignment)
    format_review = build_framework_format_review(alignment)
    return {
        "dimension_scores": dimension_scores,
        "trade_thesis": trade_theses,
        "logic_chains": logic_chains,
        "brief_thesis_anchor": brief_thesis_anchor,
        "brief_logic_benchmark_map": brief_logic_benchmark_map,
        "logic_summary": logic_summary,
        "framework_update_candidates": update_candidates,
        "framework_format_review": format_review,
    }


def publish_logic_chain_bundle(
    summary_path: str | Path,
    alignment_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    use_llm_for_thesis: bool = True,
    use_llm_for_summary: bool | None = None,
) -> dict[str, str]:
    summary_file = Path(summary_path).expanduser()
    alignment_file = Path(alignment_path).expanduser()
    target_dir = Path(output_dir).expanduser() if output_dir else summary_file.parent
    summary = read_json(summary_file)
    alignment = read_json(alignment_file)
    bundle = build_logic_chain_bundle(
        summary,
        alignment,
        use_llm_for_thesis=use_llm_for_thesis,
        use_llm_for_summary=use_llm_for_summary,
    )
    paths = {
        "dimension_scores": target_dir / "dimension_scores.json",
        "trade_thesis": target_dir / "trade_thesis.json",
        "logic_chains": target_dir / "logic_chains.json",
        "brief_thesis_anchor": target_dir / "brief_thesis_anchor.json",
        "brief_logic_benchmark_map": target_dir / "brief_logic_benchmark_map.json",
        "logic_summary": target_dir / "logic_summary.json",
        "framework_update_candidates": target_dir / "framework_update_candidates.json",
        "framework_format_review": target_dir / "framework_format_review.json",
    }
    for key, path in paths.items():
        write_json(path, bundle[key])
    return {key: str(path) for key, path in paths.items()}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build futures daily logic chains from summary and framework alignment.")
    parser.add_argument("--summary", required=True, help="Path to *_commodity_summary.json")
    parser.add_argument("--alignment", required=True, help="Path to framework_alignment.json")
    parser.add_argument("--output-dir", help="Output directory. Defaults to summary parent.")
    parser.add_argument("--no-llm-thesis", action="store_true", help="Use rule fallback instead of LLM for main trade thesis.")
    parser.add_argument("--llm-summary", action="store_true", help="Use LLM only for the top-level important events / logic summary.")
    args = parser.parse_args(argv)
    paths = publish_logic_chain_bundle(
        args.summary,
        args.alignment,
        args.output_dir,
        use_llm_for_thesis=not args.no_llm_thesis,
        use_llm_for_summary=args.llm_summary or None,
    )
    print("逻辑链候选已生成：")
    for role, path in paths.items():
        print(f"{role} → {path}")


if __name__ == "__main__":
    main()
