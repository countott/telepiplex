# Confirmed Identity Only Renaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make rename consume search identity and relationships exclusively, while retaining AI only for file-to-locked-episode mapping and carrying Japanese romaji into paths.

**Architecture:** The service may request metadata from `media.search`, but a failed or non-confirmed response is a terminal identity result and moves the release to `/未整理`. Processor code deletes the legacy TVDB+AI identity fallback. Existing deterministic and constrained AI file mapping remain unchanged.

**Tech Stack:** Python 3.12, unittest, Telepiplex plugin SDK.

## Global Constraints

- Renaming cannot choose movie versus series, infer a target series, or change season/episode identity.
- `identity.english_title` remains the compatibility field for canonical Latin naming; Japanese values are romaji.
- Failed metadata resolution moves to `/未整理`; it does not start legacy TVDB identity inference.
- AI may only map a `file_tree` entry to a season/episode already present in confirmed `media_metadata.items`.
- Work remains local to `feature/rename` and is not pushed.

---

### Task 1: Remove Legacy Identity and Relationship Inference

**Files:**
- Modify: `src/telepiplex_rename/processor.py`
- Modify: `src/telepiplex_rename/service.py`
- Modify: `src/telepiplex_rename/ai.py`
- Test: `tests/test_feature_processor.py`

**Interfaces:**
- Consumes: confirmed `media_metadata`, or `media.search.resolve_metadata` response.
- Produces: deterministic `/未整理` result when search cannot return a confirmed contract; no calls to `search_tvdb_series` for identity fallback.

- [ ] **Step 1: Write failing fallback-removal tests**

```python
async def test_unresolved_media_search_moves_release_to_unorganized(self):
    host = FakeHost()
    host.media_search_result = {}
    feature = RenameFeature(config={"unorganized_path": "/Unorganized"}, host=host)
    await feature._run_organization("job-1", self.payload_without_metadata(), "op-1")
    self.assertEqual(host.storage.moved, [("/Downloads/Release", "/Unorganized")])

@patch("telepiplex_rename.processor.search_tvdb_series")
@patch("telepiplex_rename.processor.infer_tvdb_episode_plan_with_ai")
def test_processor_without_confirmed_identity_does_not_infer_it(self, ai_mock, tvdb_mock):
    result = process_tvdb_episode(self.event_without_metadata())
    self.assertTrue(result.handled)
    self.assertIn("未整理", result.message)
    tvdb_mock.assert_not_called()
    ai_mock.assert_not_called()
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python3 -m unittest tests.test_feature_processor -v`

Expected: FAIL because `_attempt_legacy_tvdb_ai_episode_rename` is still reachable.

- [ ] **Step 3: Delete the legacy identity path**

Remove `_tvdb_title_from_metadata`, `_get_tvdb_candidates_and_episodes`, `_legacy_metadata`, `_merge_tvdb_metadata`, and `_attempt_legacy_tvdb_ai_episode_rename` when they have no remaining callers. Remove their TVDB search imports. Keep `get_tvdb_series_episodes` only where confirmed IDs require episode facts.

When no confirmed contract exists, return the existing unorganized business result through `_move_to_unorganized`; do not raise an internal exception.

- [ ] **Step 4: Keep AI file mapping locked**

Ensure the AI context includes immutable `target_series`, `library_type`, `category_kind`, and `allowed_episode_keys`. Validate every returned mapping against `media_metadata.items`; reject any extra season/episode.

- [ ] **Step 5: Run processor tests**

Run: `python3 -m unittest tests.test_feature_processor -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/telepiplex_rename/processor.py src/telepiplex_rename/service.py src/telepiplex_rename/ai.py tests/test_feature_processor.py
git commit -m "refactor(rename): require search identity"
```

### Task 2: Lock Japanese Romaji Naming

**Files:**
- Modify: `tests/test_tvdb_rename.py`
- Modify: `tests/test_feature_processor.py`
- Modify: `src/telepiplex_rename/tvdb_rename.py` only if the current helpers do not pass the tests.
- Modify: `src/telepiplex_rename/media_naming.py` only if the current helpers do not pass the tests.

**Interfaces:**
- Consumes: `identity.english_title == romanized_original_title` for Japanese media.
- Produces: Chinese + romaji directories and romaji file stems without using `official_english_title`.

- [ ] **Step 1: Write failing/locking Japanese naming tests**

```python
def test_japanese_series_uses_romaji_not_english_translation(self):
    metadata = self._confirmed_media_metadata()
    metadata["identity"].update({
        "chinese_title": "进击的巨人",
        "english_title": "Shingeki no Kyojin",
        "official_english_title": "Attack on Titan",
        "romanized_original_title": "Shingeki no Kyojin",
        "canonical_search_title": "Shingeki no Kyojin",
        "original_language": "ja",
        "search_title_policy": "romanized_original",
    })
    plan = build_tvdb_rename_plan(..., media_metadata=metadata, ...)
    self.assertIn("/进击的巨人 (Shingeki no Kyojin)/", plan["target_path"])
    self.assertIn("Shingeki no Kyojin S01E01", plan["target_path"])
    self.assertNotIn("Attack on Titan", plan["target_path"])
```

- [ ] **Step 2: Run naming tests**

Run: `python3 -m unittest tests.test_tvdb_rename tests.test_feature_processor -v`

Expected: PASS if current helpers already honor `identity.english_title`; otherwise FAIL identifies the exact helper to fix.

- [ ] **Step 3: Make the minimal compatibility fix if required**

Keep `series_titles`, `series_folder_name`, and file-stem selection based on `identity.english_title`; never prefer `official_english_title` for a Japanese policy.

- [ ] **Step 4: Run naming tests again**

Run: `python3 -m unittest tests.test_tvdb_rename tests.test_feature_processor -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/telepiplex_rename/tvdb_rename.py src/telepiplex_rename/media_naming.py tests/test_tvdb_rename.py tests/test_feature_processor.py
git commit -m "test(rename): lock Japanese romaji paths"
```

### Task 3: Verify the Renaming Branch

**Files:**
- Verify only.

**Interfaces:**
- Produces: a rename branch that requires confirmed identity and preserves constrained file mapping.

- [ ] **Step 1: Run focused suites**

Run: `python3 -m unittest tests.test_feature_processor tests.test_tvdb_rename -v`

Expected: PASS.

- [ ] **Step 2: Run the full branch suite**

Run: `python3 -m unittest discover -s tests -t . -v`

Expected: PASS.

- [ ] **Step 3: Run syntax and whitespace checks**

Run: `python3 -m compileall -q src tests`

Expected: exit 0.

Run: `git diff --check`

Expected: no output.
