# Core Media Metadata Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the search-owned `download_plan` payload with one core-owned `media_metadata v1` contract that drives all four media categories through media-search, renaming, and Plex, including unified Season 00 handling for series-related movies, while preserving module-only Feature branches.

**Architecture:** Core owns a passive JSON dictionary contract and category router. Media-search produces and confirms it for every search, renaming preserves it while binding locked Special files, and plex-management routes standalone media plus verifies official Specials or writes custom metadata for confirmed temporary Specials. The composed implementation is built and verified from current local `main`, then ported by ownership into core, media-search, renaming, and Plex Feature branches without merging `main` wholesale into them.

**Tech Stack:** Python 3.12, `unittest`, `pytest`, `python-telegram-bot`, `requests`, `plexapi`, SQLite-backed Plex jobs, existing Telepiplex module registry and 115 storage provider.

## Global Constraints

- Use an isolated worktree from current local `main` and branch `codex/core-media-metadata-contract` for the composed implementation.
- Keep all stable modules enabled by default; arbitrary module-disable combinations are outside this iteration.
- The only active public key is `metadata["media_metadata"]`; do not dual-write or read `metadata["download_plan"]`.
- Rename `plan_id` to `metadata_id` at the core boundary; Prowlarr queries and Telegram callback state remain search-local.
- Core is not a fourth runtime module and must not register commands, configuration, handlers, hooks, or services.
- media-search, renaming, and plex-management may import core but may not import one another.
- Support exactly `live_action_series`, `live_action_movie`, `animated_movie`, and `animated_series`, with strict library-type pairing.
- Any recognized series-related movie is placed in that series' Season 00; standalone movie placement is allowed only when no series placement applies.
- `mapping_kind="standalone"` also represents an ordinary primary series with no parent-series placement; only the three Special mapping kinds carry locked Season 00 episode numbers.
- Prefer official TVDB Special numbering, allow explicitly warned AI-inferred TVDB numbering, and allocate Wikipedia-backed temporary Specials from `S00E100`.
- `confirmed=true` freezes target series, category, season, and episode. Downstream consumers may bind files but may not silently change those values.
- Every live `category_folder` entry must receive a stable `kind`; human-readable names are display text, not routing keys.
- Plex official mappings are verified without custom overrides. Temporary mappings receive supported title, summary, date/year, and poster overrides. AI-inferred mappings are verified without renumbering or silent conversion.
- Do not add a metadata database. Plex may persist the JSON contract only inside its existing job repository.
- Keep existing generic behavior for downloads that do not contain `media_metadata`.
- Write tests before production code, observe the expected failure, make the minimum implementation pass, and commit each task independently.
- Do not push, force-update remote branches, or rewrite remote history without a later explicit user choice.

## Execution Topology

- Invoke `superpowers:using-git-worktrees` before Task 1.
- Create `/Users/young/Documents/telepiplex/.worktrees/core-media-metadata-contract` from local `main` on `codex/core-media-metadata-contract`.
- Run `python3 -m unittest discover tests -v` before edits. Stop and report if the baseline is not green.
- Keep ownership commits separate: core/config, media-search, renaming, Plex adapter/service, integration, then Feature-branch alignment.
- Preserve the existing `/Users/young/Documents/telepiplex/.worktrees/plex-management` worktree if it is registered and clean; do not create a duplicate worktree for the same branch.

---

### Task 1: Core `media_metadata v1` Contract

**Files:**
- Create: `app/core/media_metadata.py`
- Create: `tests/test_core_media_metadata.py`

**Interfaces:**
- Consumes: plain JSON-compatible dictionaries.
- Produces: `MEDIA_METADATA_KEY`, `CONTENT_KINDS`, `SERIES_EPISODE_MAPPINGS`, canonical `series_titles`/`series_folder_name`/`series_scope_key` helpers, `validate_media_metadata(value, require_confirmed=False)`, `attach_media_metadata(metadata, value)`, `extract_confirmed_media_metadata(metadata)`, `locked_episode(value)`, and `merge_resolved_items(value, resolved_items)`.

- [ ] **Step 1: Write failing core-contract tests**

```python
import json
import unittest

from app.core.media_metadata import (
    CONTENT_KINDS,
    MEDIA_METADATA_KEY,
    attach_media_metadata,
    extract_confirmed_media_metadata,
    locked_episode,
    merge_resolved_items,
    series_folder_name,
    series_scope_key,
    series_season_directory_name,
    validate_media_metadata,
)


class CoreMediaMetadataTest(unittest.TestCase):
    def _value(self):
        return {
            "schema_version": 1,
            "metadata_id": "metadata-a",
            "confirmed": True,
            "identity": {
                "chinese_title": "想见你",
                "english_title": "Someday or One Day The Movie",
                "year": "2022",
                "content_kind": "extension_movie",
                "summary": "电影版延续电视剧故事。",
                "original_release_date": "2022-12-24",
                "poster_url": "https://image.example/poster.jpg",
                "poster_source": "douban",
                "external_ids": {},
            },
            "relation": {
                "type": "sequel",
                "target_series": {
                    "chinese_title": "想见你",
                    "english_title": "Someday or One Day",
                    "year": "2019",
                    "external_ids": {},
                },
                "source": "wikipedia",
            },
            "placement": {
                "library_type": "series",
                "category_kind": "live_action_series",
                "season_number": 0,
                "episode_number": 100,
                "mapping_kind": "temporary_related_special",
                "mapping_source": "local_allocator",
                "tvdb_episode_id": "",
            },
            "source_entry": {
                "title": "想见你 (电影)",
                "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
                "provider": "wikipedia",
                "verification": "verified",
            },
            "items": [],
            "evidence": {},
            "warnings": [],
        }

    def test_valid_contract_round_trips_and_is_deep_copied(self):
        value = self._value()
        attached = attach_media_metadata({"source": "confirmed"}, value)
        extracted = extract_confirmed_media_metadata(attached)
        self.assertEqual(MEDIA_METADATA_KEY, "media_metadata")
        self.assertEqual(locked_episode(extracted), (0, 100))
        self.assertEqual(json.loads(json.dumps(extracted, ensure_ascii=False)), extracted)
        extracted["identity"]["chinese_title"] = "changed"
        self.assertEqual(value["identity"]["chinese_title"], "想见你")

    def test_rejects_wrong_category_pair_and_old_public_key(self):
        value = self._value()
        value["placement"]["category_kind"] = "animated_movie"
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))
        legacy_key = "_".join(("download", "plan"))
        self.assertIsNone(extract_confirmed_media_metadata({legacy_key: self._value()}))

    def test_accepts_exactly_the_four_category_library_pairs(self):
        pairs = {
            "live_action_series": "series",
            "live_action_movie": "movie",
            "animated_movie": "movie",
            "animated_series": "series",
        }
        for category_kind, library_type in pairs.items():
            with self.subTest(category_kind=category_kind):
                value = self._value()
                value["placement"].update({
                    "category_kind": category_kind,
                    "library_type": library_type,
                    "season_number": None,
                    "episode_number": None,
                    "mapping_kind": "standalone",
                })
                value["relation"]["target_series"] = {}
                if library_type == "series":
                    value["identity"]["content_kind"] = "series"
                    value["items"] = [{
                        "content_role": "main_episode",
                        "season_number": 1,
                        "episode_number": 1,
                    }]
                self.assertIsNotNone(validate_media_metadata(value, require_confirmed=True))

    def test_standalone_has_no_series_target_or_episode_lock(self):
        value = self._value()
        value["relation"]["target_series"] = {}
        value["placement"].update({
            "library_type": "movie",
            "category_kind": "live_action_movie",
            "season_number": None,
            "episode_number": None,
            "mapping_kind": "standalone",
        })
        self.assertIsNotNone(validate_media_metadata(value, require_confirmed=True))
        value["relation"]["target_series"] = {"english_title": "Someday or One Day"}
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_primary_series_uses_confirmed_items_for_ordinary_episodes(self):
        value = self._value()
        value["identity"]["content_kind"] = "series"
        value["relation"]["target_series"] = {}
        value["placement"].update({
            "mapping_kind": "standalone",
            "season_number": None,
            "episode_number": None,
        })
        value["items"] = [{
            "item_id": "episode-1",
            "content_role": "main_episode",
            "season_number": 1,
            "episode_number": 1,
        }]
        self.assertIsNotNone(validate_media_metadata(value, require_confirmed=True))
        value["items"] = []
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_all_v1_content_kinds_are_explicit_and_unknown_is_rejected(self):
        for content_kind in CONTENT_KINDS:
            with self.subTest(content_kind=content_kind):
                value = self._value()
                value["identity"]["content_kind"] = content_kind
                self.assertIsNotNone(validate_media_metadata(value, require_confirmed=True))
        value = self._value()
        value["identity"]["content_kind"] = "invented"
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_rejects_non_json_values(self):
        value = self._value()
        value["evidence"]["bad"] = {"not-json"}
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_series_storage_names_are_shared_by_search_and_renaming(self):
        value = self._value()
        self.assertEqual(series_folder_name(value), "想见你 (Someday or One Day)")
        self.assertEqual(series_season_directory_name(value, 0), "Someday or One Day Season 00")
        self.assertEqual(series_scope_key(value), "title:someday or one day:2019")

    def test_attach_rejects_a_legacy_outer_key_instead_of_dual_writing(self):
        legacy_key = "_".join(("download", "plan"))
        with self.assertRaisesRegex(ValueError, "legacy metadata key"):
            attach_media_metadata({legacy_key: {}}, self._value())

    def test_official_mapping_requires_tvdb_series_and_episode_ids(self):
        value = self._value()
        value["placement"].update({
            "mapping_kind": "tvdb_official",
            "episode_number": 5,
            "tvdb_episode_id": "",
        })
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))
        value["placement"]["tvdb_episode_id"] = "episode-5"
        value["relation"]["target_series"]["external_ids"]["tvdb"] = "series-1"
        self.assertIsNotNone(validate_media_metadata(value, require_confirmed=True))
        value["placement"]["season_number"] = 1
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_temporary_mapping_requires_source_locator(self):
        value = self._value()
        value["source_entry"]["url"] = ""
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))
        value["source_entry"]["external_id"] = "wikipedia:想見你_(電影)"
        self.assertIsNotNone(validate_media_metadata(value, require_confirmed=True))
        value["source_entry"]["title"] = ""
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_merge_resolved_items_cannot_change_locked_target(self):
        value = self._value()
        merged = merge_resolved_items(value, [{
            "content_role": "extension_movie",
            "season_number": 0,
            "episode_number": 100,
            "source_relative_path": "Movie.mkv",
            "final_path": "/真人剧集/想见你/Someday or One Day Season 00/Someday or One Day S00E100.mkv",
        }])
        self.assertEqual(merged["items"][0]["final_path"].rsplit("/", 1)[-1], "Someday or One Day S00E100.mkv")
        with self.assertRaisesRegex(ValueError, "locked target"):
            merge_resolved_items(value, [{
                "season_number": 0,
                "episode_number": 101,
                "final_path": "/wrong.mkv",
            }])
```

- [ ] **Step 2: Run the test and verify the missing module failure**

Run: `python3 -m unittest tests.test_core_media_metadata -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.media_metadata'`.

- [ ] **Step 3: Implement the core contract**

```python
# app/core/media_metadata.py
from __future__ import annotations

import json
import re
from copy import deepcopy


MEDIA_METADATA_KEY = "media_metadata"
LEGACY_METADATA_KEY = "_".join(("download", "plan"))
SCHEMA_VERSION = 1
CONTENT_KINDS = {
    "movie",
    "series",
    "main_episode",
    "ova",
    "narrative_bonus",
    "non_narrative_extra",
    "special",
    "prequel_movie",
    "sequel_movie",
    "extension_movie",
    "spin_off",
}
CATEGORY_LIBRARY_TYPES = {
    "live_action_series": "series",
    "live_action_movie": "movie",
    "animated_movie": "movie",
    "animated_series": "series",
}
MAPPING_KINDS = {
    "tvdb_official",
    "ai_inferred_tvdb",
    "temporary_related_special",
    "standalone",
}
SERIES_EPISODE_MAPPINGS = {
    "tvdb_official",
    "ai_inferred_tvdb",
    "temporary_related_special",
}
INVALID_NAME_CHARS = re.compile(r'[\\/*?"<>|]+')


def sanitize_contract_name(value) -> str:
    value = str(value or "").replace("：", ": ").replace("（", "(").replace("）", ")")
    value = re.sub(r"\s*(?:——|—|–|－)\s*", " - ", value)
    value = re.sub(r"\s+:", ":", value)
    value = re.sub(r"(?<!\d):\s*", ": ", value)
    value = INVALID_NAME_CHARS.sub("", value)
    return " ".join(value.split()).strip().strip(".")


def series_titles(value: dict) -> tuple[str, str]:
    relation_target = ((value.get("relation") or {}).get("target_series") or {})
    identity = value.get("identity") or {}
    source = relation_target if (
        relation_target.get("chinese_title") or relation_target.get("english_title")
    ) else identity
    return (
        sanitize_contract_name(source.get("chinese_title")),
        sanitize_contract_name(source.get("english_title")),
    )


def series_folder_name(value: dict) -> str:
    chinese_title, english_title = series_titles(value)
    if chinese_title and english_title and chinese_title != english_title:
        return f"{chinese_title} ({english_title})"
    return chinese_title or english_title


def series_season_directory_name(value: dict, season_number: int) -> str:
    chinese_title, english_title = series_titles(value)
    series_name = english_title or chinese_title
    return f"{series_name} Season {int(season_number):02d}" if series_name else ""


def series_scope_key(value: dict) -> str:
    target = ((value.get("relation") or {}).get("target_series") or {})
    target_ids = target.get("external_ids") or {}
    if target_ids.get("tvdb"):
        return f"tvdb:{_text(target_ids['tvdb'])}"
    chinese_title, english_title = series_titles(value)
    year = _text(target.get("year") or (value.get("identity") or {}).get("year"))
    return f"title:{(english_title or chinese_title).casefold()}:{year}"


def _text(value) -> str:
    return " ".join(str(value or "").split())


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _valid_items(items) -> bool:
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            return False
        role = _text(item.get("content_role"))
        if role and role not in CONTENT_KINDS:
            return False
        season = item.get("season_number")
        episode = item.get("episode_number")
        if season is None and episode is None:
            continue
        season = _integer(season)
        episode = _integer(episode)
        if season is None or season < 0 or episode is None or episode < 1:
            return False
    return True


def validate_media_metadata(value: object, require_confirmed: bool = False):
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return None
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    if not _text(value.get("metadata_id")):
        return None
    if require_confirmed and value.get("confirmed") is not True:
        return None

    identity = value.get("identity")
    relation = value.get("relation")
    placement = value.get("placement")
    if not isinstance(identity, dict) or not isinstance(relation, dict) or not isinstance(placement, dict):
        return None
    if not (_text(identity.get("chinese_title")) or _text(identity.get("english_title"))):
        return None
    if _text(identity.get("content_kind")) not in CONTENT_KINDS:
        return None
    if not isinstance(identity.get("external_ids") or {}, dict):
        return None

    category_kind = placement.get("category_kind")
    library_type = placement.get("library_type")
    if CATEGORY_LIBRARY_TYPES.get(category_kind) != library_type:
        return None
    mapping_kind = placement.get("mapping_kind")
    if mapping_kind not in MAPPING_KINDS:
        return None
    if not isinstance(value.get("evidence") or {}, dict):
        return None
    if not isinstance(value.get("warnings") or [], list):
        return None
    if not _valid_items(value.get("items") or []):
        return None

    if mapping_kind in SERIES_EPISODE_MAPPINGS:
        season = _integer(placement.get("season_number"))
        episode = _integer(placement.get("episode_number"))
        target = relation.get("target_series")
        if library_type != "series" or season != 0 or episode is None or episode < 1:
            return None
        if not isinstance(target, dict) or not (
            _text(target.get("chinese_title")) or _text(target.get("english_title"))
        ):
            return None

    if mapping_kind == "tvdb_official":
        target_ids = ((relation.get("target_series") or {}).get("external_ids") or {})
        if not _text(target_ids.get("tvdb")) or not _text(placement.get("tvdb_episode_id")):
            return None

    if mapping_kind == "ai_inferred_tvdb" and not value.get("warnings"):
        return None

    if mapping_kind == "temporary_related_special":
        source_entry = value.get("source_entry")
        episode = _integer(placement.get("episode_number"))
        if placement.get("season_number") != 0 or episode is None or episode < 100:
            return None
        if not isinstance(source_entry, dict) or not _text(source_entry.get("title")):
            return None
        if not (_text(source_entry.get("url")) or _text(source_entry.get("external_id"))):
            return None

    if mapping_kind == "standalone":
        target = relation.get("target_series")
        if placement.get("season_number") is not None or placement.get("episode_number") is not None:
            return None
        if isinstance(target, dict) and (
            _text(target.get("chinese_title")) or _text(target.get("english_title"))
        ):
            return None
        if library_type == "series":
            items = value.get("items") or []
            if not items or any(
                item.get("season_number") is None or item.get("episode_number") is None
                for item in items
            ):
                return None

    return deepcopy(value)


def attach_media_metadata(metadata: dict | None, value: dict) -> dict:
    validated = validate_media_metadata(value, require_confirmed=True)
    if validated is None:
        raise ValueError("invalid confirmed media_metadata")
    result = deepcopy(metadata) if isinstance(metadata, dict) else {}
    if LEGACY_METADATA_KEY in result:
        raise ValueError("legacy metadata key is not allowed")
    result[MEDIA_METADATA_KEY] = validated
    return result


def extract_confirmed_media_metadata(metadata: dict | None):
    if not isinstance(metadata, dict):
        return None
    return validate_media_metadata(metadata.get(MEDIA_METADATA_KEY), require_confirmed=True)


def locked_episode(value: dict):
    placement = value.get("placement") if isinstance(value, dict) else None
    if not isinstance(placement, dict) or placement.get("library_type") != "series":
        return None
    season = _integer(placement.get("season_number"))
    episode = _integer(placement.get("episode_number"))
    if season is None or season < 0 or episode is None or episode < 1:
        return None
    return season, episode


def merge_resolved_items(value: dict, resolved_items: list[dict]) -> dict:
    contract = validate_media_metadata(value, require_confirmed=True)
    if contract is None:
        raise ValueError("invalid confirmed media_metadata")
    allowed = {
        (int(item["season_number"]), int(item["episode_number"]))
        for item in contract.get("items") or []
        if item.get("season_number") is not None and item.get("episode_number") is not None
    }
    if not allowed:
        episode = locked_episode(contract)
        if episode:
            allowed.add(episode)

    items = deepcopy(contract.get("items") or [])
    for resolved in resolved_items or []:
        season = _integer(resolved.get("season_number"))
        episode = _integer(resolved.get("episode_number"))
        if (season, episode) not in allowed:
            raise ValueError("resolved item changes locked target")
        match = next((
            item for item in items
            if _integer(item.get("season_number")) == season
            and _integer(item.get("episode_number")) == episode
        ), None)
        if match is None:
            match = {
                "item_id": _text(resolved.get("item_id")) or f"S{season:02d}E{episode:03d}",
                "content_role": _text(resolved.get("content_role")) or contract["identity"]["content_kind"],
                "season_number": season,
                "episode_number": episode,
            }
            items.append(match)
        for key in ("content_role", "source_relative_path", "final_path"):
            if key in resolved:
                match[key] = deepcopy(resolved[key])
    contract["items"] = items
    validated = validate_media_metadata(contract, require_confirmed=True)
    if validated is None:
        raise ValueError("resolved items make media_metadata invalid")
    return validated
```

