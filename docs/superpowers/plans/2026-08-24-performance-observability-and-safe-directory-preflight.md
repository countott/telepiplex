# Performance Observability and Safe Directory Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add privacy-safe pipeline performance observations and safely replace
eligible 115 rename preflight reads with complete directory-listing snapshots.

**Architecture:** Search and Download emit bounded observations through the
existing Feature diagnostic transport without changing business control flow.
Rename retains `FileTransactionSnapshot` and projects trusted directory
listings into it only when cheaper than exact reads; uncertainty uses exact
fallback and native move receives a fresh gate.

**Tech Stack:** Python 3.12, pytest, asyncio, existing telepiplex Feature SDK
diagnostics, Open115 provider capabilities.

**Spec:** `docs/superpowers/specs/2026-08-24-performance-observability-and-safe-directory-preflight-design.md`

## Global Constraints

- Work only in `/Users/young/Documents/telepiplex`; do not run Git, create
  worktrees, publish, or connect this checkout to GitHub.
- Product-facing prose uses lowercase `telepiplex`; technical identifiers
  retain their existing spelling.
- `operation_id`, `trace_id`, `span_id`, `parent_span_id`, and
  `request_id` remain the only cross-process correlation fields.
- New observations omit raw queries, titles, URLs, paths, magnets, tokens,
  headers, response payloads, and provider request identities.
- Observer exceptions never alter provider, cache, retry, cancellation, pacing,
  or business results.
- Directory snapshots prove only pre-mutation facts. Fresh post-move and
  cleanup verification remain mandatory.
- Candidate localization, authoritative-scope hydration, and EventDispatcher
  concurrency are out of scope.

---

### Task 1: Add explicit Search performance observations

**Files:**
- Modify: `features/search/src/telepiplex_search/search_logging.py`
- Modify: `features/search/src/telepiplex_search/source_schedule.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/tests/test_search_logging.py`
- Modify: `features/search/tests/test_source_schedule.py`
- Modify: `features/search/tests/test_feature_service.py`

**Interfaces:**
- Produces `log_search_measurement(logger, event, *, search_session_id, status="completed", duration_ms=None, **facts) -> None`.
- Extends `SourceScheduler(..., observer=None)`.
- The observer receives safe key fields and never receives
  `SourceRequestKey.identity`.

- [x] **Step 1: Write failing explicit-event and privacy tests**

```python
def test_measurement_uses_explicit_diagnostic_fields_without_query_text():
    logger = Mock()
    log_search_measurement(
        logger,
        "search.discovery.completed",
        search_session_id="session-1",
        duration_ms=12,
        query_chars=6,
        candidate_count=2,
    )
    extra = logger.info.call_args.kwargs["extra"]
    assert extra["event_name"] == "search.discovery.completed"
    assert extra["diagnostic_fields"]["duration_ms"] == 12
    assert "query" not in repr(extra)
```

Add scheduler tests for cache hit, single-flight join, queued completion, and
observer exception isolation. Add feature tests for discovery, hydration, and
first/tail Prowlarr-wave observations.

- [x] **Step 2: Run Search RED tests**

```bash
cd /Users/young/Documents/telepiplex/features/search
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_search_logging.py tests/test_source_schedule.py tests/test_feature_service.py
```

Expected: the new tests fail because the helper, observer, and events are
absent; existing tests pass.

- [x] **Step 3: Implement minimal Search observations**

Keep legacy `log_search_event()` output unchanged. Add a helper that emits:

```python
extra = {
    "event_name": event,
    "diagnostic_fields": {
        "stage": "performance",
        "status": status,
        "duration_ms": duration_ms,
        "input": {"search_session_id": search_session_id},
        "output": safe_facts,
    },
}
```

Add a best-effort scheduler observer around cache hit, join, wait, completion,
and failure. Emit bounded discovery, hydration, and Prowlarr-wave events from
`SearchFeature`. Do not change request order, timeout, cache key, candidate
payload, or release-report timing.

- [x] **Step 4: Run Search GREEN tests**

Run the Step 2 command. Expected: all selected tests pass and no new
measurement payload contains a raw query, title, URL, or source identity.

- [x] **Step 5: Review Task 1**

Inspect each new event call and confirm no key/fact includes a title, query,
link, URL, identity, raw result, or exception message. Record changed files in
the execution ledger; do not use Git.

### Task 2: Add Download pacing and request observations

