# Operation Rendering and Open115 Error Design

## Goal

Restore the single-task-card Telegram interaction contract and make 115
submission failures actionable without exposing credentials, magnets, or
provider URLs.

## Approved scope

This change affects telepiplex Core and the download Feature. Search query,
candidate, release-gate, and ranking behavior remain unchanged.

Core moves every render that can mutate an operation's Telegram message under
one per-operation asynchronous lock. This includes:

- declarative actions returned directly from a Feature callback;
- background `operation.report` rendering;
- persistence of the resulting `message_id` and `message_kind`.

The lock covers the Telegram edit/send and the coordinator update as one
critical section. A background status report cannot observe an operation
before the callback renderer has persisted the message it just created.

## Same-message and read-only behavior

Incremental text states edit the current text message. If Telegram rejects an
edit for a recoverable missing/wrong-message condition, Core sends one
replacement, persists that message before the next render, and clears the
superseded message keyboard on a best-effort basis.

Core logs the sanitized Telegram error text together with operation ID,
message ID, and message kind. It never logs credentials, magnets, or raw
provider URLs.

When a valid release button is clicked, Search already freezes selection and
cancels remaining indexer work. Core must render the returned
`resolving_release` action inside the same operation lock and explicitly clear
the clicked keyboard when the next stage has no selection keyboard. Any
current `取消任务` control may remain; release-choice buttons may not.

Telegram cannot convert a text-only message into a photo or a photo into a
text-only message. Those media transitions may create a replacement message,
but all text-only incremental Prowlarr results after the transition must reuse
one message.

## Structured 115 failures

`Open115Error` carries optional safe provider metadata:

- `code`: HTTP or 115 response code;
- `operation`: the provider operation that failed;
- message: the safe provider explanation.

The download Feature classifies failures into stable user-facing codes:

- `open115_auth_failed`: authorization or token failure; remedy is `/auth`;
- `open115_directory_failed`: save-directory creation or lookup failure;
  remedy is `/config → download → 保存目录`;
- `open115_submit_rejected`: 115 rejected the offline task; remedy is to
  inspect duplicate/restricted tasks and retry or choose another release;
- `open115_request_failed`: network, HTTP, or invalid provider response;
  remedy is to check connectivity and 115 service status;
- `download_failed`: an unexpected non-provider failure.

The safe code, detail, stage, provider code, and remedy flow consistently to:

- the download job record;
- the `download.failed` event;
- the operation terminal status;
- the Telegram failure notification;
- the Feature log.

The notification begins with a plain-language summary, then shows the safe
provider detail and one concrete next action. Token values, magnets, and URLs
remain redacted.

## Release identity

- telepiplex Host display version becomes `v3.4.5-host`.
- download Feature becomes `1.0.2`.
- Search remains `1.0.8`.

## Verification

Automated coverage must prove:

- a blocked callback render prevents a concurrent operation render from
  sending a duplicate message;
- after the callback persists the current text message, the pending operation
  report edits that message;
- Telegram edit logs retain sanitized BadRequest detail and message identity;
- a callback edit without a selection keyboard clears the clicked keyboard;
- 115 authorization, directory, submit-rejection, and request failures map to
  stable details and remedies;
- an actual download job failure publishes and renders those details instead
  of only `Open115Error`;
- Host and download release identities are updated while Search is unchanged.

