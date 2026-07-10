# Media Metadata Fusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge matching Douban and TVDB results into one correctly typed candidate with a stable English query, TVDB-first cover, Douban cover fallback, and complete renaming metadata.

**Architecture:** The TVDB adapter normalizes names and images for both movies and series. Douban parsing preserves type and cover. Pure helpers in `search_resolution.py` match and merge entries, while the search handler converts the merged candidate into authoritative download metadata and sends one selected-item cover card.

**Tech Stack:** Python 3.12, `unittest`, `unittest.mock`, existing Requests-based TVDB/Douban adapters, python-telegram-bot handlers.

## Global Constraints

- Douban and TVDB are primary peers; AI remains fallback-only after both fail.
- Chinese and confirmed Latin titles prefer Douban; TVDB owns media type, TVDB IDs, and episode data after a match.
- Covers use TVDB first and Douban second; missing covers never block search.
- Do not add TMDB configuration or dependencies.
- Do not change the renaming folder grammar `中文名 (English Name)`.
- Every production behavior change must follow a witnessed RED then GREEN test cycle.

---

### Task 1: Normalize TVDB English Titles And Covers

**Files:**
- Create: `tests/test_tvdb_adapter.py`
- Modify: `app/adapters/tvdb.py`

**Interfaces:**
- Consumes: `_tvdb_get(path: str, params: dict | None = None)`
- Produces: `_normalize_search_item(item: dict, media_type: str) -> dict`
- Produces: `search_tvdb_movies(query: str, year: str = "") -> list[dict]`
- Produces: `get_tvdb_movie_artwork_url(movie_id: str) -> str`

- [ ] **Step 1: Write failing adapter tests**

```python
class TvdbAdapterTest(unittest.TestCase):
    def test_korean_primary_name_uses_latin_alias_and_search_poster(self):
        item = tvdb._normalize_search_item(
            {
                "tvdb_id": "411469",
                "name": "더 글로리",
                "aliases": ["The Glory (2022)", "The Glory (KR)"],
                "image_url": "https://art.example/glory.jpg",
                "year": "2022",
            },
            "series",
        )
        self.assertEqual(item["english_title"], "The Glory")
        self.assertEqual(item["cover_url"], "https://art.example/glory.jpg")

    @patch.object(tvdb, "_tvdb_get")
    def test_movie_search_uses_translation_endpoint_only_without_latin_title(self, get_mock):
        get_mock.side_effect = [
            {"data": [{"tvdb_id": "123", "name": "中文片名", "year": "2024"}]},
            {"data": {"name": "English Movie", "language": "eng"}},
        ]
        result = tvdb.search_tvdb_movies("中文片名", "2024")
        self.assertEqual(result[0]["english_title"], "English Movie")
        self.assertEqual(get_mock.call_args_list[1].args[0], "/movies/123/translations/eng")
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_tvdb_adapter -v
```

Expected: FAIL because `_normalize_search_item` and `search_tvdb_movies` do not exist.

- [ ] **Step 3: Implement minimal TVDB normalization**

Add helpers that:

```python
def _normalize_search_item(item, media_type):
    entity_id = str(item.get("tvdb_id") or item.get("id") or "").strip()
    english_title = _preferred_english_title(item)
    cover_url = _search_cover_url(item)
    return {
        "tvdb_id": entity_id,
        f"tvdb_{media_type}_id": entity_id,
        "media_type": "series" if media_type == "series" else "movie",
        "name": str(item.get("name") or "").strip(),
        "english_title": english_title,
        "aliases": _alias_values(item.get("aliases")),
        "year": str(item.get("year") or item.get("first_air_time") or "").strip()[:4],
        "cover_url": cover_url,
    }
```

Implement `_preferred_english_title` with the approved precedence and `_search_cover_url` with `image_url`, `poster`, `posters`, `thumbnail`, then `image`. Implement one generic `_search_tvdb(query, entity_type, year)` and keep `search_tvdb_series` as a compatible wrapper.

