# Rule-First Media Search Planning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce canonical plans without AI when strict multi-source evidence uniquely identifies ordinary media, and call two-stage AI only for unresolved or complex cases.

**Architecture:** Add a no-key Douban evidence adapter and a focused deterministic planning module. The planner performs a rule-derived first pass, returns a canonical draft when the strict gate passes, and otherwise sends the evidence and reason codes through the existing two-stage AI path.

**Tech Stack:** Python 3.12, `asyncio`, `requests`, `PyYAML`, pytest/unittest, Telepiplex Plugin SDK `media_metadata v1`.

## Global Constraints

- Work only in `/Users/young/Documents/telepiplex/.worktrees/media-search` on `feature/media-search`.
- Do not modify Core APIs or sibling Feature worktrees.
- High-confidence ordinary media must not call AI.
- Ambiguous, conflicting, single-source, relation-bearing, OVA, Special, or unverified series-scope cases must use AI and fail closed if AI cannot complete.
- Both paths output `media_metadata v1`, require confirmation, and avoid Prowlarr before confirmation.
- Wikipedia and Douban require no Key; TVDB and AI default to `enable: true` but report missing credentials accurately.
- Prowlarr uses English or original titles only.
- Test with `/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3` and `PYTHONPATH=src:/Users/young/Documents/telepiplex/.worktrees/telepiplex-core/sdk/src`.
- Keep commits local; do not merge or push.

---

## File Map

- Create `src/telepiplex_media_search/adapters/douban.py` for no-key lookup and normalized provider results.
- Create `src/telepiplex_media_search/deterministic.py` for rule queries, the strict gate, reason codes, and canonical drafts.
- Modify `adapters/wikipedia.py`, `planner.py`, `ai.py`, and `service.py` for bilingual evidence and rule-first orchestration.
- Modify `config.default.yaml` and `README.md` for the approved defaults and operator contract.
- Add adapter/gate tests and update planner/service/AI regression tests.

---

### Task 1: Add no-key evidence collection

**Files:**
- Create: `src/telepiplex_media_search/adapters/douban.py`
- Modify: `src/telepiplex_media_search/adapters/wikipedia.py`
- Modify: `src/telepiplex_media_search/service.py`
- Create: `tests/test_douban_adapter.py`
- Modify: `tests/test_wikipedia_adapter.py`

**Interfaces:**
- Produces `lookup_douban_evidence(queries: list[str], timeout: float = 10) -> dict`.
- Provider results contain `source`, `status`, `facts`, `source_urls`, and `error`.
- Facts expose stable ID, URL, Chinese/English titles, year, media type, aliases, genres, and cover where available.

- [ ] **Step 1: Write failing tests**

Test a Douban search response containing a real subject URL followed by subject JSON, duplicate IDs, `not_found`, and total network failure. Assert normalized `subject_id`, bilingual titles, year, `movie|series`, genres, and source URL. Extend Wikipedia tests to require both `zh` and `en` facts and normalized year/type signals.

```python
result = lookup_douban_evidence(["黑暗荣耀 2022"])
self.assertEqual(result["status"], "ok")
self.assertEqual(result["facts"][0]["subject_id"], "35314632")
self.assertEqual(result["facts"][0]["media_type"], "series")
```

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/.worktrees/telepiplex-core/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/test_douban_adapter.py tests/test_wikipedia_adapter.py -q
```

Expected: collection fails because the Douban module is absent.

- [ ] **Step 3: Implement minimal provider code**

Discover subjects through `https://www.douban.com/search?cat=1002&q=...`; extract only returned `movie.douban.com/subject/<id>` URLs. Resolve each ID through `j/subject_abstract`, then the mobile rexxar endpoint. Never accept an ID invented outside actual responses. Deduplicate by ID and map partial/total failures to `not_found`/`server_down`.

