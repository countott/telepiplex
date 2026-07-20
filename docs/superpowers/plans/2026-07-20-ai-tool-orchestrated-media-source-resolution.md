# AI Tool-Orchestrated Media Source Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ordinary text searches use an AI-governed, bounded tool loop over Wikipedia, Douban, and TVDB before deterministic candidate verification, while preserving direct-link identity locks, user confirmation, program-generated Prowlarr queries, and the existing renaming handoff contract.

**Architecture:** Add an OpenAI-compatible tool-call client, a source gateway that owns credentials and source resilience, and a request-scoped orchestrator that enforces one fixed first round plus at most two targeted rounds. The planner consumes only normalized facts and verifier-approved semantic edges; direct links and AI/tooling failures retain deterministic paths. The existing confirmed `media_metadata v1` and `naming_metadata` payload remain the only search-to-download-to-renaming boundary.

**Tech Stack:** Python 3.12, asyncio, requests, unittest/pytest, PyYAML, JSON Schema, Telepiplex Plugin SDK 1.1.

## Global Constraints

- Ordinary text uses AI intent and `search_media_sources` before deterministic candidate ranking when tool orchestration is enabled and supported.
- The first round always attempts Wikipedia zh/en, Douban, and TVDB in parallel.
- AI may request at most two targeted rounds, at most three tool calls per round, and at most three targets per call.
- AI never receives API keys, PINs, bearer tokens, cookies, arbitrary request headers, base URLs, or raw page bodies.
- Direct Douban and TVDB links bypass AI identity selection and retain their stable-ID lock.
- Ordinary text candidates require two independent sources; series candidates require a TVDB Series ID.
- AI may connect existing fact IDs but may not create or modify facts, IDs, titles, years, types, `media_metadata`, or Prowlarr queries.
- One to seven qualified candidates go to user confirmation; AI never selects one automatically.
- Tool/source failure and `not_found` remain distinct.
- Confirmed `media_metadata v1` and `naming_metadata` continue unchanged into `download.provider`; release-gate grammar is out of scope.
- Use `/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3` with `PYTHONPATH=src:/Users/young/Documents/telepiplex/.worktrees/telepiplex-core/sdk/src`.

---

### Task 1: Add the OpenAI-compatible tool-call transport

**Files:**
- Modify: `src/telepiplex_media_search/ai.py`
- Test: `tests/test_search_ai_pipeline.py`

**Interfaces:**
- Produces: `chat_completion_messages(messages, *, tools=None, tool_choice=None, max_tokens=4096) -> dict | None`.
- Produces: `extract_ai_message(result) -> dict | None`.
- Preserves: existing `chat_completion(prompt, max_tokens)` and JSON-only inference helpers.

- [ ] **Step 1: Write failing transport tests**

```python
@patch("telepiplex_media_search.ai.requests.post")
def test_tool_transport_sends_system_messages_tools_and_choice(self, post):
    post.return_value.status_code = 200
    post.return_value.json.return_value = {
        "choices": [{"message": {"role": "assistant", "tool_calls": []}}]
    }
    result = chat_completion_messages(
        [{"role": "system", "content": "system"}, {"role": "user", "content": "query"}],
        tools=[{"type": "function", "function": {"name": "search_media_sources"}}],
        tool_choice={"type": "function", "function": {"name": "search_media_sources"}},
    )
    payload = post.call_args.kwargs["json"]
    self.assertEqual(payload["messages"][0]["role"], "system")
    self.assertEqual(payload["tools"][0]["function"]["name"], "search_media_sources")
    self.assertEqual(payload["tool_choice"]["function"]["name"], "search_media_sources")
    self.assertNotIn("tools", result)
```

- [ ] **Step 2: Verify RED**

