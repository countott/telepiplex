from __future__ import annotations

import json
import re
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


class EventJournalError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class EventHandoffBinding:
    event_id: str
    operation_id: str
    handoff_key: str
    source_plugin_id: str
    source_revision: int
    target_plugin_id: str
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
            CREATE TABLE IF NOT EXISTS event_delivery_failures (
                event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                plugin_id TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                dead_lettered_at REAL,
                projected_at REAL,
                PRIMARY KEY (event_id, plugin_id)
            );
            CREATE TABLE IF NOT EXISTS event_handoff_bindings (
                event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
                operation_id TEXT NOT NULL,
                handoff_key TEXT NOT NULL UNIQUE,
                source_plugin_id TEXT NOT NULL,
                source_revision INTEGER NOT NULL,
                target_plugin_id TEXT NOT NULL,
                created_at REAL NOT NULL
            );
        """)
        failure_columns = {
            str(row["name"])
            for row in self._connection.execute(
                "PRAGMA table_info(event_delivery_failures)"
            ).fetchall()
        }
        if "projected_at" not in failure_columns:
            self._connection.execute(
                "ALTER TABLE event_delivery_failures ADD COLUMN projected_at REAL"
            )

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

    def publish(
        self,
        event_type: str,
        payload: dict,
        idempotency_key: str,
        *,
        handoff_binding=None,
    ) -> str:
        event_type = str(event_type)
        idempotency_key = str(idempotency_key or uuid.uuid4().hex)
        normalized_binding = self._normalize_handoff_binding(handoff_binding)
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
                    "SELECT id, payload_json FROM events "
                    "WHERE event_type = ? AND idempotency_key = ?",
                    (event_type, idempotency_key),
                ).fetchone()
                if existing:
                    event_id = str(existing["id"])
                    stored = self._connection.execute(
                        "SELECT * FROM event_handoff_bindings WHERE event_id = ?",
                        (event_id,),
                    ).fetchone()
                    if normalized_binding is not None:
                        stored_operation_id = str(
                            json.loads(str(existing["payload_json"])).get(
                                "operation_id"
                            ) or ""
                        ).strip()
                        if (
                            stored_operation_id
                            and stored_operation_id
                            != normalized_binding.operation_id
                        ):
                            raise EventJournalError(
                                "handoff_event_conflict",
                                "event delivery belongs to another handoff",
                            )
                        if stored is None:
                            self._insert_handoff_binding(
                                event_id,
                                normalized_binding,
                                time.time(),
                            )
                        elif not self._binding_matches(
                            stored,
                            normalized_binding,
                        ):
                            raise EventJournalError(
                                "handoff_event_conflict",
                                "event delivery belongs to another handoff",
                            )
                    self._connection.execute("COMMIT")
                    return event_id
                event_id = uuid.uuid4().hex
                now = time.time()
                self._connection.execute(
                    "INSERT INTO events(id, event_type, payload_json, idempotency_key, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (event_id, event_type, payload_json, idempotency_key, now),
                )
                if normalized_binding is not None:
                    self._insert_handoff_binding(
                        event_id,
                        normalized_binding,
                        now,
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
            except sqlite3.IntegrityError as exc:
                self._connection.execute("ROLLBACK")
                raise EventJournalError(
                    "handoff_event_conflict",
                    "event delivery belongs to another handoff",
                ) from exc
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def handoff_binding(self, event_id: str) -> EventHandoffBinding | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM event_handoff_bindings WHERE event_id = ?",
                (str(event_id),),
            ).fetchone()
        if row is None:
            return None
        return EventHandoffBinding(
            event_id=str(row["event_id"]),
            operation_id=str(row["operation_id"]),
            handoff_key=str(row["handoff_key"]),
            source_plugin_id=str(row["source_plugin_id"]),
            source_revision=int(row["source_revision"]),
            target_plugin_id=str(row["target_plugin_id"]),
            created_at=float(row["created_at"]),
        )

    def event_payload(self, event_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM events WHERE id = ?",
                (str(event_id),),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"]))
        return payload if isinstance(payload, dict) else {}

    def attach_handoff_binding(
        self,
        event_id: str,
        handoff_binding,
    ) -> EventHandoffBinding:
        normalized_event = str(event_id)
        normalized_binding = self._normalize_handoff_binding(handoff_binding)
        if normalized_binding is None:
            raise EventJournalError(
                "invalid_handoff_binding",
                "handoff binding identity is invalid",
            )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                event = self._connection.execute(
                    "SELECT payload_json FROM events WHERE id = ?",
                    (normalized_event,),
                ).fetchone()
                if event is None:
                    raise EventJournalError(
                        "event_not_found",
                        "event was not found",
                    )
                payload = json.loads(str(event["payload_json"]))
                payload_operation_id = str(
                    payload.get("operation_id") if isinstance(payload, dict) else ""
                ).strip()
                if payload_operation_id != normalized_binding.operation_id:
                    raise EventJournalError(
                        "handoff_event_conflict",
                        "event delivery belongs to another handoff",
                    )
                stored = self._connection.execute(
                    "SELECT * FROM event_handoff_bindings WHERE event_id = ?",
                    (normalized_event,),
                ).fetchone()
                if stored is not None:
                    if not self._binding_matches(stored, normalized_binding):
                        raise EventJournalError(
                            "handoff_event_conflict",
                            "event delivery belongs to another handoff",
                        )
                    self._connection.execute("COMMIT")
                    return EventHandoffBinding(
                        event_id=str(stored["event_id"]),
                        operation_id=str(stored["operation_id"]),
                        handoff_key=str(stored["handoff_key"]),
                        source_plugin_id=str(stored["source_plugin_id"]),
                        source_revision=int(stored["source_revision"]),
                        target_plugin_id=str(stored["target_plugin_id"]),
                        created_at=float(stored["created_at"]),
                    )
                self._insert_handoff_binding(
                    normalized_event,
                    normalized_binding,
                    time.time(),
                )
                stored = self._connection.execute(
                    "SELECT * FROM event_handoff_bindings WHERE event_id = ?",
                    (normalized_event,),
                ).fetchone()
                self._connection.execute("COMMIT")
                return EventHandoffBinding(
                    event_id=str(stored["event_id"]),
                    operation_id=str(stored["operation_id"]),
                    handoff_key=str(stored["handoff_key"]),
                    source_plugin_id=str(stored["source_plugin_id"]),
                    source_revision=int(stored["source_revision"]),
                    target_plugin_id=str(stored["target_plugin_id"]),
                    created_at=float(stored["created_at"]),
                )
            except sqlite3.IntegrityError as exc:
                self._connection.execute("ROLLBACK")
                raise EventJournalError(
                    "handoff_event_conflict",
                    "event delivery belongs to another handoff",
                ) from exc
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _normalize_handoff_binding(binding) -> EventHandoffBinding | None:
        if binding is None:
            return None
        if isinstance(binding, dict):
            value = binding.get
        else:
            value = lambda key: getattr(binding, key, None)
        operation_id = str(value("operation_id") or "").strip()
        handoff_key = str(value("handoff_key") or "").strip()
        source_plugin_id = str(value("source_plugin_id") or "").strip()
        target_plugin_id = str(value("target_plugin_id") or "").strip()
        try:
            source_revision = int(value("source_revision"))
        except (TypeError, ValueError):
            source_revision = -1
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", operation_id)
            or not source_plugin_id
            or len(source_plugin_id) > 120
            or not target_plugin_id
            or len(target_plugin_id) > 120
            or source_revision < 0
            or handoff_key
            != f"{operation_id}:{source_revision}:{target_plugin_id}"
        ):
            raise EventJournalError(
                "invalid_handoff_binding",
                "handoff binding identity is invalid",
            )
        return EventHandoffBinding(
            event_id="",
            operation_id=operation_id,
            handoff_key=handoff_key,
            source_plugin_id=source_plugin_id,
            source_revision=source_revision,
            target_plugin_id=target_plugin_id,
            created_at=0,
        )

    def _insert_handoff_binding(
        self,
        event_id: str,
        binding: EventHandoffBinding,
        created_at: float,
    ) -> None:
        self._connection.execute(
            "INSERT INTO event_handoff_bindings("
            "event_id, operation_id, handoff_key, source_plugin_id, "
            "source_revision, target_plugin_id, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                binding.operation_id,
                binding.handoff_key,
                binding.source_plugin_id,
                binding.source_revision,
                binding.target_plugin_id,
                created_at,
            ),
        )

    @staticmethod
    def _binding_matches(row, binding: EventHandoffBinding) -> bool:
        return (
            str(row["operation_id"]) == binding.operation_id
            and str(row["handoff_key"]) == binding.handoff_key
            and str(row["source_plugin_id"]) == binding.source_plugin_id
            and int(row["source_revision"]) == binding.source_revision
            and str(row["target_plugin_id"]) == binding.target_plugin_id
        )

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

    def record_failure(
        self, event_id: str, plugin_id: str, error: str, max_attempts: int
    ) -> bool:
        now = time.time()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    "INSERT INTO event_delivery_failures("
                    "event_id, plugin_id, attempt_count, last_error"
                    ") VALUES (?, ?, 1, ?) "
                    "ON CONFLICT(event_id, plugin_id) DO UPDATE SET "
                    "attempt_count = attempt_count + 1, last_error = excluded.last_error",
                    (str(event_id), str(plugin_id), str(error)[:500]),
                )
                row = self._connection.execute(
                    "SELECT attempt_count FROM event_delivery_failures "
                    "WHERE event_id = ? AND plugin_id = ?",
                    (str(event_id), str(plugin_id)),
                ).fetchone()
                exhausted = int(row["attempt_count"]) >= max(1, int(max_attempts))
                if exhausted:
                    self._connection.execute(
                        "UPDATE event_delivery_failures SET "
                        "dead_lettered_at = COALESCE(dead_lettered_at, ?), "
                        "projected_at = NULL WHERE event_id = ? AND plugin_id = ?",
                        (now, str(event_id), str(plugin_id)),
                    )
                    self._connection.execute(
                        "UPDATE event_deliveries SET status = 'acked', updated_at = ? "
                        "WHERE event_id = ? AND plugin_id = ?",
                        (now, str(event_id), str(plugin_id)),
                    )
                self._connection.execute("COMMIT")
                return exhausted
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def unprojected_dead_letters(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_id, plugin_id, last_error, dead_lettered_at "
                "FROM event_delivery_failures WHERE dead_lettered_at IS NOT NULL "
                "AND projected_at IS NULL ORDER BY dead_lettered_at, event_id, plugin_id "
                "LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_dead_letter_projected(self, event_id: str, plugin_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE event_delivery_failures SET projected_at = ? "
                "WHERE event_id = ? AND plugin_id = ? "
                "AND dead_lettered_at IS NOT NULL AND projected_at IS NULL",
                (time.time(), str(event_id), str(plugin_id)),
            )
            return cursor.rowcount == 1

    def dead_letters(self, plugin_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_id, attempt_count, last_error, dead_lettered_at, "
                "projected_at "
                "FROM event_delivery_failures WHERE plugin_id = ? "
                "AND dead_lettered_at IS NOT NULL ORDER BY dead_lettered_at",
                (str(plugin_id),),
            ).fetchall()
        return [dict(row) for row in rows]
