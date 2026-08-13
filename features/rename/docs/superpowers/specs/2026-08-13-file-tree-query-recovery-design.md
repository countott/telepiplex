# File-tree Query Recovery Design

## Goal

Make rename derive a bounded, explainable media query from the complete 115
file tree, recover only low-confidence long-tail names with AI, and keep
search as the sole authority that verifies external metadata and produces a
confirmed `media_metadata v1` contract.

## Boundaries

- Deterministic parsing owns release syntax, site and fansub prefixes,
  quality/hash noise, numeric titles, episode chains, and episode ranges.
- The probe never sends the complete file tree to AI or search. It sends a
  bounded set of identity candidates, representative paths, structural
  markers, and reason codes.
- High-confidence probes call `media.search.resolve_metadata` directly.
- Low-confidence probes may use rename's configured AI once. AI may select or
  clean only identities supported by the bounded evidence. Its output cannot
  introduce an unrelated title, year, season, or episode.
- Missing, failed, or invalid AI output leaves the item safely unresolved and
  performs no storage writes.
- Search continues from its normal root-work discovery path and validates the
  query through its external evidence providers. Rename does not call those
  providers directly and does not alter search internals.
- Confirmed `media_metadata v1` remains the only authority for naming,
  placement, and file mutation.

## Probe contract

`build_metadata_probe()` keeps its existing fields and adds:

- `identity_candidates`: ordered unique candidate strings supported by root,
  filename consensus, directories, or release title.
- `query_confidence`: `high`, `medium`, or `low`.
- `query_evidence`: bounded evidence records containing source, candidate, and
  representative relative path where applicable.
- `requires_recovery`: true only when deterministic evidence is not sufficient
  for a direct metadata lookup.
- `recovery_reasons`: stable reason codes such as `identity_conflict`,
  `numeric_title`, `unsupported_release_syntax`, and `missing_identity`.

## Recovery validation

The AI receives at most the root/release names, candidate list, content shape,
observed markers, video count, and a small deterministic sample of paths. A
response is accepted only when its normalized title matches or is contained
in one of the supplied candidate/evidence strings. Year must be empty or
already present in the evidence. Scope facts remain locked to the deterministic
probe. Accepted recovery replaces only `identity_query`, records AI recovery
evidence, and then enters the unchanged search metadata capability.

## Deterministic coverage

The parser adds support for:

- repeated ASCII/full-width fansub groups and common site prefixes;
- `Ep04`, anime `- 031 - title`, and Chinese bracketed absolute episodes;
- `S07E22E23`, `S03E01-06`, and `S02E09-E10` episode structures;
- numeric titles when the file tree carries independent episode/year/quality
  evidence, while still rejecting opaque numeric storage IDs.

## Failure behavior

Identity conflicts, missing identities, or rejected AI output must never reach
search with an empty or fabricated query. Rename reports metadata resolution
failure and leaves the original 115 item in place. Existing behavior for a
legitimate search result of `unresolved` remains unchanged.

## Verification

- Regression tests cover the pressure-test failures and the expanded probe
  contract.
- Integration tests prove high-confidence bypass, low-confidence AI recovery,
  invalid-AI safe blocking, and unchanged `resolve_metadata` handoff.
- Existing rename tests and the large 111/1,000/10,000-file tree stress check
  remain green and bounded.