- [ ] **Step 4: Run adapter tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_tvdb_adapter -v
```

Expected: all adapter tests PASS.

### Task 2: Preserve Douban Type, Subject ID, And Cover

**Files:**
- Create: `tests/test_media_metadata_fusion.py`
- Modify: `app/handlers/search_handler.py`

**Interfaces:**
- Consumes: `_extract_douban_metadata(payload: dict) -> dict | None`
- Produces: Douban metadata with `subject_id`, `media_type`, and `cover_url`

- [ ] **Step 1: Write failing Douban parser tests**

```python
class DoubanMetadataFusionTest(unittest.TestCase):
    def test_douban_series_keeps_type_and_cover(self):
        metadata = search_handler._extract_douban_metadata({
            "id": "35314632",
            "title": "黑暗荣耀",
            "original_title": "더 글로리",
            "aka": ["The Glory"],
            "year": "2022",
            "type": "tv",
            "subtype": "tv",
            "is_tv": True,
            "cover_url": "https://img.example/glory.jpg",
            "pic": {"large": "https://img.example/glory-large.jpg"},
        })
        self.assertEqual(metadata["media_type"], "series")
        self.assertEqual(metadata["subject_id"], "35314632")
        self.assertEqual(metadata["cover_url"], "https://img.example/glory.jpg")

    def test_chinese_movie_without_latin_title_remains_usable(self):
        metadata = search_handler._extract_douban_metadata({
            "id": "1", "title": "中文电影", "year": "2024",
            "type": "movie", "pic": {"large": "https://img.example/movie.jpg"},
        })
        self.assertEqual(metadata["media_type"], "movie")
        self.assertEqual(metadata["english_title"], "")
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_media_metadata_fusion.DoubanMetadataFusionTest -v
```

Expected: FAIL because the current parser discards these fields and rejects a missing Latin title.

- [ ] **Step 3: Implement minimal Douban field retention**

Add field helpers equivalent to:

```python
media_type = "series" if data.get("is_tv") or data.get("type") == "tv" or data.get("subtype") == "tv" else "movie"
pic = data.get("pic") if isinstance(data.get("pic"), dict) else {}
cover_url = str(data.get("cover_url") or pic.get("large") or pic.get("normal") or data.get("cover") or "").strip()
```

Require `chinese_title`, but allow an empty `english_title` so TVDB can supply it during fusion.

- [ ] **Step 4: Run Douban tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_media_metadata_fusion.DoubanMetadataFusionTest -v
```

Expected: both tests PASS.

### Task 3: Merge Matching Douban And TVDB Entries

**Files:**
- Modify: `app/utils/search_resolution.py`
- Modify: `app/handlers/search_handler.py`
- Modify: `tests/test_media_metadata_fusion.py`

**Interfaces:**
- Produces: `merge_primary_entries(entries: list[dict]) -> list[dict]`
- Consumes: normalized entry fields and aliases
- Produces: one canonical merged candidate per verified title/type/year match

- [ ] **Step 1: Write the failing fusion test**

```python
def test_douban_and_tvdb_glory_merge_into_one_series(self):
    merged = merge_primary_entries([
        {
            "source": "douban", "media_type": "series",
            "title": "The Glory", "chinese_title": "黑暗荣耀",
            "english_title": "The Glory", "year": "2022",
            "external_ids": {"douban_subject": "35314632"},
            "cover_url": "https://img.example/douban.jpg", "cover_source": "douban",
        },
        {
            "source": "tvdb", "media_type": "series", "scope": "whole_series",
            "title": "더 글로리", "english_title": "The Glory", "year": "2022",
            "aliases": ["The Glory (2022)"], "external_ids": {"tvdb": "411469"},
            "cover_url": "https://img.example/tvdb.jpg", "cover_source": "tvdb",
        },
    ])
    self.assertEqual(len(merged), 1)
    self.assertEqual(merged[0]["media_type"], "series")
    self.assertEqual(merged[0]["english_title"], "The Glory")
    self.assertEqual(merged[0]["chinese_title"], "黑暗荣耀")
    self.assertEqual(merged[0]["external_ids"], {"douban_subject": "35314632", "tvdb": "411469"})
    self.assertEqual(merged[0]["cover_url"], "https://img.example/tvdb.jpg")
```

Add a second test proving same-title movie and series entries remain separate.

- [ ] **Step 2: Run fusion tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_media_metadata_fusion.PrimaryEntryMergeTest -v
```

Expected: FAIL because `merge_primary_entries` does not exist.

- [ ] **Step 3: Implement pure matching and merge helpers**

Implement normalized title sets and safe matching in `search_resolution.py`. Merge with field authority from the design:

```python
merged["chinese_title"] = douban.get("chinese_title") or tvdb.get("chinese_title") or ""
merged["english_title"] = douban.get("english_title") or tvdb.get("english_title") or ""
merged["media_type"] = tvdb.get("media_type") or douban.get("media_type") or ""
merged["external_ids"] = {**douban_ids, **tvdb_ids}
merged["cover_url"] = tvdb.get("cover_url") or douban.get("cover_url") or ""
merged["cover_source"] = "tvdb" if tvdb.get("cover_url") else ("douban" if douban.get("cover_url") else "")
```

Call `merge_primary_entries` after primary-source collection and before deduplication/candidate construction. Set the TVDB lookup type from confirmed Douban metadata; query both movie and series only when type is unknown.

- [ ] **Step 4: Run fusion tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_media_metadata_fusion.PrimaryEntryMergeTest -v
```

