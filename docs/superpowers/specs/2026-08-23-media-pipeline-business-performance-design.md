# Media Pipeline Business Performance Design

**Date:** 2026-08-23  
**Status:** Approved for direct implementation by the user's 2026-08-23 instruction

## Goal

Reduce the user-visible latency of the already-correct movie/series pipeline
without weakening identity, scope, release, storage, or cleanup verification.
The automatic product flow remains:

```text
search -> confirmation -> download -> download.completed -> rename -> completed
```

Plex/sync remains manual and is not introduced as an automatic owner, event
consumer, capability call, or completion condition.

## Scope and priority

This iteration implements all previously approved P0 items and P1-1 through
P1-3. P1-4 foreground/background concurrent operations is explicitly excluded.

- P0-0: preserve rename as the automatic terminal and replace stale synthetic
  tests that imply an automatic Plex handoff.
- P0-1: make confirmed scope a single truth across identity presentation,
  query generation, release gating, download handoff, and rename metadata.
- P0-2: separate authoritative evidence from optional enrichment; candidate
  posters and Chinese-title backfill must not block a safe Prowlarr task.
- P0-3: make Telegram rendering a best-effort projection, not a business
  transaction gate, and coalesce superseded progress revisions.
- P0-4: execute rename as a logical file transaction that reuses immutable
  preflight facts while retaining fresh post-mutation verification.
- P0-5: persist cross-Feature handoff and terminal-effect receipts and expose
  dead-letter failure on the operation instead of leaving it handed off.
- P1-1: single-flight identical provider requests and reuse safe evidence.
- P1-2: stagger Prowlarr indexers in deterministic waves while preserving the
  existing incremental gate, stable callbacks, and cancellation behavior.
- P1-3: use adaptive 115 download polling and endpoint-aware pacing with
  throttle feedback.

## Superseded and retained designs

This design supersedes the following parts of
`2026-08-13-operation-stage-sealing-and-candidate-posters-design.md`:

- Telegram identity/stage delivery success is no longer required before
  starting Prowlarr, calling download, or publishing `download.completed`.
- Candidate poster completion is no longer required before candidates become
  selectable.
- A handoff report is never synchronously blocked on Telegram rendering.

It retains milestone identity, idempotent presentation intent, message cursor
rotation, and same-message rendering where delivery succeeds.

This design fully retains
`2026-08-15-rename-convergence-without-sync-design.md`, including the rename
terminal, verified file postconditions, best-effort notification, and the
absence of `media.organized` or automatic sync/Plex work.

## Business invariants

1. One user has at most one active business operation; handoff does not release
   that ownership gate.
2. Only the declared next Feature may accept a handoff. Revisions are monotonic
   and terminal operations reject later mutations.
3. Confirmed work identity, media type, year, and selected scope cannot be
   changed by posters, localization, or optional provider results.
4. Prowlarr displays only releases that passed the existing identity, year,
   media type, scope, special-content, URL, and duplicate gates.
5. Telegram failure cannot undo a durable business effect or prevent the next
   Feature from accepting work.
6. Every cross-Feature effect has a stable idempotency key and a durable receipt.
7. Rename never overwrites a conflicting target, never guesses an unverifiable
   file identity, and never removes a directory without fresh empty/absence
   proof.
8. The automatic success promise ends at verified rename completion.

## 1. Scope single truth

`retrieval.scope` plus `evidence.decision.season_number` and
`evidence.decision.episode_number` is the confirmed retrieval authority.
`placement` remains a library/target-placement concern and cannot override the
selected retrieval scope in presentation.

Identity presentation derives its label and milestone digest from the
authoritative tuple:

```text
(media_type, scope, season_number, episode_number)
```

Whole series, season 5, and S05E03 therefore have distinct text and milestone
IDs even when the work identity is otherwise identical.

## 2. Search critical-path model

Search divides source work by purpose:

- `anchor`: exact read of the selected frozen source; mandatory.
- `authoritative_scope`: evidence required to validate series inventory or the
  selected season/episode; mandatory only when the current contract lacks it.
- `presentation_locale`: verified simplified-Chinese title; optional.
- `poster`: candidate/identity artwork; optional.
- `optional_peer`: descriptive metadata that does not affect current query or
  scope safety; optional.

The critical path is:

```text
select candidate
  -> exact anchor hydration
  -> authoritative scope enrichment only when required
  -> exact rebuild and scope choice
  -> freeze query/gate contract
  -> create Prowlarr task
```

After the Prowlarr task is created, presentation/localization enrichment may
run concurrently. It may fill an empty Chinese title or poster in the later
download naming payload, but it cannot alter anchor IDs, media type, year,
scope, query titles, or the release-gate contract. A slow or failed optional
source is recorded and ignored for business progression.

Candidate titles, years, types, sources, and buttons are presented immediately.
Poster enrichment runs in the background and may refresh the current candidate
projection only if the operation is still at candidate selection.

## 3. Source single-flight and evidence reuse

A process-local `SourceScheduler` owns bounded in-flight and short-lived success
entries. Its key includes provider, purpose, media type, stable external ID when
available (otherwise normalized identity), and scope coordinates when relevant.

- Identical safe requests share one underlying task.
- Cancelling one waiter does not cancel a flight still used by another waiter.
- Returned values are deep copies.
- Transient failures are not cached.
- Different stable IDs, media types, scopes, or purposes never coalesce.
- Exact links/IDs are preferred over title search and identity conflicts are
  never cached as accepted evidence.

