from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from quanta_agents.core.config import first_env, load_default_env, quanta_data_root
from quanta_agents.core.frameworks import (
    iter_leaf_nodes,
    leaf_event_terms,
    leaf_indicator_terms,
    load_framework_for_asset,
)
from quanta_agents.core.io import read_json, utc_now_iso, write_json
from quanta_agents.core.llm_client import chat
from quanta_agents.core.llm_json import parse_json_object
from quanta_agents.core.taxonomy import load_asset_taxonomy


DEFAULT_DATA_DICT_PATHS = (
    Path("/Users/miniquanta/Documents/gj_chainplatform/backend/app/data_agent/data_dict.json"),
)

DIMENSION_KEYWORDS = {
    "inventory": ("库存", "仓单", "库容", "库销比", "港口库存", "社会库存"),
    "supply": ("供给", "供应", "产量", "产能", "开工", "发运", "进口", "出口", "装置"),
    "demand": ("需求", "消费", "成交", "下游", "终端", "产销", "织机", "聚酯"),
    "cost": ("成本", "利润", "加工费", "原料", "运费", "价差"),
    "spread": ("基差", "价差", "升贴水", "月差", "期限结构"),
    "valuation": ("价格", "收盘", "主力", "持仓", "成交量", "资金"),
    "macro": ("美元", "利率", "通胀", "PMI", "社融", "汇率", "美联储"),
    "policy": ("政策", "监管", "收储", "抛储", "关税", "环保", "安监"),
    "weather": ("天气", "降水", "气温", "干旱", "洪涝"),
}

TRACKING_WORDS = ("主力合约", "成交量", "持仓量", "价格", "收盘价", "基差", "价差")
DERIVED_ANALYTIC_WORDS = ("滚动相关性", "相关性", "回归", "预测模型", "因子得分")
STRONG_MATCH_REASONS = (
    "dimension_label_overlap",
    "dimension_name_substring",
    "framework_indicator_term",
)
SOURCE_MODES = {"market_brief", "logic_chain"}


class NoDatabaseHistoryError(RuntimeError):
    """Raised when an asset has mapped indicators but no real dzq_data history."""


@dataclass(frozen=True)
class DataSeriesMeta:
    data_name: str
    category: str
    variety: str
    sheet: str
    raw_name: str
    unit: str
    row_count: int
    distinct_dates: int
    first_dt: str
    last_dt: str
    min_value: str | None = None
    max_value: str | None = None
    source_file: str | None = None


@dataclass(frozen=True)
class DataPoint:
    dt: date
    value: Decimal
    unit: str


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _tokens(value: Any) -> set[str]:
    return set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_+-]{1,}", str(value or "").lower()))


def _bounded_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def resolve_data_dict_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path).expanduser()
    explicit = first_env(
        "GJ_DATA_AGENT_DATA_DICT_PATH",
        "GJ_CHAINPLATFORM_DATA_DICT_PATH",
        "QUANTA_ALPHA_DATA_DICT_PATH",
    )
    if explicit:
        return Path(explicit).expanduser()
    for candidate in DEFAULT_DATA_DICT_PATHS:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("未找到 Alpha 问答 data_dict.json，请配置 GJ_DATA_AGENT_DATA_DICT_PATH")


def load_alpha_data_dictionary(path: str | Path | None = None) -> list[DataSeriesMeta]:
    data_path = resolve_data_dict_path(path)
    raw = json.loads(data_path.read_text(encoding="utf-8"))
    rows = []
    for data_name, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        rows.append(
            DataSeriesMeta(
                data_name=str(payload.get("data_name") or data_name),
                category=str(payload.get("category") or ""),
                variety=str(payload.get("variety") or "").strip(),
                sheet=str(payload.get("sheet") or ""),
                raw_name=str(payload.get("raw_name") or ""),
                unit=str(payload.get("unit") or ""),
                row_count=int(_bounded_float(payload.get("row_count"), 0)),
                distinct_dates=int(_bounded_float(payload.get("distinct_dates"), 0)),
                first_dt=str(payload.get("first_dt") or ""),
                last_dt=str(payload.get("last_dt") or ""),
                min_value=payload.get("min_value"),
                max_value=payload.get("max_value"),
                source_file=payload.get("source_file"),
            )
        )
    return rows


def _asset_record(asset_name: str, root: Path) -> dict[str, Any]:
    taxonomy = load_asset_taxonomy(root, required=True)
    target = _norm(asset_name)
    for asset in taxonomy.assets:
        names = [
            asset.get("canonical_name"),
            asset.get("display_name"),
            asset.get("standard_name"),
            asset.get("generic_name"),
            asset.get("commodity_code"),
            *(asset.get("aliases") or []),
        ]
        if any(_norm(name) == target for name in names if name):
            return asset
    return {"canonical_name": asset_name, "aliases": []}


def _asset_aliases(asset: dict[str, Any]) -> set[str]:
    values = [
        asset.get("canonical_name"),
        asset.get("display_name"),
        asset.get("standard_name"),
        asset.get("generic_name"),
        asset.get("commodity_code"),
        *(asset.get("aliases") or []),
    ]
    return {_norm(value) for value in values if _norm(value)}


