from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from quanta_agents.core.frameworks import (
    iter_leaf_nodes,
    leaf_event_terms,
    leaf_indicator_terms,
    load_framework_for_asset,
)
from quanta_agents.core.io import utc_now_iso
from quanta_agents.core.taxonomy import load_asset_taxonomy
from quanta_agents.futures_daily.framework_alignment import build_framework_alignment


FACTOR_FIELDS = (
    "bullish_factors",
    "bearish_factors",
    "key_data",
    "key_events",
    "supply_demand",
    "price_forecast",
)

FIELD_WEIGHTS = {
    "bullish_factors": 1.0,
    "bearish_factors": 1.0,
    "supply_demand": 0.9,
    "key_events": 0.8,
    "key_data": 0.55,
    "price_forecast": 0.35,
}

TRACKING_DIMENSION_WORDS = (
    "价格与交易",
    "近远月",
    "持仓",
    "技术",
    "主力",
    "期现",
    "基差",
    "升贴水",
    "月差",
    "跨期",
)

BULLISH_WORDS = ("支撑", "利多", "偏强", "改善", "去库", "下降", "减少", "收缩", "检修", "减产", "修复", "回升")
BEARISH_WORDS = ("压制", "利空", "偏弱", "累库", "增加", "回落", "过剩", "走弱", "疲软", "复产", "增产", "宽松")
MARKET_WORDS = (
    "主力合约",
    "夜盘",
    "日盘",
    "收盘",
    "收涨",
    "收跌",
    "涨幅",
    "跌幅",
    "盘面",
    "技术位",
    "成交",
    "持仓",
    "期价",
    "现货价",
    "现货下跌",
    "价格下跌",
    "价格回落",
    "大幅走弱",
    "下跌",
    "上涨",
    "跌至",
    "涨至",
    "报价",
    "报盘",
    "现货指数",
    "港口现货指数",
    "指数明显",
    "基差",
    "升水",
    "贴水",
    "升贴水",
    "月差",
    "跨期",
    "期现",
)
FUNDAMENTAL_WORDS = (
    "库存",
    "开工",
    "产量",
    "产能",
    "进口",
    "出口",
    "到港",
    "发运",
    "利润",
    "成本",
    "需求",
    "消费",
    "政策",
    "天气",
)


def _hash_id(prefix: str, *parts: Any) -> str:
    raw = "||".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16].upper()}"


def _canonical_text(text: Any) -> str:
    raw = re.sub(r"\s+", "", str(text or "").strip())
    raw = re.sub(r"[，。；;、：:（）()【】\[\]{}<>《》\"'“”‘’]", "", raw)
    return raw.lower()


def _tokens(text: Any) -> set[str]:
    return set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_+-]{1,}|[+-]?\d+(?:\.\d+)?%?", str(text or "")))


def _bounded_float(value: Any, *, lower: float, upper: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))


def _direction_sign(direction: Any, text: Any = "") -> float:
    raw = str(direction or "").strip()
    if raw == "bullish":
        return 1.0
    if raw == "bearish":
        return -1.0
    value = str(text or "")
    bullish = sum(1 for word in BULLISH_WORDS if word in value)
    bearish = sum(1 for word in BEARISH_WORDS if word in value)
    if bullish == bearish:
        return 0.0
    return 0.55 if bullish > bearish else -0.55


def _is_market_only(text: Any, source_field: Any) -> bool:
    value = str(text or "")
    field = str(source_field or "")
    has_market = any(word in value for word in MARKET_WORDS)
    has_fundamental = any(word in value for word in FUNDAMENTAL_WORDS)
    return field == "price_forecast" or (has_market and not has_fundamental)


