# rename Subtitle and Callback Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development while implementing each task. This Mac workspace forbids Git, so every task ends with local verification instead of a commit.

**Goal:** Make rename organize every mapped external subtitle with the fixed `.chi` filename label, preserve duplicates with deterministic anti-collision suffixes, and render durable metadata candidate buttons with callbacks below Telegram's 64-byte limit.

**Architecture:** Subtitle planning will stop classifying language and will use only file type plus confirmed movie or episode identity. A `.chi` label will be added as a naming convention, while semantic language evidence remains `unknown` for downstream caption processing. Metadata confirmation will use a short durable selection token stored with the rename job instead of embedding the full job ID in callback data.

**Tech Stack:** Python 3.12, pytest, unittest, SQLite-backed rename job state, Telegram inline callbacks, telepiplex Feature SDK.

## Global Constraints

- Product copy must use lowercase `telepiplex`.
- Modify rename and Download chain tests only; do not modify search.
- Never overwrite a distinct subtitle target.
- Duplicate subtitles with the same episode and extension use deterministic `.variant-02`, `.variant-03`, and later suffixes before `.chi`.
- `forced`, `sdh`, `cc`, detected language, and language variant markers never survive in target names.
- `.chi` is a fixed staging filename label, not detected language evidence.
- Do not use Git in `/Users/young/Documents/telepiplex`.

---

### Task 1: Fix the subtitle naming contract

**Files:**
- Modify: `features/rename/tests/test_subtitles.py`
- Modify: `features/rename/tests/test_subtitle_preservation.py`
- Modify: `features/rename/tests/test_file_facts.py`
- Modify: `features/rename/src/telepiplex_rename/subtitles.py`
- Modify: `features/rename/src/telepiplex_rename/file_facts.py`
- Modify: `features/rename/src/telepiplex_rename/processor.py`

**Interfaces:**
- Consumes: scanned `file_tree` nodes and confirmed movie or episode identity.
- Produces: subtitle operations whose target basename is `<stem>[.variant-NN].chi<extension>`.

- [x] Add failing tests proving unmarked, English-marked, Chinese-marked, `forced`, `sdh`, and `cc` subtitles all receive `.chi` targets.
- [x] Add a failing test proving `Discovery` is not language evidence and semantic language fields remain `unknown`.
- [x] Add a failing test proving duplicate same-extension subtitles receive stable `.variant-NN` names without overwrite.
- [x] Run the focused tests and verify failures are caused by language filtering and non-`chi` output.
- [x] Remove language classification and language-based eligibility from subtitle planning.
- [x] Keep filename-noise cleanup separate from semantic language evidence.
- [x] Include every mapped subtitle in deterministic and AI-assisted series mapping regardless of its original language marker.
- [x] Re-run focused subtitle, file-fact, and processor tests until green.

### Task 2: Fix durable metadata candidate callbacks

**Files:**
- Modify: `features/rename/tests/test_feature_processor.py`
- Modify: `features/rename/src/telepiplex_rename/jobs.py`
- Modify: `features/rename/src/telepiplex_rename/service.py`

**Interfaces:**
- Consumes: a durable rename `job_id` and candidate index.
- Produces: `rename:metadata:<short-token>:<index>` callback data of at most 64 UTF-8 bytes and a durable token-to-job lookup.

- [x] Add a failing inventory test using the 48-byte file-first job ID and assert every callback is at most 64 bytes.
- [x] Add a failing callback test proving a short token resolves to the original durable job.
- [x] Add a failing restore test proving an awaiting confirmation rebuilds the same valid callback after Feature restart.
- [x] Add a failing message test proving numeric input selects the current metadata candidate only during `metadata_confirmation`.
- [x] Persist a deterministic short selection token with awaiting metadata job state.
- [x] Resolve callbacks by token while retaining owner, active operation, inventory job, and stale-state checks.
- [x] Validate generated callback length before reporting the keyboard.
- [x] Implement numeric `1` through `5` as a rename-only fallback routed through the same confirmation method.
- [x] Re-run focused metadata confirmation and inventory tests until green.

### Task 3: Correct result wording and the Download boundary contract

**Files:**
- Modify: `features/rename/tests/test_feature_processor.py`
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: `features/download/tests/test_feature_runtime.py`

**Interfaces:**
- Consumes: file-first counters and the Download `file_tree` event payload.
- Produces: explicit actual-change, already-standard, retained, conflict, and failure counts; Download continues passing all subtitle nodes unchanged.

- [x] Add a failing result-text test proving zero actual changes are not described as successful renames.
- [x] Add a Download regression test with unmarked and marked subtitle filenames and assert both remain in `download.completed.file_tree`.
- [x] Change rename completion wording to expose actual changes separately from already-standard files and retained files.
- [x] Keep work-group completion bookkeeping separate from user-facing file mutation counts.
- [x] Run the focused rename and Download tests until green.

### Task 4: Align rename release identity and verify the scoped chain

**Files:**
- Modify: `features/rename/manifest.yaml`
- Modify: `features/rename/pyproject.toml`
- Modify: `features/rename/README.md`
- Modify: `features/rename/src/telepiplex_rename.egg-info/PKG-INFO`

**Interfaces:**
- Consumes: completed behavior changes.
- Produces: rename version `1.5.1`; Download stays `1.0.14` because its runtime contract does not change.

- [x] Update every checked-in rename release identity from `1.5.0` to `1.5.1`.
- [x] Run rename targeted tests for subtitles, file facts, inventory metadata confirmation, and result wording.
- [x] Run the complete rename Feature suite.
- [x] Run the complete Download Feature suite.
- [x] Run relevant Host interaction tests without modifying Host or search.
- [x] Verify `.git` and `.worktrees` are absent and `.stfolder` is present.
- [x] Report all changed files and actual test results; wait for user-operated Syncthing handoff.
