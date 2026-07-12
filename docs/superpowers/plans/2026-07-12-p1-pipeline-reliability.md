# P1 Pipeline Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make confirmed downloads map safely to real files, report partial rename outcomes accurately, keep Plex optional failures module-local, and process ordinary movies/episodes before applying Special-specific behavior.

**Architecture:** Add a core-safe identity enrichment helper and a focused confirmed-file mapping utility. Renaming composes deterministic and AI mappings, validates them against locked items, executes a per-item ledger, and emits a terminal summary. Plex lazily initializes AI, expands a completion into per-media targets, submits only newly created jobs, locates ordinary episodes by exact path, and marks restart leftovers interrupted.

**Tech Stack:** Python 3.12, `unittest`/`pytest`, python-telegram-bot, PlexAPI, SQLite, 115 OpenAPI adapter.

## Global Constraints

- Work only on the current local `main` baseline.
- Do not port changes to feature worktrees in this plan.
- Do not implement deferred P2/P3 findings.
- Preserve only eligible mapped video files; do not preserve subtitles or sidecars.
- Prowlarr queries remain English and are not changed by Chinese-title enrichment.
- Every production behavior change requires a failing test first.
- Do not push without explicit user approval.

---

### Task 1: Canonical Chinese-title enrichment

**Files:**
- Modify: `app/core/media_metadata.py`
- Modify: `app/handlers/search_handler.py`
- Test: `tests/test_core_media_metadata.py`
- Test: `tests/test_search_media_metadata_flow.py`

**Interfaces:**
- Produces: `enrich_media_metadata_identity(metadata: dict | None, *, chinese_title: str, source: str, evidence: dict | None = None) -> dict`
- Preserves: `metadata_id`, English title, relation, placement, items, and Prowlarr query state.

- [x] **Step 1: Write failing core tests**

Add tests proving that enrichment fills only a missing canonical Chinese title,
records `evidence["identity_backfills"]`, and cannot overwrite an existing title
or alter locked placement/items.

- [x] **Step 2: Run the tests and verify RED**

Run:

```bash
PYTHONPATH=.:app python3 -m unittest tests.test_core_media_metadata -v
```

Expected: import or assertion failure because the helper does not exist.

- [x] **Step 3: Implement the core helper**

Implement a deep-copy helper that validates confirmed metadata, fills the
missing title, appends a JSON-serializable evidence entry, and validates the
result again before returning it.

- [x] **Step 4: Write and run the search-flow RED test**

Add an async test where Prowlarr receives the English query, Douban returns a
Chinese title, and the stored pending task contains the updated nested
`media_metadata.identity.chinese_title`.

- [x] **Step 5: Wire search backfill through the core helper and verify GREEN**

Run:

```bash
PYTHONPATH=.:app python3 -m unittest tests.test_core_media_metadata tests.test_search_media_metadata_flow -v
```

Expected: all tests pass and the Prowlarr mock was called with the unchanged
English query.

### Task 2: Rule-first confirmed-file mapping and coverage

**Files:**
- Create: `app/utils/confirmed_file_mapping.py`
- Test: `tests/test_confirmed_file_mapping.py`
- Modify: `app/utils/ai.py`

**Interfaces:**
- Produces: `map_confirmed_files(media_metadata, file_tree, ai_episode_map=None) -> dict`
- Returns: `state`, `mappings`, `missing_items`, `unexpected_sources`, `rejected`.
- Produces: `unresolved_mapping_context(media_metadata, file_tree, coverage) -> dict` for the existing AI mapper.

- [x] **Step 1: Write failing mapping tests**

Cover exact `SxxEyy`, `NxEE`, unique `source_hint`, partial coverage, no-match
failure, invented sources, targets outside the contract, duplicate sources, and
duplicate targets.

- [x] **Step 2: Run the tests and verify RED**

Run:

```bash
PYTHONPATH=.:app python3 -m unittest tests.test_confirmed_file_mapping -v
```

Expected: module import failure.

