# Plex Management Module Design

Date: 2026-07-11

Branch: `feature/plex-management`

Base: local `main` at `8fcf48b`

## Goal

Integrate the useful Plex management code from
[`vladimir-tutin/plex-mcp-server`](https://github.com/vladimir-tutin/plex-mcp-server)
directly into Telepiplex as a native `app.modules.plex_management` module. The
module must run after the existing renaming pipeline, expose a standard MCP
server, and reuse the existing AI configuration for an optional `/plex`
tool-calling interface.

The first release is intentionally limited to:

1. Triggering the correct Plex library scan after a successful rename.
2. Locating the newly scanned Plex item.
3. Verifying or repairing its metadata match.
4. Refreshing and verifying Simplified Chinese metadata.
5. Selecting a textless poster without using vision-model tokens.
6. Selecting the highest-quality original-language audio track.
7. Selecting a `chi` subtitle, preferring an external subtitle.

The module does not delete media, empty trash, optimize the Plex database,
manage Plex users, control playback clients, or download subtitles.

## Source integration and licensing

Use a selective vendoring approach rather than copying the entire upstream
repository or importing its executable package as an internal SDK.

The implementation may adapt Plex connection, library, matching, artwork, and
stream-selection logic from upstream commit
`0723c720498c4ab1eaf3a22ffc7b5a451985f0c0`. Upstream code is MIT licensed.
Telepiplex must add:

- `THIRD_PARTY_LICENSES/plex-mcp-server-MIT.txt`
- The upstream repository URL and source commit.
- A short note stating that the code was modified for Telepiplex.

Do not vendor upstream user management, playback control, destructive server
maintenance, legacy SSE/OAuth wrapper, or unrelated tools.

## Architecture

### Core pipeline extension

The current post-download processor pipeline stops as soon as a renaming
processor returns `should_stop=True`. A lower-priority Plex processor would
therefore never run. The core needs a separate completion-hook phase.

Add a completion hook API to `ModuleRegistry`:

- `add_download_completion_hook(hook, name)`
- Completion hooks run once after the normal post-download processors finish,
  including when a processor terminates the pipeline.
- The registry tracks the terminal processor name and passes a completion
  context containing the final `DownloadCompletedEvent`, final
  `PostDownloadResult`, and terminal processor name.
- Completion-hook exceptions are logged and isolated. They never change a
  successful download or rename into a failure.

The Plex hook only creates an automatic job when the terminal processor is a
successful `renaming.*` processor. It must not scan items moved to the
unorganized fallback.

### New components

- `app/modules/plex_management.py`
  - Registers Plex configuration sections.
  - Registers the download completion hook.
  - Registers Telegram `/plex` handlers.
  - Starts the optional MCP HTTP server.
- `app/services/plex_management.py`
  - Owns orchestration, state transitions, idempotency, and step results.
  - Is the only business service used by the automatic pipeline, MCP tools,
    and the optional AI interface.
- `app/adapters/plex.py`
  - Owns Plex connectivity and low-level Plex operations.
  - Contains the selectively adapted upstream logic.
- `app/adapters/tmdb.py`
  - Retrieves `original_language`, Chinese localization data for verification,
    and posters with `iso_639_1=null`.
- `app/adapters/fanart.py`
  - Retrieves textless posters with `lang=00`.
- `app/mcp/plex_server.py`
  - Defines the FastMCP tool surface and Streamable HTTP transport.
- `app/services/plex_ai.py`
  - Implements the optional `/plex` tool-calling loop without changing the
    existing search and renaming prompts.
- `app/repositories/plex_jobs.py`
  - Persists jobs, step results, confirmation state, and idempotency keys in
    SQLite.

Add `app.modules.plex_management` to `DEFAULT_ENABLED_MODULES` and
`MODULE_CATALOG` after `app.modules.renaming`. If Plex credentials or the
management switch are missing, registration remains safe and the completion
hook becomes a logged no-op. `feature/media-unifier` is not merged or used as
a runtime dependency; this new module recreates and replaces its Plex
relationship on top of the current composable `main` architecture.

## Automatic data flow

The trusted internal flow is:

```text
115 download completed
-> renaming determines the final path
-> download completion hook
-> enqueue Plex management job
-> scan the configured Plex library
-> locate the new Plex item
-> verify or repair the match
-> refresh and verify zh-CN metadata
-> select a textless poster
-> select original-language audio
-> select chi subtitle
-> send one Telegram summary
```

The completion hook only enqueues work. Plex network operations run in a
dedicated executor so they do not block the download executor or the original
download-completion notification.

## Job state and persistence

Persist jobs in `/config/plex_management.db` using Python's built-in `sqlite3`.
No external database is introduced.

States:

```text
queued
scanning
locating
matching
waiting_match_confirmation
localizing
artwork
streams
completed
failed
```

Each job records:

- Final organized path and idempotency key.
- Telegram user ID.
- Media type, titles, year, season, and episode.
- IMDb, TMDB, and TVDB IDs.
- Plex library ID and rating key.
- Terminal renaming processor.
- Current state, completed steps, warnings, errors, and summary.

The idempotency key is based on provider, final organized path, and resource
identity. An existing active or completed job prevents duplicate automatic
creation. Restart recovery resumes incomplete jobs from the first unfinished
step. Already-satisfied writes are treated as successful no-ops.

## Library routing and item location

Use `category_folder[].plex_library_id` as the public scan-routing contract.
Do not reintroduce `media.plex.library_id`, `path_map`, or `save_root`.

Before scanning, snapshot recently added rating keys. Trigger the selected
library scan, then poll every five seconds for up to 300 seconds. Locate the
new item using a combination of:

- New rating keys since the snapshot.
- Final directory and file leaf names.
- Media type, title, year, season, and episode.
- IMDb, TMDB, and TVDB IDs.

This must not require the 115 path and Plex-mounted filesystem path to be
identical.

If the item is not found within the timeout, mark the Plex job failed and
notify the user. The download and rename remain successful.

## Match verification and repair

Compare Plex GUIDs with the external IDs already carried by Telepiplex.

- If an external ID matches, accept the current Plex match.
- If the current match is wrong and there is one exact external-ID candidate,
  automatically apply `fixMatch`.
- If there are multiple candidates or no exact external-ID candidate, move the
  job to `waiting_match_confirmation` and send Telegram buttons.
- After a user selection, resume from matching without rescanning.
- After `fixMatch`, reload the item and verify the expected GUID before
  continuing.

## Chinese metadata

After a confirmed match, set or request the item-level `zh-CN` metadata
language and refresh only that item. Do not refresh the entire library.

Verify that the refreshed metadata is Chinese and record the result. Do not
copy TMDB title or summary text into Plex, do not lock metadata fields, and do
not fail the whole job if Plex does not return Chinese metadata. The user has
not observed missing Chinese metadata, so fallback text injection is outside
the first-release scope.

## Textless poster policy

Do not use AI vision or OCR by default.

Source order:

1. TMDB posters with `iso_639_1=null`, sorted deterministically by vote count,
   vote average, and resolution.
2. Fanart.tv posters with `lang=00`, sorted by likes and resolution.
3. If neither source returns a candidate, keep the current poster and send
   Plex's existing poster candidates to Telegram for manual selection.

The implementation treats the structured source language markers as the
textless signal. It does not spend AI tokens rechecking images.

TMDB is required for the primary artwork source. Fanart.tv is optional. Missing
keys disable only the dependent artwork source; they do not prevent Plex scan,
matching, localization, or subtitle selection.

## Original-language audio selection

Use TMDB `original_language` as the source of truth. Do not infer language
directly from production country. If the original language is missing or Plex
tracks lack usable language metadata, leave the selection unchanged and add a
warning.

If no TMDB API key is configured, skip original-language selection with a
warning rather than guessing from country or title.

For every Plex `MediaPart`:

1. Filter audio streams to the original language, normalizing ISO 639-1 and
   ISO 639-2 language codes.
2. Rank by codec quality tier.
3. Within a tier, rank by channel count and bitrate.
4. Use the current default/selected flag only as the final tie breaker.

Quality tiers, highest first:

1. TrueHD/Atmos, DTS-HD MA, FLAC, PCM/LPCM.
2. EAC3/DD+ Atmos, EAC3/DD+, DTS.
3. AC3, AAC, and other lossy codecs.

Ambiguous equal candidates are not changed automatically and are reported for
manual confirmation.

## Subtitle selection

Subtitle acquisition belongs to a separate future pipeline module. This module
only selects streams already recognized by Plex.

For every Plex `MediaPart`:

1. Keep the current selection if it is an external subtitle with
   `languageCode=chi`.
2. Otherwise select an external, non-transient `chi` subtitle.
3. If no external `chi` subtitle exists, select an embedded `chi` subtitle.
4. If no `chi` subtitle exists, leave the selection unchanged without failing
   the job.

The upstream subtitle-producing pipeline is expected to normalize all Chinese
subtitle language tags to `chi`. This module does not classify bilingual,
Simplified/Traditional, ASS/SRT, forced, or SDH variants.

## MCP server

Run a standard Streamable HTTP MCP server inside the Telepiplex container.
Suggested configuration:

```yaml
media:
  plex:
    base_url: "http://plex:32400"
    token: ""
    timeout: 30
    management:
      enabled: true
      database_path: "/config/plex_management.db"
      scan_poll_interval: 5
      scan_timeout: 300
    mcp:
      enabled: false
      host: "127.0.0.1"
      port: 8765
      path: "/mcp"
      auth_token: ""
    ai:
      enabled: false
      max_tool_rounds: 3

metadata:
  tmdb:
    api_key: ""
    timeout: 15

artwork:
  fanart:
    api_key: ""
    timeout: 15
```

If MCP binds to a non-loopback address, `auth_token` is mandatory. Refuse to
start the MCP listener without it. Authenticate with
`Authorization: Bearer <token>` and never expose credentials in tool
arguments, tool results, or logs. MCP startup failure must not prevent the
Telegram bot or automatic post-renaming pipeline from starting.

First-release read-only tools:

- `plex_server_status`
- `plex_list_libraries`
- `plex_inspect_item`
- `plex_list_match_candidates`
- `plex_list_artwork_candidates`
- `plex_get_job`
- `plex_list_jobs`

First-release write tools:

- `plex_scan_library`
- `plex_fix_match`
- `plex_refresh_chinese_metadata`
- `plex_set_textless_poster`
- `plex_select_original_audio`
- `plex_select_chi_subtitle`
- `plex_run_management_pipeline`
- `plex_retry_job`

External write tools use a two-step flow. The first call returns a preview and
a single-use confirmation token. The second call supplies the token and
performs the change. Tokens expire after ten minutes. The trusted internal
post-renaming pipeline is preauthorized, except for ambiguous match selection.

Do not expose upstream destructive or unrelated tools.

## Existing AI integration

Add an optional `/plex <natural-language request>` interface. It reuses
`ai.api_url`, `ai.api_key`, and `ai.model` but uses a dedicated tool-calling
orchestrator and prompts.

- Existing search, backfill, and TVDB-renaming AI functions remain unchanged.
- Only providers returning OpenAI-compatible `tool_calls` are supported.
- The AI receives tool schemas and sanitized tool results, never credentials.
- Read-only calls may execute directly.
- Write calls only create an operation preview; Telegram approval is still
  required.
- Limit a request to three tool-call rounds.
- If the provider does not support tool calls, return a clear unsupported
  message and do not ask the model to simulate calls with free-form JSON.
- The internal AI dispatcher and external MCP server share the same tool
  definitions and `PlexManagementService`; internal calls do not use an HTTP
  loopback.

## Failure behavior

Gating failures stop the job:

- Plex connection failure.
- Missing library route.
- Scan completes without locating the item.
- Match remains unresolved.

After matching, localization, artwork, audio, and subtitle steps are
independent. A failure or skip in one does not prevent the others.

- Retry Plex reads up to three times with short backoff.
- Before any write, reread current state and turn an already-satisfied write
  into a no-op success.
- Fall back from TMDB artwork to Fanart.tv artwork.
- Keep the current poster if both sources fail.
- Do not perform unbounded automatic retries.
- Resume persisted jobs from the last completed step.
- Sanitize Plex, TMDB, Fanart.tv, MCP, and AI credentials in logs.

Send one Telegram summary per job. Use success, warning, and unchanged markers
for each step. Missing optional artwork or subtitles does not mark the whole
job failed.

## Dependencies

The implementation is expected to add tested Python 3.12-compatible versions
of:

- `plexapi` for Plex object and stream operations.
- `mcp` v1 with an explicit `<2` upper bound until the v2 migration is
  intentionally performed.
- The ASGI/HTTP runtime required by the selected MCP SDK version.

TMDB and Fanart.tv use the existing `Requests` dependency. Dependency versions
must be pinned or bounded after resolving the complete dependency graph, and
`pip check` must pass.

## Testing

### Core completion hook

- Hooks run after a `should_stop=True` renaming processor.
- Hooks see the renamed final path and terminal processor name.
- Hooks do not run Plex automation for the unorganized fallback.
- Hook failures do not alter the primary pipeline result.

### Plex rules

- Current external-ID match.
- Unique exact-ID automatic repair.
- Multiple-candidate confirmation pause and resume.
- Audio language normalization and quality ranking.
- External `chi`, embedded `chi`, and missing `chi` behavior.
- Already-satisfied state produces no duplicate write.

### Artwork

- TMDB null-language selection and stable ranking.
- Fanart.tv `00` fallback and stable ranking.
- Both sources missing retains current artwork.

### Persistence

- State transitions and error recording.
- Restart recovery.
- Job deduplication.
- Confirmation token single-use and expiry.

### MCP and AI

- Streamable HTTP initialization and tool discovery.
- Bearer authentication rejection and acceptance.
- Read-only versus two-stage write behavior.
- AI tool-call loop and three-round limit.
- Unsupported tool-calling provider exits clearly.

### Integration

Use a fake Plex server and fake TMDB/Fanart.tv responses to verify:

```text
DownloadCompletedEvent
-> renaming
-> completion hook
-> scan
-> exact match
-> zh-CN refresh
-> textless poster
-> original audio
-> chi subtitle
-> Telegram summary
```

Run the existing Telepiplex test suite, targeted new tests,
`python3 -m py_compile`, `pip check`, and Telepiplex-aware `git diff --check`.
Do not place real service tokens in automated tests. Live validation uses a
dedicated Plex test library and test media item.

## Acceptance criteria

The feature is accepted when:

1. `feature/plex-management` composes cleanly with the latest `main` modules.
2. A successfully renamed test item automatically enters the Plex job queue.
3. The complete happy path finishes with the agreed match, Chinese metadata,
   poster, audio, and subtitle behavior.
4. Ambiguous matching pauses and resumes through Telegram confirmation.
5. The MCP endpoint exposes only the approved tools and enforces auth and
   two-stage writes.
6. `/plex` can use a compatible existing AI provider without affecting the
   existing AI pipelines.
7. Existing downloads and renaming remain successful when Plex processing
   fails.
8. All automated verification passes and the upstream MIT attribution is
   present.