def _first_sentence(text: Any, *, limit: int = 120) -> str:
    value = re.sub(r"\s+", "", str(text or "").strip()).strip("。；;，, ")
    if not value:
        return ""
    clauses = [item.strip("。；;，, ") for item in re.split(r"[。；;！？]", value) if item.strip("。；;，, ")]
    if clauses:
        value = clauses[0]
    if len(value) <= limit:
        return value
    parts = [item.strip("，,、 ") for item in re.split(r"[，,、]", value) if item.strip("，,、 ")]
    chosen: list[str] = []
    for part in parts:
        proposal = "，".join([*chosen, part]) if chosen else part
        if len(proposal) <= limit:
            chosen.append(part)
    return "，".join(chosen) if chosen else value[:limit].rstrip("，,、 ")


def _best_term_match(text: str, leaf: dict[str, Any] | None, source_field: str) -> dict[str, Any]:
    if not leaf:
        return {"term_type": "", "name": "", "confidence": 0.0, "method": "no_leaf"}
    candidates: list[tuple[str, str]] = []
    for term in leaf_indicator_terms(leaf):
        candidates.append(("indicator", term))
    for term in leaf_event_terms(leaf):
        candidates.append(("event", term))
    if not candidates:
        return {"term_type": "", "name": "", "confidence": 0.0, "method": "no_terms"}

    text_tokens = _tokens(text)
    best = ("", "", 0.0, "term_overlap")
    for term_type, term in candidates:
        name = str(term or "").strip()
        if not name:
            continue
        term_tokens = _tokens(name)
        score = 0.0
        if name in text:
            score = 0.92
        elif term_tokens and text_tokens:
            score = len(term_tokens & text_tokens) / max(min(len(term_tokens), len(text_tokens)), 1)
            score = min(0.82, score)
        if source_field == "key_events" and term_type == "event":
            score += 0.06
        if source_field == "key_data" and term_type == "indicator":
            score += 0.05
        if score > best[2]:
            best = (term_type, name, score, "term_exact" if name in text else "term_overlap")
    if best[2] < 0.18:
        return {"term_type": "", "name": "", "confidence": 0.0, "method": "weak_term_match"}
    return {"term_type": best[0], "name": best[1], "confidence": round(min(best[2], 0.96), 3), "method": best[3]}


def _report_identity(report: dict[str, Any], report_path: str) -> dict[str, Any]:
    metadata = report.get("_metadata") if isinstance(report.get("_metadata"), dict) else {}
    return {
        "report_id": str(metadata.get("row_id") or report.get("source_report_hash") or Path(report_path).stem),
        "report_path": report_path,
        "org_name": report.get("org_name"),
        "title": report.get("title"),
        "report_hash": report.get("source_report_hash"),
    }


def _leaf_cache(root: str | Path | None, assets: set[str]) -> dict[str, dict[str, dict[str, Any]]]:
    taxonomy = load_asset_taxonomy(root, required=True)
    cache: dict[str, dict[str, dict[str, Any]]] = {}
    for asset_name in assets:
        asset = next(
            (item for item in taxonomy.assets if item.get("canonical_name") == asset_name),
            {"canonical_name": asset_name},
        )
        framework = load_framework_for_asset(asset, root)
        leaves = iter_leaf_nodes(framework) if framework else []
        cache[asset_name] = {str(leaf.get("node_id") or ""): leaf for leaf in leaves if leaf.get("node_id")}
    return cache


