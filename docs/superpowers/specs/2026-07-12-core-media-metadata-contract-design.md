# Core Media Metadata Contract Design

**Date:** 2026-07-12

**Status:** Approved for implementation planning

**Scope:** Telepiplex core contract plus the media-search, renaming, and plex-management module boundaries

## 1. Goal

Define one neutral, JSON-serializable media metadata contract that is produced by `media-search`, optionally enriched by `renaming`, and consumed by `plex-management` without creating direct imports between those modules.

The contract is part of core. It is not a fourth runtime module and does not register commands, configuration sections, handlers, hooks, or background services.

The current local `main` remains the canonical composed runtime and continues to enable all stable modules by default. Module-disable combinations are outside this design's scope.

## 2. Decisions

1. The public key is `metadata["media_metadata"]`.
2. The old `metadata["download_plan"]` key is removed completely. There is no dual write and no backward-compatible read path.
3. The old `plan_id` field becomes the neutral `metadata_id`.
4. Search-only state such as Prowlarr queries, Telegram callback IDs, progress messages, and temporary result lists does not enter the core contract.
5. `media-search` is the only module that decides and confirms identity, relationship, category, target series, season, and episode placement.
6. `renaming` may bind downloaded files to confirmed items and append final file paths, but may not change the confirmed target series, category, season, or episode.
7. `plex-management` consumes the confirmed placement. It does not classify the item again.
8. Any movie that has a recognized series counterpart is managed under the series' `Season 00`, using the official TVDB Special entry whenever available.
9. A standalone movie library placement is allowed only when no series placement applies.
10. Temporary related Specials start at `S00E100` and require a findable source entry.

## 3. Architecture

```text
media-search
    produces and confirms media_metadata v1
        |
        v
core DownloadRequest / DownloadCompletedEvent
        |
        v
renaming
    executes locked placement and adds final file results
        |
        v
core DownloadPipelineCompletion
        |
        v
plex-management
    verifies official Specials or writes temporary Special metadata
```

All three feature modules depend only on core contracts and their own adapters. They do not import one another.

## 4. Core Ownership

Core owns a passive protocol implementation under `app/core/media_metadata.py`.

The core API provides:

- `MEDIA_METADATA_KEY = "media_metadata"`
- schema and enum constants
- `validate_media_metadata(value, require_confirmed=False)`
- `attach_media_metadata(metadata, value)`
- `extract_confirmed_media_metadata(metadata)`
- immutable/deep-copy boundaries
- category and library-type pair validation
- confirmed placement helpers such as a locked season/episode reader
- logical category route resolution from `category_folder[].kind`

Core does not:

- call AI or external metadata providers
- allocate `S00E100+`
- search Prowlarr
- rename or move files
- scan or write Plex
- infer a target series

## 5. `media_metadata v1` Schema

```json
{
  "schema_version": 1,
  "metadata_id": "stable-id",
  "confirmed": true,
  "identity": {
    "chinese_title": "想见你",
    "english_title": "Someday or One Day The Movie",
    "year": "2022",
    "content_kind": "extension_movie",
    "external_ids": {}
  },
  "relation": {
    "type": "sequel",
    "target_series": {
      "chinese_title": "想见你",
      "english_title": "Someday or One Day",
      "year": "2019",
      "external_ids": {
        "tvdb": "series-id"
      }
    },
    "source": "wikipedia"
  },
  "placement": {
    "library_type": "series",
    "category_kind": "live_action_series",
    "season_number": 0,
    "episode_number": 100,
    "mapping_kind": "temporary_related_special",
    "mapping_source": "local_allocator",
    "tvdb_episode_id": ""
  },
  "source_entry": {
    "title": "想见你 (电影)",
    "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
    "external_id": "",
    "provider": "wikipedia",
    "availability": "ok",
    "verification": "verified"
  },
  "items": [],
  "evidence": {},
  "warnings": []
}
```

All values must remain JSON-serializable so the contract can be stored in Plex management job payloads without conversion to feature-specific objects.

Unknown top-level fields are preserved by copy operations but are not interpreted until a newer `schema_version` defines them. Consumers reject unsupported schema versions rather than guessing.

## 6. Identity and Placement Are Separate

`identity.content_kind` describes what the source is. `placement` describes where the source is managed.

For example, a source can remain an `extension_movie` while its placement is `live_action_series / Season 00`. A movie identity never forces movie-library placement when an associated series exists.

Minimum `content_kind` values are:

- `movie`
- `series`
- `main_episode`
- `ova`
- `narrative_bonus`
- `non_narrative_extra`
- `special`
- `prequel_movie`
- `sequel_movie`
- `extension_movie`
- `spin_off`

Accepted `mapping_kind` values are:

- `tvdb_official`
- `ai_inferred_tvdb`
- `temporary_related_special`
- `standalone`

## 7. Four Logical Categories

The contract supports exactly four category pairs:

