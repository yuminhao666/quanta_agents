from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUANTA_DATA_ROOT = Path.home() / "quanta_data"
LEGACY_QUANTA_DATA_ROOTS = (
    Path.home() / "Documents" / "quanta_data",
    Path.home() / "document" / "quanta_data",
    Path("/Volumes/数字大脑/quanta_data"),
)


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE env files without overriding existing values."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)


def load_default_env() -> None:
    """Load this repo's .env, then sibling gj_chainplatform/.env as fallback."""
    load_env_file(REPO_ROOT / ".env")
    load_env_file(REPO_ROOT.parent / "gj_chainplatform" / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    return int(raw_value)


def env_path(name: str, default: str | Path) -> Path:
    return Path(os.environ.get(name, str(default))).expanduser()


def first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def quanta_data_root(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()

    raw_configured = os.getenv("GJ_QUANTA_DATA_ROOT")
    if raw_configured is None or not raw_configured.strip():
        raw_configured = os.getenv("QUANTA_DATA_ROOT")
    if raw_configured is not None and raw_configured.strip():
        return Path(raw_configured).expanduser()

    default = DEFAULT_QUANTA_DATA_ROOT.expanduser()
    if default.exists():
        return default

    taxonomy_marker = Path("gold/reference_data/assets/futures_assets.v1.json")
    for legacy_root in LEGACY_QUANTA_DATA_ROOTS:
        legacy = legacy_root.expanduser()
        if (legacy / taxonomy_marker).exists():
            return legacy
    return default


load_default_env()
