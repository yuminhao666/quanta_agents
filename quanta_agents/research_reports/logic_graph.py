from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import dated_parts, read_json, relative_to_root, utc_now_iso, write_json


SCHEMA_VERSION = "research_logic_graph.v1"
FRAMEWORK_DESIGN_SCHEMA = "research_logic_graph_framework_design.v1"
DEFAULT_START_DATE = "20260401"
NON_COMMODITY_CATEGORIES = {"金融期货", "指数"}

STANDARD_DIMENSIONS: list[dict[str, Any]] = [
    {
        "dimension_type": "supply",
        "label": "供给",
        "default_weight": 0.18,
        "role": "primary_driver",
        "catalogs": ["indicators", "event-triggers", "claim-patterns"],
    },
    {
        "dimension_type": "demand",
        "label": "需求",
        "default_weight": 0.18,
        "role": "primary_driver",
        "catalogs": ["indicators", "event-triggers", "claim-patterns"],
    },
    {
        "dimension_type": "supply_demand",
        "label": "供需平衡",
        "default_weight": 0.18,
        "role": "primary_driver",
        "catalogs": ["indicators", "event-triggers", "claim-patterns"],
    },
    {
        "dimension_type": "inventory",
        "label": "库存",
        "default_weight": 0.15,
        "role": "validation_driver",
        "catalogs": ["indicators", "event-triggers"],
    },
    {
        "dimension_type": "cost",
        "label": "成本利润",
        "default_weight": 0.12,
        "role": "transmission_driver",
        "catalogs": ["indicators", "event-triggers"],
    },
    {
        "dimension_type": "policy",
        "label": "政策",
        "default_weight": 0.10,
        "role": "external_driver",
        "catalogs": ["event-triggers", "claim-patterns"],
    },
    {
        "dimension_type": "macro",
        "label": "宏观",
        "default_weight": 0.10,
        "role": "external_driver",
        "catalogs": ["shared/macro-indicators", "shared/macro-events", "claim-patterns"],
    },
    {
        "dimension_type": "geopolitics",
        "label": "地缘",
        "default_weight": 0.10,
        "role": "external_driver",
        "catalogs": ["shared/geopolitics-events", "event-triggers"],
    },
    {
        "dimension_type": "spread",
        "label": "价差结构",
        "default_weight": 0.08,
        "role": "market_structure",
        "catalogs": ["indicators"],
    },
    {
        "dimension_type": "valuation",
        "label": "估值情绪",
        "default_weight": 0.10,
        "role": "sentiment_validation",
        "catalogs": ["indicators", "claim-patterns"],
    },
    {
        "dimension_type": "substitution",
        "label": "替代关系",
        "default_weight": 0.07,
        "role": "cross_asset_driver",
        "catalogs": ["indicators", "event-triggers"],
    },
    {
        "dimension_type": "weather",
        "label": "天气",
        "default_weight": 0.10,
        "role": "external_driver",
        "catalogs": ["event-triggers", "claim-patterns"],
    },
    {
        "dimension_type": "logistics",
        "label": "物流运输",
        "default_weight": 0.07,
        "role": "transmission_driver",
        "catalogs": ["indicators", "event-triggers"],
    },
    {
        "dimension_type": "funding",
        "label": "资金流动",
        "default_weight": 0.10,
        "role": "sentiment_validation",
        "catalogs": ["indicators", "claim-patterns"],
    },
    {
        "dimension_type": "seasonality",
        "label": "季节性",
        "default_weight": 0.06,
        "role": "time_context",
        "catalogs": ["event-triggers", "claim-patterns"],
    },
    {
        "dimension_type": "disease",
        "label": "疫病",
        "default_weight": 0.08,
        "role": "external_driver",
        "catalogs": ["event-triggers"],
    },
    {
        "dimension_type": "capacity_cycle",
        "label": "产能周期",
        "default_weight": 0.10,
        "role": "structural_driver",
        "catalogs": ["indicators", "event-triggers", "claim-patterns"],
    },
]

DIMENSION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "supply": ("供给", "供应", "产量", "产能", "开工", "减产", "增产", "复产", "停产", "检修", "进口", "出口", "发运", "到港", "矿", "装置"),
    "demand": ("需求", "消费", "成交", "订单", "终端", "下游", "补库", "补货", "地产", "基建", "汽车", "家电", "饲料", "出行"),
    "inventory": ("库存", "累库", "去库", "仓单", "库容", "港口库存", "社会库存", "油厂库存", "EIA", "API", "浮仓"),
    "cost": ("成本", "利润", "加工费", "TC", "RC", "冶炼", "压榨", "运费", "原料", "电价", "升贴水", "基差"),
    "policy": ("政策", "监管", "关税", "国储", "收储", "抛储", "限产", "环保", "安监", "保供", "稳价", "配额"),
    "macro": ("宏观", "美元", "利率", "美联储", "通胀", "PMI", "社融", "M2", "汇率", "流动性", "经济", "衰退"),
    "geopolitics": ("地缘", "中东", "制裁", "战争", "冲突", "停火", "海峡", "霍尔木兹", "OPEC", "风险溢价"),
    "spread": ("价差", "基差", "月差", "期限结构", "裂解", "EFS", "contango", "backwardation", "贴水", "升水"),
    "valuation": ("估值", "情绪", "风险偏好", "资金", "持仓", "成交", "ETF", "净多", "净空", "主力"),
    "substitution": ("替代", "比价", "竞争", "替代品", "性价比", "进口来源", "转产"),
    "weather": ("天气", "降水", "气温", "干旱", "洪涝", "霜冻", "墒情", "厄尔尼诺", "拉尼娜"),
    "logistics": ("物流", "港口", "运费", "船", "航运", "通航", "堵港", "运输", "发货", "到港"),
    "funding": ("资金", "流入", "流出", "两融", "北向", "ETF", "持仓", "净流入", "风险偏好"),
    "seasonality": ("旺季", "淡季", "季节性", "节前", "节后", "生长期", "收割", "播种", "交割"),
    "disease": ("疫病", "非瘟", "病害", "虫害", "疫情", "存栏受损"),
    "capacity_cycle": ("产能周期", "扩产", "投产", "爬坡", "新增产能", "出清", "过剩", "亏损减产"),
}

