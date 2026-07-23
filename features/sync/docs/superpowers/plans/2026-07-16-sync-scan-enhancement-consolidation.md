# Plex Management Scan and Enhancement Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-target Plex management workflow with one durable event Job, keep only scan/artwork/audio/subtitle automation, and add an interactive `/scan` General Action.

**Architecture:** `media.organized` creates one Job whose payload contains all final-path targets. The service scans each routed Plex library once, locates targets by path inside the scanning step, then runs three independently resumable enhancement steps. Telegram owns `/scan` and selection interactions; MCP exposes only scan, artwork, audio, subtitle, and Job inspection.

**Tech Stack:** Python 3.12, `telepiplex-plugin-sdk` 1.1, Host API `>=1.2,<2.0`, `plexapi`, SQLite, FastMCP, python-telegram-bot response contracts, pytest.

## Global Constraints

- Work only in `/Users/young/Documents/telepiplex/features/sync` on `feature/sync`.
- Use one durable Job per `media.organized` event.
- Exposed pipeline stages are exactly `scanning`, `artwork`, `audio`, `subtitle`, and terminal states.
- Plex owns media recognition, matching, and metadata refresh.
- `/scan` never creates a pipeline Job and never runs enhancements.
- Automatic unambiguous choices apply directly; ambiguous choices require Telegram input.
- Already accepted Plex writes are never described as rolled back.
- Feature version is `1.0.0`; state schema version is `2`.

---

### Task 1: Lock the single-Job scan contract

**Files:**
- Modify: `tests/test_plex_management.py`
- Modify: `tests/test_feature_runtime.py`
- Modify: `src/telepiplex_sync/management.py`
- Modify: `src/telepiplex_sync/feature.py`
- Modify: `src/telepiplex_sync/jobs.py`

**Interfaces:**
- Produces: `LibrarySyncService.enqueue_organized_event(event: dict) -> dict | None`
- Produces: `LibrarySyncService.run_job(job_id: int, *, should_cancel=None, on_stage=None) -> dict`
- Produces: Job payload field `targets: list[dict]`
- Produces: scanning result fields `libraries: dict` and `targets: dict`

- [ ] **Step 1: Write failing service tests**

Add tests asserting:

```python
job = service.enqueue_organized_event({
    "resource_name": "Show",
    "final_path": "/Series/Show",
    "media_metadata": contract_with_two_episode_paths,
})

assert len(repository.list()) == 1
assert [target["episode_number"] for target in job["payload"]["targets"]] == [1, 2]

result = service.run_job(job["id"])
assert plex.calls.count("scan_library") == 1
assert plex.find_paths == [
    "/Series/Show/Season 01/Show S01E01.mkv",
    "/Series/Show/Season 01/Show S01E02.mkv",
]
assert result["step_results"]["scanning"]["status"] == "success"
```

Add a partial-location test where one final path is missing and assert the located target continues through all enhancements while the Job completes with a scanning warning.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
PYTHONPATH=src:../../sdk/src \
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
-m pytest tests/test_plex_management.py tests/test_feature_runtime.py -q
```

Expected: failures because the current service creates one Job per target and exposes locating/matching/localizing stages.

- [ ] **Step 3: Implement one-Job enqueueing**

Change the enqueue path so `_payload_targets()` still resolves canonical targets, but `_enqueue_payload()` stores the complete list in one payload:

```python
payload = deepcopy(payload)
payload["targets"] = targets
job, created = self.jobs.create_or_get_with_status(idempotency_key, payload)
result = dict(job)
result["created"] = created
return result
```

The idempotency key must not include target identity.

- [ ] **Step 4: Implement scan plus final-path location as one step**

Set:

```python
STEP_ORDER = ("scanning", "artwork", "audio", "subtitle")
```

Group targets by `_route_library(job, target)`, call `scan_library()` once per group, then poll:

```python
item = self.plex.find_item_by_path(library_id, target["final_path"])
```

Persist per-library and per-target results. Raise only when no target was located.

- [ ] **Step 5: Simplify Feature event execution**

`media_organized()` must enqueue and claim one Job and spawn `_run_job(job_id, operation_id)`. Remove the event-side list of Job IDs and batch runner.

- [ ] **Step 6: Update active and interrupted states**

Use new active states in new code while retaining legacy state names in `mark_incomplete_interrupted()` so existing state databases do not strand old rows.

- [ ] **Step 7: Run targeted tests and commit**

Run the Task 1 command and expect all selected tests to pass.

Commit:

```bash
git add src/telepiplex_sync/management.py src/telepiplex_sync/feature.py \
  src/telepiplex_sync/jobs.py tests/test_plex_management.py \
  tests/test_feature_runtime.py