- [ ] **Step 4: Run the focused tests**

Run: `python3 -m unittest tests.test_core_media_metadata -v`

Expected: all core tests PASS, including the four category pairs, `S00` locks, and standalone placement.

- [ ] **Step 5: Commit the core contract**

```bash
git add app/core/media_metadata.py tests/test_core_media_metadata.py
git commit -m "feat(core): define media metadata contract"
```

---

### Task 2: Stable Four-Category Routing and Config Migration

**Files:**
- Modify: `app/core/media_metadata.py`
- Modify: `app/init.py`
- Modify: `app/config.yaml.example`
- Modify: `config/config.yaml.example`
- Modify: `tests/test_core_media_metadata.py`
- Modify: `tests/test_config_template_contract.py`
- Create: `tests/test_category_route_startup.py`

**Interfaces:**
- Consumes: `config["category_folder"]` and a validated `category_kind`.
- Produces: `resolve_category_route(config: dict, category_kind: str) -> dict | None` with `kind`, `name`, `path`, and `plex_library_id`, plus a hard-cut startup validator for live configuration.

- [ ] **Step 1: Add failing route and template tests**

```python
# Add to tests/test_core_media_metadata.py.
from app.core.media_metadata import resolve_category_route

def test_category_route_uses_kind_not_display_name(self):
    route = resolve_category_route({
        "category_folder": [{
            "kind": "live_action_series",
            "name": "可改显示名",
            "path": "/真人剧集/",
            "plex_library_id": "13",
        }]
    }, "live_action_series")
    self.assertEqual(route, {
        "kind": "live_action_series",
        "name": "可改显示名",
        "path": "/真人剧集",
        "plex_library_id": "13",
    })
    self.assertIsNone(resolve_category_route({"category_folder": []}, "live_action_series"))
```

```python
# Add to tests/test_config_template_contract.py.
def test_category_routes_cover_exactly_four_kinds(self):
    import yaml
    parsed = yaml.safe_load((ROOT / "config/config.yaml.example").read_text(encoding="utf-8"))
    routes = parsed["category_folder"]
    self.assertEqual(
        {item["kind"] for item in routes},
        {"live_action_series", "live_action_movie", "animated_movie", "animated_series"},
    )
    self.assertTrue(all(item.get("path") for item in routes))
    self.assertTrue(all("plex_library_id" in item for item in routes))
```

```python
# tests/test_category_route_startup.py
import unittest

from app.core.media_metadata import require_complete_category_routes


class CategoryRouteStartupTest(unittest.TestCase):
    def test_pre_kind_live_config_is_rejected_with_migration_message(self):
        config = {"category_folder": [
            {"name": "真人剧集", "path": "/真人剧集", "plex_library_id": "11"},
            {"name": "真人电影", "path": "/真人电影", "plex_library_id": "12"},
            {"name": "动画电影", "path": "/动画电影", "plex_library_id": "13"},
            {"name": "动画剧集", "path": "/动画剧集", "plex_library_id": "14"},
        ]}
        with self.assertRaisesRegex(ValueError, "category_folder.*kind"):
            require_complete_category_routes(config)

    def test_complete_routes_pass_without_using_display_names(self):
        config = {"category_folder": [
            {"kind": kind, "name": "任意显示名", "path": f"/{kind}", "plex_library_id": ""}
            for kind in (
                "live_action_series", "live_action_movie",
                "animated_movie", "animated_series",
            )
        ]}
        require_complete_category_routes(config)
```

- [ ] **Step 2: Run the tests and observe the missing route/kind failures**

Run: `python3 -m unittest tests.test_core_media_metadata tests.test_config_template_contract -v`

Expected: FAIL because `resolve_category_route` and the four `kind` values do not exist.

- [ ] **Step 3: Add route resolution**

```python
# Add to app/core/media_metadata.py.
def resolve_category_route(config: dict | None, category_kind: str):
    if category_kind not in CATEGORY_LIBRARY_TYPES:
        return None
    for item in (config or {}).get("category_folder") or []:
        if not isinstance(item, dict) or item.get("kind") != category_kind:
            continue
        path = "/" + str(item.get("path") or "").strip("/")
        if path == "/":
            return None
        return {
            "kind": category_kind,
            "name": str(item.get("name") or category_kind),
            "path": path,
            "plex_library_id": str(item.get("plex_library_id") or ""),
        }
    return None


def require_complete_category_routes(config: dict | None) -> None:
    routes = (config or {}).get("category_folder")
    if not isinstance(routes, list):
        raise ValueError("category_folder must define four routes with kind")
    seen = set()
    for item in routes:
        if not isinstance(item, dict):
            raise ValueError("category_folder entries must be objects with kind")
        kind = item.get("kind")
        if kind not in CATEGORY_LIBRARY_TYPES or kind in seen:
            raise ValueError("category_folder has missing, duplicate, or invalid kind")
        if not str(item.get("path") or "").strip("/"):
            raise ValueError(f"category_folder.{kind}.path is required")
        if "plex_library_id" not in item:
            raise ValueError(f"category_folder.{kind}.plex_library_id key is required")
        seen.add(kind)
    if seen != set(CATEGORY_LIBRARY_TYPES):
        raise ValueError("category_folder must contain exactly four kind values")
```

After `load_yaml_config()` parses either the live file or a newly copied
template, call `require_complete_category_routes(bot_config)` outside the broad
YAML parse `except`. A pre-kind live config must stop startup with the exact
migration error; do not infer `kind` from `name` and do not rewrite the user's
file automatically.

- [ ] **Step 4: Replace both template category blocks identically**

```yaml
category_folder:
  - kind: live_action_movie
    name: 真人电影
    path: /真人电影
    plex_library_id: ""
  - kind: animated_movie
    name: 动画电影
    path: /动画电影
    plex_library_id: ""
  - kind: live_action_series
    name: 真人剧集
    path: /真人剧集
    plex_library_id: ""
  - kind: animated_series
    name: 动画剧集
    path: /动画剧集
    plex_library_id: ""
```

- [ ] **Step 5: Run route/config regressions**

Run: `python3 -m unittest tests.test_core_media_metadata tests.test_config_template_contract tests.test_category_route_startup tests.test_directory_config -v`

Expected: all tests PASS and the two full templates remain byte-for-byte identical.

- [ ] **Step 6: Commit the core route and templates**

```bash
git add app/core/media_metadata.py app/init.py app/config.yaml.example config/config.yaml.example tests/test_core_media_metadata.py tests/test_config_template_contract.py tests/test_category_route_startup.py
git commit -m "feat(core): route four media categories by kind"
```

---

### Task 3: Media-Search Draft and Mandatory AI Output

**Files:**
- Modify: `app/utils/search_plan.py`
- Modify: `app/utils/ai.py`
- Modify: `app/services/search_planner.py`
- Modify: `tests/test_search_plan.py`
- Modify: `tests/test_search_ai_pipeline.py`
- Modify: `tests/test_search_planner_service.py`

**Interfaces:**
- Consumes: stage-two AI output shaped as `{plan_id, media_metadata, prowlarr_queries}`.
- Produces: `validate_draft_search_plan`, `finalize_search_plan`, `confirm_media_metadata`, `infer_media_metadata_draft_with_ai`, and `build_confirmable_search_plan`.

- [ ] **Step 1: Replace search-plan tests with the hard-cut draft shape**

```python
import unittest

from app.utils.search_plan import (
    TemporarySpecialAllocator,
    confirm_media_metadata,
    finalize_search_plan,
    validate_draft_search_plan,
)


class SearchPlanTest(unittest.TestCase):
    def _draft(self):
        return {
            "plan_id": "plan-a",
            "media_metadata": {
                "schema_version": 1,
                "metadata_id": "",
                "confirmed": False,
                "identity": {
                    "chinese_title": "想见你",
                    "english_title": "Someday or One Day The Movie",
                    "year": "2022",
                    "content_kind": "extension_movie",
                    "summary": "电影版延续电视剧故事。",
                    "original_release_date": "2022-12-24",
                    "poster_url": "https://image.example/poster.jpg",
                    "poster_source": "douban",
                    "external_ids": {},
                },
                "relation": {
                    "type": "sequel",
                    "target_series": {
                        "chinese_title": "想见你",
                        "english_title": "Someday or One Day",
                        "year": "2019",
                        "external_ids": {},
                    },
                    "source": "wikipedia",
                },
                "placement": {
                    "library_type": "series",
                    "category_kind": "live_action_series",
                    "season_number": 0,
                    "episode_number": None,
                    "mapping_kind": "temporary_related_special",
                    "mapping_source": "local_allocator",
                    "tvdb_episode_id": "",
                },
                "source_entry": {
                    "title": "想见你 (电影)",
                    "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
                    "provider": "wikipedia",
                    "verification": "verified",
                },
                "items": [],
                "evidence": {
                    "provider_statuses": {
                        "wikipedia": "ok",
                        "douban": "ok",
                        "tvdb": "not_found",
                    }
                },
                "warnings": [],
            },
            "prowlarr_queries": ["Someday or One Day The Movie 2022"],
        }

    def test_finalize_allocates_then_confirm_returns_only_core_contract(self):
        draft = self._draft()
        final = finalize_search_plan(draft, TemporarySpecialAllocator(), {100})
        contract = confirm_media_metadata(final)
        self.assertEqual(final["media_metadata"]["placement"]["episode_number"], 101)
        self.assertEqual(contract["metadata_id"], "plan-a")
        self.assertTrue(contract["confirmed"])
        self.assertNotIn("prowlarr_queries", contract)
        self.assertNotIn("plan_id", contract)

    def test_allocator_starts_at_100_and_skips_occupied_and_reserved_values(self):
        allocator = TemporarySpecialAllocator()
        self.assertEqual(allocator.reserve("plan-a", "show-a", set()), 100)
        self.assertEqual(allocator.reserve("plan-a", "show-a", {100, 101}), 100)
        self.assertEqual(allocator.reserve("plan-b", "show-a", {100, 102}), 101)
        self.assertEqual(allocator.reserve("plan-c", "show-a", {100, 101, 102}), 103)
        self.assertEqual(allocator.reserve("plan-d", "show-b", set()), 100)
        self.assertEqual(TemporarySpecialAllocator().reserve("after-restart", "show-a", set()), 100)

    def test_draft_requires_search_queries_and_findable_source(self):
        draft = self._draft()
        draft["prowlarr_queries"] = []
        self.assertIsNone(validate_draft_search_plan(draft))
        draft = self._draft()
        draft["media_metadata"]["source_entry"]["url"] = ""
        self.assertIsNone(validate_draft_search_plan(draft))

    def test_queries_are_normalized_before_first_query_is_consumed(self):
        draft = self._draft()
        draft["prowlarr_queries"] = ["", "  valid query  "]
        normalized = validate_draft_search_plan(draft)
        self.assertEqual(normalized["prowlarr_queries"], ["valid query"])

    def test_temporary_source_down_requires_explicit_unverified_warning(self):
        draft = self._draft()
        draft["media_metadata"]["evidence"]["provider_statuses"]["wikipedia"] = "server_down"
        draft["media_metadata"]["source_entry"].update({
            "availability": "server_down",
            "verification": "ai_supplied_unverified",
        })
        self.assertIsNone(validate_draft_search_plan(draft))
        draft["media_metadata"]["warnings"] = ["Wikipedia不可用，来源条目由AI提供，未实时验证。"]
        self.assertIsNotNone(validate_draft_search_plan(draft))

    def test_official_tvdb_hint_cannot_be_downgraded(self):
        draft = self._draft()
        draft["media_metadata"]["evidence"]["tvdb_official_special"] = {
            "series_id": "series-1",
            "episode_id": "episode-5",
        }
        self.assertIsNone(validate_draft_search_plan(draft))

    def test_standalone_drafts_cover_all_four_categories(self):
        pairs = {
            "live_action_series": "series",
            "live_action_movie": "movie",
            "animated_movie": "movie",
            "animated_series": "series",
        }
        for category_kind, library_type in pairs.items():
            with self.subTest(category_kind=category_kind):
                draft = self._draft()
                draft["media_metadata"]["relation"]["target_series"] = {}
                draft["media_metadata"]["placement"].update({
                    "library_type": library_type,
                    "category_kind": category_kind,
                    "season_number": None,
                    "episode_number": None,
                    "mapping_kind": "standalone",
                    "mapping_source": "ai",
                })
                if library_type == "series":
                    draft["media_metadata"]["identity"]["content_kind"] = "series"
                    draft["media_metadata"]["items"] = [{
                        "content_role": "main_episode",
                        "season_number": 1,
                        "episode_number": 1,
                    }]
                self.assertIsNotNone(
                    finalize_search_plan(draft, TemporarySpecialAllocator(), set())
                )
```

