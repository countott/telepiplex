"""Small durable operation journal; never stores credentials or subtitle contents."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time


class CaptionStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, payload TEXT, updated REAL)")

    def save(self, operation: dict):
        value = {k: v for k, v in operation.items() if k not in {"task", "cancel_event", "session", "inventory"}}
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO operations VALUES (?,?,?)", (
                value["operation_id"], json.dumps(value, ensure_ascii=False), time.time(),
            ))
            db.execute("DELETE FROM operations WHERE id IN (SELECT id FROM operations ORDER BY updated DESC LIMIT -1 OFFSET 500)")

    def get(self, operation_id: str) -> dict | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload FROM operations WHERE id=?", (str(operation_id),)).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        if value.get("state") in {"running", "awaiting_input", "cancelling"}:
            value.update(state="interrupted", control="", stage="interrupted",
                         status_text="字幕任务因模块重启中断；已写入的字幕保留，可重新发起。")
        return value
