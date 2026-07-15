# Core Feature Interaction Coordination Design

**Date:** 2026-07-15

**Status:** Approved for implementation

## Purpose

Telepiplex Core and every current Feature must present one coherent Telegram interaction model:

- `/start` and Telegram's command menu expose commands from active Feature manifests.
- Every non-terminal interaction step has one explicit way to leave.
- Every long-running task exposes meaningful status.
- While an interaction or task owns a user, unrelated input is dropped instead of queued as pending work.
- Task cancellation uses compensation only when exact rollback can be proven; otherwise it stops future work and reports the remaining effects.

The design applies to these independent branches and worktrees:

- `feature/telepiplex-core`
- `feature/open115`
- `feature/media-search`
- `feature/renaming`
- `feature/plex-management`

It does not add `/mag` or `/scan`. Existing command names remain authoritative.

## Current Constraints

- `main` is intentionally empty and is not the implementation target.
- Core owns Telegram update routing and Feature process supervision.
- Features run as isolated child processes through the Telepiplex Feature SDK.
- Feature branches remain independently releasable.
- The existing Core API is `1.0`; the coordinated task protocol requires Core API `1.1`.
- Older API 1.0 Features must continue to start on Core 1.1.
- Newly released coordinated Features require Core API `>=1.1,<2.0`.

## Chosen Architecture

Core owns coordination. Features own domain execution and compensation.

Feature-local coordination was rejected because it cannot stop a user from starting work in one Feature while another Feature owns that user. Process-level cancellation was rejected because it affects unrelated work in the same Feature and cannot perform domain-specific rollback.

The implementation introduces three cooperating units:

1. `InteractionCoordinator` in Core stores the single active operation for each `(chat_id, user_id)` and validates all state transitions.
2. The Core API/Feature SDK 1.1 operation protocol lets Features open interactions, report task progress, hand tasks to another Feature, and receive exit/cancel/rollback controls.
3. Each Feature implements its own interaction cleanup, cancellation checkpoints, and exact compensation where supported.

## Operation Model

An operation covers both an input-driven interaction and any long task that follows it. It is identified by a globally unique `operation_id` and has exactly one current Feature owner.

### States

- `awaiting_input`: the Feature is waiting for text, a selection, or confirmation.
- `running`: the Feature is performing work.
- `handed_off`: the current Feature has successfully transferred responsibility to the next Feature.
- `cancelling`: a cancellation request has been accepted and the current atomic operation is reaching a safe checkpoint.
- `rolling_back`: compensating actions are running in reverse order.
- `completed`: all requested work reached its intended terminal result.
- `cancelled`: execution stopped and no exact compensation was required or supported.
- `rolled_back`: execution stopped and all recorded reversible changes were restored.
- `partially_rolled_back`: execution stopped but one or more recorded changes could not be restored.
- `failed`: execution stopped because of an error.
- `interrupted`: no active executor can be confirmed after a process or Core restart.

Only the last six states are terminal. A terminal state releases the user's input gate.

### Controls

Core renders one control appropriate for the current stage:

- `exit` renders **退出** for an input or confirmation step that has not started work.
- `cancel` renders **取消任务** when future work can stop but completed effects cannot be precisely reversed.
- `rollback` renders **取消并回滚** only when the Feature has a complete, verified compensation plan for every completed mutation.

The control may change at any state transition. In particular, it must downgrade from `rollback` to `cancel` before an irreversible delete, a Plex scan, a remote refresh, or any operation whose affected object identity cannot be proven.

Existing Feature-local cancel buttons are not duplicated. During migration, each Feature returns a single explicit control descriptor; Core either uses the Feature-rendered control or injects the Core control when the response has none.

### Global Input Gate

All Telegram messages, commands, and callback queries pass through the coordinator before existing handlers:

- When no operation owns the user, routing is unchanged.
- In `awaiting_input`, only input and callbacks belonging to the active operation plus its control are allowed.
- In `running`, `cancelling`, or `rolling_back`, ordinary text and commands are silently dropped.
- An unrelated callback is answered with the toast `当前任务执行中` and has no side effect.
- A valid operation control is routed exactly once. Repeated presses return the current task state and never repeat compensation.
- No blocked update is stored for later delivery.

The gate is keyed by both chat and user so a user cannot accidentally collide with another user's operation in a shared chat.

## Core API and SDK 1.1

Core API 1.1 adds optional operation methods while retaining every 1.0 method.

### Feature-to-Core reports

The SDK Core client exposes an operation report method with these fields:

- `operation_id`
- `chat_id`
- `user_id`
- `state`
- `stage`
- `status_text`
- `control`
- `revision`
- optional `next_plugin_id` for a handoff
- optional structured `details` containing only non-secret status data

Core authenticates the reporting Feature and rejects reports for an operation it neither owns nor is the declared handoff target for. Revisions increase monotonically. A report at or below the current revision is ignored so a late progress update cannot overwrite a cancellation or later Feature stage.