## 4. Prowlarr scheduling

The existing incremental search remains authoritative. The new planner only
controls task start times.

The first wave is selected deterministically:

1. explicit `search.prowlarr.first_wave_indexer_ids`, when configured;
2. otherwise enabled indexers whose names have a positive configured
   `search.scoring.indexer_scores` value;
3. if neither produces a match, all indexers start as one compatibility wave.

The first wave starts immediately. Remaining indexers start after
`search.prowlarr.wave_delay` (default 1.5 seconds), or immediately when the
first wave completes with no eligible release. Query variants, semaphore
limits, global timeout, hard gate, rank, stable release IDs, incremental
buttons, and selection cancellation remain unchanged.

## 5. Durable handoffs, effects, and projection

Core persists two business ledgers:

- `operation_handoffs`: prepared, submitted, accepted, failed, or cancelled
  transitions for `search -> download` and `download -> rename`.
- `operation_effect_receipts`: stable Feature effects such as accepted download
  job and verified rename outcome.

`operation.report(state="handed_off")` creates a prepared receipt. The target's
first accepted report marks it accepted. Publishing `download.completed`
records its event ID as submitted. A poison/dead-letter result marks the
handoff failed and moves a still-handed-off operation to visible `failed` with
`manual_check_required`; transient delivery failures remain pending.

Milestone RPC acceptance means “presentation intent durably queued”, not
“Telegram delivered”. The intent stores mode, text, photo URL, delivery state,
attempt count, error, and delivered message target. Known delivery rejection
may retry with a bound; an uncertain network result is marked unknown and is
not blindly resent. Late milestone completion clears an operation cursor only
when owner and message target still match.

Normal operation reports are coalesced per operation: while one render is in
flight, only the newest pending revision is retained. Business reports remain
fully persisted; only obsolete Telegram projections are dropped.

## 6. Rename logical transaction

One file phase receives a `FileTransactionSnapshot` containing:

- preflight source/target info used only before mutation;
- one verified source-parent ID per unique source parent.

The snapshot is process-local, job-local, and invalid after the file phase. It
cannot replace any post-rename, post-move, or post-delete provider read.

Target conflict planning and executor preflight share the same target facts.
Source-parent IDs are collected before any rename clears provider caches and
are reused for native move grouping. Rename/post-move verification, fresh
source/target directory listings, and cleanup checks remain mandatory.

## 7. 115 pacing and polling

`EndpointPacer` classifies requests as `offline.poll`, `offline.mutation`,
`storage.read`, `storage.mutation`, or `token.refresh`. Each class has a
configured minimum start interval and throttle cooldown. HTTP 429 with
`Retry-After` slows the affected class; token refresh cannot bypass pacing.

The default storage-read profile is faster than mutations but bounded; file
info batch uses bounded workers and preserves one result per requested path.
Provider rejection or partial result never becomes a successful file fact.

Download polling starts at `poll_initial_interval` (default 2 seconds), backs
off by `poll_backoff_factor` (default 1.7) while state/progress is unchanged,
and caps at `poll_max_interval` (default 30 seconds). A progress or status
change resets the next delay to the initial interval. Cancellation continues
to wake the wait immediately.

## Failure and recovery semantics

- Optional search enrichment failure leaves the frozen query/gate contract
  usable and does not reclassify the work.
- A target Feature that is unavailable still rejects the handoff before an
  external effect begins.
- Known Telegram failure is visible in the presentation ledger and never
  changes operation success.
- Event dead-letter is visible as a failed handoff with event/target/error
  details; it cannot remain an invisible active operation.
- Rename provider responses are reconciled against fresh observed state.
  Unknown or partially applied mutations fail closed and are safe to inspect or
  rerun through existing provider-ID rules.

## Acceptance criteria

1. Season 5 and S05E03 identity cards, queries, gates, and handoffs carry the
   same scope; their milestone IDs differ from whole-series.
2. A never-returning poster or Douban localization call cannot delay candidate
   buttons or Prowlarr task creation.
3. Two identical source requests perform one fetch; unsafe identities never
   share a flight.
4. Preferred Prowlarr indexers start first, remaining indexers start by the
   bounded wave delay, and the first eligible release remains immediately
   selectable.
5. A permanently blocked Telegram renderer does not prevent
   search -> download -> rename from reaching rename terminal.
6. A poison `download.completed` event makes the operation visibly failed and
   preserves its download receipt for manual recovery.
7. A 16-file same-parent rename reads that source parent once during file
   preparation, retains all fresh post-mutation checks, and reduces storage RPC
   calls from the observed 76-call baseline to at most 60 in the equivalent
   instrumented scenario.
8. Adaptive polling checks quickly at the start, reduces a 30-minute unchanged
   task's list calls by at least 50% versus fixed 10-second polling, and remains
   immediately cancellable.
9. Root, search, download, rename, sync, and caption suites pass after known
   stale version assertions are aligned with current manifests.

## Local delivery boundary

All edits and tests run only in `/Users/young/Documents/telepiplex`. No Git
command, worktree, branch, tag, push, PR, or publication is used. Delivery ends
after local verification; the user then waits for Syncthing `Up to Date / 最新`
before any Unraid-side Git or release action.
