from __future__ import annotations

from typing import Any, TypedDict


class NewsPipelineState(TypedDict, total=False):
    run_id: str
    raw_flashes: list[dict[str, Any]]
    normalized_news: list[dict[str, Any]]
    filtered_news: list[dict[str, Any]]
    syndication_groups: list[dict[str, Any]]
    event_mentions: list[dict[str, Any]]
    event_candidates: dict[str, list[dict[str, Any]]]
    event_decisions: list[dict[str, Any]]
    changed_event_ids: list[str]
    asset_mappings: list[dict[str, Any]]
    topic_candidates: dict[str, list[dict[str, Any]]]
    topic_decisions: list[dict[str, Any]]
    changed_topic_ids: list[str]
    review_items: list[dict[str, Any]]
    validation_errors: list[dict[str, Any]]
    metrics: dict[str, Any]


NEWS_EVENT_WORKFLOW_NODES = [
    "load_news",
    "normalize_filter",
    "dedup_syndication",
    "extract_mentions",
    "retrieve_event_candidates",
    "score_event_candidates",
    "judge_ambiguous_events",
    "persist_events",
    "map_assets",
    "retrieve_topics",
    "judge_ambiguous_topics",
    "persist_topics",
    "update_states",
    "export_legacy",
    "write_manifest",
]


EVENT_SCORE_ROUTES = {
    "auto_merge": "persist_events",
    "needs_llm": "judge_ambiguous_events",
    "new_event": "persist_events",
}


TOPIC_SCORE_ROUTES = {
    "auto_link": "persist_topics",
    "needs_llm": "judge_ambiguous_topics",
    "new_topic_candidate": "persist_topics",
}
