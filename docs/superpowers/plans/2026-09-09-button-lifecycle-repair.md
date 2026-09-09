# telepiplex Button Lifecycle Repair Implementation Plan

> Implement with superpowers:subagent-driven-development and test-driven-development. The user approved the audit's implementation on 2026-09-09. Continue through local verification without a new approval gate.

**Goal:** Prevent retired controls from acting on later stages, prevent stale UI restoration, and durably retry removal of retired message controls.

**Architecture:** Extend the existing SQLite operation/segment coordinator, keep one serialized projection writer per operation, and add a durable cleanup queue for known retired Telegram messages. Feature callbacks advance state before spawning work; Host controls use message/segment/generation identity like Feature controls.

**Tech Stack:** Python 3.12, asyncio, SQLite, python-telegram-bot, pytest.

**Spec:** `docs/audits/2026-09-09-telegram-button-lifecycle-audit.md`, especially section 4.

## Constraints and rulings

- Only local Mac edits/tests; no Git, worktrees, publication, live Telegram or storage mutation.
- Existing audit is the accepted design. No repeated permission request is needed.
- Preserve current product flow and Feature identities. All new product copy uses lowercase telepiplex.
- Keep None semantics separate from the cause: server-side markup parsing does not justify blaming None.
- Use normal source copies in `/tmp` for change review, never Git metadata.
- No SDK protocol change is needed: existing operation snapshots and segments carry processing state.
- Parallel ownership: search task owns search service/tests; cleanup task owns coordinator/new cleanup module/startup tests; diagnostics task owns diagnostics/tests; controller owns handlers and their tests.

## Task 1: Search consumes the old choice before background work

Files: `features/search/src/telepiplex_search/service.py`, `features/search/tests/test_feature_service.py`.

- [x] Add a failing fixture test: block background release search; callback result must already be running, carry the identity/search segment correctly, and contain no obsolete scope/confirm keyboard.
- [x] Reject replay of consumed confirmation; keep supported retry paths explicit and preserve first-wave partial search selection.
- [x] Implement synchronous transition before spawn using existing `_advance_operation` and snapshot API; ensure background errors and cancellation still converge.
- [x] Run targeted red/green tests and the complete search tests.

Contract consumed by Host: accepted callback snapshots describe the next business state; an empty action list never implies that an obsolete choice should reopen.

## Task 2: Durable cleanup and ownership lookup

Files: `app/runtime/interaction_coordinator.py`, new `app/runtime/message_cleanup.py`, new `tests/test_message_cleanup.py`, coordinator/startup tests, `app/115bot.py`.

- [x] Add failing restart/double-failure tests: a retired message stays pending after failed delete and markup removal, survives coordinator reload, then completes on acknowledged removal.
- [x] Add persisted known-message cleanup records keyed by operation/message; enqueue old cursor in the same transaction as promotion. Preserve reason, attempts, next retry, failure and completion states.
- [x] Expose `queue_message_cleanup(operation_id, message_id, *, delete=False, reason='retired')`, `pending_message_cleanups(...)`, result acknowledgement, and `find_message_segment(chat_id, user_id, message_id)` for authorized stale-click handling. Send exact finalized signatures to controller before integration.
- [x] Provide a single cleanup worker with serialized per-operation I/O, retry backoff, restart recovery, active-message guard, and bounded cancellation/shutdown. Import the render lock lazily to avoid circular imports.
- [x] Automatically register cleanup on retired/sealed/terminal messages; never erase newer active keyboard from a queued obsolete intent. Hook worker start/stop into Host lifecycle.
- [x] Run coordinator, cleanup and startup tests; verify failures are retained rather than reported as successful.

## Task 3: Diagnostic coverage

Files: `app/runtime/telegram_diagnostics.py`, `tests/test_telegram_diagnostics.py`.

- [x] Add failing tests for edit-message-reply-markup and delete-message success/failure, including bool API results that omit message IDs.
- [x] Record target message ID, explicit keyboard intent/count, API success and sanitized failure without tokens. Retain existing outgoing event compatibility.
- [x] Run complete diagnostics tests.