**Files:**
- Create: `features/download/src/telepiplex_download/observability.py`
- Modify: `features/download/src/telepiplex_download/client.py`
- Modify: `features/download/src/telepiplex_download/runtime.py`
- Modify: `features/download/tests/test_client_pacing.py`

**Interfaces:**
- Produces `emit_download_observation(event_name: str, **facts) -> None`.
- Extends `Open115Client(..., on_observation=None)` compatibly.
- The callback receives endpoint class/operation, duration, status class,
  retryability, and cooldown only.

- [x] **Step 1: Write failing Download observation tests**

```python
def test_request_observation_exposes_duration_not_path_or_headers():
    observed = []
    client = Open115Client(
        {"access_token": "test"},
        session=success_session(),
        on_observation=lambda name, facts: observed.append((name, facts)),
    )
    client.get_file_info("/private/file.mkv")
    name, facts = observed[-1]
    assert name == "download.request.completed"
    assert facts["endpoint_class"] == "storage.read"
    assert "path" not in facts and "headers" not in facts
```

Cover wait threshold, 429 cooldown, polling delay transitions, and an observer
that raises while the same request/poll still succeeds.

- [x] **Step 2: Run Download RED tests**

```bash
cd /Users/young/Documents/telepiplex/features/download
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_client_pacing.py
```

Expected: new callback/event tests fail; existing pacing tests stay green.

- [x] **Step 3: Implement Download observation adapter and hooks**

The adapter emits explicit diagnostic records. Pass it from Download runtime to
`Open115Client`. In `_request()`, use the existing pacer wait and a
monotonic request timer; emit completed/failed request, wait, and 429 cooldown
events. In `wait_for_download()`, emit only when next delay changes. Wrap
callback invocation with `try/except Exception: pass`.

- [x] **Step 4: Run Download GREEN tests**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_client_pacing.py \
  tests/test_client_move_safety.py tests/test_feature_runtime.py
```

Expected: all Download tests pass with unchanged request, pacing, retry, and
polling outcomes when no observer is configured.

- [x] **Step 5: Review Task 2**

Confirm the adapter never serializes config, path, params, data, files,
response, URL, token, header, or exception text.

### Task 3: Build adaptive trusted directory preflight

**Files:**
- Modify: `features/rename/src/telepiplex_rename/file_executor.py`
- Modify: `features/rename/tests/test_file_executor.py`
- Modify: `features/rename/tests/test_file_first_processor.py`
- Modify: `features/rename/tests/test_regression_pressure.py`

**Interfaces:**
- Retains `build_file_transaction_snapshot(storage, *, file_paths, source_parent_paths) -> FileTransactionSnapshot`.
- Adds private `_build_directory_preflight_snapshot(...) -> FileTransactionSnapshot | None`.
- Adds private `_complete_directory_items(storage, directory_id) -> list[dict] | None`.

- [x] **Step 1: Write failing trusted-listing tests**

Assert a shared source/target listing returns the same `PreflightFileInfo`
values as exact reads and is selected only when its budget is lower. Cover
missing target parent, two pages, empty page with `has_more`, repeated page,
conflicting duplicate IDs, missing SHA-1/size, and one-file-per-directory
exact fallback.

- [x] **Step 2: Run Rename preflight RED tests**

```bash
cd /Users/young/Documents/telepiplex/features/rename
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_file_executor.py tests/test_file_first_processor.py tests/test_regression_pressure.py
```

Expected: new listing tests fail while existing snapshot tests pass.

- [x] **Step 3: Implement complete listing and fallback**

Use `offset`, `limit=1000`, and `show_dir=1`. Reject malformed,
non-progressing, over-limit, conflicting, or incomplete pages. Project only
stable provider ID, lowercased SHA-1, and non-negative size. If the directory
budget is not strictly lower or any selected listing is untrusted, call existing
`prefetch_file_info()` for every requested path. Never mix partial listing
results with absence claims from exact reads.

- [x] **Step 4: Run Rename preflight GREEN tests**

Run the Step 2 command. Expected: shared-directory preflight uses lists;
uncertain responses use exact fallback; all existing conflict/replay outcomes
remain unchanged.

- [x] **Step 5: Update the regression budget**

Extend the 16-file recorder with physical-style read accounting. Assert a
trusted same-parent snapshot performs no per-child preflight info reads while
post-move listing and cleanup reads remain present.

### Task 4: Add fresh native-move pre-submit gate

**Files:**
- Modify: `features/rename/src/telepiplex_rename/file_executor.py`
- Modify: `features/rename/tests/test_file_executor.py`
- Modify: `features/rename/tests/test_file_first_processor.py`

**Interfaces:**
- Adds private `_gate_native_move_chunk(storage, chunk, journal=None) -> tuple[list[dict], dict[int, FileExecutionOutcome]]`.
- Rejected IDs never enter `move_files_by_id()`; accepted items retain their
  existing index, source-parent ID, target-directory ID, and paths.

- [x] **Step 1: Write failing race-gate tests**

Create controllable storage fixtures that mutate source/target after preflight.
Assert a stale source ID/name and foreign target name fail that file and omit
its ID from native move. Assert source absent plus exact target ID/name becomes
`organized` without a move. Assert a failed gate listing leaves that group
unmoved while a different target group may continue.

- [x] **Step 2: Run native-gate RED tests**

Run the Task 3 command with new selectors. Expected: new tests fail because
`move_files_by_id()` trusts only the old snapshot.

- [x] **Step 3: Implement fresh chunk gate**

Use the complete-listing helper before each native batch. Require source ID and
expected current name; reject a foreign target name. Treat only source-absent
plus exact target ID/name as already organized, journal it as verified, retain
allowed IDs in batch order, and keep current post-move reconciliation intact.

- [x] **Step 4: Run native-gate GREEN tests**

```bash
cd /Users/young/Documents/telepiplex/features/rename
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_file_executor.py tests/test_file_first_processor.py \
  tests/test_regression_pressure.py tests/test_operations.py
