# Cancel and Exit Interaction Semantics Design

## Goal

Remove redundant `取消` and `退出` controls and make their user-visible
meaning consistent across telepiplex Core and the five built-in Features.

This change covers interaction controls and their result copy only. It does not
change the search evidence, candidate, metadata, Prowlarr, or download business
pipeline.

## Approved semantics

- `退出` closes an input, configuration, browsing, or pre-execution selection
  flow. No running task is stopped and no completed side effect is rolled back.
- `取消任务` stops an operation that is running or owns a durable pending job.
  Completed external effects remain unless the operation explicitly supports
  rollback.
- `取消并回滚` stops an operation and restores every side effect that the
  operation contract declares reversible.
- A rendered keyboard must not contain multiple terminal-control buttons
  (`退出`, `取消`, `取消任务`, or `取消并回滚`) with the same `callback_data`.
  Non-control navigation buttons may share a destination when the current
  candidate set makes that behavior intentional.
- Technical callback payloads and operation actions such as `cancel`, `exit`,
  and `rollback` retain their existing identities. This is a user-interface
  semantics change, not a protocol migration.

## Scope by component

### Core

- The Feature configuration chooser uses `退出` and reports that the chooser
  was exited.
- Host operation controls remain `退出`, `取消任务`, and `取消并回滚`; these
  controls already represent distinct operation actions.
- Configuration-switch rollback messages remain cancellation messages because
  they describe a running operation that was stopped and restored.

### download

- `/q` and `download:exit` consistently report that the current interaction was
  exited.
- Prompts that refer to `/q` use `退出`.
- QR authorization, download submission, token persistence, and rollback keep
  cancellation wording because they stop active work.

### search

- Recoverable planning failures and complete Prowlarr-query failure screens
  show one `退出` button instead of adjacent `取消` and `退出` buttons that send
  the same callback.
- Search configuration closure reports `已退出 search 配置。`.
- Active planning, Prowlarr lookup, release resolution, and submission keep
  cancellation wording.
- No search business-pipeline behavior changes in this implementation.

### rename

- Configuration closure reports `已退出 rename 配置。`.
- Active rename cancellation and rollback wording remains unchanged.

### sync

- Configuration closure reports `已退出 sync 配置。`.
- The manual Plex library chooser uses `退出`, because no scan operation exists
  before a library is selected.
- Plex media, artwork, audio, or subtitle selection for an existing job uses
  `取消任务`, because the callback terminalizes the durable job.
- Active Plex task cancellation and non-rollback disclosures remain unchanged.

### caption

No current `取消` or `退出` interaction exists, so no production change is
required.

## Data and control flow

Feature keyboards continue to emit their existing namespaced callback payloads.
Core continues to route host operation controls through `operation.control`.
Only the displayed label and the matching completion message change when the
current label does not match the approved semantics.

No callback schema, operation state, idempotency key, plugin manifest,
capability, or persisted job record changes.

## Error handling

Existing expired-session, stale-operation, unavailable-route, cancellation,
and rollback behavior remains unchanged. Copy changes must not turn a failed or
partially rolled-back operation into a successful exit message.

## Testing

- Add or update Core tests for the Feature configuration chooser and its exit
  result.
- Update download tests for `/q` and exit copy.
- Update search tests so recoverable planning and Prowlarr failure keyboards
  contain one `退出` control with no duplicate callback.
- Update search, rename, and sync configuration-wizard tests for exit copy.
- Update sync tests so the pre-scan chooser uses `退出`, while durable-job
  selection uses `取消任务`.
- Add a rendered-keyboard contract assertion that rejects terminal-control
  buttons with duplicate `callback_data` values in one keyboard.
- Run the affected targeted suites, then the repository and five Feature test
  suites required by the local telepiplex handoff contract.

## Out of scope

- Repairing the search source-orchestration bypass.
- Renaming callback payloads or operation-control protocol actions.
- Introducing a shared SDK button factory.
- Changing cancellation, rollback, or persistence behavior.
- Publishing, Git operations, tags, releases, or Unraid-side commands.
