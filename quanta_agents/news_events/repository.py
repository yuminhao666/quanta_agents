from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from quanta_agents.core.config import env_bool, quanta_data_root
from quanta_agents.core.io import utc_now_iso


NEWS_EVENT_SCHEMA_VERSION = 1
DEFAULT_CATALOG_RELATIVE = Path("indexes/news_event_catalog/news_events.sqlite3")


def news_event_pipeline_enabled() -> bool:
    return env_bool("QUANTA_NEWS_EVENT_PIPELINE_ENABLED", default=False)


def default_catalog_path(root: str | Path | None = None) -> Path:
    configured = os.environ.get("QUANTA_NEWS_EVENT_CATALOG_PATH")
    if configured and configured.strip():
        return Path(configured).expanduser()
    return quanta_data_root(root) / DEFAULT_CATALOG_RELATIVE


def _json(data: Any) -> str:
    return json.dumps(data if data is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


class NewsEventRepository:
    """SQLite sidecar catalog for normalized news and real-world events."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        db_path: str | Path | None = None,
        dry_run: bool = False,
        initialize: bool = True,
    ) -> None:
        self.root = quanta_data_root(root)
        self.db_path = Path(db_path).expanduser() if db_path else default_catalog_path(self.root)
        self.dry_run = dry_run
        if initialize and not dry_run:
            self.migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection | None]:
        if self.dry_run:
            yield None
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            try:
                conn.execute("BEGIN")
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    def migrate(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migration (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    description TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS news_item (
                    news_id TEXT PRIMARY KEY,
                    source_system TEXT NOT NULL,
                    source_row_id TEXT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    normalized_text TEXT NOT NULL,
                    publish_time TEXT NOT NULL,
                    ingested_at TEXT NOT NULL,
                    channel TEXT,
                    important INTEGER NOT NULL,
                    url TEXT,
                    canonical_url TEXT,
                    content_hash TEXT NOT NULL,
                    filter_decision TEXT,
                    filter_reason_codes_json TEXT NOT NULL,
                    source_ref_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_news_item_source
                    ON news_item(source_system, source_row_id);
                CREATE INDEX IF NOT EXISTS idx_news_item_hash
                    ON news_item(content_hash);
                CREATE INDEX IF NOT EXISTS idx_news_item_publish_time
                    ON news_item(publish_time);

                CREATE TABLE IF NOT EXISTS syndication_group (
                    syndication_group_id TEXT PRIMARY KEY,
                    representative_news_id TEXT NOT NULL,
                    root_source_id TEXT,
                    independent_source_count INTEGER NOT NULL,
                    member_news_ids_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS syndication_member (
                    syndication_group_id TEXT NOT NULL,
                    news_id TEXT NOT NULL,
                    duplicate_type TEXT NOT NULL,
                    similarity REAL NOT NULL,
                    raw_json TEXT NOT NULL,
                    PRIMARY KEY (syndication_group_id, news_id)
                );
                CREATE INDEX IF NOT EXISTS idx_syndication_member_news
                    ON syndication_member(news_id);

                CREATE TABLE IF NOT EXISTS event_mention (
                    mention_id TEXT PRIMARY KEY,
                    source_news_id TEXT NOT NULL,
                    syndication_group_id TEXT,
                    event_type TEXT NOT NULL,
                    event_stage TEXT NOT NULL,
                    truth_status TEXT NOT NULL,
                    event_time TEXT,
                    canonical_summary TEXT NOT NULL,
                    extraction_method TEXT NOT NULL,
                    extraction_confidence REAL NOT NULL,
                    market_attention REAL NOT NULL,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_event_mention_source
                    ON event_mention(source_news_id);
                CREATE INDEX IF NOT EXISTS idx_event_mention_type_time
                    ON event_mention(event_type, event_time);

                CREATE TABLE IF NOT EXISTS canonical_event (
                    event_id TEXT PRIMARY KEY,
                    canonical_summary TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_time TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    truth_status TEXT NOT NULL,
                    market_attention REAL NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    core_entities_json TEXT NOT NULL,
                    core_signature_json TEXT NOT NULL,
                    mention_ids_json TEXT NOT NULL,
                    supporting_mention_ids_json TEXT NOT NULL,
                    contradicting_mention_ids_json TEXT NOT NULL,
                    source_count INTEGER NOT NULL,
                    independent_source_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_canonical_event_type_time
                    ON canonical_event(event_type, last_seen_at);

                CREATE TABLE IF NOT EXISTS canonical_event_member (
                    event_id TEXT NOT NULL,
                    mention_id TEXT NOT NULL,
                    relation_to_event TEXT NOT NULL,
                    source_news_id TEXT NOT NULL,
                    syndication_group_id TEXT,
                    raw_json TEXT NOT NULL,
                    PRIMARY KEY (event_id, mention_id)
                );
                CREATE INDEX IF NOT EXISTS idx_canonical_event_member_mention
                    ON canonical_event_member(mention_id);

                CREATE TABLE IF NOT EXISTS event_relation (
                    relation_id TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    target_event_id TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    reason TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_event_relation_source
                    ON event_relation(source_event_id, relation);
                CREATE INDEX IF NOT EXISTS idx_event_relation_target
                    ON event_relation(target_event_id, relation);

                CREATE TABLE IF NOT EXISTS event_asset_link (
                    link_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    asset_label TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    mapping_method TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_event_asset_link_event
                    ON event_asset_link(event_id);
                CREATE INDEX IF NOT EXISTS idx_event_asset_link_asset
                    ON event_asset_link(asset_id, event_id);

                CREATE TABLE IF NOT EXISTS event_framework_node_link (
                    link_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    framework_id TEXT,
                    node_id TEXT NOT NULL,
                    node_label TEXT NOT NULL,
                    dimension_label TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    mapping_method TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_event_framework_node_link_event
                    ON event_framework_node_link(event_id);

                CREATE TABLE IF NOT EXISTS persistent_topic (
                    topic_id TEXT PRIMARY KEY,
                    canonical_title TEXT NOT NULL,
                    topic_type TEXT NOT NULL,
                    definition TEXT NOT NULL,
                    core_entities_json TEXT NOT NULL,
                    core_event_types_json TEXT NOT NULL,
                    core_logic_node_ids_json TEXT NOT NULL,
                    included_scope_json TEXT NOT NULL,
                    excluded_scope_json TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_active_at TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    heat_score REAL NOT NULL,
                    credibility_score REAL NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_persistent_topic_title
                    ON persistent_topic(canonical_title);

                CREATE TABLE IF NOT EXISTS topic_alias (
                    alias_id TEXT PRIMARY KEY,
                    topic_id TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    status TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_topic_alias_topic
                    ON topic_alias(topic_id);

                CREATE TABLE IF NOT EXISTS topic_membership (
                    membership_id TEXT PRIMARY KEY,
                    object_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    membership_role TEXT NOT NULL,
                    relation_to_topic TEXT NOT NULL,
                    membership_score REAL NOT NULL,
                    assigned_by TEXT NOT NULL,
                    agent_run_id TEXT,
                    effective_time TEXT NOT NULL,
                    status TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_topic_membership_topic
                    ON topic_membership(topic_id, effective_time);
                CREATE INDEX IF NOT EXISTS idx_topic_membership_object
                    ON topic_membership(object_id);

                CREATE TABLE IF NOT EXISTS topic_state_snapshot (
                    snapshot_id TEXT PRIMARY KEY,
                    topic_id TEXT NOT NULL,
                    snapshot_time TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    heat_score REAL NOT NULL,
                    credibility_score REAL NOT NULL,
                    summary TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_run (
                    run_id TEXT PRIMARY KEY,
                    run_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    input_refs_json TEXT NOT NULL,
                    output_refs_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    errors_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migration(version, applied_at, description)
                VALUES (?, ?, ?)
                """,
                (NEWS_EVENT_SCHEMA_VERSION, utc_now_iso(), "initial news event catalog schema"),
            )

    def table_counts(self) -> dict[str, int]:
        tables = [
            "news_item",
            "syndication_group",
            "syndication_member",
            "event_mention",
            "canonical_event",
            "canonical_event_member",
            "event_relation",
            "event_asset_link",
            "event_framework_node_link",
            "persistent_topic",
            "topic_membership",
            "topic_state_snapshot",
            "agent_run",
        ]
        if self.dry_run or not self.db_path.exists():
            return {table: 0 for table in tables}
        with self.connect() as conn:
            return {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}

    def list_canonical_events(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        if self.dry_run or not self.db_path.exists():
            return []
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM canonical_event ORDER BY last_seen_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def get_canonical_event(self, event_id: str) -> dict[str, Any] | None:
        if self.dry_run or not self.db_path.exists():
            return None
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM canonical_event WHERE event_id = ?", (event_id,)).fetchone()
        return self._event_from_row(row) if row else None

    def list_topics(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        if self.dry_run or not self.db_path.exists():
            return []
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM persistent_topic ORDER BY last_active_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._topic_from_row(row) for row in rows]

    def get_topic(self, topic_id: str) -> dict[str, Any] | None:
        if self.dry_run or not self.db_path.exists():
            return None
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM persistent_topic WHERE topic_id = ?", (topic_id,)).fetchone()
        return self._topic_from_row(row) if row else None

    def event_asset_links(self, event_id: str) -> list[dict[str, Any]]:
        return self._rows_by("event_asset_link", "event_id", event_id)

    def event_framework_links(self, event_id: str) -> list[dict[str, Any]]:
        return self._rows_by("event_framework_node_link", "event_id", event_id)

    def event_relations(self, event_id: str) -> list[dict[str, Any]]:
        if self.dry_run or not self.db_path.exists():
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM event_relation
                WHERE source_event_id = ? OR target_event_id = ?
                ORDER BY created_at DESC
                """,
                (event_id, event_id),
            ).fetchall()
        return [dict(row) | _loads(row["raw_json"], {}) for row in rows]

    def topic_memberships(self, topic_id: str | None = None) -> list[dict[str, Any]]:
        if self.dry_run or not self.db_path.exists():
            return []
        sql = "SELECT * FROM topic_membership"
        params: tuple[Any, ...] = ()
        if topic_id:
            sql += " WHERE topic_id = ?"
            params = (topic_id,)
        sql += " ORDER BY effective_time DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) | _loads(row["raw_json"], {}) for row in rows]

    def _rows_by(self, table: str, column: str, value: str) -> list[dict[str, Any]]:
        if self.dry_run or not self.db_path.exists():
            return []
        with self.connect() as conn:
            rows = conn.execute(f"SELECT * FROM {table} WHERE {column} = ?", (value,)).fetchall()
        return [dict(row) | _loads(row["raw_json"], {}) for row in rows]

    def _event_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        raw = _loads(row["raw_json"], {})
        raw.update(
            {
                "event_id": row["event_id"],
                "canonical_summary": row["canonical_summary"],
                "event_type": row["event_type"],
                "event_time": row["event_time"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "truth_status": row["truth_status"],
                "market_attention": row["market_attention"],
                "lifecycle_state": row["lifecycle_state"],
                "core_entities": _loads(row["core_entities_json"], []),
                "core_signature": _loads(row["core_signature_json"], {}),
                "mention_ids": _loads(row["mention_ids_json"], []),
                "supporting_mention_ids": _loads(row["supporting_mention_ids_json"], []),
                "contradicting_mention_ids": _loads(row["contradicting_mention_ids_json"], []),
                "source_count": row["source_count"],
                "independent_source_count": row["independent_source_count"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        return raw

    def _topic_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        raw = _loads(row["raw_json"], {})
        raw.update(
            {
                "topic_id": row["topic_id"],
                "canonical_title": row["canonical_title"],
                "topic_type": row["topic_type"],
                "definition": row["definition"],
                "core_entities": _loads(row["core_entities_json"], []),
                "core_event_types": _loads(row["core_event_types_json"], []),
                "core_logic_node_ids": _loads(row["core_logic_node_ids_json"], []),
                "included_scope": _loads(row["included_scope_json"], []),
                "excluded_scope": _loads(row["excluded_scope_json"], []),
                "first_seen_at": row["first_seen_at"],
                "last_active_at": row["last_active_at"],
                "lifecycle_state": row["lifecycle_state"],
                "heat_score": row["heat_score"],
                "credibility_score": row["credibility_score"],
                "status": row["status"],
                "version": row["version"],
            }
        )
        return raw

    def upsert_all(self, payload: dict[str, list[dict[str, Any]]], *, agent_run: dict[str, Any]) -> None:
        if self.dry_run:
            return
        with self.transaction() as conn:
            assert conn is not None
            for item in payload.get("news_items", []):
                self._upsert_news_item(conn, item)
            for group in payload.get("syndication_groups", []):
                self._upsert_syndication_group(conn, group)
            for member in payload.get("syndication_members", []):
                self._upsert_syndication_member(conn, member)
            for mention in payload.get("event_mentions", []):
                self._upsert_event_mention(conn, mention)
            for event in payload.get("canonical_events", []):
                self._upsert_canonical_event(conn, event)
            for member in payload.get("canonical_event_members", []):
                self._upsert_canonical_event_member(conn, member)
            for relation in payload.get("event_relations", []):
                self._upsert_event_relation(conn, relation)
            for link in payload.get("event_asset_links", []):
                self._upsert_event_asset_link(conn, link)
            for link in payload.get("event_framework_node_links", []):
                self._upsert_event_framework_node_link(conn, link)
            for topic in payload.get("persistent_topics", []):
                self._upsert_persistent_topic(conn, topic)
            for alias in payload.get("topic_aliases", []):
                self._upsert_topic_alias(conn, alias)
            for membership in payload.get("topic_memberships", []):
                self._upsert_topic_membership(conn, membership)
            for snapshot in payload.get("topic_state_snapshots", []):
                self._upsert_topic_state_snapshot(conn, snapshot)
            self._upsert_agent_run(conn, agent_run)

    def _upsert_news_item(self, conn: sqlite3.Connection, item: dict[str, Any]) -> None:
        decision = item.get("filter_decision") if isinstance(item.get("filter_decision"), dict) else {}
        conn.execute(
            """
            INSERT INTO news_item (
                news_id, source_system, source_row_id, title, content, normalized_text,
                publish_time, ingested_at, channel, important, url, canonical_url,
                content_hash, filter_decision, filter_reason_codes_json, source_ref_json,
                raw_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(news_id) DO UPDATE SET
                title=excluded.title,
                content=excluded.content,
                normalized_text=excluded.normalized_text,
                publish_time=excluded.publish_time,
                ingested_at=excluded.ingested_at,
                channel=excluded.channel,
                important=excluded.important,
                url=excluded.url,
                canonical_url=excluded.canonical_url,
                content_hash=excluded.content_hash,
                filter_decision=excluded.filter_decision,
                filter_reason_codes_json=excluded.filter_reason_codes_json,
                source_ref_json=excluded.source_ref_json,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                item["news_id"],
                item["source_system"],
                item.get("source_row_id"),
                item.get("title") or "",
                item.get("content") or "",
                item.get("normalized_text") or "",
                item.get("publish_time") or "",
                item.get("ingested_at") or "",
                item.get("channel"),
                int(item.get("important") or 0),
                item.get("url"),
                item.get("canonical_url"),
                item.get("content_hash") or "",
                decision.get("decision"),
                _json(decision.get("reason_codes") or []),
                _json(item.get("source_ref") or {}),
                _json(item),
                utc_now_iso(),
            ),
        )

    def _upsert_syndication_group(self, conn: sqlite3.Connection, group: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO syndication_group (
                syndication_group_id, representative_news_id, root_source_id,
                independent_source_count, member_news_ids_json, raw_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(syndication_group_id) DO UPDATE SET
                representative_news_id=excluded.representative_news_id,
                root_source_id=excluded.root_source_id,
                independent_source_count=excluded.independent_source_count,
                member_news_ids_json=excluded.member_news_ids_json,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                group["syndication_group_id"],
                group["representative_news_id"],
                group.get("root_source_id"),
                int(group.get("independent_source_count") or 1),
                _json(group.get("member_news_ids") or []),
                _json(group),
                utc_now_iso(),
            ),
        )

    def _upsert_syndication_member(self, conn: sqlite3.Connection, member: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO syndication_member (
                syndication_group_id, news_id, duplicate_type, similarity, raw_json
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(syndication_group_id, news_id) DO UPDATE SET
                duplicate_type=excluded.duplicate_type,
                similarity=excluded.similarity,
                raw_json=excluded.raw_json
            """,
            (
                member["syndication_group_id"],
                member["news_id"],
                member.get("duplicate_type") or "representative",
                float(member.get("similarity") or 1.0),
                _json(member),
            ),
        )

    def _upsert_event_mention(self, conn: sqlite3.Connection, mention: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO event_mention (
                mention_id, source_news_id, syndication_group_id, event_type, event_stage,
                truth_status, event_time, canonical_summary, extraction_method,
                extraction_confidence, market_attention, raw_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mention_id) DO UPDATE SET
                syndication_group_id=excluded.syndication_group_id,
                event_type=excluded.event_type,
                event_stage=excluded.event_stage,
                truth_status=excluded.truth_status,
                event_time=excluded.event_time,
                canonical_summary=excluded.canonical_summary,
                extraction_method=excluded.extraction_method,
                extraction_confidence=excluded.extraction_confidence,
                market_attention=excluded.market_attention,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                mention["mention_id"],
                mention["source_news_id"],
                mention.get("syndication_group_id"),
                mention.get("event_type") or "other",
                mention.get("event_stage") or "observation",
                mention.get("truth_status") or "unverified",
                mention.get("event_time"),
                mention.get("canonical_summary") or "",
                mention.get("extraction_method") or "rule_fallback",
                float(mention.get("extraction_confidence") or 0.0),
                float(mention.get("market_attention") or 0.0),
                _json(mention),
                mention.get("created_at") or utc_now_iso(),
                utc_now_iso(),
            ),
        )

    def _upsert_canonical_event(self, conn: sqlite3.Connection, event: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO canonical_event (
                event_id, canonical_summary, event_type, event_time, first_seen_at,
                last_seen_at, truth_status, market_attention, lifecycle_state,
                core_entities_json, core_signature_json, mention_ids_json,
                supporting_mention_ids_json, contradicting_mention_ids_json,
                source_count, independent_source_count, status, raw_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                canonical_summary=excluded.canonical_summary,
                event_type=excluded.event_type,
                event_time=excluded.event_time,
                first_seen_at=excluded.first_seen_at,
                last_seen_at=excluded.last_seen_at,
                truth_status=excluded.truth_status,
                market_attention=excluded.market_attention,
                lifecycle_state=excluded.lifecycle_state,
                core_entities_json=excluded.core_entities_json,
                core_signature_json=excluded.core_signature_json,
                mention_ids_json=excluded.mention_ids_json,
                supporting_mention_ids_json=excluded.supporting_mention_ids_json,
                contradicting_mention_ids_json=excluded.contradicting_mention_ids_json,
                source_count=excluded.source_count,
                independent_source_count=excluded.independent_source_count,
                status=excluded.status,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                event["event_id"],
                event.get("canonical_summary") or "",
                event.get("event_type") or "other",
                event.get("event_time"),
                event.get("first_seen_at") or utc_now_iso(),
                event.get("last_seen_at") or utc_now_iso(),
                event.get("truth_status") or "unverified",
                float(event.get("market_attention") or 0.0),
                event.get("lifecycle_state") or "new",
                _json(event.get("core_entities") or []),
                _json(event.get("core_signature") or {}),
                _json(event.get("mention_ids") or []),
                _json(event.get("supporting_mention_ids") or []),
                _json(event.get("contradicting_mention_ids") or []),
                int(event.get("source_count") or 0),
                int(event.get("independent_source_count") or 0),
                event.get("status") or "candidate",
                _json(event),
                event.get("created_at") or utc_now_iso(),
                event.get("updated_at") or utc_now_iso(),
            ),
        )

    def _upsert_canonical_event_member(self, conn: sqlite3.Connection, member: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO canonical_event_member (
                event_id, mention_id, relation_to_event, source_news_id,
                syndication_group_id, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, mention_id) DO UPDATE SET
                relation_to_event=excluded.relation_to_event,
                source_news_id=excluded.source_news_id,
                syndication_group_id=excluded.syndication_group_id,
                raw_json=excluded.raw_json
            """,
            (
                member["event_id"],
                member["mention_id"],
                member.get("relation_to_event") or "SAME_EVENT",
                member.get("source_news_id") or "",
                member.get("syndication_group_id"),
                _json(member),
            ),
        )

    def _upsert_event_relation(self, conn: sqlite3.Connection, relation: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO event_relation (
                relation_id, source_event_id, relation, target_event_id,
                confidence, reason, raw_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(relation_id) DO UPDATE SET
                confidence=excluded.confidence,
                reason=excluded.reason,
                raw_json=excluded.raw_json
            """,
            (
                relation["relation_id"],
                relation["source_event_id"],
                relation["relation"],
                relation["target_event_id"],
                float(relation.get("confidence") or 0.0),
                relation.get("reason") or "",
                _json(relation),
                relation.get("created_at") or utc_now_iso(),
            ),
        )

    def _upsert_event_asset_link(self, conn: sqlite3.Connection, link: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO event_asset_link (
                link_id, event_id, asset_id, asset_label, relation, confidence,
                mapping_method, raw_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(link_id) DO UPDATE SET
                relation=excluded.relation,
                confidence=excluded.confidence,
                mapping_method=excluded.mapping_method,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                link["link_id"],
                link["event_id"],
                link["asset_id"],
                link.get("asset_label") or link["asset_id"],
                link.get("relation") or "contextual",
                float(link.get("confidence") or 0.0),
                link.get("mapping_method") or "rule",
                _json(link),
                utc_now_iso(),
            ),
        )

    def _upsert_event_framework_node_link(self, conn: sqlite3.Connection, link: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO event_framework_node_link (
                link_id, event_id, asset_id, framework_id, node_id, node_label,
                dimension_label, confidence, mapping_method, raw_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(link_id) DO UPDATE SET
                node_label=excluded.node_label,
                dimension_label=excluded.dimension_label,
                confidence=excluded.confidence,
                mapping_method=excluded.mapping_method,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                link["link_id"],
                link["event_id"],
                link["asset_id"],
                link.get("framework_id"),
                link["node_id"],
                link.get("node_label") or link["node_id"],
                link.get("dimension_label") or link.get("node_label") or link["node_id"],
                float(link.get("confidence") or 0.0),
                link.get("mapping_method") or "rule",
                _json(link),
                utc_now_iso(),
            ),
        )

    def _upsert_persistent_topic(self, conn: sqlite3.Connection, topic: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO persistent_topic (
                topic_id, canonical_title, topic_type, definition, core_entities_json,
                core_event_types_json, core_logic_node_ids_json, included_scope_json,
                excluded_scope_json, first_seen_at, last_active_at, lifecycle_state,
                heat_score, credibility_score, status, version, raw_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(topic_id) DO UPDATE SET
                canonical_title=excluded.canonical_title,
                definition=excluded.definition,
                core_entities_json=excluded.core_entities_json,
                core_event_types_json=excluded.core_event_types_json,
                core_logic_node_ids_json=excluded.core_logic_node_ids_json,
                included_scope_json=excluded.included_scope_json,
                excluded_scope_json=excluded.excluded_scope_json,
                last_active_at=excluded.last_active_at,
                lifecycle_state=excluded.lifecycle_state,
                heat_score=excluded.heat_score,
                credibility_score=excluded.credibility_score,
                status=excluded.status,
                version=excluded.version,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                topic["topic_id"],
                topic.get("canonical_title") or topic["topic_id"],
                topic.get("topic_type") or "event_topic",
                topic.get("definition") or "",
                _json(topic.get("core_entities") or []),
                _json(topic.get("core_event_types") or []),
                _json(topic.get("core_logic_node_ids") or []),
                _json(topic.get("included_scope") or []),
                _json(topic.get("excluded_scope") or []),
                topic.get("first_seen_at") or utc_now_iso(),
                topic.get("last_active_at") or utc_now_iso(),
                topic.get("lifecycle_state") or "candidate",
                float(topic.get("heat_score") or 0.0),
                float(topic.get("credibility_score") or 0.0),
                topic.get("status") or "candidate",
                int(topic.get("version") or 1),
                _json(topic),
                utc_now_iso(),
            ),
        )

    def _upsert_topic_alias(self, conn: sqlite3.Connection, alias: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO topic_alias(alias_id, topic_id, alias, status, raw_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(alias_id) DO UPDATE SET
                alias=excluded.alias,
                status=excluded.status,
                raw_json=excluded.raw_json
            """,
            (
                alias["alias_id"],
                alias["topic_id"],
                alias.get("alias") or "",
                alias.get("status") or "candidate",
                _json(alias),
            ),
        )

    def _upsert_topic_membership(self, conn: sqlite3.Connection, membership: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO topic_membership (
                membership_id, object_id, topic_id, membership_role,
                relation_to_topic, membership_score, assigned_by, agent_run_id,
                effective_time, status, raw_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(membership_id) DO UPDATE SET
                membership_role=excluded.membership_role,
                relation_to_topic=excluded.relation_to_topic,
                membership_score=excluded.membership_score,
                assigned_by=excluded.assigned_by,
                agent_run_id=excluded.agent_run_id,
                effective_time=excluded.effective_time,
                status=excluded.status,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                membership["membership_id"],
                membership["object_id"],
                membership["topic_id"],
                membership.get("membership_role") or "primary",
                membership.get("relation_to_topic") or "updates",
                float(membership.get("membership_score") or 0.0),
                membership.get("assigned_by") or "rule",
                membership.get("agent_run_id"),
                membership.get("effective_time") or utc_now_iso(),
                membership.get("status") or "candidate",
                _json(membership),
                utc_now_iso(),
            ),
        )

    def _upsert_topic_state_snapshot(self, conn: sqlite3.Connection, snapshot: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO topic_state_snapshot (
                snapshot_id, topic_id, snapshot_time, lifecycle_state,
                heat_score, credibility_score, summary, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id) DO UPDATE SET
                lifecycle_state=excluded.lifecycle_state,
                heat_score=excluded.heat_score,
                credibility_score=excluded.credibility_score,
                summary=excluded.summary,
                raw_json=excluded.raw_json
            """,
            (
                snapshot["snapshot_id"],
                snapshot["topic_id"],
                snapshot.get("snapshot_time") or utc_now_iso(),
                snapshot.get("lifecycle_state") or "candidate",
                float(snapshot.get("heat_score") or 0.0),
                float(snapshot.get("credibility_score") or 0.0),
                snapshot.get("summary") or "",
                _json(snapshot),
            ),
        )

    def _upsert_agent_run(self, conn: sqlite3.Connection, run: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO agent_run (
                run_id, run_type, status, started_at, finished_at, input_refs_json,
                output_refs_json, metrics_json, errors_json, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status=excluded.status,
                finished_at=excluded.finished_at,
                output_refs_json=excluded.output_refs_json,
                metrics_json=excluded.metrics_json,
                errors_json=excluded.errors_json,
                raw_json=excluded.raw_json
            """,
            (
                run["run_id"],
                run.get("run_type") or "news_event_batch",
                run.get("status") or "succeeded",
                run.get("started_at") or utc_now_iso(),
                run.get("finished_at"),
                _json(run.get("input_refs") or []),
                _json(run.get("output_refs") or []),
                _json(run.get("metrics") or {}),
                _json(run.get("errors") or []),
                _json(run),
            ),
        )
