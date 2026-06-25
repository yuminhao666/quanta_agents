from __future__ import annotations

from typing import Protocol


class ObjectRepository(Protocol):
    def get_object(self, object_id: str) -> dict | None: ...

    def get_object_by_source(self, source_type: str, source_id: str) -> dict | None: ...

    def trace_lineage(self, object_id: str, *, max_depth: int = 8) -> dict: ...
