from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from quanta_agents.asset_event_state.models import (
    CLUSTER_SCHEMA_VERSION,
    DRIVER_SCHEMA_VERSION,
    EVENT_SCHEMA_VERSION,
    NARRATIVE_SCHEMA_VERSION,
    THEME_NARRATIVE_SCHEMA_VERSION,
    hash_id,
    normalize_signal_vector,
    utc_now_iso,
)
from quanta_agents.core.io import relative_to_root


class AssetEventStore:
    def __init__(self, quanta_root: str | Path, db_path: str | Path | None = None):
        self.quanta_root = Path(quanta_root).expanduser()
        self.db_path = (
            Path(db_path).expanduser()
            if db_path
            else self.quanta_root / "indexes" / "asset_event_state" / "events.sqlite3"
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS event_table (
                    event_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    key_facts_json TEXT NOT NULL,
                    signal_vector_json TEXT NOT NULL,
                    impact_nodes_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_id TEXT NOT NULL,
                    source_ref_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    cluster_id TEXT NOT NULL DEFAULT '',
                    driver_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    raw_event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_event_table_asset_time
                    ON event_table(asset, timestamp);
                CREATE INDEX IF NOT EXISTS idx_event_table_source
                    ON event_table(source_id);
                CREATE INDEX IF NOT EXISTS idx_event_table_cluster
                    ON event_table(cluster_id);
                CREATE INDEX IF NOT EXISTS idx_event_table_driver
                    ON event_table(driver_id);

                CREATE TABLE IF NOT EXISTS event_log (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    log_id TEXT NOT NULL UNIQUE,
                    schema_version TEXT NOT NULL,
                    stream_type TEXT NOT NULL,
                    stream_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    update_time TEXT NOT NULL,
                    effective_time TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_event_log_stream
                    ON event_log(stream_type, stream_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_event_log_action
                    ON event_log(action, sequence);

                CREATE TABLE IF NOT EXISTS graph_event_log (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    log_id TEXT NOT NULL UNIQUE,
                    graph_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    node_id TEXT NOT NULL DEFAULT '',
                    edge_from TEXT NOT NULL DEFAULT '',
                    edge_to TEXT NOT NULL DEFAULT '',
                    update_time TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_graph_event_log_type
                    ON graph_event_log(graph_type, sequence);
                CREATE INDEX IF NOT EXISTS idx_graph_event_log_event
                    ON graph_event_log(event_id);

                CREATE TABLE IF NOT EXISTS event_node_mapping (
                    id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    node_name TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    matched_terms_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(event_id) REFERENCES event_table(event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_event_node_mapping_event
                    ON event_node_mapping(event_id);
                CREATE INDEX IF NOT EXISTS idx_event_node_mapping_node
                    ON event_node_mapping(node_id);

                CREATE TABLE IF NOT EXISTS event_cluster_table (
                    cluster_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    theme TEXT NOT NULL,
                    event_ids_json TEXT NOT NULL,
                    net_signal_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    time_window TEXT NOT NULL,
                    first_event_at TEXT NOT NULL,
                    last_event_at TEXT NOT NULL,
                    raw_cluster_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_event_cluster_asset_status
                    ON event_cluster_table(asset, status, updated_at);

                CREATE TABLE IF NOT EXISTS cluster_events (
                    cluster_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    score REAL NOT NULL,
                    scoring_json TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    PRIMARY KEY(cluster_id, event_id)
                );

                CREATE TABLE IF NOT EXISTS cluster_state_history (
                    id TEXT PRIMARY KEY,
                    cluster_id TEXT NOT NULL,
                    from_status TEXT NOT NULL,
                    to_status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    changed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS driver_state_table (
                    driver_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    driver_key TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    direction REAL NOT NULL,
                    timeline_json TEXT NOT NULL,
                    supporting_events_json TEXT NOT NULL,
                    contradicting_events_json TEXT NOT NULL,
                    traceability_json TEXT NOT NULL,
                    last_transition_json TEXT NOT NULL,
                    first_event_at TEXT NOT NULL,
                    last_event_at TEXT NOT NULL,
                    raw_driver_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_driver_state_asset_strength
                    ON driver_state_table(asset, updated_at);

                CREATE TABLE IF NOT EXISTS driver_events (
                    driver_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    impact REAL NOT NULL,
                    timestamp TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    PRIMARY KEY(driver_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_driver_events_event
                    ON driver_events(event_id);

                CREATE TABLE IF NOT EXISTS causal_path_table (
                    path_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    target_asset TEXT NOT NULL,
                    final_node TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    path_signal REAL NOT NULL,
                    path_confidence REAL NOT NULL,
                    effective_time TEXT NOT NULL,
                    raw_path_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_causal_path_event
                    ON causal_path_table(event_id);

                CREATE TABLE IF NOT EXISTS signal_table (
                    signal_id TEXT PRIMARY KEY,
                    asset TEXT NOT NULL,
                    driver_id TEXT NOT NULL,
                    framework_node TEXT NOT NULL,
                    direction REAL NOT NULL,
                    magnitude REAL NOT NULL,
                    confidence REAL NOT NULL,
                    source_type TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    event_ids_json TEXT NOT NULL,
                    causal_path_ids_json TEXT NOT NULL,
                    effective_time TEXT NOT NULL,
                    status TEXT NOT NULL,
                    raw_signal_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_signal_driver
                    ON signal_table(driver_id, effective_time);
                CREATE INDEX IF NOT EXISTS idx_signal_asset
                    ON signal_table(asset, effective_time);

                CREATE TABLE IF NOT EXISTS driver_state_history (
                    id TEXT PRIMARY KEY,
                    driver_id TEXT NOT NULL,
                    from_state_json TEXT NOT NULL,
                    to_state_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    changed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS narrative_outputs (
                    narrative_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    cluster_id TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    source_cluster_json TEXT NOT NULL,
                    generated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS theme_narrative_outputs (
                    narrative_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    driver_ids_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    source_drivers_json TEXT NOT NULL,
                    generated_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column(conn, "event_table", "driver_id", "TEXT NOT NULL DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_table_driver ON event_table(driver_id)")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def insert_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = utc_now_iso()
        with self.connect() as conn:
            for event in events:
                event = dict(event)
                event.setdefault("schema_version", EVENT_SCHEMA_VERSION)
                event.setdefault("created_at", now)
                event.setdefault("event_time", event.get("timestamp", now))
                event.setdefault("update_time", now)
                event.setdefault("effective_time", event.get("timestamp", now))
                self._append_event_log_conn(
                    conn,
                    stream_type="event",
                    stream_id=event["event_id"],
                    action="canonical_event_recorded",
                    payload=event,
                    event_time=event.get("event_time", event.get("timestamp", now)),
                    effective_time=event.get("effective_time", event.get("timestamp", now)),
                    metadata={"source_id": event.get("source_id", ""), "schema_version": EVENT_SCHEMA_VERSION},
                )
                conn.execute(
                    """
                    INSERT INTO event_table (
                        event_id, schema_version, asset, event_type, summary,
                        key_facts_json, signal_vector_json, impact_nodes_json,
                        confidence, source_id, source_ref_json, timestamp,
                        cluster_id, driver_id, status, raw_event_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET
                        asset=excluded.asset,
                        event_type=excluded.event_type,
                        summary=excluded.summary,
                        key_facts_json=excluded.key_facts_json,
                        signal_vector_json=excluded.signal_vector_json,
                        impact_nodes_json=excluded.impact_nodes_json,
                        confidence=excluded.confidence,
                        source_id=excluded.source_id,
                        source_ref_json=excluded.source_ref_json,
                        timestamp=excluded.timestamp,
                        raw_event_json=excluded.raw_event_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        event["event_id"],
                        event.get("schema_version", EVENT_SCHEMA_VERSION),
                        event["asset"],
                        event["event_type"],
                        event.get("summary", ""),
                        json.dumps(event.get("key_facts", []), ensure_ascii=False),
                        json.dumps(normalize_signal_vector(event.get("signal_vector")), ensure_ascii=False),
                        json.dumps(event.get("impact_nodes", []), ensure_ascii=False),
                        float(event.get("confidence", 0.0)),
                        event.get("source_id", ""),
                        json.dumps(event.get("source_ref", {}), ensure_ascii=False),
                        event["timestamp"],
                        event.get("cluster_id", ""),
                        event.get("driver_id", ""),
                        event.get("status", "active"),
                        json.dumps(event, ensure_ascii=False),
                        event.get("created_at", now),
                        now,
                    ),
                )
                conn.execute("DELETE FROM event_node_mapping WHERE event_id = ?", (event["event_id"],))
                for mapping in event.get("node_mappings", []):
                    if not isinstance(mapping, dict):
                        continue
                    mapping_id = hash_id("ENMAP", event["event_id"], mapping.get("node"), length=16)
                    conn.execute(
                        """
                        INSERT INTO event_node_mapping (
                            id, event_id, asset, node_id, node_name, confidence,
                            matched_terms_json, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            mapping_id,
                            event["event_id"],
                            event["asset"],
                            mapping.get("node", ""),
                            mapping.get("node_name", ""),
                            float(mapping.get("confidence", 0.0)),
                            json.dumps(mapping.get("matched_terms", []), ensure_ascii=False),
                            now,
                        ),
                    )
        return [self.get_event(event["event_id"]) or event for event in events]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM event_table WHERE event_id = ?", (event_id,)).fetchone()
            if not row:
                return None
            mappings = conn.execute(
                "SELECT * FROM event_node_mapping WHERE event_id = ? ORDER BY confidence DESC",
                (event_id,),
            ).fetchall()
        event = self._event_row(row)
        event["node_mappings"] = [self._node_mapping_row(item) for item in mappings]
        return event

    def list_events(
        self,
        *,
        asset: str = "",
        since: str = "",
        until: str = "",
        unclustered: bool = False,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        clauses = ["status = 'active'"]
        params: list[Any] = []
        if asset:
            clauses.append("asset = ?")
            params.append(asset)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until:
            clauses.append("timestamp <= ?")
            params.append(until)
        if unclustered:
            clauses.append("cluster_id = ''")
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM event_table
                WHERE {' AND '.join(clauses)}
                ORDER BY timestamp ASC, event_id ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._event_row(row) for row in rows]

    def upsert_clusters(self, clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = utc_now_iso()
        with self.connect() as conn:
            for cluster in clusters:
                cluster = dict(cluster)
                previous = conn.execute(
                    "SELECT status FROM event_cluster_table WHERE cluster_id = ?",
                    (cluster["cluster_id"],),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO event_cluster_table (
                        cluster_id, schema_version, asset, theme, event_ids_json,
                        net_signal_json, status, confidence, time_window,
                        first_event_at, last_event_at, raw_cluster_json,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cluster_id) DO UPDATE SET
                        asset=excluded.asset,
                        theme=excluded.theme,
                        event_ids_json=excluded.event_ids_json,
                        net_signal_json=excluded.net_signal_json,
                        status=excluded.status,
                        confidence=excluded.confidence,
                        time_window=excluded.time_window,
                        first_event_at=excluded.first_event_at,
                        last_event_at=excluded.last_event_at,
                        raw_cluster_json=excluded.raw_cluster_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        cluster["cluster_id"],
                        cluster.get("schema_version", CLUSTER_SCHEMA_VERSION),
                        cluster["asset"],
                        cluster.get("theme", ""),
                        json.dumps(cluster.get("events", []), ensure_ascii=False),
                        json.dumps(normalize_signal_vector(cluster.get("net_signal")), ensure_ascii=False),
                        cluster.get("status", "emerging"),
                        float(cluster.get("confidence", 0.0)),
                        cluster.get("time_window", "72h"),
                        cluster.get("first_event_at", ""),
                        cluster.get("last_event_at", ""),
                        json.dumps(cluster, ensure_ascii=False),
                        cluster.get("created_at", now),
                        now,
                    ),
                )
                for event_id in cluster.get("events", []):
                    assignment = cluster.get("assignment_scores", {}).get(event_id, {})
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO cluster_events (
                            cluster_id, event_id, score, scoring_json, assigned_at
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            cluster["cluster_id"],
                            event_id,
                            float(assignment.get("score", 1.0)),
                            json.dumps(assignment, ensure_ascii=False),
                            now,
                        ),
                    )
                    conn.execute(
                        "UPDATE event_table SET cluster_id = ?, updated_at = ? WHERE event_id = ?",
                        (cluster["cluster_id"], now, event_id),
                    )
                if previous and previous["status"] != cluster.get("status", "emerging"):
                    history_id = hash_id("CLHIST", cluster["cluster_id"], previous["status"], cluster.get("status"), now)
                    conn.execute(
                        """
                        INSERT INTO cluster_state_history (
                            id, cluster_id, from_status, to_status, reason, changed_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            history_id,
                            cluster["cluster_id"],
                            previous["status"],
                            cluster.get("status", "emerging"),
                            cluster.get("state_reason", ""),
                            now,
                        ),
                    )
        return [self.get_cluster(cluster["cluster_id"]) or cluster for cluster in clusters]

    def get_cluster(self, cluster_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM event_cluster_table WHERE cluster_id = ?",
                (cluster_id,),
            ).fetchone()
            if not row:
                return None
            link_rows = conn.execute(
                "SELECT * FROM cluster_events WHERE cluster_id = ? ORDER BY assigned_at ASC",
                (cluster_id,),
            ).fetchall()
        cluster = self._cluster_row(row)
        cluster["cluster_events"] = [dict(item) for item in link_rows]
        return cluster

    def list_clusters(
        self,
        *,
        asset: str = "",
        status: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if asset:
            clauses.append("asset = ?")
            params.append(asset)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM event_cluster_table
                {where_clause}
                ORDER BY updated_at DESC, cluster_id ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._cluster_row(row) for row in rows]

    def upsert_drivers(self, drivers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = utc_now_iso()
        with self.connect() as conn:
            for driver in drivers:
                driver = dict(driver)
                timeline = [item for item in driver.get("timeline", []) if isinstance(item, dict)]
                first_event_at = str(timeline[0].get("timestamp") or "") if timeline else ""
                last_event_at = str(timeline[-1].get("timestamp") or driver.get("last_event_at") or "")
                driver.setdefault("as_of", last_event_at or now)
                driver.setdefault("support_score", 0.0)
                driver.setdefault("contradiction_score", 0.0)
                driver.setdefault("change_explanation", "")
                previous = conn.execute(
                    "SELECT state_json FROM driver_state_table WHERE driver_id = ?",
                    (driver["driver_id"],),
                ).fetchone()
                self._append_event_log_conn(
                    conn,
                    stream_type="driver",
                    stream_id=driver["driver_id"],
                    action="driver_state_upserted",
                    payload=driver,
                    event_time=last_event_at or now,
                    effective_time=driver.get("as_of", last_event_at or now),
                    metadata={"asset": driver.get("asset", ""), "driver_key": driver.get("driver_key", "")},
                )
                conn.execute(
                    """
                    INSERT INTO driver_state_table (
                        driver_id, schema_version, asset, driver_key, state_json,
                        direction, timeline_json, supporting_events_json,
                        contradicting_events_json, traceability_json,
                        last_transition_json, first_event_at, last_event_at,
                        raw_driver_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(driver_id) DO UPDATE SET
                        schema_version=excluded.schema_version,
                        asset=excluded.asset,
                        driver_key=excluded.driver_key,
                        state_json=excluded.state_json,
                        direction=excluded.direction,
                        timeline_json=excluded.timeline_json,
                        supporting_events_json=excluded.supporting_events_json,
                        contradicting_events_json=excluded.contradicting_events_json,
                        traceability_json=excluded.traceability_json,
                        last_transition_json=excluded.last_transition_json,
                        first_event_at=excluded.first_event_at,
                        last_event_at=excluded.last_event_at,
                        raw_driver_json=excluded.raw_driver_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        driver["driver_id"],
                        driver.get("schema_version", DRIVER_SCHEMA_VERSION),
                        driver.get("asset", ""),
                        driver.get("driver_key", ""),
                        json.dumps(driver.get("state", {}), ensure_ascii=False),
                        float(driver.get("direction", 0.0)),
                        json.dumps(timeline, ensure_ascii=False),
                        json.dumps(driver.get("supporting_events", []), ensure_ascii=False),
                        json.dumps(driver.get("contradicting_events", []), ensure_ascii=False),
                        json.dumps(driver.get("traceability", {}), ensure_ascii=False),
                        json.dumps(driver.get("last_transition", {}), ensure_ascii=False),
                        first_event_at,
                        last_event_at,
                        json.dumps(driver, ensure_ascii=False),
                        driver.get("created_at", now),
                        now,
                    ),
                )
                for item in timeline:
                    event_id = str(item.get("event_id") or "")
                    if not event_id:
                        continue
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO driver_events (
                            driver_id, event_id, impact, timestamp, source_id, assigned_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            driver["driver_id"],
                            event_id,
                            float(item.get("impact") or 0.0),
                            str(item.get("timestamp") or ""),
                            str(item.get("source_id") or ""),
                            now,
                        ),
                    )
                    conn.execute(
                        "UPDATE event_table SET driver_id = ?, updated_at = ? WHERE event_id = ?",
                        (driver["driver_id"], now, event_id),
                    )
                current_state_json = json.dumps(driver.get("state", {}), ensure_ascii=False)
                if previous and previous["state_json"] != current_state_json:
                    history_id = hash_id("DRVHIST", driver["driver_id"], previous["state_json"], current_state_json, now)
                    conn.execute(
                        """
                        INSERT INTO driver_state_history (
                            id, driver_id, from_state_json, to_state_json, reason, changed_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            history_id,
                            driver["driver_id"],
                            previous["state_json"],
                            current_state_json,
                            json.dumps(driver.get("last_transition", {}), ensure_ascii=False),
                            now,
                        ),
                    )
        return [self.get_driver(driver["driver_id"]) or driver for driver in drivers]

    def get_driver(self, driver_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM driver_state_table WHERE driver_id = ?",
                (driver_id,),
            ).fetchone()
        return self._driver_row(row) if row else None

    def list_drivers(
        self,
        *,
        asset: str = "",
        trend: str = "",
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if asset:
            clauses.append("asset = ?")
            params.append(asset)
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM driver_state_table
                {where_clause}
                ORDER BY updated_at DESC, driver_id ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        drivers = [self._driver_row(row) for row in rows]
        if trend:
            drivers = [driver for driver in drivers if (driver.get("state") or {}).get("trend") == trend]
        return drivers

    def insert_narrative(self, narrative: dict[str, Any], cluster: dict[str, Any]) -> dict[str, Any]:
        generated_at = narrative.get("generated_at") or utc_now_iso()
        narrative_id = narrative.get("narrative_id") or hash_id(
            "NAR", narrative.get("cluster_id"), generated_at, length=16
        )
        narrative["narrative_id"] = narrative_id
        narrative.setdefault("schema_version", NARRATIVE_SCHEMA_VERSION)
        narrative.setdefault("generated_at", generated_at)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO narrative_outputs (
                    narrative_id, schema_version, cluster_id, asset,
                    payload_json, source_cluster_json, generated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    narrative_id,
                    narrative.get("schema_version", NARRATIVE_SCHEMA_VERSION),
                    narrative["cluster_id"],
                    narrative.get("asset", ""),
                    json.dumps(narrative, ensure_ascii=False),
                    json.dumps(cluster, ensure_ascii=False),
                    generated_at,
                ),
            )
        return narrative

    def insert_theme_narrative(
        self,
        narrative: dict[str, Any],
        source_drivers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        generated_at = narrative.get("generated_at") or utc_now_iso()
        narrative_id = narrative.get("narrative_id") or hash_id(
            "DNAR", narrative.get("asset"), generated_at, length=16
        )
        narrative["narrative_id"] = narrative_id
        narrative.setdefault("schema_version", THEME_NARRATIVE_SCHEMA_VERSION)
        narrative.setdefault("generated_at", generated_at)
        driver_ids = [str(driver.get("driver_id")) for driver in source_drivers if driver.get("driver_id")]
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO theme_narrative_outputs (
                    narrative_id, schema_version, asset, driver_ids_json,
                    payload_json, source_drivers_json, generated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    narrative_id,
                    narrative.get("schema_version", THEME_NARRATIVE_SCHEMA_VERSION),
                    narrative.get("asset", ""),
                    json.dumps(driver_ids, ensure_ascii=False),
                    json.dumps(narrative, ensure_ascii=False),
                    json.dumps(source_drivers, ensure_ascii=False),
                    generated_at,
                ),
            )
        return narrative

    def record_graph_events(self, graph_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = utc_now_iso()
        recorded: list[dict[str, Any]] = []
        with self.connect() as conn:
            for item in graph_events:
                if not isinstance(item, dict):
                    continue
                payload = dict(item)
                payload.setdefault("update_time", now)
                log_id = payload.get("log_id") or hash_id(
                    "GLOG",
                    payload.get("graph_type"),
                    payload.get("action"),
                    payload.get("event_id"),
                    payload.get("node_id"),
                    payload.get("edge_from"),
                    payload.get("edge_to"),
                    payload.get("update_time"),
                    length=18,
                )
                payload["log_id"] = log_id
                conn.execute(
                    """
                    INSERT OR IGNORE INTO graph_event_log (
                        log_id, graph_type, action, event_id, node_id, edge_from,
                        edge_to, update_time, payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        log_id,
                        payload.get("graph_type", ""),
                        payload.get("action", ""),
                        payload.get("event_id", ""),
                        payload.get("node_id", ""),
                        payload.get("edge_from", ""),
                        payload.get("edge_to", ""),
                        payload.get("update_time", now),
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
                recorded.append(payload)
        return recorded

    def insert_causal_paths(self, paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = utc_now_iso()
        with self.connect() as conn:
            for path in paths:
                if not isinstance(path, dict) or not path.get("path_id"):
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO causal_path_table (
                        path_id, event_id, target_asset, final_node, steps_json,
                        path_signal, path_confidence, effective_time,
                        raw_path_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        path["path_id"],
                        path.get("event_id", ""),
                        path.get("target_asset", ""),
                        path.get("final_node", ""),
                        json.dumps(path.get("steps", []), ensure_ascii=False),
                        float(path.get("path_signal", 0.0)),
                        float(path.get("path_confidence", 0.0)),
                        path.get("effective_time", ""),
                        json.dumps(path, ensure_ascii=False),
                        path.get("created_at", now),
                    ),
                )
                self._append_event_log_conn(
                    conn,
                    stream_type="causal_path",
                    stream_id=path["path_id"],
                    action="causal_path_recorded",
                    payload=path,
                    event_time=path.get("effective_time", now),
                    effective_time=path.get("effective_time", now),
                    metadata={"event_id": path.get("event_id", ""), "final_node": path.get("final_node", "")},
                )
        return paths

    def insert_signals(self, signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = utc_now_iso()
        with self.connect() as conn:
            for signal in signals:
                if not isinstance(signal, dict) or not signal.get("signal_id"):
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO signal_table (
                        signal_id, asset, driver_id, framework_node, direction,
                        magnitude, confidence, source_type, relation,
                        event_ids_json, causal_path_ids_json, effective_time,
                        status, raw_signal_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal["signal_id"],
                        signal.get("asset", ""),
                        signal.get("driver_id", ""),
                        signal.get("framework_node", ""),
                        float(signal.get("direction", 0.0)),
                        float(signal.get("magnitude", 0.0)),
                        float(signal.get("confidence", 0.0)),
                        signal.get("source_type", ""),
                        signal.get("relation", ""),
                        json.dumps(signal.get("event_ids", []), ensure_ascii=False),
                        json.dumps(signal.get("causal_path_ids", []), ensure_ascii=False),
                        signal.get("effective_time", ""),
                        signal.get("status", "accepted"),
                        json.dumps(signal, ensure_ascii=False),
                        signal.get("created_at", now),
                    ),
                )
                self._append_event_log_conn(
                    conn,
                    stream_type="signal",
                    stream_id=signal["signal_id"],
                    action="signal_recorded",
                    payload=signal,
                    event_time=signal.get("effective_time", now),
                    effective_time=signal.get("effective_time", now),
                    metadata={"driver_id": signal.get("driver_id", ""), "asset": signal.get("asset", "")},
                )
        return signals

    def list_event_log(
        self,
        *,
        stream_type: str = "",
        stream_id: str = "",
        since_sequence: int = 0,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        clauses = ["sequence > ?"]
        params: list[Any] = [since_sequence]
        if stream_type:
            clauses.append("stream_type = ?")
            params.append(stream_type)
        if stream_id:
            clauses.append("stream_id = ?")
            params.append(stream_id)
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM event_log
                WHERE {' AND '.join(clauses)}
                ORDER BY sequence ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._event_log_row(row) for row in rows]

    def replay_events(self, *, asset: str = "", until_sequence: int | None = None) -> list[dict[str, Any]]:
        clauses = ["stream_type = 'event'", "action = 'canonical_event_recorded'"]
        params: list[Any] = []
        if until_sequence is not None:
            clauses.append("sequence <= ?")
            params.append(until_sequence)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM event_log
                WHERE {' AND '.join(clauses)}
                ORDER BY sequence ASC
                """,
                tuple(params),
            ).fetchall()
        events: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = json.loads(row["payload_json"])
            if asset and payload.get("asset") != asset:
                continue
            events[payload["event_id"]] = payload
        return sorted(events.values(), key=lambda item: (str(item.get("timestamp") or ""), item["event_id"]))

    def database_ref(self) -> str:
        return relative_to_root(self.db_path, self.quanta_root)

    def _append_event_log_conn(
        self,
        conn: sqlite3.Connection,
        *,
        stream_type: str,
        stream_id: str,
        action: str,
        payload: dict[str, Any],
        event_time: str,
        effective_time: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now_iso()
        log_id = hash_id("ELOG", stream_type, stream_id, action, now, json.dumps(payload, sort_keys=True, default=str), length=18)
        conn.execute(
            """
            INSERT INTO event_log (
                log_id, schema_version, stream_type, stream_id, action,
                event_time, update_time, effective_time, payload_json, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_id,
                "quanta_event_log.v1",
                stream_type,
                stream_id,
                action,
                event_time or now,
                now,
                effective_time or event_time or now,
                json.dumps(payload, ensure_ascii=False, default=str),
                json.dumps(metadata or {}, ensure_ascii=False, default=str),
            ),
        )

    def _event_row(self, row: sqlite3.Row) -> dict[str, Any]:
        raw = json.loads(row["raw_event_json"])
        raw.update(
            {
                "event_id": row["event_id"],
                "schema_version": row["schema_version"],
                "asset": row["asset"],
                "event_type": row["event_type"],
                "summary": row["summary"],
                "key_facts": json.loads(row["key_facts_json"]),
                "signal_vector": json.loads(row["signal_vector_json"]),
                "impact_nodes": json.loads(row["impact_nodes_json"]),
                "confidence": row["confidence"],
                "source_id": row["source_id"],
                "source_ref": json.loads(row["source_ref_json"]),
                "timestamp": row["timestamp"],
                "cluster_id": row["cluster_id"],
                "driver_id": row["driver_id"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        return raw

    def _event_log_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "sequence": row["sequence"],
            "log_id": row["log_id"],
            "schema_version": row["schema_version"],
            "stream_type": row["stream_type"],
            "stream_id": row["stream_id"],
            "action": row["action"],
            "event_time": row["event_time"],
            "update_time": row["update_time"],
            "effective_time": row["effective_time"],
            "payload": json.loads(row["payload_json"]),
            "metadata": json.loads(row["metadata_json"]),
        }

    def _node_mapping_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "event_id": row["event_id"],
            "asset": row["asset"],
            "node": row["node_id"],
            "node_name": row["node_name"],
            "confidence": row["confidence"],
            "matched_terms": json.loads(row["matched_terms_json"]),
            "created_at": row["created_at"],
        }

    def _cluster_row(self, row: sqlite3.Row) -> dict[str, Any]:
        raw = json.loads(row["raw_cluster_json"])
        raw.update(
            {
                "cluster_id": row["cluster_id"],
                "schema_version": row["schema_version"],
                "asset": row["asset"],
                "theme": row["theme"],
                "events": json.loads(row["event_ids_json"]),
                "net_signal": json.loads(row["net_signal_json"]),
                "status": row["status"],
                "confidence": row["confidence"],
                "time_window": row["time_window"],
                "first_event_at": row["first_event_at"],
                "last_event_at": row["last_event_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        return raw

    def _driver_row(self, row: sqlite3.Row) -> dict[str, Any]:
        raw = json.loads(row["raw_driver_json"])
        raw.update(
            {
                "driver_id": row["driver_id"],
                "schema_version": row["schema_version"],
                "asset": row["asset"],
                "driver_key": row["driver_key"],
                "state": json.loads(row["state_json"]),
                "direction": row["direction"],
                "timeline": json.loads(row["timeline_json"]),
                "supporting_events": json.loads(row["supporting_events_json"]),
                "contradicting_events": json.loads(row["contradicting_events_json"]),
                "traceability": json.loads(row["traceability_json"]),
                "last_transition": json.loads(row["last_transition_json"]),
                "first_event_at": row["first_event_at"],
                "last_event_at": row["last_event_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        return raw
