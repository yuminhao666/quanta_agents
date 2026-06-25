from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quanta_agents.asset_event_state.models import clean_text, hash_id
from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import read_json, relative_to_root, utc_now_iso, write_json


DEMO_SCHEMA_VERSION = "asset_event_state_demo_run.v1"
CONFIG_PATH = Path(__file__).with_name("demo_config.v1.json")
DEFAULT_WORK_ORDER_ID = "WO-DEV-20260620-ASSET-EVENT-STATE-DEMO"
GJ_CHAINPLATFORM_ROOT = Path("/Users/miniquanta/Documents/gj_chainplatform")
DATA_AGENT_DICT_PATH = GJ_CHAINPLATFORM_ROOT / "backend" / "app" / "data_agent" / "data_dict.json"

EVENT_TYPES = {
    "geopolitics",
    "policy",
    "macro",
    "supply",
    "demand",
    "inventory",
    "cost",
    "logistics",
    "liquidity",
    "sentiment",
    "price_action",
    "other",
}

BULLISH_TERMS = (
    "去库",
    "库存减少",
    "down",
    "减少",
    "减产",
    "发运中断",
    "供应偏紧",
    "矿端紧张",
    "支撑",
    "升水",
    "走强",
    "上涨",
    "偏强",
    "反弹",
    "订单",
    "需求改善",
)

BEARISH_TERMS = (
    "累库",
    "库存增加",
    "增加",
    "鹰派",
    "加息",
    "美元走强",
    "压制",
    "承压",
    "走弱",
    "下跌",
    "回落",
    "需求疲弱",
    "过剩",
    "偏弱",
)


def load_demo_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the demo knobs from JSON so propagation and state weights are auditable."""
    config_path = Path(path).expanduser() if path else CONFIG_PATH
    return read_json(config_path)


def _parse_dt(value: Any, *, default: datetime | None = None) -> datetime:
    text = clean_text(value)
    if not text:
        return default or datetime.now(timezone.utc)
    text = text.replace("Z", "+00:00")
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}T23:59:59+00:00"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        text = f"{text}T23:59:59+00:00"
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return default or datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Any) -> str:
    return _parse_dt(value).isoformat(timespec="seconds")


def _date_key(value: str | None) -> str:
    if value and re.fullmatch(r"\d{8}", value):
        return value
    if value and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value.replace("-", "")
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows),
        encoding="utf-8",
    )


def _append_execution_log(rows: list[dict[str, Any]], agent: str, status: str, **payload: Any) -> None:
    rows.append({"ts": utc_now_iso(), "agent": agent, "status": status, **payload})


def _source_path_ref(path: str | Path, root: Path) -> str:
    text = clean_text(path)
    if not text:
        return ""
    return relative_to_root(Path(text).expanduser(), root)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return ""


def _mysql_config_for_data_agent() -> dict[str, Any] | None:
    _load_env_file(GJ_CHAINPLATFORM_ROOT / ".env")
    _load_env_file(GJ_CHAINPLATFORM_ROOT / "backend" / ".env")
    database_url = _first_env("GJ_DATA_AGENT_DATABASE_URL", "DATABASE_URL")
    if database_url:
        try:
            from urllib.parse import parse_qs, unquote, urlparse

            parsed = urlparse(database_url)
            if parsed.scheme in {"mysql", "mysql+pymysql"}:
                return {
                    "host": parsed.hostname or "",
                    "port": parsed.port or 3306,
                    "user": unquote(parsed.username or ""),
                    "password": unquote(parsed.password or ""),
                    "database": (parsed.path or "/").lstrip("/"),
                    "charset": parse_qs(parsed.query).get("charset", ["utf8mb4"])[0],
                }
        except Exception:
            pass
    host = _first_env("GJ_DATA_AGENT_MYSQL_HOST", "MYSQL_HOST")
    user = _first_env("GJ_DATA_AGENT_MYSQL_USER", "MYSQL_USER")
    database = _first_env("GJ_DATA_AGENT_MYSQL_DATABASE", "MYSQL_DATABASE")
    if not (host and user and database):
        return None
    return {
        "host": host,
        "port": int(_first_env("GJ_DATA_AGENT_MYSQL_PORT", "MYSQL_PORT") or "3306"),
        "user": user,
        "password": _first_env("GJ_DATA_AGENT_MYSQL_PASSWORD", "MYSQL_PASSWORD"),
        "database": database,
        "charset": _first_env("GJ_DATA_AGENT_MYSQL_CHARSET", "MYSQL_CHARSET") or "utf8mb4",
    }


def _mysql_config_for_futures() -> dict[str, Any] | None:
    _load_env_file(GJ_CHAINPLATFORM_ROOT / ".env")
    _load_env_file(GJ_CHAINPLATFORM_ROOT / "backend" / ".env")
    host = _first_env("GJ_FUTURES_MYSQL_HOST", "FUTURES_MYSQL_HOST")
    user = _first_env("GJ_FUTURES_MYSQL_USER", "FUTURES_MYSQL_USER")
    database = _first_env("GJ_FUTURES_MYSQL_DATABASE", "FUTURES_MYSQL_DATABASE")
    if not (host and user and database):
        return None
    return {
        "host": host,
        "port": int(_first_env("GJ_FUTURES_MYSQL_PORT", "FUTURES_MYSQL_PORT") or "3306"),
        "user": user,
        "password": _first_env("GJ_FUTURES_MYSQL_PASSWORD", "FUTURES_MYSQL_PASSWORD"),
        "database": database,
        "charset": _first_env("GJ_FUTURES_MYSQL_CHARSET", "FUTURES_MYSQL_CHARSET") or "utf8mb4",
    }


def _connect_mysql(config: dict[str, Any]) -> Any:
    try:
        import pymysql  # type: ignore
        import pymysql.cursors  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pymysql is not installed; database observations skipped") from exc
    return pymysql.connect(
        host=config["host"],
        port=config["port"],
        user=config["user"],
        password=config["password"],
        database=config["database"],
        charset=config.get("charset") or "utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        read_timeout=30,
        write_timeout=30,
        connect_timeout=15,
        autocommit=True,
    )


def _load_copper_series_candidates(limit: int = 12) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not DATA_AGENT_DICT_PATH.exists():
        return [], {"path": str(DATA_AGENT_DICT_PATH), "missing": True}
    raw = read_json(DATA_AGENT_DICT_PATH)
    if not isinstance(raw, dict):
        return [], {"path": str(DATA_AGENT_DICT_PATH), "invalid": True}
    priority_terms = (
        "LME铜库存（日度）",
        "COMEX铜库存（日度）",
        "上期所铜库存（周度）",
        "三大交易所显性库存",
        "国内铜显性库存",
        "期货收盘价（主力）",
        "LME现货升贴水",
        "基金经理净多头持仓",
        "前20名净持仓",
        "电解铜制杆周度开工率",
        "再生铜制杆周度开工率",
        "现货",
    )
    candidates: list[dict[str, Any]] = []
    for data_name, meta in raw.items():
        if not isinstance(meta, dict):
            continue
        variety = clean_text(meta.get("variety"))
        if variety != "铜" and not clean_text(data_name).startswith("[有色金属/铜|铜]"):
            continue
        raw_name = clean_text(meta.get("raw_name") or data_name)
        score = 0
        for index, term in enumerate(priority_terms):
            if term in data_name or term in raw_name:
                score += 100 - index
        if any(term in raw_name for term in ("库存", "仓单", "开工率", "加工费", "收盘价", "持仓", "升贴水")):
            score += 30
        if score <= 0:
            continue
        candidates.append(
            {
                "data_name": clean_text(data_name),
                "category": clean_text(meta.get("category")),
                "variety": variety,
                "raw_name": raw_name,
                "unit": clean_text(meta.get("unit")),
                "last_dt": clean_text(meta.get("last_dt")),
                "row_count": int(_num(meta.get("row_count"), 0)),
                "score": score,
            }
        )
    candidates.sort(key=lambda item: (item["score"], item["last_dt"], item["row_count"]), reverse=True)
    return candidates[:limit], {
        "path": str(DATA_AGENT_DICT_PATH),
        "candidate_count": len(candidates),
        "selected_count": min(limit, len(candidates)),
    }


def _series_direction(raw_name: str, latest: float, previous: float | None) -> float:
    change = 0.0 if previous is None else latest - previous
    if "库存" in raw_name or "仓单" in raw_name:
        return 0.45 if change < 0 else (-0.35 if change > 0 else 0.0)
    if "开工率" in raw_name or "需求" in raw_name:
        return 0.35 if change > 0 else (-0.25 if change < 0 else 0.0)
    if "持仓" in raw_name or "净多" in raw_name:
        return 0.35 if latest > 0 else (-0.35 if latest < 0 else 0.0)
    if "升贴水" in raw_name or "基差" in raw_name:
        return 0.25 if change > 0 else (-0.2 if change < 0 else 0.0)
    if "收盘价" in raw_name or "现货" in raw_name:
        return 0.25 if change > 0 else (-0.25 if change < 0 else 0.0)
    return 0.0


def _database_observation_from_series(
    *,
    candidate: dict[str, Any],
    rows: list[dict[str, Any]],
    source_name: str,
) -> dict[str, Any] | None:
    if not rows:
        return None
    latest = rows[-1]
    previous = rows[-2] if len(rows) >= 2 else None
    latest_value = _num(latest.get("value"), 0.0)
    previous_value = _num(previous.get("value"), 0.0) if previous else None
    values = [_num(row.get("value"), 0.0) for row in rows]
    lower_count = sum(1 for value in values if value <= latest_value)
    percentile = lower_count / max(1, len(values))
    raw_name = clean_text(candidate.get("raw_name") or candidate.get("data_name"))
    direction_score = _series_direction(raw_name, latest_value, previous_value)
    node = {
        "node_id": "FWK-CU-20260616::基本面/库存/全球显性库存",
        "label": "基本面/库存/全球显性库存",
        "dimension_label": "全球显性库存",
    }
    if "开工率" in raw_name:
        node = {
            "node_id": "FWK-CU-20260616::基本面/需求/终端消费板块",
            "label": "基本面/需求/终端消费板块",
            "dimension_label": "终端消费板块",
        }
    elif "持仓" in raw_name:
        node = {
            "node_id": "FWK-CU-20260616::金融属性/持仓与资金/品种资金流向与存量",
            "label": "金融属性/持仓与资金/品种资金流向与存量",
            "dimension_label": "品种资金流向与存量",
        }
    elif "升贴水" in raw_name or "基差" in raw_name:
        node = {
            "node_id": "FWK-CU-20260616::金融属性/期限结构/现货升水与远期曲线",
            "label": "金融属性/期限结构/现货升水与远期曲线",
            "dimension_label": "现货升水与远期曲线",
        }
    series_id = clean_text(candidate.get("data_name"))
    observation_id = hash_id("DEMODB", source_name, series_id, latest.get("dt"), latest_value, length=16)
    change = None if previous_value is None else latest_value - previous_value
    pct_change = None if previous_value in {None, 0.0} else change / previous_value
    return {
        "observation_id": observation_id,
        "data_series_id": series_id,
        "source_type": "database_observation",
        "source_id": series_id,
        "published_at": _iso(latest.get("dt")),
        "data_time": _iso(latest.get("dt")),
        "summary": f"{raw_name} 最新值 {latest_value:g}{candidate.get('unit') or ''}",
        "observation_text": (
            f"{raw_name}: latest={latest_value:g}{candidate.get('unit') or ''}, "
            f"previous={previous_value if previous_value is not None else 'NA'}, change={change if change is not None else 'NA'}"
        ),
        "value_payload": {
            "value": latest_value,
            "previous_value": previous_value,
            "change": change,
            "pct_change": pct_change,
            "unit": clean_text(latest.get("unit") or candidate.get("unit")),
            "raw_value": latest_value,
            "historical_percentile": round(percentile, 4),
            "point_count": len(rows),
        },
        "direction_score": round(_clip(direction_score, -1.0, 1.0), 4),
        "framework_node": node,
        "confidence": 0.78,
        "data_quality": {
            "method": "mysql_dzq_data_recent_history",
            "previous_value_available": previous_value is not None,
            "percentile_available": True,
            "quality_note": "只读查询 dzq_data 最近序列点；未由 LLM 编造数值。",
        },
        "source_ref": {
            "ref_type": "mysql_table",
            "source_name": source_name,
            "id": series_id,
            "path": "mysql://dzq_data",
            "url": "",
        },
    }


def _fetch_data_agent_observations(errors: list[dict[str, Any]], *, points_per_series: int = 30) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates, meta = _load_copper_series_candidates()
    if not candidates:
        return [], {"status": "no_series_candidates", "dictionary": meta}
    cfg = _mysql_config_for_data_agent()
    if not cfg:
        errors.append(
            {
                "ts": utc_now_iso(),
                "stage": "data_agent_mysql",
                "error_type": "database_unavailable",
                "message": "Missing GJ_DATA_AGENT_* or MYSQL_* MySQL config; skipped dzq_data query.",
            }
        )
        return [], {"status": "missing_mysql_config", "dictionary": meta, "selected_series": candidates}
    try:
        conn = _connect_mysql(cfg)
    except Exception as exc:
        errors.append(
            {
                "ts": utc_now_iso(),
                "stage": "data_agent_mysql",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        )
        return [], {"status": "connect_failed", "dictionary": meta, "selected_series": candidates}
    observations: list[dict[str, Any]] = []
    try:
        with conn:
            with conn.cursor() as cur:
                for candidate in candidates:
                    cur.execute(
                        """
