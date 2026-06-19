from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from . import config


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _table_name() -> str:
    table = config.FLASH_TABLE.strip()
    if not _IDENT_RE.fullmatch(table):
        raise RuntimeError(f"RADAR_FLASH_TABLE 不是合法表名：{table!r}")
    return table


def _connect() -> Any:
    if not config.mysql_configured():
        raise RuntimeError("舆情雷达缺少 MySQL 配置，请设置 RADAR_MYSQL_* 或 MYSQL_* 环境变量。")
    try:
        import pymysql  # type: ignore
        import pymysql.cursors  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment diagnostic
        raise RuntimeError('缺少 PyMySQL 依赖，请安装：python3 -m pip install -e ".[radar]"') from exc

    return pymysql.connect(
        host=config.MYSQL["host"],
        port=config.MYSQL["port"],
        user=config.MYSQL["user"],
        password=config.MYSQL["password"],
        database=config.MYSQL["database"],
        charset=config.MYSQL["charset"],
        connect_timeout=10,
        cursorclass=pymysql.cursors.DictCursor,
    )


def ping() -> dict[str, Any]:
    try:
        table = _table_name()
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n, MIN(publish_time) AS a, MAX(publish_time) AS b FROM {table}")
            row = cur.fetchone()
        return {"ok": True, "count": row["n"], "min_time": str(row["a"]), "max_time": str(row["b"])}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def latest_time() -> datetime | None:
    table = _table_name()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT MAX(publish_time) AS t FROM {table}")
        row = cur.fetchone()
    return row["t"] if row else None


def fetch_flashes(start: datetime, end: datetime) -> list[dict[str, Any]]:
    table = _table_name()
    sql = (
        f"SELECT id, flash_id, publish_time, important, channel, title, content, url "
        f"FROM {table} WHERE publish_time >= %s AND publish_time < %s "
        f"ORDER BY publish_time ASC"
    )
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (start, end))
        rows = list(cur.fetchall())
    for row in rows:
        row["publish_time"] = row["publish_time"].isoformat(sep=" ") if row["publish_time"] else None
        row["important"] = int(row["important"] or 0)
    return rows
