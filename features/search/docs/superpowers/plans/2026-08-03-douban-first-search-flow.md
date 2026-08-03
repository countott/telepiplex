# Douban-First Search Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ordinary-text multi-source discovery with Douban-only discovery, a context-preserving AI decision stage, direct shared-link intake, deterministic post-confirmation Wikipedia/TVDB enrichment, non-blocking enhancement failure, and complete structured logs.

**Architecture:** Host routing declares which Feature owns unsolicited supported-link messages. The search Feature classifies those messages, resolves one stable link entity or downgrades one failed link to its share title, while `/s` remains text-only. Ordinary text enters a new staged planner that queries only Douban, performs deterministic hard matching, invokes one logical AI decision per Douban attempt, materializes only real Douban facts, and enriches the selected identity sequentially through Wikipedia and TVDB.

**Tech Stack:** Python 3.12, asyncio, dataclasses, requests, Telegram Host Feature RPC, unittest/pytest, telepiplex Plugin SDK.

## Global Constraints

- Product-facing text uses lowercase `telepiplex`.
- Mac workspace `/Users/young/Documents/telepiplex` must not run Git or create Git/worktree metadata.
- Ordinary text discovery uses Douban only; Wikipedia and TVDB never produce first-round text candidates.
- AI may only reference real Douban subject IDs and may not generate source facts.
- One business query rewrite is allowed; one identical technical retry is allowed for each failed AI decision.
- `/s <URL>` is rejected; direct supported-link messages are the only link command surface.
- Wikipedia and TVDB enhancement failure is non-blocking.
- TVDB failure degrades a series to `whole_series` without season/episode selection.
- Every business transition and terminal state is logged with a stable `search_session_id`.
- No credentials, headers, tokens, cookies, or API keys may appear in logs.
- Existing Prowlarr release search, release gating, download handoff, `/m`, and recorded P1 work remain out of scope.

---

### Task 1: Declarative Direct-Message Routing

**Files:**
- Modify: `app/runtime/plugin_manifest.py`
- Modify: `app/runtime/capability_router.py`
- Modify: `app/handlers/plugin_handler.py`
- Modify: `features/search/manifest.yaml`
- Test: `tests/test_plugin_manifest.py`
- Test: `tests/test_capability_router.py`
- Test: `tests/test_plugin_handler.py`

**Interfaces:**
- Consumes: Feature manifest mappings and the existing `message.dispatch` RPC.
- Produces: `PluginManifest.direct_message_hosts: tuple[str, ...]` and `CapabilityRouter.direct_message_route(text: str) -> PluginRoute | None`.

- [x] **Step 1: Write failing manifest and router tests**

```python
def test_direct_message_hosts_are_normalized_and_immutable():
    value = self._value()
    value["direct_message_hosts"] = ["douban.com", "wikipedia.org"]
    manifest = PluginManifest.from_mapping(value)
    self.assertEqual(manifest.direct_message_hosts, ("douban.com", "wikipedia.org"))

def test_direct_message_route_matches_supported_subdomain_only():
    route = router.direct_message_route(
        "分享 https://m.douban.com/movie/subject/35314632/"
    )
    self.assertEqual(route.plugin_id, "search")
    self.assertIsNone(router.direct_message_route("https://douban.com.evil.test/x"))
```

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_plugin_manifest.py tests/test_capability_router.py tests/test_plugin_handler.py
```

Expected: failures because `direct_message_hosts` and `direct_message_route` do not exist and no-session text is discarded.

- [x] **Step 3: Add the optional manifest field and router lookup**

Add `direct_message_hosts: tuple[str, ...]` to the existing immutable
`PluginManifest` dataclass and implement:

```python
def direct_message_route(self, text: str) -> PluginRoute | None:
    """Return the sole active Feature declaring a host found in HTTP(S) URLs."""
```

Host matching must use `urlsplit(...).hostname`, accept declared subdomains, reject lookalike suffixes, and return `None` when no declaration matches.

- [x] **Step 4: Route no-session supported links**

Update `dynamic_message_gateway` so an existing Feature session still has priority. Without a session, call `router.direct_message_route(text)` and dispatch only when a route exists.

Declare these search hosts:

```yaml
direct_message_hosts:
  - douban.com
  - wikipedia.org
  - w.wiki
  - thetvdb.com
  - tvdb.com
