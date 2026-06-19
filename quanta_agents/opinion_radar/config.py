from __future__ import annotations

import os
from pathlib import Path

from quanta_agents.core.config import REPO_ROOT, env_bool, env_int, first_env
from quanta_agents.core.llm_client import get_provider, normalize_provider_name


MYSQL = {
    "host": first_env("RADAR_MYSQL_HOST", "MYSQL_HOST", "GJ_FUTURES_MYSQL_HOST"),
    "port": env_int("RADAR_MYSQL_PORT", env_int("MYSQL_PORT", env_int("GJ_FUTURES_MYSQL_PORT", 3306))),
    "user": first_env("RADAR_MYSQL_USER", "MYSQL_USER", "GJ_FUTURES_MYSQL_USER"),
    "password": first_env("RADAR_MYSQL_PASSWORD", "MYSQL_PASSWORD", "GJ_FUTURES_MYSQL_PASSWORD"),
    "database": first_env("RADAR_MYSQL_DATABASE", "MYSQL_DATABASE", "GJ_FUTURES_MYSQL_DATABASE", default="quant_data"),
    "charset": first_env("RADAR_MYSQL_CHARSET", "MYSQL_CHARSET", "GJ_FUTURES_MYSQL_CHARSET", default="utf8mb4"),
}
FLASH_TABLE = first_env("RADAR_FLASH_TABLE", default="jin10_flash")

COMMODITY_FRAMEWORKS = Path(
    first_env(
        "GJ_COMMODITY_FRAMEWORKS_ROOT",
        default=str(REPO_ROOT.parent / "commodity_frameworks"),
    )
).expanduser()
CACHE_DIR = Path(
    first_env("QUANTA_AGENT_CACHE_DIR", default=str(Path.home() / ".cache" / "quanta_agents"))
).expanduser() / "opinion_radar"

RADAR_LLM_PROVIDER = normalize_provider_name(
    first_env("RADAR_LLM_PROVIDER", "QUANTA_AGENT_LLM_PROVIDER", default="deepseek")
)
_LLM_PROVIDER = get_provider(RADAR_LLM_PROVIDER, env_prefix="RADAR_LLM")
LLM = {
    "provider": _LLM_PROVIDER.name,
    "base_url": _LLM_PROVIDER.base_url,
    "api_keys": _LLM_PROVIDER.api_keys,
    "model": _LLM_PROVIDER.model,
    "extra_body": _LLM_PROVIDER.extra_body,
}
RADAR_LLM_PROVIDER = str(LLM["provider"])
LLM_MODEL = str(LLM["model"])
LLM_ENABLED = bool(LLM["api_keys"]) and env_bool("RADAR_LLM_ENABLED", True)

API_HOST = first_env("RADAR_API_HOST", default="127.0.0.1")
API_PORT = env_int("RADAR_API_PORT", 8090)


def mysql_configured() -> bool:
    return bool(MYSQL["host"] and MYSQL["user"] and MYSQL["database"])


def describe_llm() -> dict[str, object]:
    return {
        "enabled": LLM_ENABLED,
        "provider": RADAR_LLM_PROVIDER,
        "model": LLM_MODEL if LLM_ENABLED else "",
        "key_count": len(LLM["api_keys"]),
    }


os.makedirs(CACHE_DIR, exist_ok=True)
