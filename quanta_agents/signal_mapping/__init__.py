from __future__ import annotations

__all__ = [
    "build_polymarket_event_definition_signal",
    "map_futures_brief_candidates",
    "map_research_evidence_candidates",
    "run_signal_theme_mapping",
    "validate_signal_theme_objects",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    from . import mapper

    return getattr(mapper, name)
