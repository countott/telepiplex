from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping

from .operation_segments import (
    ACTIVE_SEGMENT_STATES,
    SEGMENT_DELIVERY_STATES,
    SEGMENT_STATES,
    OperationMessageSegment,
    freeze_projection,
    projection_hash,
    validate_segment_declaration,
)


VALID_STATES = {
    "awaiting_input",
    "running",
    "handed_off",
    "cancelling",
    "rolling_back",
    "completed",
    "cancelled",
    "rolled_back",
    "partially_rolled_back",
    "failed",
    "interrupted",
}
VALID_CONTROLS = {"", "exit", "cancel", "rollback"}
TERMINAL_STATES = {
    "completed",
    "cancelled",
    "rolled_back",
    "partially_rolled_back",
    "failed",
    "interrupted",
}
ACTIVE_STATES = VALID_STATES - TERMINAL_STATES
HANDOFF_STATES = {"prepared", "submitted", "accepted", "failed", "cancelled"}
EFFECT_RECEIPT_STATES = {
    "prepared",
    "submitted",
    "accepted",
    "completed",
    "failed",
    "cancelled",
}
EFFECT_RECEIPT_TRANSITIONS = {
    "prepared": {
        "submitted", "accepted", "completed", "failed", "cancelled",
    },
    "submitted": {"accepted", "completed", "failed", "cancelled"},
    "accepted": {"completed", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}
MILESTONE_DELIVERY_STATES = {
    "pending",
    "delivering",
    "delivered",
    "failed",
    "unknown",
}
MILESTONE_MAX_ATTEMPTS = 3


class InteractionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    chat_id: int
    user_id: int
    plugin_id: str
    state: str
    stage: str
    status_text: str
    control: str
    revision: int
    message_id: int | None
    message_kind: str
    active_segment_id: str
    next_plugin_id: str
    details: Mapping[str, Any]
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class HandoffReceipt:
    handoff_key: str
    operation_id: str
    source_plugin_id: str
    source_revision: int
    target_plugin_id: str
    state: str
    event_id: str
    error_code: str
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class EffectReceipt:
    effect_key: str
    operation_id: str
    plugin_id: str
    state: str
    receipt: Mapping[str, Any]
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class MilestoneIntent:
    operation_id: str
    milestone_id: str
    plugin_id: str
    chat_id: int
    user_id: int
    mode: str
    text: str
    photo_url: str
    delivery_state: str
    attempt_count: int
    last_error: str
    expected_message_id: int | None
    expected_message_kind: str
    delivered_message_id: int | None
    delivered_message_kind: str
    created_at: float
    updated_at: float


class InteractionCoordinator:
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
        states = ", ".join(f"'{value}'" for value in sorted(VALID_STATES))
        controls = ", ".join(f"'{value}'" for value in sorted(VALID_CONTROLS))
        active_states = ", ".join(f"'{value}'" for value in sorted(ACTIVE_STATES))
        handoff_states = ", ".join(
            f"'{value}'" for value in sorted(HANDOFF_STATES)
        )
        effect_states = ", ".join(
            f"'{value}'" for value in sorted(EFFECT_RECEIPT_STATES)
        )
        milestone_states = ", ".join(
            f"'{value}'" for value in sorted(MILESTONE_DELIVERY_STATES)
        )
        segment_states = ", ".join(
            f"'{value}'" for value in sorted(SEGMENT_STATES)
        )
        segment_delivery_states = ", ".join(
            f"'{value}'" for value in sorted(SEGMENT_DELIVERY_STATES)
        )
        active_segment_states = ", ".join(
            f"'{value}'" for value in sorted(ACTIVE_SEGMENT_STATES)
        )
        self._connection.executescript(f"""
            CREATE TABLE IF NOT EXISTS operations (
                operation_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                plugin_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ({states})),
                stage TEXT NOT NULL,
                status_text TEXT NOT NULL,
                control TEXT NOT NULL CHECK(control IN ({controls})),
                revision INTEGER NOT NULL CHECK(revision > 0),
                message_id INTEGER,
                message_kind TEXT NOT NULL DEFAULT 'text',
                active_segment_id TEXT NOT NULL DEFAULT '',
                next_plugin_id TEXT NOT NULL DEFAULT '',
                details_json TEXT NOT NULL DEFAULT '{{}}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS operations_one_active_owner
            ON operations(chat_id, user_id)
            WHERE state IN ({active_states});
            CREATE INDEX IF NOT EXISTS operations_active_plugin
            ON operations(plugin_id, state, updated_at);
            CREATE TABLE IF NOT EXISTS operation_message_segments (
                segment_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK(sequence > 0),
                owner_plugin_id TEXT NOT NULL,
                role TEXT NOT NULL,
                generation INTEGER NOT NULL CHECK(generation > 0),
                presentation_kind TEXT NOT NULL
                    CHECK(presentation_kind IN ('text', 'photo')),
                state TEXT NOT NULL CHECK(state IN ({segment_states})),
                message_id INTEGER,
                message_kind TEXT NOT NULL DEFAULT '',
                business_revision INTEGER NOT NULL CHECK(business_revision > 0),
                rendered_revision INTEGER NOT NULL DEFAULT 0
                    CHECK(rendered_revision >= 0),
                projection_hash TEXT NOT NULL,
                rendered_projection_hash TEXT NOT NULL DEFAULT '',
                projection_json TEXT NOT NULL DEFAULT '{{}}',
                callback_generation INTEGER NOT NULL DEFAULT 1
                    CHECK(callback_generation > 0),
                callback_state TEXT NOT NULL DEFAULT 'idle'
                    CHECK(callback_state IN ('idle', 'busy')),
                callback_token TEXT NOT NULL DEFAULT '',
                callback_busy_text TEXT NOT NULL DEFAULT '',
                delivery_state TEXT NOT NULL
                    CHECK(delivery_state IN ({segment_delivery_states})),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                sealed_at REAL,
                UNIQUE(operation_id, sequence),
                FOREIGN KEY(operation_id) REFERENCES operations(operation_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS operation_segments_one_active
            ON operation_message_segments(operation_id)
            WHERE state IN ({active_segment_states});
            CREATE INDEX IF NOT EXISTS operation_segments_owner
            ON operation_message_segments(operation_id, owner_plugin_id, sequence);
            CREATE TABLE IF NOT EXISTS operation_milestones (
                operation_id TEXT NOT NULL,
                milestone_id TEXT NOT NULL,
                plugin_id TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0,
                delivery_started INTEGER NOT NULL DEFAULT 0,
                delivered_message_id INTEGER,
                delivered_message_kind TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL DEFAULT '',
                photo_url TEXT NOT NULL DEFAULT '',
                delivery_state TEXT NOT NULL DEFAULT 'unknown'
                    CHECK(delivery_state IN ({milestone_states})),
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                expected_message_id INTEGER,
                expected_message_kind TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(operation_id, milestone_id),
                FOREIGN KEY(operation_id) REFERENCES operations(operation_id)
            );
            CREATE TABLE IF NOT EXISTS operation_handoffs (
                handoff_key TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                source_plugin_id TEXT NOT NULL,
                source_revision INTEGER NOT NULL CHECK(source_revision > 0),
                target_plugin_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ({handoff_states})),
                event_id TEXT NOT NULL DEFAULT '',
                error_code TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(operation_id) REFERENCES operations(operation_id)
            );
            CREATE INDEX IF NOT EXISTS operation_handoffs_operation
            ON operation_handoffs(operation_id, source_revision, target_plugin_id);
            CREATE INDEX IF NOT EXISTS operation_handoffs_event
            ON operation_handoffs(event_id, target_plugin_id)
            WHERE event_id != '';
            CREATE UNIQUE INDEX IF NOT EXISTS operation_handoffs_event_target_unique
            ON operation_handoffs(event_id, target_plugin_id)
            WHERE event_id != '';
            CREATE TABLE IF NOT EXISTS operation_effect_receipts (
                effect_key TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                plugin_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ({effect_states})),
                receipt_json TEXT NOT NULL DEFAULT '{{}}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(operation_id) REFERENCES operations(operation_id)
            );
            CREATE INDEX IF NOT EXISTS operation_effect_receipts_operation
            ON operation_effect_receipts(operation_id, created_at, effect_key);
        """)
        columns = {
            str(row["name"])
            for row in self._connection.execute(
                "PRAGMA table_info(operations)"
            ).fetchall()
        }
        if "message_kind" not in columns:
            self._connection.execute(
                "ALTER TABLE operations ADD COLUMN "
                "message_kind TEXT NOT NULL DEFAULT 'text'"
            )
        if "active_segment_id" not in columns:
            self._connection.execute(
                "ALTER TABLE operations ADD COLUMN "
                "active_segment_id TEXT NOT NULL DEFAULT ''"
            )
        segment_columns = {
            str(row["name"])
            for row in self._connection.execute(
                "PRAGMA table_info(operation_message_segments)"
            ).fetchall()
        }
        for column, declaration in {
            "rendered_projection_hash": "TEXT NOT NULL DEFAULT ''",
            "callback_state": "TEXT NOT NULL DEFAULT 'idle'",
            "callback_token": "TEXT NOT NULL DEFAULT ''",
            "callback_busy_text": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column not in segment_columns:
                self._connection.execute(
                    f"ALTER TABLE operation_message_segments ADD COLUMN "
                    f"{column} {declaration}"
                )
        self._connection.execute(
            "UPDATE operation_message_segments "
            "SET rendered_projection_hash = projection_hash "
            "WHERE rendered_projection_hash = '' AND rendered_revision > 0"
        )
        milestone_columns = {
            str(row["name"])
            for row in self._connection.execute(
                "PRAGMA table_info(operation_milestones)"
            ).fetchall()
        }
        migrated_legacy_milestones = "delivered" not in milestone_columns
        if migrated_legacy_milestones:
            self._connection.execute(
                "ALTER TABLE operation_milestones ADD COLUMN "
                "delivered INTEGER NOT NULL DEFAULT 0"
            )
            self._connection.execute(
                "UPDATE operation_milestones SET delivered = 1"
            )
        if "delivered_message_id" not in milestone_columns:
            self._connection.execute(
                "ALTER TABLE operation_milestones ADD COLUMN "
                "delivered_message_id INTEGER"
            )
        if "delivery_started" not in milestone_columns:
            self._connection.execute(
                "ALTER TABLE operation_milestones ADD COLUMN "
                "delivery_started INTEGER NOT NULL DEFAULT 0"
            )
            if migrated_legacy_milestones:
                self._connection.execute(
                    "UPDATE operation_milestones SET delivery_started = 1"
                )
        if "delivered_message_kind" not in milestone_columns:
            self._connection.execute(
                "ALTER TABLE operation_milestones ADD COLUMN "
                "delivered_message_kind TEXT NOT NULL DEFAULT ''"
            )
        durable_columns = {
            "mode": "TEXT NOT NULL DEFAULT ''",
            "text": "TEXT NOT NULL DEFAULT ''",
            "photo_url": "TEXT NOT NULL DEFAULT ''",
            "delivery_state": "TEXT NOT NULL DEFAULT 'unknown'",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "last_error": "TEXT NOT NULL DEFAULT ''",
            "expected_message_id": "INTEGER",
            "expected_message_kind": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "REAL NOT NULL DEFAULT 0",
        }
        for column, declaration in durable_columns.items():
            if column not in milestone_columns:
                self._connection.execute(
                    f"ALTER TABLE operation_milestones ADD COLUMN "
                    f"{column} {declaration}"
                )
        self._connection.execute(
            "UPDATE operation_milestones SET delivery_state = CASE "
            "WHEN delivered = 1 THEN 'delivered' "
            "WHEN typeof(delivered_message_id) = 'integer' "
            "AND delivered_message_id > 0 "
            "AND delivered_message_kind IN ('text', 'photo') "
            "AND typeof(attempt_count) = 'integer' "
            "AND attempt_count BETWEEN 0 AND ? "
            "AND ((expected_message_id IS NULL "
            "AND expected_message_kind = '') "
            "OR (typeof(expected_message_id) = 'integer' "
            "AND expected_message_id > 0 "
            "AND expected_message_kind IN ('text', 'photo'))) "
            "THEN 'delivering' "
            "WHEN (((delivery_state = 'pending') "
            "AND typeof(delivery_started) = 'integer' "
            "AND delivery_started = 0 "
            "AND attempt_count = 0 AND last_error = '') "
            "OR ((delivery_state = 'failed') "
            "AND typeof(delivery_started) = 'integer' "
            "AND ((last_error = 'telegram_rejected' "
            "AND delivery_started = 1 AND attempt_count > 0) "
            "OR (last_error = 'explicit_rejection' "
            "AND ((delivery_started = 0 AND attempt_count = 0) "
            "OR (delivery_started = 1 AND attempt_count > 0))))) "
            "OR delivery_state IN ('delivering', 'unknown')) "
            "AND delivered_message_id IS NULL "
            "AND delivered_message_kind = '' "
            "AND mode IN ('identity', 'stage') "
            "AND length(text) BETWEEN 1 AND "
            "CASE WHEN photo_url != '' THEN 1024 ELSE 4096 END "
            "AND ((mode = 'stage' AND photo_url = '') "
            "OR (mode = 'identity' AND (photo_url = '' "
            "OR (length(photo_url) <= 2048 "
            "AND substr(photo_url, 1, 8) = 'https://')))) "
            "AND typeof(attempt_count) = 'integer' "
            "AND attempt_count BETWEEN 0 AND ? "
            "AND ((expected_message_id IS NULL "
            "AND expected_message_kind = '') "
            "OR (typeof(expected_message_id) = 'integer' "
            "AND expected_message_id > 0 "
            "AND expected_message_kind IN ('text', 'photo'))) "
            "THEN delivery_state ELSE 'unknown' END, "
            "updated_at = CASE WHEN updated_at = 0 THEN created_at "
            "ELSE updated_at END",
            (MILESTONE_MAX_ATTEMPTS, MILESTONE_MAX_ATTEMPTS),
        )
        self._migrate_legacy_message_cursors()

    def _migrate_legacy_message_cursors(self) -> None:
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        rows = self._connection.execute(
            f"SELECT * FROM operations WHERE active_segment_id = '' "
            f"AND message_id IS NOT NULL AND message_id > 0 "
            f"AND state IN ({placeholders}) ORDER BY created_at, operation_id",
            tuple(sorted(ACTIVE_STATES)),
        ).fetchall()
        if not rows:
            return
        with self._connection:
            for row in rows:
                operation_id = str(row["operation_id"])
                current = self._connection.execute(
                    "SELECT active_segment_id FROM operations "
                    "WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if current is None or str(current["active_segment_id"] or ""):
                    continue
                counters = self._connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) AS sequence, "
                    "COALESCE(MAX(generation), 0) AS generation "
                    "FROM operation_message_segments WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                sequence = int(counters["sequence"]) + 1
                generation = int(counters["generation"]) + 1
                message_kind = str(row["message_kind"] or "text").casefold()
                if message_kind not in {"text", "photo"}:
                    message_kind = "text"
                projection = {
                    "state": str(row["state"]),
                    "stage": str(row["stage"]),
                    "status_text": str(row["status_text"]),
                    "control": str(row["control"]),
                    "details": json.loads(str(row["details_json"])),
                }
                projection_json = json.dumps(
                    projection,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                now = time.time()
                segment_id = "legacy-" + uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"telepiplex:operation-segment:{operation_id}:{sequence}",
                ).hex
                self._connection.execute(
                    "INSERT OR IGNORE INTO operation_message_segments("
                    "segment_id, operation_id, sequence, owner_plugin_id, role, "
                    "generation, presentation_kind, state, message_id, message_kind, "
                    "business_revision, rendered_revision, projection_hash, "
                    "rendered_projection_hash, projection_json, "
                    "callback_generation, delivery_state, "
                    "created_at, updated_at, sealed_at"
                    ") VALUES (?, ?, ?, ?, 'legacy', ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, "
                    "1, 'delivered', ?, ?, NULL)",
                    (
                        segment_id,
                        operation_id,
                        sequence,
                        str(row["plugin_id"]),
                        generation,
                        message_kind,
                        int(row["message_id"]),
                        message_kind,
                        int(row["revision"]),
                        int(row["revision"]),
                        projection_hash(projection),
                        projection_hash(projection),
                        projection_json,
                        float(row["created_at"]),
                        now,
                    ),
                )
                self._connection.execute(
                    "UPDATE operations SET active_segment_id = ? "
                    "WHERE operation_id = ? AND active_segment_id = ''",
                    (segment_id, operation_id),
                )

    def close(self):
        with self._lock:
            self._connection.close()

    def report(self, plugin_id: str, report: dict) -> OperationRecord:
        values = self._validate_report(plugin_id, report)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM operations WHERE operation_id = ?",
                    (values["operation_id"],),
                ).fetchone()
                previous = self._from_row(row) if row is not None else None
                if row is None:
                    self._reject_active_conflict(values)
                    now = time.time()
                    self._connection.execute(
                        "INSERT INTO operations("
                        "operation_id, chat_id, user_id, plugin_id, state, stage, "
                        "status_text, control, revision, message_id, next_plugin_id, "
                        "details_json, created_at, updated_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)",
                        (
                            values["operation_id"],
                            values["chat_id"],
                            values["user_id"],
                            values["plugin_id"],
                            values["state"],
                            values["stage"],
                            values["status_text"],
                            values["control"],
                            values["revision"],
                            values["next_plugin_id"],
                            values["details_json"],
                            now,
                            now,
                        ),
                    )
                else:
                    current = previous
                    self._validate_existing_owner(current, values)
                    if (
                        values["state"] == "handed_off"
                        and current.active_segment_id
                    ):
                        raise InteractionError(
                            "segment_not_sealed",
                            "active message segment must be sealed before handoff",
                        )
                    if current.state in TERMINAL_STATES:
                        self._connection.execute("COMMIT")
                        return current
                    if values["revision"] <= current.revision:
                        self._connection.execute("COMMIT")
                        return current
                    owner_changed = current.plugin_id != values["plugin_id"]
                    next_plugin_id = "" if owner_changed else values["next_plugin_id"]
                    self._connection.execute(
                        "UPDATE operations SET plugin_id = ?, state = ?, stage = ?, "
                        "status_text = ?, control = ?, revision = ?, next_plugin_id = ?, "
                        "details_json = ?, "
                        "message_id = CASE WHEN ? THEN NULL ELSE message_id END, "
                        "message_kind = CASE WHEN ? THEN '' ELSE message_kind END, "
                        "updated_at = ? WHERE operation_id = ?",
                        (
                            values["plugin_id"],
                            values["state"],
                            values["stage"],
                            values["status_text"],
                            values["control"],
                            values["revision"],
                            next_plugin_id,
                            values["details_json"],
                            owner_changed,
                            owner_changed,
                            time.time(),
                            values["operation_id"],
                        ),
                    )
                self._apply_receipt_transitions(previous, values)
                stored = self._connection.execute(
                    "SELECT * FROM operations WHERE operation_id = ?",
                    (values["operation_id"],),
                ).fetchone()
                self._connection.execute("COMMIT")
                return self._from_row(stored)
            except InteractionError:
                self._connection.execute("ROLLBACK")
                raise
            except sqlite3.IntegrityError as exc:
                self._connection.execute("ROLLBACK")
                raise InteractionError(
                    "operation_conflict", "another operation already owns this user"
                ) from exc
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def accept_segment_report(
        self,
        plugin_id: str,
        report: dict,
    ) -> tuple[OperationRecord, OperationMessageSegment]:
        values = self._validate_report(plugin_id, report)
        try:
            role, presentation_kind = validate_segment_declaration(
                report.get("segment") if isinstance(report, dict) else None
            )
            projection = report.get("projection")
            if projection is None:
                projection = {
                    "state": values["state"],
                    "stage": values["stage"],
                    "status_text": values["status_text"],
                    "control": values["control"],
                    "details": json.loads(values["details_json"]),
                }
            normalized_projection_hash = projection_hash(projection)
            projection_json = json.dumps(
                projection,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise InteractionError("invalid_segment", str(exc)) from exc

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing_row = self._connection.execute(
                    "SELECT * FROM operations WHERE operation_id = ?",
                    (values["operation_id"],),
                ).fetchone()
                now = time.time()
                previous = (
                    self._from_row(existing_row)
                    if existing_row is not None
                    else None
                )
                if previous is None:
                    self._reject_active_conflict(values)
                    self._connection.execute(
                        "INSERT INTO operations("
                        "operation_id, chat_id, user_id, plugin_id, state, stage, "
                        "status_text, control, revision, message_id, next_plugin_id, "
                        "details_json, created_at, updated_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)",
                        (
                            values["operation_id"],
                            values["chat_id"],
                            values["user_id"],
                            values["plugin_id"],
                            values["state"],
                            values["stage"],
                            values["status_text"],
                            values["control"],
                            values["revision"],
                            values["next_plugin_id"],
                            values["details_json"],
                            now,
                            now,
                        ),
                    )
                else:
                    self._validate_existing_owner(previous, values)
                    if previous.state in TERMINAL_STATES:
                        raise InteractionError(
                            "operation_terminal",
                            "terminal operation cannot update a message segment",
                        )
                    if values["revision"] > previous.revision:
                        owner_changed = previous.plugin_id != values["plugin_id"]
                        next_plugin_id = (
                            "" if owner_changed else values["next_plugin_id"]
                        )
                        self._connection.execute(
                            "UPDATE operations SET plugin_id = ?, state = ?, "
                            "stage = ?, status_text = ?, control = ?, revision = ?, "
                            "next_plugin_id = ?, details_json = ?, updated_at = ? "
                            "WHERE operation_id = ?",
                            (
                                values["plugin_id"],
                                values["state"],
                                values["stage"],
                                values["status_text"],
                                values["control"],
                                values["revision"],
                                next_plugin_id,
                                values["details_json"],
                                now,
                                values["operation_id"],
                            ),
                        )

                active_row = self._connection.execute(
                    "SELECT segment.* FROM operations operation "
                    "JOIN operation_message_segments segment "
                    "ON segment.segment_id = operation.active_segment_id "
                    "WHERE operation.operation_id = ?",
                    (values["operation_id"],),
                ).fetchone()
                if active_row is None:
                    counters = self._connection.execute(
                        "SELECT COALESCE(MAX(sequence), 0) AS sequence, "
                        "COALESCE(MAX(generation), 0) AS generation "
                        "FROM operation_message_segments WHERE operation_id = ?",
                        (values["operation_id"],),
                    ).fetchone()
                    sequence = int(counters["sequence"]) + 1
                    generation = int(counters["generation"]) + 1
                    segment_id = uuid.uuid4().hex
                    self._connection.execute(
                        "INSERT INTO operation_message_segments("
                        "segment_id, operation_id, sequence, owner_plugin_id, role, "
                        "generation, presentation_kind, state, message_id, "
                        "message_kind, business_revision, rendered_revision, "
                        "projection_hash, rendered_projection_hash, projection_json, "
                        "callback_generation, "
                        "delivery_state, created_at, updated_at, sealed_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, 'creating', NULL, '', ?, 0, "
                        "?, '', ?, 1, 'reserved', ?, ?, NULL)",
                        (
                            segment_id,
                            values["operation_id"],
                            sequence,
                            values["plugin_id"],
                            role,
                            generation,
                            presentation_kind,
                            values["revision"],
                            normalized_projection_hash,
                            projection_json,
                            now,
                            now,
                        ),
                    )
                    self._connection.execute(
                        "UPDATE operations SET active_segment_id = ? "
                        "WHERE operation_id = ?",
                        (segment_id, values["operation_id"]),
                    )
                else:
                    segment_id = str(active_row["segment_id"])
                    if str(active_row["owner_plugin_id"]) != values["plugin_id"]:
                        raise InteractionError(
                            "segment_owner_conflict",
                            "active message segment belongs to another Feature",
                        )
                    if (
                        str(active_row["role"]) != role
                        or str(active_row["presentation_kind"])
                        != presentation_kind
                    ):
                        raise InteractionError(
                            "segment_role_conflict",
                            "active message segment role or kind is incompatible",
                        )
                    current_business_revision = int(
                        active_row["business_revision"]
                    )
                    if values["revision"] > current_business_revision:
                        self._connection.execute(
                            "UPDATE operation_message_segments SET "
                            "business_revision = ?, projection_hash = ?, "
                            "projection_json = ?, updated_at = ? "
                            "WHERE segment_id = ?",
                            (
                                values["revision"],
                                normalized_projection_hash,
                                projection_json,
                                now,
                                segment_id,
                            ),
                        )
                    elif (
                        values["revision"] == current_business_revision
                        and normalized_projection_hash
                        == str(active_row["projection_hash"])
                    ):
                        pass
                    elif values["revision"] == current_business_revision:
                        raise InteractionError(
                            "segment_revision_conflict",
                            "segment revision already has another projection",
                        )
                    else:
                        raise InteractionError(
                            "invalid_revision",
                            "segment business revision cannot decrease",
                        )
                if previous is None or values["revision"] > previous.revision:
                    self._apply_receipt_transitions(previous, values)
                operation_row = self._connection.execute(
                    "SELECT * FROM operations WHERE operation_id = ?",
                    (values["operation_id"],),
                ).fetchone()
                segment_row = self._connection.execute(
                    "SELECT * FROM operation_message_segments WHERE segment_id = ?",
                    (segment_id,),
                ).fetchone()
                self._connection.execute("COMMIT")
            except InteractionError:
                self._connection.execute("ROLLBACK")
                raise
            except sqlite3.IntegrityError as exc:
                self._connection.execute("ROLLBACK")
                raise InteractionError(
                    "operation_conflict", "another operation already owns this user"
                ) from exc
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return self._from_row(operation_row), self._segment_from_row(segment_row)

    def get_active_segment(
        self,
        operation_id: str,
    ) -> OperationMessageSegment | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT segment.* FROM operations operation "
                "JOIN operation_message_segments segment "
                "ON segment.segment_id = operation.active_segment_id "
                "WHERE operation.operation_id = ?",
                (str(operation_id),),
            ).fetchone()
        return self._segment_from_row(row) if row is not None else None

    def has_message_segments(self, operation_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM operation_message_segments "
                "WHERE operation_id = ? LIMIT 1",
                (str(operation_id),),
            ).fetchone()
        return row is not None

    def get_segment(self, segment_id: str) -> OperationMessageSegment | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM operation_message_segments WHERE segment_id = ?",
                (str(segment_id),),
            ).fetchone()
        return self._segment_from_row(row) if row is not None else None

    def bind_segment_message(
        self,
        segment_id: str,
        *,
        owner_plugin_id: str,
        generation: int,
        chat_id: int,
        message_id: int,
        message_kind: str | None = None,
    ) -> OperationMessageSegment | None:
        try:
            normalized_generation = int(generation)
            normalized_chat_id = int(chat_id)
            normalized_message_id = int(message_id)
        except (TypeError, ValueError):
            normalized_generation = normalized_chat_id = normalized_message_id = 0
        normalized_owner = str(owner_plugin_id or "").strip()
        normalized_kind = str(message_kind or "").strip().casefold()
        if (
            not normalized_owner
            or normalized_generation <= 0
            or normalized_chat_id == 0
            or normalized_message_id <= 0
            or normalized_kind not in {"", "text", "photo"}
        ):
            raise InteractionError(
                "invalid_segment_delivery",
                "segment delivery target is invalid",
            )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE operation_message_segments SET "
                "message_id = ?, message_kind = CASE "
                "WHEN ? = '' THEN presentation_kind ELSE ? END, "
                "state = 'open', delivery_state = 'delivered', updated_at = ? "
                "WHERE segment_id = ? AND owner_plugin_id = ? AND generation = ? "
                "AND state = 'creating' AND delivery_state IN ('reserved', 'delivering') "
                "AND EXISTS (SELECT 1 FROM operations operation "
                "WHERE operation.operation_id = operation_message_segments.operation_id "
                "AND operation.chat_id = ? "
                "AND operation.active_segment_id = operation_message_segments.segment_id)",
                (
                    normalized_message_id,
                    normalized_kind,
                    normalized_kind,
                    time.time(),
                    str(segment_id),
                    normalized_owner,
                    normalized_generation,
                    normalized_chat_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM operation_message_segments WHERE segment_id = ?",
                (str(segment_id),),
            ).fetchone()
        return self._segment_from_row(row)

    def replace_segment_message(
        self,
        segment_id: str,
        *,
        owner_plugin_id: str,
        generation: int,
        chat_id: int,
        expected_message_id: int,
        expected_message_kind: str,
        message_id: int,
        message_kind: str,
    ) -> OperationMessageSegment | None:
        try:
            normalized_generation = int(generation)
            normalized_chat_id = int(chat_id)
            normalized_expected_id = int(expected_message_id)
            normalized_message_id = int(message_id)
        except (TypeError, ValueError):
            normalized_generation = normalized_chat_id = 0
            normalized_expected_id = normalized_message_id = 0
        normalized_owner = str(owner_plugin_id or "").strip()
        normalized_expected_kind = str(
            expected_message_kind or ""
        ).strip().casefold()
        normalized_kind = str(message_kind or "").strip().casefold()
        if (
            not normalized_owner
            or normalized_generation <= 0
            or normalized_chat_id == 0
            or normalized_expected_id <= 0
            or normalized_message_id <= 0
            or normalized_expected_kind not in {"text", "photo"}
            or normalized_kind not in {"text", "photo"}
        ):
            raise InteractionError(
                "invalid_segment_delivery",
                "replacement segment delivery target is invalid",
            )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE operation_message_segments SET message_id = ?, "
                "message_kind = ?, delivery_state = 'delivered', updated_at = ? "
                "WHERE segment_id = ? AND owner_plugin_id = ? "
                "AND generation = ? AND state = 'open' "
                "AND delivery_state = 'delivering' "
                "AND message_id = ? AND message_kind = ? "
                "AND EXISTS (SELECT 1 FROM operations operation "
                "WHERE operation.operation_id = "
                "operation_message_segments.operation_id "
                "AND operation.chat_id = ? "
                "AND operation.active_segment_id = "
                "operation_message_segments.segment_id)",
                (
                    normalized_message_id,
                    normalized_kind,
                    time.time(),
                    str(segment_id),
                    normalized_owner,
                    normalized_generation,
                    normalized_expected_id,
                    normalized_expected_kind,
                    normalized_chat_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM operation_message_segments "
                "WHERE segment_id = ?",
                (str(segment_id),),
            ).fetchone()
        return self._segment_from_row(row)

    def claim_segment_replacement_delivery(
        self,
        segment_id: str,
        *,
        owner_plugin_id: str,
        generation: int,
        chat_id: int,
        expected_message_id: int,
        expected_message_kind: str,
    ) -> OperationMessageSegment | None:
        try:
            normalized_generation = int(generation)
            normalized_chat_id = int(chat_id)
            normalized_message_id = int(expected_message_id)
        except (TypeError, ValueError):
            normalized_generation = normalized_chat_id = 0
            normalized_message_id = 0
        normalized_owner = str(owner_plugin_id or "").strip()
        normalized_kind = str(expected_message_kind or "").strip().casefold()
        if (
            not normalized_owner
            or normalized_generation <= 0
            or normalized_chat_id == 0
            or normalized_message_id <= 0
            or normalized_kind not in {"text", "photo"}
        ):
            raise InteractionError(
                "invalid_segment_delivery",
                "replacement segment delivery identity is invalid",
            )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE operation_message_segments SET "
                "delivery_state = 'delivering', updated_at = ? "
                "WHERE segment_id = ? AND owner_plugin_id = ? "
                "AND generation = ? AND state = 'open' "
                "AND delivery_state = 'delivered' "
                "AND message_id = ? AND message_kind = ? "
                "AND EXISTS (SELECT 1 FROM operations operation "
                "WHERE operation.operation_id = "
                "operation_message_segments.operation_id "
                "AND operation.chat_id = ? "
                "AND operation.active_segment_id = "
                "operation_message_segments.segment_id)",
                (
                    time.time(),
                    str(segment_id),
                    normalized_owner,
                    normalized_generation,
                    normalized_message_id,
                    normalized_kind,
                    normalized_chat_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM operation_message_segments "
                "WHERE segment_id = ?",
                (str(segment_id),),
            ).fetchone()
        return self._segment_from_row(row)

    def claim_segment_delivery(
        self,
        segment_id: str,
        *,
        owner_plugin_id: str,
        generation: int,
    ) -> OperationMessageSegment | None:
        try:
            normalized_generation = int(generation)
        except (TypeError, ValueError):
            normalized_generation = 0
        normalized_owner = str(owner_plugin_id or "").strip()
        if not normalized_owner or normalized_generation <= 0:
            raise InteractionError(
                "invalid_segment_delivery",
                "segment delivery identity is invalid",
            )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE operation_message_segments SET "
                "delivery_state = 'delivering', updated_at = ? "
                "WHERE segment_id = ? AND owner_plugin_id = ? AND generation = ? "
                "AND state = 'creating' AND delivery_state = 'reserved' "
                "AND message_id IS NULL AND EXISTS ("
                "SELECT 1 FROM operations operation "
                "WHERE operation.active_segment_id = "
                "operation_message_segments.segment_id)",
                (
                    time.time(),
                    str(segment_id),
                    normalized_owner,
                    normalized_generation,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM operation_message_segments WHERE segment_id = ?",
                (str(segment_id),),
            ).fetchone()
        return self._segment_from_row(row)

    def record_segment_rendered(
        self,
        segment_id: str,
        *,
        owner_plugin_id: str,
        generation: int,
        business_revision: int,
        projection_hash: str,
    ) -> OperationMessageSegment | None:
        try:
            normalized_generation = int(generation)
            normalized_revision = int(business_revision)
        except (TypeError, ValueError):
            normalized_generation = normalized_revision = 0
        normalized_owner = str(owner_plugin_id or "").strip()
        normalized_hash = str(projection_hash or "").strip()
        if (
            not normalized_owner
            or normalized_generation <= 0
            or normalized_revision <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", normalized_hash)
        ):
            raise InteractionError(
                "invalid_segment_render",
                "rendered segment identity is invalid",
            )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE operation_message_segments SET rendered_revision = ?, "
                "rendered_projection_hash = ?, "
                "updated_at = ? WHERE segment_id = ? AND owner_plugin_id = ? "
                "AND generation = ? AND state IN ('open', 'sealing') "
                "AND message_id IS NOT NULL AND business_revision >= ? "
                "AND projection_hash = ? "
                "AND (rendered_revision < ? OR rendered_projection_hash != ?)",
                (
                    normalized_revision,
                    normalized_hash,
                    time.time(),
                    str(segment_id),
                    normalized_owner,
                    normalized_generation,
                    normalized_revision,
                    normalized_hash,
                    normalized_revision,
                    normalized_hash,
                ),
            )
            if cursor.rowcount != 1:
                row = self._connection.execute(
                    "SELECT * FROM operation_message_segments "
                    "WHERE segment_id = ? AND owner_plugin_id = ? "
                    "AND generation = ? AND rendered_revision = ? "
                    "AND projection_hash = ?",
                    (
                        str(segment_id),
                        normalized_owner,
                        normalized_generation,
                        normalized_revision,
                        normalized_hash,
                    ),
                ).fetchone()
                return self._segment_from_row(row) if row is not None else None
            row = self._connection.execute(
                "SELECT * FROM operation_message_segments WHERE segment_id = ?",
                (str(segment_id),),
            ).fetchone()
        return self._segment_from_row(row)

    def mark_segment_delivery_uncertain(
        self,
        segment_id: str,
        *,
        owner_plugin_id: str,
        generation: int,
    ) -> OperationMessageSegment | None:
        try:
            normalized_generation = int(generation)
        except (TypeError, ValueError):
            normalized_generation = 0
        normalized_owner = str(owner_plugin_id or "").strip()
        if not normalized_owner or normalized_generation <= 0:
            raise InteractionError(
                "invalid_segment_delivery",
                "segment delivery identity is invalid",
            )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE operation_message_segments SET "
                "state = 'delivery_uncertain', delivery_state = 'uncertain', "
                "updated_at = ? WHERE segment_id = ? AND owner_plugin_id = ? "
                "AND generation = ? "
                "AND state IN ('creating', 'open', 'sealing')",
                (
                    time.time(),
                    str(segment_id),
                    normalized_owner,
                    normalized_generation,
                ),
            )
            if cursor.rowcount != 1:
                row = self._connection.execute(
                    "SELECT * FROM operation_message_segments "
                    "WHERE segment_id = ? AND owner_plugin_id = ? "
                    "AND generation = ? AND state = 'delivery_uncertain'",
                    (
                        str(segment_id),
                        normalized_owner,
                        normalized_generation,
                    ),
                ).fetchone()
                return self._segment_from_row(row) if row is not None else None
            row = self._connection.execute(
                "SELECT * FROM operation_message_segments WHERE segment_id = ?",
                (str(segment_id),),
            ).fetchone()
        return self._segment_from_row(row)

    def seal_segment(
        self,
        plugin_id: str,
        operation_id: str,
        role: str,
    ) -> OperationMessageSegment:
        normalized_plugin = str(plugin_id or "").strip()
        normalized_operation = str(operation_id or "").strip()
        normalized_role = str(role or "").strip()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT segment.* FROM operations operation "
                "JOIN operation_message_segments segment "
                "ON segment.segment_id = operation.active_segment_id "
                "WHERE operation.operation_id = ?",
                (normalized_operation,),
            ).fetchone()
            if row is None:
                raise InteractionError(
                    "segment_not_found", "active message segment was not found"
                )
            if str(row["owner_plugin_id"]) != normalized_plugin:
                raise InteractionError(
                    "segment_owner_conflict",
                    "active message segment belongs to another Feature",
                )
            if str(row["role"]) != normalized_role or normalized_role == "legacy":
                raise InteractionError(
                    "segment_role_conflict",
                    "active message segment role is incompatible",
                )
            if str(row["state"]) == "sealing":
                return self._segment_from_row(row)
            if (
                str(row["state"]) != "open"
                or str(row["delivery_state"]) != "delivered"
            ):
                raise InteractionError(
                    "segment_state_conflict",
                    "message segment is not ready to seal",
                )
            self._connection.execute(
                "UPDATE operation_message_segments SET state = 'sealing', "
                "updated_at = ? WHERE segment_id = ? AND state = 'open' "
                "AND delivery_state = 'delivered'",
                (time.time(), str(row["segment_id"])),
            )
            stored = self._connection.execute(
                "SELECT * FROM operation_message_segments WHERE segment_id = ?",
                (str(row["segment_id"]),),
            ).fetchone()
        return self._segment_from_row(stored)

    def complete_segment_seal(
        self,
        segment_id: str,
        *,
        owner_plugin_id: str,
        generation: int,
    ) -> OperationMessageSegment:
        try:
            normalized_generation = int(generation)
        except (TypeError, ValueError):
            normalized_generation = 0
        normalized_owner = str(owner_plugin_id or "").strip()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM operation_message_segments "
                    "WHERE segment_id = ? AND owner_plugin_id = ? "
                    "AND generation = ?",
                    (
                        str(segment_id),
                        normalized_owner,
                        normalized_generation,
                    ),
                ).fetchone()
                if row is None:
                    raise InteractionError(
                        "segment_not_found", "message segment was not found"
                    )
                if str(row["state"]) == "sealed":
                    self._connection.execute("COMMIT")
                    return self._segment_from_row(row)
                if (
                    str(row["state"]) != "sealing"
                    or row["message_id"] is None
                    or int(row["rendered_revision"])
                    != int(row["business_revision"])
                ):
                    raise InteractionError(
                        "segment_state_conflict",
                        "message segment has not rendered its latest revision",
                    )
                now = time.time()
                self._connection.execute(
                    "UPDATE operation_message_segments SET state = 'sealed', "
                    "sealed_at = ?, updated_at = ? WHERE segment_id = ?",
                    (now, now, str(segment_id)),
                )
                self._connection.execute(
                    "UPDATE operations SET active_segment_id = '', updated_at = ? "
                    "WHERE operation_id = ? AND active_segment_id = ?",
                    (now, str(row["operation_id"]), str(segment_id)),
                )
                stored = self._connection.execute(
                    "SELECT * FROM operation_message_segments WHERE segment_id = ?",
                    (str(segment_id),),
                ).fetchone()
                self._connection.execute("COMMIT")
            except InteractionError:
                self._connection.execute("ROLLBACK")
                raise
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return self._segment_from_row(stored)

    def claim_segment_callback(
        self,
        plugin_id: str,
        operation_id: str,
        *,
        message_id: int,
        segment_generation: int,
        callback_generation: int,
        callback_token: str = "",
        busy_text: str = "",
    ) -> OperationMessageSegment | None:
        try:
            normalized_message_id = int(message_id)
            normalized_segment_generation = int(segment_generation)
            normalized_callback_generation = int(callback_generation)
        except (TypeError, ValueError):
            normalized_message_id = 0
            normalized_segment_generation = 0
            normalized_callback_generation = 0
        normalized_plugin = str(plugin_id or "").strip()
        normalized_operation = str(operation_id or "").strip()
        normalized_token = str(callback_token or "")
        normalized_busy_text = str(busy_text or "")
        if (
            not normalized_plugin
            or not normalized_operation
            or normalized_message_id <= 0
            or normalized_segment_generation <= 0
            or normalized_callback_generation <= 0
            or len(normalized_token.encode("utf-8")) > 64
            or len(normalized_busy_text) > 1024
        ):
            raise InteractionError(
                "invalid_segment_callback",
                "segment callback identity is invalid",
            )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE operation_message_segments SET "
                "callback_generation = callback_generation + 1, "
                "callback_state = 'busy', callback_token = ?, "
                "callback_busy_text = ?, updated_at = ? "
                "WHERE operation_id = ? AND owner_plugin_id = ? "
                "AND generation = ? AND callback_generation = ? "
                "AND message_id = ? AND state = 'open' AND role != 'legacy' "
                "AND delivery_state = 'delivered' "
                "AND EXISTS (SELECT 1 FROM operations operation "
                "WHERE operation.operation_id = operation_message_segments.operation_id "
                "AND operation.active_segment_id = operation_message_segments.segment_id "
                "AND operation.plugin_id = ? "
                "AND operation.state IN ('awaiting_input', 'running'))",
                (
                    normalized_token,
                    normalized_busy_text,
                    time.time(),
                    normalized_operation,
                    normalized_plugin,
                    normalized_segment_generation,
                    normalized_callback_generation,
                    normalized_message_id,
                    normalized_plugin,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM operation_message_segments "
                "WHERE operation_id = ? AND owner_plugin_id = ? "
                "AND generation = ?",
                (
                    normalized_operation,
                    normalized_plugin,
                    normalized_segment_generation,
                ),
            ).fetchone()
        return self._segment_from_row(row)

    def release_segment_callback(
        self,
        plugin_id: str,
        operation_id: str,
        *,
        message_id: int,
        segment_generation: int,
        callback_generation: int,
        callback_token: str,
    ) -> OperationMessageSegment | None:
        try:
            normalized_message_id = int(message_id)
            normalized_segment_generation = int(segment_generation)
            normalized_callback_generation = int(callback_generation)
        except (TypeError, ValueError):
            normalized_message_id = 0
            normalized_segment_generation = 0
            normalized_callback_generation = 0
        normalized_plugin = str(plugin_id or "").strip()
        normalized_operation = str(operation_id or "").strip()
        normalized_token = str(callback_token or "")
        if (
            not normalized_plugin
            or not normalized_operation
            or normalized_message_id <= 0
            or normalized_segment_generation <= 0
            or normalized_callback_generation <= 0
            or not normalized_token
            or len(normalized_token.encode("utf-8")) > 64
        ):
            raise InteractionError(
                "invalid_segment_callback",
                "segment callback release identity is invalid",
            )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE operation_message_segments SET "
                "callback_state = 'idle', callback_token = '', "
                "callback_busy_text = '', rendered_projection_hash = '', "
                "updated_at = ? "
                "WHERE operation_id = ? AND owner_plugin_id = ? "
                "AND generation = ? AND callback_generation = ? "
                "AND message_id = ? AND callback_state = 'busy' "
                "AND callback_token = ? AND state = 'open' "
                "AND EXISTS (SELECT 1 FROM operations operation "
                "WHERE operation.operation_id = "
                "operation_message_segments.operation_id "
                "AND operation.active_segment_id = "
                "operation_message_segments.segment_id "
                "AND operation.plugin_id = ?)",
                (
                    time.time(),
                    normalized_operation,
                    normalized_plugin,
                    normalized_segment_generation,
                    normalized_callback_generation,
                    normalized_message_id,
                    normalized_token,
                    normalized_plugin,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM operation_message_segments "
                "WHERE operation_id = ? AND owner_plugin_id = ? "
                "AND generation = ?",
                (
                    normalized_operation,
                    normalized_plugin,
                    normalized_segment_generation,
                ),
            ).fetchone()
        return self._segment_from_row(row)

    def get(self, operation_id: str) -> OperationRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (str(operation_id),),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_handoffs(self, operation_id: str) -> list[HandoffReceipt]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM operation_handoffs WHERE operation_id = ? "
                "ORDER BY source_revision, target_plugin_id",
                (str(operation_id),),
            ).fetchall()
        return [self._handoff_from_row(row) for row in rows]

    def get_effect_receipts(self, operation_id: str) -> list[EffectReceipt]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM operation_effect_receipts WHERE operation_id = ? "
                "ORDER BY created_at, effect_key",
                (str(operation_id),),
            ).fetchall()
        return [self._effect_from_row(row) for row in rows]

    def operation_snapshot(
        self,
        plugin_id: str,
        operation_id: str,
    ) -> dict:
        normalized_plugin = str(plugin_id or "").strip()
        normalized_operation = str(operation_id or "").strip()
        record = self.get(normalized_operation)
        if record is None:
            raise InteractionError("not_found", "operation was not found")
        handoffs = self.get_handoffs(normalized_operation)
        participants = {record.plugin_id}
        for handoff in handoffs:
            participants.add(handoff.source_plugin_id)
            participants.add(handoff.target_plugin_id)
        if normalized_plugin not in participants:
            raise InteractionError(
                "operation_forbidden",
                "Feature is not an operation participant",
            )
        segment = self.get_active_segment(normalized_operation)
        snapshot = {
            "operation_id": record.operation_id,
            "owner_plugin_id": record.plugin_id,
            "state": record.state,
            "stage": record.stage,
            "revision": record.revision,
            "active_segment": None,
            "latest_handoff": None,
        }
        if segment is not None:
            snapshot["active_segment"] = {
                "segment_id": segment.segment_id,
                "owner_plugin_id": segment.owner_plugin_id,
                "role": segment.role,
                "generation": segment.generation,
                "state": segment.state,
                "business_revision": segment.business_revision,
                "rendered_revision": segment.rendered_revision,
                "delivery_state": segment.delivery_state,
                "callback_generation": segment.callback_generation,
            }
        if handoffs:
            latest = handoffs[-1]
            snapshot["latest_handoff"] = {
                "handoff_key": latest.handoff_key,
                "source_plugin_id": latest.source_plugin_id,
                "target_plugin_id": latest.target_plugin_id,
                "state": latest.state,
                "event_id": latest.event_id,
            }
        return snapshot

    def capture_handoff(
        self,
        operation_id: str,
        source_plugin_id: str,
    ) -> HandoffReceipt | None:
        normalized_operation = str(operation_id)
        normalized_source = str(source_plugin_id)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                operation = self._connection.execute(
                    "SELECT * FROM operations WHERE operation_id = ? "
                    "AND plugin_id = ? AND state = 'handed_off' LIMIT 1",
                    (normalized_operation, normalized_source),
                ).fetchone()
                if operation is None or not str(operation["next_plugin_id"]):
                    self._connection.execute("COMMIT")
                    return None
                source_revision = int(operation["revision"])
                target_plugin_id = str(operation["next_plugin_id"])
                handoff_key = (
                    f"{normalized_operation}:{source_revision}:"
                    f"{target_plugin_id}"
                )
                now = time.time()
                self._connection.execute(
                    "INSERT OR IGNORE INTO operation_handoffs("
                    "handoff_key, operation_id, source_plugin_id, source_revision, "
                    "target_plugin_id, state, event_id, error_code, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, 'prepared', '', '', ?, ?)",
                    (
                        handoff_key,
                        normalized_operation,
                        normalized_source,
                        source_revision,
                        target_plugin_id,
                        now,
                        now,
                    ),
                )
                row = self._connection.execute(
                    "SELECT * FROM operation_handoffs WHERE handoff_key = ? "
                    "AND operation_id = ? AND source_plugin_id = ? "
                    "AND source_revision = ? AND target_plugin_id = ? "
                    "AND state IN ('prepared', 'submitted') LIMIT 1",
                    (
                        handoff_key,
                        normalized_operation,
                        normalized_source,
                        source_revision,
                        target_plugin_id,
                    ),
                ).fetchone()
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return self._handoff_from_row(row) if row is not None else None

    def record_handoff_event(
        self,
        operation_id: str,
        event_id: str,
        target_plugin_id: str,
        *,
        handoff_key: str | None = None,
    ) -> HandoffReceipt:
        normalized_operation = str(operation_id or "").strip()
        normalized_event = str(event_id or "").strip()
        normalized_target = str(target_plugin_id or "").strip()
        normalized_handoff = str(handoff_key or "").strip()
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", normalized_operation)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", normalized_event)
            or not normalized_target
            or len(normalized_target) > 120
        ):
            raise InteractionError(
                "invalid_handoff_event",
                "handoff event identity is invalid",
            )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if normalized_handoff:
                    row = self._connection.execute(
                        "SELECT * FROM operation_handoffs WHERE handoff_key = ? "
                        "AND operation_id = ? AND target_plugin_id = ?",
                        (
                            normalized_handoff,
                            normalized_operation,
                            normalized_target,
                        ),
                    ).fetchone()
                else:
                    operation_row = self._connection.execute(
                        "SELECT * FROM operations WHERE operation_id = ?",
                        (normalized_operation,),
                    ).fetchone()
                    if operation_row is None:
                        raise InteractionError("not_found", "operation was not found")
                    operation = self._from_row(operation_row)
                    if not (
                        (
                            operation.state == "handed_off"
                            and operation.next_plugin_id == normalized_target
                        )
                        or operation.plugin_id == normalized_target
                    ):
                        raise InteractionError(
                            "handoff_not_found",
                            "matching active handoff was not found",
                        )
                    row = self._connection.execute(
                        "SELECT * FROM operation_handoffs WHERE operation_id = ? "
                        "AND target_plugin_id = ? "
                        "AND state IN ('prepared', 'submitted', 'accepted') "
                        "ORDER BY source_revision DESC LIMIT 1",
                        (normalized_operation, normalized_target),
                    ).fetchone()
                if row is None:
                    raise InteractionError(
                        "handoff_not_found",
                        "matching active handoff was not found",
                    )
                collision = self._connection.execute(
                    "SELECT handoff_key FROM operation_handoffs "
                    "WHERE event_id = ? AND target_plugin_id = ? "
                    "AND handoff_key != ? LIMIT 1",
                    (
                        normalized_event,
                        normalized_target,
                        str(row["handoff_key"]),
                    ),
                ).fetchone()
                if collision is not None:
                    raise InteractionError(
                        "handoff_event_conflict",
                        "event delivery belongs to another handoff",
                    )
                existing_event = str(row["event_id"] or "")
                if existing_event and existing_event != normalized_event:
                    raise InteractionError(
                        "handoff_event_conflict",
                        "handoff already references another event",
                    )
                if (
                    existing_event == normalized_event
                    and str(row["state"]) in {"submitted", "accepted"}
                ):
                    self._connection.execute("COMMIT")
                    return self._handoff_from_row(row)
                now = time.time()
                self._connection.execute(
                    "UPDATE operation_handoffs SET "
                    "state = CASE WHEN state = 'prepared' THEN 'submitted' ELSE state END, "
                    "event_id = ?, updated_at = ? WHERE handoff_key = ?",
                    (normalized_event, now, str(row["handoff_key"])),
                )
                stored = self._connection.execute(
                    "SELECT * FROM operation_handoffs WHERE handoff_key = ?",
                    (str(row["handoff_key"]),),
                ).fetchone()
                self._connection.execute("COMMIT")
                return self._handoff_from_row(stored)
            except sqlite3.IntegrityError as exc:
                self._connection.execute("ROLLBACK")
                raise InteractionError(
                    "handoff_event_conflict",
                    "event delivery belongs to another handoff",
                ) from exc
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def fail_handoff_delivery(
        self,
        event_id: str,
        target_plugin_id: str,
        error_code: str,
    ) -> HandoffReceipt | None:
        normalized_event = str(event_id or "").strip()
        normalized_target = str(target_plugin_id or "").strip()
        normalized_error = str(error_code or "").strip()
        if (
            not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", normalized_event)
            or not normalized_target
            or len(normalized_target) > 120
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", normalized_error)
        ):
            raise InteractionError(
                "invalid_handoff_failure",
                "handoff delivery failure identity is invalid",
            )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM operation_handoffs WHERE event_id = ? "
                    "AND target_plugin_id = ? LIMIT 1",
                    (normalized_event, normalized_target),
                ).fetchone()
                if row is None:
                    self._connection.execute("COMMIT")
                    return None
                if str(row["state"]) in {"prepared", "submitted"}:
                    self._connection.execute(
                        "UPDATE operation_handoffs SET state = 'failed', "
                        "error_code = ?, updated_at = ? WHERE handoff_key = ?",
                        (
                            normalized_error,
                            time.time(),
                            str(row["handoff_key"]),
                        ),
                    )
                operation_row = self._connection.execute(
                    "SELECT * FROM operations WHERE operation_id = ?",
                    (str(row["operation_id"]),),
                ).fetchone()
                if operation_row is not None:
                    operation = self._from_row(operation_row)
                    if (
                        operation.state == "handed_off"
                        and operation.next_plugin_id == normalized_target
                    ):
                        details = dict(operation.details)
                        details.update({
                            "manual_check_required": True,
                            "handoff_event_id": normalized_event,
                            "handoff_target_plugin_id": normalized_target,
                            "handoff_error_code": normalized_error,
                        })
                        status_text = (
                            f"{operation.status_text}\n任务交接失败，需要手动检查。"
                        )[:4096]
                        self._connection.execute(
                            "UPDATE operations SET state = 'failed', stage = ?, "
                            "status_text = ?, control = '', revision = ?, "
                            "next_plugin_id = '', details_json = ?, updated_at = ? "
                            "WHERE operation_id = ?",
                            (
                                "handoff_failed",
                                status_text,
                                operation.revision + 1,
                                self._encode_details(details),
                                time.time(),
                                operation.operation_id,
                            ),
                        )
                stored = self._connection.execute(
                    "SELECT * FROM operation_handoffs WHERE handoff_key = ?",
                    (str(row["handoff_key"]),),
                ).fetchone()
                self._connection.execute("COMMIT")
                return self._handoff_from_row(stored)
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def enqueue_milestone(
        self,
        plugin_id: str,
        payload: dict,
    ) -> tuple[MilestoneIntent, bool]:
        values = self._validate_milestone_payload(plugin_id, payload)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                operation = self._connection.execute(
                    "SELECT * FROM operations WHERE operation_id = ?",
                    (values["operation_id"],),
                ).fetchone()
                if operation is None:
                    raise InteractionError("not_found", "operation was not found")
                existing = self._select_milestone_locked(
                    values["operation_id"], values["milestone_id"]
                )
                if existing is not None:
                    if (
                        str(existing["plugin_id"]) != values["plugin_id"]
                        or str(existing["mode"]) != values["mode"]
                        or str(existing["text"]) != values["text"]
                        or str(existing["photo_url"]) != values["photo_url"]
                    ):
                        raise InteractionError(
                            "milestone_conflict",
                            "operation milestone payload conflicts with its intent",
                        )
                    intent = self._milestone_from_row(existing)
                    self._connection.execute("COMMIT")
                    return intent, True
                if str(operation["plugin_id"]) != values["plugin_id"]:
                    raise InteractionError(
                        "owner_mismatch",
                        "operation belongs to another Feature",
                    )
                expected_message_id = operation["message_id"]
                expected_message_kind = (
                    str(operation["message_kind"] or "text")
                    if expected_message_id is not None
                    else ""
                )
                now = time.time()
                self._connection.execute(
                    "INSERT INTO operation_milestones("
                    "operation_id, milestone_id, plugin_id, delivered, "
                    "delivery_started, delivered_message_id, "
                    "delivered_message_kind, mode, text, photo_url, "
                    "delivery_state, attempt_count, last_error, "
                    "expected_message_id, expected_message_kind, "
                    "created_at, updated_at"
                    ") VALUES (?, ?, ?, 0, 0, NULL, '', ?, ?, ?, "
                    "'pending', 0, '', ?, ?, ?, ?)",
                    (
                        values["operation_id"],
                        values["milestone_id"],
                        values["plugin_id"],
                        values["mode"],
                        values["text"],
                        values["photo_url"],
                        expected_message_id,
                        expected_message_kind,
                        now,
                        now,
                    ),
                )
                stored = self._select_milestone_locked(
                    values["operation_id"], values["milestone_id"]
                )
                intent = self._milestone_from_row(stored)
                self._connection.execute("COMMIT")
                return intent, False
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def get_milestone(
        self,
        operation_id: str,
        milestone_id: str,
    ) -> MilestoneIntent | None:
        with self._lock:
            row = self._select_milestone_locked(
                str(operation_id), str(milestone_id)
            )
        return self._milestone_from_row(row) if row is not None else None

    def claim_milestone_delivery(
        self,
        operation_id: str,
        milestone_id: str,
    ) -> MilestoneIntent | None:
        normalized_operation = str(operation_id)
        normalized_milestone = str(milestone_id)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                now = time.time()
                cursor = self._connection.execute(
                    "UPDATE operation_milestones SET "
                    "delivery_state = 'delivering', delivery_started = 1, "
                    "attempt_count = attempt_count + 1, last_error = '', "
                    "updated_at = ? WHERE operation_id = ? AND milestone_id = ? "
                    "AND delivery_state IN ('pending', 'failed') "
                    "AND attempt_count < ?",
                    (
                        now,
                        normalized_operation,
                        normalized_milestone,
                        MILESTONE_MAX_ATTEMPTS,
                    ),
                )
                if cursor.rowcount != 1:
                    self._connection.execute("COMMIT")
                    return None
                stored = self._select_milestone_locked(
                    normalized_operation, normalized_milestone
                )
                intent = self._milestone_from_row(stored)
                self._connection.execute("COMMIT")
                return intent
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def reject_milestone_delivery(
        self,
        plugin_id: str,
        operation_id: str,
        milestone_id: str,
        error_code: str,
    ) -> MilestoneIntent:
        return self._transition_milestone_delivery(
            plugin_id,
            operation_id,
            milestone_id,
            source_state="delivering",
            target_state="failed",
            error_code=error_code,
        )

    def mark_milestone_delivery_unknown(
        self,
        plugin_id: str,
        operation_id: str,
        milestone_id: str,
        error_code: str,
    ) -> MilestoneIntent:
        return self._transition_milestone_delivery(
            plugin_id,
            operation_id,
            milestone_id,
            source_state="delivering",
            target_state="unknown",
            error_code=error_code,
        )

    def _transition_milestone_delivery(
        self,
        plugin_id: str,
        operation_id: str,
        milestone_id: str,
        *,
        source_state: str,
        target_state: str,
        error_code: str,
    ) -> MilestoneIntent:
        normalized_error = self._milestone_error_code(error_code)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._select_milestone_locked(
                    str(operation_id), str(milestone_id)
                )
                if row is None:
                    raise InteractionError(
                        "milestone_not_found",
                        "operation milestone was not found",
                    )
                if str(row["plugin_id"]) != str(plugin_id):
                    raise InteractionError(
                        "owner_mismatch",
                        "operation milestone belongs to another Feature",
                    )
                current_state = str(row["delivery_state"])
                if current_state == target_state:
                    intent = self._milestone_from_row(row)
                    self._connection.execute("COMMIT")
                    return intent
                if current_state != source_state:
                    raise InteractionError(
                        "milestone_state_conflict",
                        "operation milestone delivery state has changed",
                    )
                self._connection.execute(
                    "UPDATE operation_milestones SET delivery_state = ?, "
                    "last_error = ?, updated_at = ? WHERE operation_id = ? "
                    "AND milestone_id = ?",
                    (
                        target_state,
                        normalized_error,
                        time.time(),
                        str(operation_id),
                        str(milestone_id),
                    ),
                )
                stored = self._select_milestone_locked(
                    str(operation_id), str(milestone_id)
                )
                intent = self._milestone_from_row(stored)
                self._connection.execute("COMMIT")
                return intent
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def record_milestone_delivery_target(
        self,
        plugin_id: str,
        operation_id: str,
        milestone_id: str,
        message_id: int,
        message_kind: str,
    ) -> MilestoneIntent:
        try:
            normalized_message_id = int(message_id)
        except (TypeError, ValueError):
            normalized_message_id = 0
        normalized_kind = str(message_kind or "").strip().casefold()
        if normalized_message_id <= 0 or normalized_kind not in {"text", "photo"}:
            raise InteractionError(
                "invalid_message", "milestone delivery target is invalid"
            )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._select_milestone_locked(
                    str(operation_id), str(milestone_id)
                )
                if row is None:
                    raise InteractionError(
                        "milestone_not_found",
                        "operation milestone was not found",
                    )
                if str(row["plugin_id"]) != str(plugin_id):
                    raise InteractionError(
                        "owner_mismatch",
                        "operation milestone belongs to another Feature",
                    )
                existing_id = row["delivered_message_id"]
                existing_kind = str(row["delivered_message_kind"] or "")
                if existing_id is not None:
                    if (
                        int(existing_id) != normalized_message_id
                        or existing_kind != normalized_kind
                    ):
                        raise InteractionError(
                            "milestone_conflict",
                            "operation milestone delivery target conflicts",
                        )
                    intent = self._milestone_from_row(row)
                    self._connection.execute("COMMIT")
                    return intent
                if str(row["delivery_state"]) != "delivering":
                    raise InteractionError(
                        "milestone_state_conflict",
                        "operation milestone is not being delivered",
                    )
                self._connection.execute(
                    "UPDATE operation_milestones SET delivered_message_id = ?, "
                    "delivered_message_kind = ?, updated_at = ? "
                    "WHERE operation_id = ? AND milestone_id = ?",
                    (
                        normalized_message_id,
                        normalized_kind,
                        time.time(),
                        str(operation_id),
                        str(milestone_id),
                    ),
                )
                stored = self._select_milestone_locked(
                    str(operation_id), str(milestone_id)
                )
                intent = self._milestone_from_row(stored)
                self._connection.execute("COMMIT")
                return intent
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def complete_milestone_delivery(
        self,
        plugin_id: str,
        operation_id: str,
        milestone_id: str,
    ) -> OperationRecord:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                milestone = self._select_milestone_locked(
                    str(operation_id), str(milestone_id)
                )
                if milestone is None:
                    raise InteractionError(
                        "milestone_not_found",
                        "operation milestone was not found",
                    )
                if str(milestone["plugin_id"]) != str(plugin_id):
                    raise InteractionError(
                        "owner_mismatch",
                        "operation milestone belongs to another Feature",
                    )
                if str(milestone["delivery_state"]) != "delivered":
                    if str(milestone["delivery_state"]) != "delivering":
                        raise InteractionError(
                            "milestone_state_conflict",
                            "operation milestone is not being delivered",
                        )
                    self._complete_milestone_locked(milestone, time.time())
                row = self._connection.execute(
                    "SELECT * FROM operations WHERE operation_id = ?",
                    (str(operation_id),),
                ).fetchone()
                operation = self._from_row(row)
                self._connection.execute("COMMIT")
                return operation
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def recover_milestones(self) -> list[MilestoneIntent]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                delivering = self._connection.execute(
                    "SELECT m.*, o.chat_id AS operation_chat_id, "
                    "o.user_id AS operation_user_id "
                    "FROM operation_milestones AS m "
                    "JOIN operations AS o ON o.operation_id = m.operation_id "
                    "WHERE m.delivery_state = 'delivering' "
                    "ORDER BY m.created_at, m.operation_id, m.milestone_id"
                ).fetchall()
                now = time.time()
                for milestone in delivering:
                    if milestone["delivered_message_id"] is not None:
                        self._complete_milestone_locked(milestone, now)
                    else:
                        self._connection.execute(
                            "UPDATE operation_milestones SET "
                            "delivery_state = 'unknown', "
                            "last_error = 'restart_uncertain', updated_at = ? "
                            "WHERE operation_id = ? AND milestone_id = ?",
                            (
                                now,
                                str(milestone["operation_id"]),
                                str(milestone["milestone_id"]),
                            ),
                        )
                rows = self._connection.execute(
                    "SELECT m.*, o.chat_id AS operation_chat_id, "
                    "o.user_id AS operation_user_id "
                    "FROM operation_milestones AS m "
                    "JOIN operations AS o ON o.operation_id = m.operation_id "
                    "WHERE m.delivery_state IN ('pending', 'failed') "
                    "AND m.attempt_count < ? "
                    "ORDER BY m.created_at, m.operation_id, m.milestone_id",
                    (MILESTONE_MAX_ATTEMPTS,),
                ).fetchall()
                recoverable = [
                    self._milestone_from_row(row) for row in rows
                ]
                self._connection.execute("COMMIT")
                return recoverable
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def milestone_delivery_record(
        self,
        intent: MilestoneIntent,
    ) -> OperationRecord:
        current = self.get(intent.operation_id)
        if current is None:
            raise InteractionError("not_found", "operation was not found")
        return OperationRecord(
            operation_id=current.operation_id,
            chat_id=intent.chat_id,
            user_id=intent.user_id,
            plugin_id=intent.plugin_id,
            state=current.state,
            stage=current.stage,
            status_text=current.status_text,
            control=current.control,
            revision=current.revision,
            message_id=intent.expected_message_id,
            message_kind=(
                intent.expected_message_kind
                if intent.expected_message_id is not None
                else ""
            ),
            active_segment_id=current.active_segment_id,
            next_plugin_id=current.next_plugin_id,
            details=current.details,
            created_at=current.created_at,
            updated_at=current.updated_at,
        )

    def claim_milestone(
        self,
        plugin_id: str,
        operation_id: str,
        milestone_id: str,
    ) -> OperationRecord | None:
        normalized_plugin = str(plugin_id or "").strip()
        normalized_operation = str(operation_id or "").strip()
        normalized_milestone = str(milestone_id or "").strip()
        if (
            not normalized_plugin
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", normalized_operation)
            or not re.fullmatch(
                r"[A-Za-z0-9_.:-]{1,120}",
                normalized_milestone,
            )
        ):
            raise InteractionError(
                "invalid_milestone",
                "operation milestone identity is invalid",
            )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM operations WHERE operation_id = ?",
                    (normalized_operation,),
                ).fetchone()
                if row is None:
                    raise InteractionError(
                        "not_found",
                        "operation was not found",
                    )
                record = self._from_row(row)
                if record.plugin_id != normalized_plugin:
                    raise InteractionError(
                        "owner_mismatch",
                        "operation belongs to another Feature",
                    )
                now = time.time()
                cursor = self._connection.execute(
                    "INSERT OR IGNORE INTO operation_milestones("
                    "operation_id, milestone_id, plugin_id, delivered, "
                    "delivery_started, mode, text, photo_url, delivery_state, "
                    "attempt_count, expected_message_id, expected_message_kind, "
                    "created_at, updated_at"
                    ") VALUES (?, ?, ?, 0, 0, 'identity', ?, '', 'pending', "
                    "0, ?, ?, ?, ?)",
                    (
                        normalized_operation,
                        normalized_milestone,
                        normalized_plugin,
                        normalized_milestone,
                        row["message_id"],
                        (
                            str(row["message_kind"] or "text")
                            if row["message_id"] is not None else ""
                        ),
                        now,
                        now,
                    ),
                )
                if cursor.rowcount != 1:
                    milestone = self._connection.execute(
                        "SELECT plugin_id, delivered, delivery_state "
                        "FROM operation_milestones "
                        "WHERE operation_id = ? AND milestone_id = ?",
                        (normalized_operation, normalized_milestone),
                    ).fetchone()
                    if (
                        milestone is None
                        or str(milestone["plugin_id"]) != normalized_plugin
                    ):
                        raise InteractionError(
                            "owner_mismatch",
                            "operation milestone belongs to another Feature",
                        )
                    if (
                        int(milestone["delivered"]) == 1
                        or str(milestone["delivery_state"]) == "delivered"
                    ):
                        self._connection.execute("COMMIT")
                        return None
                self._connection.execute("COMMIT")
                return record
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def complete_milestone(
        self,
        plugin_id: str,
        operation_id: str,
        milestone_id: str,
    ) -> OperationRecord:
        normalized_plugin = str(plugin_id or "").strip()
        normalized_operation = str(operation_id or "").strip()
        normalized_milestone = str(milestone_id or "").strip()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                milestone = self._select_milestone_locked(
                    normalized_operation, normalized_milestone
                )
                if milestone is None:
                    raise InteractionError(
                        "milestone_not_found",
                        "operation milestone was not found",
                    )
                if str(milestone["plugin_id"]) != normalized_plugin:
                    raise InteractionError(
                        "owner_mismatch",
                        "operation milestone belongs to another Feature",
                    )
                if str(milestone["delivery_state"]) != "delivered":
                    if str(milestone["delivery_state"]) != "delivering":
                        self._connection.execute(
                            "UPDATE operation_milestones SET "
                            "delivery_state = 'delivering', delivery_started = 1, "
                            "updated_at = ? WHERE operation_id = ? "
                            "AND milestone_id = ?",
                            (
                                time.time(),
                                normalized_operation,
                                normalized_milestone,
                            ),
                        )
                        milestone = self._select_milestone_locked(
                            normalized_operation, normalized_milestone
                        )
                    self._complete_milestone_locked(milestone, time.time())
                row = self._connection.execute(
                    "SELECT * FROM operations WHERE operation_id = ?",
                    (normalized_operation,),
                ).fetchone()
                operation = self._from_row(row)
                self._connection.execute("COMMIT")
                return operation
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def milestone_delivery_target(
        self,
        plugin_id: str,
        operation_id: str,
        milestone_id: str,
    ) -> tuple[int, str] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT plugin_id, delivered_message_id, "
                "delivered_message_kind FROM operation_milestones "
                "WHERE operation_id = ? AND milestone_id = ?",
                (str(operation_id), str(milestone_id)),
            ).fetchone()
        if row is None:
            return None
        if str(row["plugin_id"]) != str(plugin_id):
            raise InteractionError(
                "owner_mismatch",
                "operation milestone belongs to another Feature",
            )
        message_id = row["delivered_message_id"]
        if message_id is None:
            return None
        return int(message_id), str(row["delivered_message_kind"] or "text")

    def milestone_delivery_started(
        self,
        plugin_id: str,
        operation_id: str,
        milestone_id: str,
    ) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT plugin_id, delivery_started FROM operation_milestones "
                "WHERE operation_id = ? AND milestone_id = ?",
                (str(operation_id), str(milestone_id)),
            ).fetchone()
        if row is None:
            return False
        if str(row["plugin_id"]) != str(plugin_id):
            raise InteractionError(
                "owner_mismatch",
                "operation milestone belongs to another Feature",
            )
        return int(row["delivery_started"]) == 1

    def begin_milestone_delivery(
        self,
        plugin_id: str,
        operation_id: str,
        milestone_id: str,
    ) -> None:
        claimed = self.claim_milestone_delivery(operation_id, milestone_id)
        if claimed is not None:
            if claimed.plugin_id != str(plugin_id):
                raise InteractionError(
                    "owner_mismatch",
                    "operation milestone belongs to another Feature",
                )
            return
        current = self.get_milestone(operation_id, milestone_id)
        if (
            current is None
            or current.plugin_id != str(plugin_id)
            or current.delivery_state != "delivering"
        ):
            raise InteractionError(
                "milestone_not_found",
                "undelivered operation milestone was not found",
            )

    def record_milestone_delivery(
        self,
        plugin_id: str,
        operation_id: str,
        milestone_id: str,
        message_id: int,
        message_kind: str,
    ) -> OperationRecord:
        current = self.get_milestone(operation_id, milestone_id)
        if current is None:
            raise InteractionError(
                "milestone_not_found",
                "operation milestone was not found",
            )
        if current.delivery_state in {"pending", "failed"}:
            claimed = self.claim_milestone_delivery(operation_id, milestone_id)
            if claimed is None:
                raise InteractionError(
                    "milestone_state_conflict",
                    "operation milestone delivery state has changed",
                )
        self.record_milestone_delivery_target(
            plugin_id,
            operation_id,
            milestone_id,
            message_id,
            message_kind,
        )
        record = self.get(operation_id)
        if record is None:
            raise InteractionError("not_found", "operation was not found")
        return record

    def release_milestone(
        self,
        plugin_id: str,
        operation_id: str,
        milestone_id: str,
    ) -> None:
        normalized_plugin = str(plugin_id or "").strip()
        normalized_operation = str(operation_id or "").strip()
        normalized_milestone = str(milestone_id or "").strip()
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE operation_milestones SET delivery_state = 'failed', "
                "last_error = 'explicit_rejection', updated_at = ? "
                "WHERE operation_id = ? AND milestone_id = ? AND plugin_id = ? "
                "AND delivery_state IN ('pending', 'delivering', 'failed')",
                (
                    time.time(),
                    normalized_operation,
                    normalized_milestone,
                    normalized_plugin,
                ),
            )

    def active(self, chat_id: int, user_id: int) -> OperationRecord | None:
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        with self._lock:
            row = self._connection.execute(
                f"SELECT * FROM operations WHERE chat_id = ? AND user_id = ? "
                f"AND state IN ({placeholders}) ORDER BY updated_at DESC LIMIT 1",
                (int(chat_id), int(user_id), *sorted(ACTIVE_STATES)),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def active_records(self) -> list[OperationRecord]:
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM operations WHERE state IN ({placeholders}) "
                "ORDER BY created_at, operation_id",
                tuple(sorted(ACTIVE_STATES)),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def set_message_id(
        self,
        operation_id: str,
        message_id: int,
        message_kind: str | None = None,
    ) -> OperationRecord:
        try:
            normalized = int(message_id)
        except (TypeError, ValueError):
            normalized = 0
        if normalized <= 0:
            raise InteractionError("invalid_message", "message ID must be positive")
        normalized_kind = str(message_kind or "").strip().casefold()
        if normalized_kind and normalized_kind not in {"text", "photo"}:
            raise InteractionError(
                "invalid_message", "message kind must be text or photo"
            )
        with self._lock, self._connection:
            if normalized_kind:
                cursor = self._connection.execute(
                    "UPDATE operations SET message_id = ?, message_kind = ?, "
                    "updated_at = ? WHERE operation_id = ?",
                    (
                        normalized,
                        normalized_kind,
                        time.time(),
                        str(operation_id),
                    ),
                )
            else:
                cursor = self._connection.execute(
                    "UPDATE operations SET message_id = ?, updated_at = ? "
                    "WHERE operation_id = ?",
                    (normalized, time.time(), str(operation_id)),
                )
            if cursor.rowcount != 1:
                raise InteractionError("not_found", "operation was not found")
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (str(operation_id),),
            ).fetchone()
        return self._from_row(row)

    def set_message_id_if_current(
        self,
        operation_id: str,
        plugin_id: str,
        revision: int,
        message_id: int,
        message_kind: str,
    ) -> OperationRecord | None:
        try:
            normalized_message_id = int(message_id)
            normalized_revision = int(revision)
        except (TypeError, ValueError):
            normalized_message_id = normalized_revision = 0
        normalized_kind = str(message_kind or "").strip().casefold()
        if (
            normalized_message_id <= 0
            or normalized_revision <= 0
            or normalized_kind not in {"text", "photo"}
        ):
            raise InteractionError("invalid_message", "message cursor is invalid")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE operations SET message_id = ?, message_kind = ?, "
                "updated_at = ? WHERE operation_id = ? AND plugin_id = ? "
                "AND revision = ?",
                (
                    normalized_message_id,
                    normalized_kind,
                    time.time(),
                    str(operation_id),
                    str(plugin_id),
                    normalized_revision,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (str(operation_id),),
            ).fetchone()
        return self._from_row(row)

    def clear_message_id(self, operation_id: str) -> OperationRecord:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE operations SET message_id = NULL, message_kind = '', "
                "updated_at = ? WHERE operation_id = ?",
                (time.time(), str(operation_id)),
            )
            if cursor.rowcount != 1:
                raise InteractionError("not_found", "operation was not found")
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (str(operation_id),),
            ).fetchone()
        return self._from_row(row)

    def interrupt_unowned(
        self, active_plugin_ids: set[str]
    ) -> list[OperationRecord]:
        active_plugins = {str(value) for value in active_plugin_ids}
        return self._interrupt_matching(
            lambda record: record.plugin_id not in active_plugins
        )

    def interrupt_unconfirmed(
        self,
        confirmed_operation_ids: set[str],
        expected: dict[str, tuple[str, int]] | None = None,
    ) -> list[OperationRecord]:
        confirmed = {str(value) for value in confirmed_operation_ids}
        baseline = {
            str(operation_id): (str(owner), int(revision))
            for operation_id, (owner, revision) in (expected or {}).items()
        }
        return self._interrupt_matching(
            lambda record: (
                record.operation_id not in confirmed
                and (
                    expected is None
                    or baseline.get(record.operation_id)
                    == (record.plugin_id, record.revision)
                )
            )
        )

    def _interrupt_matching(self, predicate) -> list[OperationRecord]:
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        interrupted: list[OperationRecord] = []
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                rows = self._connection.execute(
                    f"SELECT * FROM operations WHERE state IN ({placeholders}) "
                    "ORDER BY created_at, operation_id",
                    tuple(sorted(ACTIVE_STATES)),
                ).fetchall()
                for row in rows:
                    current = self._from_row(row)
                    if not predicate(current):
                        continue
                    details = dict(current.details)
                    details["interrupted_at_stage"] = current.stage
                    details["manual_check_required"] = True
                    status_text = (
                        f"{current.status_text}\n执行器未恢复，任务已中断。"
                    )[:4096]
                    now = time.time()
                    self._connection.execute(
                        "UPDATE operations SET state = 'interrupted', status_text = ?, "
                        "control = '', revision = ?, next_plugin_id = '', "
                        "details_json = ?, updated_at = ? WHERE operation_id = ?",
                        (
                            status_text,
                            current.revision + 1,
                            self._encode_details(details),
                            now,
                            current.operation_id,
                        ),
                    )
                    stored = self._connection.execute(
                        "SELECT * FROM operations WHERE operation_id = ?",
                        (current.operation_id,),
                    ).fetchone()
                    interrupted.append(self._from_row(stored))
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return interrupted

    def _reject_active_conflict(self, values: dict):
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        row = self._connection.execute(
            f"SELECT operation_id FROM operations WHERE chat_id = ? AND user_id = ? "
            f"AND state IN ({placeholders}) LIMIT 1",
            (values["chat_id"], values["user_id"], *sorted(ACTIVE_STATES)),
        ).fetchone()
        if row is not None:
            raise InteractionError(
                "operation_conflict",
                f"operation {row['operation_id']} already owns this user",
            )

    @staticmethod
    def _validate_existing_owner(current: OperationRecord, values: dict):
        if current.chat_id != values["chat_id"] or current.user_id != values["user_id"]:
            raise InteractionError(
                "owner_mismatch", "operation chat or user cannot be changed"
            )
        if current.plugin_id == values["plugin_id"]:
            if current.state == "handed_off" and values["revision"] > current.revision:
                if values["state"] in TERMINAL_STATES | {
                    "cancelling", "rolling_back"
                }:
                    return
                raise InteractionError(
                    "handoff_pending", "only the declared Feature may accept this handoff"
                )
            return
        if (
            current.state != "handed_off"
            or not current.next_plugin_id
            or current.next_plugin_id != values["plugin_id"]
        ):
            raise InteractionError(
                "owner_mismatch", "Feature does not own this operation"
            )

    @classmethod
    def _validate_report(cls, plugin_id: str, report: dict) -> dict:
        if not isinstance(report, dict):
            raise InteractionError("invalid_report", "operation report must be an object")
        plugin_id = str(plugin_id or "").strip()
        operation_id = str(report.get("operation_id") or "").strip()
        if not plugin_id or not operation_id or len(operation_id) > 40:
            raise InteractionError("invalid_operation", "operation identity is invalid")
        try:
            chat_id = int(report.get("chat_id"))
            user_id = int(report.get("user_id"))
        except (TypeError, ValueError):
            chat_id = user_id = 0
        if chat_id == 0 or user_id <= 0:
            raise InteractionError("invalid_owner", "operation owner is invalid")
        state = str(report.get("state") or "")
        if state not in VALID_STATES:
            raise InteractionError("invalid_state", "operation state is invalid")
        control = str(report.get("control") or "")
        if control not in VALID_CONTROLS:
            raise InteractionError("invalid_control", "operation control is invalid")
        try:
            revision = int(report.get("revision"))
        except (TypeError, ValueError):
            revision = 0
        if revision <= 0:
            raise InteractionError("invalid_revision", "operation revision must be positive")
        next_plugin_id = str(report.get("next_plugin_id") or "").strip()
        if state == "handed_off" and (
            not next_plugin_id or next_plugin_id == plugin_id
        ):
            raise InteractionError(
                "invalid_handoff", "handoff requires a different target Feature"
            )
        if state != "handed_off":
            next_plugin_id = ""
        details = report.get("details", {})
        return {
            "operation_id": operation_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "plugin_id": plugin_id,
            "state": state,
            "stage": str(report.get("stage") or "")[:256],
            "status_text": str(report.get("status_text") or "")[:4096],
            "control": control,
            "revision": revision,
            "next_plugin_id": next_plugin_id,
            "details_json": cls._encode_details(details),
            "effect_receipt": cls._validate_effect_receipt(details),
        }

    @classmethod
    def _validate_effect_receipt(cls, details) -> dict | None:
        if not isinstance(details, dict):
            raise InteractionError("invalid_details", "operation details must be an object")
        effect = details.get("effect_receipt")
        if effect is None:
            return None
        if not isinstance(effect, dict):
            raise InteractionError(
                "invalid_effect_receipt",
                "effect receipt must be an object",
            )
        effect_key = str(effect.get("effect_key") or "").strip()
        state = str(effect.get("state") or "").strip()
        receipt = effect.get("receipt")
        if (
            not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", effect_key)
            or state not in EFFECT_RECEIPT_STATES
            or not isinstance(receipt, dict)
        ):
            raise InteractionError(
                "invalid_effect_receipt",
                "effect receipt identity, state, or payload is invalid",
            )
        return {
            "effect_key": effect_key,
            "state": state,
            "receipt_json": cls._encode_details(receipt),
        }

    def _apply_receipt_transitions(
        self,
        previous: OperationRecord | None,
        values: dict,
    ) -> None:
        operation_id = values["operation_id"]
        now = time.time()
        owner_changed = (
            previous is not None
            and previous.plugin_id != values["plugin_id"]
        )
        if owner_changed:
            accepted = self._connection.execute(
                "SELECT handoff_key FROM operation_handoffs "
                "WHERE operation_id = ? AND source_plugin_id = ? "
                "AND target_plugin_id = ? "
                "AND state IN ('prepared', 'submitted') "
                "ORDER BY source_revision DESC LIMIT 1",
                (
                    operation_id,
                    previous.plugin_id,
                    values["plugin_id"],
                ),
            ).fetchone()
            if accepted is None:
                legacy_handoff_key = (
                    f"{operation_id}:{previous.revision}:{values['plugin_id']}"
                )
                self._connection.execute(
                    "INSERT INTO operation_handoffs("
                    "handoff_key, operation_id, source_plugin_id, source_revision, "
                    "target_plugin_id, state, event_id, error_code, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, 'prepared', '', '', ?, ?)",
                    (
                        legacy_handoff_key,
                        operation_id,
                        previous.plugin_id,
                        previous.revision,
                        values["plugin_id"],
                        previous.updated_at,
                        now,
                    ),
                )
                accepted = {"handoff_key": legacy_handoff_key}
            self._connection.execute(
                "UPDATE operation_handoffs SET state = 'accepted', updated_at = ? "
                "WHERE handoff_key = ?",
                (now, str(accepted["handoff_key"])),
            )

        if values["state"] == "handed_off":
            handoff_key = (
                f"{operation_id}:{values['revision']}:{values['next_plugin_id']}"
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO operation_handoffs("
                "handoff_key, operation_id, source_plugin_id, source_revision, "
                "target_plugin_id, state, event_id, error_code, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, 'prepared', '', '', ?, ?)",
                (
                    handoff_key,
                    operation_id,
                    values["plugin_id"],
                    values["revision"],
                    values["next_plugin_id"],
                    now,
                    now,
                ),
            )

        if values["state"] in TERMINAL_STATES | {"cancelling", "rolling_back"}:
            self._connection.execute(
                "UPDATE operation_handoffs SET state = 'cancelled', updated_at = ? "
                "WHERE operation_id = ? AND state IN ('prepared', 'submitted')",
                (now, operation_id),
            )

        effect = values.get("effect_receipt")
        if effect is None:
            return
        existing = self._connection.execute(
            "SELECT operation_id, plugin_id, state, receipt_json "
            "FROM operation_effect_receipts WHERE effect_key = ?",
            (effect["effect_key"],),
        ).fetchone()
        if existing is not None:
            existing_state = str(existing["state"])
            if (
                str(existing["operation_id"]) != operation_id
                or str(existing["plugin_id"]) != values["plugin_id"]
                or str(existing["receipt_json"]) != effect["receipt_json"]
            ):
                raise InteractionError(
                    "effect_conflict",
                    "effect key owner or payload conflicts with its receipt",
                )
            if existing_state == effect["state"]:
                return
            if effect["state"] not in EFFECT_RECEIPT_TRANSITIONS[existing_state]:
                raise InteractionError(
                    "effect_conflict",
                    "effect receipt state transition is not monotonic",
                )
            self._connection.execute(
                "UPDATE operation_effect_receipts SET state = ?, updated_at = ? "
                "WHERE effect_key = ?",
                (effect["state"], now, effect["effect_key"]),
            )
            return
        self._connection.execute(
            "INSERT INTO operation_effect_receipts("
            "effect_key, operation_id, plugin_id, state, receipt_json, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                effect["effect_key"],
                operation_id,
                values["plugin_id"],
                effect["state"],
                effect["receipt_json"],
                now,
                now,
            ),
        )

    def _select_milestone_locked(
        self,
        operation_id: str,
        milestone_id: str,
    ) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT m.*, o.chat_id AS operation_chat_id, "
            "o.user_id AS operation_user_id "
            "FROM operation_milestones AS m "
            "JOIN operations AS o ON o.operation_id = m.operation_id "
            "WHERE m.operation_id = ? AND m.milestone_id = ?",
            (str(operation_id), str(milestone_id)),
        ).fetchone()

    def _complete_milestone_locked(
        self,
        milestone: sqlite3.Row,
        now: float,
    ) -> None:
        operation_id = str(milestone["operation_id"])
        milestone_id = str(milestone["milestone_id"])
        plugin_id = str(milestone["plugin_id"])
        self._connection.execute(
            "UPDATE operation_milestones SET delivery_state = 'delivered', "
            "delivered = 1, delivery_started = 1, last_error = '', "
            "updated_at = ? WHERE operation_id = ? AND milestone_id = ?",
            (now, operation_id, milestone_id),
        )
        expected_message_id = milestone["expected_message_id"]
        if expected_message_id is None:
            self._connection.execute(
                "UPDATE operations SET message_id = NULL, message_kind = '', "
                "updated_at = ? WHERE operation_id = ? AND plugin_id = ? "
                "AND message_id IS NULL",
                (now, operation_id, plugin_id),
            )
        else:
            self._connection.execute(
                "UPDATE operations SET message_id = NULL, message_kind = '', "
                "updated_at = ? WHERE operation_id = ? AND plugin_id = ? "
                "AND message_id = ? AND message_kind = ?",
                (
                    now,
                    operation_id,
                    plugin_id,
                    int(expected_message_id),
                    str(milestone["expected_message_kind"] or ""),
                ),
            )

    @staticmethod
    def _validate_milestone_payload(plugin_id: str, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise InteractionError(
                "invalid_milestone", "operation milestone payload is invalid"
            )
        normalized_plugin = str(plugin_id or "").strip()
        operation_id = str(payload.get("operation_id") or "").strip()
        milestone_id = str(payload.get("milestone_id") or "").strip()
        mode = str(payload.get("mode") or "identity").strip().casefold()
        text = str(payload.get("text") or "")
        photo_url = str(payload.get("photo_url") or "").strip()
        text_limit = 1024 if photo_url else 4096
        if (
            not normalized_plugin
            or len(normalized_plugin) > 120
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", operation_id)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", milestone_id)
            or mode not in {"identity", "stage"}
            or not text
            or len(text) > text_limit
            or len(photo_url) > 2048
            or (photo_url and not photo_url.startswith("https://"))
            or (mode == "stage" and photo_url)
        ):
            raise InteractionError(
                "invalid_milestone", "operation milestone payload is invalid"
            )
        return {
            "plugin_id": normalized_plugin,
            "operation_id": operation_id,
            "milestone_id": milestone_id,
            "mode": mode,
            "text": text,
            "photo_url": photo_url,
        }

    @staticmethod
    def _milestone_error_code(value: str) -> str:
        normalized = str(value or "milestone_delivery_error").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", normalized):
            return type(value).__name__[:120] or "milestone_delivery_error"
        return normalized

    @staticmethod
    def _encode_details(details) -> str:
        if not isinstance(details, dict):
            raise InteractionError("invalid_details", "operation details must be an object")
        try:
            encoded = json.dumps(
                InteractionCoordinator._sanitize_detail(details),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise InteractionError(
                "invalid_details", "operation details must be JSON-compatible"
            ) from None
        if len(encoded.encode("utf-8")) > 16384:
            raise InteractionError("invalid_details", "operation details are too large")
        return encoded

    @staticmethod
    def _sanitize_detail(value, key: str = ""):
        normalized_key = str(key).lower().replace("-", "_")
        if any(part in normalized_key for part in (
            "access_token",
            "refresh_token",
            "api_key",
            "password",
            "secret",
            "cookie",
            "authorization",
        )) or normalized_key == "token":
            return "***redacted***"
        if isinstance(value, dict):
            return {
                str(child_key): InteractionCoordinator._sanitize_detail(
                    child_value, str(child_key)
                )
                for child_key, child_value in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [InteractionCoordinator._sanitize_detail(item) for item in value]
        if isinstance(value, str):
            value = re.sub(
                r"magnet:\?[^\s\"'`]+",
                "magnet:?***redacted***",
                value,
                flags=re.IGNORECASE,
            )
            return re.sub(
                r"(?i)(access_token|refresh_token|api[_-]?key|token|secret|password|cookie)"
                r"\s*([=:])\s*([^&\s]+)",
                r"\1\2***redacted***",
                value,
            )
        return value

    @staticmethod
    def _from_row(row: sqlite3.Row) -> OperationRecord:
        return OperationRecord(
            operation_id=str(row["operation_id"]),
            chat_id=int(row["chat_id"]),
            user_id=int(row["user_id"]),
            plugin_id=str(row["plugin_id"]),
            state=str(row["state"]),
            stage=str(row["stage"]),
            status_text=str(row["status_text"]),
            control=str(row["control"]),
            revision=int(row["revision"]),
            message_id=(int(row["message_id"]) if row["message_id"] is not None else None),
            message_kind=str(row["message_kind"] or "text"),
            active_segment_id=str(row["active_segment_id"] or ""),
            next_plugin_id=str(row["next_plugin_id"]),
            details=MappingProxyType(json.loads(str(row["details_json"]))),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _segment_from_row(row: sqlite3.Row) -> OperationMessageSegment:
        return OperationMessageSegment(
            segment_id=str(row["segment_id"]),
            operation_id=str(row["operation_id"]),
            sequence=int(row["sequence"]),
            owner_plugin_id=str(row["owner_plugin_id"]),
            role=str(row["role"]),
            generation=int(row["generation"]),
            presentation_kind=str(row["presentation_kind"]),
            state=str(row["state"]),
            message_id=(
                int(row["message_id"])
                if row["message_id"] is not None
                else None
            ),
            message_kind=str(row["message_kind"] or ""),
            business_revision=int(row["business_revision"]),
            rendered_revision=int(row["rendered_revision"]),
            projection_hash=str(row["projection_hash"]),
            rendered_projection_hash=str(
                row["rendered_projection_hash"] or ""
            ),
            projection=freeze_projection(json.loads(str(row["projection_json"]))),
            callback_generation=int(row["callback_generation"]),
            callback_state=str(row["callback_state"] or "idle"),
            callback_token=str(row["callback_token"] or ""),
            callback_busy_text=str(row["callback_busy_text"] or ""),
            delivery_state=str(row["delivery_state"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            sealed_at=(
                float(row["sealed_at"])
                if row["sealed_at"] is not None
                else None
            ),
        )

    @staticmethod
    def _handoff_from_row(row: sqlite3.Row) -> HandoffReceipt:
        return HandoffReceipt(
            handoff_key=str(row["handoff_key"]),
            operation_id=str(row["operation_id"]),
            source_plugin_id=str(row["source_plugin_id"]),
            source_revision=int(row["source_revision"]),
            target_plugin_id=str(row["target_plugin_id"]),
            state=str(row["state"]),
            event_id=str(row["event_id"] or ""),
            error_code=str(row["error_code"] or ""),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _effect_from_row(row: sqlite3.Row) -> EffectReceipt:
        return EffectReceipt(
            effect_key=str(row["effect_key"]),
            operation_id=str(row["operation_id"]),
            plugin_id=str(row["plugin_id"]),
            state=str(row["state"]),
            receipt=MappingProxyType(json.loads(str(row["receipt_json"]))),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _milestone_from_row(row: sqlite3.Row) -> MilestoneIntent:
        return MilestoneIntent(
            operation_id=str(row["operation_id"]),
            milestone_id=str(row["milestone_id"]),
            plugin_id=str(row["plugin_id"]),
            chat_id=int(row["operation_chat_id"]),
            user_id=int(row["operation_user_id"]),
            mode=str(row["mode"] or ""),
            text=str(row["text"] or ""),
            photo_url=str(row["photo_url"] or ""),
            delivery_state=str(row["delivery_state"] or "unknown"),
            attempt_count=int(row["attempt_count"] or 0),
            last_error=str(row["last_error"] or ""),
            expected_message_id=(
                int(row["expected_message_id"])
                if row["expected_message_id"] is not None
                else None
            ),
            expected_message_kind=str(row["expected_message_kind"] or ""),
            delivered_message_id=(
                int(row["delivered_message_id"])
                if row["delivered_message_id"] is not None
                else None
            ),
            delivered_message_kind=str(row["delivered_message_kind"] or ""),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"] or row["created_at"]),
        )
