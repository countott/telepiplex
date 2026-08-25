# Performance Observability and Safe Directory Preflight Design

**Date:** 2026-08-24  
**Status:** Approved for implementation by the user

## Goal

Make the next telepiplex performance iteration measurable in production and
remove redundant 115 pre-mutation reads without weakening identity, conflict,
rollback, move-reconciliation, or cleanup guarantees.

The automatic business flow remains:

```text
search -> confirmation -> download -> download.completed -> rename -> completed
```

Plex and sync remain manual. Event-dispatch concurrency is deferred. Candidate
locale and authoritative-scope behavior are recorded below as business rules,
but are not changed by this implementation.

## Scope

1. Emit privacy-safe structured performance observations through the existing
   Feature diagnostic transport for Search and Download.
2. Replace only eligible rename pre-mutation path reads with an adaptive,
   complete directory-listing snapshot and exact-read fallback.
3. Add a fresh pre-submit directory gate for native 115 move batches.
4. Preserve all current post-mutation reconciliation, cleanup, journal, and
   user-facing business behavior.

## Non-goals

- No OpenTelemetry SDK, collector, database, network sink, or telemetry
  configuration.
- No rate-limit reduction, mutation parallelism, batch-rename endpoint, or
  change to 115 pacing decisions.
- No EventDispatcher concurrency change.
- No change to candidate Chinese-title timing or confirmation-scope authority.

## Existing contracts retained

1. `trace_id`, `span_id`, `parent_span_id`, `operation_id`, and
   `request_id` remain the only cross-process correlation fields.
   `search_session_id` is a Search fact, not a new global identity.
2. `FileTransactionSnapshot` is immutable, process-local, and valid only
   before a mutation. It cannot prove post-rename, post-move, or post-delete
   state.
3. A target owned by the same provider ID is a no-op; a different target ID is
   a conflict; a same-hash different-ID target may use only the existing
   verified duplicate-recovery path.
4. A source missing, replaced, or lacking a verifiable parent fails closed.
5. Native move is authoritative only after a fresh source/target listing proves
   the expected ID/name target and source absence.
6. Directory cleanup still requires a fresh empty/absence proof before and
   after deletion.

## 1. Structured performance observations

### Transport and failure model

Use the existing SDK diagnostic handler. New observations are bounded log
records with explicit `event_name` and `diagnostic_fields`; they add no
queue, persistence, retry, or network call.

```python
try:
    observer(event_name, facts)
except Exception:
    pass
```

Observer failure must never change a provider result, cache result,
cancellation result, pacing reservation, or retry decision.

### Search events

| Event | Safe facts | Excluded values |
|---|---|---|
| `search.discovery.*` | entry kind, query length, candidate/exact/relation counts, error code, duration | query, title, URL, facts |
| `search.source.request` | provider, purpose, media type, scope, outcome, wait/duration, cacheable | request identity, URL, external ID |
| `search.hydration.*` | frozen-link count, anchor-required, metadata-hydrated, enrichment-needed, error code, duration | source URL, candidate title |
| `search.prowlarr.*` | wave, indexer/query counts, raw/eligible/result counts, status class, elapsed | query text, release title, URL |

`search_logging.py` retains its existing human-readable messages. A new
helper emits explicit diagnostics only for these new privacy-safe observations.
`SourceScheduler` accepts an optional observer and exposes cache hit,
single-flight join, queue wait, completion, and failure without exposing
`SourceRequestKey.identity`.

### Download events

`Open115Client` accepts an optional callback from the Download runtime and
emits:

- `download.request.completed` and `download.request.failed`: endpoint
  class/operation, pacer wait, HTTP elapsed, status class, retryability;
- `download.pacing.waited` only when pacer wait is at least 50 ms;
- `download.pacing.throttled` for HTTP 429 with parsed cooldown; and
- `download.poll.backoff_changed` only when adaptive delay changes.

No event contains a path, magnet, URL, token, header, response body, file
name, or info hash.

### Correlation and overhead

Feature Runtime already binds diagnostic context to each request.
`asyncio.to_thread` preserves it for current synchronous provider calls.
Search includes its existing plan ID as `search_session_id`; cross-Feature
analysis joins on `operation_id`. Timing uses monotonic clocks only.