def build_evidence_nodes_from_reports(
    report_results: list[tuple[dict[str, Any], Path | str]],
    root: str | Path | None = None,
    *,
    use_llm_refinement: bool = False,
) -> dict[str, Any]:
    assets: set[str] = set()
    alignments: list[dict[str, Any]] = []
    detail_lookup: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    for report, report_path in report_results:
        if not isinstance(report, dict):
            continue
        details = report.get("detailed_analysis") if isinstance(report.get("detailed_analysis"), dict) else {}
        report_assets = [str(asset) for asset, payload in details.items() if isinstance(payload, dict)]
        if not report_assets:
            continue
        report_summary = {
            "date": report.get("date"),
            "title": report.get("title"),
            "analysis_date": report.get("analysis_date"),
            "source_report_hash": report.get("source_report_hash"),
            "detailed_analysis": {
                asset: details[asset]
                for asset in report_assets
                if isinstance(details.get(asset), dict)
            },
        }
        report_alignment = build_framework_alignment(report_summary, root, use_llm_refinement=use_llm_refinement)
        identity = _report_identity(report, str(report_path))
        for link in report_alignment.get("alignments") or []:
            if not isinstance(link, dict):
                continue
            asset = str(link.get("asset") or "")
            source_field = str(link.get("source_field") or "")
            text = str(link.get("text") or "")
            factor_key = (identity["report_id"], asset, source_field, _canonical_text(text))
            detail = details.get(asset) if isinstance(details.get(asset), dict) else {}
            detail_lookup[factor_key] = detail
            enriched = dict(link)
            enriched["report"] = identity
            enriched["factor_id"] = _hash_id("RFAC", identity["report_id"], asset, source_field, text)
            alignments.append(enriched)
            if asset:
                assets.add(asset)

    leaves_by_asset = _leaf_cache(root, assets)
    evidence_nodes = []
    for link in alignments:
        report = link.get("report") or {}
        asset = str(link.get("asset") or "")
        source_field = str(link.get("source_field") or "")
        text = str(link.get("text") or "")
        report_id = str(report.get("report_id") or "")
        detail = detail_lookup.get((report_id, asset, source_field, _canonical_text(text))) or {}
        node = link.get("framework_node") or {}
        node_id = str(node.get("node_id") or "")
        match = link.get("match") or {}
        confidence = _bounded_float(match.get("confidence"), lower=0.0, upper=1.0, default=0.2)
        influence = _bounded_float(detail.get("influence_score"), lower=0.0, upper=1.0, default=0.4)
        report_score = _bounded_float(detail.get("sentiment_score"), lower=-10.0, upper=10.0)
        direction = str(link.get("llm_impact_direction") or link.get("direction") or "")
        sign = 0.0 if _is_market_only(text, source_field) else _direction_sign(direction, text)
        term_match = _best_term_match(text, leaves_by_asset.get(asset, {}).get(node_id), source_field)
        evidence_weight = (
            max(confidence, 0.25)
            * max(influence, 0.05)
            * FIELD_WEIGHTS.get(source_field, 0.6)
            * (0.65 + min(abs(report_score), 10.0) / 20.0)
        )
        evidence_nodes.append(
            {
                "evidence_id": _hash_id("EVID", report_id, asset, source_field, text),
                "report_id": report_id,
                "report_path": report.get("report_path"),
                "org_name": report.get("org_name"),
                "title": report.get("title"),
                "asset": asset,
                "source_field": source_field,
                "text": text,
                "canonical_text": _canonical_text(text),
                "report_sentiment_score": round(report_score, 3),
                "influence_score": round(influence, 3),
                "framework_node": node,
                "framework": link.get("framework") or {},
                "mapping": {
                    "method": match.get("method"),
                    "confidence": round(confidence, 3),
                    "status": link.get("status"),
                },
                "term_match": term_match,
                "impact_direction": direction or "neutral",
                "impact_sign": round(sign, 3),
                "evidence_weight": round(evidence_weight, 4),
                "scoring_role": "market_observation" if sign == 0.0 and _is_market_only(text, source_field) else "framework_evidence",
            }
        )

    return {
        "schema_version": "evidence_nodes.v1",
        "status": "candidate",
        "generated_at": utc_now_iso(),
        "mapping_method": "per_report_framework_alignment",
        "stats": {
            "report_count": len(report_results),
            "asset_count": len(assets),
            "evidence_count": len(evidence_nodes),
        },
        "evidence_nodes": evidence_nodes,
    }


def _evidence_sort_key(item: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(item.get("evidence_weight") or 0),
        float(item.get("influence_score") or 0),
        float((item.get("mapping") or {}).get("confidence") or 0),
    )


