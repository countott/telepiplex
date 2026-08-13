# rename Colon Fixes Implementation Plan

> **For agentic workers:** Execute inline in this session. Do not use Git or publish from the Mac workspace.

**Goal:** Make rename metadata confirmation accept colon-bearing durable job IDs and remove colons only from rename-generated final target names.

**Architecture:** Parse the callback index from the right so the internal job ID remains opaque. Keep source-path normalization unchanged, and introduce a separate final-target component sanitizer that removes both ASCII and full-width colons before storage operations are built.

**Tech Stack:** Python 3.12, `unittest`, `pytest`, telepiplex Feature SDK.

## Global Constraints

- Do not change Search scope classification or metadata strictness behavior.
- Do not change Host poster delivery or fallback behavior.
- Do not remove colons from job IDs, callback protocols, source paths, or canonical metadata titles.
- Do not run Git on the Mac workspace.

---

### Task 1: Opaque rename callback job IDs

**Files:**
- Modify: `features/rename/tests/test_feature_processor.py`
- Modify: `features/rename/src/telepiplex_rename/service.py`

**Interface:** `RenameFeature._metadata_callback(request, payload)` continues accepting `metadata:<job_id>:<candidate-index>` and preserves the entire `<job_id>` even when it contains colons.

- [x] Change the existing ambiguous-magnet integration fixture to use the real durable ID shape `telegram:219358366`.
- [x] Run that single test and confirm it fails with `invalid_callback`.
- [x] Remove the exact `metadata:` prefix and split the remaining payload once from the right.
- [x] Re-run the single test and confirm the job resumes and completes.

### Task 2: Target-only colon removal

**Files:**
- Modify: `features/rename/tests/test_media_auto_rename.py`
- Modify: `features/rename/tests/test_tvdb_rename.py`
- Modify: `features/rename/src/telepiplex_rename/media_naming.py`
- Modify: `features/rename/src/telepiplex_rename/tvdb_rename.py`

**Interface:** `sanitize_target_name(value: str) -> str` removes Windows-invalid filename punctuation, including `:` and `：`, while `_clean_path()` keeps using source-safe normalization that preserves existing source path characters.

- [x] Update movie naming expectations so final folders and file names contain no colon.
- [x] Add a strict-series plan test proving a colon-bearing source path remains locatable while generated target root, directory, filename, and final path contain no colon.
- [x] Run the focused tests and confirm the current implementation fails only the new expectations.
- [x] Add the target-only sanitizer and apply it at every final target materialization boundary.
- [x] Re-run focused tests, then the complete rename Feature suite.
