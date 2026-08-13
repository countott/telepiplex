# rename External Subtitles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic and bounded-AI external subtitle organization to rename for `.srt`, `.ass`, `.sup`, and `.vtt`, including sparse cross-directory episode pairing and subtitle-only merges.

**Architecture:** Introduce a focused subtitle evidence/planning module. Expand content probing to use subtitle paths when video evidence is absent, enrich the confirmed video plan with subtitle operations before the shared preflight, and update inventory terminal validation to accept normalized `.chi` subtitle files.

**Tech Stack:** Python 3.12, asyncio Feature runtime, `storage.provider`, `media_metadata v1`, pytest/unittest.

## Global Constraints

- Do not run Git or create Git metadata in the Mac workspace.
- Preserve the existing `content_probe -> media.search.resolve_metadata -> confirmed media_metadata` authority chain.
- Keep only bilingual simplified-Chinese-and-English subtitles first, otherwise simplified-Chinese-only; exclude all other known languages.
- Unknown subtitle language or ambiguous episode identity blocks writes for the current direct child.
- Preserve `.srt`, `.ass`, `.sup`, and `.vtt` source extensions and generate `.chi` language suffixes.
- Apply cross-platform target sanitization to every generated directory and file name.

---

### Task 1: Subtitle evidence and policy

**Files:**
- Create: `features/rename/src/telepiplex_rename/subtitles.py`
- Create: `features/rename/tests/test_subtitles.py`

**Interfaces:**
- Produces: `SUBTITLE_EXTENSIONS`, `collect_subtitle_evidence(file_tree)`, and `build_subtitle_operations(...)`.
- Consumes: confirmed series titles, optional video operations, source file-tree nodes, and bounded AI episode assignments.

- [x] Write failing tests for four formats, language classification, bilingual preference, sparse episode maps, flat subtitle paths, subtitle-only maps, and ambiguity blocking.
- [x] Run `test_subtitles.py` and confirm failures.
- [x] Implement normalized subtitle evidence and operation construction.
- [x] Run `test_subtitles.py` and confirm passes.

### Task 2: Query and metadata integration

**Files:**
- Modify: `features/rename/src/telepiplex_rename/content_probe.py`
- Modify: `features/rename/src/telepiplex_rename/ai.py`
- Modify: `features/rename/tests/test_feature_processor.py`

**Interfaces:**
- Consumes: subtitle paths as secondary identity evidence and primary evidence only for subtitle-only trees.
- Produces: the existing bounded probe schema and evidence-constrained AI recovery/mapping payloads.

- [x] Add failing tests for subtitle-only query extraction and video-first subtitle evidence.
- [x] Run focused probe tests and confirm failures.
- [x] Extend probing without changing video-first behavior.
- [x] Run focused probe tests and confirm passes.

### Task 3: Shared organization plan and merge execution

**Files:**
- Modify: `features/rename/src/telepiplex_rename/processor.py`
- Modify: `features/rename/src/telepiplex_rename/tvdb_rename.py`
- Modify: `features/rename/tests/test_tvdb_rename.py`
- Modify: `features/rename/tests/test_feature_processor.py`

**Interfaces:**
- Consumes: video rename plan plus subtitle operations.
- Produces: one preflighted operation list with `media_kind` values `video` and `subtitle`.

- [x] Add failing tests for existing-directory merge, subtitle-only writes, whole-plan preflight, known-language exclusion, and no cleanup on ambiguity.
- [x] Run focused processor tests and confirm failures.
- [x] Merge subtitle operations before `_assert_no_target_conflicts` and execute through the existing storage path.
- [x] Run focused processor tests and confirm passes.

### Task 4: Inventory terminal contract and documentation

**Files:**
- Modify: `features/rename/src/telepiplex_rename/inventory.py`
- Modify: `features/rename/tests/test_inventory.py`
- Modify: `features/rename/README.md`
- Modify: `features/rename/manifest.yaml`
- Modify: `features/rename/pyproject.toml`

**Interfaces:**
- Produces: terminal recognition for normalized `.chi` subtitles alongside video or in subtitle-only roots.

- [x] Add failing terminal-structure tests.
- [x] Implement subtitle-aware terminal recognition without admitting raw attachment names.
- [x] Update rename behavior documentation and bump the normal semantic version to `1.4.0`.
- [x] Run inventory and version-contract tests.

### Task 5: Verification

**Files:**
- Test only; no additional production files expected.

**Interfaces:**
- Verifies: focused subtitle behavior, all rename regressions, and repository-wide regressions.

- [x] Run the complete rename Feature suite.
- [x] Run the project tests required by the local telepiplex handoff contract.
- [x] Verify `.git` and `.worktrees` remain absent and `.stfolder` remains present.
- [x] Report exact files, commands, results, and Syncthing handoff requirement.
