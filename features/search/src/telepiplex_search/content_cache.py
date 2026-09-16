"""Bounded, durable metadata reuse. Never stores releases or download tasks."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import unicodedata


def cache_key(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def normalized_query(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def rebind_metadata(value: dict, plan_id: str) -> dict:
    """A cache hit supplies facts, never the previous task's identity."""
    result = deepcopy(value)
    if "plan_id" in result:
        result["plan_id"] = plan_id
    if "search_session_id" in result:
        result["search_session_id"] = plan_id
    contract = result.get("media_metadata")
    if isinstance(contract, dict) and contract.get("schema_version") == 1:
        contract["metadata_id"] = plan_id
        contract["confirmed"] = False
    if isinstance(result.get("candidates"), list):
        result["candidates"] = [rebind_metadata(item, plan_id) for item in result["candidates"]]
    return result


class ContentCache:
    def __init__(self, path: Path | None = None, *, now=time.time, max_entries=512):
        self.path = str(path) if path else ""
        self.now = now
        self.max_entries = max(1, int(max_entries))
        self._memory = {}
        if self.path:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as db:
                db.execute("CREATE TABLE IF NOT EXISTS search_content ("
                           "namespace TEXT, key TEXT, payload TEXT NOT NULL, "
                           "expires REAL NOT NULL, used REAL NOT NULL, PRIMARY KEY(namespace,key))")

    def get(self, namespace: str, key: str):
        now = self.now()
        if not self.path:
            entry = self._memory.get((namespace, key))
            if not entry or entry[0] <= now:
                self._memory.pop((namespace, key), None)
                return None
            self._memory[(namespace, key)] = (entry[0], now, entry[2])
            return deepcopy(entry[2])
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM search_content WHERE expires<=?", (now,))
            row = db.execute("SELECT payload FROM search_content WHERE namespace=? AND key=?",
                             (namespace, key)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE search_content SET used=? WHERE namespace=? AND key=?",
                       (now, namespace, key))
        return json.loads(row[0])

    def put(self, namespace: str, key: str, payload: dict, ttl: float):
        now = self.now()
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if not self.path:
            self._memory = {k: v for k, v in self._memory.items() if v[0] > now}
            self._memory[(namespace, key)] = (now + ttl, now, json.loads(encoded))
            while len(self._memory) > self.max_entries:
                oldest = min(self._memory, key=lambda k: self._memory[k][1])
                del self._memory[oldest]
            return
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM search_content WHERE expires<=?", (now,))
            db.execute("INSERT OR REPLACE INTO search_content VALUES (?,?,?,?,?)",
                       (namespace, key, encoded, now + ttl, now))
            db.execute("DELETE FROM search_content WHERE rowid IN (SELECT rowid FROM search_content "
                       "ORDER BY used DESC, rowid DESC LIMIT -1 OFFSET ?)", (self.max_entries,))