### Core-to-Feature controls

The Feature runtime exposes an operation control handler receiving:

- `operation_id`
- `action`: `exit`, `cancel`, or `rollback`
- the last accepted Core revision

The handler returns the accepted operation state and next revision. Control handling is idempotent.

### Long-task execution

Long handlers must return an operation descriptor promptly, then run through the Feature runtime's managed background-task facility. A Telegram-facing command or callback must not remain blocked on a long network request, download poll, filesystem pipeline, or Plex scan.

Managed work has a stable task ID and reports checkpoints through Core. Cancellation sets a cooperative cancellation flag. A blocking atomic call is allowed to finish; the Feature checks the flag before starting the next operation.

### Cross-Feature handoff

The same `operation_id` is propagated in capability payloads and events:

1. media-search reports `handed_off` only after open115 accepts the download.
2. open115 reports ownership when it accepts the capability call and passes the ID through `download.completed`.
3. renaming reports ownership when it accepts that event and passes the ID through `media.organized`.
4. plex-management reports ownership when it accepts that event.

Core changes the owner atomically only when the target Feature has accepted the operation. A handed-off operation remains active and never briefly releases the user's gate.

## Status Delivery

Core stores the Telegram chat ID and status message ID for the active operation. Feature reports update the same status message and replace its control button.

If an edit fails because the message no longer exists or Telegram rejects the edit, Core sends a replacement status message and stores its ID. If a report arrives before the initial status message has been stored, Core retains only the newest revision and renders it after the initial response completes.

Status is stage-based rather than timer-based. A Feature reports before and after meaningful external calls, at cancellation checkpoints, at handoff, and at terminal outcomes. It does not emit artificial second-by-second progress.

## Persistent Coordination and Recovery

Core stores the minimum task record in `/config/core.db`:

- `operation_id`
- `chat_id` and `user_id`
- current `plugin_id`
- state, stage, control, and revision
- Telegram status message ID
- created and updated timestamps
- sanitized structured details required for recovery reporting

The table never stores access tokens, refresh tokens, API keys, raw magnet links, cookies, or full media metadata.

On Core startup, the coordinator loads every non-terminal operation after Feature startup completes. It requests operation snapshots from the declared owner:

- A confirmed running or resumable task remains gated and resumes status reporting.
- A confirmed terminal task is finalized and releases the gate.
- An operation with no active executor becomes `interrupted`, releases the gate, and reports the last confirmed stage plus manual checks.

No stale `running` row may permanently block a user.

## Dynamic Command Discovery

The active Core router snapshot is the single source for Feature commands.

### `/start`

`/start` renders:

1. Core commands: `/start`, `/reload`, `/plugin`, and `/config`.
2. One section per active and routable Feature.
3. Every non-reserved command and description from that Feature's manifest, in declaration order.

Disabled Features and Features blocked by missing capabilities are not advertised as executable. Core-reserved commands are shown once under Core and cannot be overridden by a Feature manifest. The open115 manifest's legacy `config` declaration is removed from the advertised command surface; `/auth` and Core `/config` remain the supported configuration entries.

### Telegram command menu

The bot command menu uses the same builder as `/start`:

- Core commands first.
- Features sorted by `plugin_id`.
- Commands within a Feature remain in manifest order.
- Core-reserved names are deduplicated in favor of Core.

The menu is synchronized after Core startup and after successful install, update, enable, disable, rollback, or remove operations. A Telegram menu synchronization failure does not roll back the completed Feature lifecycle operation. The user-facing operation result reports the menu failure, Core logs it, and the next lifecycle change or Core restart retries synchronization.

No `/mag` or `/scan` alias is introduced.

## Feature Behavior

### open115

Interactive controls cover authorization mode selection, Access Token entry, Refresh Token entry, QR authorization waiting, and magnet destination selection.

Authorization status stages are mode selection, credential collection, QR creation, QR wait, token exchange, token persistence, and completion. Token persistence snapshots the previous private configuration. If cancellation occurs while exact restoration remains possible, the control is `rollback`; after successful atomic persistence the operation is terminal.

Download stages are preparing submission, submitted to 115, waiting for completion, reading the file tree, and handing off to renaming. Once an offline task is submitted, open115 cannot prove that no media content was created, so the control is `cancel`, not `rollback`.

On cancellation, open115 attempts one standard offline-task deletion only when it has an unambiguous task ID or InfoHash. If the identifier is unknown or deletion fails, it preserves the remote task record, does not retry destructively, and reports that the task remains. It never deletes downloaded media content as part of cancellation.

### media-search

Interactive controls cover empty-query input, media-plan confirmation, release selection, and every configuration-wizard prompt.

Task stages include evidence planning, provider lookup, Prowlarr search, release ranking, link resolution, and download submission. These stages are read-only until submission, so they use `cancel`. After open115 accepts the download, media-search hands off the operation instead of completing the user gate.

Feature configuration application snapshots the old config and active process route through Core's existing atomic configure flow. During config write and reload, exact restoration uses `rollback`.

