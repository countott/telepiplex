# media_metadata v2-only and Terminal Render Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make telepiplex use one strict media_metadata v2 contract end-to-end while fixing English naming, `/m` recovery, terminal-message duplication, approved copy, and coordinated versions.

**Architecture:** SDK v2 is the only cross-Feature contract. Search projects private evidence into v2, Download passes it unchanged, Rename builds an organization context directly from v2 and records file effects separately, and Sync consumes v2 plus organization results. Host rendering keeps segment identity through queued terminal renders.

**Tech Stack:** Python 3.12, asyncio, SQLite, python-telegram-bot mocks, pytest/unittest, YAML/TOML manifests.

**Spec:** `docs/superpowers/specs/2026-08-26-media-metadata-v2-only-and-terminal-render-design.md`

## Global Constraints

- Host `v3.6.5-host`; Host API remains `1.7`.
- Plugin SDK, search, download, rename, and sync are `2.0.0`; caption remains `0.1.4`.
- `identity.title_en` is distinct from `title_original` and is authoritative for all Rename output names.
- New or recovered schema 1 metadata is rejected; no v1 converter remains on the Rename path.
- `media_metadata` stays immutable; file effects belong to `organization_result`.
- Mac-local only: no Git, worktrees, publication, tags, or GitHub writes.

---

### Task 1: Canonical SDK v2 contract

**Files:**
- Modify: `sdk/src/telepiplex_plugin_sdk/media_metadata_v2.py`
- Modify: `sdk/src/telepiplex_plugin_sdk/__init__.py`
- Modify: `tests/test_media_metadata_v2.py`
- Modify: `tests/test_media_metadata_pressure.py`

**Interfaces:**
- Produces: `validate_media_metadata_v2(value)`, `attach_media_metadata_v2(metadata, value)`, `extract_confirmed_media_metadata_v2(metadata)` with required `identity.title_en`.
- Removes: `convert_media_metadata_v1_to_v2` from active runtime use and public exports.

- [ ] Add failing fixtures proving `title_en` is required and Japanese/Spanish `title_original` remains distinct.
- [ ] Run the focused SDK tests and confirm failure at the identity key contract.
- [ ] Add `title_en` validation, remove converter exports, and isolate common constants from v1-specific validation.
- [ ] Re-run focused SDK tests until green.

### Task 2: Search v2 projection and English authority

**Files:**
- Modify: `features/search/src/telepiplex_search/media_metadata_v2.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/tests/test_media_metadata_v2.py`
- Modify: `features/search/tests/test_feature_service.py`

**Interfaces:**
- Consumes: confirmed private candidate identity fields including `official_english_title`.
- Produces: strict downstream v2 with `title_zh`, `title_en`, and `title_original`.

- [ ] Add failing Game Life and Hundred Years fixtures asserting English and original titles are separate.
- [ ] Run the focused Search tests and observe missing `title_en`.
- [ ] Project verified English title into v2 and fail closed when no verified English authority exists.
- [ ] Re-run Search projection and capability tests until green.

### Task 3: Download strict v2 and approved copy

**Files:**
- Modify: `features/download/src/telepiplex_download/service.py`
- Modify: `features/download/tests/test_feature_runtime.py`

**Interfaces:**
- Consumes and emits byte-equivalent canonical v2.
- Produces statuses `已选定片源，提交下载` and `下载完成，开始整理`.

- [ ] Add failing tests for missing `title_en`, schema 1 rejection, exact copy, and unchanged completion payload.
- [ ] Run the focused Download tests and confirm the copy/contract failures.
- [ ] Use only v2 validation and update every persistent-status variant of the approved copy.
- [ ] Re-run focused Download tests until green.

### Task 4: Rename direct v2 organization and English naming

**Files:**
- Modify: `features/rename/src/telepiplex_rename/media_metadata_v2.py`
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: `features/rename/src/telepiplex_rename/processor.py`
- Modify: `features/rename/src/telepiplex_rename/models.py`
- Modify: `features/rename/tests/test_media_metadata_v2.py`
- Modify: `features/rename/tests/test_feature_processor.py`

**Interfaces:**
- Produces: one direct v2 adoption path for initial payloads and runtime recovery.
- Produces: naming identity whose English field comes only from `identity.title_en`.
- Removes: `private_v1_adapter_from_v2` and runtime v1 conversion.

- [ ] Add failing tests for Game Life all-English output and `/m` recovered v2 selecting the real processor.
- [ ] Run those focused tests and confirm Japanese naming and fallback-unorganized failures.
- [ ] Replace the recovered v1 attach branch with strict v2 adoption; build observed episode mapping directly from scope and file tree.
- [ ] Keep media metadata unchanged and put file effects in `organization_result`.
- [ ] Re-run focused Rename tests until green.

### Task 5: Sync v2 consumption

**Files:**
- Modify: `features/sync/src/telepiplex_sync/sync_service.py`
- Modify: `features/sync/tests/test_sync_service.py`

**Interfaces:**
- Consumes: v2 identity/provider refs/category and separate organization targets.
- Produces: Plex routing and external ID lookup without v1 items or placement extensions.

- [ ] Add failing tests with canonical v2 movie and episode fixtures.
- [ ] Run focused Sync tests and confirm the existing v1 extractor rejects them.
- [ ] Replace v1 extraction/routing with v2 scope, category, provider refs, and organization targets.
- [ ] Re-run focused Sync tests until green.

### Task 6: Host queued terminal render identity

**Files:**
- Modify: `app/handlers/interaction_handler.py`
- Modify: `tests/test_interaction_handler.py`

**Interfaces:**
- OperationReportSink queues both `OperationRecord` and segment id.
- A sealed segment with `rendered_revision >= queued revision` consumes the queued render without calling the legacy sender.

- [ ] Add a failing three-revision text-segment test with a blocked renderer, queued terminal report, and concurrent seal.
- [ ] Run the single test and observe two `send_message` calls.
- [ ] Preserve segment identity in pending work and suppress already-rendered sealed snapshots.
- [ ] Re-run interaction tests until green.

### Task 7: Coordinated versions and documentation

**Files:**
- Modify: `app/115bot.py`
- Modify: `sdk/pyproject.toml`
- Modify: `features/{search,download,rename,sync}/{pyproject.toml,manifest.yaml,README.md}`
- Modify: technical identity and component contract tests.

**Interfaces:**
- Produces exact versions from Global Constraints and SDK pins `telepiplex-plugin-sdk==2.0.0`.

- [ ] Update failing identity expectations first.
- [ ] Run identity/version tests and confirm current versions fail.
- [ ] Update runtime sources, manifests, dependencies, and current-version README build examples.
- [ ] Re-run identity/version tests until green.

### Task 8: Integrated verification

**Files:**
- Test only.

**Interfaces:**
- Verifies all outputs from Tasks 1–7 together.

- [ ] Run SDK and Host focused suites.
- [ ] Run download, search, rename, and sync complete Feature suites.
- [ ] Run the Host complete suite and static v1-pressure scan.
- [ ] Verify `.git` and `.worktrees` remain absent and `.stfolder` remains present without invoking Git.
- [ ] Record exact commands, counts, failures, and changed files for Syncthing handoff.