```

- [x] **Step 5: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

### Task 2: Shared-Link Input Contract

**Files:**
- Modify: `features/search/src/telepiplex_search/input_contract.py`
- Modify: `features/search/src/telepiplex_search/direct_link.py`
- Test: `features/search/tests/test_input_contract.py`
- Test: `features/search/tests/test_direct_link.py`

**Interfaces:**
- Produces: `extract_message_urls(text: str) -> tuple[str, ...]`, `contains_url(text: str) -> bool`, `classify_search_input(text: str) -> ParsedInput`, and `resolve_shared_metadata_link(parsed: ParsedInput) -> tuple[MetadataLink | None, str]`.
- `ParsedInput` additionally carries `urls`, `fallback_title`, and `reason`.

- [x] **Step 1: Write failing share-message tests**

Cover:

```python
classify_search_input("分享《繁花》 https://m.douban.com/movie/subject/123/")
classify_search_input("https://zh.m.wikipedia.org/wiki/%E7%B9%81%E8%8A%B1")
classify_search_input("https://thetvdb.com/zh-CN/series/411469")
classify_search_input("a https://movie.douban.com/subject/1/ b https://movie.douban.com/subject/2/")
```

Assert the first three become one link entity and the fourth returns `kind == "invalid_link"` with `reason == "multiple_metadata_entities"`.

- [x] **Step 2: Run focused tests and verify RED**

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
cd /Users/young/Documents/telepiplex/features/search
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_input_contract.py tests/test_direct_link.py
```

- [x] **Step 3: Implement extraction and stable patterns**

The parser must:

- extract URLs from the entire message rather than treating the whole message as a URL;
- remove trailing Chinese/English punctuation and decode HTML entities;
- support Wikipedia mobile hosts and TVDB localized prefixes;
- treat duplicate URLs resolving to the same provider/entity ID as one;
- reject multiple distinct stable entities;
- preserve non-URL share text as `fallback_title`.

- [x] **Step 4: Implement bounded redirect/canonical resolution**

Add a resolver that:

- accepts only declared platform and short-link hosts;
- follows at most three redirects;
- rejects any redirect hop to a private/local address or non-HTTP(S) scheme;
- reads final URL, `<link rel="canonical">`, and `og:url`;
- returns a stable `MetadataLink` when possible;
- returns the cleaned share/page title when stable identity cannot be extracted.

- [x] **Step 5: Verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

### Task 3: Unified AI Search Decision Contract

**Files:**
- Create: `features/search/src/telepiplex_search/discovery_flow.py`
- Modify: `features/search/src/telepiplex_search/ai.py`
- Test: `features/search/tests/test_discovery_flow.py`
- Test: `features/search/tests/test_search_ai_pipeline.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class SearchContext:
    search_session_id: str
    original_input: str
    title: str
    year: str
    media_type: str
    scope: str
    season_number: int | None
    episode_number: int | None
    attempt: int
    query: str
    candidates: tuple[dict, ...]
    history: tuple[dict, ...]
    retry_available: bool

@dataclass(frozen=True)
class SearchDecision:
    action: str
    candidate_ids: tuple[str, ...] = ()
    rewrite_query: str = ""

def infer_douban_search_decision_with_ai(context: dict) -> dict | None
def validate_search_decision(payload: object, context: SearchContext) -> SearchDecision
```

- [x] **Step 1: Write failing validation tests**

Tests must reject unknown subject IDs, duplicate IDs, more than five IDs, one ID selected from a multi-result pool, rewrite on attempt two, unchanged rewrite queries, and extra JSON fields.

- [x] **Step 2: Verify RED**

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
cd /Users/young/Documents/telepiplex/features/search
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_discovery_flow.py tests/test_search_ai_pipeline.py
```

- [x] **Step 3: Implement the fixed AI prompt and parser**

The AI payload is exactly:

```json
{
  "action": "show_candidates",
  "candidate_ids": ["35314632"],
  "rewrite_query": ""
}
```

The prompt states that all IDs must come from `SearchContext.candidates`, facts cannot be edited, and first-attempt semantic failure must use `retry` when `retry_available` is true.

- [x] **Step 4: Implement identical technical retry**

Expose:

```python
def decide_with_technical_retry(
    context: SearchContext,
    decide: Callable[[dict], object],
    *,
    logger,
) -> SearchDecision | None:
```

Call `decide(context.to_dict())` once, retry once with byte-for-byte equivalent context after exceptions or invalid output, then return `None`. Do not query Douban inside this function.

- [x] **Step 5: Verify GREEN**

Run the command from Step 2.

### Task 4: Douban-Only Discovery Planner

**Files:**
- Modify: `features/search/src/telepiplex_search/discovery_flow.py`
- Modify: `features/search/src/telepiplex_search/planner.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Test: `features/search/tests/test_discovery_flow.py`
- Test: `features/search/tests/test_search_planner_service.py`
- Test: `features/search/tests/test_feature_service.py`