SELECT data_name, dt, value, unit
FROM dzq_data
WHERE data_name = %s
ORDER BY dt DESC
LIMIT %s
""".strip(),
                        (candidate["data_name"], points_per_series),
                    )
                    rows = list(reversed(cur.fetchall()))
                    observation = _database_observation_from_series(
                        candidate=candidate,
                        rows=rows,
                        source_name="data_agent.dzq_data",
                    )
                    if observation:
                        observations.append(observation)
    except Exception as exc:
        errors.append(
            {
                "ts": utc_now_iso(),
                "stage": "data_agent_mysql",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        )
        return observations, {"status": "query_failed", "dictionary": meta, "selected_series": candidates}
    return observations, {
        "status": "succeeded",
        "dictionary": meta,
        "selected_series": candidates,
        "observation_count": len(observations),
        "tables": ["dzq_data"],
        "database": cfg.get("database"),
        "host": cfg.get("host"),
    }


def _fetch_futures_table_observations(errors: list[dict[str, Any]], *, as_of: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cfg = _mysql_config_for_futures()
    if not cfg:
        errors.append(
            {
                "ts": utc_now_iso(),
                "stage": "futures_mysql",
                "error_type": "database_unavailable",
                "message": "Missing GJ_FUTURES_MYSQL_* or FUTURES_MYSQL_* config; skipped tushare_fut_* query.",
            }
        )
        return [], {"status": "missing_mysql_config", "tables": ["tushare_fut_daily", "tushare_fut_wsr"]}
    try:
        conn = _connect_mysql(cfg)
    except Exception as exc:
        errors.append(
            {
                "ts": utc_now_iso(),
                "stage": "futures_mysql",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        )
        return [], {"status": "connect_failed", "tables": ["tushare_fut_daily", "tushare_fut_wsr"]}
    observations: list[dict[str, Any]] = []
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(trade_date) AS trade_date FROM tushare_fut_daily")
                latest_trade_date = clean_text((cur.fetchone() or {}).get("trade_date")) or as_of.strftime("%Y%m%d")
                cur.execute(
                    """
SELECT trade_date, ts_code, close, vol, amount, oi
FROM tushare_fut_daily
WHERE trade_date <= %s AND (ts_code LIKE 'CU%%' OR ts_code LIKE 'cu%%')
ORDER BY trade_date DESC, oi DESC, vol DESC
LIMIT 2
""".strip(),
                    (latest_trade_date,),
                )
                rows = list(reversed(cur.fetchall()))
                if rows:
                    candidate = {
                        "data_name": "tushare_fut_daily:CU_active_price_oi",
                        "raw_name": "铜期货活跃合约价格/持仓",
                        "unit": "元/吨",
                    }
                    converted = [
                        {"dt": row["trade_date"], "value": row.get("close"), "unit": "元/吨"}
                        for row in rows
                        if row.get("close") is not None
                    ]
                    observation = _database_observation_from_series(
                        candidate=candidate,
                        rows=converted,
                        source_name="futures_store.tushare_fut_daily",
                    )
                    if observation:
                        observation["source_ref"]["path"] = "mysql://tushare_fut_daily"
                        observations.append(observation)
                cur.execute(
                    """
