from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping


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
    next_plugin_id: str
    details: Mapping[str, Any]
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
                    current = self._from_row(row)
                    self._validate_existing_owner(current, values)
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
                        "details_json = ?, updated_at = ? WHERE operation_id = ?",
                        (
                            values["plugin_id"],
                            values["state"],
                            values["stage"],
                            values["status_text"],
                            values["control"],
                            values["revision"],
                            next_plugin_id,
                            values["details_json"],
                            time.time(),
                            values["operation_id"],
                        ),
                    )
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

    def get(self, operation_id: str) -> OperationRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (str(operation_id),),
            ).fetchone()
        return self._from_row(row) if row is not None else None

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
            "details_json": cls._encode_details(report.get("details", {})),
        }

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
            next_plugin_id=str(row["next_plugin_id"]),
            details=MappingProxyType(json.loads(str(row["details_json"]))),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )
