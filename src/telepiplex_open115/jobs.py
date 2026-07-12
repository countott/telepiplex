from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class DownloadJobStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS download_jobs (
                job_id TEXT PRIMARY KEY, state TEXT NOT NULL, payload_json TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL
            )""")
            db.execute("UPDATE download_jobs SET state='interrupted' WHERE state='running'")

    def get(self, job_id):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM download_jobs WHERE job_id=?", (str(job_id),)).fetchone()
        if not row:
            return None
        return {"job_id": row["job_id"], "state": row["state"],
                "payload": json.loads(row["payload_json"]),
                "result": json.loads(row["result_json"]), "error": row["error"]}

    def create_or_get(self, job_id, payload):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR IGNORE INTO download_jobs(job_id,state,payload_json,updated_at) VALUES (?,'queued',?,?)",
                       (str(job_id), json.dumps(payload, ensure_ascii=False, sort_keys=True), time.time()))
        return self.get(job_id)

    def update(self, job_id, state, *, result=None, error=""):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE download_jobs SET state=?, result_json=?, error=?, updated_at=? WHERE job_id=?",
                       (str(state), json.dumps(result or {}, ensure_ascii=False, sort_keys=True), str(error)[:500], time.time(), str(job_id)))
        return self.get(job_id)

    def resumable(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT job_id FROM download_jobs WHERE state IN ('interrupted','downloaded') ORDER BY updated_at").fetchall()
        return [self.get(row["job_id"]) for row in rows]