- [ ] **Step 2: Update AI tests to expect nested `media_metadata` and the renamed function**

```python
from app.utils.ai import infer_media_metadata_draft_with_ai

def test_stage_two_returns_search_local_queries_and_nested_contract(self):
    payload = infer_media_metadata_draft_with_ai({"sources": []})
    self.assertIn("media_metadata", payload)
    self.assertIn("prowlarr_queries", payload)
    self.assertNotIn("_".join(("download", "plan")), payload)
```

Keep the existing `chat_completion` patch and return a complete JSON object matching the `_draft()` shape above.

- [ ] **Step 3: Run focused tests and observe missing renamed interfaces**

Run: `python3 -m unittest tests.test_search_plan tests.test_search_ai_pipeline tests.test_search_planner_service -v`

Expected: FAIL because the renamed functions and nested output do not exist.

- [ ] **Step 4: Replace `app/utils/search_plan.py` with search-local planning functions**

```python
from __future__ import annotations

from copy import deepcopy
from threading import Lock

from app.core.media_metadata import series_scope_key, validate_media_metadata


TEMPORARY_MAPPING_KIND = "temporary_related_special"


def _text(value) -> str:
    return " ".join(str(value or "").split())


def validate_draft_search_plan(value: object):
    if not isinstance(value, dict) or not _text(value.get("plan_id")):
        return None
    contract = value.get("media_metadata")
    queries = value.get("prowlarr_queries")
    if not isinstance(contract, dict) or not isinstance(queries, list):
        return None
    normalized_queries = [_text(item) for item in queries if _text(item)]
    if not normalized_queries:
        return None
    placement = contract.get("placement")
    identity = contract.get("identity")
    relation = contract.get("relation")
    if not isinstance(placement, dict) or not isinstance(identity, dict) or not isinstance(relation, dict):
        return None
    if placement.get("mapping_kind") == TEMPORARY_MAPPING_KIND:
        source_entry = contract.get("source_entry")
        if placement.get("season_number") != 0 or placement.get("episode_number") is not None:
            return None
        if not isinstance(source_entry, dict) or not _text(source_entry.get("title")):
            return None
        if not (_text(source_entry.get("url")) or _text(source_entry.get("external_id"))):
            return None
        provider = _text(source_entry.get("provider"))
        statuses = ((contract.get("evidence") or {}).get("provider_statuses") or {})
        status = _text(statuses.get(provider))
        if status and status != "ok":
            if _text(source_entry.get("availability")) != status:
                return None
            if _text(source_entry.get("verification")) in {"", "verified"}:
                return None
            if not contract.get("warnings"):
                return None
    official_hint = ((contract.get("evidence") or {}).get("tvdb_official_special") or {})
    if official_hint and placement.get("mapping_kind") != "tvdb_official":
        return None
    if official_hint:
        target_ids = (((contract.get("relation") or {}).get("target_series") or {}).get("external_ids") or {})
        if _text(target_ids.get("tvdb")) != _text(official_hint.get("series_id")):
            return None
        if _text(placement.get("tvdb_episode_id")) != _text(official_hint.get("episode_id")):
            return None
        verified_key = f"{_text(official_hint.get('series_id'))}:{_text(official_hint.get('episode_id'))}"
        verified_keys = set((contract.get("evidence") or {}).get("verified_tvdb_episode_keys") or [])
        if verified_key not in verified_keys:
            return None
    result = deepcopy(value)
    result["prowlarr_queries"] = normalized_queries
    result["media_metadata"]["metadata_id"] = _text(result["plan_id"])
    result["media_metadata"]["confirmed"] = False
    return result


class TemporarySpecialAllocator:
    def __init__(self):
        self._lock = Lock()
        self._reservations: dict[str, tuple[str, int]] = {}

    def reserve(self, plan_id: str, scope_key: str, occupied: set[int]) -> int:
        with self._lock:
            if plan_id in self._reservations:
                reserved_scope, number = self._reservations[plan_id]
                if reserved_scope != scope_key:
                    raise ValueError("plan_id changed target series")
                return number
            unavailable = set()
            for item in occupied:
                try:
                    number = int(item)
                except (TypeError, ValueError):
                    continue
                if number >= 100:
                    unavailable.add(number)
            unavailable.update(
                number
                for reserved_scope, number in self._reservations.values()
                if reserved_scope == scope_key
            )
            candidate = 100
            while candidate in unavailable:
                candidate += 1
            self._reservations[plan_id] = (scope_key, candidate)
            return candidate

    def release(self, plan_id: str) -> None:
        with self._lock:
            self._reservations.pop(plan_id, None)


def finalize_search_plan(draft: dict, allocator: TemporarySpecialAllocator, occupied: set[int]):
    plan = validate_draft_search_plan(draft)
    if plan is None:
        raise ValueError("invalid search plan")
    placement = plan["media_metadata"]["placement"]
    if placement.get("mapping_kind") == TEMPORARY_MAPPING_KIND:
        placement["episode_number"] = allocator.reserve(
            plan["plan_id"], series_scope_key(plan["media_metadata"]), occupied
        )
    if validate_media_metadata(plan["media_metadata"], require_confirmed=False) is None:
        raise ValueError("invalid finalized media_metadata")
    return plan


def confirm_media_metadata(plan: dict) -> dict:
    contract = deepcopy((plan or {}).get("media_metadata"))
    if not isinstance(contract, dict):
        raise ValueError("search plan has no media_metadata")
    contract["confirmed"] = True
    validated = validate_media_metadata(contract, require_confirmed=True)
    if validated is None:
        raise ValueError("invalid confirmed media_metadata")
    return validated
```

- [ ] **Step 5: Rename the stage-two AI prompt and function**

Replace `DOWNLOAD_PLAN_PROMPT` with `MEDIA_METADATA_DRAFT_PROMPT`. Its output contract is:

```python
MEDIA_METADATA_DRAFT_PROMPT = """你是影视中立元数据规划器。只返回JSON。
AI是主决策层；Wikipedia、豆瓣和TVDB是可失败的证据提供者。
每次搜索都必须使用两阶段AI，并读取Wikipedia、豆瓣和TVDB的状态；任一证据源server_down不能阻止第二阶段AI。
引用server_down或AI补出的来源条目时，source_entry必须标明availability/verification，并在warnings中明确说明未实时验证。
只要存在对应剧集版，电影版默认归入目标剧集Season 00；优先TVDB官方Special。
standalone只允许在不存在剧集关联时使用，target_series必须为空，season_number和episode_number必须为null。
普通主系列也使用standalone，但library_type=series时必须在items中列出用户确认的每个目标集（例如S01E01）；不得把普通集写进顶层Season 00锁。
items中的season_number/episode_number是后续重命名不可改写的锁，content_role使用允许的content_kind值。
temporary_related_special的episode_number必须为null，由本地分配器从S00E100开始填写。
tvdb_official必须输出目标tvdb series ID和tvdb_episode_id。
ai_inferred_tvdb必须输出明确的未实时验证warning。
identity必须包含中英文标题、year、content_kind、summary、original_release_date、poster_url、poster_source和external_ids。
只返回以下结构：
{"plan_id":"string","media_metadata":{"schema_version":1,"metadata_id":"","confirmed":false,"identity":{},"relation":{"type":"string","target_series":{},"source":"string"},"placement":{"library_type":"movie|series","category_kind":"live_action_series|live_action_movie|animated_movie|animated_series","season_number":null,"episode_number":null,"mapping_kind":"tvdb_official|ai_inferred_tvdb|temporary_related_special|standalone","mapping_source":"string","tvdb_episode_id":"string"},"source_entry":{},"items":[{"item_id":"string","content_role":"main_episode|ova|narrative_bonus|non_narrative_extra|special","season_number":1,"episode_number":1}],"evidence":{},"warnings":[]},"prowlarr_queries":["string"]}
输入事实：
"""


def infer_media_metadata_draft_with_ai(context: dict):
    if not check_ai_api_available():
        return None
    prompt = MEDIA_METADATA_DRAFT_PROMPT + json.dumps(context or {}, ensure_ascii=False, indent=2)
    _log_ai_info(f"AI中立元数据输入 context={_compact_json_for_log(context)}")
    result = chat_completion(prompt, max_tokens=8192)
    _log_ai_info(f"AI中立元数据原始响应 result={_compact_json_for_log(result)}")
    parsed = parse_ai_json_response(result)
    return parsed if isinstance(parsed, dict) else None
```

- [ ] **Step 6: Replace the planning service entry point**

```python
from app.utils.ai import infer_media_metadata_draft_with_ai, infer_search_hypotheses_with_ai
from app.utils.search_plan import (
    TEMPORARY_MAPPING_KIND,
    TemporarySpecialAllocator,
    finalize_search_plan,
)


async def build_confirmable_search_plan(
    raw_query: str,
    plan_id: str,
    providers: dict[str, Callable],
    occupied_loader: Callable[[dict], set[int]],
    allocator: TemporarySpecialAllocator,
) -> dict:
    hypotheses = await asyncio.to_thread(infer_search_hypotheses_with_ai, raw_query)
    if not isinstance(hypotheses, dict):
        _log_info(f"ai_stage=hypothesis status=unavailable metadata_id={plan_id}")
        raise SearchPlanningError("ai_hypothesis_unavailable")
    _log_info(f"ai_stage=hypothesis status=ok metadata_id={plan_id}")
    sources = await collect_evidence(hypotheses, providers)
    context = {
        "raw_query": raw_query,
        "plan_id": plan_id,
        "hypotheses": hypotheses,
        "sources": sources,
    }
    draft = await asyncio.to_thread(infer_media_metadata_draft_with_ai, context)
    if not isinstance(draft, dict):
        _log_info(f"ai_stage=media_metadata status=unavailable metadata_id={plan_id}")
        raise SearchPlanningError("ai_media_metadata_unavailable")
    _log_info(f"ai_stage=media_metadata status=ok metadata_id={plan_id}")
    draft["plan_id"] = plan_id
    contract = draft.get("media_metadata") if isinstance(draft.get("media_metadata"), dict) else {}
    evidence = contract.setdefault("evidence", {})
    evidence["provider_statuses"] = {
        str(item.get("source") or ""): str(item.get("status") or "invalid")
        for item in sources
        if isinstance(item, dict) and str(item.get("source") or "")
    }
    verified_episode_keys = set()
    tvdb_source = next((
        item for item in sources
        if isinstance(item, dict) and item.get("source") == "tvdb" and item.get("status") == "ok"
    ), None)
    for fact in (tvdb_source or {}).get("facts") or []:
        for series_id, episodes in (fact.get("episodes_by_series") or {}).items():
            for episode in episodes or []:
                episode_id = str(episode.get("tvdb_episode_id") or episode.get("id") or "").strip()
                if episode_id:
                    verified_episode_keys.add(f"{series_id}:{episode_id}")
    evidence["verified_tvdb_episode_keys"] = sorted(verified_episode_keys)
    try:
        occupied = (
            set(occupied_loader(contract) or set())
            if (contract.get("placement") or {}).get("mapping_kind") == TEMPORARY_MAPPING_KIND
            else set()
        )
    except Exception as exc:
        raise SearchPlanningError("temporary_occupancy_unavailable") from exc
    try:
        plan = finalize_search_plan(draft, allocator, occupied)
    except ValueError as exc:
        raise SearchPlanningError("invalid_media_metadata") from exc
    placement = plan["media_metadata"]["placement"]
    _log_info(
        "search_plan status=ready "
        f"metadata_id={plan_id} mapping_kind={placement.get('mapping_kind')} "
        f"query={(plan.get('prowlarr_queries') or [''])[0]}"
    )
    return plan
```

Update the existing soft-failure service test rather than deleting it: all three
provider fakes return `status="server_down"`; assert each runs once, all three
status objects reach `infer_media_metadata_draft_with_ai`, the second AI result
is still finalized, and its warning is preserved. Keep the first-AI and
second-AI unavailable tests: either mandatory AI failure must raise before
Prowlarr can run.

Add a focused policy test whose second-AI output contains a
`evidence.tvdb_official_special` claim that is backed by the TVDB provider's
returned series/episode IDs but chooses `temporary_related_special`; finalizing
must reject it. A matching `tvdb_official` draft with the same target series ID
and episode ID must pass. This keeps AI as the required decision layer while
making its own evidence/placement priority internally non-contradictory.

- [ ] **Step 7: Run the media-search planning tests**

Run: `python3 -m unittest tests.test_core_media_metadata tests.test_search_plan tests.test_search_ai_pipeline tests.test_search_planner_service -v`

Expected: all tests PASS and no test imports an old stage-two function.

- [ ] **Step 8: Commit the producer contract**

```bash
git add app/utils/search_plan.py app/utils/ai.py app/services/search_planner.py tests/test_search_plan.py tests/test_search_ai_pipeline.py tests/test_search_planner_service.py
git commit -m "feat(media-search): produce core media metadata"
```

---

### Task 4: One-Confirmation Search Flow Uses Core Contract

**Files:**
- Modify: `app/handlers/search_handler.py`
- Rename: `tests/test_search_download_plan_flow.py` -> `tests/test_search_media_metadata_flow.py`
- Modify: `tests/test_media_metadata_fusion.py`

**Interfaces:**
- Consumes: Task 2 `resolve_category_route`, Task 3 `build_confirmable_search_plan` and `confirm_media_metadata`.
- Produces: a `DownloadRequest` whose only shared contract is `metadata["media_metadata"]`.

- [ ] **Step 1: Rename the flow test and write failing hard-cut assertions**

Run: `git mv tests/test_search_download_plan_flow.py tests/test_search_media_metadata_flow.py`

Replace its dispatch assertion with:

```python
request = submit_mock.call_args.args[1]
self.assertEqual(request.selected_path, "/真人剧集")
self.assertIn("media_metadata", request.metadata)
self.assertNotIn("_".join(("download", "plan")), request.metadata)
self.assertEqual(request.metadata["media_metadata"]["metadata_id"], "plan-a")
self.assertTrue(request.metadata["media_metadata"]["confirmed"])
```

Change the fixture to the nested Task 3 search-plan shape and keep the existing assertions that confirmation text includes `S00E100`, the source URL, and the Prowlarr query.

- [ ] **Step 2: Run the renamed flow test and verify current key/name failures**

Run: `python3 -m unittest tests.test_search_media_metadata_flow -v`

Expected: FAIL because the handler still reads and writes `download_plan`.

- [ ] **Step 3: Replace handler imports and plan display helpers**