Run:

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/.worktrees/telepiplex-core/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_search_ai_pipeline -v
```

Expected: import failure for `chat_completion_messages`.

- [ ] **Step 3: Implement the shared request builder**

```python
def chat_completion_messages(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    tool_choice=None,
    max_tokens: int = 4096,
):
    payload = {
        "model": _ai_config()["model"],
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    return _post_chat_completion(payload)
```

Keep the Authorization header and endpoint construction server-side. Log only sanitized status/error summaries.

- [ ] **Step 4: Verify GREEN and regression coverage**

Run the focused AI tests, then the full 156-test baseline command.

- [ ] **Step 5: Commit and push checkpoint**

```bash
git add src/telepiplex_media_search/ai.py tests/test_search_ai_pipeline.py
git commit -m "feat(media-search): add AI tool-call transport"
git push origin feature/media-search
```

### Task 2: Implement the bounded Source Tool Gateway and source statuses

**Files:**
- Create: `src/telepiplex_media_search/source_tools.py`
- Modify: `src/telepiplex_media_search/adapters/douban.py`
- Modify: `src/telepiplex_media_search/adapters/tvdb.py`
- Modify: `src/telepiplex_media_search/service.py`
- Create: `tests/test_source_tools.py`
- Modify: `tests/test_douban_adapter.py`
- Modify: `tests/test_tvdb_adapter.py`

**Interfaces:**
- Produces: `FIRST_ROUND_TOOL`, `TARGETED_TOOLS`.
- Produces: `SourceToolGateway(providers, targeted_handlers, config, logger=None)`.
- Produces: `await gateway.search_media_sources(raw_query, arguments) -> list[dict]`.
- Produces: `await gateway.execute_targeted(name, arguments, known_facts) -> list[dict]`.
- Produces TVDB status distinction: `disabled`, `credential_missing`, `authentication_failed`, `server_down`.

- [ ] **Step 1: Write failing gateway tests**

Cover:

```python
async def test_first_round_always_calls_all_three_sources_in_parallel(): ...
async def test_gateway_overwrites_model_supplied_raw_query(): ...
async def test_gateway_rejects_url_header_key_token_and_fourth_query(): ...
async def test_targeted_round_rejects_unknown_fact_or_stable_id(): ...
async def test_tool_results_expose_only_whitelisted_fact_fields(): ...
```

Use events/barriers in providers to prove all three calls start before any completes.

- [ ] **Step 2: Write failing adapter status tests**

```python
def test_douban_429_returns_rate_limited_and_opens_circuit(): ...
def test_douban_403_returns_blocked(): ...
def test_douban_cache_avoids_duplicate_subject_request(): ...
def test_tvdb_disabled_differs_from_missing_credentials(): ...
def test_tvdb_401_is_authentication_failed(): ...
```

- [ ] **Step 3: Verify RED**

Run:

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/.worktrees/telepiplex-core/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_source_tools tests.test_douban_adapter tests.test_tvdb_adapter -v
```

- [ ] **Step 4: Implement schemas and hard argument validation**

The first tool accepts only:

```python
{
    "intent": {
        "title_hints": list[str],
        "media_type_hint": "movie|series|unknown",
        "year_hint": str,
        "scope": "work|whole_series|season|episode|unknown",
        "season_number": int | None,
        "episode_number": int | None,
    },
    "source_queries": {
        "wikipedia_zh": list[str],
        "wikipedia_en": list[str],
        "douban": list[str],
        "tvdb": list[str],
    },
}
```

Every query is one line, at most 160 Unicode characters, with at most three values per source. Reject recursive sensitive keys matching `url`, `header`, `authorization`, `api_key`, `token`, `cookie`, or `base_url`.

- [ ] **Step 5: Implement fixed first-round execution**

Aggregate Wikipedia zh/en as one independent source, but preserve per-language query summaries. Invoke all configured source slots even when one returns disabled or missing credentials.

- [ ] **Step 6: Implement Douban cache, concurrency guard, and circuit breaker**

Use process-local monotonic TTL caches keyed by normalized query/subject ID, a bounded semaphore from `metadata.douban.max_concurrency`, and a failure window using `circuit_breaker_failures` plus `circuit_breaker_seconds`. Map HTTP 403 to `blocked`, 429 to `rate_limited`, request timeout to `timeout`, successful empty search to `not_found`.

- [ ] **Step 7: Implement TVDB typed failures**

```python
class TvdbConfigError(Exception):
    def __init__(self, message: str, code: str): ...

class TvdbAuthenticationError(TvdbRequestError): ...
```

Map disabled, missing key, login/token 401/403, timeout, and other request errors separately in the service provider and targeted handlers. Never include the key or token in returned errors.

- [ ] **Step 8: Verify GREEN**

Run focused gateway/adapter tests and full suite.

- [ ] **Step 9: Commit and push checkpoint**

```bash
git add src/telepiplex_media_search/source_tools.py src/telepiplex_media_search/adapters/douban.py src/telepiplex_media_search/adapters/tvdb.py src/telepiplex_media_search/service.py tests/test_source_tools.py tests/test_douban_adapter.py tests/test_tvdb_adapter.py
git commit -m "feat(media-search): add bounded source tool gateway"
git push origin feature/media-search
```

### Task 3: Add the orchestration state machine and evidence verifier

**Files:**
- Create: `src/telepiplex_media_search/source_orchestrator.py`
- Create: `src/telepiplex_media_search/evidence_verifier.py`
- Modify: `src/telepiplex_media_search/entity_graph.py`
- Create: `tests/test_source_orchestrator.py`
- Create: `tests/test_evidence_verifier.py`
- Modify: `tests/test_entity_graph.py`

**Interfaces:**
- Produces: `await orchestrate_sources(raw_query, gateway, ai_call=chat_completion_messages, config=None) -> OrchestrationOutcome`.
- Produces: `validate_orchestrator_output(payload, graph) -> VerifiedAiDecision`.
- Produces: `merge_verified_equivalence_edges(graph, edges) -> SearchGraph`.
- `OrchestrationOutcome` includes `status`, `intent`, `sources`, `decision`, `rounds_used`, and `fallback_reason`.

- [ ] **Step 1: Write failing state-machine tests**

Cover:

```python
async def test_first_model_action_is_forced_to_search_media_sources(): ...
async def test_one_protocol_correction_then_fallback(): ...
async def test_ai_can_stop_after_first_round(): ...
async def test_ai_can_choose_two_targeted_rounds(): ...
async def test_third_targeted_round_is_rejected(): ...
async def test_more_than_three_calls_in_one_round_is_rejected(): ...
async def test_unsupported_tooling_returns_deterministic_fallback(): ...
```

- [ ] **Step 2: Write failing verifier tests**

Cover unknown fields/facts/candidates, invented stable IDs, year/type/stable-ID conflicts, incomplete candidate assessments, and valid cross-language same-entity edges.

- [ ] **Step 3: Verify RED**

Run new orchestrator, verifier, and entity graph tests.

- [ ] **Step 4: Implement the system prompt and tool loop**

The first request exposes only `search_media_sources` and sets an explicit function `tool_choice`. Later requests expose only the four targeted tools. After every tool round, append a normalized request graph containing program-assigned temporary `candidate_key` values. Stop after two targeted rounds or a valid final JSON response.

- [ ] **Step 5: Implement strict final JSON validation**

Accept exactly:

```python
{
    "status": "resolved|ambiguous|insufficient_evidence",
    "intent": {...},
    "equivalence_edges": [
        {
            "left_fact_id": str,
            "right_fact_id": str,
            "relation": "same_entity",
            "reason": str,
        }
    ],
    "candidate_assessments": [
        {
            "candidate_key": str,
            "supporting_fact_ids": list[str],
            "conflicting_fact_ids": list[str],
            "reason": str,
        }
    ],
    "recommended_next_action": "confirm|clarify|stop",
}
```

- [ ] **Step 6: Merge only verifier-approved edges**

Programmatically reject same-provider edges, unknown facts, incompatible years/types, or different values for the same stable-ID namespace. Rebuild final candidate keys deterministically after accepted merges.

- [ ] **Step 7: Verify GREEN**

Run focused tests and full suite.

- [ ] **Step 8: Commit and push checkpoint**

```bash
git add src/telepiplex_media_search/source_orchestrator.py src/telepiplex_media_search/evidence_verifier.py src/telepiplex_media_search/entity_graph.py tests/test_source_orchestrator.py tests/test_evidence_verifier.py tests/test_entity_graph.py
git commit -m "feat(media-search): orchestrate AI source queries"
git push origin feature/media-search
```

### Task 4: Route ordinary text through AI-first planning with deterministic fallback

**Files:**
- Modify: `src/telepiplex_media_search/planner.py`
- Modify: `src/telepiplex_media_search/service.py`
- Modify: `tests/test_ranked_planner.py`
- Modify: `tests/test_search_planner_service.py`
- Modify: `tests/test_feature_service.py`

**Interfaces:**
- `build_confirmable_search_plan(..., source_orchestrator=orchestrate_sources)` uses AI-first only for ordinary text.
- Direct links preserve `locked_identity` and the existing provider path.
- Planner candidates are built from orchestration facts plus approved semantic edges; legacy title-prefix filtering is not applied to successful AI orchestration.

- [ ] **Step 1: Write failing planner tests**

Cover:

```python
async def test_plain_text_uses_orchestrated_sources_before_rule_queries(): ...
async def test_ai_typo_correction_can_resolve_batman_begins(): ...
async def test_cross_language_edge_prevents_empty_candidate_loss(): ...
async def test_direct_link_never_calls_source_orchestrator(): ...
async def test_ai_unavailable_keeps_clear_deterministic_query_working(): ...
async def test_single_source_and_series_without_tvdb_remain_unqualified(): ...
```

- [ ] **Step 2: Verify RED**

Run ranked planner, planner service, and feature service tests.

- [ ] **Step 3: Implement AI-first branch**

Use orchestration intent fields for media type, year, and scope. Build the candidate graph from all successful source rounds, apply verifier-approved edges, qualify without raw normalized-title prefix filtering, and retain deterministic score/order plus the existing 1–7 display limit.

- [ ] **Step 4: Implement fallback behavior**

On `ai_unavailable`, `tooling_unsupported`, `tool_protocol_invalid`, or invalid final output, run the current rule hypotheses and deterministic provider chain. Do not report “作品不存在” when every source failed.

- [ ] **Step 5: Preserve final program ownership**

Keep `resolve_title_policy()`, `_candidate_contract()`, `build_prowlarr_query()`, `confirm_media_metadata()`, and release gating program-owned. Set evidence decision mode to `ai_tool_orchestrated` only when the new path supplied verified facts.

- [ ] **Step 6: Verify GREEN with log regression inputs**

Add deterministic mocked source fixtures for:

```text
蝙蝠侠：谍影之谜
蝙蝠侠：黑暗骑士
蝙蝠侠黑暗骑士
蜂蜜与四叶草
布达佩斯大饭店
```

Each must yield confirmation candidates or an explicit ambiguity error.

- [ ] **Step 7: Commit and push checkpoint**

```bash
git add src/telepiplex_media_search/planner.py src/telepiplex_media_search/service.py tests/test_ranked_planner.py tests/test_search_planner_service.py tests/test_feature_service.py
git commit -m "feat(media-search): make source orchestration AI first"
git push origin feature/media-search
```

### Task 5: Publish configuration, documentation, and version 1.5.0

**Files:**
- Modify: `config.default.yaml`
- Modify: `config.schema.json`
- Modify: `README.md`
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `tests/test_config_schema_contract.py`
- Modify: `tests/test_feature_service.py`

**Interfaces:**
- Adds `metadata.douban`.
- Adds `ai.source_orchestration`.
- Produces Feature version `1.5.0`.

- [ ] **Step 1: Write failing config/version tests**

Assert:

```python
default["metadata"]["douban"] == {
    "enable": True,
    "timeout": 10,
    "cache_ttl": 900,
    "max_concurrency": 2,
    "circuit_breaker_failures": 3,
    "circuit_breaker_seconds": 300,
}
default["ai"]["source_orchestration"] == {
    "enable": True,
    "max_targeted_rounds": 2,
    "max_tools_per_round": 3,
    "protocol": "openai_tools_v1",
}
```

Schema maxima for targeted rounds/tools are both fixed to 2/3 respectively, and all credential fields remain `writeOnly`.

- [ ] **Step 2: Verify RED**

Run config and feature source-contract tests.

- [ ] **Step 3: Update default config and schema**

Keep orchestration controls editable through YAML only; do not add secret values or internal resilience controls to the chat wizard.

- [ ] **Step 4: Update README and version surfaces**

Document AI-first ordinary text, fixed first round, two autonomous targeted rounds, source degradation, TVDB credential isolation, deterministic direct links/fallback, and unchanged renaming handoff. Bump manifest/project/build example to `1.5.0`.

- [ ] **Step 5: Verify GREEN, build artifact, commit, and push**

Run focused tests, full suite, compile, schema validation, build `dist/media-search-1.5.0.tpx`, inspect the archive, then:

```bash
git add config.default.yaml config.schema.json README.md manifest.yaml pyproject.toml tests/test_config_schema_contract.py tests/test_feature_service.py
git commit -m "chore(media-search): prepare 1.5.0"
git push origin feature/media-search
```

### Task 6: Verify the search-confirmation-renaming boundary and final remote state

**Files:**
- Inspect: `src/telepiplex_media_search/service.py`
- Inspect: `/Users/young/Documents/telepiplex/.worktrees/open115/src`
- Inspect: `/Users/young/Documents/telepiplex/.worktrees/renaming/src`
- Test: `tests/test_feature_service.py`
- Test: `/Users/young/Documents/telepiplex/.worktrees/renaming/tests`

**Interfaces:**
- Search hands off confirmed `media_metadata v1`, `naming_metadata`, release data, operation ID/revision, and idempotency key to `download.provider`.
- Open115 carries the contract into `download.completed`.
- Renaming consumes confirmed metadata without letting filename inference overwrite confirmed Chinese/English titles.

- [ ] **Step 1: Add or strengthen the search handoff test**

Assert the exact selected-release call contains:

```python
{
    "media_metadata": {"schema_version": 1, "confirmed": True, ...},
    "naming_metadata": {
        "source": "confirmed",
        "media_type": str,
        "chinese_title": str,
        "english_title": str,
        "year": str,
    },
    "operation_id": str,
    "operation_revision": int,
}
```

- [ ] **Step 2: Audit Open115 pass-through and renaming merge order**

Trace the actual code from `download.provider.submit` through `download.completed` to renaming. If the confirmed metadata is already preserved, make no cross-branch code change. If a defect is proven, first add a failing test in the owning worktree, then make the smallest branch-local fix and push only that affected feature branch.

- [ ] **Step 3: Run composed verification**

Run media-search full unittest/pytest, compile, `pip check`, schema validation, artifact inspection, and whitespace checks. Run the renaming contract tests with Python 3.12 and the Core SDK on `PYTHONPATH`.

- [ ] **Step 4: Verify remote publication**

Fetch origin and require:

```bash
git rev-list --left-right --count feature/media-search...origin/feature/media-search
```

Expected: `0 0`. If renaming or open115 changed, require the same exact synchronization for that branch.

- [ ] **Step 5: Final report**

Report commit hashes, remote branches, test counts, artifact result, and the concrete search → confirmation → download → renaming evidence. Explicitly state that release-gate grammar remains a separate follow-up.