Expected: all fusion tests PASS.

### Task 4: Isolate Covers And Preserve Renaming Metadata

**Files:**
- Modify: `app/handlers/search_handler.py`
- Modify: `tests/test_media_metadata_fusion.py`
- Test: `tests/test_tvdb_rename.py`

**Interfaces:**
- Produces: `_backfill_candidate_covers(candidates: list[dict]) -> list[dict]`
- Produces: `_send_candidate_info_card(update, candidate: dict)`
- Produces: canonical `_candidate_naming_metadata` and `_candidate_search_metadata`

- [ ] **Step 1: Write failing cover and renaming handoff tests**

```python
def test_douban_movie_cover_is_not_overwritten_by_other_candidate(self):
    candidates = [
        {"media_type": "movie", "cover_url": "https://img.example/movie.jpg", "cover_source": "douban", "external_ids": {}},
        {"media_type": "series", "cover_url": "https://img.example/series.jpg", "cover_source": "tvdb", "external_ids": {"tvdb": "2"}},
    ]
    result = asyncio.run(search_handler._backfill_candidate_covers(candidates))
    self.assertEqual(result[0]["cover_url"], "https://img.example/movie.jpg")

def test_candidate_metadata_overlays_stale_nested_movie_type(self):
    candidate = {
        "media_type": "series", "scope": "whole_series",
        "chinese_title": "黑暗荣耀", "english_title": "The Glory", "year": "2022",
        "external_ids": {"tvdb": "411469"}, "cover_url": "https://img.example/glory.jpg",
        "metadata": {"media_type": "movie", "selected_scope": "movie"},
    }
    metadata = search_handler._candidate_search_metadata(candidate)
    self.assertEqual(metadata["media_type"], "series")
    self.assertEqual(metadata["selected_scope"], "whole_series")
    self.assertEqual(metadata["external_ids"], {"tvdb": "411469"})
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_media_metadata_fusion.CoverAndHandoffTest -v
```

Expected: FAIL because cover backfill is list-global and nested metadata currently bypasses canonical fields.

- [ ] **Step 3: Implement candidate-scoped cover and canonical handoff**

Replace `_backfill_single_series_cover` with per-candidate logic. When a candidate has a TVDB ID but no TVDB cover, try the corresponding artwork fallback; retain its own Douban cover on failure. Never write one candidate's URL into another candidate.

Build `naming_metadata` and search `metadata` from a copy of nested metadata, then always overlay canonical candidate fields. Replace the series-only info card with one selected-candidate card and call it in `_send_confirmed_candidate_search` before `_send_search_results`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_media_metadata_fusion tests.test_tvdb_adapter tests.test_tvdb_rename -v
```

Expected: all focused tests PASS, including current `中文名 (English Name)` renaming expectations.

### Task 5: Full Verification, Renaming Merge Audit, And Publish

**Files:**
- Verify all modified production/test/docs files.
- Git refs: `feature/renaming`, `origin/feature/renaming`, `main`, `origin/main`.

**Interfaces:**
- Produces: verified `main` containing metadata fusion without renaming regressions.

- [ ] **Step 1: Run the complete verification stack**

Run:

```bash
python3 -m unittest discover tests -v
python3 -m py_compile $(git ls-files '*.py')
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

Expected: zero failures, zero syntax errors, and a clean whitespace check.

- [ ] **Step 2: Audit renaming ancestry and current behavior**

Run:

```bash
git fetch origin --prune
git merge-base --is-ancestor origin/feature/renaming main
git merge-base --is-ancestor main origin/main
git log --graph --decorate --oneline --all -20
python3 -m unittest tests.test_composable_renaming tests.test_tvdb_rename tests.test_media_auto_rename -v
```

Expected: `origin/feature/renaming` is an ancestor of `main`, local `main` is represented on `origin/main` before the new integration, and renaming tests pass.

- [ ] **Step 3: Commit the implementation branch**

Stage only the scoped metadata fusion files and commit with:

```bash
git commit -m "Fix media metadata fusion and covers"
```

- [ ] **Step 4: Integrate and push**

Fast-forward or merge the verified branch into local `main`, rerun the complete verification stack on `main`, then:

```bash
git push origin main
git fetch origin
test "$(git rev-parse main)" = "$(git rev-parse origin/main)"
```

Expected: push succeeds and local/remote `main` resolve to the same commit.