git commit -m "refactor(plex): consolidate organized events into one job"
```

---

### Task 2: Implement deterministic enhancement selection

**Files:**
- Modify: `src/telepiplex_sync/rules.py`
- Modify: `src/telepiplex_sync/adapters/plex.py`
- Modify: `src/telepiplex_sync/management.py`
- Modify: `tests/test_plex_rules.py`
- Modify: `tests/test_plex_adapters.py`
- Modify: `tests/test_plex_management.py`
- Delete: `tests/test_plex_media_metadata_adapter.py`

**Interfaces:**
- Produces: `rank_textless_posters(tmdb_posters, fanart_posters) -> list[dict]`
- Produces: `choose_textless_poster(...) -> dict | None`
- Produces: `rank_original_audio(streams, original_language) -> list[dict]`
- Produces: `choose_original_audio(...) -> dict | None`
- Produces: `rank_chi_subtitles(streams) -> list[dict]`
- Produces: `choose_chi_subtitle(...) -> dict | None`
- Produces: `PlexAdapter.find_item_by_path(library_id, final_path) -> dict | None`

- [ ] **Step 1: Write failing rules tests**

Cover:

```python
assert choose_textless_poster(unique_top, [])["url"] == "https://top"
assert choose_textless_poster(tied_top, []) is None
assert [item["id"] for item in rank_original_audio(tied_audio, "ja")] == [1, 2]
assert choose_original_audio(tied_audio, "ja") is None
assert choose_chi_subtitle(two_external_chi) is None
```

- [ ] **Step 2: Write failing service ambiguity tests**

Assert an ambiguous enhancement returns:

```python
assert job["state"] == "awaiting_selection"
waiting = service.pending_selection(job["id"])
assert waiting["kind"] in {"artwork", "audio", "subtitle"}
```

Assert `confirm_selection(job_id, candidate_index)` applies the selected candidate and resumes the same Job.

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
PYTHONPATH=src:../../sdk/src \
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
-m pytest tests/test_plex_rules.py tests/test_plex_adapters.py \
tests/test_plex_management.py -q
```

Expected: failures because poster and subtitle ties are currently broken silently and selection is match-specific.

- [ ] **Step 4: Implement ranking rules**

Poster uniqueness compares the business score:

```python
(provider_priority, vote_count_or_likes, vote_average, resolution)
```

Do not include URL in uniqueness comparison.

Subtitle tiers are:

```python
selected external -> stable external -> embedded
```

Return `None` when the best tier contains more than one candidate.

- [ ] **Step 5: Implement generic waiting selection**

Replace `WaitingForMatchConfirmation` with:

```python
class WaitingForSelection(RuntimeError):
    def __init__(self, kind, target_id, candidates, *, rating_key="", part_id=0):
        ...
```

Persist the waiting record in the current step result and expose:

```python
pending_selection(job_id)
set_selection_index(job_id, index)
confirm_selection(job_id, index, *, should_cancel=None, on_stage=None)
```

- [ ] **Step 6: Remove Plex match and metadata adapter methods**

Delete `list_match_candidates`, `fix_match`, `refresh_zh_cn`, and custom Special metadata editing. Add parent/grandparent rating keys to normalized item dictionaries and resolve series artwork to the show item.

- [ ] **Step 7: Run targeted tests and commit**

Run the Task 2 command and expect all selected tests to pass.

Commit:

```bash
git add src/telepiplex_sync/rules.py src/telepiplex_sync/adapters/plex.py \
  src/telepiplex_sync/management.py tests/test_plex_rules.py \
  tests/test_plex_adapters.py tests/test_plex_management.py
git rm tests/test_plex_media_metadata_adapter.py
git commit -m "feat(plex): automate or confirm media enhancements"
```

---

### Task 3: Add Telegram selection and `/scan`

**Files:**
- Modify: `manifest.yaml`
- Modify: `src/telepiplex_sync/runtime.py`
- Modify: `src/telepiplex_sync/feature.py`
- Modify: `tests/test_feature_runtime.py`

**Interfaces:**
- Produces: command `scan`
- Produces: callback payloads `plex:scan:*` and `plex:choice:*`
- Produces: `LibrarySyncService.scan_libraries(library_ids=None, *, should_cancel=None) -> dict`

- [ ] **Step 1: Write failing `/scan` tests**

Assert `/scan` returns live library buttons plus:

```python
{"text": "扫描全部媒体库", "callback_data": "plex:scan:all"}
```

Assert selecting one library scans only that ID. Assert `all` continues after one library failure and reports both successes and failures. Assert a second page appears when there are more than eight libraries.

- [ ] **Step 2: Write failing selection rendering tests**

Artwork waiting must expose `photo_url` with previous/select/next controls. Audio and subtitle waiting must expose labeled candidate buttons. A click must call `confirm_selection()` and continue the same operation.

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
PYTHONPATH=src:../../sdk/src \
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
-m pytest tests/test_feature_runtime.py -q
```

Expected: failures because `scan` is not registered and callbacks are match-specific.

- [ ] **Step 4: Implement `/scan` menu and execution**

Register:

```yaml
- name: scan
  description: 扫描 Plex 媒体库
```

Fetch libraries on every menu render. Paginate eight per page. On selection, validate the ID against a fresh Plex library list and spawn a cancellable manual-scan operation.

- [ ] **Step 5: Implement generic enhancement callbacks**

Use one waiting choice at a time:

```text
choice:<job_id>:prev
choice:<job_id>:next
choice:<job_id>:pick:<index>
```

Keep every callback under Telegram's 64-byte limit.

- [ ] **Step 6: Simplify `/plex`**

- No arguments: list recent Jobs.
- Numeric argument: show one Job and reopen its pending selection if needed.
- Other arguments: return command usage.
- Remove AI planning and write confirmation callbacks.

- [ ] **Step 7: Run targeted tests and commit**

Run the Task 3 command and expect it to pass.

Commit:

```bash
git add manifest.yaml src/telepiplex_sync/runtime.py \
  src/telepiplex_sync/feature.py tests/test_feature_runtime.py
