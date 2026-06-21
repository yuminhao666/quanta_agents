from __future__ import annotations

from pathlib import Path

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import relative_to_root


class ContextResolver:
    def __init__(self, root: str | Path | None = None):
        self.root = quanta_data_root(root)

    def resolve_path(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        return candidate if candidate.is_absolute() else self.root / candidate

    def relative_path(self, path: str | Path) -> str:
        return relative_to_root(self.resolve_path(path), self.root)
