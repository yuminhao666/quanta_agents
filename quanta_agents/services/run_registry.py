from __future__ import annotations

from pathlib import Path


def latest_run_dir(base: str | Path, *, required_files: list[str] | None = None) -> Path | None:
    base_path = Path(base).expanduser()
    required_files = required_files or []
    candidates = [
        path
        for path in sorted(base_path.glob("*/*/*/RUN-*"))
        if path.is_dir() and all((path / item).exists() for item in required_files)
    ]
    return candidates[-1] if candidates else None