```python
def lookup_douban_evidence(queries, timeout=10):
    urls, request_succeeded, errors = _search_subject_urls(queries, timeout)
    facts = [fact for url in urls if (fact := _fetch_subject(url, timeout))]
    if facts:
        return _result("ok", facts, [fact["url"] for fact in facts])
    return _result("not_found" if request_succeeded else "server_down", error="; ".join(errors))
```

Make Wikipedia continue through both languages instead of stopping after the first language with facts. Wire `MediaSearchFeature._douban_provider()` in place of the disabled lambda.

- [ ] **Step 4: Verify GREEN and commit**

Run the Step 2 command, then stage the five Task 1 files and commit with `feat(media-search): add no-key evidence collection`.

---

### Task 2: Add the strict deterministic gate

**Files:**
- Create: `src/telepiplex_media_search/deterministic.py`
- Create: `tests/test_deterministic_planner.py`

**Interfaces:**
- Produces `build_rule_hypotheses(raw_query: str) -> dict`.
- Produces `evaluate_deterministic_plan(plan_id: str, raw_query: str, sources: list[dict]) -> DeterministicResult`.
- `DeterministicResult.plan` is a draft or `None`; `reason_codes` is stable; `decision` is JSON-safe evidence.

- [ ] **Step 1: Write failing gate tests**

Cover a Wikipedia+Douban unique movie, a TVDB+Douban series episode, same-title years, movie/series conflict, one-source evidence, missing bilingual title, nonexistent TVDB scope, relation/OVA/Special signals, and missing Latin query title.

```python
result = evaluate_deterministic_plan("plan-1", "盗梦空间 2010", sources)
self.assertEqual(result.plan["media_metadata"]["identity"]["english_title"], "Inception")
self.assertEqual(result.decision["mode"], "deterministic")
```

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/.worktrees/telepiplex-core/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/test_deterministic_planner.py -q
```

Expected: collection fails because `deterministic.py` is absent.

- [ ] **Step 3: Implement the result type, normalizer, gate, and draft builder**

```python
@dataclass(frozen=True)
class DeterministicResult:
    plan: dict | None
    reason_codes: tuple[str, ...]
    decision: dict
```

Group Wikipedia languages by Wikibase ID. Group cross-provider candidates only when normalized title sets intersect and non-empty years/types agree. Require two providers for movies; require unique TVDB series ID plus Wikipedia or Douban for series. Require TVDB proof for seasons/episodes. Require bilingual canonical identity and a Latin query title.

Choose animated categories only from explicit animation genre/signals; conflicting category signals fail. Otherwise choose `live_action_*` and record `default_live_action_without_animation_signal`.

Movies use standalone placement and empty `items`. Series use standalone placement and enumerate requested aired TVDB episodes as locked `main_episode` items. Record `evidence.decision.scope` for later Prowlarr query construction.

- [ ] **Step 4: Verify GREEN and commit**

Run the deterministic tests plus `tests/test_search_plan.py`, then stage the Task 2 source and test files and commit with `feat(media-search): add strict deterministic planning`.

---

### Task 3: Make AI conditional on gate failure

**Files:**
- Modify: `src/telepiplex_media_search/planner.py`
- Modify: `src/telepiplex_media_search/ai.py`
- Modify: `tests/test_search_planner_service.py`
- Modify: `tests/test_search_ai_pipeline.py`

**Interfaces:**
- Preserve `build_confirmable_search_plan(...) -> dict`.
- AI stage one accepts `raw_query`, `intent`, first-pass `sources`, and `gate_reason_codes`.

- [ ] **Step 1: Write failing orchestration tests**

Assert a strict-pass plan never calls either AI function. Assert a gate failure calls stage one with first-pass evidence, performs expanded evidence collection, and sends merged evidence to stage two. Keep TVDB Official Special anti-downgrade coverage. Assert AI failures use `ai_unavailable_after_gate_failure` or `ai_invalid_after_gate_failure`.

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/.worktrees/telepiplex-core/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/test_search_planner_service.py tests/test_search_ai_pipeline.py -q
```

