# DeepSeek Thinking Tool Compatibility Design

Date: 2026-07-24

Status: approved for local implementation

Owner: `features/search`

## Goal

Make the OpenAI-compatible source orchestrator work with DeepSeek V4
thinking-mode tool calls without weakening Telepiplex's local tool validation,
and preserve provider error details instead of collapsing every failure into
`ai_unavailable`.

## Scope

This change covers:

- AI request payload construction;
- source-orchestration thinking and tool-choice policy;
- one bounded compatibility retry;
- provider error normalization and fallback reasons;
- configuration defaults and JSON Schema;
- regression tests for request payloads, reasoning history, retry behavior,
  and error preservation.

It does not change source adapters, entity normalization, candidate
qualification, Prowlarr queries, release gates, metadata handoff, or the
public configuration wizard flow.

## Configuration contract

`ai.source_orchestration` gains:

```yaml
thinking_mode: enabled
tool_choice_mode: omit
```

Allowed values:

- `thinking_mode`: `enabled` or `disabled`;
- `tool_choice_mode`: `omit` or `forced`.

Defaults are `enabled` and `omit`. Missing values use the same defaults so
existing installed configurations receive the compatibility fix without a
manual migration.

## Request behavior

Every source-orchestration request explicitly sends:

```json
{"thinking": {"type": "enabled"}}
```

or:

```json
{"thinking": {"type": "disabled"}}
```

When `tool_choice_mode` is `omit`, the request must not contain the
`tool_choice` key in either the mandatory first round or later targeted
rounds.

When `tool_choice_mode` is `forced`, the first request forces
`search_media_sources` and later requests use `auto`. The existing local
state machine remains authoritative: the first successful assistant response
must contain exactly one call to `search_media_sources`, even when
`tool_choice` is omitted.

## Compatibility retry

If a request that contains `tool_choice` fails with a structured provider
error whose message identifies thinking mode and `tool_choice` as
incompatible, the orchestrator retries that same request exactly once without
`tool_choice`. The provider's `param` may be `tool_choice` or empty because
the observed DeepSeek response used `param: null`.

No other 4xx response receives this retry. A failed retry returns the retry
response's normalized fallback reason.

## Thinking history

Assistant messages containing tool calls are added back to conversation
history with these fields preserved:

- `role`;
- `content`;
- `reasoning_content`;
- `tool_calls`.

If a tool-call message has `content: null`, Telepiplex stores an empty string
in history while preserving `reasoning_content` and `tool_calls`.

## Provider error contract

Non-200 responses and request exceptions return a normalized error envelope:

```json
{
  "error": {
    "kind": "provider_invalid_request",
    "http_status": 400,
    "code": "invalid_request_error",
    "type": "invalid_request_error",
    "param": "tool_choice",
    "message": "Thinking mode does not support this tool_choice",
    "retryable": false,
    "request_id": ""
  }
}
```

Kinds include:

- `provider_invalid_request`;
- `authentication_failed`;
- `permission_denied`;
- `model_or_endpoint_not_found`;
- `provider_timeout`;
- `rate_limited`;
- `provider_unavailable`;
- `provider_client_error`.

Logs contain the sanitized envelope. API keys, authorization headers, request
messages, and tool results are not included.

## Verification

Tests must prove:

- thinking mode is included in the transport payload;
- plain requests still omit tool-only fields;
- `omit` removes `tool_choice` from every orchestration round;
- `forced` retains the old forced/auto behavior when accepted;
- the exact DeepSeek compatibility error triggers one omit-field retry;
- unrelated invalid requests do not retry;
- `reasoning_content` survives tool round trips;
- provider status, code, type, parameter, retryability, and request ID survive
  transport normalization;
- default configuration validates against the public JSON Schema.

The final pass runs the complete search Feature test suite with the bundled
Python runtime and confirms the Mac workspace contains `.stfolder` but no
`.git` or `.worktrees`.