git commit -m "feat(plex): add interactive library scan command"
```

---

### Task 4: Shrink MCP and remove local AI

**Files:**
- Modify: `src/telepiplex_sync/mcp_server.py`
- Delete: `src/telepiplex_sync/ai.py`
- Modify: `tests/test_plex_mcp.py`
- Delete: `tests/test_plex_ai.py`

**Interfaces:**
- Produces the exact 13-tool MCP surface specified in the design.
- Retains `prepare_operation()` and `apply_operation()` single-use confirmation behavior.

- [ ] **Step 1: Write failing exact-surface test**

Expected names:

```python
{
    "plex_server_status", "plex_list_libraries", "plex_inspect_item",
    "plex_list_artwork_candidates", "plex_list_audio_candidates",
    "plex_list_subtitle_candidates", "plex_get_job", "plex_list_jobs",
    "plex_scan_library", "plex_set_textless_poster",
    "plex_select_original_audio", "plex_select_chi_subtitle",
    "plex_retry_job",
}
```

Assert removed match, localization, pipeline, and metadata-batch tools are absent.

- [ ] **Step 2: Run MCP tests and verify RED**

Run:

```bash
PYTHONPATH=src:../../sdk/src \
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
-m pytest tests/test_plex_mcp.py -q
```

Expected: exact-surface failure against the old 16-tool server.

- [ ] **Step 3: Replace MCP tool registrations**

Keep read/write annotations, Bearer middleware, Streamable HTTP, and confirmation handling. Remove `PlexToolDispatcher`.

- [ ] **Step 4: Remove AI source and tests**

Delete the orchestrator and every import/config path that initializes it.

- [ ] **Step 5: Run tests and commit**

Run the Task 4 command and expect it to pass.

Commit:

```bash
git add src/telepiplex_sync/mcp_server.py tests/test_plex_mcp.py
git rm src/telepiplex_sync/ai.py tests/test_plex_ai.py
git commit -m "refactor(plex): reduce mcp to scan and enhancement tools"
```

---

### Task 5: Remove AI configuration and cut version 1.0.0

**Files:**
- Modify: `src/telepiplex_sync/config_wizard.py`
- Modify: `config.default.yaml`
- Modify: `config.schema.json`
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `tests/test_config_wizard.py`
- Modify: `tests/test_config_schema_contract.py`
- Modify: `tests/test_feature_runtime.py`

**Interfaces:**
- Configuration sections are exactly `category_folder`, `plex`, `tmdb`, `fanart`, and `mcp`.
- Interactive wizard sections are Plex, TMDB, and Fanart.

- [ ] **Step 1: Write failing configuration tests**

Assert `ai` is absent from defaults/schema/wizard, `/scan` exists in the manifest, version is `1.0.0`, and `state_schema_version` is `2`.

- [ ] **Step 2: Run configuration tests and verify RED**

Run:

```bash
PYTHONPATH=src:../../sdk/src \
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
-m pytest tests/test_config_wizard.py tests/test_config_schema_contract.py \
tests/test_feature_runtime.py -q
```

Expected: failures because AI and version 1.1.0 are still present.

- [ ] **Step 3: Update configuration and documentation**

Remove AI defaults/schema/wizard fields. Update the README workflow, commands, configuration paths, MCP confirmation behavior, and build command:

```text
dist/sync-1.0.0.tpx
```

- [ ] **Step 4: Run tests and commit**

Run the Task 5 command and expect it to pass.

Commit:

```bash
git add src/telepiplex_sync/config_wizard.py config.default.yaml \
  config.schema.json manifest.yaml pyproject.toml README.md \
  tests/test_config_wizard.py tests/test_config_schema_contract.py \
  tests/test_feature_runtime.py
git commit -m "chore(sync): cut 1.0.0"
```

---

### Task 6: Full verification and cleanup

**Files:**
- Review all changed source, tests, docs, and manifest files.

**Interfaces:**
- No obsolete match/localization/AI symbols remain in runtime source.
- No tracked build or cache files are introduced.

- [ ] **Step 1: Run the complete test suite**

```bash
PYTHONPATH=src:../../sdk/src \
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
-m pytest -q
```

Expected: zero failures.

- [ ] **Step 2: Compile source**

```bash
PYTHONPATH=src:../../sdk/src \
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
-m compileall -q src
```

Expected: exit code `0`.

- [ ] **Step 3: Validate YAML and JSON**

```bash
PYTHONPATH=src \
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
-c 'import json, pathlib, yaml; yaml.safe_load(pathlib.Path("manifest.yaml").read_text()); yaml.safe_load(pathlib.Path("config.default.yaml").read_text()); json.loads(pathlib.Path("config.schema.json").read_text())'
```

Expected: exit code `0`.

- [ ] **Step 4: Scan for obsolete runtime symbols**

```bash
rg -n "matching|localizing|fix_match|refresh_zh_cn|PlexAIOrchestrator|waiting_match_confirmation|metadata_batch" \
  src manifest.yaml config.default.yaml config.schema.json README.md
```

Expected: no matches except historical design documents outside the scanned paths.

- [ ] **Step 5: Check diff and status**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors and only intentional source/test/doc changes.

- [ ] **Step 6: Commit any verification cleanup**

If cleanup was required:

```bash
git add -A
git commit -m "test(plex): verify consolidated scan workflow"
```