```

Expected: all preflight, journal, reconciliation, cleanup, and race-gate tests
pass.

- [x] **Step 5: Review Task 4**

Verify rejected IDs never reach native move and no gate replaces post-move or
cleanup fresh reads.

### Task 5: Cross-Feature verification and live-measurement protocol

**Files:**
- Modify: this plan to mark completed steps and record exact results.
- Modify: the spec only if a test exposes an approved-spec ambiguity.

**Interfaces:**
- Consumes Tasks 1–4 without changing public result shapes.
- Produces local verification evidence and a five-to-ten-operation live
  measurement checklist.

- [x] **Step 1: Run Core tests**

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests
```

- [x] **Step 2: Run Feature suites independently**

```bash
for module in download search rename sync caption; do
  (
    cd "/Users/young/Documents/telepiplex/features/$module"
    PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider tests
  )
done
```

- [x] **Step 3: Inspect hygiene and boundary**

Search new observation calls for forbidden keys (`url`, `path`, `query`,
`magnet`, `token`, `headers`, `response`), compile changed Python files,
then run:

```bash
test ! -e /Users/young/Documents/telepiplex/.git
test ! -e /Users/young/Documents/telepiplex/.worktrees
test -d /Users/young/Documents/telepiplex/.stfolder
```

- [ ] **Step 4: Record live protocol**

After Syncthing shows `Up to Date / 最新`, collect five to ten real operations:
cold/warm candidate search, a movie, bounded season/episode, and a 16-file
release. Group by `operation_id`; compare discovery, hydration, Prowlarr
waves, 115 pacer wait/HTTP elapsed/429, and rename preflight/gate/postcondition
counts. Do not publish or run Unraid Git commands from this Mac workspace.

## Execution ledger

Append one entry per finished task:

```text
Task N RED: exact command and new-test failure
Task N GREEN: exact command and passed count
Task N changed files: exact paths
Task N review: design verdict, quality verdict, open findings
Ruling: decision — reason — cost if wrong
Task N complete
```

Task 1 RED: focused new tests failed for absent measurement helper, scheduler
observer, and Search event calls; cancellation repair RED raised no
`CancelledError`.
Task 1 GREEN: Search logging/scheduler 19 passed; all 132 Feature nodes passed
in four runner-safe shards (42/42/26/22); cancellation repair independently
passed (2 passed).
Task 1 changed files: `features/search/src/telepiplex_search/search_logging.py`,
`features/search/src/telepiplex_search/source_schedule.py`,
`features/search/src/telepiplex_search/service.py`,
`features/search/tests/test_search_logging.py`,
`features/search/tests/test_source_schedule.py`,
`features/search/tests/test_feature_service.py`.
Task 1 review: approved after one P1 repair; async observer cancellation now
propagates while ordinary observer failure stays isolated.
Task 1 complete.