### renaming

Interactive controls cover every configuration-wizard prompt. Automatic organization work joins the originating operation.

Task stages include metadata resolution, plan construction, conflict validation, directory preparation, rename, move, cleanup, and event publication.

Before any storage mutation, renaming records stable source and destination identities plus the inverse operation. Rename and move stages expose `rollback` only while every completed mutation can still be validated and reversed. Compensation executes in reverse order and stops on the first identity conflict, producing `partially_rolled_back` with restored and remaining paths.

Before deleting unmatched files, cleaning source directories, or accepting storage results that cannot distinguish copy from copy-plus-delete, the control downgrades to `cancel`. Cancellation never invents or guesses a reverse filesystem operation.

### plex-management

Interactive controls cover every configuration-wizard prompt, AI write confirmation, and manual match selection. A single immediate read-only command such as `/plex` with no arguments is terminal and does not receive a meaningless exit button.

Task stages include AI planning, scan preparation, scanning, locating, matching, localization, artwork, streams, and completion.

Plex scanning, metadata refresh, and any write whose previous value cannot be restored exactly use `cancel`. Cancellation stops later stages but does not claim to stop or reverse a Plex server operation already accepted by Plex.

Only operations with a captured old selection and a verified inverse API, such as supported audio or subtitle selection restoration, may use `rollback`.

## Configuration Wizards

Every current Feature configuration wizard follows the same rule:

- The first choice screen retains its existing cancel control.
- Every text-input prompt gains an explicit exit control.
- Boolean and confirmation screens retain exactly one cancel/exit control.
- Invalid input re-renders the prompt with the same control.
- Exit clears both Feature-local session data and Core's operation record.
- Saving and Feature reload are reported as a running Core-owned configuration task.

Existing secrets remain redacted. Status reports never echo submitted values.

## Rollback Safety

Rollback is compensation, not deletion-based cleanup.

- It acts only on objects recorded before mutation with stable identity.
- It verifies that the current object still matches the recorded result before applying the inverse.
- It never deletes downloaded media to restore a pre-download state.
- It never deletes content that existed before the operation.
- It never assumes an external API call failed merely because the local request timed out.
- A failed or ambiguous inverse produces `partially_rolled_back` and an explicit manual-action list.

If exact rollback is impossible by design, the UI displays **取消任务** from that stage onward.

## Error Handling

- Invalid or unauthorized Feature reports are rejected without changing the operation.
- A cancellation failure while execution remains active leaves the gate in place and reports `取消失败，任务仍在执行`.
- A lost Feature process is not treated as cancelled until Core confirms no active executor remains.
- A terminal Feature error releases the gate only after background work has stopped or been accounted for.
- Duplicate cancellation, completion, and handoff reports are idempotent.
- Sensitive exception messages pass through the existing Core and Feature sanitizers.

## Compatibility and Release Order

1. Release Core with Core API 1.1, the optional operation protocol, dynamic command discovery, and compatibility with API 1.0 Features.
2. Release open115, media-search, renaming, and plex-management independently with `core_api: ">=1.1,<2.0"`.
3. Until a Feature is upgraded, it continues to run but does not claim the complete operation-control contract.
4. Catalog releases retain their existing dependency relationships and immutable artifact checksums.

Implementation and verification occur in each existing Feature worktree. `main` remains untouched.

## Verification Strategy

### Core tests

- `/start` uses the active router snapshot and groups real manifest commands.
- The Telegram command menu changes after every Feature lifecycle transition.
- Reserved Core commands cannot be duplicated or overridden.
- The global gate drops unrelated text and commands and answers unrelated callbacks without routing them.
- Operation revisions reject late reports.
- Repeated controls are idempotent.
- Persistent active operations reconcile after Core and Feature restarts.
- Status edit failure falls back to a new message.

### Feature tests

For each current Feature:

- Every non-terminal response has exactly one explicit exit, cancel, or rollback control.
- Invalid input preserves the control.
- Every meaningful long-task stage is reported.
- Cancellation stops before the next side effect.
- The control downgrades before an irreversible step.
- Full compensation returns `rolled_back`.
- Inverse failure returns `partially_rolled_back` with actionable details.

### Cross-Feature tests

- media-search, open115, renaming, and plex-management preserve one `operation_id` through the complete event chain.
- Ownership changes only after the next Feature accepts the work.
- The input gate remains active during every handoff.
- Cancellation is routed to the current owner.
- No late report from a former owner overwrites the current state.

### Build and runtime verification

- Run the complete test suite in each of the five worktrees.
- Run Python compilation checks for all changed Python sources.
- Build all four `.tpx` artifacts with the updated Core builder.
- Install the built artifacts into a temporary Core plugin root.
- Exercise command discovery, operation progress, cancellation, rollback, restart recovery, and the full Feature handoff chain through the real local RPC runtime.
- Run Telepiplex-aware `git diff --check` independently in each worktree.