```python
from app.core.media_metadata import (
    attach_media_metadata,
    extract_confirmed_media_metadata,
    resolve_category_route,
    series_folder_name,
    series_season_directory_name,
)
from app.services.search_planner import SearchPlanningError, build_confirmable_search_plan
from app.utils.search_plan import TemporarySpecialAllocator, confirm_media_metadata


def _contract_from_search_plan(plan: dict) -> dict:
    value = plan.get("media_metadata") if isinstance(plan, dict) else None
    return value if isinstance(value, dict) else {}


def _build_media_metadata_text(plan: dict) -> str:
    contract = _contract_from_search_plan(plan)
    identity = contract.get("identity") or {}
    relation = contract.get("relation") or {}
    target = relation.get("target_series") or {}
    placement = contract.get("placement") or {}
    source_entry = contract.get("source_entry") or {}
    episode = placement.get("episode_number")
    episode_number = int(episode) if episode is not None else None
    width = 3 if episode_number is not None and episode_number >= 100 else 2
    marker = (
        f"S{int(placement.get('season_number') or 0):02d}E{episode_number:0{width}d}"
        if episode_number is not None else "未分配"
    )
    lines = [
        "📋 媒体元数据方案",
        f"目标：{identity.get('chinese_title') or ''} / {identity.get('english_title') or ''} ({identity.get('year') or '年份未知'})",
        f"内容身份：{identity.get('content_kind') or 'unknown'}",
        f"关联剧集：{target.get('chinese_title') or target.get('english_title') or '无'}",
        f"关系依据：{relation.get('source') or 'ai'}",
        f"归属：{placement.get('category_kind') or 'unknown'} / {marker}",
        f"来源条目：{source_entry.get('title') or '无'}",
    ]
    item_markers = []
    for item in contract.get("items") or []:
        season = item.get("season_number")
        episode = item.get("episode_number")
        if season is not None and episode is not None:
            item_markers.append(f"S{int(season):02d}E{int(episode):02d}")
    if item_markers:
        lines.append(f"已锁定集：{', '.join(item_markers)}")
    locator = source_entry.get("url") or source_entry.get("external_id") or ""
    if locator:
        lines.append(f"来源定位：{locator}")
    lines.append(f"搜索词：{(plan.get('prowlarr_queries') or [''])[0]}")
    lines.extend(f"⚠️ {warning}" for warning in contract.get("warnings") or [])
    return "\n".join(lines)


def _resolve_plan_selected_path(plan: dict) -> str:
    contract = _contract_from_search_plan(plan)
    route = resolve_category_route(init.bot_config, (contract.get("placement") or {}).get("category_kind"))
    return str((route or {}).get("path") or "")


def _occupied_special_numbers(contract: dict) -> set[int]:
    evidence_values = ((contract.get("evidence") or {}).get("occupied_special_numbers") or [])
    occupied = {int(value) for value in evidence_values if str(value).isdigit() and int(value) >= 100}
    route = resolve_category_route(
        init.bot_config,
        (contract.get("placement") or {}).get("category_kind"),
    )
    storage = getattr(init, "openapi_115", None)
    category_info = storage.get_file_info(route["path"]) if storage and route else None
    if not category_info:
        raise RuntimeError("cannot inspect configured category root")
    season_path = "/".join((
        route["path"].rstrip("/"),
        series_folder_name(contract),
        series_season_directory_name(contract, 0),
    ))
    if storage.get_file_info(season_path):
        for item in storage.get_files_from_dir(season_path) or []:
            name = str(item.get("name") or item.get("fn") or item) if isinstance(item, dict) else str(item)
            match = re.search(r"(?i)\bS00E(\d{3,})\b", name)
            if match:
                occupied.add(int(match.group(1)))
    return occupied
```

The storage scan is scoped to the canonical target-series `Season 00`
directory shared with renaming. After a process restart, an existing custom
`S00E100+` filename remains occupied even though TVDB cannot know about it,
while a different series may independently use E100. Add a test with a fresh
allocator and a target-series directory containing `Existing S00E100.mkv`; the
confirmed plan must allocate 101, then switch the contract to another target
series and assert it allocates 100. If the required category root cannot be inspected, a
temporary mapping stops before Prowlarr with
`temporary_occupancy_unavailable`; official/inferred/standalone mappings do not
perform the scan.

- [ ] **Step 4: Rename state, callback, and conversation identifiers**

Rename `SEARCH_CONFIRM_DOWNLOAD_PLAN` to `SEARCH_CONFIRM_MEDIA_METADATA` and
`confirm_download_plan_callback` to `confirm_media_metadata_callback`, including
the `ConversationHandler` state map and every test reference. Rename pending
state from `download_plan` to `search_plan`.

Use these complete helpers and parameter names:

```python
def _release_search_plan(search_plan):
    if isinstance(search_plan, dict):
        temporary_special_allocator.release(str(search_plan.get("plan_id") or ""))


def _store_pending_search_task(
    update,
    query: str,
    results: list[dict],
    naming_metadata,
    metadata,
    search_plan,
    selected_path: str,
) -> str:
    task_id = uuid.uuid4().hex[:10]
    pending_search_tasks[task_id] = {
        "created_at": time.time(),
        "query": query,
        "results": results,
        "user_id": update.effective_user.id,
        "naming_metadata": naming_metadata,
        "metadata": deepcopy(metadata) if isinstance(metadata, dict) else None,
        "search_plan": deepcopy(search_plan) if isinstance(search_plan, dict) else None,
        "selected_path": selected_path,
    }
    return task_id
```

Rename `_send_search_results(..., download_plan=None, ...)` to `_send_search_results(..., search_plan=None, ...)` and call `_release_search_plan(search_plan)` in every pre-dispatch error/no-result branch.

- [ ] **Step 5: Replace planning and confirmation callbacks**

```python
async def _start_entry_resolution(update, context, raw_query: str):
    plan_id = uuid.uuid4().hex[:10]
    providers = {
        "wikipedia": _wikipedia_plan_provider,
        "douban": _douban_plan_provider,
        "tvdb": _tvdb_plan_provider,
    }
    try:
        plan = await build_confirmable_search_plan(
            raw_query,
            plan_id,
            providers,
            _occupied_special_numbers,
            temporary_special_allocator,
        )
    except SearchPlanningError as exc:
        await update.message.reply_text(f"❌ 无法生成媒体元数据：{exc}")
        return ConversationHandler.END
    selected_path = _resolve_plan_selected_path(plan)
    if not selected_path:
        temporary_special_allocator.release(plan_id)
        await update.message.reply_text("❌ 媒体元数据无法对应到已配置的分类目录。")
        return ConversationHandler.END
    pending_entry_confirmations[plan_id] = {
        "created_at": time.time(),
        "user_id": update.effective_user.id,
        "plan": plan,
        "selected_path": selected_path,
    }
    await update.message.reply_text(
        _build_media_metadata_text(plan),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("确认并搜索", callback_data=f"plan_confirm:{plan_id}"),
            InlineKeyboardButton("取消", callback_data=f"plan_cancel:{plan_id}"),
        ]]),
        disable_web_page_preview=True,
    )
    return SEARCH_CONFIRM_MEDIA_METADATA


async def confirm_media_metadata_callback(update, context):
    callback = update.callback_query
    await callback.answer()
    action, plan_id = (callback.data or "").split(":", 1)
    task = get_pending_entry_confirmation(plan_id)
    if not task or not _owner_matches(task, update.effective_user.id):
        await callback.edit_message_text("⚠️ 媒体元数据已过期，请重新搜索。")
        return ConversationHandler.END
    if action == "plan_cancel":
        pending_entry_confirmations.pop(plan_id, None)
        temporary_special_allocator.release(plan_id)
        await callback.edit_message_text("已取消本次搜索。")
        return ConversationHandler.END
    search_plan = task["plan"]
    contract = confirm_media_metadata(search_plan)
    identity = contract["identity"]
    metadata = attach_media_metadata({"source": "confirmed"}, contract)
    pending_entry_confirmations.pop(plan_id, None)
    await callback.edit_message_text(f"✅ 已确认媒体元数据：{identity.get('chinese_title') or identity.get('english_title') or ''}")
    return await _send_search_results(
        update,
        context,
        (search_plan.get("prowlarr_queries") or [""])[0],
        naming_metadata={
            "source": "confirmed",
            "media_type": contract["placement"]["library_type"],
            "chinese_title": identity.get("chinese_title") or "",
            "english_title": identity.get("english_title") or "",
            "year": identity.get("year") or "",
        },
        metadata=metadata,
        search_plan=search_plan,
        selected_path=task["selected_path"],
    )
```

In `select_search_result`, dispatch the copied task metadata directly. Assert `extract_confirmed_media_metadata(metadata)` is not `None` before creating `DownloadRequest`; on failure release the search-plan reservation and stop. Register `confirm_media_metadata_callback` under `SEARCH_CONFIRM_MEDIA_METADATA`; no active handler symbol retains the old plan name.

In the flow test, record a shared `timeline` from the two AI mocks, the three
provider mocks, the confirmation-message mock, the Prowlarr search mock, and the
download-submit mock. Assert exactly one confirmation message/buttons pair is
sent, its index is before the first Prowlarr call, no Prowlarr/submit call occurs
before `confirm_media_metadata_callback`, and release selection submits without
adding another confirmation event.

- [ ] **Step 6: Run search-flow regressions**

Run: `python3 -m unittest tests.test_search_media_metadata_flow tests.test_media_metadata_fusion tests.test_media_search_surface tests.test_media_search_utils -v`

Expected: all tests PASS; one confirmation precedes Prowlarr and release selection does not prompt for a second category.

- [ ] **Step 7: Commit the media-search handoff**

```bash
git add app/handlers/search_handler.py tests/test_search_media_metadata_flow.py tests/test_media_metadata_fusion.py
git commit -m "feat(media-search): dispatch confirmed media metadata"
```

---

### Task 5: Plan-Locked Renaming Builder and Resolved Items

**Files:**
- Modify: `app/utils/tvdb_rename.py`
- Modify: `tests/test_tvdb_rename.py`

**Interfaces:**
- Consumes: confirmed core `media_metadata`, an AI file map, and the storage file tree.
- Produces: `build_confirmed_rename_plan(..., media_metadata: dict, ...)` and `enrich_media_metadata_with_rename_plan(media_metadata, rename_plan)`.

- [ ] **Step 1: Rewrite confirmed-builder tests around nested identity/relation**

```python
from app.utils.tvdb_rename import (
    build_confirmed_rename_plan,
    enrich_media_metadata_with_rename_plan,
)

def test_temporary_special_uses_locked_target_and_enriches_final_file(self):
    media_metadata = self._confirmed_media_metadata()
    rename_plan = build_confirmed_rename_plan(
        final_path="/真人剧集/Raw.Release",
        selected_path="/真人剧集",
        metadata={},
        media_metadata=media_metadata,
        ai_plan={"episode_map": [{
            "source_file": "Movie.mkv",
            "season_number": 0,
            "episode_number": 100,
        }]},
        file_tree=[{"name": "Movie.mkv", "relative_path": "Movie.mkv", "is_dir": False}],
    )
    enriched = enrich_media_metadata_with_rename_plan(media_metadata, rename_plan)
    self.assertEqual(rename_plan["operations"][0]["rename_to"], "Someday or One Day S00E100.mkv")
    self.assertEqual(enriched["items"][0]["season_number"], 0)
    self.assertTrue(enriched["items"][0]["final_path"].endswith("Someday or One Day S00E100.mkv"))

def test_missing_ai_season_does_not_default_to_locked_season_zero(self):
    media_metadata = self._confirmed_media_metadata()
    plan = build_confirmed_rename_plan(
        final_path="/真人剧集/Raw.Release",
        selected_path="/真人剧集",
        metadata={},
        media_metadata=media_metadata,
        ai_plan={"episode_map": [{"source_file": "Movie.mkv", "episode_number": 100}]},
        file_tree=[{"name": "Movie.mkv", "relative_path": "Movie.mkv", "is_dir": False}],
    )
    self.assertIsNone(plan)
```

The fixture uses Task 1's complete confirmed contract with `relation.target_series.english_title = "Someday or One Day"`.

- [ ] **Step 2: Run the test and verify signature/schema failures**

Run: `python3 -m unittest tests.test_tvdb_rename -v`

Expected: FAIL because the builder still expects flat confirmed-plan fields and no enrichment helper exists.

- [ ] **Step 3: Replace confirmed builder field access and operation results**

```python
from app.core.media_metadata import (
    merge_resolved_items,
    series_folder_name,
    series_titles,
)


def build_confirmed_rename_plan(
    final_path: str,
    selected_path: str,
    metadata: dict,
    media_metadata: dict,
    ai_plan: dict,
    file_tree: list[dict],
) -> dict | None:
    placement = media_metadata.get("placement") or {}
    identity = media_metadata.get("identity") or {}
    if media_metadata.get("confirmed") is not True or placement.get("library_type") != "series":
        return None

    allowed_targets = set()
    for item in media_metadata.get("items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("season_number") is None or str(item.get("season_number")).strip() == "":
            continue
        season = _safe_season_int(item.get("season_number"))
        episode = _safe_episode_int(item.get("episode_number"))
        if season is not None and episode is not None:
            allowed_targets.add((season, episode))
    if not allowed_targets:
        season = _safe_season_int(placement.get("season_number"))
        episode = _safe_episode_int(placement.get("episode_number"))
        if season is None or episode is None:
            return None
        allowed_targets.add((season, episode))

    source_lookup = _source_index(file_tree)
    source_video_paths = {node["relative_path"] for node in _video_file_nodes(file_tree)}
    chinese_title, english_title = series_titles(media_metadata)
    series_name = english_title or chinese_title
    if not series_name:
        return None

    target_root = _join_path(selected_path, series_folder_name(media_metadata))
    operations = []
    seen_sources = set()
    seen_targets = set()
    for item in ai_plan.get("episode_map") or []:
        if not isinstance(item, dict):
            continue
        if item.get("season_number") is None or str(item.get("season_number")).strip() == "":
            continue
        source_node = source_lookup.get(_clean_path(item.get("source_file") or ""))
        season = _safe_season_int(item.get("season_number"))
        episode = _safe_episode_int(item.get("episode_number"))
        if not source_node or (season, episode) not in allowed_targets:
            continue
        source_relative_path = source_node["relative_path"]
        marker = _episode_marker_text(season, episode)
        suffix = PurePosixPath(source_relative_path).suffix
        rename_to = f"{series_name} {marker}{suffix}"
        target_dir = _join_path(target_root, f"{series_name} Season {season:02d}")
        target_relative_path = _join_path(f"{series_name} Season {season:02d}", rename_to)
        resolved_path = _join_path(target_dir, rename_to)
        if source_relative_path in seen_sources or resolved_path in seen_targets:
            continue
        seen_sources.add(source_relative_path)
        seen_targets.add(resolved_path)
        source_parent = "/".join(source_relative_path.split("/")[:-1])
        operations.append({
            "content_role": item.get("content_role") or identity.get("content_kind"),
            "season_number": season,
            "episode_number": episode,
            "source_relative_path": source_relative_path,
            "source_path": _join_path(final_path, source_relative_path),
            "rename_to": rename_to,
            "renamed_source_path": _join_path(final_path, source_parent, rename_to),
            "target_dir": target_dir,
            "target_relative_path": target_relative_path,
            "final_path": resolved_path,
        })
    if not operations:
        return None
    return {
        "target_root": target_root,
        "series_name": series_name,
        "operations": operations,
        "unmatched_sources": sorted(source_video_paths - seen_sources),
        "warnings": [str(item) for item in media_metadata.get("warnings") or [] if str(item).strip()],
    }
```

Add the enrichment helper:

```python
def enrich_media_metadata_with_rename_plan(media_metadata: dict, rename_plan: dict) -> dict:
    resolved = [{
        "content_role": operation.get("content_role"),
        "season_number": operation["season_number"],
        "episode_number": operation["episode_number"],
        "source_relative_path": operation["source_relative_path"],
        "final_path": operation["final_path"],
    } for operation in rename_plan.get("operations") or []]
    return merge_resolved_items(media_metadata, resolved)
```

- [ ] **Step 4: Run the pure rename tests**

Run: `python3 -m unittest tests.test_tvdb_rename -v`

Expected: all tests PASS, including partial mapping and `S00E100` formatting.

- [ ] **Step 5: Commit the pure renaming contract**

```bash
git add app/utils/tvdb_rename.py tests/test_tvdb_rename.py
git commit -m "feat(renaming): bind files to core media metadata"
```

---

### Task 6: Renaming Processor Returns Enriched Contract

**Files:**
- Modify: `app/modules/renaming.py`
- Modify: `app/utils/ai.py`
- Delete: `app/utils/confirmed_download_plan.py`
- Modify: `tests/test_composable_renaming.py`
- Delete: `tests/test_confirmed_download_plan.py`

**Interfaces:**
- Consumes: Task 1 `extract_confirmed_media_metadata` and Task 5 rename plan.
- Produces: terminal `PostDownloadResult.metadata` containing the same `metadata_id` plus resolved `items[].final_path`.

- [ ] **Step 1: Write failing processor enrichment assertions**

In the existing confirmed temporary-Special test, replace its fixture key with `media_metadata` and add:

```python
self.assertTrue(result.handled)
self.assertTrue(result.should_stop)
self.assertEqual(result.metadata["media_metadata"]["metadata_id"], "metadata-a")
self.assertTrue(result.metadata["media_metadata"]["items"][0]["final_path"].endswith("S00E100.mkv"))
self.assertNotIn("_".join(("download", "plan")), result.metadata)
```

Replace the old partial nested value with the complete Task 1 contract fixture.
The mocked rename operation must also match Task 5's resolved shape so the
enrichment helper is exercised rather than raising `KeyError`:

```python
rename_plan["operations"][0].update({
    "content_role": "extension_movie",
    "season_number": 0,
    "episode_number": 100,
    "source_relative_path": "Movie.mkv",
    "target_relative_path": "Someday or One Day Season 00/Someday or One Day S00E100.mkv",
    "final_path": "/真人剧集/想见你 (Someday or One Day)/Someday or One Day Season 00/Someday or One Day S00E100.mkv",
})
```

- [ ] **Step 2: Run the processor tests and observe old extractor/key failures**

Run: `python3 -m unittest tests.test_composable_renaming -v`

Expected: FAIL because renaming still imports `extract_confirmed_download_plan` and does not return enriched metadata.

- [ ] **Step 3: Replace imports and confirmed extraction**

```python
from app.core.media_metadata import (
    MEDIA_METADATA_KEY,
    attach_media_metadata,
    extract_confirmed_media_metadata,
)
from app.utils.tvdb_rename import (
    VIDEO_EXTENSIONS,
    build_confirmed_rename_plan,
    build_tvdb_rename_plan,
    enrich_media_metadata_with_rename_plan,
)


def _media_metadata_state(event: DownloadCompletedEvent):
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    present = MEDIA_METADATA_KEY in metadata
    return extract_confirmed_media_metadata(metadata), present


def _confirmed_series_metadata(event: DownloadCompletedEvent):
    contract = extract_confirmed_media_metadata(event.metadata)
    placement = contract.get("placement") if isinstance(contract, dict) else None
    if (
        not isinstance(placement, dict)
        or placement.get("library_type") != "series"
    ):
        return None
    return contract
```

At the start of `process_tvdb_episode`, inspect `_media_metadata_state(event)`.
If the key is present but validation fails, return a handled, `should_stop=True`
result that leaves files in place, preserves `event.metadata`, and says the
contract is invalid/unsupported. It must not enter any legacy inference path.

```python
media_metadata, contract_present = _media_metadata_state(event)
if contract_present and media_metadata is None:
    return PostDownloadResult(
        True,
        final_path=event.final_path,
        message="⚠️ media_metadata 无效或版本不受支持；文件保持原位。",
        should_stop=True,
        metadata=event.metadata,
    )
confirmed_series = _confirmed_series_metadata(event)
```

Rename every local `confirmed_plan` variable to `media_metadata`. Pass it to
`build_confirmed_rename_plan(media_metadata=media_metadata, ...)`. Every valid
contract-bound series uses the locked builder: Special mappings use the
top-level Season 00 lock, while an ordinary primary series uses its confirmed
`items` locks such as S01E01. A valid standalone movie skips TVDB episode
inference and continues to contract-derived movie naming. Only downloads with
no `media_metadata` may enter the legacy unconstrained inference path.

```python
def _attempt_tvdb_ai_episode_rename(event, metadata):
    media_metadata, contract_present = _media_metadata_state(event)
    confirmed_series = _confirmed_series_metadata(event)
    if contract_present:
        if confirmed_series:
            return _attempt_confirmed_series_rename(event, metadata, confirmed_series)
        return None
    return _attempt_legacy_tvdb_ai_episode_rename(event, metadata)
```

Extract the current confirmed branch into
`_attempt_confirmed_series_rename`; do not duplicate its file operations. Add
an S01E01 fixture where constrained AI proposes S01E02 and assert the locked
builder rejects it and the source enters the confirmed-failure path without
renumbering.

- [ ] **Step 4: Update the constrained AI context name**

Replace prompt rule 8 with:

```text
8. 如果输入包含 confirmed_media_metadata，target_series、library_type、category_kind、season_number 和 episode_number 都是已确认锁，禁止改写。
```

The context dictionary uses:

```python
context = {
    "metadata": metadata,
    "confirmed_media_metadata": media_metadata,
    "release_title": metadata.get("release_title") or event.resource_name,
    "resource_name": event.resource_name,
    "download_path": event.final_path,
    "file_tree": file_tree,
    "tvdb_candidates": tvdb_candidates,
    "tvdb_episodes": tvdb_episodes,
}
```

- [ ] **Step 5: Enrich successful terminal results**

After confirmed file operations succeed:

```python
enriched = enrich_media_metadata_with_rename_plan(media_metadata, rename_plan)
rename_plan["media_metadata"] = enriched
return rename_plan
```

Build the result as:

```python
result_metadata = event.metadata
if rename_plan.get("media_metadata"):
    result_metadata = attach_media_metadata(event.metadata, rename_plan["media_metadata"])
return PostDownloadResult(
    True,
    final_path=rename_plan["target_root"],
    message=message,
    should_stop=True,
    metadata=result_metadata,
)
```

Confirmed mapping failure continues to move the source to unorganized storage but does not synthesize resolved items. This prevents Plex from treating an unorganized failure as a completed Special.

For a successful generic/legacy path carrying a valid standalone contract,
preserve `event.metadata` in `PostDownloadResult.metadata`. Do not add inferred
episode locks to the core contract after confirmation.

```python
return PostDownloadResult(
    True,
    final_path=target_path,
    message=message,
    should_stop=True,
    metadata=event.metadata,
)
```

- [ ] **Step 6: Delete the feature-owned confirmed-plan reader**

Delete `app/utils/confirmed_download_plan.py` and `tests/test_confirmed_download_plan.py`; all validation now lives in core.

- [ ] **Step 7: Run renaming regressions**

Run: `python3 -m unittest tests.test_core_media_metadata tests.test_media_auto_rename tests.test_tvdb_rename tests.test_composable_renaming -v`

Expected: all tests PASS; successful confirmed Specials carry resolved items;
all four standalone category fixtures preserve their contract through generic
renaming; an invalid present contract never falls back to legacy behavior; and
downloads with no contract retain existing behavior.

- [ ] **Step 8: Commit the renaming processor**

```bash
git add app/modules/renaming.py app/utils/ai.py app/utils/tvdb_rename.py tests/test_composable_renaming.py
git rm app/utils/confirmed_download_plan.py tests/test_confirmed_download_plan.py
git commit -m "feat(renaming): return resolved media metadata"
```

---

### Task 7: Plex Adapter Locates and Edits Exact Specials

**Files:**
- Modify: `app/adapters/plex.py`
- Create: `tests/test_plex_media_metadata_adapter.py`

**Interfaces:**
- Produces: `find_series_episode(..., expected_final_paths=()) -> dict | None` and `edit_custom_episode_metadata(...) -> dict`.

- [ ] **Step 1: Write failing adapter tests**

```python
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.adapters.plex import PlexAdapter


class PlexMediaMetadataAdapterTest(unittest.TestCase):
    def _adapter(self):
        episode = Mock()
        episode.ratingKey = "100"
        episode.title = "Episode 100"
        episode.originalTitle = ""
        episode.year = 2022
        episode.type = "episode"
        episode.summary = ""
        episode.guids = []
        part = Mock()
        part.id = 1
        part.file = "/mnt/media/真人剧集/想见你/Someday or One Day Season 00/Someday or One Day S00E100.mkv"
        part.audioStreams.return_value = []
        part.subtitleStreams.return_value = []
        episode.media = [SimpleNamespace(parts=[part])]
        episode.reload.return_value = episode
        show = Mock()
        show.guids = [SimpleNamespace(id="tvdb://series-1")]
        show.episode.return_value = episode
        section = Mock()
        section.search.return_value = [show]
        adapter = PlexAdapter.__new__(PlexAdapter)
        adapter.server = Mock()
        adapter.server.library.sectionByID.return_value = section
        adapter.server.fetchItem.return_value = episode
        return adapter, show, episode

    def test_find_series_episode_uses_series_id_and_locked_number(self):
        adapter, show, _episode = self._adapter()
        result = adapter.find_series_episode(
            "13",
            tvdb_series_id="series-1",
            title="Someday or One Day",
            year="2019",
            season_number=0,
            episode_number=100,
            expected_final_paths=[
                "/真人剧集/想见你/Someday or One Day Season 00/Someday or One Day S00E100.mkv"
            ],
        )
        self.assertEqual(result["rating_key"], "100")
        show.episode.assert_called_once_with(season=0, episode=100)

    def test_find_series_episode_rejects_wrong_media_part_path(self):
        adapter, _show, _episode = self._adapter()
        result = adapter.find_series_episode(
            "13",
            tvdb_series_id="series-1",
            title="Someday or One Day",
            year="2019",
            season_number=0,
            episode_number=100,
            expected_final_paths=["/真人剧集/wrong/S00E100.mkv"],
        )
        self.assertIsNone(result)

    def test_edit_custom_episode_writes_only_supported_fields(self):
        adapter, _show, episode = self._adapter()
        adapter.edit_custom_episode_metadata(
            "100",
            title="想见你：电影版",
            summary="电影版延续电视剧故事。",
            original_release_date="2022-12-24",
            year="2022",
        )
        episode.editTitle.assert_called_once_with("想见你：电影版", locked=True)
        episode.editSummary.assert_called_once_with("电影版延续电视剧故事。", locked=True)
        episode.editOriginallyAvailable.assert_called_once_with("2022-12-24", locked=True)
```

- [ ] **Step 2: Run tests and verify missing adapter methods**

Run: `python3 -m unittest tests.test_plex_media_metadata_adapter -v`

Expected: FAIL because both adapter methods are missing.

- [ ] **Step 3: Implement exact series/Special location**

```python
def find_series_episode(
    self,
    library_id,
    *,
    tvdb_series_id="",
    title="",
    year="",
    season_number=0,
    episode_number=0,
    expected_final_paths=(),
):
    section = self.server.library.sectionByID(int(library_id))
    kwargs = {"libtype": "show"}
    if year:
        kwargs["year"] = int(year)
    shows = section.search(title=str(title or "") or None, **kwargs)
    expected_guid = f"tvdb://{tvdb_series_id}" if tvdb_series_id else ""
    if expected_guid:
        shows = [
            show for show in shows
            if expected_guid in {
                str(self._value(guid, "id", "") or "")
                for guid in getattr(show, "guids", []) or []
            }
        ]
    if len(shows) != 1:
        return None
    try:
        episode = shows[0].episode(
            season=int(season_number),
            episode=int(episode_number),
        )
    except Exception:
        return None
    item = self._item_dict(episode)
    expected = [str(path or "").replace("\\", "/").rstrip("/") for path in expected_final_paths if str(path or "").strip()]
    actual = [str(part.get("file") or "").replace("\\", "/").rstrip("/") for part in item.get("parts") or []]
    if expected and not any(
        actual_path == expected_path or actual_path.endswith(expected_path)
        for actual_path in actual
        for expected_path in expected
    ):
        return None
    return item
```

- [ ] **Step 4: Implement supported custom fields**

```python
def edit_custom_episode_metadata(
    self,
    rating_key,
    *,
    title="",
    summary="",
    original_release_date="",
    year="",
):
    item = self.server.fetchItem(int(rating_key))
    if title:
        item.editTitle(str(title), locked=True)
    if summary:
        item.editSummary(str(summary), locked=True)
    if original_release_date:
        item.editOriginallyAvailable(str(original_release_date), locked=True)
    elif year:
        item.editField("year", int(year), locked=True)
    return self._item_dict(item.reload())
```

Poster updates continue to use the existing `set_poster_url` method so text/date and artwork remain separately recoverable Plex steps.

- [ ] **Step 5: Run adapter tests**

Run: `python3 -m unittest tests.test_plex_media_metadata_adapter tests.test_plex_adapters -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit the Plex adapter**

```bash
git add app/adapters/plex.py tests/test_plex_media_metadata_adapter.py
git commit -m "feat(plex): locate and edit confirmed specials"
```

---

### Task 8: Plex Management Consumes Contract Without Reclassification

**Files:**
- Modify: `app/services/plex_management.py`
- Modify: `tests/test_plex_management.py`

**Interfaces:**
- Consumes: confirmed `media_metadata` persisted inside a Plex job payload.
- Produces: contract-bound routing for all four categories, exact Special location, official/inferred verification, standalone automatic matching, and temporary custom metadata behavior.

- [ ] **Step 1: Add failing official, inferred, and temporary tests**

Add helpers that build a completion with a resolved contract item and category route `kind=live_action_series`. Add these assertions:

```python
def make_media_metadata_completion(mapping_kind):
    from app.core.media_metadata import attach_media_metadata
    from app.core.module_registry import (
        DownloadCompletedEvent,
        DownloadPipelineCompletion,
        PostDownloadResult,
    )

    episode = 100 if mapping_kind == "temporary_related_special" else 5
    episode_marker = f"E{episode:03d}" if episode >= 100 else f"E{episode:02d}"
    contract = {
        "schema_version": 1,
        "metadata_id": "metadata-a",
        "confirmed": True,
        "identity": {
            "chinese_title": "想见你",
            "english_title": "Someday or One Day The Movie",
            "year": "2022",
            "content_kind": "extension_movie",
            "summary": "电影版延续电视剧故事。",
            "original_release_date": "2022-12-24",
            "poster_url": "https://image.example/poster.jpg",
            "poster_source": "douban",
            "external_ids": {},
        },
        "relation": {
            "type": "sequel",
            "target_series": {
                "chinese_title": "想见你",
                "english_title": "Someday or One Day",
                "year": "2019",
                "external_ids": {"tvdb": "series-1"},
            },
            "source": "wikipedia",
        },
        "placement": {
            "library_type": "series",
            "category_kind": "live_action_series",
            "season_number": 0,
            "episode_number": episode,
            "mapping_kind": mapping_kind,
            "mapping_source": "tvdb" if mapping_kind == "tvdb_official" else "ai",
            "tvdb_episode_id": "episode-5" if mapping_kind == "tvdb_official" else "",
        },
        "source_entry": {
            "title": "想见你 (电影)",
            "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
            "provider": "wikipedia",
            "availability": "ok",
            "verification": "verified",
        },
        "items": [{
            "content_role": "extension_movie",
            "season_number": 0,
            "episode_number": episode,
            "final_path": f"/真人剧集/想见你 (Someday or One Day)/Someday or One Day Season 00/Someday or One Day S00{episode_marker}.mkv",
        }],
        "evidence": {},
        "warnings": ["TVDB编号尚未实时验证"] if mapping_kind == "ai_inferred_tvdb" else [],
    }
    if mapping_kind == "standalone":
        contract["identity"]["content_kind"] = "movie"
        contract["relation"]["target_series"] = {}
        contract["placement"].update({
            "library_type": "movie",
            "category_kind": "live_action_movie",
            "season_number": None,
            "episode_number": None,
        })
        contract["items"] = []
    metadata = attach_media_metadata({}, contract)
    event = DownloadCompletedEvent(
        link="magnet:?xt=urn:btih:" + "a" * 40,
        selected_path="/真人剧集" if contract["placement"]["library_type"] == "series" else "/真人电影",
        user_id=7,
        final_path="/download/raw",
        resource_name="Media.Release",
        provider="115",
        metadata=metadata,
    )
    result = PostDownloadResult(
        True,
        final_path=contract["items"][0]["final_path"].rsplit("/", 1)[0] if contract["items"] else "/真人电影/想见你",
        should_stop=True,
        metadata=metadata,
    )
    return DownloadPipelineCompletion(
        event=event,
        result=result,
        terminal_processor="renaming.media_metadata",
    )


