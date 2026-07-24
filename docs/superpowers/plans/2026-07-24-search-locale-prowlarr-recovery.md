# Search locale, Prowlarr error, and typo recovery implementation plan

> **For agentic workers:** Execute inline with test-driven development. Mac local
> Git operations are prohibited by `AGENTS.md`.

**Goal:** Implement the approved Search title, artwork, Prowlarr, interaction,
timeout, typo-recovery, and `1.0.2` release contracts.

**Architecture:** Preserve locale provenance in evidence facts, make title and
poster selection explicit, preserve structured adapter errors through the
service boundary, and add one bounded AI recovery pass after qualification
failure. Existing source verification and release gates remain authoritative.

**Tech Stack:** Python 3.12, asyncio, requests, unittest, pytest, YAML, TOML.

## Global constraints

- Do not convert Taiwanese translations into mainland-Chinese titles.
- Do not let AI invent media facts; AI hypotheses must be re-queried.
- Do not expose API keys or sensitive request headers in logs.
- Remove stale candidate controls as soon as Prowlarr starts and keep terminal
  failures control-free; retaining the candidate photo is allowed.
- Use a 200-second Prowlarr default.
- Do not execute Git on the Mac workspace.

### Task 1: Canonical title and source-language poster

**Files:**
- Modify: `features/search/src/telepiplex_search/entity_graph.py`
- Modify: `features/search/src/telepiplex_search/title_policy.py`
- Modify: `features/search/src/telepiplex_search/planner.py`
- Modify: `features/search/src/telepiplex_search/adapters/tvdb.py`
- Test: `features/search/tests/test_title_policy.py`
- Test: `features/search/tests/test_entity_graph.py`
- Test: `features/search/tests/test_tvdb_adapter.py`

- [ ] Add failing tests proving a verified user title beats Wikipedia, Taiwanese
  Wikipedia text is not converted or selected as a mainland fallback, and
  original-language or language-neutral posters beat mismatched posters.
- [ ] Run the focused tests and confirm assertion failures.
- [ ] Add explicit Chinese-title and poster-language evidence fields, pass the
  preferred request title into title resolution, and select TVDB artwork by
  original language.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Structured Prowlarr failures and terminal interaction cleanup

**Files:**
- Modify: `features/search/src/telepiplex_search/adapters/prowlarr.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/config.default.yaml`
- Test: `features/search/tests/test_feature_service.py`
- Test: `features/search/tests/test_config_schema_contract.py`

- [ ] Add failing tests for structured timeout/HTTP errors, safe error text,
  terminal operation details without keyboards, and the 200-second default.
- [ ] Run the focused tests and confirm assertion failures.
- [ ] Implement structured `ProwlarrRequestError`, safe logging and status
  propagation, photo-only terminal details, and the 200-second default.
- [ ] Run the focused tests and confirm they pass.

### Task 3: Qualification-failure AI typo recovery

**Files:**
- Modify: `features/search/src/telepiplex_search/planner.py`
- Test: `features/search/tests/test_ranked_planner.py`

- [ ] Add a failing test where a lexical wrong-title candidate exists but fails
  TVDB qualification, while AI returns the corrected title and the second
  source pass forms a valid candidate.
- [ ] Run the focused test and confirm it fails before invoking AI recovery.
- [ ] Add one bounded recovery pass after qualification returns no ranked
  candidates; merge and verify the returned source evidence normally.
- [ ] Run the focused test and related planner tests.

### Task 4: Release identity and complete verification

**Files:**
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/README.md`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `tests/test_technical_identity_migration.py`

- [ ] Update the five release-contract files from `1.0.1` to `1.0.2`.
- [ ] Run Search unittest and pytest suites.
- [ ] Run the root technical-identity test, dependency check, package build, and
  workspace marker checks.
- [ ] Report exact files and results, then wait for Syncthing before any Unraid
  publication.
