from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import RLock


@dataclass(frozen=True)
class EventDelivery:
    event_id: str
    plugin_id: str
    event_type: str
    payload: dict
    status: str
    created_at: float


class EventJournal:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def _create_schema(self):
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS event_subscriptions (
                plugin_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                PRIMARY KEY (plugin_id, event_type)
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE (event_type, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS event_deliveries (
                event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                plugin_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'delivering', 'acked')),
                updated_at REAL NOT NULL,
                PRIMARY KEY (event_id, plugin_id)
            );
            CREATE INDEX IF NOT EXISTS event_deliveries_pending
            ON event_deliveries(plugin_id, status, updated_at);
        """)

    def close(self):
        with self._lock:
            self._connection.close()

    def set_subscriptions(self, plugin_id: str, event_types):
        plugin_id = str(plugin_id)
        normalized = sorted({str(value) for value in event_types if str(value)})
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM event_subscriptions WHERE plugin_id = ?",
                (plugin_id,),
            )
            self._connection.executemany(
                "INSERT INTO event_subscriptions(plugin_id, event_type) VALUES (?, ?)",
                [(plugin_id, event_type) for event_type in normalized],
            )

    def publish(self, event_type: str, payload: dict, idempotency_key: str) -> str:
        event_type = str(event_type)
        idempotency_key = str(idempotency_key or uuid.uuid4().hex)
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    "SELECT id FROM events WHERE event_type = ? AND idempotency_key = ?",
                    (event_type, idempotency_key),
                ).fetchone()
                if existing:
                    self._connection.execute("COMMIT")
                    return str(existing["id"])
                event_id = uuid.uuid4().hex
                now = time.time()
                self._connection.execute(
                    "INSERT INTO events(id, event_type, payload_json, idempotency_key, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (event_id, event_type, payload_json, idempotency_key, now),
                )
                subscribers = self._connection.execute(
                    "SELECT plugin_id FROM event_subscriptions WHERE event_type = ?",
                    (event_type,),
                ).fetchall()
                self._connection.executemany(
                    "INSERT INTO event_deliveries(event_id, plugin_id, status, updated_at) "
                    "VALUES (?, ?, 'pending', ?)",
                    [(event_id, row["plugin_id"], now) for row in subscribers],
                )
                self._connection.execute("COMMIT")
                return event_id
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def pending(self, plugin_id: str, limit: int = 100) -> list[EventDelivery]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT e.id, d.plugin_id, e.event_type, e.payload_json, d.status, e.created_at "
                "FROM event_deliveries d JOIN events e ON e.id = d.event_id "
                "WHERE d.plugin_id = ? AND d.status IN ('pending', 'delivering') "
                "ORDER BY e.created_at, e.id LIMIT ?",
                (str(plugin_id), max(1, int(limit))),
            ).fetchall()
        return [EventDelivery(
            event_id=str(row["id"]),
            plugin_id=str(row["plugin_id"]),
            event_type=str(row["event_type"]),
            payload=json.loads(row["payload_json"]),
            status=str(row["status"]),
            created_at=float(row["created_at"]),
        ) for row in rows]

    def ack(self, event_id: str, plugin_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE event_deliveries SET status = 'acked', updated_at = ? "
                "WHERE event_id = ? AND plugin_id = ? "
                "AND status IN ('pending', 'delivering')",
                (time.time(), str(event_id), str(plugin_id)),
            )
            return cursor.rowcount == 1
