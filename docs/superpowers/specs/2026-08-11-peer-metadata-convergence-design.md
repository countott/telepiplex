# Peer Metadata Convergence Design

**Date:** 2026-08-11

## Goal

Make Douban, Wikipedia, TVDB, and TMDB peer evidence providers after a user has selected a work, use AniList only to enrich confirmed Japanese animation titles, and freeze one `media_metadata v1` contract that is reused by Prowlarr query generation, release validation, renaming, and Plex processing.

## Boundaries

- Ordinary text discovery remains Douban-first. Adding TMDB must not change the first candidate pool.
- Provider facts have equal eligibility. There is no provider-wide confidence score and no last-writer-wins merge.
- Provider-specific capabilities remain specific: TVDB owns verified episode inventory; AniList supplies Japanese animation romaji; TMDB supplies cross IDs and descriptive metadata.
- A provider outage is recorded but does not invalidate facts from other providers.
- An identity conflict is never silently resolved by majority vote.
- The frozen confirmed contract is the only downstream metadata input. Rename and sync must not independently infer a different identity.
- Mac-local development performs no Git operation and publishes nothing.

## Provider Flow

1. The user selects a Douban candidate and freezes the anchor subject ID, title, year, and media type.
2. Search requests Wikipedia, TVDB when applicable, and TMDB enrichment for that confirmed identity.
3. TMDB prefers exact external-ID bindings from Wikidata, IMDb, or TVDB. A title search is accepted only when title, year, and media type produce one unambiguous entity.
4. Every accepted result is stored as an independent `EvidenceFact` with stable source URL and external IDs.
5. Facts converge only when they share an external ID or have an exact normalized title, year, and media type match.
6. If the converged work is Japanese animation, search queries AniList. AniList results must match native/original title or official English title plus year and media type before their romaji is accepted.
7. Search exact-reads every frozen source link again before confirmation. A non-anchor provider that becomes unreadable or conflicts is quarantined and recorded; the Douban anchor remains mandatory.
8. Search builds and freezes the confirmed `media_metadata v1` contract.

## Source-Neutral Field Convergence

Each canonical field records the facts that supplied it. Values are grouped by safe normalization and resolved deterministically:

- If multiple providers supply equivalent values, preserve one display spelling and record all supporting facts.
- A single exact-bound provider value is valid; English query titles do not require a second source.
- Conflicting non-equivalent values remain in evidence and produce a diagnostic. They cannot silently overwrite the selected identity.
- Multi-valued descriptive fields such as aliases, genres, countries, studios, networks, cast, and crew are stable unions.

Provider-specific semantics do not constitute provider ranking:

- Douban is normally the source of the selected simplified-Chinese display title.
- TVDB episode inventory is the only source allowed to verify season and episode coordinates.
- TMDB contributes `tmdb`, IMDb, Wikidata, and TVDB cross IDs, localized titles, release dates, runtime, status, production entities, credits, certification, artwork, and aggregate season/episode counts.
- Wikipedia contributes Wikidata identity, localized titles, and explanatory summary.
- AniList contributes `native`, `romaji`, and official English anime titles only after Japanese-animation identity is confirmed.

## Query Plan

`identity.query_titles` is an ordered, deduplicated set derived from frozen facts.

- Japanese animation: verified AniList romaji, verified official English titles, then other verified Latin aliases.
- Other works: the canonical official English title followed by other exact-bound official English titles.
- Locally generated kana romanization is removed.
- Each query uses the same confirmed retrieval scope and season/episode coordinates.
- Movies append the confirmed release year.
- Query execution is bounded to three title variants.
- Release validation uses the same complete alias/title set as query generation, so retrieval and validation cannot disagree about identity.

## `media_metadata v1` Additions

The schema version remains `1` because all additions are optional and existing consumers already permit additional identity/evidence fields.

`identity` gains:

- `query_titles`
- `genres`
- `original_release_date`
- `runtime_minutes`
- `status`
- `studios`
- `networks`
- `cast`
- `crew`
- `certifications`
- `backdrop_urls`
- `season_count`
- `episode_count`

`external_ids` can contain `douban_subject`, `wikipedia`, `wikidata`, `tvdb`, `tmdb`, `imdb`, and `anilist`.

`evidence.field_resolutions` records, per field, the selected value, all source values, supporting fact IDs, and whether different normalized values remain.

## Downstream Behavior

### Prowlarr

Search executes up to three frozen queries. The release gate receives all frozen aliases and query titles. Prowlarr results never mutate identity facts.

### Rename

Rename continues to use `identity.chinese_title` and `identity.english_title` from the confirmed contract. It does not call metadata providers or recompute titles after download.

### Plex/sync

Sync continues locating items by final path. It then uses frozen external IDs for artwork and metadata checks. `identity.original_language` is used first for audio selection; live TMDB details are only a fallback when the confirmed contract lacks original language. The existing live TMDB image request remains available because textless poster candidates are operational data obtained at Plex-processing time.

## Configuration

Search adds optional sections:

```yaml
metadata:
  tmdb:
    enable: true
    api_key: ""
    base_url: https://api.themoviedb.org/3
    timeout: 15
  anilist:
    enable: true
    endpoint: https://graphql.anilist.co
    timeout: 15
```

`metadata.tmdb.api_key` stores the TMDB API Read Access Token and is marked `writeOnly`. AniList uses its public GraphQL API and has no credential field. The search config wizard exposes TMDB enable/key settings; AniList remains an internal YAML option because it has no secret and only runs conditionally.

Sync retains its existing TMDB credential because it may request live textless-poster candidates after Plex has scanned the media. Both fields accept the same API Read Access Token.

## Failure Handling and Observability

Provider states use `ok`, `disabled`, `credential_missing`, `authentication_failed`, `not_found`, `not_unique`, `timeout`, `rate_limited`, and `server_down` where applicable.

Logs record provider start/completion, lookup mode (`external_id` or `title_year_type`), stable IDs, fact counts, convergence conflicts, selected query variants, and downstream use of frozen versus live metadata. Credentials, authorization headers, full provider payloads, and magnet URLs are never logged.

## Versioning

- search: `1.7.1` to `1.8.0`
- sync: `1.0.3` to `1.1.0`
- rename: unchanged because it already consumes the frozen contract without provider calls
- SDK schema version: unchanged at `media_metadata v1`

## Validation

- Adapter unit tests cover TMDB authentication, search/detail normalization, exact ID binding, AniList matching, and failure statuses.
- Input and direct-link tests cover TMDB and AniList stable URLs.
- Convergence tests cover shared IDs, title/year/type binding, provider conflicts, field provenance, and Japanese title policy.
- Query tests cover multiple bounded variants and removal of local kana romanization.
- Service tests cover selected-candidate enrichment, provider degradation, and frozen exact reads.
- Sync tests prove frozen original language avoids a live TMDB details call and missing language retains the live fallback.
- Feature config, manifest, project version, README, and local package-build tests are updated.

