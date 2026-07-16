"""SQLite registry containing only user-selected canonical media entities."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .entity_graph import normalize_title


_SCHEMA = """
CREATE TABLE IF NOT EXISTS canonical_entities (
    entity_key TEXT PRIMARY KEY,
    content_kind TEXT NOT NULL,
    year TEXT NOT NULL,
    chinese_title TEXT NOT NULL DEFAULT '',
    original_title TEXT NOT NULL DEFAULT '',
    original_language TEXT NOT NULL DEFAULT '',
    official_english_title TEXT NOT NULL DEFAULT '',
    romanized_original_title TEXT NOT NULL DEFAULT '',
    canonical_search_title TEXT NOT NULL,
    search_title_policy TEXT NOT NULL,
    canonical_latin_title TEXT NOT NULL,
    poster_url TEXT NOT NULL DEFAULT '',
    poster_source TEXT NOT NULL DEFAULT '',
    external_ids_json TEXT NOT NULL DEFAULT '{}',
    scoring_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS canonical_relations (
    source_entity_key TEXT PRIMARY KEY,
    relation_type TEXT NOT NULL,
    target_entity_key TEXT NOT NULL DEFAULT '',
    target_chinese_title TEXT NOT NULL DEFAULT '',
    target_canonical_latin_title TEXT NOT NULL DEFAULT '',
    target_year TEXT NOT NULL DEFAULT '',
    target_external_ids_json TEXT NOT NULL DEFAULT '{}',
    mapping_kind TEXT NOT NULL DEFAULT '',
    season_number INTEGER,
    episode_number INTEGER,
    tvdb_episode_id TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT NOT NULL,
    FOREIGN KEY(source_entity_key) REFERENCES canonical_entities(entity_key)
        ON DELETE CASCADE
);
"""


def _text(value) -> str:
    return " ".join(str(value or "").split())


def _json_object(value) -> str:
    if not isinstance(value, dict):
        value = {}
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class CanonicalEntityRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def upsert_selected(self, entity: dict, relation: dict | None = None) -> None:
        entity_key = _text(entity.get("entity_key"))
        canonical_title = _text(entity.get("canonical_search_title"))
        canonical_latin = _text(entity.get("canonical_latin_title"))
        year = _text(entity.get("year"))
        if not entity_key or not canonical_title or not canonical_latin or not year:
            raise ValueError("selected canonical entity is incomplete")
        now = datetime.now(timezone.utc).isoformat()
        values = (
            entity_key,
            _text(entity.get("content_kind")),
            year,
            _text(entity.get("chinese_title")),
            _text(entity.get("original_title")),
            _text(entity.get("original_language")).casefold(),
            _text(entity.get("official_english_title")),
            _text(entity.get("romanized_original_title")),
            canonical_title,
            _text(entity.get("search_title_policy")),
            canonical_latin,
            _text(entity.get("poster_url")),
            _text(entity.get("poster_source")),
            _json_object(entity.get("external_ids")),
            _text(entity.get("scoring_version")),
            now,
            now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO canonical_entities (
                    entity_key, content_kind, year, chinese_title, original_title,
                    original_language, official_english_title,
                    romanized_original_title, canonical_search_title,
                    search_title_policy, canonical_latin_title, poster_url,
                    poster_source, external_ids_json, scoring_version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_key) DO UPDATE SET
                    content_kind=excluded.content_kind,
                    year=excluded.year,
                    chinese_title=excluded.chinese_title,
                    original_title=excluded.original_title,
                    original_language=excluded.original_language,
                    official_english_title=excluded.official_english_title,
                    romanized_original_title=excluded.romanized_original_title,
                    canonical_search_title=excluded.canonical_search_title,
                    search_title_policy=excluded.search_title_policy,
                    canonical_latin_title=excluded.canonical_latin_title,
                    poster_url=excluded.poster_url,
                    poster_source=excluded.poster_source,
                    external_ids_json=excluded.external_ids_json,
                    scoring_version=excluded.scoring_version,
                    updated_at=excluded.updated_at
                """,
                values,
            )
            connection.execute(
                "DELETE FROM canonical_relations WHERE source_entity_key = ?",
                (entity_key,),
            )
            if isinstance(relation, dict) and _text(relation.get("relation_type")):
                connection.execute(
                    """
                    INSERT INTO canonical_relations (
                        source_entity_key, relation_type, target_entity_key,
                        target_chinese_title, target_canonical_latin_title,
                        target_year, target_external_ids_json, mapping_kind,
                        season_number, episode_number, tvdb_episode_id, confirmed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_key,
                        _text(relation.get("relation_type")),
                        _text(relation.get("target_entity_key")),
                        _text(relation.get("target_chinese_title")),
                        _text(relation.get("target_canonical_latin_title")),
                        _text(relation.get("target_year")),
                        _json_object(relation.get("target_external_ids")),
                        _text(relation.get("mapping_kind")),
                        relation.get("season_number"),
                        relation.get("episode_number"),
                        _text(relation.get("tvdb_episode_id")),
                        now,
                    ),
                )

    @staticmethod
    def _decode(row: sqlite3.Row, relation: sqlite3.Row | None = None) -> dict:
        result = dict(row)
        result["external_ids"] = json.loads(result.pop("external_ids_json") or "{}")
        if relation is not None:
            relation_value = dict(relation)
            relation_value.pop("source_entity_key", None)
            relation_value["target_external_ids"] = json.loads(
                relation_value.pop("target_external_ids_json") or "{}"
            )
            result["relation"] = relation_value
        else:
            result["relation"] = None
        return result

    def get(self, entity_key: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM canonical_entities WHERE entity_key = ?",
                (_text(entity_key),),
            ).fetchone()
            if row is None:
                return None
            relation = connection.execute(
                "SELECT * FROM canonical_relations WHERE source_entity_key = ?",
                (row["entity_key"],),
            ).fetchone()
        return self._decode(row, relation)

    def resolve_exact(self, query: str) -> dict | None:
        query = _text(query)
        stable = re.fullmatch(r"([a-z][a-z0-9_-]*):([^\s:]+)", query.casefold())
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM canonical_entities").fetchall()
        if stable:
            provider, value = stable.groups()
            for row in rows:
                identifiers = json.loads(row["external_ids_json"] or "{}")
                if _text(identifiers.get(provider)).casefold() == value:
                    return self.get(row["entity_key"])
            return None

        match = re.fullmatch(r"(.+?)\s+((?:19|20)\d{2})", query)
        if not match:
            return None
        title, year = match.groups()
        normalized = normalize_title(title)
        for row in rows:
            if row["year"] != year:
                continue
            if normalized == normalize_title(row["canonical_latin_title"]):
                return self.get(row["entity_key"])
        return None

    def count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM canonical_entities"
            ).fetchone()[0])

    def raw_columns_for_test(self, table: str) -> set[str]:
        if table not in {"canonical_entities", "canonical_relations"}:
            raise ValueError("unknown registry table")
        with self._connect() as connection:
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"] for row in rows}