FIELD_DIMENSION_HINTS: dict[str, tuple[str, ...]] = {
    "supply_demand": ("supply", "demand", "inventory"),
    "key_data": ("inventory", "spread", "cost"),
    "key_events": ("policy", "geopolitics", "supply"),
    "price_forecast": ("valuation", "spread"),
    "bullish_factors": ("supply", "demand", "macro"),
    "bearish_factors": ("supply", "demand", "macro"),
}

DIRECTION_SCORE = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0, "mixed": 0.0, "unknown": 0.0}
DIRECTION_CN = {"bullish": "偏多", "bearish": "偏空", "neutral": "跟踪", "mixed": "分歧", "unknown": "待判定"}


def _hash_id(prefix: str, *parts: Any, length: int = 14) -> str:
    raw = "||".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:length].upper()}"


def _clean_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _short_text(text: str, limit: int = 80) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _date_key_from_path(path: Path) -> str:
    parts = path.parts
    try:
        idx = parts.index("hzzhqx_wechat")
        return "".join(parts[idx + 1 : idx + 4])
    except (ValueError, IndexError):
        return ""


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[:19] if fmt.endswith("%S") else text[:10], fmt).date()
        except ValueError:
            continue
    match = re.search(r"(20\d{2})[-/年]?(\d{1,2})[-/月]?(\d{1,2})", text)
    if match:
        yyyy, mm, dd = (int(item) for item in match.groups())
        try:
            return date(yyyy, mm, dd)
        except ValueError:
            return None
    return None


def _profile_paths(root: Path, start_date: str, end_date: str, limit: int = 0) -> list[Path]:
    base = root / "canonical_documents" / "research_reports" / "structured_profiles" / "hzzhqx_wechat"
    if not base.exists():
        return []
    paths: list[Path] = []
    for path in sorted(base.glob("20[0-9][0-9]/*/*/RREP-PROFILE-*.json")):
        date_key = _date_key_from_path(path)
        if date_key and start_date <= date_key <= end_date:
            paths.append(path)
            if limit and len(paths) >= limit:
                break
    return paths


def _load_latest_json(root: Path, relative_path: str) -> dict[str, Any]:
    path = root / relative_path
    if not path.exists():
        return {}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def _registry_dimensions(root: Path) -> dict[str, list[dict[str, Any]]]:
    registry = _load_latest_json(
        root,
        "agent_workspace/candidates/analysis_framework/latest/analysis-framework-registry.json",
    )
    by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in registry.get("dimensions") or []:
        if not isinstance(row, dict):
            continue
        asset_id = str(row.get("asset_id") or "").strip()
        if asset_id:
            by_asset[asset_id].append(row)
    return by_asset


def _catalog_terms(root: Path) -> dict[str, list[dict[str, Any]]]:
    catalog = _load_latest_json(
        root,
        "agent_workspace/candidates/analysis_framework/latest/indicator-event-catalog.json",
    )
    by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in catalog.get("terms") or []:
        if not isinstance(row, dict):
            continue
        asset_id = str(row.get("asset_id") or "").strip()
        name = str(row.get("name") or "").strip()
        if not asset_id or len(name) < 2:
            continue
        by_asset[asset_id].append(row)
    return by_asset


def _term_aliases(term: dict[str, Any]) -> list[str]:
    values = [str(term.get("name") or "").strip()]
    values.extend(str(item).strip() for item in term.get("aliases") or [])
    out = []
    for value in values:
        if not value or len(value) < 2:
            continue
        # Long dated event sentences are useful evidence but poor stable catalog keys.
        if len(value) > 36 and re.search(r"20\d{2}|同比|环比|万吨|亿元", value):
            continue
        out.append(value)
    return list(dict.fromkeys(out))


def _match_terms(text: str, terms: list[dict[str, Any]], *, max_hits: int = 8) -> list[dict[str, Any]]:
    if not text or not terms:
        return []
    low = text.lower()
    hits: list[dict[str, Any]] = []
    for term in terms:
        best_alias = ""
        best_score = 0.0
        for alias in _term_aliases(term):
            alias_low = alias.lower()
            if alias in text or alias_low in low:
                score = min(0.95, 0.25 + min(len(alias), 24) / 32)
                if score > best_score:
                    best_score = score
                    best_alias = alias
        if best_score:
            hits.append(
                {
                    "term_id": term.get("term_id"),
                    "term_type": term.get("term_type"),
                    "name": term.get("name"),
                    "matched_alias": best_alias,
                    "dimension_id": term.get("dimension_id"),
                    "dimension_label": term.get("dimension_label"),
                    "dimension_type": term.get("dimension_type"),
                    "score": round(best_score, 3),
                }
            )
    return sorted(hits, key=lambda item: item["score"], reverse=True)[:max_hits]


def _dimension_hints(text: str, field_tags: list[str]) -> Counter[str]:
    counter: Counter[str] = Counter()
    low = text.lower()
    for dimension_type, keywords in DIMENSION_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if keyword in text or keyword.lower() in low)
        if hits:
            counter[dimension_type] += hits
    for tag in field_tags:
        for dimension_type in FIELD_DIMENSION_HINTS.get(str(tag), ()):
            counter[dimension_type] += 1
    return counter


