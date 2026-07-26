# Runtime Recovery and Metadata Reliability Design

## Goal

Repair every product defect exposed by the 2026-07-26 runtime log while
preserving the user's intentional workflow boundaries.

## Product boundaries

- `/s` and `/m` remain independent entry points. A later `/m` operation must
  not inherit, correlate with, or resume a previous `/s` operation.
- `/m` is nevertheless a first-class series intake. Its media probe must carry
  enough episode-pack evidence for Search to choose a safe series scope.
- Prowlarr indexer HTTP 400 responses remain visible provider diagnostics and
  are not disabled or hidden by this change.
- Telepiplex still owns at most one active download pipeline. Reattaching an
  already-existing 115 task observes that external task; it does not create a
  second Telepiplex task.

## Metadata probe and series scope

Rename recognizes `SxxExx`, `NxM`, and bare `Exx` episode filenames. The probe
keeps the observed episode numbers, season numbers when present, file count,
and a stable content shape.

Search consumes that probe after resolving a unique series identity:

- explicit season evidence selects that season;
- a multi-season probe selects the whole series;
- a bare-episode pack may select a season only when provider inventory makes
  the mapping unambiguous;
- otherwise Search asks for scope instead of inventing a season.

This scope derivation applies equally to a series reached through `/m` and to
other callers of the metadata capability. It creates no `/s` to `/m` linkage.

## AI response normalization

OpenAI-compatible providers may return `message.content` as a JSON object, a
JSON string, or content-part arrays. Search normalizes these shapes once at
the provider boundary and passes one JSON object to the planner and
source-orchestrator validators. Invalid shapes retain distinct protocol
failure reasons rather than collapsing into an unexplained parse failure.

## Existing 115 task recovery

115 response code `10008` means the submitted offline task already exists.
The download Feature treats it as a recoverable attach signal:

1. query the external offline task list and identify the task by the submitted
   link's info-hash/task identity;
2. report that Telepiplex has reattached and show the current external state;
3. if the task is running, resume normal progress polling;
4. if it is in a retry/failed state, notify the user and continue through the
   existing retry polling path;
5. if it is complete, continue file discovery and the downstream pipeline.

If no matching external task can be found, the stable submit-rejection failure
is retained. A task already renamed or moved is not the same external task and
does not get falsely matched.

## Japanese title policy

Japanese animation series and movies may satisfy the Latin folder-name policy
with a deterministic Kana-to-Hepburn romanization derived from the Japanese
canonical title when an explicit romaji title is absent. Existing
source-supplied romanized titles remain preferred. Titles containing Kanji
without a source-supplied reading are not guessed. The fallback is limited to
Japanese animation series/movies and never fabricates identity for unrelated
content.

Evidence-source outages remain partial-provider failures. An empty expanded
query set is reported as unavailable evidence, not as proof that the title
does not exist.

## Error and storage contracts

Cross-module capability failures carry a sanitized structured envelope:
`code`, `stage`, `detail`, and `retryable`. Rename preserves this envelope and
reports it to the operation instead of silently replacing it with an empty
metadata result.

Fallback moves use the detailed storage result. `copy_failed` and
`copied_source_retained` remain distinct, so users are not told a generic move
failed after a successful copy.

## Runtime identity and log severity

The release image receives the source revision as a Docker build argument and
exports it as `TELEPIPLEX_COMMIT`. Local builds continue to use `unknown`.

The plugin supervisor parses the Feature's structured log prefix and emits
embedded `WARNING`, `ERROR`, and `CRITICAL` lines at matching Host severity.
Unstructured stdout stays `INFO`; unstructured stderr stays `WARNING`.

## Verification

Tests must cover each behavioral branch above, including malformed provider
content, ambiguous bare-episode packs, missing duplicate tasks, retry-state
reattachment, completed-task continuation, Japanese romanization, partial
evidence outages, structured cross-module failures, detailed fallback moves,
Docker revision propagation, and embedded Feature log severity.
