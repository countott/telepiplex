# Download/Rename media_metadata v2, Recovery, and Version Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan. Subagent execution requires an explicit user request and is not enabled by this plan.

**Goal:** Preserve confirmed v2 metadata through Download, make Rename organize from v2 plus the observed file tree, reconcile durable jobs against Host truth after restart, and ship the coordinated version set.

**Architecture:** Download validates and stores v2 but treats it as opaque frozen data. Rename converts legacy v1 once at its durable boundary, persists v2, derives organization from v2 scope plus actual files, and stores outcomes separately. Startup reconciliation reads the Host operation snapshot before resuming any side effect.

**Tech Stack:** Python 3.12, asyncio, SQLite, telepiplex Download/Rename Features, shared SDK validators, unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-08-25-search-message-segments-and-minimal-media-contract-design.md`

## Global Constraints

- Follow the Mac-only, no-Git, no-publish constraints from the earlier plans.
- `media_metadata v2` is immutable across Feature boundaries; organization outcomes belong in `organization_result`.
- Do not restore Provider episode inventory as a requirement. For whole-series scope, observed files determine the coordinates Rename attempts.
- The automatic pipeline ends at Rename. Do not add automatic sync or Plex enqueue.
- Apply TDD with the bundled Python runtime.

---

## Task 1: Preserve v2 through Download exactly

**Files:**

- Modify: `features/download/src/telepiplex_download/service.py`
- Modify: `features/download/src/telepiplex_download/jobs.py`
- Test: `features/download/tests/test_feature_runtime.py`

**Step 1: Add failing pass-through tests**

Prove that:

- `download.submit` validates a confirmed v2 contract;
- the job store, completed result, and `download.completed` event contain an equal deep copy;
- later mutation of the caller's dictionary cannot alter the stored value;
- new v2 submissions neither accept nor emit `naming_metadata`;
- a malformed v2 submission fails before the offline-download side effect;
- Download reports use the `download` text segment and seal it before the Rename handoff.

**Step 2: Verify RED**

```bash
cd features/download
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_feature_runtime.py \
  -k 'media_metadata_v2 or naming_metadata or segment or handoff'
```

**Step 3: Implement the v2 boundary**

Validate with `validate_media_metadata_v2`, store a deep copy in the durable payload, and copy it unchanged into completion events. Keep legacy durable payload reading only for jobs created before this release; no new code path may construct v1 or `naming_metadata`.

**Step 4: Implement the Download message segment**

Every interactive report declares `{"role": "download", "presentation_kind": "text"}`. Seal the segment after completion/failure rendering and before publishing the Rename event.

**Step 5: Verify GREEN**

Run `test_feature_runtime.py` in full.

## Task 2: Convert legacy v1 once at the Rename boundary

**Files:**

- Modify: `sdk/src/telepiplex_plugin_sdk/media_metadata_v2.py`
- Create: `features/rename/src/telepiplex_rename/media_metadata_v2.py`
- Modify: `features/rename/src/telepiplex_rename/jobs.py`
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Create: `features/rename/tests/test_media_metadata_v2.py`
- Modify: `tests/test_media_metadata_v2.py`

**Step 1: Add failing conversion tests**

Cover valid movie, series, season, and episode v1 fixtures. Require one deterministic v2 result and a durable marker such as `metadata_migration: "v1_to_v2"`. Reject ambiguous legacy identities or placements instead of guessing. After conversion, resumption must read persisted v2 without converting again.

**Step 2: Verify RED**

Run the root and Rename v2 test files.

**Step 3: Implement a narrow converter**

Expose:

```python
def convert_media_metadata_v1_to_v2(value: object) -> tuple[dict | None, dict | None]: ...
```

The second tuple item is a structured error when safe conversion is impossible. Map only identity, verified provider refs, media type, titles, year, scope coordinates, and exact existing `category_kind`; discard rich v1 fields.

**Step 4: Persist conversion before side effects**

At Rename event acceptance and durable resume, normalize metadata to v2 and update the job record before probing files, asking AI, or moving anything. Retain legacy `naming_metadata` only long enough to convert an old job; do not carry it into the normalized payload.

**Step 5: Verify GREEN**

Run both v2 test files and the Rename job-store tests.

## Task 3: Organize series and movies from v2 plus the observed file tree

**Files:**

- Modify: `features/rename/src/telepiplex_rename/media_metadata_v2.py`
- Modify: `features/rename/src/telepiplex_rename/processor.py`
- Modify: `features/rename/src/telepiplex_rename/tvdb_rename.py`
- Modify: `features/rename/src/telepiplex_rename/models.py`
- Test: `features/rename/tests/test_media_metadata_v2.py`
- Test: `features/rename/tests/test_file_first_processor.py`
- Test: `features/rename/tests/test_feature_processor.py`
- Test: `features/rename/tests/test_tvdb_rename.py`
- Test: `features/rename/tests/test_media_auto_rename.py`

**Step 1: Add failing scope and file-tree tests**

Use literal file trees to prove:

- whole-series scope admits every uniquely observed `SxxEyy` coordinate without Provider inventory;
- season scope rejects files outside the frozen season;
- episode scope moves only the frozen coordinate;
- duplicates and unparseable files remain in place and are reported explicitly;
- movie scope selects one main video and keeps extras deterministic;
- v2 remains unchanged after organization;
- `organization_result` contains file-level outcomes separately.

**Step 2: Verify RED**

```bash
cd features/rename
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_media_metadata_v2.py tests/test_file_first_processor.py \
  tests/test_feature_processor.py tests/test_tvdb_rename.py \
  tests/test_media_auto_rename.py -k 'v2 or observed or scope or immutable'