def _dimension_label(row: dict[str, Any]) -> str:
    label = str(row.get("dimension_label") or row.get("dimension_name") or "").strip()
    if "/" in label:
        return label.rsplit("/", 1)[-1].strip() or label
    return label or "未归类"


def _default_dimension(asset_id: str, asset: str, dimension_type: str) -> dict[str, Any]:
    label = next(
        (row["label"] for row in STANDARD_DIMENSIONS if row["dimension_type"] == dimension_type),
        dimension_type or "未归类",
    )
    return {
        "dimension_id": f"DEFAULT:{asset_id}:{dimension_type or 'other'}",
        "dimension_label": label,
        "dimension_name": label,
        "dimension_type": dimension_type or "other",
        "asset": asset,
        "asset_id": asset_id,
        "framework_id": "default_research_logic_graph",
        "default_weight": next(
            (row["default_weight"] for row in STANDARD_DIMENSIONS if row["dimension_type"] == dimension_type),
            0.05,
        ),
    }


def _match_dimension(
    *,
    asset_id: str,
    asset: str,
    text: str,
    field_tags: list[str],
    dimensions: list[dict[str, Any]],
    term_hits: list[dict[str, Any]],
) -> tuple[dict[str, Any], float, str]:
    if not dimensions:
        hints = _dimension_hints(text, field_tags)
        dimension_type = hints.most_common(1)[0][0] if hints else "other"
        return _default_dimension(asset_id, asset, dimension_type), 0.35 if hints else 0.2, "default_dimension"

    scores: dict[str, float] = defaultdict(float)
    by_id = {str(row.get("dimension_id")): row for row in dimensions}
    hints = _dimension_hints(text, field_tags)

    for hit in term_hits:
        dimension_id = str(hit.get("dimension_id") or "")
        if dimension_id in by_id:
            scores[dimension_id] += 0.45 + float(hit.get("score") or 0.0) * 0.35

    for row in dimensions:
        dimension_id = str(row.get("dimension_id") or "")
        dimension_type = str(row.get("dimension_type") or "other")
        label = _dimension_label(row)
        if label and label != "未归类" and label in text:
            scores[dimension_id] += 0.35
        if dimension_type in hints:
            scores[dimension_id] += min(0.45, 0.12 * hints[dimension_type])

    if scores:
        dimension_id, score = max(scores.items(), key=lambda item: item[1])
        return by_id[dimension_id], round(min(0.95, 0.35 + score), 3), "framework_catalog_rule"

    if hints:
        dimension_type = hints.most_common(1)[0][0]
        same_type = [row for row in dimensions if str(row.get("dimension_type") or "") == dimension_type]
        if same_type:
            return same_type[0], 0.48, "framework_dimension_hint"
        return _default_dimension(asset_id, asset, dimension_type), 0.36, "default_dimension_hint"

    return _default_dimension(asset_id, asset, "other"), 0.2, "unmapped"


def _asset_map_from_profile(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    details = profile.get("detailed_analysis") or {}
    if not isinstance(details, dict):
        return mapping
    for name, payload in details.items():
        if not isinstance(payload, dict):
            continue
        asset = payload.get("asset") if isinstance(payload.get("asset"), dict) else {}
        row = {
            "asset": asset.get("name") or payload.get("commodity") or name,
            "asset_id": asset.get("asset_id") or payload.get("asset_id") or "",
            "commodity_code": asset.get("commodity_code") or "",
            "category": asset.get("category") or "",
            "sector": asset.get("sector") or "",
        }
        mapping[str(name)] = row
        mapping[str(row["asset"])] = row
    return mapping


def _is_commodity_asset(row: dict[str, Any]) -> bool:
    category = str(row.get("category") or "")
    sector = str(row.get("sector") or "")
    return category not in NON_COMMODITY_CATEGORIES and sector not in NON_COMMODITY_CATEGORIES


def _iter_evidence_capsules(
    *,
    root: Path,
    profile_paths: list[Path],
    commodity_only: bool,
) -> list[dict[str, Any]]:
    capsules: list[dict[str, Any]] = []
    for path in profile_paths:
        try:
            profile = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(profile, dict):
            continue
        metadata = profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}
        asset_map = _asset_map_from_profile(profile)
        profile_id = str(profile.get("profile_id") or path.stem)
        fallback_date = _date_key_from_path(path)
        published_at = metadata.get("published_at") or fallback_date
        event_date = _parse_date(published_at) or _parse_date(fallback_date)
        for raw in profile.get("evidence") or []:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or "").strip()
            if len(text) < 8:
                continue
            asset_name = str(raw.get("asset") or "").strip()
            asset_info = asset_map.get(asset_name) or {"asset": asset_name, "asset_id": ""}
            if commodity_only and not _is_commodity_asset(asset_info):
                continue
            asset_id = str(asset_info.get("asset_id") or "").strip()
            if not asset_id:
                continue
            capsules.append(
                {
                    "evidence_id": str(raw.get("evidence_id") or _hash_id("EVID", profile_id, text)),
                    "profile_id": profile_id,
                    "article_id": metadata.get("article_id") or metadata.get("raw_id") or "",
                    "raw_id": metadata.get("raw_id") or metadata.get("article_id") or "",
                    "asset": asset_info.get("asset") or asset_name,
                    "asset_id": asset_id,
                    "commodity_code": asset_info.get("commodity_code") or "",
                    "category": asset_info.get("category") or "",
                    "sector": asset_info.get("sector") or "",
                    "text": text,
                    "direction": str(raw.get("direction") or "neutral"),
                    "direction_score": float(raw.get("direction_score") or 0.0),
                    "scoring_role": raw.get("scoring_role") or "fundamental_evidence",
                    "field_tags": [str(item) for item in raw.get("field_tags") or []],
                    "section": raw.get("section") if isinstance(raw.get("section"), dict) else {},
                    "published_at": published_at,
                    "event_date": event_date.isoformat() if event_date else "",
                    "source_account": metadata.get("source_account") or "",
                    "title": metadata.get("title") or "",
                    "source_url": metadata.get("source_url") or "",
                    "source_ref": {
                        "profile_path": relative_to_root(path, root),
                        "profile_id": profile_id,
                        "article_id": metadata.get("article_id") or "",
                        "raw_manifest": metadata.get("raw_manifest") or "",
                        "text_path": metadata.get("text_path") or "",
                    },
                }
            )
    return capsules


