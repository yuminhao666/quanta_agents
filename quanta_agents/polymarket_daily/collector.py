from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quanta_agents.core.io import utc_now_iso


DEFAULT_MARKET_POOLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "volume_24h",
        ("markets", "list", "--active", "true", "--closed", "false", "--order", "volume24hr"),
    ),
    (
        "volume_1w",
        ("markets", "list", "--active", "true", "--closed", "false", "--order", "volume1wk"),
    ),
    (
        "liquidity",
        (
            "markets",
            "list",
            "--active",
            "true",
            "--closed",
            "false",
            "--order",
            "liquidity_num",
        ),
    ),
    (
        "positive_price_move_1d",
        (
            "markets",
            "list",
            "--active",
            "true",
            "--closed",
            "false",
            "--order",
            "oneDayPriceChange",
        ),
    ),
    (
        "negative_price_move_1d",
        (
            "markets",
            "list",
            "--active",
            "true",
            "--closed",
            "false",
            "--order",
            "oneDayPriceChange",
            "--ascending",
        ),
    ),
    (
        "new_markets",
        ("markets", "list", "--active", "true", "--closed", "false", "--order", "createdAt"),
    ),
)


@dataclass(frozen=True)
class CliResult:
    command: list[str]
    stdout: str
    stderr: str
    returncode: int
    payload: Any


class PolymarketCliError(RuntimeError):
    """Raised when the Polymarket CLI cannot produce usable JSON."""


def resolve_cli(explicit: str | Path | None = None) -> str:
    if explicit:
        return str(Path(explicit).expanduser())
    configured = os.environ.get("POLYMARKET_CLI")
    if configured:
        return str(Path(configured).expanduser())
    discovered = shutil.which("polymarket")
    if discovered:
        return discovered
    return "polymarket"


def parse_json_payload(text: str) -> Any:
    value = text.strip()
    if not value:
        raise ValueError("empty JSON payload")
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        starts = [pos for pos in (value.find("["), value.find("{")) if pos >= 0]
        for start in sorted(starts):
            try:
                parsed, _ = decoder.raw_decode(value[start:])
                return parsed
            except json.JSONDecodeError:
                continue
        raise


def run_json_cli(
    args: list[str] | tuple[str, ...],
    *,
    cli: str | Path | None = None,
    timeout: int = 90,
) -> CliResult:
    executable = resolve_cli(cli)
    command = [executable, "-o", "json", *list(args)]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip()
        raise PolymarketCliError(
            f"polymarket cli failed ({completed.returncode}): {' '.join(command)}\n{message}"
        )
    try:
        payload = parse_json_payload(completed.stdout)
    except Exception as exc:  # pragma: no cover - message is more useful than subtype here
        raise PolymarketCliError(
            f"polymarket cli returned non-JSON output: {' '.join(command)}"
        ) from exc
    return CliResult(
        command=command,
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
        payload=payload,
    )


def collect_market_pools(
    *,
    cli: str | Path | None = None,
    limit: int = 80,
    timeout: int = 90,
    pools: tuple[tuple[str, tuple[str, ...]], ...] = DEFAULT_MARKET_POOLS,
) -> dict[str, Any]:
    """Collect several sorted market pools from the Polymarket CLI."""
    if limit <= 0:
        raise ValueError("limit must be positive")

    resolved_cli = resolve_cli(cli)
    collected: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for pool_name, base_args in pools:
        args = [*base_args, "--limit", str(limit)]
        try:
            result = run_json_cli(args, cli=resolved_cli, timeout=timeout)
            payload = result.payload
            item_count = len(payload) if isinstance(payload, list) else 1
            collected.append(
                {
                    "pool": pool_name,
                    "command": result.command,
                    "stderr": result.stderr.strip(),
                    "item_count": item_count,
                    "payload": payload,
                }
            )
        except Exception as exc:
            errors.append({"pool": pool_name, "args": args, "error": str(exc)})

    if not collected:
        raise PolymarketCliError(f"all polymarket market pool commands failed: {errors}")

    return {
        "schema_version": "polymarket_cli_snapshot.v1",
        "source": "polymarket_cli",
        "cli": resolved_cli,
        "collected_at": utc_now_iso(),
        "limit_per_pool": limit,
        "pools": collected,
        "errors": errors,
    }
