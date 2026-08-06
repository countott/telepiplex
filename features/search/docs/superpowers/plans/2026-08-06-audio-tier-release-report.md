# Search Audio Tier Release Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement this plan task-by-task. This Mac workspace prohibits Git; local verification checkpoints replace commit steps.

**Goal:** Implement the approved compact Prowlarr report with title-level scope, no video codec, and user-friendly audio capability tiers, then bump search to 1.7.0.

**Architecture:** Keep release-title parsing inside `release_report.py`. Replace the deferred audio-format labels with one deterministic display label, move the shared scope label into the report header, and preserve the existing two-line result boundary without blank lines between releases.

**Tech Stack:** Python 3.12, unittest/pytest, YAML, setuptools metadata

## Global Constraints

- Product-facing name remains lowercase `telepiplex`.
- Do not run Git or publish from the Mac workspace.
- Display order is resolution, source, dynamic range, edition, audio tier.
- Audio labels are `Nch沉浸`, `?ch沉浸`, `Nch环绕`, `2ch立体`, or `?ch`.
- Search version must be synchronized as `1.7.0`.

---

### Task 1: Lock the report behavior with failing tests

**Files:**
- Modify: `features/search/tests/test_release_report.py`

**Interfaces:**
- Consumes: `format_release_report(query, gate, ranked, indexer_summary) -> str`
- Produces: Literal user-visible expectations for header scope, compact rows, and audio tiers

- [ ] Replace deferred-audio assertions with table-driven expectations for Atmos, DTS:X, Auro-3D, FLAC multichannel, stereo, and unknown channels.
- [ ] Assert video codecs and per-result scope are absent.
- [ ] Assert the shared scope appears in the title and adjacent releases have no blank separator.
- [ ] Run `test_release_report.py` and confirm failures are caused by the old report behavior.

### Task 2: Implement the compact report

**Files:**
- Modify: `features/search/src/telepiplex_search/release_report.py`

**Interfaces:**
- Consumes: Prowlarr release dictionaries with `title` and `scope_label`
- Produces: One audio tier per result and one optional shared scope in the report title

- [ ] Parse explicit channel layouts and immersive markers conservatively.
- [ ] Remove video codec and per-result scope from specifications.
- [ ] Append the shared non-movie scope to the title.
- [ ] Run `test_release_report.py` and confirm it passes.

### Task 3: Synchronize the Search Feature version

**Files:**
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/src/telepiplex_search.egg-info/PKG-INFO`
- Modify: `features/search/README.md`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `tests/test_technical_identity_migration.py`
- Modify: `tests/test_unraid_publish_script.py`

**Interfaces:**
- Consumes: Search Feature version `1.7.0`
- Produces: Matching runtime, package, documentation, and publisher-test identities

- [ ] Update current version references from `1.6.0` to `1.7.0`.
- [ ] Preserve historical documentation that explicitly describes the earlier 1.6.0 integration.
- [ ] Run Search Feature tests and the affected root contract tests.

### Task 4: Final verification

**Files:**
- Verify only

**Interfaces:**
- Consumes: Completed local implementation
- Produces: Test evidence and Syncthing handoff

- [ ] Run the full Search Feature test suite from `features/search`.
- [ ] Run affected root version and publisher tests.
- [ ] Verify `.git` and `.worktrees` are absent and `.stfolder` is present.