def _signal_kind(capsule: dict[str, Any], direction: str) -> str:
    if capsule.get("scoring_role") == "market_observation":
        return "price_validation"
    if direction == "neutral":
        return "framework_mapping"
    return "thesis_support"


def _signal_strength(capsule: dict[str, Any], mapping_confidence: float) -> float:
    raw_direction = abs(float(capsule.get("direction_score") or DIRECTION_SCORE.get(capsule.get("direction"), 0.0)))
    if raw_direction <= 0:
        raw_direction = 0.25
    source_weight = 0.75 if capsule.get("scoring_role") == "fundamental_evidence" else 0.35
    return round(min(1.0, raw_direction * source_weight * max(mapping_confidence, 0.2)), 4)


def _predicate_from_signal(signal: dict[str, Any]) -> tuple[str, str, list[str]]:
    terms = signal.get("matched_terms") or []
    if terms:
        term = terms[0]
        name = str(term.get("name") or term.get("matched_alias") or "").strip()
        if name:
            return _clean_key(name), name, [str(item.get("term_id") or "") for item in terms if item.get("term_id")]
    dimension = signal.get("framework_node") or {}
    label = _dimension_label(dimension)
    if label != "未归类":
        return _clean_key(label), label, []
    text = str(signal.get("summary") or "")
    words = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9+_-]{1,16}", text)
    name = " ".join(words[:3]) if words else "未归类"
    return _clean_key(name), name, []


