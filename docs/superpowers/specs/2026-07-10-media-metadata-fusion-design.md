# Media Metadata Fusion Design

## Goal

Make media search produce one trustworthy candidate when Douban and TVDB describe the same movie or series. The merged candidate must carry a stable English Prowlarr query, correct media type, source IDs, and a usable cover into the download and renaming pipeline.

## Confirmed Problems

- TVDB search already returns translated names, aliases, and several image fields, but the adapter only reads `name` and `image`. A Korean primary name can therefore be mislabeled as `english_title`, while `image_url`, `poster`, and `thumbnail` are ignored.
- Douban rexxar metadata already returns `type`, `subtype`, `is_tv`, `cover_url`, and `pic`, but the parser discards them. A confirmed series can consequently default to `movie`, and a Chinese movie loses its poster.
- Douban and TVDB entries are appended as separate candidates. The first Douban entry is marked recommended even when the matching TVDB entry proves that the subject is a series.
- The existing cover backfill assigns one TVDB series cover to every candidate in a mixed candidate list and cannot return a movie cover.

## Source Responsibilities

Douban and TVDB remain primary peers. AI stays fallback-only when neither primary source can verify a result.

Field authority after a successful merge:

- Chinese title: Douban first.
- English title: confirmed Douban Latin title first, then TVDB English translation or Latin alias.
- Media type and episode data: TVDB first, then Douban `type/subtype/is_tv`.
- External IDs: union of Douban and TVDB IDs.
- Cover: TVDB first; Douban `cover_url`, `pic.large`, or `pic.normal` as fallback.
- Year: require equality when both sources provide a year; otherwise keep the available year.

## TVDB Adapter

Use the normal `/search` response before making more requests.

English title precedence:

1. `name_translated` when it contains Latin characters.
2. `translations.eng` when present.
3. A Latin alias from `aliases`, after removing terminal year/country qualifiers such as `(2022)` and `(KR)`.
4. `title` or `name` when it already contains Latin characters.
5. `/series/{id}/translations/eng` or `/movies/{id}/translations/eng` only when the search response has no Latin title.

Search both TVDB entity types when the input type is unknown. When Douban already provides a type, query only the matching TVDB type. Series results retain episode lookup; movie results do not perform episode calls.

Cover precedence inside TVDB:

1. `image_url`
2. `poster`
3. first non-empty item in `posters`
4. `thumbnail`
5. `image`
6. series artworks or movie extended record as a lazy fallback

## Douban Metadata

The Douban parser will preserve:

- `subject_id`
- `media_type` mapped from `type`, `subtype`, and `is_tv`
- `chinese_title`
- optional Latin `english_title`
- `year`
- `cover_url` from `cover_url`, `pic.large`, `pic.normal`, or `cover`

A Douban result with a Chinese title but no Latin title remains valid. TVDB may supply the English title during source fusion; AI is not invoked merely because Douban lacks English.

## Cross-Source Matching And Merge

Entries match only when:

- their known media types do not conflict;
- their known years do not conflict; and
- their normalized title sets intersect.

The title set includes `title`, `chinese_title`, `english_title`, and aliases. Normalization removes case, year-only suffixes, terminal TVDB country qualifiers, and punctuation while preserving meaningful letters and CJK characters.

Matching entries become one candidate. Non-matching movie and series results remain separate so genuine adaptations or same-name works are not collapsed.

The merged candidate is converted into fresh `naming_metadata` and search `metadata`; stale nested dictionaries cannot override merged titles, media type, IDs, scope, or cover.

## Candidate And Cover Feedback

Each candidate owns its own cover. No cover is copied across candidates.

The selected or auto-confirmed candidate sends one generic metadata card when it has a cover. The card supports both movies and series and is sent before the Prowlarr search begins. This replaces the series-only pre-confirmation card and prevents a movie candidate from displaying a series image.

## Renaming Contract

The download request must carry the merged values unchanged:

- `media_type`
- `chinese_title`
- `english_title`
- `year`
- `external_ids`
- `selected_scope`
- `cover_url`
- selected release title

The renaming module continues to own final path generation and TVDB episode mapping. This work does not change the current `中文名 (English Name)` folder grammar. It fixes the metadata entering renaming and adds regression coverage proving a merged series does not arrive as `movie`.

## Failure Handling

- TVDB translation or artwork fallback failures are logged and leave the primary search result usable.
- A missing cover never blocks search or download.
- If primary entries cannot be safely merged, confirmation continues to show separate choices.
- AI remains a retry into the same Douban plus TVDB verification chain, never a replacement authority.

## Verification

Regression coverage must include:

- TVDB Korean primary name with English alias resolves to `The Glory`.
- TVDB search image fields are recognized.
- Douban `黑暗荣耀` retains `series` type and poster.
- Douban and TVDB `黑暗荣耀 / The Glory / 2022` merge into one series candidate with TVDB ID and TVDB-first cover.
- A Chinese movie receives a Douban poster when TVDB has no image.
- Selected candidate metadata preserves the merged values for renaming.
- Current renaming folder grammar and episode mapping tests remain green.

