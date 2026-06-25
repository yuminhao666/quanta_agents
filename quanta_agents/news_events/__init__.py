"""News event sidecar pipeline.

The package keeps real-world news events separate from the legacy asset-level
`opinion_radar.news_logic` rows. Legacy rows can still be generated as a view,
but the durable objects here are news items, mentions, canonical events, topics,
and links.
"""

from .ids import stable_hash, stable_news_id
from .repository import NewsEventRepository, default_catalog_path, news_event_pipeline_enabled

__all__ = [
    "NewsEventRepository",
    "default_catalog_path",
    "news_event_pipeline_enabled",
    "stable_hash",
    "stable_news_id",
]