**Interfaces:**
- Produces:

```python
async def build_douban_first_search_plan(
    raw_query: str,
    plan_id: str,
    douban_provider: Callable[[dict], dict],
    *,
    ai_decider: Callable[[dict], object],
) -> dict:
```

The returned plan preserves the existing candidate dictionary contract consumed by `SearchFeature`.

- [x] **Step 1: Write failing discovery behavior tests**

Cover:

- only `douban_provider` is called for ordinary text;
- exact title/year/type unique hard match sets `auto_confirm=True` and makes zero AI calls;
- a single non-hard candidate sets `auto_confirm=False`;
- multi-result AI selection returns 2–5 candidates in AI order;
- zero facts trigger one rewritten Douban query;
- second no-match raises `SearchPlanningError("no_match")`;
- Douban technical status raises `source_failure` without AI;
- AI double failure with candidates returns the first five normalized Douban results;
- AI double failure with zero facts raises `ai_candidate_failure`.

- [x] **Step 2: Verify RED**

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
cd /Users/young/Documents/telepiplex/features/search
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_discovery_flow.py tests/test_search_planner_service.py tests/test_feature_service.py
```

- [x] **Step 3: Materialize one Douban fact per candidate**

Convert selected real Douban facts to existing anchored candidate payloads. Use:

- `candidate_id = "douban:<subject_id>"`;
- the Douban fact as anchor;
- `movie` or `series_root` role based on source media type;
- requested scope from deterministic input parsing;
- no Wikipedia/TVDB bindings before user confirmation.

- [x] **Step 4: Switch ordinary text service planning**

`SearchFeature._build_plan` must:

- use `build_douban_first_search_plan` for text;
- keep direct stable links as zero-AI confirmed anchors;
- never pass all three providers into ordinary-text discovery;
- store `auto_confirm` in the plan.

`SearchFeature._prepare_plan` must auto-select only when `auto_confirm is True`; one non-hard candidate must remain interactive.

- [x] **Step 5: Verify GREEN**

Run the command from Step 2.

### Task 5: Post-Confirmation Wikipedia and TVDB Enrichment

**Files:**
- Create: `features/search/src/telepiplex_search/confirmed_enrichment.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/media_metadata_v1.py`
- Modify: `sdk/src/telepiplex_plugin_sdk/media_metadata.py`
- Test: `features/search/tests/test_confirmed_enrichment.py`
- Test: `features/search/tests/test_media_metadata_v1.py`
- Test: `features/search/tests/test_feature_service.py`
- Test: `tests/test_host_media_metadata.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class ConfirmedIdentity:
    provider: str
    stable_id: str
    chinese_title: str
    english_title: str
    original_title: str
    year: str
    media_type: str
    requested_scope: str

def build_wikipedia_queries(identity: ConfirmedIdentity) -> dict[str, list[str]]
def select_unique_wikipedia_fact(result: dict, identity: ConfirmedIdentity) -> dict | None
def build_tvdb_query(identity: ConfirmedIdentity, wikipedia_fact: dict | None) -> dict | None
def select_unique_tvdb_series(result: dict, identity: ConfirmedIdentity) -> dict | None
```

- [x] **Step 1: Write failing query and unique-match tests**

Assert Wikipedia queries use only confirmed title/year/type and TVDB prefers verified Wikipedia English title. Assert ambiguous Wikipedia or TVDB results return `None`.

- [x] **Step 2: Verify RED**

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
cd /Users/young/Documents/telepiplex
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=features/search/src:sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  features/search/tests/test_confirmed_enrichment.py \
  features/search/tests/test_media_metadata_v1.py \
  tests/test_host_media_metadata.py
```

- [x] **Step 3: Implement deterministic sequential enrichment**

Replace AI-driven selected-source supplementation with:

```text
selected candidate
→ Wikipedia query and unique same-work match
→ merge Wikipedia fact if unique
→ TVDB series query using verified English identity
→ fetch episode inventory only when one TVDB series matches
```

No AI call is allowed in this stage. Preserve selected Douban anchor and never reorder candidates.

- [x] **Step 4: Support TVDB-unavailable whole-series metadata**

Allow a confirmed standalone series contract with empty `items` only when:

- retrieval scope is `whole_series`;
- warning includes `warning:tvdb_inventory_unavailable`;
- placement has no season or episode number;
- no TVDB ID is asserted.

When TVDB enhancement fails, rewrite the selected series to this degraded whole-series contract and skip scope selection.

- [x] **Step 5: Verify GREEN**

Run the command from Step 2.

### Task 6: Candidate UX and Rejection Semantics

**Files:**
- Modify: `features/search/src/telepiplex_search/service.py`
- Test: `features/search/tests/test_feature_service.py`

**Interfaces:**
- Consumes: candidate dictionaries from Task 4.
- Produces: simplified Chinese-first candidate messages and `search:reject:<plan_id>` callback behavior.

- [x] **Step 1: Write failing UX tests**

Assert candidate messages contain only:

```text
简中标题（年份）
English title
类型
来源：豆瓣
```

Assert they do not contain score, confidence, AI reason, candidate version, unresolved provider labels, or relation labels.

- [x] **Step 2: Verify RED**

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
cd /Users/young/Documents/telepiplex/features/search
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_feature_service.py
```

- [x] **Step 3: Implement single and multi candidate actions**

- One non-hard candidate: `就是它` and `都不是`.
- Two to five candidates: one button per candidate and one `都不是`.
- `都不是` releases the plan, records `user_rejected`, and never calls the planner again.
- A hard auto-confirmed item must include an `已识别为：繁花（2023）`-style status before release searching.

- [x] **Step 4: Verify GREEN**

Run the command from Step 2.

### Task 7: Structured Search Logging

**Files:**
- Create: `features/search/src/telepiplex_search/search_logging.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/discovery_flow.py`
- Modify: `features/search/src/telepiplex_search/confirmed_enrichment.py`
- Test: `features/search/tests/test_search_logging.py`
- Test: `features/search/tests/test_feature_service.py`

**Interfaces:**
- Produces:

```python
def log_search_event(
    logger,
    event: str,
    *,
    search_session_id: str,
    level: str = "info",
    **fields,
) -> None:
```

- [x] **Step 1: Write failing logging tests**

Capture logger calls and assert event name, session ID, attempt, query/result counts, AI action, enhancement status, terminal status, and sanitization.

- [x] **Step 2: Verify RED**

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
cd /Users/young/Documents/telepiplex/features/search
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_search_logging.py tests/test_feature_service.py
```

- [x] **Step 3: Emit all design-contract events**

Every terminal path must call `search.completed` exactly once. Candidate arrays log subject ID/title/year/type summaries; TVDB inventory logs counts, not every episode.

- [x] **Step 4: Verify GREEN**

Run the command from Step 2.

### Task 8: Documentation, Version, and Full Verification

**Files:**
- Modify: `features/search/README.md`
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/src/telepiplex_search.egg-info/PKG-INFO`
- Modify: `tests/test_technical_identity_migration.py`
- Modify: version assertions in `features/search/tests/test_feature_service.py`
- Inspect: `app/config.yaml.example`
- Inspect: `config/config.yaml.example`

**Interfaces:**
- Produces: search Feature version `1.3.0` and user-facing documentation matching the implemented flow.

- [x] **Step 1: Update README and version contract**

Describe text-only `/s`, direct shared links, Douban-only discovery, unified AI decision, one business retry, post-confirmation Wikipedia/TVDB enrichment, degraded whole-series behavior, and structured logs.

No config-template edit is required unless implementation introduces a new configurable key.

- [x] **Step 2: Run focused search tests**

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
cd /Users/young/Documents/telepiplex/features/search
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests
```

- [x] **Step 3: Run Host and SDK regression tests**

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
cd /Users/young/Documents/telepiplex
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider tests
```

- [x] **Step 4: Run all Feature suites**

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
cd /Users/young/Documents/telepiplex
for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider tests
  )
done
```

- [x] **Step 5: Compile and verify workspace boundaries**

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
cd /Users/young/Documents/telepiplex
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m compileall -q \
  app sdk/src features/search/src
test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

Expected: all commands exit 0; `.git` and `.worktrees` remain absent; `.stfolder` remains present.

## Completion Record

- search: `417 passed, 2 skipped, 105 subtests passed`
- Host/SDK: `374 passed, 1 skipped, 176 subtests passed`
- download: `60 passed, 25 subtests passed`
- rename: `81 passed, 2 subtests passed`
- sync: `136 passed, 64 subtests passed`
- caption: `1 passed`
- `compileall` and all three workspace-boundary checks exited `0`
