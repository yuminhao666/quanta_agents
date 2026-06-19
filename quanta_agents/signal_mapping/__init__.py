from __future__ import annotations

__all__ = [
    "build_polymarket_event_definition_signal",
    "load_theme_anchor_index",
    "map_futures_brief_candidates",
    "map_research_evidence_candidates",
    "run_signal_theme_mapping",
    "theme_anchor_ref",
    "validate_signal_theme_objects",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    if name in {"load_theme_anchor_index", "theme_anchor_ref"}:
        from . import theme_anchor_matcher

        return getattr(theme_anchor_matcher, name)
    from . import mapper

    return getattr(mapper, name)
