from __future__ import annotations

from collections import defaultdict
from typing import Any

from .ids import stable_hash


def build_news_logic_compatible_view(
    events: list[dict[str, Any]],
    asset_links: list[dict[str, Any]],
    framework_links: list[dict[str, Any]],
    *,
    news_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Project canonical events back into legacy asset-level news_logic rows."""
    news_by_id = news_by_id or {}
    assets_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    framework_by_event_asset: dict[tuple[str, str], dict[str, Any]] = {}
    for link in asset_links:
        assets_by_event[link["event_id"]].append(link)
    for link in framework_links:
        framework_by_event_asset[(link["event_id"], link["asset_id"])] = link

    rows: list[dict[str, Any]] = []
    for event in events:
        source_news_id = ""
        representative_mentions = event.get("representative_mentions") or []
        if representative_mentions:
            source_news_id = representative_mentions[0].get("source_news_id") or ""
        news = news_by_id.get(source_news_id) or {}
        for asset in assets_by_event.get(event["event_id"], []):
            framework = framework_by_event_asset.get((event["event_id"], asset["asset_id"])) or {}
            rows.append(
                {
                    "event_id": f"NLOGIC-{stable_hash(event['event_id'], asset['asset_id'], framework.get('node_id'))}",
                    "canonical_event_id": event["event_id"],
                    "flash_id": source_news_id,
                    "asset": asset.get("asset_label"),
                    "asset_id": asset.get("asset_id"),
                    "publish_time": event.get("event_time") or event.get("last_seen_at"),
                    "text": event.get("canonical_summary"),
                    "url": news.get("url"),
                    "channel": news.get("channel"),
                    "important": int(news.get("important") or 0),
                    "direction_score": 0.0,
                    "heat": round(float(event.get("market_attention") or 0.0), 3),
                    "framework": {
                        "framework_id": framework.get("framework_id") or "",
                        "asset_id": asset.get("asset_id"),
                    },
                    "framework_node": {
                        "node_id": framework.get("node_id") or "default::未归类",
                        "label": framework.get("node_label") or "未归类",
                        "dimension_label": framework.get("dimension_label") or "未归类",
                    },
                    "match": {
                        "method": framework.get("mapping_method")
                        or asset.get("mapping_method")
                        or "news_event_sidecar_adapter",
                        "confidence": min(
                            float(asset.get("confidence") or 0.0),
                            float(framework.get("confidence") or 1.0),
                        ),
                    },
                    "consistency": {
                        "status": "tracking",
                        "reason": "news_event_sidecar compatibility view",
                    },
                    "theme_anchor_refs": [],
                    "anchoring_status": "unanchored_theme_candidate",
                    "theme_anchor_match_method": "news_event_topic_sidecar",
                }
            )
    return rows