- [x] **Step 3: Implement deterministic mapping and AI validation**

Build indexes from real video paths and locked `(season, episode)` items. Apply
rules first, then accept AI bindings only for unresolved real sources and
unresolved locked targets. Derive completed/partial/failed from final coverage.

- [x] **Step 4: Update the mapping AI prompt**

State explicitly that AI receives unresolved files/items only, must not repeat
rule-resolved targets, and must return an empty map for ambiguity.

- [x] **Step 5: Verify GREEN**

Run the new tests plus `tests.test_tvdb_rename`.

### Task 3: Rename execution ledger and terminal partial result

**Files:**
- Modify: `app/modules/renaming.py`
- Modify: `app/utils/tvdb_rename.py`
- Modify: `app/core/open_115.py`
- Modify: `app/handlers/download_handler.py`
- Test: `tests/test_composable_renaming.py`
- Test: `tests/test_download_task_startup.py`
- Test: `tests/test_tvdb_rename.py`

**Interfaces:**
- `auto_clean_all(path, clean_empty_dir=False)` returns a cleanup summary without changing existing callers that ignore the return value.
- Confirmed rename plans expose coverage and execution entries.
- `process_tvdb_episode()` returns a terminal handled result for completed, partial, and failed confirmed mappings.

- [x] **Step 1: Write failing cleanup and partial-failure tests**

Cover returned cleanup counts, first move success/second move failure, stopping
later formal moves, moving untouched eligible videos to unorganized, preserving
only successful final paths, and a message that lists both formal and
unorganized outcomes.

- [x] **Step 2: Run focused tests and verify RED**

Run:

```bash
PYTHONPATH=.:app python3 -m unittest tests.test_composable_renaming tests.test_download_task_startup tests.test_tvdb_rename -v
```

Expected: assertions fail against current exception/fallback behavior.

- [x] **Step 3: Capture cleanup summary**

Return deleted file names/count from `auto_clean_all` and attach the summary to
outer event metadata without modifying `media_metadata`.

- [x] **Step 4: Compose rules and AI only for unresolved items**

Use Task 2 coverage before the AI call, skip AI when coverage is already
complete, and merge only validated AI bindings.

- [x] **Step 5: Execute and persist the ledger**

Preflight all target conflicts, execute mapped units in order, stop after the
first operational failure, route unmatched and untouched eligible videos to
unorganized, enrich only successful items, and build one terminal summary.

- [x] **Step 6: Verify GREEN**

Run all three focused test modules until they pass.

### Task 4: Lazy Plex AI and module-level startup isolation

**Files:**
- Modify: `app/modules/plex_management.py`
- Modify: `app/handlers/plex_handler.py`
- Test: `tests/test_plex_module.py`
- Test: `tests/test_plex_ai.py`

**Interfaces:**
- Produces: `get_plex_ai_orchestrator()` which is synchronous and intended to run through `asyncio.to_thread`.
- Produces module health fields for base service, AI, and MCP without changing core registry APIs.

- [x] **Step 1: Write failing async-loop and startup-isolation tests**

Enable Plex AI inside an active event loop and assert bot startup does not call
`asyncio.run`. Make service construction, interrupted-job marking, and MCP start
raise separately and assert the startup hook never propagates.

- [x] **Step 2: Run focused tests and verify RED**

Run `tests.test_plex_module tests.test_plex_ai`.

- [x] **Step 3: Move AI construction behind the lazy accessor**

Base service stores AI configuration but no orchestrator. `/plex` constructs
and runs the orchestrator in a worker thread and reports initialization errors.

- [x] **Step 4: Add independent startup exception boundaries**

Catch and log base service, interrupted-job marking, and MCP failures
independently; never raise from `start_plex_module_services`.

- [x] **Step 5: Verify GREEN**

Run the two focused modules and the async startup reproduction.

### Task 5: Common Plex targets and Special patches

