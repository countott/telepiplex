# Search Full-Series Query and Clarification Fix Implementation Plan

> **Execution:** Run inline with `superpowers:test-driven-development` and
> verify with `superpowers:verification-before-completion`. Mac-local Git
> operations are prohibited by `AGENTS.md`.

**Goal:** Preserve the verified series choice, expose explicit 2-channel audio,
use the configured TV category, and improve one-season whole-series recall with
three bounded parallel query variants.

**Architecture:** Tighten source ambiguity option construction around verified
cross-type title families, normalize only explicit audio/channel evidence, map
the Search media type to Prowlarr's category namespace, and fan confirmed
whole-series searches across a bounded `(indexer, query)` task matrix before
the existing deduplication, release gate, and ranking stages.

**Tech Stack:** Python 3.12, asyncio, unittest, pytest, telepiplex Feature
operation API.

## Global Constraints

- Do not run Git or create Git metadata in the Mac workspace.
- Do not weaken title, year, media type, TVDB, scope, or release gates.
- Do not use a bare-title fallback for a verified one-season whole-series
  search.
- Do not infer `2.0` from AAC without an explicit `2CH` or `2.0` token.
- Preserve incremental result selection and cancellation.

---

### Task 1: Keep both verified clarification types

**Files:**
- Modify: `features/search/tests/test_ranked_planner.py`
- Modify: `features/search/src/telepiplex_search/planner.py`

- [ ] Add a failing regression with six prefixed movie candidates, one
  unrelated prefixed series, and the verified 2019 series.
- [ ] Filter clarification candidates to the verified movie/series title
  family.
- [ ] Deduplicate options and reserve list capacity for both media types.
- [ ] Run the focused planner tests.

### Task 2: Normalize explicit 2CH and series category mapping

**Files:**
- Modify: `features/search/tests/test_release_report.py`
- Modify: `features/search/src/telepiplex_search/release_report.py`
- Modify: `features/search/tests/test_prowlarr_adapter.py`
- Modify: `features/search/src/telepiplex_search/adapters/prowlarr.py`

- [ ] Add failing `AAC.2CH` and custom `categories.tv` regressions.
- [ ] Normalize explicit `2CH` to `2.0`.
- [ ] Map `series` to `categories.tv` with safe defaults.
- [ ] Run the focused report and adapter tests.

### Task 3: Fan out one-season whole-series queries

**Files:**
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `features/search/src/telepiplex_search/service.py`

- [ ] Add failing query-generation tests for `S01`, `Season 01`, and
  `Complete`, with no bare title.
- [ ] Add a failing search-matrix regression covering merge/deduplication and
  partial versus complete variant failure.
- [ ] Generate query lists from the confirmed contract.
- [ ] Search every indexer/query pair under a bounded semaphore.
- [ ] Count completion and downtime per indexer, not per task.
- [ ] Apply the same multi-query partial-failure rule to aggregate fallback.
- [ ] Add per-variant and merged gate logging.
- [ ] Run focused service tests.

### Task 4: Release identity, documentation, and full verification

**Files:**
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/src/telepiplex_search.egg-info/PKG-INFO`
- Modify: `features/search/README.md`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `tests/test_technical_identity_migration.py`

- [ ] Bump the Search Feature from `1.0.6` to `1.0.7`.
- [ ] Run all Search tests.
- [ ] Run root tests and every Feature test suite.
- [ ] Build and inspect `/tmp/search-1.0.7.tpx`.
- [ ] Confirm `.git` and `.worktrees` are absent and `.stfolder` exists.
- [ ] Hand off through Syncthing without publishing.
