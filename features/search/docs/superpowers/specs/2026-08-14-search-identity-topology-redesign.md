# Search Identity and Series Topology Redesign

**Status:** approved for implementation  
**Target:** search 1.11.0  
**Date:** 2026-08-14

## 1. Problem Statement

search 1.10.0 treats source search results, verified media identities, localized
titles, and provider episode orders as if they were interchangeable. That
causes five apparently separate failures with one common cause: unverified
facts are allowed to become canonical before the identity graph is complete.

- A partial title match such as `贼王` can suppress Wikidata relationship
  expansion for `海贼王`.
- Wikipedia candidates are rendered before an exact Wikidata-to-Douban link is
  hydrated, so `百年孤寂` appears even though the verified Simplified Chinese
  title is `百年孤独`.
- Wikipedia and Wikidata are used as fallback alternatives instead of a union,
  so valid editions such as the 1950 `男儿本色` disappear.
- TVDB and TMDB season coordinates are intersected even when they describe
  different order schemes, which turns hundreds of `死神` episodes into 41.
- A Douban season link is flattened into a root-work link before metadata v1,
  so the requested season is lost and a foreign work can reach Prowlarr with a
  Chinese query.

The redesign establishes a single evidence flow:

`recall seeds -> verified identity graph -> localized candidate -> requested
scope -> selected series topology -> Prowlarr query`.

No stage may infer the next stage from a substring, intersect incompatible
coordinates, or discard a verified relationship.

## 2. Invariants

1. Wikipedia remains the primary recall and exact-page source. Wikidata search
   and exact entities are always unioned with it; neither source is merely a
   fallback for the other.
2. A search hit is selectable only when its normalized title exactly matches an
   input/alias, or it is reached through a verified Wikidata relationship from
   an exact seed. A substring may generate another search query but is never
   identity evidence.
3. Recall never stops because one weak candidate exists. Relationship expansion
   is bounded by depth and node count, deduplicated by QID, and records its
   provenance.
4. Wikidata property P4529 is an exact Douban subject binding. That subject is
   read before the candidate is displayed; a verified Douban root title is the
   Simplified Chinese title used both before and after selection.
5. A direct season link remains a season request. The seasonal entity is
   normalized to a root identity before metadata v1, while its season number and
   source relationship remain attached to the candidate.
6. Foreign works require a verified Latin-script original/English search title
   before release search. search fails closed instead of sending only a Chinese
   Prowlarr query.
7. Episode coordinates from different order schemes are complete profiles. They
   are never set-intersected. Wikipedia is authoritative when its verified
   episode table or directly linked episode-list page is usable; otherwise one
   compatible TVDB/TMDB profile is selected as a whole.
8. An unresolved provider-order conflict is observable and blocks a misleading
   season menu. It must not silently create a smaller inventory.
9. Candidate limits affect presentation only. Discovery keeps the bounded
   verified set and the UI paginates it without dropping candidates.

## 3. Root-Work Discovery Graph

### 3.1 Seeds

The discovery coordinator requests the existing bounded Wikipedia queries and
Wikidata `wbsearchentities` query independently. It normalizes every returned
QID and performs one batched Wikidata entity lookup.

Seeds are classified as:

- `exact_title`: an input title exactly equals a normalized entity label,
  alias, Wikipedia canonical title, redirect title, or lead title alias;
- `weak_recall`: search returned the entity but the title is only partial;
- `verified_relation`: an entity was reached from an exact seed through a
  Wikidata structural property.

Only `exact_title` and `verified_relation` can become selectable media.
`weak_recall` is logged and may contribute an alternate search term, but cannot
suppress other sources or relationship expansion.

### 3.2 Relationship Expansion

From exact seeds, the coordinator follows a bounded breadth-first graph through
`adaptation_ids` and `part_ids`. The maximum depth is two and the maximum entity
budget is 60 QIDs. This covers a title/franchise hub, its film-series hub, and
the individual films without unbounded franchise traversal.

Every edge records `from_qid`, `to_qid`, property, and depth. A related entity is
selectable only when it is a movie or series and satisfies any explicit media
type/year/scope constraints. A relation-reached title does not need to repeat
the input string because the edge itself is the evidence.

