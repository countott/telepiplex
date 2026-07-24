# Query and Candidate Resolution Design

Date: 2026-07-24

Status: approved for local implementation

Owner: `features/search`

## Goal

Make ordinary title, explicit season/episode, and standalone episode-title
queries survive noisy source APIs without weakening Telepiplex's
source-verification requirements.

This design supersedes the earlier rule that rejected a request solely because
more than seven qualified candidates remained.

## Confirmed behavior

### Work title plus explicit scope

Scope syntax is parsed before candidate matching:

```text
Rick and Morty S09E08
```

becomes:

```text
title = Rick and Morty
scope = episode
season_number = 9
episode_number = 8
```

Exact matching applies to the normalized base work title, not to the complete
raw query. Candidate confirmation keeps the parsed scope, and the later
Prowlarr query is built as `Rick and Morty S09E08`.

When an exact base-title candidate exists and the request also supplies an
explicit season/episode/whole-series scope, year, or media type, prefix
relatives such as spin-offs and similarly named works do not remain in the
same candidate pool. A bare work title without those constraints keeps its
title-family candidates so searches such as `杀死比尔` can still expose both
volumes for confirmation.

### Standalone episode title

The official TVDB v4 `/search` endpoint searches series, movies, people, and
companies; it does not provide an episode-title entity search. Telepiplex
therefore does not invent a direct TVDB episode lookup.

For a standalone episode title:

1. AI source orchestration may propose a parent-series search hypothesis.
2. The parent series must be verified by normal Wikipedia, Douban, and TVDB
   evidence rules.
3. TVDB episode inventory is fetched for the verified series.
4. Program code, not the model, exact-matches the raw query to an episode name
   in that inventory.
5. A unique match supplies the season and episode numbers.
6. No match fails with `tvdb_scope_not_verified`; multiple parent/episode
   matches fail with `ambiguous_candidates`.

The model may propose a query hypothesis but cannot invent a TVDB ID, episode
number, media contract, or Prowlarr query.

## Candidate stages

Candidate counts have distinct meanings:

```text
source facts
  -> raw candidate graph
  -> base-title match
  -> targeted evidence enrichment
  -> qualified candidates
  -> ordered confirmation candidates
```

Raw API result count is never a failure reason.

The independent-evidence gate remains unchanged:

- normal text candidates require at least two independent sources;
- media type and year must not conflict;
- series require a TVDB Series ID;
- direct metadata links remain authoritative single-source anchors.

Controlled expansion remains bounded to three candidates, but those candidates
are selected by deterministic query relevance rather than `candidate_key`
ordering. Exact title, requested year/type, shortest prefix expansion, and
existing provider support determine the order.

## Candidate count and AI scoring

The hard `MAX_DISPLAY_CANDIDATES = 7` failure is removed. The existing Telegram
candidate browser already displays one candidate at a time with previous/next
navigation, so eight qualified candidates are valid confirmation state.

AI scorecard parsing must preserve every score returned for the supplied
candidate list. It must not silently slice the response to seven entries.
AI scoring only orders already-qualified candidates and never removes one.

## Observability

Candidate-finalization logs distinguish:

- `raw`;
- `title_matched`;
- `qualified`;
- `rejected_single_source`;
- `rejected_missing_tvdb`;
- `rejected_missing_scope`;
- `rejected_media_type`;
- `rejected_year`;
- `rejected_title_policy`.

This prevents a raw `candidates=8` message from being mistaken for eight
qualified candidates.

## Error behavior

- Explicit episode scope missing from TVDB inventory:
  `tvdb_scope_not_verified`.
- Standalone episode title with no inventory match:
  `tvdb_scope_not_verified`.
- Standalone episode title matching multiple parent-series episodes:
  `ambiguous_candidates`.
- No candidate surviving the independent-source gate:
  `insufficient_independent_support`.
- Source/provider errors keep the structured error behavior from the
  thinking/tool compatibility change.

## Files

- `src/telepiplex_search/planner.py`
- `src/telepiplex_search/ai.py`
- `src/telepiplex_search/source_orchestrator.py`
- `src/telepiplex_search/service.py`
- `tests/test_ranked_planner.py`
- `tests/test_search_utils.py`
- `tests/test_search_ai_pipeline.py`
- `tests/test_source_orchestrator.py`

## Verification

Tests must prove:

- English and Chinese explicit episode queries preserve the base work title
  and season/episode scope;
- explicit season/episode queries fail if TVDB inventory cannot verify the
  requested range;
- exact base titles exclude prefix-noise candidates;
- bare title-family searches still retain legitimate numbered volumes;
- standalone episode titles resolve only through a verified parent series and
  exact TVDB inventory match;
- ambiguous or missing episode-title matches fail structurally;
- relevance ordering, not candidate-key ordering, selects controlled
  expansion targets;
- eight qualified candidates remain available for confirmation;
- AI scorecard responses are not truncated to seven;
- candidate-funnel logs report qualified and rejection counts;
- the complete Search Feature test suite still passes.
