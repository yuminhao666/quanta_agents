from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_json_dumps(data: Any, *, indent: int | None = 2) -> str:
    return json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=False, default=str)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(path)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    atomic_write_text(path, stable_json_dumps(data, indent=indent))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def relative_to_root(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def dated_parts(date_key: str) -> tuple[str, str, str]:
    if len(date_key) != 8 or not date_key.isdigit():
        raise ValueError("date must be YYYYMMDD")
    return date_key[:4], date_key[4:6], date_key[6:8]
