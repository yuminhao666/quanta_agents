from __future__ import annotations

__all__ = [
    "build_polymarket_event_definition_signal",
    "load_theme_anchor_index",
    "map_polymarket_hotspots_to_signals",
    "map_futures_brief_candidates",
    "map_research_evidence_candidates",
    "polymarket_market_to_research_signal",
    "run_polymarket_signal_mapping",
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
    if name in {
        "map_polymarket_hotspots_to_signals",
        "polymarket_market_to_research_signal",
        "run_polymarket_signal_mapping",
    }:
        from . import polymarket_mapper

        return getattr(polymarket_mapper, name)
    from . import mapper

    return getattr(mapper, name)