## 2. Adaptive directory-listing preflight

### Strategy selection

`build_file_transaction_snapshot()` keeps its result shape:

```python
FileTransactionSnapshot(
    file_info: Mapping[str, PreflightFileInfo | None],
    source_parent_ids: Mapping[str, str],
)
```

It calculates:

```text
exact budget     = requested source + target + source-parent paths
directory budget = unique required parent lookups + complete listings
```

It uses a directory snapshot only when the optimistic directory budget is
strictly lower. One-file-per-directory work therefore keeps the current exact
batch behavior.

### Trusted listing rules

For each selected existing parent directory:

1. read parent info and require a stable directory ID;
2. read all pages with `cid`, `offset`, `limit=1000`, and `show_dir=1`;
3. reject an empty page with `has_more`, non-progressing/repeated pages,
   conflicting duplicate IDs, unbounded page count, or ambiguous completion;
4. project a requested child by parent path and child name only if it has
   stable ID, SHA-1, and non-negative byte size.

An absent target parent means children under it are absent in this preflight;
existing directory creation remains later behavior. If any selected listing is
untrusted or lacks a required fact, discard all partial listing results and
execute the current exact batch for the whole transaction. A partial listing
never proves absence.

Raw 115 list shapes stay private to `file_executor.py`; the snapshot still
contains only existing `PreflightFileInfo` facts.

### Fresh native-move gate

Before `move_files_by_id()` submits a native batch, take fresh complete
source/target directory listings for the chunk. Permit a file only when:

- its source parent still contains the selected provider ID under the expected
  current name; and
- its target directory has no entry with the desired name.

If source is absent and target has the same ID under the desired name, mark it
already organized without a new mutation. All other stale or ambiguous states
fail that file closed and omit its ID from the batch.

This gate is additive. It does not replace the per-rename journal, fresh
post-move reconciliation, or cleanup's fresh empty/post-delete reads.

### Expected benefit

For the 16-file, one-source-parent, one-target-parent fixture:

```text
current preflight:       33 exact provider reads
trusted initial snapshot: 4 reads (two infos plus two listings)
fresh pre-submit gate:    2 listings
```

The safe target is about six pre-mutation reads, not an unsafe four-read
claim. At the current 0.25-second storage-read pacing, it retains an estimated
6–7 seconds of request-start saving while adding the race detector.

## 3. Deferred business-rule decisions

### Candidate Chinese-title localization

No code changes in this iteration. The agreed boundary is:

- a candidate with an existing `douban_subject` may receive a
  presentation-only update while candidate selection remains current;
- a candidate without that direct binding creates its English Prowlarr task
  first, then may backfill Chinese title for naming/presentation only;
- an update preserves candidate ID, order, identity, scope, and Prowlarr query
  and is guarded by plan ID, candidate-set revision, and stage.

### Confirmation scope hydration

No code changes in this iteration. The agreed boundary is:

- exact frozen links and anchor validation are mandatory first;
- authoritative scope enrichment happens only for a selected candidate missing
  verified scope;
- a bounded season or episode request never falls back to whole-series;
- only an originally unscoped/whole-series request retains the existing
  whole-series fallback; and
- optional presentation facts cannot mutate the frozen retrieval contract.

## Acceptance criteria

1. New observations have explicit event names, preserve diagnostic correlation,
   and omit sensitive/query/path values.
2. Observer failure affects no scheduler, provider, pacing, retry, or polling
   result.
3. 115 observations expose pacer wait, HTTP time, throttle, and adaptive delay
   transitions without a provider payload.
4. A well-formed shared-directory listing gives the same preflight facts as
   exact reads.
5. Incomplete or ambiguous pagination and incomplete item identity use exact
   fallback or fail closed; no fact is fabricated.
6. The pre-submit gate omits stale/foreign files, retains post-move and cleanup
   verification, and never submits a rejected ID.
7. Core and all Feature test suites pass.

## Local delivery boundary

All edits and tests run only in
`/Users/young/Documents/telepiplex`. Do not run Git, create worktrees,
publish, or connect this checkout to GitHub. Delivery ends after local
verification; wait for Syncthing `Up to Date / 最新` before Unraid-side work.
