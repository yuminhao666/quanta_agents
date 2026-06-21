from __future__ import annotations

from pathlib import Path
from typing import Any

from quanta_agents.core.io import read_json, relative_to_root


class FilesystemRepository:
    """Small helper for adapters that need schema-safe relative quanta_data refs."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()

    def resolve(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        return candidate if candidate.is_absolute() else self.root / candidate

    def read_json(self, path: str | Path) -> Any:
        return read_json(self.resolve(path))

    def relative(self, path: str | Path) -> str:
        return relative_to_root(self.resolve(path), self.root)