def make_four_category_routes():
    return [
        {"kind": "live_action_series", "path": "/真人剧集", "plex_library_id": "11"},
        {"kind": "live_action_movie", "path": "/真人电影", "plex_library_id": "12"},
        {"kind": "animated_movie", "path": "/动画电影", "plex_library_id": "13"},
        {"kind": "animated_series", "path": "/动画剧集", "plex_library_id": "14"},
    ]


def make_service(self, *, plex=None, tmdb=None, notifier=None, category_folders=None,
                 scan_poll_interval=0, scan_timeout=0):
    from app.services.plex_management import PlexManagementService

    return PlexManagementService(
        self.jobs,
        plex or FakePlex(),
        tmdb=tmdb or FakeTmdb(),
        category_folders=category_folders or make_four_category_routes(),
        scan_poll_interval=scan_poll_interval,
        scan_timeout=scan_timeout,
        sleeper=lambda _: None,
        notifier=notifier,
    )


def test_temporary_special_routes_by_kind_and_writes_custom_metadata(self):
    plex = FakePlex()
    plex.find_series_episode = Mock(return_value={
        "rating_key": "42", "title": "Episode 100", "guids": [], "media_type": "episode",
    })
    plex.edit_custom_episode_metadata = Mock(return_value={"rating_key": "42"})
    service = self.make_service(plex=plex)
    job = service.enqueue_completion(make_media_metadata_completion("temporary_related_special"))
    result = service.run_job(job["id"])
    self.assertEqual(result["state"], "completed")
    plex.find_series_episode.assert_called_once()
    plex.edit_custom_episode_metadata.assert_called_once()
    self.assertNotIn("list_match_candidates", plex.calls)

def test_temporary_special_wrong_final_path_fails_before_any_write(self):
    plex = FakePlex()
    plex.find_series_episode = Mock(return_value=None)
    plex.edit_custom_episode_metadata = Mock()
    plex.refresh_zh_cn = Mock()
    plex.set_poster_url = Mock()
    plex.fix_match = Mock()
    service = self.make_service(plex=plex, scan_timeout=0)
    job = service.enqueue_completion(make_media_metadata_completion("temporary_related_special"))
    result = service.run_job(job["id"])
    self.assertEqual(result["state"], "failed")
    plex.edit_custom_episode_metadata.assert_not_called()
    plex.refresh_zh_cn.assert_not_called()
    plex.set_poster_url.assert_not_called()
    plex.fix_match.assert_not_called()

def test_official_special_verifies_tvdb_episode_without_custom_write(self):
    plex = FakePlex()
    official_item = {
        "rating_key": "42", "title": "Official", "guids": ["tvdb://episode-5"], "media_type": "episode",
    }
    plex.find_series_episode = Mock(return_value=official_item)
    plex.get_item = Mock(return_value=official_item)
    plex.edit_custom_episode_metadata = Mock()
    service = self.make_service(plex=plex)
    job = service.enqueue_completion(make_media_metadata_completion("tvdb_official"))
    result = service.run_job(job["id"])
    self.assertEqual(result["state"], "completed")
    plex.edit_custom_episode_metadata.assert_not_called()

def test_ai_inferred_special_fails_without_tvdb_guid_and_never_renumbers(self):
    plex = FakePlex()
    plex.find_series_episode = Mock(return_value={
        "rating_key": "42", "title": "Unverified", "guids": [], "media_type": "episode",
    })
    service = self.make_service(plex=plex)
    job = service.enqueue_completion(make_media_metadata_completion("ai_inferred_tvdb"))
    result = service.run_job(job["id"])
    self.assertEqual(result["state"], "failed")
    self.assertEqual(job["payload"]["metadata"]["media_metadata"]["placement"]["episode_number"], 5)

def test_ai_inferred_special_recovers_after_tvdb_identity_appears(self):
    plex = FakePlex()
    plex.find_series_episode = Mock(return_value={
        "rating_key": "42", "title": "Unverified", "guids": [], "media_type": "episode",
    })
    plex.get_item = Mock(return_value={
        "rating_key": "42", "title": "Unverified", "guids": [], "media_type": "episode",
    })
    service = self.make_service(plex=plex)
    job = service.enqueue_completion(make_media_metadata_completion("ai_inferred_tvdb"))
    original_path = job["payload"]["final_path"]
    self.assertEqual(service.run_job(job["id"])["state"], "failed")
    plex.get_item.return_value = {
        "rating_key": "42", "title": "Verified", "guids": ["tvdb://episode-5"], "media_type": "episode",
    }
    retried = service.retry_job(job["id"])
    self.assertEqual(retried["state"], "completed")
    self.assertEqual(retried["payload"]["final_path"], original_path)
    self.assertEqual(retried["payload"]["metadata"]["media_metadata"]["placement"]["episode_number"], 5)

def test_all_four_standalone_categories_route_without_reclassification(self):
    routes = {
        "live_action_series": ("series", "11"),
        "live_action_movie": ("movie", "12"),
        "animated_movie": ("movie", "13"),
        "animated_series": ("series", "14"),
    }
    service = self.make_service(category_folders=make_four_category_routes())
    for category_kind, (library_type, library_id) in routes.items():
        with self.subTest(category_kind=category_kind):
            completion = make_media_metadata_completion("standalone")
            contract = completion.event.metadata["media_metadata"]
            contract["relation"]["target_series"] = {}
            contract["placement"].update({
                "mapping_kind": "standalone",
                "category_kind": category_kind,
                "library_type": library_type,
                "season_number": None,
                "episode_number": None,
            })
            if library_type == "series":
                contract["identity"]["content_kind"] = "series"
                contract["items"] = [{
                    "content_role": "main_episode",
                    "season_number": 1,
                    "episode_number": 1,
                    "final_path": completion.result.final_path + "/Series S01E01.mkv",
                }]
            else:
                contract["identity"]["content_kind"] = "movie"
                contract["items"] = []
            job = service.enqueue_completion(completion)
            self.assertEqual(service._route_library(job), library_id)

def test_special_location_polls_until_plex_scan_exposes_episode(self):
    plex = FakePlex()
    plex.find_series_episode = Mock(side_effect=[None, {
        "rating_key": "42", "title": "Episode 100", "guids": [], "media_type": "episode",
    }])
    service = self.make_service(plex=plex, scan_poll_interval=0, scan_timeout=1)
    job = service.enqueue_completion(make_media_metadata_completion("temporary_related_special"))
    result = service.run_job(job["id"])
    self.assertEqual(result["state"], "completed")
    self.assertEqual(plex.find_series_episode.call_count, 2)

def test_standalone_ignores_non_plex_ids_and_verifies_by_title_year(self):
    completion = make_media_metadata_completion("standalone")
    identity = completion.event.metadata["media_metadata"]["identity"]
    identity.update({
        "chinese_title": "电影",
        "english_title": "Movie",
        "year": "2024",
    })
    identity["external_ids"] = {"douban_subject": "123"}
    service = self.make_service()
    job = service.enqueue_completion(completion)
    result = service.run_job(job["id"])
    self.assertEqual(result["state"], "completed")
    self.assertEqual(result["step_results"]["matching"]["action"], "verified_by_title_year")
```

- [ ] **Step 2: Run Plex service tests and verify current legacy behavior fails**

Run: `python3 -m unittest tests.test_plex_management -v`

Expected: FAIL because routing uses selected paths, location uses recent candidates, and temporary mappings enter match confirmation.

- [ ] **Step 3: Add core contract helpers to the service**

```python
from app.core.media_metadata import (
    MEDIA_METADATA_KEY,
    SERIES_EPISODE_MAPPINGS,
    extract_confirmed_media_metadata,
    resolve_category_route,
)


def _media_metadata(self, job):
    metadata = (job.get("payload") or {}).get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    contract = extract_confirmed_media_metadata(metadata)
    if MEDIA_METADATA_KEY in metadata and contract is None:
        raise ValueError("Invalid or unsupported media_metadata contract")
    return contract


def _effective_metadata(self, job):
    contract = self._media_metadata(job)
    if not contract:
        return (job.get("payload") or {}).get("metadata") or {}
    identity = dict(contract.get("identity") or {})
    identity["title"] = identity.get("chinese_title") or identity.get("english_title") or ""
    identity["original_title"] = identity.get("english_title") or ""
    identity["media_type"] = (
        "tv" if contract["placement"]["library_type"] == "series" else "movie"
    )
    return identity
```

- [ ] **Step 4: Route contract jobs by category kind**

```python
def _route_library(self, job):
    contract = self._media_metadata(job)
    if contract:
        route = resolve_category_route(
            {"category_folder": self.category_folders},
            contract["placement"]["category_kind"],
        )
        if not route or not route.get("plex_library_id"):
            raise LookupError(f"No Plex library route for {contract['placement']['category_kind']}")
        return route["plex_library_id"]
    selected_path = str(job["payload"].get("selected_path") or "").rstrip("/")
    matches = []
    for entry in self.category_folders:
        path = str(entry.get("path") or "").rstrip("/")
        library_id = str(entry.get("plex_library_id") or "").strip()
        if path and library_id and (selected_path == path or selected_path.startswith(path + "/")):
            matches.append((len(path), library_id))
    if not matches:
        raise LookupError(f"No Plex library route for {selected_path}")
    return max(matches)[1]
```

- [ ] **Step 5: Locate exact confirmed series episodes**

At the start of `_locate` after reading the scan result:

```python
contract = self._media_metadata(job)
if contract and contract["placement"]["mapping_kind"] in SERIES_EPISODE_MAPPINGS:
    placement = contract["placement"]
    target = (contract.get("relation") or {}).get("target_series") or {}
    expected_final_paths = [
        item.get("final_path")
        for item in contract.get("items") or []
        if item.get("final_path")
        and item.get("season_number") is not None
        and item.get("episode_number") is not None
        and int(item.get("season_number")) == int(placement["season_number"])
        and int(item.get("episode_number")) == int(placement["episode_number"])
    ]
    if not expected_final_paths:
        raise LookupError("Confirmed Plex Special has no resolved final path")
    deadline = self._clock() + self.scan_timeout
    item = None
    while item is None:
        item = self.plex.find_series_episode(
            library_id,
            tvdb_series_id=((target.get("external_ids") or {}).get("tvdb") or ""),
            title=target.get("english_title") or target.get("chinese_title") or "",
            year=target.get("year") or "",
            season_number=placement["season_number"],
            episode_number=placement["episode_number"],
            expected_final_paths=expected_final_paths,
        )
        if item is not None or self._clock() >= deadline:
            break
        self._sleep(self.scan_poll_interval)
    if not item:
        raise LookupError("Confirmed Plex Special was not found")
    self.jobs.update(job["id"], rating_key=str(item["rating_key"]))
    return {"status": "success", "rating_key": str(item["rating_key"]), "candidates": [item]}
```

For the remaining standalone path, replace the direct payload read with
`metadata = self._effective_metadata(job)`. Keep the existing title/year
candidate selection, but if a contract is present and the candidates are
ambiguous, raise `LookupError` so the job is recoverable; only a download with
no contract may raise `WaitingForMatchConfirmation`. Thus a contract-bound
location never asks for a second confirmation.

- [ ] **Step 6: Branch matching by mapping kind**

```python
def _match(self, job):
    rating_key = str(job.get("rating_key") or "")
    if not rating_key:
        raise LookupError("Plex rating key is missing")
    contract = self._media_metadata(job)
    if contract:
        mapping_kind = contract["placement"]["mapping_kind"]
        item = self.plex.get_item(rating_key)
        if mapping_kind == "temporary_related_special":
            return {"status": "unchanged", "action": "custom_metadata_pending", "item": item}
        if mapping_kind == "tvdb_official":
            expected = {"tvdb": str(contract["placement"]["tvdb_episode_id"])}
            if not plex_rules.external_ids_match(expected, item.get("guids")):
                raise RuntimeError("Official Plex Special does not match confirmed TVDB episode")
            return {"status": "success", "action": "verified", "item": item}
        if mapping_kind == "ai_inferred_tvdb":
            if not any(str(guid).startswith("tvdb://") for guid in item.get("guids") or []):
                raise RuntimeError("AI-inferred Special is still not verified by TVDB")
            return {"status": "success", "action": "verified_after_scan", "item": item}
        if mapping_kind == "standalone":
            expected = {
                source: str(value)
                for source, value in ((contract.get("identity") or {}).get("external_ids") or {}).items()
                if source in {"imdb", "tmdb", "tvdb"} and str(value).strip()
            }
            if expected and plex_rules.external_ids_match(expected, item.get("guids")):
                return {"status": "success", "action": "verified", "item": item}
            if expected:
                candidates = self.plex.list_match_candidates(
                    rating_key,
                    title=(contract.get("identity") or {}).get("english_title")
                    or (contract.get("identity") or {}).get("chinese_title"),
                    year=(contract.get("identity") or {}).get("year"),
                )
                exact = plex_rules.choose_exact_match(expected, candidates)
                if exact is None:
                    raise RuntimeError("Standalone Plex match could not be verified")
                fixed = self.plex.fix_match(rating_key, exact["guid"])
                if not plex_rules.external_ids_match(expected, fixed.get("guids")):
                    raise RuntimeError("Standalone Plex match verification failed")
                return {"status": "success", "action": "fixed", "item": fixed}
            expected_identity = self._candidate_identity(self._effective_metadata(job))
            if self._candidate_identity(item) != expected_identity:
                raise RuntimeError("Standalone Plex title/year could not be verified")
            return {"status": "success", "action": "verified_by_title_year", "item": item}
    return self._legacy_match(job, rating_key)
```

Move the current non-contract body of `_match` into `_legacy_match(job, rating_key)` unchanged.

- [ ] **Step 7: Write temporary text/date and poster without changing placement**

At the start of `_localize`:

```python
contract = self._media_metadata(job)
mapping_kind = (contract.get("placement") or {}).get("mapping_kind") if contract else ""
if mapping_kind in {"tvdb_official", "ai_inferred_tvdb"}:
    return {"status": "unchanged", "action": "official_metadata_preserved"}