```

**Step 3: Implement v2 naming primitives**

Add pure functions:

```python
def naming_identity_from_v2(contract: dict) -> dict: ...
def scope_allows_coordinate(contract: dict, season: int, episode: int) -> bool: ...
def observed_episode_plan(contract: dict, file_tree: list[dict]) -> dict: ...
```

They may use file-name parsing results but must not call Providers or mutate the contract.

**Step 4: Route confirmed v2 through the deterministic processor**

Replace v1 reads of `items`, `warnings`, `retrieval`, and `evidence.decision` on the confirmed path. Keep Provider lookups as optional naming enrichment only when a verified `provider_ref` supports exact lookup; lookup failure cannot change the frozen identity or broaden scope.

**Step 5: Separate output from input metadata**

Stop calling `enrich_media_metadata_with_rename_plan` for v2. Return:

```python
{
    "media_metadata": original_v2,
    "organization_result": {
        "status": "completed",
        "files": [...],
        "target_relative_dir": "...",
    },
}
```

Keep any v1 enrichment helper only for historical standalone fixtures until those fixtures are migrated.

**Step 6: Verify GREEN**

Run the five files in full, then all Rename tests.

## Task 4: Reconcile Rename durable jobs with Host truth on startup

**Files:**

- Modify: `features/rename/src/telepiplex_rename/jobs.py`
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Test: `features/rename/tests/test_feature_processor.py`
- Test: `tests/test_operation_pipeline_e2e.py`

**Step 1: Add failing restart tests**

Cover each Host snapshot:

- terminal/cancelled operation → persist a terminal external-cancel state and do not resume;
- operation missing → persist `orphaned_operation` and do not move files;
- owner is not Rename and no accepted Rename handoff exists → persist `handoff_not_accepted`;
- owner is Rename with an open segment → resume exactly once;
- previously completed organization → idempotently render/seal without moving again;
- Host temporarily unavailable → keep the job resumable and retry later without side effects.

**Step 2: Verify RED**

Run the two files filtered to `resume`, `host_snapshot`, `orphaned`, and `cancelled`.

**Step 3: Query Host before accepting the event operation**

At the top of `_resume_durable_job`, call `host.get_operation_snapshot(operation_id)`. Classify the returned owner, terminal status, segment state, and handoff receipt before `_accept_event_operation` or `_run_organization` can execute.

**Step 4: Make terminal decisions durable**

Add explicit terminal job states and timestamps. `jobs.resumable()` must exclude them. A transient Host transport failure is not terminal and must leave a retryable reason without changing ownership.

**Step 5: Verify GREEN**

Run both files in full and restart a real in-memory Host/Rename e2e fixture twice to prove no duplicate move or message.

## Task 5: Apply the Rename segment topology

**Files:**

- Modify: `features/rename/src/telepiplex_rename/service.py`
- Test: `features/rename/tests/test_feature_processor.py`
- Test: `tests/test_operation_pipeline_e2e.py`

**Step 1: Add a failing lifecycle test**

Assert that all Rename progress, confirmation, outcome, and failure projections use `{"role": "rename", "presentation_kind": "text"}`, edit one message, and seal at the terminal state. Assert there is no automatic sync or Plex capability call after sealing.

**Step 2: Verify RED, implement, verify GREEN**

Centralize Rename reports through one helper, seal only after the final durable result exists, and run both files in full.

## Task 6: Apply coordinated versions and compatibility declarations

**Files:**

- Modify: `app/115bot.py`
- Modify: `app/runtime/plugin_contract.py`
- Modify: `sdk/pyproject.toml`
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/README.md`
- Modify: `features/download/manifest.yaml`
- Modify: `features/download/pyproject.toml`
- Modify: `features/download/README.md`
- Modify: `features/rename/manifest.yaml`
- Modify: `features/rename/pyproject.toml`
- Modify: `features/rename/README.md`
- Modify: version/compatibility assertions in `tests/test_bot_runtime_startup.py`, `tests/test_deployment_contract.py`, `tests/test_plugin_manager.py`, `tests/test_technical_identity_migration.py`, and Feature version tests.

**Step 1: Add failing version-contract tests**

Set literal expectations:

- Host `v3.6.0-host`
- Host API `1.7`
- SDK `1.4.0`
- Search `1.12.0`
- Download `1.1.0`
- Rename `1.6.0`
- Sync and Caption unchanged

Verify each Feature's manifest, package metadata, Host API bounds, README example, and runtime version agree.

**Step 2: Verify RED**

Run the version-focused root and Feature tests and confirm they fail only on old values.

**Step 3: Update source version surfaces**

Change authoritative source files and checked documentation. Do not hand-edit `build/lib`; rebuild distributable metadata using the repository's existing packaging command only if a test proves those artifacts are part of the checked delivery contract.

**Step 4: Verify GREEN**

Run all version/contract tests and import smoke tests for Host plus Search/Download/Rename.

## Task 7: Full local verification and Syncthing handoff

**Step 1: Run the complete local suite**

```bash
cd /Users/young/Documents/telepiplex

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests

for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider tests
  )
done

test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

**Step 2: Run focused end-to-end scenarios**

At minimum, cover:

- `死神 千年血战` → one candidate card → one confirmation update → one Prowlarr message;
- double-click candidate callback → one accepted transition and one stale rejection;
- Search → Download → Rename creates three separate owner segments;
- v2 survives both handoffs unchanged;
- Rename restart does not resume a terminal old operation;
- automatic completion stops at Rename.

**Step 3: Deliver locally**

List every created, modified, deleted, or renamed file and the purpose of each group. Report only commands actually run and their actual results. Do not publish or run Git. Ask the user to wait until Syncthing shows `Up to Date / 最新` before operating on Unraid.
