# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path


JOB_SCHEMA = """
CREATE TABLE IF NOT EXISTS plex_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    step_results_json TEXT NOT NULL DEFAULT '{}',
    rating_key TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plex_confirmations (
    token_hash TEXT PRIMARY KEY,
    job_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    expires_at REAL NOT NULL,
    consumed_at REAL
);
"""

_UNSET = object()


class PlexJobRepository:
    def __init__(self, database_path, clock=time.time):
        self.database_path = str(database_path)
        self._clock = clock
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.executescript(JOB_SCHEMA)

    @staticmethod
    def _decode_job(row):
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "idempotency_key": row["idempotency_key"],
            "state": row["state"],
            "payload": json.loads(row["payload_json"] or "{}"),
            "step_results": json.loads(row["step_results_json"] or "{}"),
            "rating_key": row["rating_key"],
            "error": row["error"],
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

    def create_or_get(self, idempotency_key, payload):
        job, _created = self.create_or_get_with_status(idempotency_key, payload)
        return job

    def create_or_get_with_status(self, idempotency_key, payload):
        now = float(self._clock())
        payload_json = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        created = False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM plex_jobs WHERE idempotency_key = ?",
                (str(idempotency_key),),
            ).fetchone()
            if row is None:
                created = True
                cursor = connection.execute(
                    """
                    INSERT INTO plex_jobs (
                        idempotency_key, state, payload_json, step_results_json,
                        created_at, updated_at
                    ) VALUES (?, 'queued', ?, '{}', ?, ?)
                    """,
                    (str(idempotency_key), payload_json, now, now),
                )
                row = connection.execute(
                    "SELECT * FROM plex_jobs WHERE id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
            connection.commit()
        return self._decode_job(row), created

    def get(self, job_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM plex_jobs WHERE id = ?",
                (int(job_id),),
            ).fetchone()
        return self._decode_job(row)

    def list(self, limit=50):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM plex_jobs ORDER BY id DESC LIMIT ?",
                (max(int(limit), 1),),
            ).fetchall()
        return [self._decode_job(row) for row in rows]

    def claim(self, job_id) -> bool:
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE plex_jobs SET state = 'running', updated_at = ? "
                "WHERE id = ? AND state IN ('queued', 'failed', 'interrupted')",
                (now, int(job_id)),
            )
            connection.commit()
        return cursor.rowcount == 1

    def mark_incomplete_interrupted(self) -> list[int]:
        active_states = (
            "running", "scanning", "artwork", "audio", "subtitle",
            "locating", "matching", "localizing", "streams",
        )
        placeholders = ",".join("?" for _ in active_states)
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"SELECT id FROM plex_jobs WHERE state IN ({placeholders})",
                active_states,
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                connection.execute(
                    f"UPDATE plex_jobs SET state = 'interrupted', "
                    f"error = 'interrupted by Feature process stop', updated_at = ? "
                    f"WHERE state IN ({placeholders})",
                    (now, *active_states),
                )
            connection.commit()
        return ids

    def update(
        self,
        job_id,
        *,
        state=_UNSET,
        rating_key=_UNSET,
        step_results=_UNSET,
        error=_UNSET,
    ):
        assignments = []
        values = []
        for column, value in (
            ("state", state),
            ("rating_key", rating_key),
            ("error", error),
        ):
            if value is not _UNSET:
                assignments.append(f"{column} = ?")
                values.append(value)
        if step_results is not _UNSET:
            assignments.append("step_results_json = ?")
            values.append(json.dumps(step_results or {}, ensure_ascii=False, sort_keys=True))
        assignments.append("updated_at = ?")
        values.append(float(self._clock()))
        values.append(int(job_id))
        with self._connect() as connection:
            connection.execute(
                f"UPDATE plex_jobs SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
        return self.get(job_id)

    def issue_confirmation(self, job_id, action, payload, ttl_seconds=600):
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        expires_at = float(self._clock()) + int(ttl_seconds)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO plex_confirmations (
                    token_hash, job_id, action, payload_json, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    token_hash,
                    int(job_id),
                    str(action),
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                    expires_at,
                ),
            )
        return raw_token

    def consume_confirmation(self, token, action):
        token_hash = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM plex_confirmations
                WHERE token_hash = ? AND action = ?
                  AND consumed_at IS NULL AND expires_at >= ?
                """,
                (token_hash, str(action), now),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            connection.execute(
                "UPDATE plex_confirmations SET consumed_at = ? WHERE token_hash = ?",
                (now, token_hash),
            )
            connection.commit()
        payload = json.loads(row["payload_json"] or "{}")
        return {"job_id": int(row["job_id"]), "action": row["action"], **payload}