## Task 4: Single writer, versioned controls, stale-click repair

Files: `app/handlers/interaction_handler.py`, `app/handlers/plugin_handler.py`, handler/pipeline tests.

- [x] Convert the temporary late-busy reproduction into a deterministic regression: hold busy I/O, release callback and render latest state; final visible projection must match the durable target.
- [x] Route busy feedback through operation serialization and live snapshot checks. Keep callback acknowledgement and Feature dispatch independent of Telegram latency; retire obsolete feedback before issuing I/O.
- [x] Accept callback result before releasing claim, then render its latest valid projection. Prevent transient busy projections from marking business projection as delivered.
- [x] Add a failing test that an old message's Host cancel cannot reach a newer segment. Version native Host control payloads and atomically claim exact current controls before dispatch.
- [x] Add failing stale-click tests for historical message, current message old generation, no active operation, and terminal operation. Repair current projection or enqueue retired-message cleanup as appropriate.
- [x] Integrate promotion/legacy/error cleanup with the durable queue. Preserve task control handoff races after a legitimate current click has been accepted.
- [x] Run handler/coordinator/plugin/pipeline tests and the end-to-end partial-selection flow.

## Task 5: Review, broad verification and local delivery

- [x] Independent review of changed source against the audit and all failure paths; resolve actionable findings before completion.
- [x] Run Host and all five Feature test suites with `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, SDK paths as in AGENTS.md.
- [x] Run relevant operation/Telegram pressure tests with latency and duplicate clicks; inspect final visible keyboards as well as side-effect counts.
- [x] Record actual commands/results, changed files and any remaining limits in a delivery report; preserve the audit as historical evidence.
- [x] Check `test ! -e .git && test ! -e .worktrees && test -d .stfolder`; hand off through Syncthing Up to Date / 最新 only.

## Preflight consistency and progress ledger

| Tasks | Shared interface / decision | Result |
|---|---|---|
| 1 / 4 | Existing operation snapshot and segment | Advance state before spawn; no SDK change |
| 2 / 4 | Coordinator cleanup API, lazy operation render lock | Separate file ownership; finalize signatures before handler integration |
| 3 / 2,4 | Telegram method signatures | Preserve callers and add delivery diagnostics only |
| 1 | Processing/replay tests vs implementation | Retain partial-result interaction and retry semantics |
| 2 | Restart/double-failure tests vs implementation | Durable records and startup worker required |
| 3 | API bool results vs diagnostics | Use target message ID fallback |
| 4 | Single writer vs fast RPC | Ack remains asynchronous; UI writes serialize |
| 5 | Review vs local no-Git rule | Source snapshots and tests replace Git-based checks |

Progress: implemented and locally verified on 2026-09-09. Full Host and all five Feature suites: 2024 passed, 3 skipped, 508 subtests passed. Final lifecycle/E2E check after making the asynchronous journal assertion deterministic: 19 passed, 7 subtests passed.

Independent reviews resolved callback cancellation stranding claims, search retry spawn rejection, in-flight cleanup address reuse, late terminal binding/promotion, and old-database ownership migration. Native callbacks and renderer writes check durable message ownership. Authoritative recovery only releases orphan claims from an earlier Host instance. Feature errors and action-only results cannot overwrite native operation cards.

Pressure verification: 200 operation pipelines / 32 concurrency / 10 injected milestone faults completed without failures. Telegram direct (40 pipelines) and default queue (12 pipelines), both with 500 ms busy latency and three clicks per selection, produced 156 correct final stage cards and rejected 104 duplicates; no duplicate download/rename effects or task/fd/lock leaks.

Host version: 3.6.10. Search manifest/package version: 2.1.3. No Git or publication performed. The source audit remains historical evidence; complete file inventory, commands, results, limitations and Syncthing handoff are in `docs/audits/2026-09-09-button-lifecycle-delivery.md`.