| `category_kind` | Required `library_type` | Display category |
|---|---|---|
| `live_action_series` | `series` | 真人剧集 |
| `live_action_movie` | `movie` | 真人电影 |
| `animated_movie` | `movie` | 动画电影 |
| `animated_series` | `series` | 动画剧集 |

Core rejects unknown category values and mismatched pairs.

OVA, bonus, Special, and extension movie are content kinds, not additional library categories. Their relationship and placement determine which of the four categories receives them.

Examples:

- animated OVA related to a series -> `animated_series / Season 00`
- standalone animated movie -> `animated_movie`
- live-action extension movie related to a series -> `live_action_series / Season 00`
- standalone live-action movie -> `live_action_movie`

## 8. Category Routing Configuration

`category_folder` receives a stable machine key. Human-readable names are no longer used as routing keys.

```yaml
category_folder:
  - kind: live_action_series
    name: 真人剧集
    path: /真人剧集
    plex_library_id: ""

  - kind: live_action_movie
    name: 真人电影
    path: /真人电影
    plex_library_id: ""

  - kind: animated_movie
    name: 动画电影
    path: /动画电影
    plex_library_id: ""

  - kind: animated_series
    name: 动画剧集
    path: /动画剧集
    plex_library_id: ""
```

`media_metadata.placement.category_kind` selects the logical route. The configured route supplies the physical download path and Plex library ID. This is automatic infrastructure routing, not a second Plex classification decision.

The hard-cut migration requires all four live `category_folder` entries to receive the correct `kind` value before the new runtime is deployed.

## 9. Series and Special Placement Policy

Placement priority is fixed:

1. If TVDB has an official Special linked to the target series, use its official season, episode, and TVDB episode ID.
2. If TVDB is unavailable and AI can identify the precise TVDB Special number, use `ai_inferred_tvdb` and include an explicit unverified warning.
3. If no usable TVDB episode exists but Wikipedia provides a strong narrative relationship and a findable source entry, allocate `temporary_related_special` at the first free number from `S00E100`.
4. Use standalone movie placement only when a series relationship does not apply.

An official or inferred series placement requires a populated `relation.target_series`. A `tvdb_official` placement additionally requires both `relation.target_series.external_ids.tvdb` and `placement.tvdb_episode_id`; without those identifiers it is not an official mapping.

A temporary related Special requires:

- `placement.library_type == "series"`
- `placement.season_number == 0`
- `placement.episode_number >= 100`
- a non-empty source-entry title
- a source-entry URL or external ID

The user confirms the complete placement once before Prowlarr. No downstream consumer may silently alter it.

## 10. Multi-File Items

`items` represents confirmed logical content inside a multi-file release. Each item may contain:

```json
{
  "item_id": "stable-item-id",
  "content_role": "ova",
  "season_number": 0,
  "episode_number": 3,
  "source_hint": "",
  "final_path": ""
}
```

Before download, `source_hint` and `final_path` may be empty. After download, renaming may bind an actual source file and append its final path. It may only use season and episode targets already permitted by the confirmed top-level placement or confirmed `items` entries.

Files that cannot be mapped reliably are moved to the configured unorganized path. Duplicate targets, occupied targets, and invented source files are rejected.

## 11. Module Responsibilities

### 11.1 media-search

- runs the two mandatory AI stages
- collects soft-failing Wikipedia, Douban, and TVDB evidence
- keeps search-only state local
- allocates temporary Special numbers
- presents exactly one complete confirmation
- converts the confirmed draft into `media_metadata v1`
- validates and attaches the contract before dispatch

Invalid contracts never reach Prowlarr download dispatch.

### 11.2 renaming

- reads only confirmed `media_metadata`
- treats target series, category, season, and episode as locks
- maps actual files to confirmed item targets
- uses official TVDB Special numbering when present
- uses confirmed temporary numbering without TVDB IDs
- appends final file paths to `items`
- returns the updated contract through `PostDownloadResult.metadata`

Renaming does not import media-search or plex-management.

### 11.3 plex-management

- reads the same contract from `DownloadPipelineCompletion`
- routes to the library selected by `placement.category_kind`
- does not decide movie versus series again
- does not change season or episode numbers

For `tvdb_official`, Plex scans the target series library and verifies the official series/Special identity without writing custom metadata.

For `ai_inferred_tvdb`, Plex scans the locked Season 00 number and attempts to verify it against the now-visible Plex/TVDB identity. If verification succeeds, it leaves the official metadata untouched. If verification is still unavailable or conflicts, it preserves the confirmed file placement, writes no custom metadata, and records a recoverable warning/failure; it does not renumber the file or silently convert the mapping into a temporary Special.

For `temporary_related_special`, Plex locates the exact Season 00 episode using the confirmed target series, episode number, and renamed final path. It writes the supported custom title, summary, original availability/year, and artwork fields from `identity`, `relation`, and `source_entry`. The source locator remains in the persisted Plex job payload and audit logs; the design does not invent a Plex metadata field for the URL.

