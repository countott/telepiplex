"""Durable frozen metadata resolutions for confirmation without re-planning."""

from __future__ import annotations

from copy import deepcopy
import json
import sqlite3
import time
from pathlib import Path


class MetadataResolutionStore:
    def __init__(
        self,
        path: Path | str | None = None,
        *,
        ttl_seconds: float = 24 * 60 * 60,
        max_entries: int = 256,
        now=None,
    ):
        self.path = str(path) if path is not None else ""
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self.now = now or time.time
        self._memory: dict[str, dict] = {}
        if self.path:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS metadata_resolutions ("
                    "resolution_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, "
                    "created_at REAL NOT NULL, expires_at REAL NOT NULL)"
                )

    def save(self, resolution_id: str, payload: dict) -> None:
        resolution_id = str(resolution_id or "").strip()
        if not resolution_id or not isinstance(payload, dict):
            raise ValueError("metadata resolution identity and payload are required")
        now = float(self.now())
        record = deepcopy(payload)
        record.setdefault("selected_candidate_ref", "")
        record.setdefault("result", None)
        if not self.path:
            self._prune_memory(now)
            self._memory[resolution_id] = {
                "payload": record,
                "created_at": now,
                "expires_at": now + self.ttl_seconds,
            }
            self._prune_memory(now)
            return
        encoded = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with sqlite3.connect(self.path) as db:
            db.execute(
                "DELETE FROM metadata_resolutions WHERE expires_at<=?",
                (now,),
            )
            db.execute(
                "INSERT OR REPLACE INTO metadata_resolutions "
                "(resolution_id,payload_json,created_at,expires_at) "
                "VALUES (?,?,?,?)",
                (resolution_id, encoded, now, now + self.ttl_seconds),
            )
            db.execute(
                "DELETE FROM metadata_resolutions WHERE resolution_id IN ("
                "SELECT resolution_id FROM metadata_resolutions "
                "ORDER BY created_at DESC, resolution_id DESC LIMIT -1 OFFSET ?"
                ")",
                (self.max_entries,),
            )

    def load(self, resolution_id: str) -> tuple[str, dict | None]:
        resolution_id = str(resolution_id or "").strip()
        if not resolution_id:
            return "missing", None
        now = float(self.now())
        if not self.path:
            stored = self._memory.get(resolution_id)
            if stored is None:
                return "missing", None
            if float(stored["expires_at"]) <= now:
                self._memory.pop(resolution_id, None)
                return "expired", None
            self._prune_memory(now, keep=resolution_id)
            return "found", deepcopy(stored["payload"])
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT payload_json,expires_at FROM metadata_resolutions "
                "WHERE resolution_id=?",
                (resolution_id,),
            ).fetchone()
            if row is None:
                db.execute(
                    "DELETE FROM metadata_resolutions WHERE expires_at<=?",
                    (now,),
                )
                return "missing", None
            if float(row[1]) <= now:
                db.execute(
                    "DELETE FROM metadata_resolutions WHERE resolution_id=?",
                    (resolution_id,),
                )
                return "expired", None
        return "found", json.loads(row[0])

    def cache_result(
        self,
        resolution_id: str,
        candidate_ref: str,
        result: dict,
    ) -> None:
        state, record = self.load(resolution_id)
        if state != "found" or record is None:
            raise KeyError(str(resolution_id))
        record["selected_candidate_ref"] = str(candidate_ref or "")
        record["result"] = deepcopy(result)
        if not self.path:
            stored = self._memory[str(resolution_id)]
            stored["payload"] = record
            return
        encoded = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE metadata_resolutions SET payload_json=? "
                "WHERE resolution_id=?",
                (encoded, str(resolution_id)),
            )

    def _prune_memory(self, now: float, *, keep: str = "") -> None:
        for key in list(self._memory):
            if key != keep and float(self._memory[key]["expires_at"]) <= now:
                self._memory.pop(key, None)
        overflow = len(self._memory) - self.max_entries
        if overflow <= 0:
            return
        oldest = sorted(
            self._memory,
            key=lambda key: (
                float(self._memory[key]["created_at"]),
                key,
            ),
        )
        for key in oldest:
            if overflow <= 0:
                break
            if key == keep:
                continue
            self._memory.pop(key, None)
            overflow -= 1