Expected: current planner calls AI before providers.

- [ ] **Step 3: Refactor orchestration**

```python
rule_hypotheses = build_rule_hypotheses(raw_query)
first_sources = await collect_evidence(rule_hypotheses, providers)
deterministic = evaluate_deterministic_plan(plan_id, raw_query, first_sources)
if deterministic.plan is not None:
    return _finalize_plan(deterministic.plan, first_sources, allocator, occupied_loader)
ai_context = {
    "raw_query": raw_query,
    "intent": rule_hypotheses["intent"],
    "sources": first_sources,
    "gate_reason_codes": list(deterministic.reason_codes),
}
```

Stage one expands hypotheses/queries; merge the second provider pass without duplicate facts, URLs, or stable IDs; stage two builds the AI draft. Inject provider support, failed-gate evidence, and AI statuses before existing Official Special validation. Update prompts to say AI receives unresolved cases, while retaining unrestricted identity/relation analysis and the stage-one Prowlarr prohibition.

- [ ] **Step 4: Verify GREEN and commit**

Run the Step 2 command, stage the four Task 3 files, and commit with `feat(media-search): use AI only for unresolved plans`.

---

### Task 4: Align UX, query scope, configuration, and docs

**Files:**
- Modify: `src/telepiplex_media_search/service.py`
- Modify: `config.default.yaml`
- Modify: `tests/test_feature_service.py`
- Modify: `tests/test_media_search_utils.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing tests**

Assert blocked searches return a specific safe Chinese reason and store no plan. Assert release search is untouched before confirm. Assert whole-series/season/episode queries are `English Title 2022`, `English Title S02`, and `English Title S02E05`. Parse YAML and assert Wikipedia, TVDB, and AI defaults are true.

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/.worktrees/telepiplex-core/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/test_feature_service.py tests/test_media_search_utils.py -q
```

- [ ] **Step 3: Implement reason mapping, scope-aware query, and defaults**

Map planning codes to user-safe Chinese messages. Read `evidence.decision.scope` before falling back to placement/items. Change only `metadata.tvdb.enable` from false to true. Document always-on no-key Douban and credential-backed enabled TVDB/AI.

```python
if scope == "whole_series":
    return " ".join(item for item in (english, year) if item)
if scope == "season":
    return f"{english} S{int(season):02d}"
```

- [ ] **Step 4: Verify GREEN and commit**

Run the Step 2 command, stage the five Task 4 files, and commit with `fix(media-search): explain fallback decisions`.

---

### Task 5: Full verification and package validation

**Files:**
- Modify only previously listed files if verification exposes a defect.

- [ ] **Step 1: Run the full suite and compile check**

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/.worktrees/telepiplex-core/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest -q
PYTHONPATH=src:/Users/young/Documents/telepiplex/.worktrees/telepiplex-core/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m compileall -q src tests
```

Expected: zero failures and exit code 0.

- [ ] **Step 2: Build and inspect `.tpx`**

```bash
rm -rf dist
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 /Users/young/Documents/telepiplex/.worktrees/telepiplex-core/tools/build_feature.py . dist/media-search-1.0.0.tpx
unzip -t dist/media-search-1.0.0.tpx
```

Expected: builder succeeds and archive test reports no errors. If the builder is Linux-only, report the exact environmental limitation and verify the supported wheel/config/manifest output without claiming a `.tpx` success.

- [ ] **Step 3: Verify scope**

```bash
git diff --check origin/feature/media-search..HEAD
git status --short
git diff --name-only origin/feature/media-search..HEAD
```

Expected: no whitespace errors, only planned media-search files, and a clean worktree.

- [ ] **Step 4: Commit verification corrections only if necessary**

Rerun the failed command after any correction and commit exact corrected files with `test(media-search): close rule-first regressions`. Do not create an empty commit.
