# P1 Pipeline Reliability Design

**Date:** 2026-07-12

**Status:** Approved for direct TDD implementation

**Baseline:** local `main` at `931fbc55d807b0954d2a6a7acb49c5f8b3924778`

## 1. Goal

Fix the approved P1 defects in the composed `main` runtime without starting the
feature-branch composability work and without expanding into the deferred P2/P3
audit items.

The implementation must make downloaded-file handling explicit, preserve the
confirmed media contract, keep ordinary movies and episodes as the Plex
baseline, layer Special handling as a patch, isolate Plex optional-service
failures from the bot, and prevent duplicate automatic Plex execution.

## 2. Scope

This change includes:

1. Download-time file classification and plan coverage.
2. Rule-first, AI-assisted, deterministically validated episode mapping.
3. Per-item rename execution results and one complete user summary.
4. Canonical Chinese-title enrichment after the English Prowlarr search.
5. Lazy Plex AI initialization and module-level startup fault isolation.
6. A common Plex target pipeline for movies and episodes, with Special patches.
7. Created-only automatic Plex submission and explicit interrupted jobs.

This change excludes:

- P2/P3 fixes from the 2026-07-12 audit.
- Module feature-branch mergeability or portability changes.
- Subtitle or sidecar preservation. Another pipeline owns those assets.
- Automatic retry of interrupted Plex jobs.
- A distributed lease or long-running job claim protocol.

## 3. File Retention Policy

The retained media set contains only video files that satisfy the configured
size policy and can be mapped to a confirmed media target.

- Subtitle, NFO, image, archive, checksum, and all other non-video files are
  cleanup candidates.
- Small video files remain cleanup candidates under `clean_policy`.
- A large video mapped to a confirmed item is moved to the formal library.
- A large video that cannot be mapped is moved to `media.unorganized_path`.
- No sidecar association or sidecar movement is implemented.

Classification must happen before formal-library mutations so the final user
message can distinguish retained, cleaned, unmatched, and failed files. Existing
115 cleanup remains allowed, but the renaming result must not describe an
entire package as moved to unorganized when some items already reached the
formal library.

## 4. Confirmed Plan and Actual File Mapping

`media_metadata.items` locks expected logical targets before download. It does
not claim that a selected Prowlarr release contains every expected item.

After download, mapping runs in three layers:

1. Deterministic rules bind explicit markers such as `S01E02`, `1x02`, and an
   exact unique source hint.
2. The existing download-time AI mapper receives only unresolved eligible
   videos plus the still-unresolved confirmed items.
3. A deterministic validator rejects invented sources, targets outside the
   confirmed item set, duplicate sources, and duplicate targets.

The resulting coverage has these sets:

- `mapped`: real sources bound to confirmed items.
- `missing`: confirmed items with no real source.
- `unexpected`: eligible large videos not bound to the plan.
- `ineligible`: videos below the active `clean_policy` threshold; these never
  enter either deterministic or AI mapping, even when an earlier deletion
  attempt failed.
- `rejected`: invalid AI bindings and their reasons.

Deterministic mapping never depends on AI configuration. Missing AI credentials
only disable the unresolved-file inference step. Canonical validation rejects
duplicate logical `(season_number, episode_number)` targets before indexes are
built.

The state is:

- `completed` when every confirmed item is mapped.
- `partial` when at least one item is mapped and at least one is missing or an
  eligible video is unexpected.
- `failed` when no confirmed item is mapped.

Only mapped items receive `source_relative_path` and `final_path`. Missing items
remain in the contract without a fabricated path. Plex consumes only resolved
items.

## 5. Rename Execution Ledger

The 115 move primitive is copy-then-delete and cannot provide a real package
transaction. The implementation therefore uses an explicit execution ledger
instead of pretending to roll back.

Before execution:

- validate all source paths;
- validate all target directories and names;
- reject duplicate targets;
- preflight every existing target conflict.

Each mapped episode is one execution unit. A unit records:

- `planned`;
- `renamed`;
- `moved`;
- `failed`;
- its source path, target path, and sanitized error.

The storage adapter exposes the copy and source-delete phases separately. A
successful copy followed by a failed source deletion is recorded as
`copied_source_retained`: the formal target is a successful canonical result,
the operation is partial, and the retained source is reported but is not sent
to unorganized. Cleanup failures for non-video and ineligible small-video files
are likewise explicit ledger entries.

Execution stops at the first operational failure. Successfully moved units stay
in the formal library. Remaining eligible videos are moved to unorganized when
possible. The renaming processor returns a terminal handled result so the
generic fallback cannot misreport the package.

The final Telegram message is emitted once and contains:

- overall state: completed, partial, or failed;
- formal-library success count and paths;
- missing confirmed targets;
- unorganized files/path;
- cleanup count;
- failed operation and reason.

The enriched canonical contract contains only successful final paths.

## 6. Chinese-Title Enrichment Boundary

Prowlarr continues to use the confirmed English query. Chinese-title enrichment
does not alter or rerun Prowlarr.

After Prowlarr has returned usable candidates, a missing Chinese title may be
backfilled from Douban or the existing AI fallback. The enrichment is applied
to both:

- outer naming metadata used by the renaming interface; and
- `metadata["media_metadata"]["identity"]["chinese_title"]`.

