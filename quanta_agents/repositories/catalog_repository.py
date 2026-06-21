from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from quanta_agents.core.config import env_bool, quanta_data_root
from quanta_agents.domain import (
    AgentRun,
    CanonicalEvent,
    EventAssetLink,
    EventNodeActivation,
    EventSourceLink,
    EventTopicMembership,
    ObjectRelation,
    ReportDependency,
    ResearchObject,
    Topic,
    TopicAlias,
    TopicMembership,
    TopicStateSnapshot,
)


CATALOG_SCHEMA_VERSION = 1
DEFAULT_CATALOG_RELATIVE = Path("indexes/object_catalog/quanta_catalog.sqlite3")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def object_catalog_enabled() -> bool:
    return env_bool("QUANTA_OBJECT_CATALOG_ENABLED", default=False)


def default_catalog_path(root: str | Path | None = None) -> Path:
    env_path = os.environ.get("QUANTA_OBJECT_CATALOG_PATH")
    if env_path and env_path.strip():
        return Path(env_path).expanduser()
    return quanta_data_root(root) / DEFAULT_CATALOG_RELATIVE


def _json(data: Any) -> str:
    return json.dumps(data if data is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def _dict(model_or_dict: Any) -> dict[str, Any]:
    if hasattr(model_or_dict, "model_dump"):
        return model_or_dict.model_dump(mode="json")
    return dict(model_or_dict or {})


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class CatalogRepository:
    """SQLite sidecar index for Research Objects, relations, events, and topics."""

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

                CREATE TABLE IF NOT EXISTS research_object (
                    object_id TEXT PRIMARY KEY,
                    object_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content_uri TEXT,
                    content_hash TEXT,
                    source_type TEXT NOT NULL,
                    source_id TEXT,
                    published_at TEXT,
                    event_time TEXT,
                    recorded_at TEXT NOT NULL,
                    truth_status TEXT NOT NULL,
                    lifecycle_status TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_research_object_source
                    ON research_object(source_type, source_id);
                CREATE INDEX IF NOT EXISTS idx_research_object_type_time
                    ON research_object(object_type, event_time, recorded_at);

                CREATE TABLE IF NOT EXISTS object_relation (
                    relation_id TEXT PRIMARY KEY,
                    from_object_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    to_object_id TEXT NOT NULL,
                    polarity INTEGER,
                    confidence REAL NOT NULL,
                    effective_from TEXT,
                    effective_to TEXT,
                    evidence_object_id TEXT,
                    agent_run_id TEXT,
                    status TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_object_relation_from
                    ON object_relation(from_object_id, relation_type);
                CREATE INDEX IF NOT EXISTS idx_object_relation_to
                    ON object_relation(to_object_id, relation_type);
                CREATE INDEX IF NOT EXISTS idx_object_relation_trace
                    ON object_relation(evidence_object_id, agent_run_id);

                CREATE TABLE IF NOT EXISTS canonical_event (
                    event_id TEXT PRIMARY KEY,
                    canonical_summary TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    truth_status TEXT NOT NULL,
                    market_attention REAL NOT NULL,
                    entities_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT,
                    metadata_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_canonical_event_time
                    ON canonical_event(event_time, event_type);
                CREATE INDEX IF NOT EXISTS idx_canonical_event_source
                    ON canonical_event(source_type, source_id);

                CREATE TABLE IF NOT EXISTS event_source_link (
                    link_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    source_object_id TEXT NOT NULL,
                    source_id TEXT,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_event_source_link_event
                    ON event_source_link(event_id);

                CREATE TABLE IF NOT EXISTS event_asset_link (
                    link_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    asset_label TEXT NOT NULL,
                    polarity INTEGER,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_event_asset_link_asset
                    ON event_asset_link(asset_id, event_id);

                CREATE TABLE IF NOT EXISTS event_node_activation (
                    activation_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    logic_node_id TEXT NOT NULL,
                    logic_node_label TEXT,
                    asset_id TEXT,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_event_node_activation_node
                    ON event_node_activation(logic_node_id, event_id);

                CREATE TABLE IF NOT EXISTS event_topic_membership (
                    membership_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS topic (
                    topic_id TEXT PRIMARY KEY,
                    canonical_title TEXT NOT NULL,
                    topic_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_active_at TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    heat_score REAL NOT NULL,
                    credibility_score REAL NOT NULL,
                    source_diversity REAL NOT NULL,
                    contradiction_score REAL NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_topic_type_active
                    ON topic(topic_type, last_active_at);
                CREATE INDEX IF NOT EXISTS idx_topic_title
                    ON topic(canonical_title);

                CREATE TABLE IF NOT EXISTS topic_alias (
                    alias_id TEXT PRIMARY KEY,
                    topic_id TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    language TEXT,
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
                    metadata_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL
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
                    source_diversity REAL NOT NULL,
                    contradiction_score REAL NOT NULL,
                    summary TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_topic_state_snapshot_topic
                    ON topic_state_snapshot(topic_id, snapshot_time);

                CREATE TABLE IF NOT EXISTS agent_run (
                    run_id TEXT PRIMARY KEY,
                    run_type TEXT NOT NULL,
                    source_id TEXT,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    manifest_uri TEXT,
                    input_refs_json TEXT NOT NULL,
                    output_refs_json TEXT NOT NULL,
                    errors_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS report_dependency (
                    dependency_id TEXT PRIMARY KEY,
                    report_object_id TEXT NOT NULL,
                    dependency_object_id TEXT NOT NULL,
                    dependency_type TEXT NOT NULL,
                    agent_run_id TEXT,
                    evidence_weight REAL NOT NULL,
                    metadata_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_report_dependency_report
                    ON report_dependency(report_object_id);
                CREATE INDEX IF NOT EXISTS idx_report_dependency_source
                    ON report_dependency(dependency_object_id);
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migration(version, applied_at, description)
                VALUES (?, ?, ?)
                """,
                (CATALOG_SCHEMA_VERSION, utc_now_iso(), "initial object catalog schema"),
            )

    def upsert_research_object(self, obj: ResearchObject | dict[str, Any], conn: sqlite3.Connection | None = None) -> str:
        data = _dict(obj)
        if self.dry_run:
            return str(data["object_id"])
        owns_conn = conn is None
        if owns_conn:
            with self.transaction() as tx:
                return self.upsert_research_object(data, tx)
        assert conn is not None
        conn.execute(
            """
            INSERT INTO research_object (
                object_id, object_type, title, content_uri, content_hash, source_type, source_id,
                published_at, event_time, recorded_at, truth_status, lifecycle_status,
                schema_version, metadata_json, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(object_id) DO UPDATE SET
                title=excluded.title,
                content_uri=excluded.content_uri,
                content_hash=excluded.content_hash,
                source_id=excluded.source_id,
                published_at=excluded.published_at,
                event_time=excluded.event_time,
                truth_status=excluded.truth_status,
                lifecycle_status=excluded.lifecycle_status,
                schema_version=excluded.schema_version,
                metadata_json=excluded.metadata_json,
                raw_json=excluded.raw_json
            """,
            (
                data["object_id"],
                data["object_type"],
                data["title"],
                data.get("content_uri"),
                data.get("content_hash"),
                data["source_type"],
                data.get("source_id"),
                data.get("published_at"),
                data.get("event_time"),
                data["recorded_at"],
                data["truth_status"],
                data["lifecycle_status"],
                data["schema_version"],
                _json(data.get("metadata")),
                _json(data),
            ),
        )
        return str(data["object_id"])

    def upsert_relation(self, relation: ObjectRelation | dict[str, Any], conn: sqlite3.Connection | None = None) -> str:
        data = _dict(relation)
        if self.dry_run:
            return str(data["relation_id"])
        if conn is None:
            with self.transaction() as tx:
                return self.upsert_relation(data, tx)
        conn.execute(
            """
            INSERT INTO object_relation (
                relation_id, from_object_id, relation_type, to_object_id, polarity,
                confidence, effective_from, effective_to, evidence_object_id,
                agent_run_id, status, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(relation_id) DO UPDATE SET
                confidence=excluded.confidence,
                effective_from=excluded.effective_from,
                effective_to=excluded.effective_to,
                status=excluded.status,
                raw_json=excluded.raw_json
            """,
            (
                data["relation_id"],
                data["from_object_id"],
                data["relation_type"],
                data["to_object_id"],
                data.get("polarity"),
                data["confidence"],
                data.get("effective_from"),
                data.get("effective_to"),
                data.get("evidence_object_id"),
                data.get("agent_run_id"),
                data["status"],
                _json(data),
            ),
        )
        return str(data["relation_id"])

    def upsert_canonical_event(self, event: CanonicalEvent | dict[str, Any], conn: sqlite3.Connection | None = None) -> str:
        data = _dict(event)
        if self.dry_run:
            return str(data["event_id"])
        if conn is None:
            with self.transaction() as tx:
                return self.upsert_canonical_event(data, tx)
        conn.execute(
            """
            INSERT INTO canonical_event (
                event_id, canonical_summary, event_type, event_time, truth_status,
                market_attention, entities_json, status, source_type, source_id,
                metadata_json, raw_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                canonical_summary=excluded.canonical_summary,
                event_type=excluded.event_type,
                truth_status=excluded.truth_status,
                market_attention=MAX(canonical_event.market_attention, excluded.market_attention),
                entities_json=excluded.entities_json,
                status=excluded.status,
                metadata_json=excluded.metadata_json,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                data["event_id"],
                data["canonical_summary"],
                data["event_type"],
                data["event_time"],
                data["truth_status"],
                data["market_attention"],
                _json(data.get("entities", [])),
                data["status"],
                data["source_type"],
                data.get("source_id"),
                _json(data.get("metadata")),
                _json(data),
                utc_now_iso(),
            ),
        )
        return str(data["event_id"])

    def upsert_event_source_link(self, link: EventSourceLink | dict[str, Any], conn: sqlite3.Connection | None = None) -> str:
        data = _dict(link)
        if self.dry_run:
            return str(data["link_id"])
        if conn is None:
            with self.transaction() as tx:
                return self.upsert_event_source_link(data, tx)
        conn.execute(
            """
            INSERT INTO event_source_link (
                link_id, event_id, source_object_id, source_id, confidence,
                status, metadata_json, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(link_id) DO UPDATE SET
                confidence=excluded.confidence,
                status=excluded.status,
                metadata_json=excluded.metadata_json,
                raw_json=excluded.raw_json
            """,
            (
                data["link_id"],
                data["event_id"],
                data["source_object_id"],
                data.get("source_id"),
                data["confidence"],
                data["status"],
                _json(data.get("metadata")),
                _json(data),
            ),
        )
        return str(data["link_id"])

    def upsert_event_asset_link(self, link: EventAssetLink | dict[str, Any], conn: sqlite3.Connection | None = None) -> str:
        data = _dict(link)
        if self.dry_run:
            return str(data["link_id"])
        if conn is None:
            with self.transaction() as tx:
                return self.upsert_event_asset_link(data, tx)
        conn.execute(
            """
            INSERT INTO event_asset_link (
                link_id, event_id, asset_id, asset_label, polarity, confidence,
                status, metadata_json, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(link_id) DO UPDATE SET
                asset_label=excluded.asset_label,
                polarity=excluded.polarity,
                confidence=excluded.confidence,
                status=excluded.status,
                metadata_json=excluded.metadata_json,
                raw_json=excluded.raw_json
            """,
            (
                data["link_id"],
                data["event_id"],
                data["asset_id"],
                data["asset_label"],
                data.get("polarity"),
                data["confidence"],
                data["status"],
                _json(data.get("metadata")),
                _json(data),
            ),
        )
        return str(data["link_id"])

    def upsert_event_node_activation(
        self,
        activation: EventNodeActivation | dict[str, Any],
        conn: sqlite3.Connection | None = None,
    ) -> str:
        data = _dict(activation)
        if self.dry_run:
            return str(data["activation_id"])
        if conn is None:
            with self.transaction() as tx:
                return self.upsert_event_node_activation(data, tx)
        conn.execute(
            """
            INSERT INTO event_node_activation (
                activation_id, event_id, logic_node_id, logic_node_label, asset_id,
                confidence, status, metadata_json, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(activation_id) DO UPDATE SET
                logic_node_label=excluded.logic_node_label,
                asset_id=excluded.asset_id,
                confidence=excluded.confidence,
                status=excluded.status,
                metadata_json=excluded.metadata_json,
                raw_json=excluded.raw_json
            """,
            (
                data["activation_id"],
                data["event_id"],
                data["logic_node_id"],
                data.get("logic_node_label"),
                data.get("asset_id"),
                data["confidence"],
                data["status"],
                _json(data.get("metadata")),
                _json(data),
            ),
        )
        return str(data["activation_id"])

    def upsert_event_topic_membership(
        self,
        membership: EventTopicMembership | dict[str, Any],
        conn: sqlite3.Connection | None = None,
    ) -> str:
        data = _dict(membership)
        if self.dry_run:
            return str(data["membership_id"])
        if conn is None:
            with self.transaction() as tx:
                return self.upsert_event_topic_membership(data, tx)
        conn.execute(
            """
            INSERT INTO event_topic_membership (
                membership_id, event_id, topic_id, confidence, status, metadata_json, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(membership_id) DO UPDATE SET
                confidence=excluded.confidence,
                status=excluded.status,
                metadata_json=excluded.metadata_json,
                raw_json=excluded.raw_json
            """,
            (
                data["membership_id"],
                data["event_id"],
                data["topic_id"],
                data["confidence"],
                data["status"],
                _json(data.get("metadata")),
                _json(data),
            ),
        )
        return str(data["membership_id"])

    def upsert_topic(self, topic: Topic | dict[str, Any], conn: sqlite3.Connection | None = None) -> str:
        data = _dict(topic)
        if self.dry_run:
            return str(data["topic_id"])
        if conn is None:
            with self.transaction() as tx:
                return self.upsert_topic(data, tx)
        conn.execute(
            """
            INSERT INTO topic (
                topic_id, canonical_title, topic_type, description, first_seen_at,
                last_active_at, lifecycle_state, heat_score, credibility_score,
                source_diversity, contradiction_score, status, version,
                metadata_json, raw_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(topic_id) DO UPDATE SET
                canonical_title=excluded.canonical_title,
                description=COALESCE(NULLIF(excluded.description, ''), topic.description),
                first_seen_at=MIN(topic.first_seen_at, excluded.first_seen_at),
                last_active_at=MAX(topic.last_active_at, excluded.last_active_at),
                lifecycle_state=excluded.lifecycle_state,
                heat_score=MAX(topic.heat_score, excluded.heat_score),
                credibility_score=MAX(topic.credibility_score, excluded.credibility_score),
                source_diversity=MAX(topic.source_diversity, excluded.source_diversity),
                contradiction_score=MAX(topic.contradiction_score, excluded.contradiction_score),
                status=excluded.status,
                version=MAX(topic.version, excluded.version),
                metadata_json=excluded.metadata_json,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                data["topic_id"],
                data["canonical_title"],
                data["topic_type"],
                data.get("description", ""),
                data["first_seen_at"],
                data["last_active_at"],
                data["lifecycle_state"],
                data["heat_score"],
                data["credibility_score"],
                data["source_diversity"],
                data["contradiction_score"],
                data["status"],
                int(data.get("version") or 1),
                _json(data.get("metadata")),
                _json(data),
                utc_now_iso(),
            ),
        )
        return str(data["topic_id"])

    def upsert_topic_alias(self, alias: TopicAlias | dict[str, Any], conn: sqlite3.Connection | None = None) -> str:
        data = _dict(alias)
        if self.dry_run:
            return str(data["alias_id"])
        if conn is None:
            with self.transaction() as tx:
                return self.upsert_topic_alias(data, tx)
        conn.execute(
            """
            INSERT INTO topic_alias(alias_id, topic_id, alias, language, status, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(alias_id) DO UPDATE SET
                alias=excluded.alias,
                language=excluded.language,
                status=excluded.status,
                raw_json=excluded.raw_json
            """,
            (
                data["alias_id"],
                data["topic_id"],
                data["alias"],
                data.get("language"),
                data["status"],
                _json(data),
            ),
        )
        return str(data["alias_id"])

    def upsert_topic_membership(
        self,
        membership: TopicMembership | dict[str, Any],
        conn: sqlite3.Connection | None = None,
    ) -> str:
        data = _dict(membership)
        if self.dry_run:
            return str(data["membership_id"])
        if conn is None:
            with self.transaction() as tx:
                return self.upsert_topic_membership(data, tx)
        conn.execute(
            """
            INSERT INTO topic_membership (
                membership_id, object_id, topic_id, membership_role,
                relation_to_topic, membership_score, assigned_by, agent_run_id,
                effective_time, status, metadata_json, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(membership_id) DO UPDATE SET
                membership_role=excluded.membership_role,
                membership_score=MAX(topic_membership.membership_score, excluded.membership_score),
                assigned_by=excluded.assigned_by,
                agent_run_id=excluded.agent_run_id,
                effective_time=MIN(topic_membership.effective_time, excluded.effective_time),
                status=excluded.status,
                metadata_json=excluded.metadata_json,
                raw_json=excluded.raw_json
            """,
            (
                data["membership_id"],
                data["object_id"],
                data["topic_id"],
                data["membership_role"],
                data["relation_to_topic"],
                data["membership_score"],
                data["assigned_by"],
                data.get("agent_run_id"),
                data["effective_time"],
                data["status"],
                _json(data.get("metadata")),
                _json(data),
            ),
        )
        return str(data["membership_id"])

    def upsert_topic_snapshot(
        self,
        snapshot: TopicStateSnapshot | dict[str, Any],
        conn: sqlite3.Connection | None = None,
    ) -> str:
        data = _dict(snapshot)
        if self.dry_run:
            return str(data["snapshot_id"])
        if conn is None:
            with self.transaction() as tx:
                return self.upsert_topic_snapshot(data, tx)
        conn.execute(
            """
            INSERT INTO topic_state_snapshot (
                snapshot_id, topic_id, snapshot_time, lifecycle_state, heat_score,
                credibility_score, source_diversity, contradiction_score,
                summary, metadata_json, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id) DO UPDATE SET
                lifecycle_state=excluded.lifecycle_state,
                heat_score=excluded.heat_score,
                credibility_score=excluded.credibility_score,
                source_diversity=excluded.source_diversity,
                contradiction_score=excluded.contradiction_score,
                summary=excluded.summary,
                metadata_json=excluded.metadata_json,
                raw_json=excluded.raw_json
            """,
            (
                data["snapshot_id"],
                data["topic_id"],
                data["snapshot_time"],
                data["lifecycle_state"],
                data["heat_score"],
                data["credibility_score"],
                data["source_diversity"],
                data["contradiction_score"],
                data.get("summary", ""),
                _json(data.get("metadata")),
                _json(data),
            ),
        )
        return str(data["snapshot_id"])

    def upsert_agent_run(self, run: AgentRun | dict[str, Any], conn: sqlite3.Connection | None = None) -> str:
        data = _dict(run)
        if self.dry_run:
            return str(data["run_id"])
        if conn is None:
            with self.transaction() as tx:
                return self.upsert_agent_run(data, tx)
        conn.execute(
            """
            INSERT INTO agent_run (
                run_id, run_type, source_id, status, started_at, finished_at,
                manifest_uri, input_refs_json, output_refs_json, errors_json,
                metadata_json, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status=excluded.status,
                finished_at=excluded.finished_at,
                manifest_uri=excluded.manifest_uri,
                input_refs_json=excluded.input_refs_json,
                output_refs_json=excluded.output_refs_json,
                errors_json=excluded.errors_json,
                metadata_json=excluded.metadata_json,
                raw_json=excluded.raw_json
            """,
            (
                data["run_id"],
                data["run_type"],
                data.get("source_id"),
                data["status"],
                data["started_at"],
                data.get("finished_at"),
                data.get("manifest_uri"),
                _json(data.get("input_refs", [])),
                _json(data.get("output_refs", [])),
                _json(data.get("errors", [])),
                _json(data.get("metadata")),
                _json(data),
            ),
        )
        return str(data["run_id"])

    def upsert_report_dependency(
        self,
        dependency: ReportDependency | dict[str, Any],
        conn: sqlite3.Connection | None = None,
    ) -> str:
        data = _dict(dependency)
        if self.dry_run:
            return str(data["dependency_id"])
        if conn is None:
            with self.transaction() as tx:
                return self.upsert_report_dependency(data, tx)
        conn.execute(
            """
            INSERT INTO report_dependency (
                dependency_id, report_object_id, dependency_object_id,
                dependency_type, agent_run_id, evidence_weight,
                metadata_json, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dependency_id) DO UPDATE SET
                dependency_type=excluded.dependency_type,
                agent_run_id=excluded.agent_run_id,
                evidence_weight=excluded.evidence_weight,
                metadata_json=excluded.metadata_json,
                raw_json=excluded.raw_json
            """,
            (
                data["dependency_id"],
                data["report_object_id"],
                data["dependency_object_id"],
                data["dependency_type"],
                data.get("agent_run_id"),
                data["evidence_weight"],
                _json(data.get("metadata")),
                _json(data),
            ),
        )
        return str(data["dependency_id"])

    def upsert_many(
        self,
        *,
        objects: Iterable[ResearchObject | dict[str, Any]] = (),
        relations: Iterable[ObjectRelation | dict[str, Any]] = (),
        events: Iterable[CanonicalEvent | dict[str, Any]] = (),
        event_sources: Iterable[EventSourceLink | dict[str, Any]] = (),
        event_assets: Iterable[EventAssetLink | dict[str, Any]] = (),
        event_nodes: Iterable[EventNodeActivation | dict[str, Any]] = (),
        event_topics: Iterable[EventTopicMembership | dict[str, Any]] = (),
        topics: Iterable[Topic | dict[str, Any]] = (),
        topic_aliases: Iterable[TopicAlias | dict[str, Any]] = (),
        topic_memberships: Iterable[TopicMembership | dict[str, Any]] = (),
        topic_snapshots: Iterable[TopicStateSnapshot | dict[str, Any]] = (),
        agent_runs: Iterable[AgentRun | dict[str, Any]] = (),
        report_dependencies: Iterable[ReportDependency | dict[str, Any]] = (),
    ) -> dict[str, int | bool]:
        if self.dry_run:
            return {"dry_run": True}
        with self.transaction() as conn:
            assert conn is not None
            counts = {
                "research_object": 0,
                "object_relation": 0,
                "canonical_event": 0,
                "event_source_link": 0,
                "event_asset_link": 0,
                "event_node_activation": 0,
                "event_topic_membership": 0,
                "topic": 0,
                "topic_alias": 0,
                "topic_membership": 0,
                "topic_state_snapshot": 0,
                "agent_run": 0,
                "report_dependency": 0,
            }
            for obj in objects:
                self.upsert_research_object(obj, conn)
                counts["research_object"] += 1
            for relation in relations:
                self.upsert_relation(relation, conn)
                counts["object_relation"] += 1
            for event in events:
                self.upsert_canonical_event(event, conn)
                counts["canonical_event"] += 1
            for link in event_sources:
                self.upsert_event_source_link(link, conn)
                counts["event_source_link"] += 1
            for link in event_assets:
                self.upsert_event_asset_link(link, conn)
                counts["event_asset_link"] += 1
            for activation in event_nodes:
                self.upsert_event_node_activation(activation, conn)
                counts["event_node_activation"] += 1
            for membership in event_topics:
                self.upsert_event_topic_membership(membership, conn)
                counts["event_topic_membership"] += 1
            for topic in topics:
                self.upsert_topic(topic, conn)
                counts["topic"] += 1
            for alias in topic_aliases:
                self.upsert_topic_alias(alias, conn)
                counts["topic_alias"] += 1
            for membership in topic_memberships:
                self.upsert_topic_membership(membership, conn)
                counts["topic_membership"] += 1
            for snapshot in topic_snapshots:
                self.upsert_topic_snapshot(snapshot, conn)
                counts["topic_state_snapshot"] += 1
            for run in agent_runs:
                self.upsert_agent_run(run, conn)
                counts["agent_run"] += 1
            for dep in report_dependencies:
                self.upsert_report_dependency(dep, conn)
                counts["report_dependency"] += 1
            return counts

    def get_object(self, object_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM research_object WHERE object_id = ?", (object_id,)).fetchone()
        return _row_to_dict(row)

    def get_object_by_source(self, source_type: str, source_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM research_object
                WHERE source_type = ? AND source_id = ?
                ORDER BY recorded_at DESC
                LIMIT 1
                """,
                (source_type, source_id),
            ).fetchone()
        return _row_to_dict(row)

    def query_objects(
        self,
        *,
        object_type: str | None = None,
        source_id: str | None = None,
        asset_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if object_type:
            clauses.append("object_type = ?")
            params.append(object_type)
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        if asset_id:
            clauses.append(
                """
                object_id IN (
                    SELECT from_object_id FROM object_relation
                    WHERE relation_type = 'ABOUT_ASSET' AND to_object_id = ?
                )
                """
            )
            params.append(asset_id)
        if since:
            clauses.append("COALESCE(event_time, published_at, recorded_at) >= ?")
            params.append(since)
        if until:
            clauses.append("COALESCE(event_time, published_at, recorded_at) <= ?")
            params.append(until)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM research_object
                {where}
                ORDER BY COALESCE(event_time, published_at, recorded_at) DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM canonical_event WHERE event_id = ?", (event_id,)).fetchone()
        return _row_to_dict(row)

    def event_asset_links(self, event_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM event_asset_link WHERE event_id = ? ORDER BY asset_label",
                (event_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_topics(self, *, query: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if query:
            where = "WHERE canonical_title LIKE ? OR description LIKE ?"
            params.extend([f"%{query}%", f"%{query}%"])
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM topic
                {where}
                ORDER BY last_active_at DESC, heat_score DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_topic(self, topic_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM topic WHERE topic_id = ?", (topic_id,)).fetchone()
        return _row_to_dict(row)

    def topic_aliases(self, topic_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM topic_alias WHERE topic_id = ? ORDER BY alias",
                (topic_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def topic_timeline(self, topic_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM topic_state_snapshot
                WHERE topic_id = ?
                ORDER BY snapshot_time ASC
                LIMIT ?
                """,
                (topic_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def topic_memberships(self, topic_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT tm.*, ro.object_type, ro.title, ro.source_type, ro.source_id
                FROM topic_membership tm
                LEFT JOIN research_object ro ON ro.object_id = tm.object_id
                WHERE tm.topic_id = ?
                ORDER BY tm.effective_time ASC
                LIMIT ?
                """,
                (topic_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def table_counts(self) -> dict[str, int]:
        tables = (
            "research_object",
            "object_relation",
            "canonical_event",
            "event_source_link",
            "event_asset_link",
            "event_node_activation",
            "event_topic_membership",
            "topic",
            "topic_alias",
            "topic_membership",
            "topic_state_snapshot",
            "agent_run",
            "report_dependency",
            "schema_migration",
        )
        with self.connect() as conn:
            return {
                table: int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])
                for table in tables
            }

    def topic_source_stats(self, topic_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT tm.relation_to_topic, tm.membership_score, ro.source_type, ro.metadata_json
                FROM topic_membership tm
                JOIN research_object ro ON ro.object_id = tm.object_id
                WHERE tm.topic_id = ? AND tm.status != 'rejected'
                """,
                (topic_id,),
            ).fetchall()
        source_types: set[str] = set()
        support_score = 0.0
        contradiction_score = 0.0
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            source_type = str(row["source_type"] or "")
            if source_type == "agent" or (
                "independent_evidence_weight" in metadata
                and float(metadata.get("independent_evidence_weight") or 0) == 0
            ):
                continue
            source_types.add(source_type)
            relation = str(row["relation_to_topic"] or "")
            score = float(row["membership_score"] or 0.0)
            if relation == "contradicts":
                contradiction_score += score
            else:
                support_score += score
        return {
            "independent_source_types": sorted(source_types),
            "source_diversity": min(1.0, len(source_types) / 4.0),
            "credibility_score": min(1.0, support_score / 3.0),
            "contradiction_score": min(1.0, contradiction_score / 3.0),
        }

    def trace_lineage(self, object_id: str, *, max_depth: int = 8) -> dict[str, Any]:
        """Traverse relations and report dependencies without treating reports as evidence."""
        seen_objects = {object_id}
        frontier = {object_id}
        relations: list[dict[str, Any]] = []
        report_dependencies: list[dict[str, Any]] = []
        with self.connect() as conn:
            for _ in range(max_depth):
                if not frontier:
                    break
                placeholders = ",".join("?" for _ in frontier)
                rows = conn.execute(
                    f"""
                    SELECT * FROM object_relation
                    WHERE from_object_id IN ({placeholders}) OR to_object_id IN ({placeholders})
                    """,
                    [*frontier, *frontier],
                ).fetchall()
                dep_rows = conn.execute(
                    f"""
                    SELECT * FROM report_dependency
                    WHERE report_object_id IN ({placeholders}) OR dependency_object_id IN ({placeholders})
                    """,
                    [*frontier, *frontier],
                ).fetchall()
                next_frontier: set[str] = set()
                for row in rows:
                    item = dict(row)
                    relations.append(item)
                    for key in ("from_object_id", "to_object_id", "evidence_object_id"):
                        value = item.get(key)
                        if value and value not in seen_objects:
                            seen_objects.add(value)
                            next_frontier.add(value)
                for row in dep_rows:
                    item = dict(row)
                    report_dependencies.append(item)
                    for key in ("report_object_id", "dependency_object_id"):
                        value = item.get(key)
                        if value and value not in seen_objects:
                            seen_objects.add(value)
                            next_frontier.add(value)
                frontier = next_frontier
            object_rows = []
            if seen_objects:
                placeholders = ",".join("?" for _ in seen_objects)
                object_rows = conn.execute(
                    f"SELECT * FROM research_object WHERE object_id IN ({placeholders})",
                    list(seen_objects),
                ).fetchall()
        return {
            "root_object_id": object_id,
            "object_ids": sorted(seen_objects),
            "objects": [dict(row) for row in object_rows],
            "relations": relations,
            "report_dependencies": report_dependencies,
        }