Task 2 RED: five new pacing-observation tests failed while the optional
observer interface was absent; the logger-adapter privacy mutation test failed
when its temporary leak was enabled.
Task 2 GREEN: pacing, move-safety, and runtime suite 112 passed, 31 subtests
passed; privacy repair review approved.
Task 2 changed files: `features/download/src/telepiplex_download/observability.py`,
`features/download/src/telepiplex_download/client.py`,
`features/download/src/telepiplex_download/runtime.py`,
`features/download/tests/test_client_pacing.py`.
Task 2 review: approved after one P2 test-coverage repair; the logger-facing
adapter now has completed/failed whitelist regressions for sensitive inputs.
Task 2 complete.

Task 3 RED: initial trusted-listing nodes failed as expected (3 failed, 85
passed); P1 repair nodes failed for malformed unrequested item, semantic
repeated page, and malformed `has_more`.
Task 3 GREEN: Task 3 suite 91 passed; complete Rename suite 332 passed, 8
subtests passed; focused repair review 3 passed.
Task 3 changed files: `features/rename/src/telepiplex_rename/file_executor.py`,
`features/rename/tests/test_file_executor.py`,
`features/rename/tests/test_regression_pressure.py`.
Task 3 review: approved after two P1 repairs. Complete directory facts now use
canonical stable page identities and strict pagination flags; any distrust
falls back atomically to exact reads. The 16-file pressure fixture has no
per-child preflight reads while its post-move and cleanup reads remain.
Task 3 complete.

Task 4 RED: initial race-gate nodes failed (3); later regressions failed for
foreign source replacement (1), duplicate source name (1), source/target
state-matrix ambiguities (2), and transaction topology (3).
Task 4 GREEN: final required Rename subset 106 passed; syntax compilation
passed; independent final safety and test reviews approved.
Task 4 changed files: `features/rename/src/telepiplex_rename/file_executor.py`,
`features/rename/tests/test_file_executor.py`,
`features/rename/tests/test_regression_pressure.py`.
Task 4 review: approved after hardening the pre-submit gate into a strict
source/target state matrix plus full-transaction logical and physical-alias
collision guards. Rejected IDs never reach native move; post-move
reconciliation and cleanup fresh reads remain mandatory. Pressure accounting
is now 61 calls for shared parent (six listings) and 72 for two source parents
(eleven listings).
Task 4 complete.

Task 5 GREEN: final fresh Core suite passed (528 passed, 1 skipped, 198
subtests). Fresh Feature suites passed independently: Download 112 passed, 31
subtests; Search 511 passed, 2 skipped, 72 subtests; Rename 346 passed, 8
subtests; Sync 138 passed, 64 subtests; Caption 1 passed. The Rename fixture
repair was independently re-reviewed after the final run.
Task 5 changed files: this plan and
`features/rename/tests/test_feature_processor.py` for test-double state
fidelity discovered during cross-feature verification.
Task 5 review: approved. The fixture now mirrors successful rename and legacy
move directory state without relaxing the production fresh native-move gate;
all production observation adapters remain bounded by their privacy
whitelists.
Task 5 hygiene: changed production Python compiled successfully; scoped
observation wiring was inspected; `.git` and `.worktrees` are absent and
`.stfolder` is present.
Ruling: local implementation verification is complete; defer the live
five-to-ten-operation measurement protocol until Syncthing reports `Up to
Date / 最新`, because it requires real operations and must not be synthesized
from tests.

### Preflight review

| Tasks | Shared file or interface | Finding |
|---|---|---|
| 1 and 2 | Diagnostic transport only | Independent Feature implementations; both use existing explicit diagnostic records and do not share source files. |
| 1 and 3/4 | None | Search instrumentation has no Rename dependency. |
| 2 and 3/4 | Open115 `get_file_list` capability | Download preserves the capability shape; Rename consumes the existing shape and supplies its own strict pagination parser. |
| 3 and 4 | `file_executor.py` and `FileTransactionSnapshot` | Sequential execution is required: Task 4 consumes Task 3's complete-listing helper. |
| 5 and 1–4 | All public interfaces | Task 5 is verification only and changes no public result shape. |

Ruling: use this plan as the durable local execution ledger and review each
changed file plus fresh test evidence without Git/worktrees/commits — the Mac
workspace contract forbids Git; the cost if wrong is less automated diff
packaging, mitigated by task-scoped review and full final suites.

## Plan self-review

- Every design acceptance criterion maps to Tasks 1–5.
- All new interfaces are defined before their consumers.
- No task adds external telemetry, Git, publication, or unapproved Search
  business-rule work.
- The snapshot has exact fallback and a fresh gate; it never substitutes a
  preflight fact for a postcondition.