if mapping_kind == "temporary_related_special":
    identity = contract["identity"]
    item = self.plex.edit_custom_episode_metadata(
        job["rating_key"],
        title=identity.get("chinese_title") or identity.get("english_title") or "",
        summary=identity.get("summary") or "",
        original_release_date=identity.get("original_release_date") or "",
        year=identity.get("year") or "",
    )
    return {"status": "success", "action": "custom_metadata", "item": item}
```

At the start of `_artwork`:

```python
contract = self._media_metadata(job)
mapping_kind = (contract.get("placement") or {}).get("mapping_kind") if contract else ""
if mapping_kind in {"tvdb_official", "ai_inferred_tvdb"}:
    return {"status": "unchanged", "action": "official_artwork_preserved"}
if mapping_kind == "temporary_related_special":
    poster_url = str((contract.get("identity") or {}).get("poster_url") or "").strip()
    if not poster_url:
        return {"status": "unchanged", "message": "No confirmed custom poster"}
    self.plex.set_poster_url(job["rating_key"], poster_url)
    return {
        "status": "success",
        "selected": {
            "url": poster_url,
            "source": (contract.get("identity") or {}).get("poster_source") or "media_metadata",
        },
    }
```

Use `_effective_metadata(job)` in legacy match/artwork/stream code instead of directly reading the payload metadata.

Extend the official and inferred tests to assert `refresh_zh_cn`,
`set_poster_url`, `edit_custom_episode_metadata`, and `fix_match` are never
called. The temporary wrong-final-path test must fail in `locating` and assert
all four write methods remain untouched.

- [ ] **Step 8: Run Plex service regressions**

Run: `python3 -m unittest tests.test_plex_management tests.test_plex_rules tests.test_plex_jobs -v`

Expected: contract tests and all legacy Plex tests PASS; all four standalone
category routes are automatic; no contract-bound test waits for classification,
location, or match confirmation; official/inferred/temporary Specials retain
their locked Season 00 behavior.

- [ ] **Step 9: Commit Plex contract consumption**

```bash
git add app/services/plex_management.py tests/test_plex_management.py
git commit -m "feat(plex): consume confirmed media metadata"
```

---

### Task 9: Completion Hook Persists Only Resolved Contract Jobs

**Files:**
- Modify: `app/modules/plex_management.py`
- Modify: `app/services/plex_management.py`
- Modify: `tests/test_plex_module.py`
- Modify: `tests/test_plex_management_integration.py`

**Interfaces:**
- Consumes: enriched `DownloadPipelineCompletion` from Task 6.
- Produces: an idempotent Plex job keyed by `metadata_id`; locked Specials require resolved items, standalone media require a terminal path, and invalid/unorganized contract failures are skipped.

- [ ] **Step 1: Add failing hook/payload tests**

```python
def test_contract_completion_persists_metadata_id_and_resolved_items(self):
    completion = make_media_metadata_completion("temporary_related_special")
    service = self.make_service()
    job = service.enqueue_completion(completion)
    contract = job["payload"]["metadata"]["media_metadata"]
    self.assertEqual(contract["metadata_id"], "metadata-a")
    self.assertTrue(contract["items"][0]["final_path"].endswith("S00E100.mkv"))

def test_unresolved_contract_completion_is_not_enqueued(self):
    completion = make_media_metadata_completion("temporary_related_special")
    completion.event.metadata["media_metadata"]["items"] = []
    completion.result.metadata = completion.event.metadata
    self.assertIsNone(self.make_service().enqueue_completion(completion))

def test_standalone_contract_uses_terminal_path_without_inventing_items(self):
    completion = make_media_metadata_completion("standalone")
    contract = completion.event.metadata["media_metadata"]
    contract["identity"]["content_kind"] = "movie"
    contract["relation"]["target_series"] = {}
    contract["placement"].update({
        "library_type": "movie",
        "category_kind": "live_action_movie",
        "season_number": None,
        "episode_number": None,
    })
    contract["items"] = []
    completion.result.metadata = completion.event.metadata
    job = self.make_service().enqueue_completion(completion)
    self.assertIsNotNone(job)
    self.assertEqual(job["payload"]["metadata"]["media_metadata"]["metadata_id"], "metadata-a")

def test_present_but_invalid_contract_never_falls_back_to_legacy_job(self):
    completion = make_media_metadata_completion("standalone")
    completion.event.metadata["media_metadata"]["schema_version"] = 999
    completion.result.metadata = completion.event.metadata
    self.assertIsNone(self.make_service().enqueue_completion(completion))

def test_same_metadata_id_is_idempotent_even_if_terminal_path_changes(self):
    service = self.make_service()
    first = make_media_metadata_completion("temporary_related_special")
    second = make_media_metadata_completion("temporary_related_special")
    second.result.final_path = "/retry/changed/path"
    self.assertEqual(
        service.enqueue_completion(first)["id"],
        service.enqueue_completion(second)["id"],
    )
```

- [ ] **Step 2: Run hook/integration tests and verify unresolved jobs are currently accepted**

Run: `python3 -m unittest tests.test_plex_module tests.test_plex_management tests.test_plex_management_integration -v`

Expected: FAIL on unresolved job acceptance and missing metadata-ID identity.

- [ ] **Step 3: Make completion payload prefer terminal metadata and copy the contract**

```python
@staticmethod
def _completion_payload(completion):
    event = completion.event
    metadata = {}
    for value in (event.naming_metadata, event.metadata, completion.result.metadata):
        if isinstance(value, dict):
            metadata.update(deepcopy(value))
    return {
        "provider": str(event.provider or ""),
        "selected_path": str(event.selected_path or ""),
        "final_path": str(completion.result.final_path or event.final_path or ""),
        "resource_name": str(event.resource_name or ""),
        "user_id": int(event.user_id),
        "terminal_processor": str(completion.terminal_processor or ""),
        "metadata": metadata,
    }
```

Import `deepcopy` from `copy`.

- [ ] **Step 4: Reject unresolved contract jobs and key them by metadata ID**

```python
def enqueue_completion(self, completion):
    if not str(completion.terminal_processor or "").startswith("renaming."):
        return None
    payload = self._completion_payload(completion)
    metadata = payload["metadata"]
    contract_present = MEDIA_METADATA_KEY in metadata
    contract = extract_confirmed_media_metadata(metadata)
    if contract_present and contract is None:
        return None
    if contract:
        if contract["placement"]["library_type"] == "series":
            resolved = [item for item in contract.get("items") or [] if item.get("final_path")]
            if not resolved:
                return None
        elif not payload["final_path"]:
            return None
        identity = str(contract["metadata_id"])
    else:
        identity = "\x1f".join((
            payload["provider"],
            payload["final_path"],
            payload["resource_name"],
        ))
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return self.jobs.create_or_get(key, payload)
```

Import `MEDIA_METADATA_KEY` and `extract_confirmed_media_metadata` from core.
Log a concise warning when a
present contract is rejected; do not silently create a legacy job.

- [ ] **Step 5: Extend the integration fake and assert temporary writes**

Add `find_series_episode(..., expected_final_paths=())` and
`edit_custom_episode_metadata` to `IntegrationPlex`; record and assert the
resolved final path passed into lookup. Build the integration completion with a
resolved `media_metadata` fixture, category kind `live_action_series`, and a
route containing `kind` plus `plex_library_id`. Assert:

```python
self.assertEqual(fake_plex.custom_title, "想见你")
self.assertEqual(fake_plex.special_lookup, (0, 100))
self.assertEqual(fake_plex.poster_url, "https://image.example/poster.jpg")
```

- [ ] **Step 6: Run completion/Plex integration tests**

Run: `python3 -m unittest tests.test_download_completion_hooks tests.test_plex_module tests.test_plex_management tests.test_plex_management_integration -v`

Expected: all tests PASS; unresolved locked Specials and invalid present
contracts create no Plex job, while standalone fixtures for each of the four
categories enqueue by `metadata_id` using their terminal path.

- [ ] **Step 7: Commit the completion boundary**

```bash
git add app/modules/plex_management.py app/services/plex_management.py tests/test_plex_module.py tests/test_plex_management.py tests/test_plex_management_integration.py
git commit -m "feat(plex): persist resolved media metadata jobs"
```

---

### Task 10: Main End-to-End Hard Cut and Contract Audit

**Files:**
- Modify: `tests/test_composable_integration.py`
- Modify: `docs/superpowers/specs/2026-07-11-ai-wikipedia-download-planner-design.md`
- Modify: `docs/superpowers/plans/2026-07-11-ai-wikipedia-download-planner.md`
- Modify or rename: every active test/file still using the removed key or removed function names.

**Interfaces:**
- Produces: one full pipeline contract and a repository audit proving no active old-key use remains.

- [ ] **Step 1: Replace the end-to-end test with all three consumers**

```python
async def test_one_media_metadata_id_survives_real_producer_rename_and_plex_job(self):
    import init
    from app.core.media_metadata import attach_media_metadata
    from app.core.module_registry import DownloadCompletedEvent, DownloadRequest, ModuleRegistry
    from app.modules import plex_management, renaming
    from app.repositories.plex_jobs import PlexJobRepository
    from app.services.plex_management import PlexManagementService
    from app.services.search_planner import build_confirmable_search_plan
    from app.utils.search_plan import TemporarySpecialAllocator, confirm_media_metadata

    with patch("app.services.search_planner.infer_search_hypotheses_with_ai", return_value=hypothesis_fixture()), \
         patch("app.services.search_planner.infer_media_metadata_draft_with_ai", return_value=temporary_draft_fixture()):
        plan = await build_confirmable_search_plan(
            "想见你 电影",
            "metadata-a",
            providers={
                "wikipedia": lambda _h: wikipedia_ok_evidence(),
                "douban": lambda _h: douban_ok_evidence(),
                "tvdb": lambda _h: tvdb_not_found_evidence(),
            },
            occupied_loader=lambda _contract: set(),
            allocator=TemporarySpecialAllocator(),
        )
    contract = confirm_media_metadata(plan)
    request = DownloadRequest(
        link="magnet:?xt=urn:btih:" + "a" * 40,
        selected_path="/真人剧集",
        user_id=1,
        metadata=attach_media_metadata({"source": "confirmed"}, contract),
    )
    provider = CopyingDownloadProvider(
        final_path="/真人剧集/Raw.Release",
        resource_name="Raw.Release",
        storage=FakeRenameStorage(),
    )
    registry = ModuleRegistry()
    registry.set_download_provider(provider)
    event = registry.dispatch_download(request)
    self.assertEqual(
        event.metadata["media_metadata"]["metadata_id"],
        request.metadata["media_metadata"]["metadata_id"],
    )

    renaming.register_module(registry)
    repository = PlexJobRepository(self.temp_path / "plex.db")
    plex = IntegrationPlexForMediaMetadata()
    service = PlexManagementService(
        repository,
        plex,
        category_folders=make_four_category_routes(),
        scan_poll_interval=0,
        scan_timeout=0,
    )
    service.enabled = True
    plex_management._service = service
    plex_management.register_module(registry)

    with patch.object(renaming, "collect_storage_file_tree", return_value=[{
             "name": "Movie.mkv", "relative_path": "Movie.mkv", "is_dir": False,
         }]), \
         patch.object(renaming, "infer_tvdb_episode_plan_with_ai", return_value={
             "episode_map": [{"source_file": "Movie.mkv", "season_number": 0, "episode_number": 100}],
         }), \
         patch.object(renaming, "_get_tvdb_candidates_and_episodes", return_value=([], [])), \
         patch.object(plex_management.plex_executor, "submit", side_effect=lambda fn, *args: fn(*args)):
        result = registry.run_post_download_pipeline(event)

    jobs = repository.list(10)
    self.assertEqual(len(jobs), 1)
    persisted = jobs[0]["payload"]["metadata"]["media_metadata"]
    self.assertEqual(result.metadata["media_metadata"]["metadata_id"], "metadata-a")
    self.assertEqual(persisted["metadata_id"], "metadata-a")
    self.assertTrue(persisted["items"][0]["final_path"].endswith("S00E100.mkv"))
    self.assertEqual(plex.custom_title, "想见你")
```

Make the test class `unittest.IsolatedAsyncioTestCase`; create/clean a temporary
directory in `asyncSetUp`/`asyncTearDown`; restore
`plex_management._service` in `addCleanup`. `CopyingDownloadProvider.submit`
must build a real `DownloadCompletedEvent` by deep-copying the request's
`naming_metadata` and `metadata`. Use the complete Task 3 draft/evidence
fixtures, the Task 6 storage fake, and the Task 9 Plex fake—no lambda may stand
in for the producer, renaming processor, completion hook, service, or
repository. The two AI calls are patched external boundaries, not replacement
business logic.

Add a second variant whose producer returns a primary-series `standalone`
contract with one confirmed `items` target S01E01. The real locked rename
builder must produce S01E01, persist the same `metadata_id`, and reject an AI
file map proposing S01E02. This proves ordinary series episodes do not use the
Season 00 Special path or legacy placement inference.

- [ ] **Step 2: Run the integration test**

Run: `python3 -m unittest tests.test_composable_integration -v`

Expected: PASS after Tasks 1-9.

- [ ] **Step 3: Mark the old design and plan as superseded**

Add immediately below each title:

```markdown
> Superseded on 2026-07-12 by `2026-07-12-core-media-metadata-contract-design.md`; active code uses only `metadata["media_metadata"]`.
```

Historical text may retain the old key to document migration, but active Python, tests, and templates may not read, write, or fixture it.

- [ ] **Step 4: Run the active hard-cut scan**

Run:

```bash
cd /Users/young/Documents/telepiplex/.worktrees/core-media-metadata-contract
rg -n 'download_plan|attach_download_plan|confirm_download_plan|extract_confirmed_download_plan|infer_download_plan_with_ai|build_confirmable_plan' app tests config
```

Expected: no output and exit code 1.

Run:

```bash
rg -n 'app\.modules\.(media_search|renaming|plex_management)|from app\.modules import (media_search|renaming|plex_management)' \
  app/handlers/search_handler.py app/services/search_planner.py app/modules/renaming.py \
  app/services/plex_management.py app/modules/plex_management.py
```

Expected: only each module's own lazy handler/service imports; no module imports another business module.

- [ ] **Step 5: Run the complete composed test suite**

Run: `python3 -m unittest discover tests -v`

Expected: all discovered tests PASS.

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q`

Expected: all tests and subtests PASS.

- [ ] **Step 6: Commit the integration hard cut**

```bash
git add tests docs/superpowers/specs/2026-07-11-ai-wikipedia-download-planner-design.md docs/superpowers/plans/2026-07-11-ai-wikipedia-download-planner.md
git commit -m "test: cover core media metadata pipeline"
```

---

### Task 11: Align Module-Only Feature Branches

**Files:**
- Modify in `feature/telepiplex-core`: core contract, core route, core tests, core config.
- Modify in `feature/media-search`: core contract copy plus media-search producer files/tests/config.
- Modify in `feature/renaming`: core contract copy plus renaming consumer files/tests/config.
- Modify in `feature/plex-management`: core contract copy plus Plex consumer files/tests/config; remove unrelated business modules/tests.
- Create branch surfaces: `tests/test_renaming_feature_surface.py`, `tests/test_plex_feature_surface.py`, and `config/modules/plex-management.yaml.example`.
- Rewrite on Plex branch: `tests/test_bot_runtime_startup.py`, `tests/test_telepiplex_core_surface.py`, and `tests/test_config_template_contract.py`.

**Interfaces:**
- Consumes: the ownership commits produced by Tasks 1-10.
- Produces: four isolated branch tips with no cross-feature implementation files.

