from __future__ import annotations

from functools import lru_cache

from quanta_agents.core.taxonomy import load_asset_taxonomy


@lru_cache(maxsize=1)
def _taxonomy():
    return load_asset_taxonomy()


def variety_names() -> list[str]:
    return _taxonomy().names()


def classify(text: str) -> list[tuple[str, str, str]]:
    """Return matched buckets as [(key, label, kind)] from quanta_data taxonomy."""
    return [(hit.key, hit.label, hit.kind) for hit in _taxonomy().classify(text)]
