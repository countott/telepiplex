# DeepSeek Thinking Tool Compatibility Implementation Plan

> **Execution:** Implement inline in the current Codex task. Git operations are
> prohibited by `/Users/young/Documents/telepiplex/AGENTS.md`.

**Goal:** Support DeepSeek V4 thinking-mode tool calls, preserve reasoning
history, and retain real provider error details.

**Architecture:** Extend the shared AI transport with thinking payloads and a
normalized provider-error envelope. Make the bounded source orchestrator
choose whether to omit or explicitly send `tool_choice`, while retaining local
first-tool validation and allowing only one narrowly matched compatibility
retry.

**Tech Stack:** Python 3.12, asyncio, requests, unittest, pytest, PyYAML, JSON
Schema.

## Global constraints

- Do not execute Git commands.
- Do not change source adapters, candidate qualification, Prowlarr, release
  gates, metadata handoff, or the public configuration wizard.
- Use test-first red-green cycles.
- Never log credentials, prompts, source facts, or authorization headers in
  provider-error records.

### Task 1: Transport contract and provider errors

**Files:**

- Modify: `features/search/tests/test_search_ai_pipeline.py`
- Modify: `features/search/src/telepiplex_search/ai.py`

**Produces:**

- `chat_completion_messages(..., thinking_mode=None)`;
- structured provider error envelopes for non-200 responses and request
  exceptions.

Steps:

1. Add failing tests for `thinking.type`, structured 400 details, request ID,
   retryability, timeout, and unavailable-network kinds.
2. Run only those tests and confirm expected assertion failures.
3. Implement minimal payload and error normalization.
4. Re-run the focused AI transport tests.

### Task 2: Orchestration policy and compatibility retry

**Files:**

- Modify: `features/search/tests/test_source_orchestrator.py`
- Modify: `features/search/src/telepiplex_search/source_orchestrator.py`

**Produces:**

- settings `thinking_mode` and `tool_choice_mode`;
- omit-field policy for thinking mode;
- one exact compatibility retry;
- assistant history normalization preserving `reasoning_content`;
- accurate provider fallback reasons.

Steps:

1. Add failing tests for default omit behavior across first and targeted
   rounds, explicit forced behavior, exact compatibility retry, unrelated
   400 no-retry, and reasoning history preservation.
2. Run the new tests and confirm expected failures.
3. Implement minimal policy, retry, history, and fallback helpers.
4. Re-run the complete source-orchestrator test module.

### Task 3: Configuration contract

**Files:**

- Modify: `features/search/tests/test_config_schema_contract.py`
- Modify: `features/search/config.schema.json`
- Modify: `features/search/config.default.yaml`

**Produces:**

- public enums for `thinking_mode` and `tool_choice_mode`;
- defaults `enabled` and `omit`.

Steps:

1. Update the contract test first and confirm failure.
2. Add the two schema fields and defaults.
3. Run schema and default-config tests.

### Task 4: Verification

1. Run the focused AI, orchestrator, and schema tests.
2. Run the complete `features/search` unittest suite.
3. Run the complete `features/search` pytest suite.
4. Compile all modified Python modules.
5. Validate the JSON Schema and YAML default through tests.
6. Confirm `.git` and `.worktrees` are absent and `.stfolder` exists.
7. Report every created and modified file and remind the user to wait for
   Syncthing to show `Up to Date / 最新`.