def _build_evidence_signals(
    capsules: list[dict[str, Any]],
    dimensions_by_asset: dict[str, list[dict[str, Any]]],
    terms_by_asset: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for capsule in capsules:
        text = str(capsule.get("text") or "")
        asset_id = str(capsule.get("asset_id") or "")
        term_hits = _match_terms(text, terms_by_asset.get(asset_id, []))
        dimension, confidence, method = _match_dimension(
            asset_id=asset_id,
            asset=str(capsule.get("asset") or ""),
            text=text,
            field_tags=list(capsule.get("field_tags") or []),
            dimensions=dimensions_by_asset.get(asset_id, []),
            term_hits=term_hits,
        )
        direction = str(capsule.get("direction") or "neutral")
        if direction not in DIRECTION_SCORE:
            direction = "unknown"
        signal = {
            "schema_version": "research_signal.v1-lite",
            "signal_id": _hash_id("SIG", capsule.get("evidence_id"), asset_id, dimension.get("dimension_id"), direction),
            "status": "candidate",
            "source_role": "research_report",
            "signal_kind": _signal_kind(capsule, direction),
            "asset": capsule.get("asset"),
            "asset_id": asset_id,
            "framework_node": {
                "dimension_id": dimension.get("dimension_id"),
                "dimension_label": dimension.get("dimension_label") or dimension.get("dimension_name"),
                "dimension_type": dimension.get("dimension_type"),
                "framework_id": dimension.get("framework_id"),
            },
            "matched_terms": term_hits,
            "direction": direction,
            "direction_score": capsule.get("direction_score"),
            "strength": _signal_strength(capsule, confidence),
            "confidence": confidence,
            "mapping": {"method": method, "confidence": confidence},
            "time_window": {
                "event_time": capsule.get("published_at"),
                "event_date": capsule.get("event_date"),
                "update_time": utc_now_iso(),
            },
            "summary": _short_text(text, 160),
            "evidence_ref": {
                "evidence_id": capsule.get("evidence_id"),
                "profile_id": capsule.get("profile_id"),
                "article_id": capsule.get("article_id"),
                "source_account": capsule.get("source_account"),
                "title": capsule.get("title"),
                "source_ref": capsule.get("source_ref"),
            },
            "scoring_role": capsule.get("scoring_role"),
            "field_tags": capsule.get("field_tags"),
        }
        signals.append(signal)
    return signals


def _direction_from_net(net: float) -> str:
    if net > 0.05:
        return "bullish"
    if net < -0.05:
        return "bearish"
    return "neutral"


def _week_bucket(value: str) -> str:
    parsed = _parse_date(value)
    if not parsed:
        return "unknown"
    start = parsed - timedelta(days=parsed.weekday())
    return start.isoformat()


def _latest_state(
    dated_strengths: list[tuple[date, float]],
    support_count: int,
    conflict_count: int,
    as_of: date,
) -> str:
    if not dated_strengths:
        return "new"
    latest_date = max(item[0] for item in dated_strengths)
    if (as_of - latest_date).days > 21:
        return "dormant"
    recent_start = as_of - timedelta(days=14)
    prior_start = as_of - timedelta(days=28)
    recent = sum(value for dt, value in dated_strengths if dt >= recent_start)
    prior = sum(value for dt, value in dated_strengths if prior_start <= dt < recent_start)
    if support_count + conflict_count <= 2:
        return "new"
    if support_count and conflict_count >= max(2, int(support_count * 0.55)):
        return "conflict"
    if prior and recent and (prior > 0 > recent or prior < 0 < recent):
        return "reversal"
    if abs(recent) > abs(prior) * 1.25 + 0.2:
        return "strengthening"
    if abs(recent) + 0.2 < abs(prior) * 0.75:
        return "weakening"
    return "tracking"


def _build_logic_nodes(signals: list[dict[str, Any]], as_of: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        if signal.get("scoring_role") == "market_observation":
            continue
        if float((signal.get("mapping") or {}).get("confidence") or 0.0) < 0.3:
            continue
        predicate_key, _, _ = _predicate_from_signal(signal)
        node = signal.get("framework_node") or {}
        grouped[
            (
                str(signal.get("asset_id") or ""),
                str(node.get("dimension_id") or ""),
                predicate_key or "unknown",
            )
        ].append(signal)

    nodes: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    for (asset_id, dimension_id, predicate_key), items in grouped.items():
        if not asset_id or not dimension_id:
            continue
        sample = items[0]
        dimension = sample.get("framework_node") or {}
        _, predicate_label, term_refs = _predicate_from_signal(sample)
        dated_strengths: list[tuple[date, float]] = []
        direction_counts: Counter[str] = Counter()
        sources: set[str] = set()
        weekly: dict[str, dict[str, Any]] = defaultdict(lambda: {"support": 0.0, "conflict": 0.0, "neutral": 0.0, "evidence_count": 0})
        for signal in items:
            direction = str(signal.get("direction") or "neutral")
            signed = DIRECTION_SCORE.get(direction, 0.0) * float(signal.get("strength") or 0.0)
            parsed_date = _parse_date((signal.get("time_window") or {}).get("event_date"))
            if parsed_date:
                dated_strengths.append((parsed_date, signed))
            direction_counts[direction] += 1
            source = ((signal.get("evidence_ref") or {}).get("source_account") or "").strip()
            if source:
                sources.add(source)
            bucket = _week_bucket((signal.get("time_window") or {}).get("event_date") or "")
            weekly[bucket]["evidence_count"] += 1
            if signed > 0:
                weekly[bucket]["support"] += signed
            elif signed < 0:
                weekly[bucket]["conflict"] += abs(signed)
            else:
                weekly[bucket]["neutral"] += float(signal.get("strength") or 0.0)

        net = sum(value for _, value in dated_strengths)
        node_direction = _direction_from_net(net)
        support_count = direction_counts["bullish"] if node_direction == "bullish" else direction_counts["bearish"] if node_direction == "bearish" else direction_counts["neutral"]
        conflict_count = direction_counts["bearish"] if node_direction == "bullish" else direction_counts["bullish"] if node_direction == "bearish" else 0
        event_dates = [dt for dt, _ in dated_strengths]
        node_id = _hash_id("LGN", asset_id, dimension_id, predicate_key)
        dimension_label = _dimension_label(dimension)
        human_label = f"{predicate_label}{DIRECTION_CN.get(node_direction, '')}"
        if dimension_label and dimension_label not in human_label and dimension_label != "未归类":
            human_label = f"{dimension_label}：{human_label}"
        avg_confidence = sum(float(item.get("confidence") or 0.0) for item in items) / max(len(items), 1)
        confidence = round(min(0.95, avg_confidence + min(len(sources), 5) * 0.025), 3)
        state = _latest_state(dated_strengths, support_count, conflict_count, as_of)
        evidence_refs = [
            {
                "signal_id": signal.get("signal_id"),
                **(signal.get("evidence_ref") or {}),
                "summary": signal.get("summary"),
            }
            for signal in sorted(items, key=lambda row: (row.get("time_window") or {}).get("event_date") or "", reverse=True)[:8]
        ]
        nodes.append(
            {
                "logic_node_id": node_id,
                "human_label": human_label,
                "asset": sample.get("asset"),
                "asset_id": asset_id,
                "framework_node_refs": [dimension_id],
                "dimension_label": dimension.get("dimension_label") or dimension.get("dimension_name"),
                "dimension_type": dimension.get("dimension_type"),
                "catalog_term_refs": [ref for ref in term_refs if ref],
                "predicate": predicate_label,
                "direction": node_direction,
                "state": state,
                "strength": round(min(1.0, abs(net) / max(len(items), 1) * 1.8), 4),
                "confidence": confidence,
                "support_evidence_count": support_count,
                "conflict_evidence_count": conflict_count,
                "neutral_evidence_count": direction_counts["neutral"],
                "source_count": len(sources),
                "evidence_count": len(items),
                "first_seen": min(event_dates).isoformat() if event_dates else "",
                "last_seen": max(event_dates).isoformat() if event_dates else "",
                "latest_evidence_summary": evidence_refs[0]["summary"] if evidence_refs else "",
                "evidence_refs": evidence_refs,
                "review_status": "candidate",
            }
        )
        for bucket, payload in sorted(weekly.items()):
            episodes.append(
                {
                    "episode_id": _hash_id("EP", node_id, bucket),
                    "logic_node_id": node_id,
                    "asset_id": asset_id,
                    "bucket": bucket,
                    "support_score": round(payload["support"], 4),
                    "conflict_score": round(payload["conflict"], 4),
                    "neutral_score": round(payload["neutral"], 4),
                    "evidence_count": payload["evidence_count"],
                }
            )

    state_rank = {
        "reversal": 7,
        "strengthening": 6,
        "conflict": 5,
        "tracking": 4,
        "new": 3,
        "weakening": 2,
        "dormant": 0,
    }
    nodes.sort(
        key=lambda row: (
            state_rank.get(str(row.get("state") or ""), 1),
            str(row.get("last_seen") or ""),
            row["evidence_count"],
            row["confidence"],
            row["strength"],
        ),
        reverse=True,
    )
    return nodes, episodes


def _build_edges_and_triggers(nodes: list[dict[str, Any]], as_of: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    edges: list[dict[str, Any]] = []
    triggers: list[dict[str, Any]] = []
    for node in nodes:
        polarity = 1 if node.get("direction") == "bullish" else -1 if node.get("direction") == "bearish" else 0
        driver_id = f"DRV:{node.get('asset_id')}:{node.get('dimension_type') or 'other'}"
        weight = round(float(node.get("strength") or 0.0) * float(node.get("confidence") or 0.0), 4)
        edge_id = _hash_id("LEDGE", node.get("logic_node_id"), driver_id)
        edges.append(
            {
                "edge_id": edge_id,
                "from": node.get("logic_node_id"),
                "to": driver_id,
                "edge_type": "logic_to_driver_candidate",
                "human_label": f"{node.get('human_label')} -> {node.get('asset')} {node.get('dimension_label')}",
                "weight": weight,
                "polarity": polarity,
                "delay": "days",
                "confidence": node.get("confidence"),
                "evidence_count": node.get("evidence_count"),
                "status": "candidate",
            }
        )
        state = str(node.get("state") or "")
        if state == "dormant":
            continue
        trigger_score = weight
        if state in {"strengthening", "reversal", "conflict"}:
            trigger_score += 0.18
        if node.get("last_seen"):
            parsed = _parse_date(node.get("last_seen"))
            if parsed:
                trigger_score += max(0.0, 0.12 - min((as_of - parsed).days, 30) / 250)
        trigger_score = round(min(1.0, trigger_score), 4)
        if trigger_score >= 0.45 and int(node.get("evidence_count") or 0) >= 2:
            triggers.append(
                {
                    "trigger_id": _hash_id("DTC", node.get("logic_node_id"), state),
                    "logic_node_id": node.get("logic_node_id"),
                    "driver_id": driver_id,
                    "asset": node.get("asset"),
                    "asset_id": node.get("asset_id"),
                    "human_label": node.get("human_label"),
                    "state": state,
                    "direction": node.get("direction"),
                    "trigger_score": trigger_score,
                    "confidence": node.get("confidence"),
                    "support_evidence_count": node.get("support_evidence_count"),
                    "conflict_evidence_count": node.get("conflict_evidence_count"),
                    "latest_evidence_summary": node.get("latest_evidence_summary"),
                    "required_next_step": "enter_asset_event_state_as_research_event",
                    "status": "candidate",
                }
            )
    triggers.sort(key=lambda row: row["trigger_score"], reverse=True)
    return edges, triggers[:120]


def _build_framework_design(root: Path, *, generated_at: str, audit: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": FRAMEWORK_DESIGN_SCHEMA,
        "status": "candidate",
        "generated_at": generated_at,
        "principle": "稳定 framework 只保存维度、规则、传导模板和 catalog_refs；具体指标、事件触发词、研报 claim pattern 独立进入 catalog；研报运行只生成候选 logic nodes 和 driver triggers。",
        "source_refs": {
            "asset_taxonomy": "gold/reference_data/assets/futures_assets.v1.json",
            "framework_registry": "agent_workspace/candidates/analysis_framework/latest/analysis-framework-registry.json",
            "indicator_event_catalog": "agent_workspace/candidates/analysis_framework/latest/indicator-event-catalog.json",
            "framework_audit": "agent_workspace/candidates/framework_audit/latest/framework-audit.json",
        },
        "dimension_taxonomy": STANDARD_DIMENSIONS,
        "framework_core_template": {
            "path": "knowledge_base/frameworks/commodities/{asset_id}/framework-core.v1.json",
            "fields": [
                "asset_id",
                "dimensions.dimension_id",
                "dimensions.dimension_type",
                "dimensions.default_weight",
                "dimensions.driver_mapping",
                "dimensions.catalog_refs",
                "causal_templates",
                "governance",
            ],
            "forbidden_runtime_fields": [
                "daily_news",
                "report_sentences",
                "point_in_time_indicator_values",
                "investment_conclusion",
            ],
        },
        "catalog_templates": [
            "knowledge_base/catalogs/commodities/{asset_id}/indicators.v1.json",
            "knowledge_base/catalogs/commodities/{asset_id}/event-triggers.v1.json",
            "knowledge_base/catalogs/commodities/{asset_id}/claim-patterns.v1.json",
            "knowledge_base/catalogs/commodities/{asset_id}/aliases.v1.json",
            "knowledge_base/catalogs/shared/macro-indicators.v1.json",
            "knowledge_base/catalogs/shared/macro-events.v1.json",
            "knowledge_base/catalogs/shared/geopolitics-events.v1.json",
        ],
        "logic_graph_outputs": {
            "latest": "agent_workspace/candidates/research_logic_graph/latest/",
            "run": "agent_workspace/runs/research_logic_graph/{yyyy}/{mm}/{dd}/RUN-*/",
            "objects": [
                "evidence-signals.jsonl",
                "logic-nodes.json",
                "logic-edges.json",
                "temporal-episodes.json",
                "driver-trigger-candidates.json",
                "human-report.md",
            ],
        },
        "audit_summary": (audit or {}).get("summary") or {},
        "governance": {
            "write_back": "human_review_required",
            "active_framework_direct_write": "forbidden",
            "runtime_outputs": "candidate_only",
        },
    }


def _framework_design_markdown(design: dict[str, Any]) -> str:
    dims = design.get("dimension_taxonomy") or []
    lines = [
        "# 优化后的商品逻辑图谱框架",
        "",
        design.get("principle", ""),
        "",
        "## 稳定框架",
        "",
        f"- 路径模板：`{design['framework_core_template']['path']}`",
        "- 只保存维度、默认权重、driver mapping、causal templates 和 catalog_refs。",
        "- 不保存每日新闻、研报原句、点状指标数值和投资结论。",
        "",
        "## 指标/事件 Catalog",
        "",
    ]
    lines.extend(f"- `{item}`" for item in design.get("catalog_templates") or [])
    lines.extend(["", "## 标准维度", ""])
    for row in dims:
        lines.append(f"- {row['label']} (`{row['dimension_type']}`)：{row['role']}，默认权重 {row['default_weight']}")
    audit_summary = design.get("audit_summary") or {}
    if audit_summary:
        lines.extend(
            [
                "",
                "## 当前框架审计摘要",
                "",
                f"- framework_count: {audit_summary.get('framework_count')}",
                f"- optimization_candidate_count: {audit_summary.get('optimization_candidate_count')}",
                f"- embedded_indicator_event_terms: {(audit_summary.get('issue_counts') or {}).get('embedded_indicator_event_terms')}",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _human_report(
    *,
    result: dict[str, Any],
    nodes: list[dict[str, Any]],
    triggers: list[dict[str, Any]],
    profile_count: int,
    signal_count: int,
) -> str:
    lines = [
        "# 研报逻辑图谱首轮运行报告",
        "",
        f"- run_id: `{result['run_id']}`",
        f"- 时间范围: `{result['start_date']}` 至 `{result['end_date']}`",
        f"- 结构化研报 profile: {profile_count}",
        f"- evidence signal: {signal_count}",
        f"- logic nodes: {len(nodes)}",
        f"- driver trigger candidates: {len(triggers)}",
        "",
        "## 核心逻辑节点",
        "",
    ]
    for node in nodes[:30]:
        lines.append(
            f"- {node['asset']} / {node.get('dimension_label')}: {node['human_label']} "
            f"[{node['state']}, evidence={node['evidence_count']}, confidence={node['confidence']}]"
        )
        if node.get("latest_evidence_summary"):
            lines.append(f"  - 最新证据：{node['latest_evidence_summary']}")
    lines.extend(["", "## Driver 触发候选", ""])
    for trigger in triggers[:30]:
        lines.append(
            f"- {trigger['asset']}: {trigger['human_label']} -> {trigger['driver_id']} "
            f"[score={trigger['trigger_score']}, state={trigger['state']}]"
        )
    lines.extend(
        [
            "",
            "## 使用边界",
            "",
            "- 本轮输出是 candidate，不写 active framework，不写 gold。",
            "- 研报是观点和证据，不是事实本身；后续需要新闻、数据和行情交叉验证。",
            "- 前端展示应使用 `human_label`，机器 ID 只放入溯源详情。",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")


def _copy_to_latest(latest: Path, files: dict[str, Path]) -> dict[str, str]:
    latest.mkdir(parents=True, exist_ok=True)
    refs = {}
    for name, path in files.items():
        target = latest / path.name
        target.write_bytes(path.read_bytes())
        refs[name] = str(target)
    return refs


def run_research_logic_graph(
    root: str | Path | None = None,
    *,
    start_date: str = DEFAULT_START_DATE,
    end_date: str | None = None,
    max_profiles: int = 0,
    commodity_only: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    now_dt = now or datetime.now(timezone.utc)
    end_date = end_date or now_dt.strftime("%Y%m%d")
    as_of = _parse_date(end_date) or now_dt.date()
    stamp = now_dt.strftime("%H%M%S")
    yyyy, mm, dd = dated_parts(now_dt.strftime("%Y%m%d"))
    run_id = f"RUN-{now_dt.strftime('%Y%m%d')}-{stamp}-RESEARCH-LOGIC-GRAPH"
    candidate_id = f"CAND-RESEARCH-LOGIC-GRAPH-{now_dt.strftime('%Y%m%d')}-{stamp}"

    profile_paths = _profile_paths(root_path, start_date, end_date, limit=max_profiles)
    dimensions_by_asset = _registry_dimensions(root_path)
    terms_by_asset = _catalog_terms(root_path)
    audit = _load_latest_json(root_path, "agent_workspace/candidates/framework_audit/latest/framework-audit.json")
    design = _build_framework_design(root_path, generated_at=utc_now_iso(), audit=audit)

    capsules = _iter_evidence_capsules(root=root_path, profile_paths=profile_paths, commodity_only=commodity_only)
    signals = _build_evidence_signals(capsules, dimensions_by_asset, terms_by_asset)
    nodes, episodes = _build_logic_nodes(signals, as_of)
    edges, triggers = _build_edges_and_triggers(nodes, as_of)

    run_dir = root_path / "agent_workspace" / "runs" / "research_logic_graph" / yyyy / mm / dd / run_id
    candidate_dir = root_path / "agent_workspace" / "candidates" / "research_logic_graph" / yyyy / mm / dd / candidate_id
    latest_dir = root_path / "agent_workspace" / "candidates" / "research_logic_graph" / "latest"
    design_latest_dir = root_path / "agent_workspace" / "candidates" / "research_logic_graph" / "framework_design" / "latest"

    graph_payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "candidate",
        "run_id": run_id,
        "generated_at": utc_now_iso(),
        "start_date": start_date,
        "end_date": end_date,
        "stats": {
            "profile_count": len(profile_paths),
            "evidence_capsule_count": len(capsules),
            "evidence_signal_count": len(signals),
            "logic_node_count": len(nodes),
            "logic_edge_count": len(edges),
            "temporal_episode_count": len(episodes),
            "driver_trigger_candidate_count": len(triggers),
        },
        "input_refs": {
            "structured_profiles_root": "canonical_documents/research_reports/structured_profiles/hzzhqx_wechat",
            "analysis_framework_registry": "agent_workspace/candidates/analysis_framework/latest/analysis-framework-registry.json",
            "indicator_event_catalog": "agent_workspace/candidates/analysis_framework/latest/indicator-event-catalog.json",
            "framework_audit": "agent_workspace/candidates/framework_audit/latest/framework-audit.json",
        },
    }
    report = _human_report(
        result={"run_id": run_id, "start_date": start_date, "end_date": end_date},
        nodes=nodes,
        triggers=triggers,
        profile_count=len(profile_paths),
        signal_count=len(signals),
    )

    files: dict[str, Path] = {
        "logic_nodes": run_dir / "logic-nodes.json",
        "logic_edges": run_dir / "logic-edges.json",
        "temporal_episodes": run_dir / "temporal-episodes.json",
        "driver_trigger_candidates": run_dir / "driver-trigger-candidates.json",
        "human_report": run_dir / "human-report.md",
    }
    write_json(run_dir / "optimized-framework-design.json", design)
    (run_dir / "optimized-framework-design.md").parent.mkdir(parents=True, exist_ok=True)
    (run_dir / "optimized-framework-design.md").write_text(_framework_design_markdown(design), encoding="utf-8")
    write_json(files["logic_nodes"], {**graph_payload, "nodes": nodes})
    write_json(files["logic_edges"], {**graph_payload, "edges": edges})
    write_json(files["temporal_episodes"], {**graph_payload, "episodes": episodes})
    write_json(files["driver_trigger_candidates"], {**graph_payload, "driver_trigger_candidates": triggers})
    files["human_report"].write_text(report, encoding="utf-8")
    _write_jsonl(run_dir / "evidence-signals.jsonl", signals)
    _write_jsonl(
        run_dir / "logic-node-updates.jsonl",
        [{"logic_node_id": node["logic_node_id"], "state": node["state"], "as_of": end_date, "node": node} for node in nodes],
    )
    _write_jsonl(run_dir / "graph-mutations.jsonl", [{"mutation_type": "candidate_edge_upsert", "edge": edge} for edge in edges])
    write_json(
        run_dir / "input_refs.json",
        {
            "profile_count": len(profile_paths),
            "profiles_sample": [relative_to_root(path, root_path) for path in profile_paths[:50]],
            "registry_asset_count": len(dimensions_by_asset),
            "catalog_asset_count": len(terms_by_asset),
        },
    )
    validation_text = (
        "# Research Logic Graph Validation\n\n"
        f"- profile_count: {len(profile_paths)}\n"
        f"- evidence_signal_count: {len(signals)}\n"
        f"- logic_node_count: {len(nodes)}\n"
        f"- driver_trigger_candidate_count: {len(triggers)}\n"
        "- active framework write: forbidden\n"
        "- gold write: forbidden\n"
    )
    (run_dir / "validation.md").write_text(validation_text, encoding="utf-8")
    manifest = {
        **graph_payload,
        "run_dir": relative_to_root(run_dir, root_path),
        "candidate_dir": relative_to_root(candidate_dir, root_path),
        "latest_dir": relative_to_root(latest_dir, root_path),
        "outputs": {name: relative_to_root(path, root_path) for name, path in files.items()},
        "intermediate_outputs": {
            "optimized_framework_design_json": relative_to_root(run_dir / "optimized-framework-design.json", root_path),
            "optimized_framework_design_md": relative_to_root(run_dir / "optimized-framework-design.md", root_path),
            "evidence_signals": relative_to_root(run_dir / "evidence-signals.jsonl", root_path),
            "logic_node_updates": relative_to_root(run_dir / "logic-node-updates.jsonl", root_path),
            "graph_mutations": relative_to_root(run_dir / "graph-mutations.jsonl", root_path),
            "validation": relative_to_root(run_dir / "validation.md", root_path),
        },
    }
    write_json(run_dir / "run_manifest.json", manifest)

    candidate_dir.mkdir(parents=True, exist_ok=True)
    candidate_files = _copy_to_latest(candidate_dir, files)
    write_json(candidate_dir / "optimized-framework-design.json", design)
    (candidate_dir / "optimized-framework-design.md").write_text(_framework_design_markdown(design), encoding="utf-8")
    write_json(candidate_dir / "run_manifest.json", manifest)
    latest_files = _copy_to_latest(latest_dir, files)
    write_json(latest_dir / "optimized-framework-design.json", design)
    (latest_dir / "optimized-framework-design.md").write_text(_framework_design_markdown(design), encoding="utf-8")
    write_json(latest_dir / "run_manifest.json", manifest)
    design_latest_dir.mkdir(parents=True, exist_ok=True)
    write_json(design_latest_dir / "optimized-framework-design.json", design)
    (design_latest_dir / "optimized-framework-design.md").write_text(_framework_design_markdown(design), encoding="utf-8")

    return {
        **manifest,
        "absolute_run_dir": str(run_dir),
        "absolute_latest_dir": str(latest_dir),
        "absolute_candidate_dir": str(candidate_dir),
        "absolute_design_latest_dir": str(design_latest_dir),
        "candidate_files": candidate_files,
        "latest_files": latest_files,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build research-driven logic graph candidates from structured report profiles.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE, help="Inclusive YYYYMMDD start date.")
    parser.add_argument("--end-date", help="Inclusive YYYYMMDD end date. Defaults to today.")
    parser.add_argument("--max-profiles", type=int, default=0, help="Optional profile limit for smoke runs.")
    parser.add_argument("--include-financial", action="store_true", help="Include financial futures and index reports.")
    args = parser.parse_args(argv)
    result = run_research_logic_graph(
        args.quanta_root,
        start_date=args.start_date,
        end_date=args.end_date,
        max_profiles=args.max_profiles,
        commodity_only=not args.include_financial,
    )
    stats = result["stats"]
    print(f"研报逻辑图谱 run → {result['run_dir']}")
    print(f"latest → {result['latest_dir']}")
    print(
        "profiles={profile_count} signals={evidence_signal_count} nodes={logic_node_count} triggers={driver_trigger_candidate_count}".format(
            **stats
        )
    )


if __name__ == "__main__":
    main()
