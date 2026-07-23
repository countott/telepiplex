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

    def update(self, job_id, state, result):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE rename_jobs SET state=?, result_json=?, updated_at=? WHERE job_id=?", (str(state), json.dumps(result or {}, ensure_ascii=False, sort_keys=True), time.time(), str(job_id)))
        return self.get(job_id)

    def resumable(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT job_id FROM rename_jobs "
                "WHERE state IN ('processed', 'published') "
                "ORDER BY updated_at"
            ).fetchall()
        return [self.get(row["job_id"]) for row in rows]