**Files:**
- Modify: `app/services/plex_management.py`
- Modify: `app/adapters/plex.py`
- Modify: `app/modules/plex_management.py`
- Test: `tests/test_plex_adapters.py`
- Test: `tests/test_plex_management.py`
- Test: `tests/test_plex_management_integration.py`
- Test: `tests/test_plex_module.py`

**Interfaces:**
- Produces: `completion_targets(completion) -> list[dict]`.
- Each target contains `target_id`, `final_path`, `media_type`, optional `season_number`/`episode_number`, and the unchanged contract.
- `enqueue_completion(completion) -> list[dict]` returns one job record per resolved target.

- [x] **Step 1: Write failing target-expansion tests**

Cover one movie target, one target per resolved ordinary episode, no target for
missing items, and one target for each Special mapping kind.

- [x] **Step 2: Write failing exact-location tests**

Cover an episode added to an existing show, exact final-path validation, and an
ordinary movie location path unaffected by Special rules.

- [x] **Step 3: Run focused tests and verify RED**

Run the four Plex-focused modules listed above.

- [x] **Step 4: Implement target expansion and per-target payloads**

Derive stable target IDs and make the service operate on one movie/episode per
job. Keep the full contract in each payload for matching and patches.

- [x] **Step 5: Implement ordinary path-first location**

Extend the adapter with exact movie path lookup and reuse exact series episode
lookup for ordinary episodes. Remove ordinary series dependence on new-show
recent candidates.

- [x] **Step 6: Apply Special behavior as step overrides**

Keep official/inferred verification and temporary custom metadata as match,
localize, and artwork overrides after the common route/scan/location baseline.

- [x] **Step 7: Verify GREEN**

Run all Plex target, adapter, and integration tests.

### Task 6: Created-only submission and interrupted jobs

**Files:**
- Modify: `app/repositories/plex_jobs.py`
- Modify: `app/services/plex_management.py`
- Modify: `app/modules/plex_management.py`
- Test: `tests/test_plex_jobs.py`
- Test: `tests/test_plex_management.py`
- Test: `tests/test_plex_module.py`

**Interfaces:**
- `create_or_get(idempotency_key, payload) -> tuple[dict, bool]`.
- Produces: `mark_active_interrupted(reason="process_restarted") -> int`.
- Automatic submit happens only for the `created=True` jobs returned by target expansion.

- [x] **Step 1: Write failing repository and duplicate-submit tests**

Assert first creation returns true, repeated creation returns false, active
states become interrupted, completed/failed/waiting remain unchanged, startup
does not auto-resume, and duplicate completion submits once.

- [x] **Step 2: Run focused tests and verify RED**

Run `tests.test_plex_jobs tests.test_plex_management tests.test_plex_module`.

- [x] **Step 3: Implement repository semantics**

Return the creation flag from the existing immediate transaction and add one
transactional update for queued/scanning/locating/matching/localizing/artwork/
streams states to interrupted with a restart reason.

- [x] **Step 4: Update service and module callers**

Submit only newly created target jobs. Replace startup resume with interrupted
marking. Keep explicit `retry_job` as the manual recovery path.

- [x] **Step 5: Verify GREEN**

Run the focused modules and assert executor submission counts.

### Task 7: Full verification and deferred-scope audit

**Files:**
- Verify: `docs/superpowers/specs/2026-07-12-p1-pipeline-reliability-design.md`
- Verify: `docs/superpowers/plans/2026-07-12-p1-pipeline-reliability.md`

- [x] **Step 1: Run all tests**

```bash
PYTHONPATH=.:app PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t . -v
PYTHONPATH=.:app PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
```

- [x] **Step 2: Run static and dependency checks**

Compile tracked Python in memory, run `python3 -m pip check`, parse all YAML
examples, and run the Telepiplex-aware `git diff --check`.

- [x] **Step 3: Recheck scope**

Confirm no feature worktree changed, no P2/P3 item was silently implemented,
and module branch composability remains a separate next phase.

- [x] **Step 4: Report exact state**

Report changed files, test counts, remaining warnings, local commit state, and
that nothing was pushed.
