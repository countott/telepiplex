from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class RenameJobStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS rename_jobs (
                job_id TEXT PRIMARY KEY, state TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}', updated_at REAL NOT NULL
            )""")
            db.execute("UPDATE rename_jobs SET state='failed' WHERE state='processing'")

    def get(self, job_id):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM rename_jobs WHERE job_id=?", (str(job_id),)).fetchone()
        return None if not row else {"job_id": row["job_id"], "state": row["state"], "result": json.loads(row["result_json"])}

    def claim(self, job_id):
        with sqlite3.connect(self.path) as db:
            cursor = db.execute("INSERT OR IGNORE INTO rename_jobs(job_id,state,updated_at) VALUES (?,'processing',?)", (str(job_id), time.time()))
            return cursor.rowcount == 1

    def claim_retryable(self, job_id, *, reopen_completed=False):
        """Claim a new inventory job or reopen a terminal retryable one."""
        now = time.time()
        with sqlite3.connect(self.path) as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO rename_jobs(job_id,state,updated_at) "
                "VALUES (?,'processing',?)",
                (str(job_id), now),
            )
            if cursor.rowcount == 1:
                return True
            retryable_states = ["failed", "cancelled"]
            if reopen_completed:
                retryable_states.extend(["completed", "partial_completed"])
            placeholders = ",".join("?" for _ in retryable_states)
            cursor = db.execute(
                "UPDATE rename_jobs SET state='processing', result_json='{}', "
                f"updated_at=? WHERE job_id=? AND state IN ({placeholders})",
                (now, str(job_id), *retryable_states),
            )
            return cursor.rowcount == 1

    def update(self, job_id, state, result):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE rename_jobs SET state=?, result_json=?, updated_at=? WHERE job_id=?", (str(state), json.dumps(result or {}, ensure_ascii=False, sort_keys=True), time.time(), str(job_id)))
        return self.get(job_id)

    def find_awaiting_metadata(self, selector):
        """Resolve either a legacy job id or a durable short callback token."""
        selector = str(selector or "")
        exact = self.get(selector)
        if exact and exact.get("state") == "awaiting_metadata":
            return exact
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT job_id FROM rename_jobs "
                "WHERE state='awaiting_metadata' ORDER BY updated_at"
            ).fetchall()
        matches = []
        for row in rows:
            job = self.get(row["job_id"])
            result = (job or {}).get("result") or {}
            if str(result.get("metadata_callback_token") or "") == selector:
                matches.append(job)
        return matches[0] if len(matches) == 1 else None

    def resumable(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT job_id FROM rename_jobs "
                "WHERE state IN ("
                "'awaiting_metadata', 'resolving_metadata', 'ready_metadata', "
                "'processed', 'published'"
                ") "
                "ORDER BY updated_at"
            ).fetchall()
        return [self.get(row["job_id"]) for row in rows]