The helper preserves `metadata_id`, English title, relationship, placement,
items, source entry, and warnings. It records the title source under the
contract evidence without changing classification or episode locks.
Latin-only Douban results retain their subject ID, media type, English title,
and cover with an empty Chinese title; they remain eligible as enrichment
evidence without being mislabeled as Chinese.

## 7. Plex Module Fault Isolation

Base Plex management service construction must not construct local AI tool
schemas.

- Plex AI is initialized lazily from the `/plex` request inside a worker thread.
- `PlexToolDispatcher` may use its synchronous `asyncio.run()` bridge only in
  that worker thread, where no Telegram event loop is running.
- AI initialization failure marks only Plex AI as degraded and returns a clear
  `/plex` error.
- Base service construction, interrupted-job marking, AI, and MCP startup each
  have separate exception boundaries.
- MCP dependency import is inside the MCP boundary, so a missing or incompatible
  optional dependency cannot unwind bot startup.
- No Plex startup failure escapes the module startup hook into core bot
  shutdown.

Plex automatic management remains available when Plex AI is degraded. The bot
and other modules remain available when all Plex management startup fails.

## 8. Common Plex Target Pipeline

Renaming completion is expanded into resolved `PlexTarget` records:

- standalone movie: one target using the terminal movie file/folder result;
- standalone series: one target per resolved `items[].final_path`;
- official, inferred, or temporary Special: one resolved episode target.

Each target carries its own final path, library route, media type, season and
episode when applicable, and stable target identity.

All targets run the same baseline:

```text
route -> scan -> locate exact media -> match -> localize -> artwork -> streams
```

Movies are located by their final media path and identity. Episodes are located
by series identity, season, episode, and final media path. The ordinary series
path never relies on a new show rating key from `recentlyAdded()`.

## 9. Special Patches

Special behavior modifies only the required baseline steps:

- `tvdb_official`: verify the locked TVDB episode ID and preserve official text
  and artwork.
- `ai_inferred_tvdb`: require a TVDB identity after scan, preserve numbering,
  and report a recoverable failure when it remains unverifiable.
- `temporary_related_special`: locate the locked S00E100+ target and apply the
  confirmed custom title, summary, date, and poster.

Special logic does not replace ordinary movie/episode routing, scanning, path
location, or stream handling.

## 10. Plex Job Semantics

Each resolved target receives a stable idempotency key derived from
`metadata_id` and its target identity. A standalone movie uses a stable movie
target key.

Repository creation returns both the job and whether it was newly created.

- Only a newly created job is automatically submitted.
- A repeated completion returning completed, running, failed, waiting, or
  interrupted state is not automatically submitted again.
- On process startup, jobs left in queued or active execution states are marked
  `interrupted` with a restart reason.
- Startup does not automatically resume interrupted jobs.
- A user may inspect and explicitly retry an interrupted job through the
  existing Plex management surfaces.

This deliberately avoids leases and unbounded automatic retries. Human retry
is the recovery boundary approved for this phase.

## 11. Error Handling

- Invalid canonical metadata stops contract-bound renaming without legacy
  inference.
- No mapping result moves eligible large videos to unorganized and reports a
  failed mapping state.
- Partial mapping keeps successful formal files and reports missing items.
- Rename/move failure stops later formal operations and returns a terminal
  partial/failed result.
- Plex failure never rolls back renamed media.
- Plex AI or MCP failure never shuts down the Telegram bot.
- Duplicate completion never starts a second automatic run for the same target.

## 12. Deferred Audit Register

The following remain recorded but are not implemented here:

- provider/global-registry dependency leaks;
- module shutdown/drain lifecycle;
- AI/search enable-switch inconsistencies;
- old search callback/dead-path removal;
- multiple Prowlarr query fallback;
- pending-task TTL and owner fixes;
- Plex confirmation-token journaling and other P2/P3 recovery/UI issues;
- feature-branch composability and merge conflict cleanup.

## 13. TDD Acceptance Matrix

The implementation must add failing tests first for:

1. Canonical Chinese-title enrichment without changing the English Prowlarr
   query or placement.
2. Deterministic SxxEyy mapping before AI and AI use only for unresolved files.
3. Rejection of invented, duplicate, and out-of-contract AI mappings.
4. Completed, partial, and failed coverage.
5. Mid-batch move failure producing a terminal detailed result without generic
   fallback misreporting.
6. Successful items alone receiving final paths.
7. Plex AI initialization from an active Telegram event loop without nested
   `asyncio.run()` failure.
8. Plex startup failures remaining module-local.
9. Existing-show episode location by exact season/episode/final path.
10. Ordinary movie handling remaining unchanged by Special patches.
11. One Plex target per resolved ordinary episode.
12. Newly created jobs auto-submit once; repeated completions do not resubmit.
13. Rule-resolved packages complete without AI credentials.
14. Copy success plus source-delete failure is a partial formal success and is
    never duplicated into unorganized.
15. Small videos remain ineligible after cleanup failure and are reported.
16. Duplicate logical episode targets and MCP import failures are rejected or
    isolated at their module boundaries.
13. Active jobs becoming interrupted at startup and not auto-resuming.

Full main tests, tracked-Python compilation, `pip check`, YAML parsing, and
Telepiplex whitespace checks remain required before completion.