Contract-bound Plex jobs do not ask the user to choose a movie or series classification. Failure to locate the exact target enters the existing Plex job failure/retry path.

## 12. Data Flow

1. media-search keeps the AI draft and Prowlarr queries in ephemeral search state.
2. The user confirms one complete plan.
3. media-search freezes and attaches `metadata["media_metadata"]` to `DownloadRequest`.
4. The download provider copies request metadata unchanged into `DownloadCompletedEvent`.
5. renaming extracts and validates the confirmed contract.
6. renaming performs locked file operations and adds final file results.
7. core updates the event metadata from `PostDownloadResult.metadata`.
8. `DownloadPipelineCompletion` carries the enriched contract to completion hooks.
9. plex-management persists the full contract in its job payload and performs official verification or temporary metadata writing.

The same `metadata_id` is preserved through all stages for logs and job correlation.

## 13. Failure Rules

- Mandatory AI unavailable before search -> stop before Prowlarr.
- Wikipedia, Douban, or TVDB unavailable -> continue planning with explicit source statuses and warnings.
- Invalid or unconfirmed contract -> media-search does not dispatch it.
- Unsupported schema downstream -> log and stop contract-bound processing without guessing.
- Renaming cannot map any confirmed item -> move the intact source directory to unorganized storage.
- Some files remain unmatched -> move only unmatched files to unorganized storage.
- Target episode already exists -> leave source files in place and report a conflict; never increment silently.
- Plex unavailable or Plex job fails -> keep renamed files unchanged and use the Plex job retry/recovery mechanism.
- Download without `media_metadata` -> retain the pre-existing generic renaming and Plex behavior.

Temporary number reservations remain process-local before dispatch. Once dispatched, the confirmed number is carried by the contract and persisted with the downstream Plex job payload.

## 14. Branch Ownership

The current `main` is the source of truth for the composed runtime. Changes are ported by ownership; `main` is not merged wholesale into a module branch.

### `feature/telepiplex-core`

- core runtime
- `media_metadata v1` schema and helpers
- category route lookup
- no business modules

### `feature/media-search`

- media-search module and producer code
- Wikipedia, Douban, TVDB, AI, and Prowlarr search dependencies
- no renaming or Plex implementation

### `feature/renaming`

- renaming module and consumer code
- no media-search or Plex implementation

### `feature/plex-management`

- Plex management module and consumer code
- no media-search or renaming implementation

### `main`

- composed runtime with all stable modules enabled by default
- cross-module integration tests

The current plex-management feature branch contains unrelated module files and must be returned to a Plex-only surface as part of the branch-alignment work. No remote branch history is rewritten without explicit approval during implementation handoff.

## 15. Verification Matrix

### Core branch

- schema v1 validation
- hard rejection of `download_plan`
- four category/library pair tests
- temporary source locator requirements
- JSON round-trip
- deep-copy and immutability boundaries
- request/event/completion metadata preservation

### media-search branch

- generates and confirms `media_metadata`
- performs the hard cut from `download_plan`
- resolves all four category kinds through configuration
- contains no renaming or Plex imports/files
- passes module surface tests

### renaming branch

- consumes a core fixture without media-search installed
- official S00 mapping
- inferred TVDB warning preservation
- temporary S00E100 mapping
- multi-file and partial mapping
- target conflict and unorganized fallback
- contains no media-search or Plex imports/files

### plex-management branch

- consumes a completion fixture without media-search or renaming imports
- official Special verification does not write overrides
- inferred TVDB placement is verified without renumbering or silent conversion
- temporary Special writes custom metadata
- placement is never reclassified
- failed jobs retain file placement and recover through the repository
- contains no unrelated business modules

### main

- one end-to-end test preserves one `metadata_id` from media-search through renaming to Plex
- active code, tests, and templates contain no read, write, or fixture use of `download_plan`; migration documentation may name the removed key explicitly
- complete unittest and pytest suites
- tracked Python compilation
- dependency consistency
- Telepiplex whitespace check
- branch-specific local/remote comparisons before publication

## 16. Out of Scope

- supporting arbitrary module-disable combinations in this iteration
- retaining or migrating persisted `download_plan` payloads
- adding a metadata database outside the existing Plex job repository
- changing the user's four logical media categories
- allowing Plex to override confirmed classification or numbering
- redesigning unrelated Plex management commands, MCP tools, or AI actions

## 17. Success Criteria

The work is complete when:

1. core owns one neutral `media_metadata v1` contract;
2. active code contains no `download_plan` compatibility path;
3. all four categories route automatically by stable `kind`;
4. any recognized series-related movie is managed under the target series' Season 00;
5. official TVDB Specials use official metadata and numbering;
6. temporary S00E100+ Specials receive Plex custom metadata from their source entry;
7. media-search, renaming, and plex-management have no direct imports between one another;
8. every Feature branch passes its own isolated surface and behavior tests; and
9. the composed `main` passes the full end-to-end pipeline test.