- [ ] **Step 1: Record composed ownership commits and create/reuse worktrees**

```bash
set -eu
ROOT=/Users/young/Documents/telepiplex
COMPOSED=$ROOT/.worktrees/core-media-metadata-contract

ensure_worktree() {
  path=$1
  branch=$2
  registered=$(git -C "$ROOT" worktree list --porcelain | awk -v ref="refs/heads/$branch" '
    $1 == "worktree" { path = $2 }
    $1 == "branch" && $2 == ref { print path }
  ')
  if [ -n "$registered" ]; then
    test "$registered" = "$path" || { echo "$branch is checked out at $registered" >&2; exit 1; }
  else
    test ! -e "$path" || { echo "Unregistered path exists: $path" >&2; exit 1; }
    git -C "$ROOT" worktree add "$path" "$branch"
  fi
  test "$(git -C "$path" branch --show-current)" = "$branch"
  test -z "$(git -C "$path" status --porcelain=v1 --untracked-files=all)" || {
    git -C "$path" status --short
    exit 1
  }
  test ! -e "$(git -C "$path" rev-parse --git-dir)/CHERRY_PICK_HEAD"
}

test "$(git -C "$COMPOSED" branch --show-current)" = codex/core-media-metadata-contract
test -z "$(git -C "$COMPOSED" status --porcelain=v1 --untracked-files=all)"
ensure_worktree "$ROOT/.worktrees/telepiplex-core" feature/telepiplex-core
ensure_worktree "$ROOT/.worktrees/media-search" feature/media-search
ensure_worktree "$ROOT/.worktrees/renaming" feature/renaming
ensure_worktree "$ROOT/.worktrees/plex-management" feature/plex-management
for commit in ed3178a 2133fc2 ad0cd0d 740908e a800b73 b0cdacc f9fd5e4 45da983 678f8ef; do
  git -C "$ROOT" cat-file -e "$commit^{commit}"
done
```

- [ ] **Step 2: Bring the already-landed AI/Wikipedia producer baseline into media-search**

In the media-search worktree, cherry-pick these existing ownership commits in order:

```bash
git -C /Users/young/Documents/telepiplex/.worktrees/media-search cherry-pick \
  ed3178a 2133fc2 ad0cd0d 740908e a800b73 b0cdacc
```

Resolve the known full-template conflict with `apply_patch`: keep the branch's
core-only full templates, add Wikipedia/AI fields to
`config/modules/media-search.yaml.example`, and rewrite
`tests/test_media_search_config.py` to parse that module snippet. Stage the four
resolved paths and continue the cherry-pick. Do not add renaming or Plex files.
Run:

```bash
(cd /Users/young/Documents/telepiplex/.worktrees/media-search && python3 -m unittest discover tests -v)
```

Expected: all media-search branch tests PASS.

- [ ] **Step 3: Bring the already-landed confirmed-renaming baseline into renaming**

In the renaming worktree, cherry-pick:

```bash
git -C /Users/young/Documents/telepiplex/.worktrees/renaming cherry-pick \
  f9fd5e4 45da983 678f8ef
```

Keep the module-only renaming config and do not add media-search or Plex files. Run:

```bash
(cd /Users/young/Documents/telepiplex/.worktrees/renaming && python3 -m unittest discover tests -v)
```

Expected: all renaming branch tests PASS.

- [ ] **Step 4: Port new ownership commits to the three consumer branches**

Apply core commits to core, media-search, renaming, and Plex worktrees; then apply only each branch's feature commits:

```bash
set -eu
ROOT=/Users/young/Documents/telepiplex
COMPOSED=$ROOT/.worktrees/core-media-metadata-contract
BASE=$(git -C "$COMPOSED" merge-base main HEAD)

one_commit() {
  found=$(git -C "$COMPOSED" log --format='%H%x09%s' "$BASE..HEAD" | awk -F '\t' -v subject="$1" '$2 == subject { print $1 }')
  count=$(printf '%s\n' "$found" | awk 'NF { n++ } END { print n + 0 }')
  test "$count" -eq 1 || { echo "Expected one commit for: $1; found $count" >&2; exit 1; }
  printf '%s\n' "$found"
}

CORE=$(one_commit 'feat(core): define media metadata contract')
ROUTE=$(one_commit 'feat(core): route four media categories by kind')
SEARCH_1=$(one_commit 'feat(media-search): produce core media metadata')
SEARCH_2=$(one_commit 'feat(media-search): dispatch confirmed media metadata')
RENAME_1=$(one_commit 'feat(renaming): bind files to core media metadata')
RENAME_2=$(one_commit 'feat(renaming): return resolved media metadata')
PLEX_1=$(one_commit 'feat(plex): locate and edit confirmed specials')
PLEX_2=$(one_commit 'feat(plex): consume confirmed media metadata')
PLEX_3=$(one_commit 'feat(plex): persist resolved media metadata jobs')

git -C "$ROOT/.worktrees/telepiplex-core" cherry-pick "$CORE" "$ROUTE"
git -C "$ROOT/.worktrees/media-search" cherry-pick "$CORE" "$ROUTE" "$SEARCH_1" "$SEARCH_2"
git -C "$ROOT/.worktrees/renaming" cherry-pick "$CORE" "$ROUTE" "$RENAME_1" "$RENAME_2"
git -C "$ROOT/.worktrees/plex-management" cherry-pick "$CORE" "$ROUTE" "$PLEX_1" "$PLEX_2" "$PLEX_3"
```

When full-template commits conflict with module config snippets, keep the branch's module-only template and apply the exact four `category_folder.kind` values plus fields consumed by that module.

- [ ] **Step 5: Add a Plex-only surface test before removing unrelated files**

Create `tests/test_plex_feature_surface.py` on `feature/plex-management`:

```python
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PlexFeatureSurfaceTest(unittest.TestCase):
    def test_only_plex_business_module_is_present(self):
        modules = sorted(path.name for path in (ROOT / "app/modules").glob("*.py") if path.name != "__init__.py")
        self.assertEqual(modules, ["plex_management.py"])

    def test_other_business_surfaces_are_absent(self):
        for relative in (
            "app/core/open_115.py",
            "app/handlers/auth_handler.py",
            "app/handlers/config_handler.py",
            "app/handlers/download_handler.py",
            "app/handlers/search_handler.py",
            "app/adapters/prowlarr.py",
            "app/adapters/tvdb.py",
            "app/utils/media_naming.py",
            "app/utils/search_query.py",
            "app/utils/search_resolution.py",
            "app/utils/tvdb_rename.py",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_plex_imports_core_contract_not_other_modules(self):
        source = (ROOT / "app/services/plex_management.py").read_text(encoding="utf-8")
        self.assertIn("app.core.media_metadata", source)
        self.assertNotIn("app.modules.media_search", source)
        self.assertNotIn("app.modules.renaming", source)
```

Run: `python3 -m unittest tests.test_plex_feature_surface -v`

Expected: FAIL because the current Plex Feature branch still contains unrelated modules.

- [ ] **Step 6: Remove the exact unrelated Plex-branch surfaces**

Delete these paths from `feature/plex-management`:

```text
app/core/open_115.py
app/handlers/auth_handler.py
app/handlers/config_handler.py
app/handlers/download_handler.py
app/handlers/search_handler.py
app/modules/media_search.py
app/modules/open115.py
app/modules/renaming.py
app/adapters/prowlarr.py
app/adapters/tvdb.py
app/utils/media_metadata.py
app/utils/media_naming.py
app/utils/release_score.py
app/utils/search_query.py
app/utils/search_resolution.py
app/utils/tvdb_rename.py
tests/test_auth_handler_startup.py
tests/test_composable_115.py
tests/test_composable_integration.py
tests/test_composable_renaming.py
tests/test_config_handler.py
tests/test_download_task_startup.py
tests/test_feature_115_surface.py
tests/test_media_auto_rename.py
tests/test_media_metadata_fusion.py
tests/test_media_search_surface.py
tests/test_media_search_utils.py
tests/test_open_115_startup.py
tests/test_tvdb_adapter.py
tests/test_tvdb_rename.py
```

Update `app/115bot.py` so the Plex Feature branch default and catalog contain only:

```python
DEFAULT_ENABLED_MODULES = ("app.modules.plex_management",)
MODULE_CATALOG = {
    "app.modules.plex_management": {
        "label": "Plex 管理",
        "description": "扫库、匹配、中文化、海报、默认流和确认特别篇元数据",
    },
}
```

Keep core runtime, Plex adapters/services/repository/MCP/handler, `message_queue`, logging utilities, and `app/core/media_metadata.py`.

Rewrite the three stale runtime/config tests so they expect only the core
runtime plus `app.modules.plex_management`; remove their Open115/search/rename
cases. The full templates remain identical and core-only, while
`tests/test_config_template_contract.py` requires exactly the new
`config/modules/plex-management.yaml.example` business snippet.

- [ ] **Step 7: Update module-only config snippets**

- Core config: add the four `kind` values and no business-service settings.
- media-search config: include four category routes with `kind`, paths, Wikipedia, TVDB, Prowlarr, and mandatory AI settings; omit `media.plex`.
- renaming config: include four category routes with `kind`, paths, TVDB, AI, and `media.unorganized_path`; omit search and Plex management.
- Plex config: include four category routes with `kind` and `plex_library_id`, Plex/TMDB/Fanart/AI settings; omit Prowlarr, TVDB search, 115 credentials, and renaming settings.

Each branch's surface test must parse and assert its exact config contract.
Create `tests/test_renaming_feature_surface.py` to assert renaming is the sole
business module and media-search/Plex imports and files are absent.

- [ ] **Step 8: Run every Feature branch independently**

```bash
python3 -m unittest discover tests -v
git ls-files -z '*.py' | xargs -0 python3 -m py_compile
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

Run those commands separately in:

- `/Users/young/Documents/telepiplex/.worktrees/telepiplex-core`
- `/Users/young/Documents/telepiplex/.worktrees/media-search`
- `/Users/young/Documents/telepiplex/.worktrees/renaming`
- `/Users/young/Documents/telepiplex/.worktrees/plex-management`

Expected: every branch is green on its own, and its business-module file list matches its surface test.

- [ ] **Step 9: Commit branch-only packaging changes**

Stage only the packaging surfaces below; Task 11 Step 6 deletions must be
staged with `git rm -- <the exact listed paths>`, never a broad remove.

```bash
ROOT=/Users/young/Documents/telepiplex
git -C "$ROOT/.worktrees/telepiplex-core" add -- config/modules/core.yaml.example tests/test_telepiplex_core_surface.py tests/test_config_template_contract.py
git -C "$ROOT/.worktrees/media-search" add -- config/modules/media-search.yaml.example tests/test_media_search_surface.py tests/test_media_search_config.py
git -C "$ROOT/.worktrees/renaming" add -- config/modules/renaming.yaml.example tests/test_renaming_feature_surface.py
git -C "$ROOT/.worktrees/plex-management" add -- app/115bot.py app/config.yaml.example config/config.yaml.example config/modules/plex-management.yaml.example tests/test_plex_feature_surface.py tests/test_bot_runtime_startup.py tests/test_telepiplex_core_surface.py tests/test_config_template_contract.py
```

For each worktree, run `git diff --cached --check`, assert both
`git diff --name-only` and `git ls-files --others --exclude-standard` are empty,
inspect `git diff --cached --name-status`, then run its complete unittest and
tracked-Python compilation suite. Commit with the respective messages:
`feat(core): publish media metadata contract`,
`feat(media-search): publish media metadata producer`,
`feat(renaming): publish media metadata consumer`, and
`feat(plex): publish isolated media metadata consumer`. Do not use `commit -a`
and do not push.

---

### Task 12: Final Composed Verification and Handoff

**Files:**
- Modify only if verification reveals a defect in an approved file.

**Interfaces:**
- Produces: a clean composed implementation branch plus four verified local Feature branch tips ready for user-selected publication.

Every command in Steps 1-6 runs with
`workdir=/Users/young/Documents/telepiplex/.worktrees/core-media-metadata-contract`;
do not run these checks from root `main`.

- [ ] **Step 1: Run the complete composed unittest suite**

Run: `(cd /Users/young/Documents/telepiplex/.worktrees/core-media-metadata-contract && python3 -m unittest discover tests -v)`

Expected: zero failures and zero errors.

- [ ] **Step 2: Run pytest without unrelated global plugins**

Run: `(cd /Users/young/Documents/telepiplex/.worktrees/core-media-metadata-contract && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q)`

Expected: all tests and subtests PASS.

- [ ] **Step 3: Compile all tracked Python files**

Run: `(cd /Users/young/Documents/telepiplex/.worktrees/core-media-metadata-contract && git ls-files -z '*.py' | xargs -0 python3 -m py_compile)`

Expected: exit code 0 and no output.

- [ ] **Step 4: Check dependency consistency**

Run: `(cd /Users/young/Documents/telepiplex/.worktrees/core-media-metadata-contract && python3 -m pip check)`

Expected: `No broken requirements found.`

- [ ] **Step 5: Run contract and module-isolation scans**

Run:

```bash
cd /Users/young/Documents/telepiplex/.worktrees/core-media-metadata-contract
rg -n 'download_plan|attach_download_plan|confirm_download_plan|extract_confirmed_download_plan|infer_download_plan_with_ai|build_confirmable_plan' app tests config
```

Expected: no output and exit code 1.

Run:

```bash
cd /Users/young/Documents/telepiplex/.worktrees/core-media-metadata-contract
rg -n 'app\.modules\.(media_search|renaming|plex_management)|from app\.modules import (media_search|renaming|plex_management)' \
  app/handlers/search_handler.py app/services/search_planner.py app/modules/renaming.py \
  app/services/plex_management.py app/modules/plex_management.py
```

Expected: no cross-business-module imports.

- [ ] **Step 6: Run Telepiplex whitespace and scope checks**

```bash
cd /Users/young/Documents/telepiplex/.worktrees/core-media-metadata-contract
BASE=$(git merge-base main HEAD)
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check "$BASE...HEAD"
git diff --name-status "$BASE...HEAD"
test -z "$(git status --porcelain=v1 --untracked-files=all)"
```

Expected: no whitespace errors, only approved core/media-search/renaming/Plex/config/test/docs files, and a clean working tree after commits.

- [ ] **Step 7: Gate deployment on the live category routes**

If `/config/config.yaml` exists in the deployment environment, parse it and
require exactly the four approved `kind` values, non-empty paths, and non-empty
`plex_library_id` values because all modules are enabled by default. If the
file is not mounted in the implementation environment, report live deployment
as unverified and hand off the exact four-entry YAML; do not claim rollout is
ready until the user updates and validates `/config/config.yaml`.

- [ ] **Step 8: Compare every Feature branch to its remote without publishing**

```bash
ROOT=/Users/young/Documents/telepiplex
git -C "$ROOT" fetch --prune origin
for branch in feature/telepiplex-core feature/media-search feature/renaming feature/plex-management; do
  if git -C "$ROOT" show-ref --verify --quiet "refs/remotes/origin/$branch"; then
    printf '%s remote-only/local-only: ' "$branch"
    git -C "$ROOT" rev-list --left-right --count "origin/$branch...$branch"
  else
    printf '%s remote=missing\n' "$branch"
  fi
done
```

Expected: report exact ahead/behind counts. If no remote Plex branch exists, state that explicitly rather than creating one.

- [ ] **Step 9: Enter the finishing workflow**

Invoke `superpowers:finishing-a-development-branch`. Present the standard merge/PR/keep/discard choices for the composed branch, then separately ask which verified Feature branches may be pushed. Do not merge, push, force-update, or delete worktrees before the user's choice.