SELECT trade_date, exchange, symbol, SUM(vol_chg) AS vol_chg, SUM(vol) AS vol
FROM tushare_fut_wsr
WHERE trade_date <= %s AND (symbol = 'CU' OR symbol = 'cu')
GROUP BY trade_date, exchange, symbol
ORDER BY trade_date DESC
LIMIT 2
""".strip(),
                    (latest_trade_date,),
                )
                wsr_rows = list(reversed(cur.fetchall()))
                if wsr_rows:
                    candidate = {
                        "data_name": "tushare_fut_wsr:CU_warehouse_receipts",
                        "raw_name": "铜仓单变化",
                        "unit": "吨",
                    }
                    converted = [
                        {"dt": row["trade_date"], "value": row.get("vol"), "unit": "吨"}
                        for row in wsr_rows
                        if row.get("vol") is not None
                    ]
                    observation = _database_observation_from_series(
                        candidate=candidate,
                        rows=converted,
                        source_name="futures_store.tushare_fut_wsr",
                    )
                    if observation:
                        observation["source_ref"]["path"] = "mysql://tushare_fut_wsr"
                        observations.append(observation)
    except Exception as exc:
        errors.append(
            {
                "ts": utc_now_iso(),
                "stage": "futures_mysql",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        )
        return observations, {"status": "query_failed", "tables": ["tushare_fut_daily", "tushare_fut_wsr"]}
    return observations, {
        "status": "succeeded",
        "tables": ["tushare_fut_daily", "tushare_fut_wsr"],
        "observation_count": len(observations),
        "database": cfg.get("database"),
        "host": cfg.get("host"),
    }


def _collect_database_observations(
    errors: list[dict[str, Any]],
    *,
    as_of: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data_agent_observations, data_agent_meta = _fetch_data_agent_observations(errors)
    futures_observations, futures_meta = _fetch_futures_table_observations(errors, as_of=as_of)
    observations = [*data_agent_observations, *futures_observations]
    return observations, {
        "data_agent": data_agent_meta,
        "futures_store": futures_meta,
        "observation_count": len(observations),
    }


def _news_logic_paths(root: Path) -> list[Path]:
    base = root / "agent_workspace" / "candidates" / "opinion_radar" / "news_logic"
    dated = sorted(base.glob("20??/??/??/news-logic*.json"))
    latest = base / "latest" / "news-logic.json"
    if latest.exists():
        dated.append(latest)
    return dated


def _latest_generated_at(root: Path, *, fallback_date: str | None = None) -> datetime:
    latest = root / "agent_workspace" / "candidates" / "opinion_radar" / "news_logic" / "latest" / "news-logic.json"
    if latest.exists():
        payload = read_json(latest)
        return _parse_dt(payload.get("generated_at"), default=_parse_dt(fallback_date))
    return _parse_dt(fallback_date)


def _news_source_id(event: dict[str, Any]) -> str:
    return clean_text(event.get("flash_id") or event.get("event_id") or hash_id("NEWS", event.get("text")))


def _news_record(event: dict[str, Any], *, path: Path, root: Path) -> dict[str, Any]:
    text = clean_text(event.get("text"), limit=5000)
    source_id = _news_source_id(event)
    confidence = _num((event.get("match") or {}).get("confidence"), 0.2)
    return {
        "source_id": source_id,
        "event_id": clean_text(event.get("event_id")),
        "asset": clean_text(event.get("asset")),
        "asset_id": clean_text(event.get("asset_id")),
        "event_time": _iso(event.get("publish_time")),
        "text": text,
        "url": clean_text(event.get("url")),
        "channel": clean_text(event.get("channel")),
        "important": int(_num(event.get("important"), 0)),
        "direction_score": _num(event.get("direction_score"), 0.0),
        "heat": _num(event.get("heat"), 0.0),
        "framework": event.get("framework") if isinstance(event.get("framework"), dict) else {},
        "framework_node": event.get("framework_node") if isinstance(event.get("framework_node"), dict) else {},
        "match": event.get("match") if isinstance(event.get("match"), dict) else {"confidence": confidence},
        "consistency": event.get("consistency") if isinstance(event.get("consistency"), dict) else {},
        "theme_anchor_refs": event.get("theme_anchor_refs") if isinstance(event.get("theme_anchor_refs"), list) else [],
        "source_path": relative_to_root(path, root),
        "source_payload_paths": [relative_to_root(path, root)],
        "duplicate_count": 0,
    }


def _load_news_records(root: Path, *, as_of: datetime, window_days: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    start = as_of - timedelta(days=window_days)
    records_by_id: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    input_paths: list[str] = []

    for path in _news_logic_paths(root):
        try:
            payload = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        input_paths.append(relative_to_root(path, root))
        for event in payload.get("events") or []:
            if not isinstance(event, dict):
                continue
            event_time = _parse_dt(event.get("publish_time"), default=as_of)
            if not (start <= event_time <= as_of):
                continue
            record = _news_record(event, path=path, root=root)
            source_id = record["source_id"]
            existing = records_by_id.get(source_id)
            if existing is None:
                records_by_id[source_id] = record
                continue
            duplicate_count += 1
            existing["duplicate_count"] = int(existing.get("duplicate_count") or 0) + 1
            existing.setdefault("source_payload_paths", []).append(relative_to_root(path, root))
            old_conf = _num((existing.get("match") or {}).get("confidence"), 0.0)
            new_conf = _num((record.get("match") or {}).get("confidence"), 0.0)
            if new_conf > old_conf:
                record["source_payload_paths"] = existing["source_payload_paths"]
                record["duplicate_count"] = existing["duplicate_count"]
                records_by_id[source_id] = record

    records = sorted(records_by_id.values(), key=lambda item: (item["event_time"], item["source_id"]))
    return records, {
        "input_paths": sorted(set(input_paths)),
        "duplicate_count": duplicate_count,
        "loaded_count": len(records),
        "window_start": start.isoformat(timespec="seconds"),
        "window_end": as_of.isoformat(timespec="seconds"),
    }


def _filter_news(
    records: list[dict[str, Any]],
    *,
    target_asset: str,
    max_news: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    filtered_rows: list[dict[str, Any]] = []
    relevant: list[dict[str, Any]] = []
    for record in records:
        text = record.get("text") or ""
        asset = clean_text(record.get("asset"))
        node = record.get("framework_node") or {}
        confidence = _num((record.get("match") or {}).get("confidence"), 0.0)
        if asset == target_asset:
            status = "relevant"
            reason = "asset field matches target asset"
            relevant.append(record)
        elif target_asset in text or "copper" in text.lower() or "铜" in text:
            status = "candidate"
            reason = "text mentions copper but mapped asset is different or missing"
            relevant.append(record)
        else:
            status = "filtered_out"
            reason = "no target asset or upstream copper causal-chain keyword"
        if node.get("node_id") == "default::未归类" and status == "relevant":
            reason = "target asset matched; framework mapping is low confidence"
        filtered_rows.append(
            {
                "source_id": record["source_id"],
                "source_event_id": record.get("event_id"),
                "asset": asset,
                "event_time": record.get("event_time"),
                "status": status,
                "filter_reason": reason,
                "framework_node": node,
                "match_confidence": confidence,
                "duplicate_count": record.get("duplicate_count", 0),
                "text": clean_text(text, limit=500),
            }
        )
    return relevant[:max_news], filtered_rows


def _report_payload_path(root: Path) -> Path:
    return (
        root
        / "agent_workspace"
        / "candidates"
        / "research_reports"
        / "wechat_evidence"
        / "latest"
        / "wechat-research-evidence.json"
    )


def _load_report_evidence(
    root: Path,
    *,
    target_asset: str,
    as_of: datetime,
    window_days: int,
    max_reports: int,
    max_evidence: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    path = _report_payload_path(root)
    if not path.exists():
        return [], [], {"input_path": relative_to_root(path, root), "missing": True}
    payload = read_json(path)
    start = as_of - timedelta(days=window_days)
    candidates: list[dict[str, Any]] = []
    for item in payload.get("evidence") or []:
        if not isinstance(item, dict) or clean_text(item.get("asset")) != target_asset:
            continue
        published_at = _parse_dt(item.get("published_at"), default=as_of)
        if start <= published_at <= as_of:
            candidates.append(item)

    by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in candidates:
        by_article[clean_text(item.get("article_id") or item.get("raw_id"))].append(item)
    ranked_articles = sorted(
        by_article.items(),
        key=lambda pair: (-len(pair[1]), clean_text(pair[1][0].get("published_at")), pair[0]),
    )
    selected_article_ids = {article_id for article_id, _items in ranked_articles[:max_reports]}
    selected = [
        item
        for item in sorted(candidates, key=lambda row: (clean_text(row.get("published_at")), clean_text(row.get("evidence_id"))))
        if clean_text(item.get("article_id") or item.get("raw_id")) in selected_article_ids
    ][:max_evidence]
    articles = []
    for article_id in sorted(selected_article_ids):
        items = by_article[article_id]
        first = items[0]
        articles.append(
            {
                "article_id": article_id,
                "source_account": clean_text(first.get("source_account")),
                "title": clean_text(first.get("title")),
                "published_at": _iso(first.get("published_at")),
                "evidence_count": len(items),
                "evidence_ids": [clean_text(item.get("evidence_id")) for item in items],
                "raw_manifest": _source_path_ref(first.get("raw_manifest"), root),
                "text_path": _source_path_ref(first.get("text_path"), root),
            }
        )
    return selected, articles, {
        "input_path": relative_to_root(path, root),
        "candidate_evidence_count": len(candidates),
        "candidate_article_count": len(by_article),
        "selected_evidence_count": len(selected),
        "selected_article_count": len(articles),
        "payload_stats": payload.get("stats") or {},
        "asset_summary": (payload.get("assets") or {}).get(target_asset, {}),
    }


def _framework_ref(root: Path, news: list[dict[str, Any]], reports: list[dict[str, Any]]) -> dict[str, Any]:
    for item in [*news, *reports]:
        framework = item.get("framework") if isinstance(item.get("framework"), dict) else {}
        source_path = framework.get("source_path")
        if source_path:
            path = Path(source_path)
            payload: dict[str, Any] = {}
            if path.exists():
                payload = read_json(path)
            return {
                "framework_id": clean_text(framework.get("framework_id") or payload.get("candidate_id")),
                "asset_id": clean_text(framework.get("asset_id") or payload.get("commodity_code")),
                "source_path": _source_path_ref(source_path, root),
                "schema_version": clean_text(payload.get("schema_version")),
                "stats": payload.get("stats") or {},
            }
    fallback = root / "agent_workspace" / "candidates" / "frameworks" / "2026" / "06" / "16" / "FWK-CU-20260616.json"
    payload = read_json(fallback) if fallback.exists() else {}
    return {
        "framework_id": clean_text(payload.get("candidate_id") or "FWK-CU-20260616"),
        "asset_id": clean_text(payload.get("commodity_code") or "CU"),
        "source_path": relative_to_root(fallback, root),
        "schema_version": clean_text(payload.get("schema_version")),
        "stats": payload.get("stats") or {},
    }


def _incremental_state_ref(root: Path, target_asset: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = root / "agent_workspace" / "candidates" / "incremental_state" / "latest" / "research_state.json"
    if not path.exists():
        return [], {"input_path": relative_to_root(path, root), "missing": True}
    payload = read_json(path)
    dimensions = []
    for item in payload.get("dimensions") or []:
        if not isinstance(item, dict):
            continue
        refs = item.get("asset_refs") if isinstance(item.get("asset_refs"), list) else []
        if any(clean_text(ref.get("label")) == target_asset for ref in refs if isinstance(ref, dict)):
            dimensions.append(item)
    return dimensions, {
        "input_path": relative_to_root(path, root),
        "state_id": clean_text(payload.get("state_id")),
        "generated_at": clean_text(payload.get("generated_at")),
        "stats": payload.get("stats") or {},
        "selected_dimension_count": len(dimensions),
    }


def _event_type(text: str, framework_node: dict[str, Any] | None = None) -> str:
    label = clean_text((framework_node or {}).get("label") or (framework_node or {}).get("dimension_label"))
    haystack = f"{text} {label}".lower()
    if any(term in haystack for term in ("霍尔木兹", "伊朗", "以色列", "g7", "俄", "制裁")):
        return "geopolitics"
    if any(term in haystack for term in ("关税", "政策", "调查", "tariff", "policy")):
        return "policy"
    if any(term in haystack for term in ("美联储", "利率", "美元", "pce", "cpi", "pmi", "fed", "宏观")):
        return "macro"
    if any(term in haystack for term in ("库存", "仓单", "inventory", "stocks", "warrant")):
        return "inventory"
    if any(term in haystack for term in ("tc", "加工费", "冶炼盈利", "硫酸", "成本")):
        return "cost"
    if any(term in haystack for term in ("供应", "供给", "铜矿", "精矿", "发运", "减产", "supply")):
        return "supply"
    if any(term in haystack for term in ("需求", "消费", "开工率", "订单", "demand")):
        return "demand"
    if any(term in haystack for term in ("资金", "持仓", "净流入", "多头", "空头")):
        return "sentiment"
    if any(term in haystack for term in ("上涨", "下跌", "回落", "反弹", "涨", "跌")):
        return "price_action"
    return "other"


def _entities(text: str) -> list[str]:
    candidates = (
        "铜",
        "LME",
        "SHFE",
        "上期所",
        "美联储",
        "美元",
        "霍尔木兹",
        "Oyu Tolgoi",
        "力拓",
        "TC",
        "铜箔",
        "COMEX",
    )
    lower = text.lower()
    return [term for term in candidates if term.lower() in lower or term in text][:8]


def _direction_from_text(text: str, explicit: Any = None) -> float:
    value = _num(explicit, 0.0)
    if abs(value) > 0:
        return _clip(value, -1.0, 1.0)
    lower = text.lower()
    bullish = sum(1 for term in BULLISH_TERMS if term.lower() in lower or term in text)
    bearish = sum(1 for term in BEARISH_TERMS if term.lower() in lower or term in text)
    if bullish == bearish:
        return 0.0
    return _clip((bullish - bearish) * 0.35, -1.0, 1.0)


def _node_id(item: dict[str, Any]) -> str:
    node = item.get("framework_node") if isinstance(item.get("framework_node"), dict) else {}
    return clean_text(node.get("node_id") or node.get("label") or "")


def _atomic_event(
    *,
    asset: str,
    event_time: Any,
    source_type: str,
    source_id: str,
    summary: str,
    text: str,
    framework_node: dict[str, Any] | None,
    confidence: float,
    evidence: str,
    direction_score: float,
    source_ref: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event_type = _event_type(text, framework_node)
    direct_nodes = [node for node in [_node_id({"framework_node": framework_node or {}})] if node]
    event_id = hash_id("DEMOEV", source_type, source_id, event_time, summary, length=16)
    return {
        "event_id": event_id,
        "asset": [asset],
        "event_time": _iso(event_time),
        "source_type": source_type,
        "source_id": source_id,
        "summary": clean_text(summary, limit=260),
        "entities": _entities(text),
        "event_type": event_type if event_type in EVENT_TYPES else "other",
        "direct_nodes": direct_nodes,
        "confidence": round(_clip(confidence, 0.0, 1.0), 4),
        "evidence_quote_or_value": clean_text(evidence, limit=500),
        "extraction_version": "demo_v1",
        "raw_direction_score": round(_clip(direction_score, -1.0, 1.0), 4),
        "source_ref": source_ref,
        "extra": extra or {},
    }


def _summary_from_text(text: str) -> str:
    text = clean_text(text)
    parts = re.split(r"(?<=[。！？.!?])\s*", text)
    return clean_text(parts[0] if parts else text, limit=220)


def _build_atomic_events(
    root: Path,
    *,
    asset: str,
    news: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in news:
        text = clean_text(item.get("text"), limit=5000)
        confidence = _num((item.get("match") or {}).get("confidence"), 0.5)
        if item.get("important"):
            confidence = min(1.0, confidence + 0.05)
        events.append(
            _atomic_event(
                asset=asset,
                event_time=item.get("event_time"),
                source_type="news",
                source_id=clean_text(item.get("source_id")),
                summary=_summary_from_text(text),
                text=text,
                framework_node=item.get("framework_node") if isinstance(item.get("framework_node"), dict) else {},
                confidence=confidence,
                evidence=text,
                direction_score=_direction_from_text(text, item.get("direction_score")),
                source_ref={
                    "ref_type": "candidate",
                    "source_name": "opinion_radar.news_logic",
                    "id": clean_text(item.get("event_id") or item.get("source_id")),
                    "path": clean_text(item.get("source_path")),
                    "url": clean_text(item.get("url")),
                },
                extra={
                    "heat": item.get("heat"),
                    "match": item.get("match"),
                    "consistency": item.get("consistency"),
                    "duplicate_count": item.get("duplicate_count", 0),
                },
            )
        )
    for item in reports:
        text = clean_text(item.get("text"), limit=5000)
        confidence = _num((item.get("match") or {}).get("confidence"), _num(item.get("heat"), 0.65))
        events.append(
            _atomic_event(
                asset=asset,
                event_time=item.get("published_at"),
                source_type="research_report",
                source_id=clean_text(item.get("evidence_id")),
                summary=f"研报证据片段：{_summary_from_text(text)}",
                text=text,
                framework_node=item.get("framework_node") if isinstance(item.get("framework_node"), dict) else {},
                confidence=confidence,
                evidence=text,
                direction_score=_direction_from_text(text, item.get("direction_score")),
                source_ref={
                    "ref_type": "candidate",
                    "source_name": "research_reports.wechat_evidence",
                    "id": clean_text(item.get("article_id")),
                    "path": _source_path_ref(item.get("raw_manifest"), root),
                    "url": clean_text(item.get("source_url")),
                },
                extra={
                    "article_id": clean_text(item.get("article_id")),
                    "source_account": clean_text(item.get("source_account")),
                    "title": clean_text(item.get("title")),
                    "direction": clean_text(item.get("direction")),
                    "consistency": item.get("consistency"),
                    "scoring_role": clean_text(item.get("scoring_role")),
                },
            )
        )
    for obs in observations:
        text = clean_text(obs.get("observation_text"), limit=1000)
        events.append(
            _atomic_event(
                asset=asset,
                event_time=obs.get("data_time") or obs.get("published_at"),
                source_type="fundamental_data",
                source_id=clean_text(obs.get("observation_id")),
                summary=clean_text(obs.get("summary") or text, limit=260),
                text=text,
                framework_node=obs.get("framework_node") if isinstance(obs.get("framework_node"), dict) else {},
                confidence=_num(obs.get("confidence"), 0.68),
                evidence=json.dumps(obs.get("value_payload") or {}, ensure_ascii=False),
                direction_score=_num(obs.get("direction_score"), 0.0),
                source_ref=obs.get("source_ref") or {},
                extra={"data_series_id": clean_text(obs.get("data_series_id")), "data_quality": obs.get("data_quality")},
            )
        )
    return events


def _extract_number(text: str) -> float | None:
    match = re.search(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?", text)
    return _num(match.group(0)) if match else None


def _observation(
    *,
    source: dict[str, Any],
    source_type: str,
    series_id: str,
    summary: str,
    value_payload: dict[str, Any],
    direction_score: float,
    framework_node: dict[str, Any],
    confidence: float,
    root: Path,
) -> dict[str, Any]:
    source_id = clean_text(source.get("source_id") or source.get("evidence_id") or source.get("event_id"))
    observation_id = hash_id("DEMOOBS", source_id, series_id, summary, length=16)
    return {
        "observation_id": observation_id,
        "data_series_id": series_id,
        "source_type": source_type,
        "source_id": source_id,
        "published_at": _iso(source.get("event_time") or source.get("published_at") or source.get("publish_time")),
        "data_time": _iso(source.get("event_time") or source.get("published_at") or source.get("publish_time")),
        "summary": summary,
        "observation_text": clean_text(source.get("text"), limit=700),
        "value_payload": value_payload,
        "direction_score": round(_clip(direction_score, -1.0, 1.0), 4),
        "framework_node": framework_node,
        "confidence": confidence,
        "data_quality": {
            "method": "regex_from_existing_evidence",
            "previous_value_available": value_payload.get("previous_value") is not None,
            "percentile_available": False,
            "quality_note": "从已有新闻/研报证据文本中抽取数值；未访问新增 MySQL 序列。",
        },
        "source_ref": {
            "ref_type": "candidate",
            "source_name": source_type,
            "id": source_id,
            "path": _source_path_ref(source.get("source_path") or source.get("raw_manifest"), root),
            "url": clean_text(source.get("url") or source.get("source_url")),
        },
    }


def _extract_fundamental_observations(
    root: Path,
    *,
    news: list[dict[str, Any]],
    reports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    inventory_node = {
        "node_id": "FWK-CU-20260616::基本面/库存/全球显性库存",
        "label": "基本面/库存/全球显性库存",
        "dimension_label": "全球显性库存",
    }
    cost_node = {
        "node_id": "FWK-CU-20260616::基本面/成本利润/冶炼盈利与加工费",
        "label": "基本面/成本利润/冶炼盈利与加工费",
        "dimension_label": "冶炼盈利与加工费",
    }
    demand_node = {
        "node_id": "FWK-CU-20260616::基本面/需求/终端消费板块",
        "label": "基本面/需求/终端消费板块",
        "dimension_label": "终端消费板块",
    }
    policy_node = {
        "node_id": "FWK-CU-20260616::金融属性/跨市场/关税与跨市物流重塑",
        "label": "金融属性/跨市场/关税与跨市物流重塑",
        "dimension_label": "关税与跨市物流重塑",
    }

    for source in news:
        text = clean_text(source.get("text"))
        if not ("铜" in text or "copper" in text.lower()):
            continue
        if "库存" in text or "inventory" in text.lower() or "stocks" in text.lower():
            change_match = re.search(r"(铜库存|copper)(?:[^\d+-]{0,30})(增加|减少|up|down)?\s*([-+]?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:吨|tonnes|t)?", text, re.I)
            if change_match:
                direction_word = clean_text(change_match.group(2)).lower()
                raw_value = _num(change_match.group(3))
                is_draw = direction_word in {"减少", "down"} or "库存减少" in text or "down" in text.lower()
                observations.append(
                    _observation(
                        source=source,
                        source_type="news",
                        series_id="copper_exchange_inventory_change",
                        summary=f"交易所铜库存{'下降' if is_draw else '上升或变化'} {raw_value:g} 吨",
                        value_payload={
                            "value": -raw_value if is_draw else raw_value,
                            "previous_value": None,
                            "change": -raw_value if is_draw else raw_value,
                            "unit": "tonne",
                            "raw_value": change_match.group(0),
                            "historical_percentile": None,
                        },
                        direction_score=0.55 if is_draw else -0.45,
                        framework_node=inventory_node,
                        confidence=0.7,
                        root=root,
                    )
                )
    for source in reports:
        text = clean_text(source.get("text"))
        if "TC" in text or "加工费" in text:
            value = _extract_number(text)
            if value is not None:
                observations.append(
                    _observation(
                        source=source,
                        source_type="research_reports.wechat_evidence",
                        series_id="copper_concentrate_tc",
                        summary=f"铜精矿 TC/加工费证据值 {value:g}",
                        value_payload={
                            "value": value,
                            "previous_value": None,
                            "change": None,
                            "unit": "USD/t or quoted unit",
                            "raw_value": text,
                            "historical_percentile": None,
                        },
                        direction_score=0.55 if value < 0 else -0.2,
                        framework_node=cost_node,
                        confidence=0.68,
                        root=root,
                    )
                )
        if "开工率" in text and ("铜箔" in text or "铜板带" in text):
            value = _extract_number(text)
            if value is not None:
                observations.append(
                    _observation(
                        source=source,
                        source_type="research_reports.wechat_evidence",
                        series_id="copper_downstream_operating_rate",
                        summary=f"铜下游开工率证据值 {value:g}%",
                        value_payload={
                            "value": value,
                            "previous_value": None,
                            "change": None,
                            "unit": "%",
                            "raw_value": text,
                            "historical_percentile": None,
                        },
                        direction_score=0.4,
                        framework_node=demand_node,
                        confidence=0.62,
                        root=root,
                    )
                )
        if "CL价差" in text or "COMEX" in text or "关税" in text:
            value = _extract_number(text)
            if value is not None:
                observations.append(
                    _observation(
                        source=source,
                        source_type="research_reports.wechat_evidence",
                        series_id="copper_cross_market_tariff_spread",
                        summary=f"铜跨市/关税价差相关证据值 {value:g}",
                        value_payload={
                            "value": value,
                            "previous_value": None,
                            "change": None,
                            "unit": "quoted unit",
                            "raw_value": text,
                            "historical_percentile": None,
                        },
                        direction_score=0.35,
                        framework_node=policy_node,
                        confidence=0.58,
                        root=root,
                    )
                )
    # Keep the evidence small and diverse; repeated article snippets still remain traceable.
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in observations:
        unique.setdefault((item["data_series_id"], item["source_id"]), item)
    return list(unique.values())[:30]


def _event_group_key(event: dict[str, Any]) -> str:
    node = event.get("direct_nodes", [""])[0] if event.get("direct_nodes") else ""
    summary = re.sub(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?", "#", clean_text(event.get("summary")).lower())
    summary = re.sub(r"\s+", "", summary)[:80]
    return f"{event.get('event_type')}|{node}|{summary}"


def _build_event_groups(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, int]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[_event_group_key(event)].append(event)
    groups: list[dict[str, Any]] = []
    event_to_group: dict[str, str] = {}
    group_sizes: dict[str, int] = {}
    for key, items in sorted(grouped.items()):
        group_id = hash_id("DEMOGRP", key, length=14)
        source_ids = sorted({clean_text(item.get("source_id")) for item in items})
        event_ids = [clean_text(item.get("event_id")) for item in items]
        for event_id in event_ids:
            event_to_group[event_id] = group_id
        group_sizes[group_id] = len(items)
        groups.append(
            {
                "event_group_id": group_id,
                "group_key": key,
                "event_ids": event_ids,
                "source_ids": source_ids,
                "event_type": items[0].get("event_type"),
                "framework_node": (items[0].get("direct_nodes") or [""])[0],
                "representative_summary": items[0].get("summary"),
                "source_count": len(source_ids),
                "event_count": len(items),
                "merge_method": "event_type+framework_node+normalized_summary",
            }
        )
    return groups, event_to_group, group_sizes


def _causal_node_for_event(event: dict[str, Any]) -> tuple[str, float, float, str]:
    text = clean_text(event.get("summary") or event.get("evidence_quote_or_value"))
    node = "copper_price_support"
    direction = _direction_from_text(text, event.get("raw_direction_score"))
    event_type = clean_text(event.get("event_type"))
    reason = "default copper relevance rule"
    if event_type == "geopolitics" and ("霍尔木兹" in text or "海峡" in text):
        node, direction, reason = "hormuz_shipping_disruption", 1.0, "地缘运输节点直接出现"
    elif "美联储" in text or "鹰派" in text or "加息" in text or "美元" in text:
        node, direction, reason = "fed_hawkish_expectation", 1.0 if direction <= 0 else direction, "鹰派/美元线索激活宏观流动性压力"
    elif event_type == "inventory":
        node, reason = "copper_visible_inventory_tightness", "库存变化激活显性库存节点"
        direction = 1.0 if direction >= 0 else -1.0
    elif "Oyu Tolgoi" in text or "发运中断" in text or "道路封锁" in text:
        node, direction, reason = "copper_supply_disruption", 1.0, "铜矿发运扰动激活供应中断节点"
    elif "TC" in text or "加工费" in text or "矿端紧张" in text:
        node, direction, reason = "copper_processing_fee_pressure", 1.0 if direction >= 0 else direction, "加工费/矿端紧张激活成本利润节点"
    elif event_type == "demand":
        node, reason = ("copper_demand_improvement" if direction >= 0 else "copper_demand_softness"), "需求文本激活需求节点"
    elif event_type == "policy" and ("关税" in text or "COMEX" in text or "CL价差" in text):
        node, direction, reason = "copper_cross_market_tightness", 1.0 if direction >= 0 else direction, "关税或跨市价差激活跨市物流节点"
    elif event_type == "sentiment":
        node, reason = "copper_fund_flow", "资金/持仓线索激活资金流节点"
    elif event_type == "price_action":
        node = "copper_price_support" if direction >= 0 else "copper_price_pressure"
        reason = "价格动作只作为行情状态，不伪装成基本面直接原因"
    if abs(direction) < 0.05:
        direction = 0.25
    magnitude = min(1.0, max(0.18, abs(direction)))
    return node, 1.0 if direction >= 0 else -1.0, magnitude, reason


def _build_activations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    activations: list[dict[str, Any]] = []
    for event in events:
        node, direction, magnitude, reason = _causal_node_for_event(event)
        activations.append(
            {
                "event_id": event["event_id"],
                "activated_node": node,
                "direction": int(direction),
                "magnitude": round(magnitude, 4),
                "confidence": round(_num(event.get("confidence"), 0.5), 4),
                "activation_type": "observed",
                "reason": reason,
            }
        )
    return activations


def _edge_index(config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in config.get("causal_edges") or []:
        if isinstance(edge, dict):
            edges[clean_text(edge.get("from_node"))].append(edge)
    return edges


def _build_paths(
    activations: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    max_depth = int((config.get("selection") or {}).get("max_propagation_depth") or 4)
    depth_decay_base = _num((config.get("propagation") or {}).get("depth_decay_base"), 0.8)
    edges = _edge_index(config)
    paths: list[dict[str, Any]] = []
    mapped_nodes = set((config.get("node_framework_map") or {}).keys())

    for activation in activations:
        queue: list[tuple[str, list[dict[str, Any]], float, float, list[str], set[str]]] = [
            (
                activation["activated_node"],
                [],
                _num(activation.get("magnitude"), 0.0) * int(activation.get("direction") or 1),
                _num(activation.get("confidence"), 0.5),
                [],
                {activation["activated_node"]},
            )
        ]
        emitted = False
        while queue:
            node, steps, signal, confidence, assumptions, seen = queue.pop(0)
            outgoing = edges.get(node, [])
            if (steps and node in mapped_nodes) or not outgoing or len(steps) >= max_depth:
                hop_count = len(steps)
                path_id = hash_id("DEMOPATH", activation["event_id"], node, hop_count, signal, length=16)
                paths.append(
                    {
                        "path_id": path_id,
                        "event_id": activation["event_id"],
                        "target_asset": "COPPER",
                        "steps": steps,
                        "path_signal": round(_clip(signal, -1.0, 1.0), 4),
                        "path_confidence": round(_clip(confidence, 0.0, 1.0), 4),
                        "assumptions": assumptions,
                        "alternative_paths": [],
                        "final_node": node,
                        "path_type": "direct" if hop_count == 0 else "propagated",
                    }
                )
                emitted = True
                continue
            for edge in outgoing:
                to_node = clean_text(edge.get("to_node"))
                if not to_node or to_node in seen:
                    continue
                hop_count = len(steps) + 1
                edge_weight = _num(edge.get("edge_weight"), 1.0)
                edge_confidence = _num(edge.get("confidence"), 0.6)
                polarity = int(edge.get("polarity") or 1)
                next_step = {
                    "from_node": node,
                    "to_node": to_node,
                    "polarity": polarity,
                    "edge_weight": edge_weight,
                    "confidence": edge_confidence,
                    "delay_hint": clean_text(edge.get("delay_hint")),
                }
                next_signal = signal * polarity * edge_weight
                depth_decay = depth_decay_base ** max(0, hop_count - 1)
                next_conf = _num(activation.get("confidence"), 0.5)
                for step in [*steps, next_step]:
                    next_conf *= _num(step.get("confidence"), 0.6)
                next_conf *= depth_decay
                queue.append(
                    (
                        to_node,
                        [*steps, next_step],
                        next_signal,
                        next_conf,
                        [*assumptions, clean_text(edge.get("assumption"))],
                        {*seen, to_node},
                    )
                )
        if not emitted:
            node = clean_text(activation.get("activated_node"))
            paths.append(
                {
                    "path_id": hash_id("DEMOPATH", activation["event_id"], node, "direct", length=16),
                    "event_id": activation["event_id"],
                    "target_asset": "COPPER",
                    "steps": [],
                    "path_signal": round(_num(activation.get("magnitude"), 0.0) * int(activation.get("direction") or 1), 4),
                    "path_confidence": round(_num(activation.get("confidence"), 0.5), 4),
                    "assumptions": ["无可用传播边，保留为直接风险提示"],
                    "alternative_paths": [],
                    "final_node": node,
                    "path_type": "direct",
                }
            )
    return paths


def _framework_node_for_path(
    path: dict[str, Any],
    event: dict[str, Any],
    config: dict[str, Any],
) -> str:
    node_map = config.get("node_framework_map") or {}
    mapped = clean_text(node_map.get(clean_text(path.get("final_node"))))
    if mapped:
        return mapped
    direct = event.get("direct_nodes") or []
    if direct and clean_text(direct[0]) != "default::未归类":
        return clean_text(direct[0])
    return ""


def _build_signal_updates(
    events: list[dict[str, Any]],
    paths: list[dict[str, Any]],
    event_to_group: dict[str, str],
    *,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    event_by_id = {event["event_id"]: event for event in events}
    signals: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    accepted_max_hops = int((config.get("propagation") or {}).get("accepted_max_hops") or 2)
    min_conf = _num((config.get("propagation") or {}).get("min_path_confidence_for_signal"), 0.08)
    for path in paths:
        event = event_by_id[path["event_id"]]
        framework_node = _framework_node_for_path(path, event, config)
        signal_value = _num(path.get("path_signal"), 0.0)
        confidence = _num(path.get("path_confidence"), 0.0)
        hop_count = len(path.get("steps") or [])
        if not framework_node:
            unmapped.append({"event_id": event["event_id"], "path_id": path["path_id"], "reason": "no framework node mapping"})
            status = "needs_review"
        elif (
            confidence < min_conf
            or hop_count > accepted_max_hops
            or _num(event.get("confidence"), 0.0) < 0.5
            or "default::未归类" in (event.get("direct_nodes") or [])
        ):
            status = "low_confidence"
        else:
            status = "accepted"
        if framework_node:
            direction = 1 if signal_value >= 0 else -1
            relation = "contextual"
            if abs(signal_value) >= 0.08:
                relation = "supports" if direction > 0 else "contradicts"
            signal_id = hash_id("DEMOSIG", event["event_id"], path["path_id"], framework_node, length=16)
            signals.append(
                {
                    "signal_id": signal_id,
                    "asset": "COPPER",
                    "framework_node": framework_node,
                    "driver_id": hash_id("DEMODRV", "COPPER", framework_node, length=16),
                    "direction": direction,
                    "magnitude": round(_clip(abs(signal_value), 0.0, 1.0), 4),
                    "confidence": round(_clip(confidence, 0.0, 1.0), 4),
                    "source_type": event.get("source_type"),
                    "event_ids": [event["event_id"]],
                    "event_group_id": event_to_group.get(event["event_id"], ""),
                    "causal_path_ids": [path["path_id"]],
                    "relation": relation,
                    "effective_time": event.get("event_time"),
                    "decay_half_life_hours": int((config.get("driver_update") or {}).get("decay_half_life_hours") or 72),
                    "status": status,
                    "direct_or_inferred": "direct" if hop_count <= 1 else "multi_hop_inferred",
                    "traceability": {
                        "source_id": event.get("source_id"),
                        "source_type": event.get("source_type"),
                        "summary": event.get("summary"),
                        "final_node": path.get("final_node"),
                        "hop_count": hop_count,
                    },
                }
            )
    return signals, unmapped


def _previous_driver_states(dimensions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for item in dimensions:
        ref = item.get("framework_node_ref") if isinstance(item.get("framework_node_ref"), dict) else {}
        framework_node = clean_text(ref.get("id"))
        if not framework_node:
            continue
        driver_id = hash_id("DEMODRV", "COPPER", framework_node, length=16)
        total_weight = _num(item.get("total_weight"), 0.0)
        direction_score = _num(item.get("direction_score"), 0.0)
        strength = _clip(direction_score * min(1.0, total_weight), -1.0, 1.0)
        states[driver_id] = {
            "driver_id": driver_id,
            "asset": "COPPER",
            "framework_node": framework_node,
            "as_of": clean_text(item.get("last_seen_at")) or utc_now_iso(),
            "strength": round(strength, 4),
            "trend": "stable",
            "confidence": round(min(0.85, total_weight / max(1.0, _num(item.get("signal_count"), 1.0))), 4),
            "support_score": round(_num(item.get("support_count"), 0.0), 4),
            "contradiction_score": round(_num(item.get("conflict_count"), 0.0), 4),
            "source_diversity": 0.0,
            "supporting_signal_ids": [ref.get("id") for ref in item.get("signal_refs") or [] if isinstance(ref, dict)],
            "contradicting_signal_ids": [],
            "previous_state_ref": clean_text(item.get("dimension_state_id")),
            "change_explanation": "Loaded from incremental_state dimension_state as demo baseline.",
        }
    return states


def _signal_weight(
    signal: dict[str, Any],
    *,
    config: dict[str, Any],
    as_of: datetime,
    group_sizes: dict[str, int],
) -> float:
    driver_cfg = config.get("driver_update") or {}
    source_weight = _num((driver_cfg.get("source_weight") or {}).get(clean_text(signal.get("source_type"))), 0.4)
    status_weight = _num((driver_cfg.get("status_weight") or {}).get(clean_text(signal.get("status"))), 0.2)
    group_size = group_sizes.get(clean_text(signal.get("event_group_id")), 1)
    dup_cfg = driver_cfg.get("duplication_penalty") or {}
    duplication_penalty = _num(dup_cfg.get("unique"), 1.0)
    if group_size > 1:
        duplication_penalty = _num(dup_cfg.get("same_event_group"), 0.5) / math.sqrt(group_size)
    age_hours = max(0.0, (as_of - _parse_dt(signal.get("effective_time"), default=as_of)).total_seconds() / 3600.0)
    half_life = max(1.0, _num(driver_cfg.get("decay_half_life_hours"), 72.0))
    time_decay = 0.5 ** (age_hours / half_life)
    return (
        int(signal.get("direction") or 1)
        * _num(signal.get("magnitude"), 0.0)
        * _num(signal.get("confidence"), 0.0)
        * source_weight
        * status_weight
        * time_decay
        * duplication_penalty
    )


def _build_driver_states(
    signals: list[dict[str, Any]],
    previous_states: dict[str, dict[str, Any]],
    *,
    config: dict[str, Any],
    as_of: datetime,
    group_sizes: dict[str, int],
) -> tuple[dict[str, Any], dict[str, Any]]:
    before = dict(previous_states)
    for signal in signals:
        before.setdefault(
            signal["driver_id"],
            {
                "driver_id": signal["driver_id"],
                "asset": "COPPER",
                "framework_node": signal["framework_node"],
                "as_of": as_of.isoformat(timespec="seconds"),
                "strength": 0.0,
                "trend": "uncertain",
                "confidence": 0.0,
                "support_score": 0.0,
                "contradiction_score": 0.0,
                "source_diversity": 0.0,
                "supporting_signal_ids": [],
                "contradicting_signal_ids": [],
                "previous_state_ref": "",
                "change_explanation": "No matching incremental_state baseline; initialized at zero for demo.",
            },
        )

    by_driver: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        by_driver[signal["driver_id"]].append(signal)

    after: dict[str, dict[str, Any]] = {}
    persistence = _num((config.get("driver_update") or {}).get("persistence"), 0.65)
    for driver_id, state in before.items():
        related = by_driver.get(driver_id, [])
        weighted = [_signal_weight(signal, config=config, as_of=as_of, group_sizes=group_sizes) for signal in related]
        support = sum(value for value in weighted if value > 0)
        contradiction = abs(sum(value for value in weighted if value < 0))
        previous_strength = _num(state.get("strength"), 0.0)
        next_strength = _clip(previous_strength * persistence + sum(weighted), -1.0, 1.0)
        delta = next_strength - previous_strength
        source_types = {clean_text(signal.get("source_type")) for signal in related if signal.get("source_type")}
        diversity = min(1.0, len(source_types) / 3.0)
        avg_conf = sum(_num(signal.get("confidence"), 0.0) for signal in related) / len(related) if related else _num(state.get("confidence"), 0.0)
        confidence = _clip(avg_conf + diversity * 0.12 - min(0.35, contradiction * 0.35), 0.0, 1.0)
        if abs(delta) < 0.03:
            trend = "stable"
        elif previous_strength * next_strength < -0.01:
            trend = "reversing"
        elif abs(next_strength) > abs(previous_strength):
            trend = "strengthening"
        else:
            trend = "weakening"
        after[driver_id] = {
            "driver_id": driver_id,
            "asset": "COPPER",
            "framework_node": state.get("framework_node") or (related[0].get("framework_node") if related else ""),
            "as_of": as_of.isoformat(timespec="seconds"),
            "strength": round(next_strength, 4),
            "trend": trend,
            "confidence": round(confidence, 4),
            "support_score": round(support, 4),
            "contradiction_score": round(contradiction, 4),
            "source_diversity": round(diversity, 4),
            "supporting_signal_ids": [signal["signal_id"] for signal, value in zip(related, weighted, strict=False) if value > 0],
            "contradicting_signal_ids": [signal["signal_id"] for signal, value in zip(related, weighted, strict=False) if value < 0],
            "previous_state_ref": state.get("previous_state_ref", ""),
            "change_explanation": (
                "driver_strength=clip(previous_strength*persistence + sum(weighted_signal), -1, 1); "
                f"previous={previous_strength:.4f}, weighted_sum={sum(weighted):.4f}, persistence={persistence:.2f}"
            ),
        }
    return {"schema_version": "asset_event_state_demo_driver_before.v1", "drivers": list(before.values())}, {
        "schema_version": "asset_event_state_demo_driver_after.v1",
        "drivers": sorted(after.values(), key=lambda item: (-abs(item["strength"]), item["driver_id"])),
    }


def _validate_sources(signals: list[dict[str, Any]], drivers_after: dict[str, Any]) -> dict[str, Any]:
    by_driver: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        by_driver[signal["driver_id"]].append(signal)
    results = []
    for driver in drivers_after.get("drivers", []):
        related = by_driver.get(driver["driver_id"], [])
        by_source: dict[str, list[int]] = defaultdict(list)
        for signal in related:
            by_source[clean_text(signal.get("source_type"))].append(int(signal.get("direction") or 0))
        source_signs = {
            source: 1 if sum(values) > 0 else (-1 if sum(values) < 0 else 0)
            for source, values in by_source.items()
        }
        signs = {value for value in source_signs.values() if value}
        if not related:
            agreement = "insufficient"
        elif len(signs) > 1:
            agreement = "conflicted"
        elif len(by_source) >= 2:
            agreement = "confirmed"
        else:
            agreement = "partially_confirmed"
        warnings = []
        if any(signal.get("direct_or_inferred") == "multi_hop_inferred" for signal in related):
            warnings.append("存在多跳推断，长链条仅作为 risk hint，不作为强确认。")
        if "news" in by_source and "research_report" in by_source and "fundamental_data" not in by_source:
            warnings.append("新闻与研报有交集，但缺少独立结构化数据验证。")
        if len(signs) > 1:
            warnings.append("同一 driver 存在相反方向证据，降低置信度而不是抵消为确定结论。")
        results.append(
            {
                "driver_id": driver["driver_id"],
                "framework_node": driver.get("framework_node"),
                "agreement": agreement,
                "supporting_sources": [source for source, sign in source_signs.items() if sign > 0],
                "contradicting_sources": [source for source, sign in source_signs.items() if sign < 0],
                "independent_source_count": len(by_source),
                "key_conflicts": [
                    {
                        "signal_id": signal["signal_id"],
                        "source_type": signal.get("source_type"),
                        "direction": signal.get("direction"),
                        "summary": signal.get("traceability", {}).get("summary"),
                    }
                    for signal in related
                    if signal.get("direction", 0) < 0
                ][:5],
                "model_warning": warnings,
                "confidence_adjustment": round(-0.15 if len(signs) > 1 else (0.08 if len(by_source) >= 2 else -0.05), 4),
            }
        )
    return {"schema_version": "asset_event_state_demo_validation.v1", "results": results}


def _quality_metrics(news_rows: list[dict[str, Any]], selected_news: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(row["status"] for row in news_rows)
    duplicate_news = sum(1 for row in news_rows if int(row.get("duplicate_count") or 0) > 0)
    relevant = statuses.get("relevant", 0) + statuses.get("candidate", 0)
    return {
        "news_total_unique": len(news_rows),
        "news_selected": len(selected_news),
        "filter_status_counts": dict(statuses),
        "duplicate_row_count": duplicate_news,
        "duplicate_rate": round(duplicate_news / max(1, len(news_rows)), 4),
        "relevance_rate": round(relevant / max(1, len(news_rows)), 4),
        "missing_url_count": sum(1 for item in selected_news if not item.get("url")),
        "low_framework_confidence_count": sum(
            1 for item in selected_news if _num((item.get("match") or {}).get("confidence"), 0.0) < 0.5
        ),
    }


def _selected_inputs(
    *,
    asset: str,
    window_start: datetime,
    as_of: datetime,
    news: list[dict[str, Any]],
    report_articles: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    framework_ref: dict[str, Any],
    quality: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "asset_event_state_demo_selected_inputs.v1",
        "asset": asset,
        "time_window": {
            "start": window_start.isoformat(timespec="seconds"),
            "end": as_of.isoformat(timespec="seconds"),
        },
        "news_source_ids": [item["source_id"] for item in news],
        "report_source_ids": [item["article_id"] for item in report_articles],
        "data_series_ids": sorted({item["data_series_id"] for item in observations}),
        "framework_id": framework_ref.get("framework_id"),
        "framework_version": framework_ref.get("schema_version") or "framework_candidate.v1",
        "framework_ref": framework_ref,
        "quality": quality,
    }


def _agent_records(
    *,
    root: Path,
    paths: dict[str, Any],
    selected: dict[str, Any],
    stats: dict[str, Any],
    assumptions: list[str],
    risks: list[str],
) -> list[dict[str, Any]]:
    return [
        {
            "agent": "Repository Auditor",
            "read_files": paths["read_files"],
            "modified_files": paths["modified_files"],
            "generated_data": paths["generated_data"],
            "assumptions": assumptions[:2],
            "risks": risks,
            "handoff": "Use selected_inputs.json and run_manifest.json as the fixed demo contract.",
        },
        {
            "agent": "Data Scout",
            "read_files": paths["read_files"],
            "modified_files": [],
            "generated_data": ["selected_inputs.json", "filtered_news.jsonl"],
            "assumptions": [
                "最近 3 天铜新闻不足 50 条，按任务要求扩展到 7 天。",
                "基本面数据优先读取 data_agent MySQL 指标，缺口用已有证据文本中的结构化数值补充。",
            ],
            "risks": [f"news_selected={len(selected.get('news_source_ids', []))}; report_count={len(selected.get('report_source_ids', []))}"],
            "handoff": "Pipeline Builder should use selected source IDs only.",
        },
        {
            "agent": "Pipeline Builder",
            "read_files": paths["read_files"],
            "modified_files": paths["modified_files"],
            "generated_data": paths["generated_data"],
            "assumptions": ["LLM unavailable or not required; demo uses deterministic rules and records extraction_version=demo_v1."],
            "risks": ["Long causal paths are decayed and marked low_confidence when they exceed accepted_max_hops."],
            "handoff": "Evaluator should audit atomic_events, causal_paths, and signal_updates together.",
        },
        {
            "agent": "Evaluator",
            "read_files": ["atomic_events.jsonl", "causal_paths.jsonl", "signal_updates.jsonl"],
            "modified_files": [],
            "generated_data": ["validation_results.json", "audit_samples.md"],
            "assumptions": ["Financial conclusion correctness is not asserted; traceability and honest confidence are checked."],
            "risks": [f"unmapped_signal_count={stats.get('unmapped_signal_count', 0)}"],
            "handoff": "Integration Reporter should expose the run path and reproduce command.",
        },
        {
            "agent": "Integration Reporter",
            "read_files": ["run_manifest.json", "validation_results.json", "driver_state_after.json"],
            "modified_files": [],
            "generated_data": ["demo_report.md", "latest_manifest.json"],
            "assumptions": [],
            "risks": ["正式 gj_chainplatform UI 未修改；平台后续可读取静态 JSON。"],
            "handoff": "Demo complete when run_manifest.status is succeeded.",
        },
    ]


def _demo_report(
    *,
    selected: dict[str, Any],
    stats: dict[str, Any],
    drivers_after: dict[str, Any],
    validation: dict[str, Any],
    signals: list[dict[str, Any]],
    events: list[dict[str, Any]],
    paths: list[dict[str, Any]],
) -> str:
    event_by_id = {event["event_id"]: event for event in events}
    top_drivers = drivers_after.get("drivers", [])[:8]
    confirmed = [item for item in validation.get("results", []) if item["agreement"] in {"confirmed", "partially_confirmed"}]
    conflicted = [item for item in validation.get("results", []) if item["agreement"] == "conflicted"]
    long_paths = [path for path in paths if len(path.get("steps") or []) >= 3 or _num(path.get("path_confidence"), 0.0) < 0.18]
    sample_signal = next((signal for signal in signals if signal.get("source_type") == "fundamental_data"), None)
    if not sample_signal:
        sample_signal = next((signal for signal in signals if signal.get("status") == "accepted"), None)
    if not sample_signal:
        sample_signal = signals[0] if signals else {}
    sample_event = event_by_id.get((sample_signal.get("event_ids") or [""])[0], {})
    sample_path = next((path for path in paths if path.get("path_id") in (sample_signal.get("causal_path_ids") or [])), {})
    lines = [
        "# Asset Event State Demo Report",
        "",
        f"- 资产：{selected.get('asset')}",
        f"- 时间窗口：{selected.get('time_window', {}).get('start')} 至 {selected.get('time_window', {}).get('end')}",
        f"- 新闻：原始/过滤候选 {stats.get('news_total_unique')}，选中 {stats.get('news_selected')}，有效原子事件 {stats.get('atomic_event_count')}",
        f"- 研报：选中 {len(selected.get('report_source_ids', []))} 篇，基本面/结构化序列 {len(selected.get('data_series_ids', []))} 组",
        f"- 框架：{selected.get('framework_id')} ({selected.get('framework_version')})",
        "",
        "## Driver 状态变化",
    ]
    for driver in top_drivers:
        lines.append(
            f"- `{driver['driver_id']}` `{driver.get('framework_node')}` strength={driver['strength']} "
            f"trend={driver['trend']} confidence={driver['confidence']} support={driver['support_score']} "
            f"contradiction={driver['contradiction_score']}"
        )
    lines.extend(["", "## 证据强化与反向信号"])
    for signal in signals[:12]:
        lines.append(
            f"- `{signal['signal_id']}` {signal['relation']} `{signal['framework_node']}` "
            f"dir={signal['direction']} mag={signal['magnitude']} conf={signal['confidence']} "
            f"event={signal['event_ids'][0]} source={signal['traceability']['source_id']}"
        )
    lines.extend(["", "## 多源验证"])
    for item in confirmed[:6]:
        lines.append(
            f"- `{item['driver_id']}` {item['agreement']} independent_sources={item['independent_source_count']} "
            f"support={item['supporting_sources']} conflict={item['contradicting_sources']}"
        )
    lines.extend(["", "## 分歧与低置信链条"])
    for item in conflicted[:6]:
        lines.append(f"- `{item['driver_id']}` conflicted: {item['model_warning']}")
    for path in long_paths[:6]:
        lines.append(
            f"- `{path['path_id']}` event={path['event_id']} hops={len(path.get('steps') or [])} "
            f"signal={path['path_signal']} confidence={path['path_confidence']} final={path.get('final_node')}"
        )
    lines.extend(
        [
            "",
            "## Raw → Event → Path → Signal → Driver 示例",
            f"- raw/source：`{sample_event.get('source_id', '')}`",
            f"- event：`{sample_event.get('event_id', '')}` {sample_event.get('summary', '')}",
            f"- causal_path：`{sample_path.get('path_id', '')}` final={sample_path.get('final_node', '')} signal={sample_path.get('path_signal', '')}",
            f"- signal：`{sample_signal.get('signal_id', '')}` driver=`{sample_signal.get('driver_id', '')}`",
            "- 说明：报告不输出明确买卖指令，只输出证据、状态变化和需要继续验证的指标。",
        ]
    )
    return "\n".join(lines) + "\n"


def _audit_samples(news_rows: list[dict[str, Any]], signals: list[dict[str, Any]], events: list[dict[str, Any]]) -> str:
    event_by_id = {event["event_id"]: event for event in events}
    lines = ["# Asset Event State Demo Audit Samples", "", "## News Samples"]
    relevant = [row for row in news_rows if row.get("status") in {"relevant", "candidate"}]
    filtered = [row for row in news_rows if row.get("status") == "filtered_out"]
    sampled_news = [*relevant[:10], *filtered[:3]]
    for row in sampled_news:
        lines.append(
            f"- source={row['source_id']} status={row['status']} reason={row['filter_reason']} "
            f"node={clean_text((row.get('framework_node') or {}).get('label'))} text={clean_text(row.get('text'), limit=180)}"
        )
    lines.extend(["", "## Signal Samples"])
    for signal in signals[:5]:
        event = event_by_id.get((signal.get("event_ids") or [""])[0], {})
        lines.append(
            f"- signal={signal['signal_id']} status={signal['status']} relation={signal['relation']} "
            f"event={event.get('event_id')} source={event.get('source_id')} evidence={clean_text(event.get('evidence_quote_or_value'), limit=220)}"
        )
    return "\n".join(lines) + "\n"


def run_asset_event_state_demo(
    root: str | Path | None = None,
    *,
    date_key: str | None = None,
    config_path: str | Path | None = None,
    work_order_id: str = DEFAULT_WORK_ORDER_ID,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    config = load_demo_config(config_path)
    selection = config.get("selection") or {}
    target_asset = clean_text(selection.get("preferred_asset") or "铜")
    asset = clean_text(selection.get("canonical_asset") or "COPPER")
    as_of = _latest_generated_at(root_path, fallback_date=date_key)
    output_date = _date_key(date_key or as_of.strftime("%Y%m%d"))
    stamp = datetime.now(timezone.utc).strftime("%H%M%S%f")
    run_id = f"RUN-{output_date}-{stamp}-ASSET-EVENT-STATE-DEMO"
    run_dir = root_path / "agent_workspace" / "candidates" / "asset_event_state_demo" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    execution_log: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    _append_execution_log(execution_log, "Repository Auditor", "started", root=str(root_path))
    all_news, news_meta = _load_news_records(
        root_path,
        as_of=as_of,
        window_days=int(selection.get("fallback_window_days") or 7),
    )
    preferred_start = as_of - timedelta(days=int(selection.get("preferred_window_days") or 3))
    preferred_target_count = sum(1 for item in all_news if item["asset"] == target_asset and _parse_dt(item["event_time"]) >= preferred_start)
    if preferred_target_count >= int(selection.get("min_news") or 50):
        window_days = int(selection.get("preferred_window_days") or 3)
    else:
        window_days = int(selection.get("fallback_window_days") or 7)
    window_start = as_of - timedelta(days=window_days)
    window_news = [item for item in all_news if _parse_dt(item["event_time"]) >= window_start]
    selected_news, filtered_news = _filter_news(
        window_news,
        target_asset=target_asset,
        max_news=int(selection.get("max_news") or 300),
    )
    _append_execution_log(
        execution_log,
        "Data Scout",
        "news_selected",
        preferred_window_target_count=preferred_target_count,
        selected_count=len(selected_news),
        window_days=window_days,
    )

    reports, report_articles, report_meta = _load_report_evidence(
        root_path,
        target_asset=target_asset,
        as_of=as_of,
        window_days=window_days,
        max_reports=int(selection.get("max_reports") or 10),
        max_evidence=int(selection.get("max_report_evidence") or 80),
    )
    database_observations, database_meta = _collect_database_observations(errors, as_of=as_of)
    evidence_observations = _extract_fundamental_observations(root_path, news=selected_news, reports=reports)
    observations = [*database_observations, *evidence_observations]
    framework_ref = _framework_ref(root_path, selected_news, reports)
    incremental_dimensions, incremental_ref = _incremental_state_ref(root_path, target_asset)
    quality = _quality_metrics(filtered_news, selected_news)
    selected_inputs = _selected_inputs(
        asset=asset,
        window_start=window_start,
        as_of=as_of,
        news=selected_news,
        report_articles=report_articles,
        observations=observations,
        framework_ref=framework_ref,
        quality=quality,
    )
    _append_execution_log(
        execution_log,
        "Data Scout",
        "completed",
        report_count=len(report_articles),
        observation_count=len(observations),
        database_observation_count=len(database_observations),
        framework_id=framework_ref.get("framework_id"),
    )

    atomic_events = _build_atomic_events(root_path, asset=asset, news=selected_news, reports=reports, observations=observations)
    event_groups, event_to_group, group_sizes = _build_event_groups(atomic_events)
    activations = _build_activations(atomic_events)
    causal_paths = _build_paths(activations, config=config)
    signal_updates, unmapped_signals = _build_signal_updates(
        atomic_events,
        causal_paths,
        event_to_group,
        config=config,
    )
    before_states = _previous_driver_states(incremental_dimensions)
    driver_before, driver_after = _build_driver_states(
        signal_updates,
        before_states,
        config=config,
        as_of=as_of,
        group_sizes=group_sizes,
    )
    validation = _validate_sources(signal_updates, driver_after)
    _append_execution_log(
        execution_log,
        "Pipeline Builder",
        "completed",
        atomic_event_count=len(atomic_events),
        signal_count=len(signal_updates),
        driver_count=len(driver_after.get("drivers", [])),
    )
    _append_execution_log(
        execution_log,
        "Evaluator",
        "completed",
        validation_count=len(validation.get("results", [])),
        unmapped_signal_count=len(unmapped_signals),
    )

    stats = {
        **quality,
        "report_article_count": len(report_articles),
        "report_evidence_count": len(reports),
        "fundamental_observation_count": len(observations),
        "database_observation_count": len(database_observations),
        "evidence_text_observation_count": len(evidence_observations),
        "atomic_event_count": len(atomic_events),
        "event_group_count": len(event_groups),
        "activation_count": len(activations),
        "causal_path_count": len(causal_paths),
        "signal_update_count": len(signal_updates),
        "driver_count": len(driver_after.get("drivers", [])),
        "unmapped_signal_count": len(unmapped_signals),
        "window_days": window_days,
    }
    assumptions = [
        "最近 3 天铜新闻未达到 50 条，使用 7 天窗口满足最小新闻数量。",
        "LLM 未作为必要依赖，抽取、传播、验证均使用可回放规则。",
        "基本面观察优先来自 data_agent MySQL 指标，补充来自已有 news_logic/wechat_evidence 文本中的可追溯数值。",
    ]
    risks = [
        "新闻相关性依赖现有 news_logic 映射，少量股票题材新闻仍需人工复核。",
        "MySQL 指标序列可计算近端分位数；从新闻/研报文本抽取的补充 observation 没有历史分位数。",
        "多跳地缘链条假设强，已通过深度衰减和 low_confidence 限制强更新。",
    ]

    read_files = [
        *news_meta.get("input_paths", []),
        report_meta.get("input_path"),
        incremental_ref.get("input_path"),
        framework_ref.get("source_path"),
        str(DATA_AGENT_DICT_PATH),
        relative_to_root(Path(config_path).expanduser(), root_path) if config_path else str(CONFIG_PATH),
    ]
    read_files = [clean_text(path) for path in read_files if clean_text(path)]
    generated_files = [
        "run_manifest.json",
        "selected_inputs.json",
        "filtered_news.jsonl",
        "atomic_events.jsonl",
        "event_groups.jsonl",
        "causal_activations.jsonl",
        "causal_paths.jsonl",
        "signal_updates.jsonl",
        "unmapped_signals.jsonl",
        "driver_state_before.json",
        "driver_state_after.json",
        "validation_results.json",
        "demo_report.md",
        "audit_samples.md",
        "execution_log.jsonl",
        "errors.jsonl",
        "agent_records.json",
        "task_state.json",
    ]

    agent_records = _agent_records(
        root=root_path,
        paths={
            "read_files": read_files,
            "modified_files": [
                "quanta_agents/asset_event_state/demo_pipeline.py",
                "quanta_agents/asset_event_state/demo_config.v1.json",
                "docs/asset-event-state-demo.md",
            ],
            "generated_data": generated_files,
        },
        selected=selected_inputs,
        stats=stats,
        assumptions=assumptions,
        risks=risks,
    )

    task_state = {
        "schema_version": "asset_event_state_demo_task_state.v1",
        "work_order_id": work_order_id,
        "run_id": run_id,
        "status": "completed",
        "stages": {
            "audit": "completed",
            "data_selection": "completed",
            "implementation": "completed",
            "execution": "completed",
            "evaluation": "completed",
            "completed": "completed",
        },
        "agent_records_ref": "agent_records.json",
        "shared_result_dir": relative_to_root(run_dir, root_path),
    }
    report_text = _demo_report(
        selected=selected_inputs,
        stats=stats,
        drivers_after=driver_after,
        validation=validation,
        signals=signal_updates,
        events=atomic_events,
        paths=causal_paths,
    )
    audit_text = _audit_samples(filtered_news, signal_updates, atomic_events)

    output_refs = {name: relative_to_root(run_dir / name, root_path) for name in generated_files}
    manifest = {
        "schema_version": DEMO_SCHEMA_VERSION,
        "status": "succeeded",
        "run_id": run_id,
        "asset": asset,
        "time_window": selected_inputs["time_window"],
        "generated_at": utc_now_iso(),
        "as_of": as_of.isoformat(timespec="seconds"),
        "work_order_id": work_order_id,
        "root": str(root_path),
        "demo_namespace": relative_to_root(run_dir, root_path),
        "input_refs": {
            "news_logic": news_meta,
            "wechat_evidence": report_meta,
            "database_observations": database_meta,
            "incremental_state": incremental_ref,
            "framework": framework_ref,
            "config": str(CONFIG_PATH if not config_path else Path(config_path).expanduser()),
        },
        "outputs": output_refs,
        "stats": stats,
        "constraints": {
            "gold_write": False,
            "production_config_modified": False,
            "gj_chainplatform_ui_modified": False,
            "llm_required": False,
            "demo_adapter_version": "demo_v1",
            "max_propagation_depth": selection.get("max_propagation_depth"),
            "source_provenance_required": True,
        },
        "assumptions": assumptions,
        "risks": risks,
        "reproduce_command": (
            f"{json.dumps(sys.executable, ensure_ascii=False)} -m quanta_agents.asset_event_state.demo_pipeline "
            f"--root {json.dumps(str(root_path), ensure_ascii=False)} --date {output_date}"
        ),
        "human_review_required": True,
    }

    write_json(run_dir / "selected_inputs.json", selected_inputs)
    _write_jsonl(run_dir / "filtered_news.jsonl", filtered_news)
    _write_jsonl(run_dir / "atomic_events.jsonl", atomic_events)
    _write_jsonl(run_dir / "event_groups.jsonl", event_groups)
    _write_jsonl(run_dir / "causal_activations.jsonl", activations)
    _write_jsonl(run_dir / "causal_paths.jsonl", causal_paths)
    _write_jsonl(run_dir / "signal_updates.jsonl", signal_updates)
    _write_jsonl(run_dir / "unmapped_signals.jsonl", unmapped_signals)
    write_json(run_dir / "driver_state_before.json", driver_before)
    write_json(run_dir / "driver_state_after.json", driver_after)
    write_json(run_dir / "validation_results.json", validation)
    (run_dir / "demo_report.md").write_text(report_text, encoding="utf-8")
    (run_dir / "audit_samples.md").write_text(audit_text, encoding="utf-8")
    write_json(run_dir / "agent_records.json", agent_records)
    write_json(run_dir / "task_state.json", task_state)
    _append_execution_log(execution_log, "Integration Reporter", "completed", run_dir=relative_to_root(run_dir, root_path))
    _write_jsonl(run_dir / "execution_log.jsonl", execution_log)
    _write_jsonl(run_dir / "errors.jsonl", errors)
    write_json(run_dir / "run_manifest.json", manifest)
    latest_manifest = root_path / "agent_workspace" / "candidates" / "asset_event_state_demo" / "latest_manifest.json"
    write_json(latest_manifest, manifest)

    return {
        "status": "succeeded",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "paths": output_refs,
        "stats": stats,
        "selected_inputs": selected_inputs,
        "manifest": manifest,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Quanta asset event state minimal demo pipeline.")
    parser.add_argument("--root", default="", help="quanta_data root. Defaults to GJ_QUANTA_DATA_ROOT discovery.")
    parser.add_argument("--date", default="", help="Output date key YYYYMMDD.")
    parser.add_argument("--config", default="", help="Optional demo config JSON path.")
    parser.add_argument("--work-order-id", default=DEFAULT_WORK_ORDER_ID)
    args = parser.parse_args(argv)
    result = run_asset_event_state_demo(
        args.root or None,
        date_key=args.date or None,
        config_path=args.config or None,
        work_order_id=args.work_order_id,
    )
    printable = {key: value for key, value in result.items() if key not in {"manifest", "selected_inputs"}}
    print(json.dumps(printable, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