def _dimension_label(leaf: dict[str, Any]) -> str:
    path = leaf.get("path")
    if isinstance(path, list) and path:
        return "/".join(str(item) for item in path if str(item).strip())
    return str(leaf.get("display") or leaf.get("name") or leaf.get("node_id") or "")


def load_framework_dimensions(asset_name: str, root: str | Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root_path = quanta_data_root(root)
    asset = _asset_record(asset_name, root_path)
    framework = load_framework_for_asset(asset, root_path) or {}
    dimensions = []
    for leaf in iter_leaf_nodes(framework):
        terms = []
        terms.extend(leaf_indicator_terms(leaf))
        terms.extend(leaf_event_terms(leaf))
        label = _dimension_label(leaf)
        dim_type = str(leaf.get("dimension_type") or "other")
        dimensions.append(
            {
                "dimension_id": str(leaf.get("node_id") or leaf.get("dimension_id") or label),
                "dimension_label": label,
                "dimension_name": str(leaf.get("name") or leaf.get("display") or label.rsplit("/", 1)[-1]),
                "dimension_type": dim_type,
                "terms": list(dict.fromkeys(str(term) for term in terms if str(term).strip())),
                "path": leaf.get("path") if isinstance(leaf.get("path"), list) else [],
            }
        )
    return asset, dimensions


def _series_matches_asset(meta: DataSeriesMeta, aliases: set[str]) -> bool:
    values = [_norm(meta.variety), _norm(meta.sheet), _norm(meta.data_name)]
    return any(alias and any(alias in value or value in alias for value in values if value) for alias in aliases)


def _quality_score(meta: DataSeriesMeta) -> float:
    recency = 0.0
    if meta.last_dt:
        try:
            days = (datetime.now().date() - date.fromisoformat(meta.last_dt[:10])).days
            recency = max(0.0, 16.0 - min(days, 365) / 365 * 16.0)
        except ValueError:
            recency = 0.0
    rows = min(meta.row_count, 1200) / 1200 * 10.0
    distinct = min(meta.distinct_dates, 800) / 800 * 8.0
    return recency + rows + distinct


def _dimension_match_score(meta: DataSeriesMeta, dimension: dict[str, Any]) -> tuple[float, list[str]]:
    raw = " ".join([meta.raw_name, meta.data_name, meta.sheet])
    raw_norm = _norm(raw)
    raw_tokens = _tokens(raw)
    label = str(dimension.get("dimension_label") or "")
    dim_type = str(dimension.get("dimension_type") or "other")
    terms = [str(item) for item in (dimension.get("terms") or []) if str(item).strip()]
    score = 0.0
    reasons: list[str] = []

    if any(word in raw for word in DERIVED_ANALYTIC_WORDS):
        return 0.0, ["derived_analytic_indicator_excluded"]

    for value in [label, dimension.get("dimension_name")]:
        value_norm = _norm(value)
        if value_norm and (value_norm in raw_norm or raw_norm in value_norm):
            score += 18.0
            reasons.append("dimension_name_substring")
        overlap = _tokens(value) & raw_tokens
        if overlap:
            score += min(16.0, len(overlap) * 4.0)
            reasons.append("dimension_label_overlap")

    for term in terms[:80]:
        term_norm = _norm(term)
        if not term_norm:
            continue
        if term_norm in raw_norm or raw_norm in term_norm:
            score += 28.0
            reasons.append("framework_indicator_term")
            break
        overlap = _tokens(term) & raw_tokens
        if overlap:
            score += min(10.0, len(overlap) * 3.0)

    for keyword in DIMENSION_KEYWORDS.get(dim_type, ()):
        if keyword in raw:
            score += 10.0
            reasons.append(f"type_keyword:{dim_type}")
            break

    if dim_type not in {"valuation", "spread"} and any(word in meta.raw_name for word in TRACKING_WORDS):
        score -= 10.0
        reasons.append("trading_indicator_penalty")
    if dim_type in {"valuation", "spread"} and any(word in meta.raw_name for word in TRACKING_WORDS):
        score += 6.0
        reasons.append("trading_indicator_allowed")

    score += _quality_score(meta) * 0.25
    if not any(reason in reasons for reason in STRONG_MATCH_REASONS):
        score *= 0.45
    return score, list(dict.fromkeys(reasons))


def map_indicators_to_framework(
    asset_name: str,
    *,
    root: str | Path | None = None,
    data_dict_path: str | Path | None = None,
    max_series_per_dimension: int = 4,
    max_total_series: int = 18,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    asset, dimensions = load_framework_dimensions(asset_name, root_path)
    aliases = _asset_aliases(asset)
    dictionary = load_alpha_data_dictionary(data_dict_path)
    candidates = [meta for meta in dictionary if _series_matches_asset(meta, aliases)]

    dimension_rows = []
    selected_names: set[str] = set()
    for dimension in dimensions:
        scored = []
        for meta in candidates:
            score, reasons = _dimension_match_score(meta, dimension)
            if score <= 12:
                continue
            scored.append((score, reasons, meta))
        scored.sort(key=lambda item: (item[0], item[2].last_dt, item[2].row_count), reverse=True)
        mapped = []
        for score, reasons, meta in scored[:max_series_per_dimension]:
            selected_names.add(meta.data_name)
            mapped.append(_series_payload(meta, score=score, reasons=reasons))
        dimension_rows.append(
            {
                "dimension_id": dimension.get("dimension_id"),
                "dimension_label": dimension.get("dimension_label"),
                "dimension_type": dimension.get("dimension_type"),
                "framework_terms": (dimension.get("terms") or [])[:20],
                "mapped_series": mapped,
                "coverage": "covered" if mapped else "missing_indicator",
            }
        )

    if len(selected_names) > max_total_series:
        ranked = sorted(
            (series for row in dimension_rows for series in row["mapped_series"]),
            key=lambda item: (item["score"], item["last_dt"], item["row_count"]),
            reverse=True,
        )[:max_total_series]
        keep = {item["data_name"] for item in ranked}
        for row in dimension_rows:
            row["mapped_series"] = [item for item in row["mapped_series"] if item["data_name"] in keep]
            row["coverage"] = "covered" if row["mapped_series"] else "missing_indicator"
        selected_names = keep

    unbound = [
        _series_payload(meta, score=0.0, reasons=["asset_series_not_bound"])
        for meta in candidates
        if meta.data_name not in selected_names
    ]
    type_counts = Counter(row["dimension_type"] for row in dimension_rows if row["mapped_series"])
    return {
        "schema_version": "commodity_indicator_mapping.v1",
        "status": "candidate",
        "generated_at": utc_now_iso(),
        "asset": asset.get("canonical_name") or asset_name,
        "asset_id": asset.get("asset_id"),
        "data_source": {
            "kind": "alpha_data_agent",
            "data_dict_path": str(resolve_data_dict_path(data_dict_path)),
            "mysql_table": "dzq_data",
        },
        "stats": {
            "candidate_series_count": len(candidates),
            "mapped_series_count": len(selected_names),
            "dimension_count": len(dimensions),
            "covered_dimension_count": sum(1 for row in dimension_rows if row["mapped_series"]),
            "covered_dimension_type_counts": dict(type_counts),
        },
        "dimensions": dimension_rows,
        "unbound_series": sorted(unbound, key=lambda item: (item["last_dt"], item["row_count"]), reverse=True)[:50],
    }


def _series_payload(meta: DataSeriesMeta, *, score: float, reasons: list[str]) -> dict[str, Any]:
    return {
        "data_name": meta.data_name,
        "category": meta.category,
        "variety": meta.variety,
        "sheet": meta.sheet,
        "raw_name": meta.raw_name,
        "unit": meta.unit,
        "row_count": meta.row_count,
        "distinct_dates": meta.distinct_dates,
        "first_dt": meta.first_dt,
        "last_dt": meta.last_dt,
        "score": round(score, 3),
        "reasons": reasons,
    }


def _parse_mysql_url(database_url: str) -> dict[str, Any] | None:
    if not database_url:
        return None
    parsed = urlparse(database_url)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        return None
    query = parse_qs(parsed.query)
    return {
        "host": parsed.hostname or "",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": (parsed.path or "/").lstrip("/"),
        "charset": query.get("charset", ["utf8mb4"])[0],
    }


def _mysql_config() -> dict[str, Any] | None:
    load_default_env()
    url = first_env("GJ_DATA_AGENT_DATABASE_URL", "DATABASE_URL")
    parsed = _parse_mysql_url(url)
    if parsed:
        return parsed
    host = first_env("GJ_DATA_AGENT_MYSQL_HOST", "MYSQL_HOST")
    user = first_env("GJ_DATA_AGENT_MYSQL_USER", "MYSQL_USER")
    database = first_env("GJ_DATA_AGENT_MYSQL_DATABASE", "MYSQL_DATABASE")
    if not host or not user or not database:
        return None
    return {
        "host": host,
        "port": int(first_env("GJ_DATA_AGENT_MYSQL_PORT", "MYSQL_PORT", default="3306") or "3306"),
        "user": user,
        "password": first_env("GJ_DATA_AGENT_MYSQL_PASSWORD", "MYSQL_PASSWORD"),
        "database": database,
        "charset": first_env("GJ_DATA_AGENT_MYSQL_CHARSET", "MYSQL_CHARSET", default="utf8mb4") or "utf8mb4",
    }


def fetch_recent_history(data_names: list[str], *, points_per_series: int = 520) -> tuple[dict[str, list[DataPoint]], list[str]]:
    if not data_names:
        return {}, []
    config = _mysql_config()
    if not config:
        return {name: [] for name in data_names}, ["MySQL 未配置，已仅生成指标映射。"]
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError:
        return {name: [] for name in data_names}, ["缺少 pymysql，无法拉取 dzq_data 历史序列。"]

    placeholders = ",".join(["%s"] * len(data_names))
    sql = f"""
SELECT data_name, dt, value, unit
FROM (
    SELECT data_name, dt, value, unit,
           ROW_NUMBER() OVER (PARTITION BY data_name ORDER BY dt DESC) AS rn
    FROM dzq_data
    WHERE data_name IN ({placeholders})
) AS ranked
WHERE rn <= %s
ORDER BY data_name, dt
""".strip()
    grouped: dict[str, list[DataPoint]] = {name: [] for name in data_names}
    conn = pymysql.connect(
        host=config["host"],
        port=config["port"],
        user=config["user"],
        password=config["password"],
        database=config["database"],
        charset=config["charset"],
        cursorclass=DictCursor,
        read_timeout=30,
        write_timeout=30,
        connect_timeout=15,
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql, [*data_names, points_per_series])
            for row in cur.fetchall():
                dt = row["dt"]
                if isinstance(dt, str):
                    dt = date.fromisoformat(dt)
                value = row["value"]
                if not isinstance(value, Decimal):
                    value = Decimal(str(value))
                grouped[str(row["data_name"])].append(DataPoint(dt=dt, value=value, unit=row.get("unit") or ""))
    finally:
        conn.close()
    return grouped, []


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _nearest_on_or_before(points: list[DataPoint], latest_index: int, days: int) -> DataPoint | None:
    target = points[latest_index].dt - timedelta(days=days)
    prior = None
    for point in points[:latest_index]:
        if point.dt <= target:
            prior = point
        else:
            break
    return prior


def _change(latest: DataPoint, prior: DataPoint | None) -> dict[str, Any]:
    if not prior:
        return {"previous_dt": None, "previous_value": None, "abs_change": None, "pct_change": None}
    latest_value = _float(latest.value)
    prior_value = _float(prior.value)
    abs_change = latest_value - prior_value if latest_value is not None and prior_value is not None else None
    pct_change = abs_change / abs(prior_value) if abs_change is not None and prior_value not in {None, 0} else None
    return {
        "previous_dt": prior.dt.isoformat(),
        "previous_value": prior_value,
        "abs_change": abs_change,
        "pct_change": pct_change,
    }


def _trend(points: list[DataPoint]) -> str:
    if len(points) < 6:
        return "样本不足"
    values = [_float(point.value) for point in points[-6:]]
    values = [value for value in values if value is not None]
    if len(values) < 6:
        return "样本不足"
    first = mean(values[:3])
    last = mean(values[-3:])
    if first == 0:
        return "震荡"
    diff = (last - first) / abs(first)
    if diff > 0.03:
        return "上行"
    if diff < -0.03:
        return "下行"
    return "震荡"


def build_series_stats(series: dict[str, Any], points: list[DataPoint]) -> dict[str, Any]:
    if not points:
        return {
            **series,
            "latest_dt": None,
            "latest_value": None,
            "trend": "无数据",
            "changes": {},
            "recent_points": 0,
            "recent_min": None,
            "recent_max": None,
        }
    latest = points[-1]
    values = [_float(point.value) for point in points if _float(point.value) is not None]
    return {
        **series,
        "latest_dt": latest.dt.isoformat(),
        "latest_value": _float(latest.value),
        "unit": latest.unit or series.get("unit") or "",
        "trend": _trend(points),
        "changes": {
            "d7": _change(latest, _nearest_on_or_before(points, len(points) - 1, 7)),
            "d30": _change(latest, _nearest_on_or_before(points, len(points) - 1, 30)),
            "d365": _change(latest, _nearest_on_or_before(points, len(points) - 1, 365)),
        },
        "recent_points": len(points),
        "recent_min": min(values) if values else None,
        "recent_max": max(values) if values else None,
    }


def _score_stat_signal(stats: dict[str, Any], dimension_type: str) -> float:
    change30 = ((stats.get("changes") or {}).get("d30") or {}).get("pct_change")
    if change30 is None:
        return 0.0
    raw_name = str(stats.get("raw_name") or "")
    sign = 1.0
    if any(word in raw_name for word in ["成交量", "持仓量"]):
        return 0.0
    if any(word in raw_name for word in ["库存", "仓单"]):
        sign = -1.0
    elif any(word in raw_name for word in ["价格", "结算价", "收盘价", "基差", "价差", "CIF", "CFR"]):
        sign = 1.0
    elif any(word in raw_name for word in ["开工", "产量", "进口", "到港"]):
        sign = -1.0 if dimension_type == "supply" else 1.0
    elif any(word in raw_name for word in ["需求", "消费", "产销", "成交"]):
        sign = 1.0
    elif dimension_type == "inventory":
        sign = -1.0
    return max(-10.0, min(10.0, float(change30) * 100.0 * sign))


def _fallback_report(asset: str, source_context: dict[str, Any], dimension_cards: list[dict[str, Any]]) -> dict[str, Any]:
    leading = sorted(dimension_cards, key=lambda item: abs(float(item.get("data_signal_score") or 0)), reverse=True)[:3]
    pieces = []
    for item in leading:
        stats = item.get("stats") or []
        if not stats:
            continue
        first = stats[0]
        pieces.append(f"{item.get('dimension_label')}跟踪{first.get('raw_name')}，趋势{first.get('trend')}")
    return {
        "title": f"{asset}数据简报",
        "executive_summary": _source_context_summary(asset, source_context),
        "data_view": "；".join(pieces) if pieces else "当前指标覆盖不足，需先补充数据绑定。",
        "fundamental_alignment": "数据简报已按品种分析框架维度归类，可与期市速递主线逐项校验。",
        "conflicts": [],
        "tracking_indicators": [
            stats[0]["raw_name"]
            for stats in [item.get("stats") or [] for item in leading]
            if stats
        ],
        "llm_used": False,
    }


def _llm_report(
    asset: str,
    source_context: dict[str, Any],
    dimension_cards: list[dict[str, Any]],
    *,
    llm_provider: str | None = None,
) -> dict[str, Any]:
    payload = {
        "asset": asset,
        "futures_daily_market_brief": source_context,
        "dimension_data_cards": [
            {
                "dimension_label": item.get("dimension_label"),
                "dimension_type": item.get("dimension_type"),
                "data_signal_score": item.get("data_signal_score"),
                "stats": [
                    {
                        "raw_name": stat.get("raw_name"),
                        "latest_dt": stat.get("latest_dt"),
                        "latest_value": stat.get("latest_value"),
                        "unit": stat.get("unit"),
                        "trend": stat.get("trend"),
                        "changes": stat.get("changes"),
                    }
                    for stat in (item.get("stats") or [])[:4]
                ],
            }
            for item in dimension_cards[:10]
        ],
    }
    prompt = (
        "你是商品期货数据研究员。请结合期市速递的品种逻辑信息和结构化数据库指标，生成品种数据简报。\n"
        "注意：本轮不使用期市逻辑链 trade_thesis 或 logic_chains，只以期市速递 JSON 与数据库指标为依据。\n"
        "要求：只使用输入数据；不要编造数据库之外的数据；先给结论，再说明数据验证或反证；"
        "明确哪些维度需要继续跟踪。严格输出 JSON："
        '{"title":"","executive_summary":"","data_view":"","fundamental_alignment":"",'
        '"conflicts":[""],"tracking_indicators":[""]}\n\n'
        + json.dumps(payload, ensure_ascii=False)
    )
    parsed = parse_json_object(
        chat(prompt, max_tokens=2200, temperature=0.2, timeout=120, provider=llm_provider)
    )
    return {
        "title": str(parsed.get("title") or f"{asset}数据简报"),
        "executive_summary": str(parsed.get("executive_summary") or ""),
        "data_view": str(parsed.get("data_view") or ""),
        "fundamental_alignment": str(parsed.get("fundamental_alignment") or ""),
        "conflicts": parsed.get("conflicts") if isinstance(parsed.get("conflicts"), list) else [],
        "tracking_indicators": parsed.get("tracking_indicators") if isinstance(parsed.get("tracking_indicators"), list) else [],
        "llm_used": True,
    }


def _latest_logic_run_dir(root: Path, date_key: str | None = None) -> Path | None:
    base = root / "agent_workspace" / "candidates" / "futures_daily_raw_runs"
    manifests = list(base.glob("*/*/*/RUN-*/manifest.json"))
    if date_key:
        clean = date_key.replace("-", "")
        manifests = [p for p in manifests if "".join(p.relative_to(base).parts[:3]) == clean]
    candidates = []
    for manifest_path in manifests:
        try:
            manifest = read_json(manifest_path)
        except Exception:
            continue
        outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
        if str(manifest.get("status") or "").startswith("aborted"):
            continue
        if {"summary", "trade_thesis", "logic_chains"} <= set(outputs):
            candidates.append((str(manifest.get("generated_at") or ""), manifest_path.parent))
    return sorted(candidates)[-1][1] if candidates else None


def _source_context_summary(asset: str, source_context: dict[str, Any]) -> str:
    if source_context.get("source_mode") == "market_brief":
        analysis = source_context.get("analysis") if isinstance(source_context.get("analysis"), dict) else {}
        summary = str(analysis.get("fundamental_summary") or "").strip()
        if summary:
            return summary
        bullish = analysis.get("bullish_factors") if isinstance(analysis.get("bullish_factors"), list) else []
        bearish = analysis.get("bearish_factors") if isinstance(analysis.get("bearish_factors"), list) else []
        return f"{asset}期市速递：利多 {len(bullish)} 条，利空 {len(bearish)} 条。"
    logic = source_context.get("analysis") if isinstance(source_context.get("analysis"), dict) else source_context
    return str(logic.get("main_trade_thesis") or f"{asset}暂无期市逻辑链主线。")


def _summary_output_path(run_dir: Path) -> Path | None:
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = read_json(manifest_path)
            outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
            relative = outputs.get("summary")
            if relative:
                candidate = run_dir / str(relative)
                if candidate.exists():
                    return candidate
        except Exception:
            pass
    matches = sorted(run_dir.glob("*_commodity_summary.json"))
    return matches[-1] if matches else None


def _load_market_brief_summary(run_dir: Path) -> dict[str, Any]:
    path = _summary_output_path(run_dir)
    if not path:
        return {}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def _market_brief_assets(run_dir: Path) -> list[str]:
    summary = _load_market_brief_summary(run_dir)
    detailed = summary.get("detailed_analysis") if isinstance(summary.get("detailed_analysis"), dict) else {}
    return [str(asset) for asset in detailed.keys()]


def _load_market_brief_context(run_dir: Path, asset: str) -> dict[str, Any]:
    summary = _load_market_brief_summary(run_dir)
    detailed = summary.get("detailed_analysis") if isinstance(summary.get("detailed_analysis"), dict) else {}
    analysis = detailed.get(asset) if isinstance(detailed.get(asset), dict) else {}
    return {
        "source_mode": "market_brief",
        "source_name": "期市速递",
        "date": summary.get("date"),
        "title": summary.get("title"),
        "org_name": summary.get("org_name"),
        "sentiment_score": (summary.get("sentiment_scores") or {}).get(asset)
        if isinstance(summary.get("sentiment_scores"), dict)
        else analysis.get("sentiment_score"),
        "analysis": {
            key: analysis.get(key)
            for key in [
                "commodity",
                "fundamental_summary",
                "bullish_factors",
                "bearish_factors",
                "key_data",
                "key_events",
                "supply_demand",
                "price_forecast",
                "sentiment_score",
                "weighted_sentiment_score",
            ]
            if key in analysis
        },
    }


def _load_trade_thesis(run_dir: Path, asset: str) -> dict[str, Any]:
    path = run_dir / "trade_thesis.json"
    if not path.exists():
        return {}
    payload = read_json(path)
    assets = payload.get("assets") if isinstance(payload.get("assets"), dict) else {}
    analysis = assets.get(asset) if isinstance(assets.get(asset), dict) else {}
    return {
        "source_mode": "logic_chain",
        "source_name": "期市逻辑链",
        "analysis": analysis,
        **analysis,
    }


def _trade_thesis_assets(run_dir: Path) -> list[str]:
    path = run_dir / "trade_thesis.json"
    if not path.exists():
        return []
    payload = read_json(path)
    assets = payload.get("assets") if isinstance(payload.get("assets"), dict) else {}
    return [str(asset) for asset in assets.keys()]


def _source_assets(run_dir: Path, source_mode: str) -> list[str]:
    if source_mode == "market_brief":
        return _market_brief_assets(run_dir)
    return _trade_thesis_assets(run_dir)


def _load_source_context(run_dir: Path, asset: str, source_mode: str) -> dict[str, Any]:
    if source_mode == "market_brief":
        return _load_market_brief_context(run_dir, asset)
    return _load_trade_thesis(run_dir, asset)


def discover_assets_with_alpha_data(
    *,
    root: str | Path | None = None,
    run_dir: str | Path | None = None,
    date_key: str | None = None,
    data_dict_path: str | Path | None = None,
    source_mode: str = "market_brief",
) -> dict[str, Any]:
    """Find futures-daily assets that have at least one Alpha data-agent series."""
    if source_mode not in SOURCE_MODES:
        raise ValueError(f"unsupported source_mode: {source_mode}")
    root_path = quanta_data_root(root)
    selected_run_dir = Path(run_dir).expanduser() if run_dir else _latest_logic_run_dir(root_path, date_key)
    if not selected_run_dir:
        raise FileNotFoundError("未找到可用期市速递 run")

    dictionary = load_alpha_data_dictionary(data_dict_path)
    rows = []
    skipped = []
    source_assets = _source_assets(selected_run_dir, source_mode)
    for asset_name in source_assets:
        try:
            asset_record, _ = load_framework_dimensions(asset_name, root_path)
            aliases = _asset_aliases(asset_record)
            candidate_count = sum(1 for meta in dictionary if _series_matches_asset(meta, aliases))
        except Exception as exc:
            skipped.append({"asset": asset_name, "error": str(exc)})
            continue
        if candidate_count:
            rows.append({"asset": asset_name, "candidate_series_count": candidate_count})
    return {
        "run_dir": str(selected_run_dir),
        "assets": rows,
        "skipped": skipped,
        "stats": {
            "source_asset_count": len(source_assets),
            "covered_asset_count": len(rows),
            "skipped_count": len(skipped),
        },
    }


def build_commodity_data_brief(
    asset: str,
    *,
    root: str | Path | None = None,
    run_dir: str | Path | None = None,
    date_key: str | None = None,
    data_dict_path: str | Path | None = None,
    points_per_series: int = 520,
    use_llm: bool = True,
    llm_provider: str | None = None,
    source_mode: str = "market_brief",
    require_chart_data: bool = True,
) -> dict[str, Any]:
    if source_mode not in SOURCE_MODES:
        raise ValueError(f"unsupported source_mode: {source_mode}")
    root_path = quanta_data_root(root)
    selected_run_dir = Path(run_dir).expanduser() if run_dir else _latest_logic_run_dir(root_path, date_key)
    if not selected_run_dir:
        raise FileNotFoundError("未找到可用期市逻辑链 run")

    mapping = map_indicators_to_framework(asset, root=root_path, data_dict_path=data_dict_path)
    selected_series = []
    for row in mapping["dimensions"]:
        selected_series.extend(row.get("mapped_series") or [])
    selected_series = list({item["data_name"]: item for item in selected_series}.values())
    histories, warnings = fetch_recent_history(
        [item["data_name"] for item in selected_series],
        points_per_series=points_per_series,
    )

    dimension_cards = []
    chart_series_by_name: dict[str, dict[str, Any]] = {}
    for row in mapping["dimensions"]:
        stats_rows = []
        for series in row.get("mapped_series") or []:
            points = histories.get(series["data_name"], [])
            stats = build_series_stats(series, points)
            stats["data_signal_score"] = round(_score_stat_signal(stats, str(row.get("dimension_type") or "")), 3)
            stats_rows.append(stats)
            if points and series["data_name"] not in chart_series_by_name:
                chart_series_by_name[series["data_name"]] = (
                    {
                        "data_name": series["data_name"],
                        "dimension_id": row.get("dimension_id"),
                        "dimension_label": row.get("dimension_label"),
                        "raw_name": series["raw_name"],
                        "unit": stats.get("unit") or series.get("unit") or "",
                        "points": [
                            {"dt": point.dt.isoformat(), "value": float(point.value), "unit": point.unit}
                            for point in points
                        ],
                    }
                )
        if not stats_rows and not row.get("mapped_series"):
            continue
        signal_values = [float(item.get("data_signal_score") or 0) for item in stats_rows]
        dimension_cards.append(
            {
                "dimension_id": row.get("dimension_id"),
                "dimension_label": row.get("dimension_label"),
                "dimension_type": row.get("dimension_type"),
                "coverage": row.get("coverage"),
                "data_signal_score": round(mean(signal_values), 3) if signal_values else 0.0,
                "stats": stats_rows,
                "mapped_series_count": len(row.get("mapped_series") or []),
            }
        )

    source_context = _load_source_context(selected_run_dir, asset, source_mode)
    chart_series = list(chart_series_by_name.values())[:24]
    if require_chart_data and not chart_series:
        raise NoDatabaseHistoryError(f"{asset} 映射指标在 dzq_data 中没有可用历史序列")
    try:
        report = (
            _llm_report(asset, source_context, dimension_cards, llm_provider=llm_provider)
            if use_llm
            else _fallback_report(asset, source_context, dimension_cards)
        )
    except Exception as exc:
        warnings.append(f"LLM 数据简报生成失败，已使用规则摘要：{exc}")
        report = _fallback_report(asset, source_context, dimension_cards)

    data_signal_values = [float(item.get("data_signal_score") or 0) for item in dimension_cards]
    return {
        "schema_version": "commodity_data_brief.v1",
        "status": "candidate",
        "generated_at": utc_now_iso(),
        "asset": asset,
        "source": {
            "futures_daily_run_dir": str(selected_run_dir),
            "source_mode": source_mode,
            "market_brief_summary": str(_summary_output_path(selected_run_dir) or ""),
            "data_dict_path": str(resolve_data_dict_path(data_dict_path)),
            "mysql_table": "dzq_data",
        },
        "summary": {
            "dimension_count": len(mapping["dimensions"]),
            "mapped_series_count": mapping["stats"]["mapped_series_count"],
            "chart_series_count": len(chart_series),
            "data_signal_score": round(mean(data_signal_values), 3) if data_signal_values else 0.0,
            "warnings": warnings,
        },
        "logic_context": source_context,
        "indicator_mapping": mapping,
        "dimension_data_cards": sorted(
            dimension_cards,
            key=lambda item: (abs(float(item.get("data_signal_score") or 0)), item.get("mapped_series_count") or 0),
            reverse=True,
        ),
        "chart_series": chart_series,
        "report": report,
    }


def publish_commodity_data_brief(
    asset: str,
    *,
    root: str | Path | None = None,
    run_dir: str | Path | None = None,
    date_key: str | None = None,
    output_dir: str | Path | None = None,
    data_dict_path: str | Path | None = None,
    points_per_series: int = 520,
    use_llm: bool = True,
    llm_provider: str | None = None,
    source_mode: str = "market_brief",
    require_chart_data: bool = True,
) -> Path:
    root_path = quanta_data_root(root)
    payload = build_commodity_data_brief(
        asset,
        root=root_path,
        run_dir=run_dir,
        date_key=date_key,
        data_dict_path=data_dict_path,
        points_per_series=points_per_series,
        use_llm=use_llm,
        llm_provider=llm_provider,
        source_mode=source_mode,
        require_chart_data=require_chart_data,
    )
    selected_run_dir = Path(str(payload["source"]["futures_daily_run_dir"]))
    default_folder = "commodity_data_briefs_market_brief" if source_mode == "market_brief" else "commodity_data_briefs"
    target_dir = Path(output_dir).expanduser() if output_dir else selected_run_dir / default_folder
    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = target_dir / f"{asset}.json"
    write_json(output_path, payload)
    return output_path


def publish_all_commodity_data_briefs(
    *,
    root: str | Path | None = None,
    run_dir: str | Path | None = None,
    date_key: str | None = None,
    output_dir: str | Path | None = None,
    data_dict_path: str | Path | None = None,
    points_per_series: int = 520,
    use_llm: bool = True,
    llm_provider: str | None = None,
    max_assets: int | None = None,
    source_mode: str = "market_brief",
    require_chart_data: bool = True,
) -> Path:
    root_path = quanta_data_root(root)
    discovery = discover_assets_with_alpha_data(
        root=root_path,
        run_dir=run_dir,
        date_key=date_key,
        data_dict_path=data_dict_path,
        source_mode=source_mode,
    )
    selected_run_dir = Path(str(discovery["run_dir"]))
    default_folder = "commodity_data_briefs_market_brief" if source_mode == "market_brief" else "commodity_data_briefs"
    target_dir = Path(output_dir).expanduser() if output_dir else selected_run_dir / default_folder
    target_dir.mkdir(parents=True, exist_ok=True)
    assets = [str(row["asset"]) for row in discovery["assets"]]
    if max_assets is not None:
        assets = assets[: max(0, max_assets)]

    results = []
    errors = []
    for index, asset_name in enumerate(assets, start=1):
        print(f"[{index}/{len(assets)}] {asset_name}", flush=True)
        try:
            payload = build_commodity_data_brief(
                asset_name,
                root=root_path,
                run_dir=selected_run_dir,
                data_dict_path=data_dict_path,
                points_per_series=points_per_series,
                use_llm=use_llm,
                llm_provider=llm_provider,
                source_mode=source_mode,
                require_chart_data=require_chart_data,
            )
            output_path = target_dir / f"{asset_name}.json"
            write_json(output_path, payload)
            summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
            results.append(
                {
                    "asset": asset_name,
                    "path": str(output_path),
                    "mapped_series_count": summary.get("mapped_series_count"),
                    "chart_series_count": summary.get("chart_series_count"),
                    "data_signal_score": summary.get("data_signal_score"),
                    "warnings": summary.get("warnings") if isinstance(summary.get("warnings"), list) else [],
                }
            )
        except NoDatabaseHistoryError as exc:
            errors.append({"asset": asset_name, "error_type": "no_database_history", "error": str(exc)})
        except Exception as exc:
            errors.append({"asset": asset_name, "error_type": "runtime_error", "error": str(exc)})

    manifest = {
        "schema_version": "commodity_data_brief_batch.v1",
        "status": "candidate",
        "generated_at": utc_now_iso(),
        "run_dir": str(selected_run_dir),
        "output_dir": str(target_dir),
        "data_dict_path": str(resolve_data_dict_path(data_dict_path)),
        "source_mode": source_mode,
        "llm_used": use_llm,
        "llm_provider": llm_provider or "",
        "stats": {
            **discovery["stats"],
            "requested_asset_count": len(assets),
            "succeeded_count": len(results),
            "failed_count": len(errors),
            "with_chart_series_count": sum(1 for item in results if int(item.get("chart_series_count") or 0) > 0),
            "no_database_history_count": sum(1 for item in errors if item.get("error_type") == "no_database_history"),
        },
        "assets": results,
        "errors": errors,
        "discovery_skipped": discovery.get("skipped") or [],
    }
    manifest_path = target_dir / "commodity_data_briefs_manifest.json"
    write_json(manifest_path, manifest)
    return manifest_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate commodity data brief from Alpha data indicators.")
    parser.add_argument("--asset", help="标准品种名，例如 石油沥青、PTA。")
    parser.add_argument("--all-assets", action="store_true", help="对期市速递中有 Alpha 指标数据的品种批量生成。")
    parser.add_argument("--quanta-root", help="quanta_data root.")
    parser.add_argument("--run-dir", help="期市速递 run 目录；不传则取指定日期最新 run。")
    parser.add_argument("--date", help="YYYYMMDD，用于选择最新期市速递 run。")
    parser.add_argument("--data-dict-path", help="Alpha 问答 data_dict.json 路径。")
    parser.add_argument("--output-dir", help="输出目录；默认 market_brief 写入 run/commodity_data_briefs_market_brief。")
    parser.add_argument("--points-per-series", type=int, default=520)
    parser.add_argument("--llm-provider", help="LLM provider，例如 m3、deepseek；默认读取 QUANTA_AGENT_LLM_PROVIDER。")
    parser.add_argument("--max-assets", type=int, help="调试用：最多生成多少个品种。")
    parser.add_argument("--source-mode", choices=sorted(SOURCE_MODES), default="market_brief")
    parser.add_argument("--allow-empty-history", action="store_true", help="允许无 dzq_data 历史序列的品种生成空图表简报。")
    parser.add_argument("--no-llm", action="store_true", help="只生成规则摘要。")
    args = parser.parse_args(argv)
    if args.all_assets:
        path = publish_all_commodity_data_briefs(
            root=args.quanta_root,
            run_dir=args.run_dir,
            date_key=args.date,
            output_dir=args.output_dir,
            data_dict_path=args.data_dict_path,
            points_per_series=args.points_per_series,
            use_llm=not args.no_llm,
            llm_provider=args.llm_provider,
            max_assets=args.max_assets,
            source_mode=args.source_mode,
            require_chart_data=not args.allow_empty_history,
        )
        print(path)
        return
    if not args.asset:
        parser.error("--asset 或 --all-assets 必须指定一个")
    path = publish_commodity_data_brief(
        args.asset,
        root=args.quanta_root,
        run_dir=args.run_dir,
        date_key=args.date,
        output_dir=args.output_dir,
        data_dict_path=args.data_dict_path,
        points_per_series=args.points_per_series,
        use_llm=not args.no_llm,
        llm_provider=args.llm_provider,
        source_mode=args.source_mode,
        require_chart_data=not args.allow_empty_history,
    )
    print(path)


if __name__ == "__main__":
    main()