Wikipedia-backed and Wikidata-only media are converted to the same root shape,
deduplicated by QID, and sorted by exactness, relation depth, source rank, year,
and stable ID. At most 40 verified roots are retained. Candidate pages contain
five items and preserve the full frozen list.

## 4. Candidate Localization

Wikidata normalization exposes P4529 as
`external_ids.douban_subject`. Before rendering a candidate, search performs an
exact lookup of that subject ID. The fact is accepted only if the returned
subject ID is identical and its media type is compatible. Shared IMDb remains
additional evidence but no IMDb API or key is required.

For a verified series-season Douban title, the conservative existing season
suffix cleaner produces the root title and retains `season_number`. The
candidate stores:

- the exact Douban source link;
- the raw Douban title;
- the selected Simplified Chinese root title;
- `douban_match_mode=wikidata_exact`;
- field-source provenance.

Candidates without an exact P4529 binding are not fuzzily localized before
selection. The existing bounded fuzzy lookup remains available only after the
user selects an identity.

## 5. Direct-Link Normalization

A direct Douban link is first resolved as the exact source entity. If its title
contains a verified season suffix, the resolver returns `scope=season`, retains
the integer season number, strips the suffix from the root Chinese and English
titles, and excludes the season premiere year from root-work discovery.

Root identity supplementation then uses the English/original root title. Exact
Wikipedia, Wikidata, TVDB, or TMDB evidence must bind the seasonal source entity
to that root. When a provider inventory proves the requested season exists, its
source link is bound with `role=season` and the corresponding inventory
verification. Metadata v1 therefore receives both the root and requested-scope
evidence instead of a flattened work.

For non-Chinese foreign works, release query construction requires a verified
Latin-script root title. The resulting query for the example is
`House of the Dragon S03`; a Chinese-only fallback is rejected with a structured
metadata error.

## 6. SeriesTopology

`SeriesTopology` is a normalized view over complete provider order profiles.
Each profile contains provider, order key, all episode coordinates, dates,
known totals, stable episode IDs, and diagnostic status.

Selection rules are:

1. Use a verified Wikipedia episode table from the root page.
2. If the root page explicitly links an episode-list page, resolve that exact
   link and use its table while preserving the root QID relationship.
3. If Wikipedia is absent or unavailable, compare whole TVDB and TMDB profiles.
4. If their coordinate sets agree, merge metadata by coordinate.
5. If they differ, score whole profiles using verified episode/season totals,
   aired coverage, nonzero coordinates, and requested-scope compatibility.
6. Select only a unique best profile. A tie or incompatible result is
   `provider_order_conflict` and produces no season menu.

Downstream provider IDs may enrich the selected profile at matching coordinates
but may not remove or renumber its episodes. All inventory diagnostics include
the selected provider/order, rejected profile counts, conflict reason, and
whether Wikipedia followed an exact episode-list link.

## 7. Errors and Observability

The machine log records one structured summary per stage:

- discovery seed counts and rejection reasons;
- relationship edges visited and the entity budget;
- candidate localization source and match mode;
- direct-link source scope and normalized root scope;
- available topology profiles, selected profile/order, and score reasons;
- Prowlarr query language/title source.

New stable reason codes are `weak_title_rejected`, `relation_budget_exhausted`,
`douban_exact_binding_failed`, `provider_order_conflict`, and
`foreign_search_title_missing`.

## 8. Acceptance Tests

All tests use fixed provider facts rather than live network calls.

1. `死神`: divergent full TVDB/TMDB profiles can never produce their
   coordinate intersection; selection returns one complete profile or an
   explicit conflict, and never the 41-episode menu.
2. `百年孤独`: Q124175370 with P4529/30482958 renders `百年孤独` on the
   candidate screen before selection and retains it in metadata v1.
3. `海贼王`: partial `贼王` is rejected; exact franchise/title seeds expand to
   the anime and verified film entities, with more than five candidates still
   reachable through UI pagination.
4. `男儿本色`: Wikipedia results and Wikidata search are unioned, including the
   1950 film instead of limiting output to the three Wikipedia pages.
5. A direct Douban season-three link resolves the root as
   `House of the Dragon`, preserves season 3, and emits
   `House of the Dragon S03` to Prowlarr.
6. Existing exact-link, same-title ambiguity, ongoing-season, release-gate, and
   config migration tests remain green.