def _merge_evidence_texts(nodes: list[dict[str, Any]], *, sign: float | None, limit: int = 4) -> list[dict[str, Any]]:
    rows = []
    seen: set[str] = set()
    candidates = nodes
    if sign is not None:
        candidates = [node for node in nodes if float(node.get("impact_sign") or 0) * sign > 0]
    for node in sorted(candidates, key=_evidence_sort_key, reverse=True):
        key = str(node.get("canonical_text") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "text": _first_sentence(node.get("text"), limit=150),
                "report_id": node.get("report_id"),
                "org_name": node.get("org_name"),
                "source_field": node.get("source_field"),
                "influence_score": node.get("influence_score"),
                "mapping_confidence": (node.get("mapping") or {}).get("confidence"),
                "term_match": node.get("term_match") or {},
            }
        )
        if len(rows) >= limit:
            break
    return rows


def build_asset_dimension_summaries(evidence_nodes_payload: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for node in evidence_nodes_payload.get("evidence_nodes") or []:
        if not isinstance(node, dict):
            continue
        framework_node = node.get("framework_node") or {}
        dimension_id = str(framework_node.get("node_id") or "default::未归类")
        grouped[(str(node.get("asset") or ""), dimension_id)].append(node)

    rows = []
    for (asset, dimension_id), nodes in sorted(grouped.items()):
        if not asset:
            continue
        label = str(((nodes[0].get("framework_node") or {}).get("label")) or "未归类")
        is_tracking_dimension = any(word in label for word in TRACKING_DIMENSION_WORDS)
        is_unclassified = label == "未归类" or dimension_id == "default::未归类"
        source_reports = sorted({str(node.get("report_id") or "") for node in nodes if node.get("report_id")})
        positive_weight = sum(float(node.get("evidence_weight") or 0) for node in nodes if float(node.get("impact_sign") or 0) > 0)
        negative_weight = sum(float(node.get("evidence_weight") or 0) for node in nodes if float(node.get("impact_sign") or 0) < 0)
        neutral_count = sum(1 for node in nodes if float(node.get("impact_sign") or 0) == 0)
        total_directional = positive_weight + negative_weight
        net = positive_weight - negative_weight
        direction_score = 0.0 if total_directional <= 0 else 10.0 * net / total_directional
        conflict_ratio = 0.0 if total_directional <= 0 else min(positive_weight, negative_weight) / max(total_directional, 1e-9)
        direction = "bullish" if direction_score > 1.2 else "bearish" if direction_score < -1.2 else "neutral"
        importance_score = min(
            10.0,
            1.2
            + min(len(source_reports), 6) * 0.8
            + min(len(nodes), 10) * 0.25
            + max((float(node.get("influence_score") or 0) for node in nodes), default=0.0) * 1.6
            + (0.8 if len(source_reports) >= 2 and conflict_ratio < 0.25 else 0.0),
        )
        if is_tracking_dimension or is_unclassified:
            direction_score = 0.0
            direction = "neutral"
            importance_score = min(importance_score, 1.2 if is_unclassified else 1.8)
        validation = (
            "pending_review_unclassified"
            if is_unclassified
            else
            "tracking_only"
            if is_tracking_dimension
            else
            "conflicted"
            if conflict_ratio >= 0.32
            else "multi_source_confirmed"
            if len(source_reports) >= 2 and abs(direction_score) >= 1.2
            else "single_source"
            if len(source_reports) == 1
            else "insufficient_directional_evidence"
        )
        support = _merge_evidence_texts(nodes, sign=1.0, limit=4)
        pressure = _merge_evidence_texts(nodes, sign=-1.0, limit=4)
        neutral = _merge_evidence_texts([node for node in nodes if float(node.get("impact_sign") or 0) == 0], sign=None, limit=3)
        leading = support[0]["text"] if direction == "bullish" and support else pressure[0]["text"] if direction == "bearish" and pressure else ""
        summary = (
            f"{label}维度{_direction_label(direction)}，{leading}"
            if leading
            else f"{label}维度暂未形成明确方向，需继续等待证据验证"
        )
        rows.append(
            {
                "asset": asset,
                "dimension_id": dimension_id,
                "dimension_label": label.split("/")[-1] if "/" in label else label,
                "full_dimension_label": label,
                "direction": direction,
                "direction_score": round(direction_score, 2),
                "importance_score": round(importance_score, 2),
                "weighted_score": round(direction_score * importance_score / 10.0, 3),
                "positive_weight": round(positive_weight, 3),
                "negative_weight": round(negative_weight, 3),
                "neutral_count": neutral_count,
                "conflict_ratio": round(conflict_ratio, 3),
                "validation_status": validation,
                "source_report_count": len(source_reports),
                "evidence_count": len(nodes),
                "source_reports": source_reports[:10],
                "support_evidence": support,
                "pressure_evidence": pressure,
                "neutral_evidence": neutral,
                "dimension_summary": summary,
            }
        )

    assets: dict[str, Any] = {}
    for asset in sorted({row["asset"] for row in rows}):
        asset_rows = [row for row in rows if row["asset"] == asset]
        scoring_rows = [
            row for row in asset_rows
            if row.get("validation_status") not in {"tracking_only", "pending_review_unclassified"}
            and abs(float(row.get("direction_score") or 0)) > 0
        ]
        if not scoring_rows:
            scoring_rows = asset_rows
        total_importance = sum(max(float(row["importance_score"]), 0.0) for row in scoring_rows)
        aggregate = (
            sum(float(row["direction_score"]) * max(float(row["importance_score"]), 0.0) for row in scoring_rows) / total_importance
            if total_importance > 0
            else 0.0
        )
        aggregate *= _coverage_multiplier(scoring_rows)
        asset_rows.sort(key=lambda row: abs(float(row["weighted_score"])), reverse=True)
        assets[asset] = {
            "asset": asset,
            "framework_evidence_score": round(max(-10.0, min(10.0, aggregate)), 2),
            "dimension_count": len(asset_rows),
            "evidence_count": sum(int(row["evidence_count"]) for row in asset_rows),
            "dimensions": asset_rows,
        }

    return {
        "schema_version": "asset_dimension_summaries.v1",
        "status": "candidate",
        "generated_at": utc_now_iso(),
        "method": "per_report_evidence_nodes_weighted_by_influence_and_mapping",
        "stats": {
            "asset_count": len(assets),
            "dimension_count": len(rows),
            "evidence_count": len(evidence_nodes_payload.get("evidence_nodes") or []),
        },
        "assets": assets,
    }


def _direction_label(direction: str) -> str:
    if direction == "bullish":
        return "形成支撑"
    if direction == "bearish":
        return "形成压力"
    return "方向中性"


def _coverage_multiplier(rows: list[dict[str, Any]]) -> float:
    source_reports: set[str] = set()
    multi_source_count = 0
    scoring_dimension_count = 0
    for row in rows:
        source_reports.update(str(item) for item in (row.get("source_reports") or []) if item)
        if row.get("validation_status") == "multi_source_confirmed":
            multi_source_count += 1
        if abs(float(row.get("direction_score") or 0)) > 0:
            scoring_dimension_count += 1

    source_count = len(source_reports)
    if source_count <= 1:
        multiplier = 0.45
    elif source_count == 2:
        multiplier = 0.68
    elif source_count == 3:
        multiplier = 0.84
    else:
        multiplier = 1.0

    if scoring_dimension_count <= 1:
        multiplier *= 0.75
    elif scoring_dimension_count == 2:
        multiplier *= 0.88
    if multi_source_count == 0:
        multiplier *= 0.85
    return max(0.25, min(1.0, multiplier))


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


def _bias_text(score: float) -> str:
    if score >= 3.0:
        return "偏多"
    if score <= -3.0:
        return "偏空"
    if score > 0.8:
        return "小幅偏多"
    if score < -0.8:
        return "小幅偏空"
    return "中性，等待更多证据确认"


def _combine_scores(framework_score: float, source_score: Any) -> tuple[float, dict[str, Any]]:
    raw = _bounded_float(source_score, lower=-10.0, upper=10.0, default=0.0)
    if raw * framework_score < 0 and abs(raw) >= 0.8 and abs(framework_score) >= 0.8:
        decision = max(-0.7, min(0.7, (framework_score + raw) * 0.15))
        return max(-10.0, min(10.0, decision)), {
            "status": "opposite_direction",
            "source_score": round(raw, 2),
            "framework_evidence_score": round(framework_score, 2),
            "policy": "conflict_downgrade_to_neutral_zone",
        }
    decision = framework_score * 0.72 + raw * 0.28
    return max(-10.0, min(10.0, decision)), {
        "status": "aligned_or_weak_source",
        "source_score": round(raw, 2),
        "framework_evidence_score": round(framework_score, 2),
        "policy": "framework_dominant_with_legacy_score_check",
    }


def _dimension_phrase(row: dict[str, Any], *, positive: bool) -> str:
    evidence_key = "support_evidence" if positive else "pressure_evidence"
    evidence = row.get(evidence_key) or []
    text = str((evidence[0] or {}).get("text") or row.get("dimension_summary") or "").strip() if evidence else str(row.get("dimension_summary") or "")
    label = str(row.get("dimension_label") or "相关维度")
    validation = str(row.get("validation_status") or "")
    suffix = "，多来源验证" if validation == "multi_source_confirmed" else "，存在分歧" if validation == "conflicted" else ""
    return f"{label}（{_first_sentence(text, limit=96)}{suffix}）"


def _is_tracking_or_unclassified_row(row: dict[str, Any]) -> bool:
    validation = str(row.get("validation_status") or "")
    label = str(row.get("full_dimension_label") or row.get("dimension_label") or "")
    return validation in {"tracking_only", "pending_review_unclassified"} or any(
        word in label for word in TRACKING_DIMENSION_WORDS
    )


def _unique_dimension_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        label = str(row.get("dimension_label") or row.get("full_dimension_label") or "").strip()
        key = _canonical_text(label)
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(row)
    return selected


def _candidate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [row for row in rows if not _is_tracking_or_unclassified_row(row)]
    return _unique_dimension_rows(candidates or rows)


def _compose_enhanced_mainline(asset: str, score: float, rows: list[dict[str, Any]], diagnostics: dict[str, Any]) -> str:
    bias = _bias_text(score)
    ranked_rows = _candidate_rows(rows)
    supports = [row for row in ranked_rows if row.get("direction") == "bullish"][:3]
    pressures = [row for row in ranked_rows if row.get("direction") == "bearish"][:3]
    if score < -0.8:
        core = pressures
        constraints = supports
        core_label = "核心压力"
        constraint_label = "修复线索"
        positive = False
    elif score > 0.8:
        core = supports
        constraints = pressures
        core_label = "核心支撑"
        constraint_label = "主要约束"
        positive = True
    else:
        core = sorted(ranked_rows, key=lambda row: abs(float(row.get("weighted_score") or 0)), reverse=True)[:3]
        constraints = []
        core_label = "主要分歧"
        constraint_label = ""
        positive = True

    parts = [f"{asset}基本面主线{bias}。"]
    if diagnostics.get("status") == "opposite_direction":
        parts.append("单篇研报情绪与框架维度证据方向相反，结论先降级为谨慎观察。")
    if core:
        phrases = [_dimension_phrase(row, positive=positive if score > 0.8 or score < -0.8 else row.get("direction") == "bullish") for row in core[:2]]
        parts.append(f"{core_label}来自{'、'.join(phrases)}。")
    else:
        parts.append("当前缺少足够的框架维度证据形成清晰主线。")
    if constraints:
        phrases = [_dimension_phrase(row, positive=not positive) for row in constraints[:2]]
        parts.append(f"{constraint_label}是{'、'.join(phrases)}。")
    watch = "、".join(str(row.get("dimension_label") or "") for row in ranked_rows[:3] if row.get("dimension_label"))
    if watch:
        parts.append(f"后续重点跟踪{watch}是否继续被新研报、新闻或数据库指标验证。")
    return "".join(parts)


def _factor_rows(rows: list[dict[str, Any]], direction: str, *, limit: int = 6) -> list[str]:
    labels = []
    seen: set[str] = set()
    for row in _candidate_rows(rows):
        if row.get("direction") != direction:
            continue
        label = str(row.get("dimension_label") or "").strip()
        key = _canonical_text(label)
        if not label or key in seen:
            continue
        seen.add(key)
        labels.append(label)
        if len(labels) >= limit:
            break
    return labels


def _primary_dimension_view(row: dict[str, Any], rank: int) -> dict[str, Any]:
    evidence = []
    for item in (row.get("support_evidence") or [])[:2]:
        evidence.append({"text": item.get("text"), "direction": "bullish", "source_field": item.get("source_field")})
    for item in (row.get("pressure_evidence") or [])[:2]:
        evidence.append({"text": item.get("text"), "direction": "bearish", "source_field": item.get("source_field")})
    importance = _bounded_float(row.get("importance_score"), lower=0.0, upper=10.0)
    return {
        "dimension_rank": rank,
        "dimension_id": row.get("dimension_id"),
        "dimension_label": row.get("dimension_label"),
        "direction_score": row.get("direction_score"),
        "importance_score": importance,
        "effective_weight": round(importance / 10.0, 3),
        "key_evidence": evidence[:3],
        "validation_status": row.get("validation_status"),
    }


def apply_dimension_summaries_to_trade_theses(
    trade_theses: dict[str, Any],
    asset_dimension_summaries: dict[str, Any],
) -> dict[str, Any]:
    result = json.loads(json.dumps(trade_theses, ensure_ascii=False))
    assets = result.get("assets") if isinstance(result.get("assets"), dict) else {}
    summary_assets = asset_dimension_summaries.get("assets") if isinstance(asset_dimension_summaries.get("assets"), dict) else {}
    for asset, thesis in assets.items():
        payload = summary_assets.get(asset)
        if not isinstance(payload, dict):
            continue
        rows = payload.get("dimensions") if isinstance(payload.get("dimensions"), list) else []
        framework_score = _bounded_float(payload.get("framework_evidence_score"), lower=-10.0, upper=10.0)
        decision_score, diagnostics = _combine_scores(framework_score, thesis.get("source_sentiment_score"))
        rows = sorted(rows, key=lambda row: abs(float(row.get("weighted_score") or 0)), reverse=True)
        ranked_core_rows = _candidate_rows(rows)
        old_text = thesis.get("main_trade_thesis")
        thesis["legacy_main_trade_thesis"] = old_text
        thesis["framework_score"] = round(framework_score, 2)
        thesis["decision_score"] = round(decision_score, 2)
        thesis["recommendation"] = _recommendation(decision_score)
        thesis["recommendation_basis"] = "per_report_dimension_evidence_score"
        thesis["score_diagnostics"] = {
            **(thesis.get("score_diagnostics") or {}),
            "per_report_dimension_score": diagnostics,
        }
        thesis["main_trade_thesis"] = _compose_enhanced_mainline(asset, decision_score, rows, diagnostics)
        thesis["supporting_dimensions"] = _factor_rows(rows, "bullish")
        thesis["opposing_dimensions"] = _factor_rows(rows, "bearish")
        thesis["primary_dimensions"] = [
            _primary_dimension_view(row, index + 1)
            for index, row in enumerate(ranked_core_rows[:3])
        ]
        thesis["framework_enhancement"] = {
            "method": "per_report_evidence_nodes_to_dimension_summary",
            "dimension_count": payload.get("dimension_count"),
            "evidence_count": payload.get("evidence_count"),
            "top_dimensions": ranked_core_rows[:5],
            "tracking_dimensions": [row for row in rows if _is_tracking_or_unclassified_row(row)][:5],
        }
    result["thesis_generation_method"] = "per_report_dimension_enhanced"
    result["framework_enhancement_method"] = "legacy_per_report_influence_weighted_dimension_summary"
    return result
