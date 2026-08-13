# File-tree Query Recovery Implementation Plan

**Goal:** Add deterministic query evidence and a bounded AI recovery gate before rename calls the existing search metadata capability.

**Architecture:** `content_probe` parses the complete file tree into candidates, confidence, evidence, and structural facts. `query_recovery` may recover only low-confidence probes using rename's existing AI transport and validates the answer against probe evidence before `service` forwards it to `media.search.resolve_metadata`.

**Tech Stack:** Python 3.12, unittest/pytest, telepiplex Feature capability RPC.

## Global Constraints

- No Git operations on the Mac workspace.
- No direct external metadata calls from rename.
- No file mutation before confirmed `media_metadata v1`.
- Never send the complete file tree to AI.
- AI failure or unsupported output blocks safely.

### Task 1: Expand the probe contract and deterministic grammar

**Files:**
- Modify: `src/telepiplex_rename/content_probe.py`
- Test: `tests/test_feature_processor.py`

- [ ] Add failing literal regression tests for evidence fields, numeric titles,
  repeated groups, site prefixes, anime absolute episodes, and episode chains.
- [ ] Run the focused tests and verify each fails for the missing behavior.
- [ ] Implement bounded candidate/evidence collection and episode grammar.
- [ ] Run the focused tests and existing probe tests until green.

### Task 2: Add bounded AI query recovery

**Files:**
- Create: `src/telepiplex_rename/query_recovery.py`
- Modify: `src/telepiplex_rename/ai.py`
- Test: `tests/test_feature_processor.py`

- [ ] Add failing tests for accepted evidence-bound recovery, fabricated-title
  rejection, missing-AI blocking, and bounded representative paths.
- [ ] Run the tests and confirm expected failures.
- [ ] Implement the JSON-only prompt, response normalization, and deterministic
  evidence validator.
- [ ] Run recovery tests until green.

### Task 3: Integrate the recovery gate with metadata resolution

**Files:**
- Modify: `src/telepiplex_rename/service.py`
- Test: `tests/test_feature_processor.py`

- [ ] Add failing integration tests proving high-confidence direct handoff,
  low-confidence recovery, invalid recovery without storage calls, and that
  the existing probe reaches `media.search.resolve_metadata`.
- [ ] Run the tests and confirm expected failures.
- [ ] Resolve the probe before capability RPC and raise a typed non-retryable
  metadata error when no evidence-bound query exists.
- [ ] Run integration tests until green.

### Task 4: Version and verify

**Files:**
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify generated package metadata only if required by existing project tests.

- [ ] Bump rename from `1.2.2` to the next normal semantic version without a
  suffix and update release documentation/tests that own that identity.
- [ ] Run rename's entire test suite.
- [ ] Run the stress corpus and large-tree performance check.
- [ ] Run workspace boundary checks for `.git`, `.worktrees`, and `.stfolder`.
