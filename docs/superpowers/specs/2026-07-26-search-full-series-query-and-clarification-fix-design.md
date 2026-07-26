# Search Full-Series Query and Clarification Fix Design

## Goal

Fix the observed `想见你` search flow without weakening identity or scope
gates:

- a verified 2019 series must remain visible even when many prefixed movie
  matches exist;
- `AAC.2CH` must display `2.0·有损`;
- `media_type=series` must use the configured TV category;
- a verified one-season whole-series search must query the common pack naming
  variants in parallel instead of sending only a bare title.

## Approved behavior

### Source-backed clarification

The planner still requires a verified movie/series title-family relationship
before asking the user to choose a media type. Prefix matches are retrieval
candidates only; unrelated works such as a title beginning with the requested
title are not clarification options merely because of that prefix.

The option list is deduplicated and reserves capacity for both media types.
When both sides are verified, the first movie and first series option are kept
before remaining candidates are added up to the six-option limit. Existing
stable-identity locks and simplified/traditional display behavior remain
unchanged.

### Audio label

An explicit `2CH` token is normalized to `2.0`. AAC alone does not imply a
channel layout, so titles without an explicit channel token remain unchanged.

### Prowlarr media category

The public media type remains `series`, while Prowlarr category lookup maps it
to the configured `search.prowlarr.categories.tv` value. A legacy explicit
`categories.series` value is accepted only as a fallback; the documented `tv`
key has precedence.

### One-season whole-series queries

After TVDB-backed scope confirmation, a whole-series contract whose verified
inventory contains exactly season 1 generates three independent queries:

1. `<English title> S01`
2. `<English title> Season 01`
3. `<English title> Complete`

The bare title is not used for this case. Every `(indexer, query)` pair is
searched independently with bounded concurrency. Results are merged,
deduplicated, scope-gated, and ranked as one set.

A failed query variant does not mark an indexer down when any other variant for
that indexer completes successfully, including a successful empty result. An
indexer is reported down only when all of its variants fail. Aggregate search
fallback follows the same partial-failure rule.

## Observability

Each indexer/variant completion logs its query and raw result count; failures
log the same dimensions with structured error data. Every merged result update
logs the active query set, raw count, deduplicated count, eligible count, and
gate rejection counts.

## Verification

Automated coverage must prove:

- six or more prefixed movie candidates cannot crowd out the verified series
  option, and unrelated prefixes do not become options;
- `WEB-DL.x264.AAC.2CH` renders `2.0·有损`;
- `media_type=series` sends `categories.tv`;
- a one-season whole-series contract emits exactly the three approved queries,
  searches the query/indexer Cartesian product concurrently, merges duplicate
  releases, and only marks all-variant failures down;
- movie, season, episode, incremental search, cancellation, and release
  selection behavior remains intact.

