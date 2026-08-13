# Wikipedia-first Deterministic Search Design

**Date:** 2026-08-12  
**Status:** Approved for implementation  
**Scope:** search Feature plus compatibility verification for Download, Rename, and Sync

## Goal

Replace search's natural-language and candidate-decision AI with a deterministic
Wikipedia/Wikidata discovery pipeline. Keep exact metadata links, enrich a frozen
identity from multiple sources, construct series scope from TVDB then TMDB then
explicit Wikipedia structure, and preserve the confirmed `media_metadata v1`
contract through Download, Rename, and Plex management.

## Input contract

Search accepts an explicit work title with optional year, media type, season, or
episode. It does not interpret descriptive natural language. Unsupported text is
rejected with a prompt to provide a title, for example `副总统 2012` or
`副总统 第一季`.

Supported metadata links remain first-class inputs:

- Douban subject links;
- Wikipedia and `w.wiki` article links;
- TMDB movie and TV links;
- TVDB movie, series, season, and episode links;
- AniList anime links.

An exact link is resolved to its stable provider identity before discovery. A
Wikipedia disambiguation page is not an exact work identity. A resolvable share
link may fall back to deterministic title discovery only when it yields a clear
page title but no stable entity link.

## Identity discovery

Plain-title discovery uses Chinese Wikipedia first and English Wikipedia as a
fixed fallback. The adapter must preserve MediaWiki search rank, detect
`pageprops.disambiguation` by key presence, retain page IDs and Wikidata QIDs,
and use Wikidata `P31` structural types instead of extract keywords to decide
whether a page is a movie or series.

For a disambiguation page, the program may read article-namespace links in one
bounded request and run the same structural filtering over those linked pages.
It removes people, novels, games, lists, episode lists, season-only pages, and
specials. Remaining works are deduplicated by QID and ranked by exact title,
explicit year/type agreement, MediaWiki rank, and language preference.

Ambiguity is resolved by the user. Results show a simple-Chinese title when
verified, otherwise English, followed by year, country/region, and movie/series.
The program and AI never choose between two still-valid same-title works.

## Frozen identity and enrichment

Selecting a root work or resolving an exact link creates an immutable identity
lock containing anchor provider, stable ID, root title, year, and media type.
Supplemental providers can add fields but cannot replace this identity.

After the lock, search performs multi-source enrichment:

1. Wikipedia/Wikidata: root identity, titles, aliases, year, type, countries,
   summary, and QID.
2. TMDB: external IDs, artwork, release data, runtime, genres, countries,
   studios, networks, cast, crew, certifications, and aggregate season counts.
3. TVDB for series: root series ID and authoritative default episode inventory.
4. Douban: exact-link anchor, or unique identity-matched simple-Chinese title
   and poster fallback. Douban never supplies season/episode structure.
5. AniList only for a confirmed Japanese animation: romaji title and AniList ID.
   It does not provide poster, season count, episode count, relations, or scope.

Display title order is verified simple Chinese, Wikipedia zh-cn/zh-hans,
Wikidata zh-cn/zh-hans, unique Douban Chinese, then English. There is no AI
translation.

## Series scope catalog

Series structure is assembled in this order:

1. TVDB default episode inventory, excluding season zero;
2. TMDB season summaries and per-season episode inventory;
3. explicit Wikipedia/Wikidata season count, season pages, or episode-list
   structure;
4. no invented structure.

If only a trustworthy season count exists, the UI may show season choices but
must not show episode choices. If no trustworthy structure exists, it offers
only whole series. Specials, season zero, OVA, ONA, music, PV, and dedicated
special episodes are excluded. A normally numbered episode in a regular season
remains eligible.

For an unscoped series, the second menu contains whole series, known seasons,
and back. Choosing a season searches that season. If the original input already
specified a season, the second menu contains the whole season, known numbered
episodes, and back. A verified one-season series shows only `全剧（共 1 季）`.

## Confirmed metadata contract

After identity enrichment and scope selection, search confirms one
`media_metadata v1` contract. It retains:

- identity titles, aliases, dates, language, countries, genres, summary,
  artwork, companies, people, certifications, counts, and external IDs;
- retrieval media type, scope, and every final release query;
- placement library/category and selected season/episode;
- numbered inventory items;
- per-field sources, provider statuses, frozen source links, warnings, and a
  deterministic decision record.

Search-specific `evidence.ai` and `mode=ai_fact_binding` are removed. The public
identity, retrieval, placement, items, and evidence shapes remain compatible
with downstream Features.

## Release search and presentation

Before release lookup, Host delivery must accept a poster identity card, local
placeholder, or text fallback. Artwork priority is TMDB, Douban, Wikipedia,
placeholder, text. AniList artwork is not used.

The user sees the final executed release queries without a backend product name.
Examples are `Veep S01`, `Veep Season 01`, and `Veep S01E02`. Semantic title
punctuation such as `3%` is preserved.

The release gate checks verified aliases, movie year, media type, requested
scope, specials, URL validity, and duplicates before quality scoring.

## Downstream handoff

Search sends the confirmed `media_metadata` and lightweight `naming_metadata`
with the selected release to Download. Download preserves them unchanged in
`download.completed`. Rename validates the confirmed contract, uses identity,
placement, and items to map and name files, then writes resolved source/final
paths back into the metadata items. `media.organized` carries the updated
contract to Sync/Plex, which uses it for library routing, matching, artwork, and
scan targets.

When Rename receives a download without metadata, it builds a bounded title and
file-tree probe and calls search's deterministic `resolve_metadata`. A unique
result continues; ambiguity pauses for user confirmation. Filename inference
may fill empty fields only and cannot overwrite confirmed identity.

Rename's separately configured constrained file-to-episode AI is outside this
change. Removing search AI must not alter that execution-layer capability.

## Reliability

Wikipedia requests use a contact-bearing User-Agent, bounded request count,
maximum concurrency of three, caching, `Retry-After`, and a rate-limit circuit
breaker. Search/disambiguation fan-out is batched. Cache TTL is 15 minutes for
interactive sessions and 24 hours for page/QID and enriched metadata records.

Source failures degrade individual fields or scope detail, not the frozen work.
Identity conflicts fail closed. Invalid confirmed metadata leaves files in
place. Direct-link failures offer retry and never silently select a different
work.

## Version and verification

The search Feature version changes from `1.8.1` to `1.9.0`; `manifest.yaml`,
`pyproject.toml`, and checked-in package metadata remain aligned. Host version
does not change unless a public Host contract changes.

Verification covers Wikipedia ordering/disambiguation/type filtering, exact
links, title fallback, metadata enrichment, TVDB/TMDB/Wikipedia scope priority,
special exclusion, query punctuation, poster delivery ordering, and the full
Search to Download to Rename to Sync metadata handoff.

