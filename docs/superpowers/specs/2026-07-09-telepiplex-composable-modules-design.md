# Telepiplex Composable Modules Design

## Goal

Telepiplex must move from deletion-heavy feature snapshots to lightweight composable modules. The first module set is:

- `feature/telepiplex-core`
- `feature/115`
- `feature/media-search`
- `feature/renaming`

`main` remains the current stitched business reference. New feature branches should express additive module contracts that can be combined by `main` or an integration branch.

## Current Problem

The existing feature branches prove individual business boundaries, but they are not plug-in modules. Each branch rewrites shared files such as `app/115bot.py`, `config/config.yaml.example`, `requirements.txt`, and `app/handlers/download_handler.py`. Merging the branches together produces content conflicts and modify/delete conflicts.

The heaviest coupling is around the download completion flow. In current `main`, 115 delivery, search handoff, TVDB/AI metadata use, file renaming, unorganized fallback, media-library refresh, and retry handling are all coordinated inside `app/handlers/download_handler.py`. This makes future features such as audio-track cleanup and subtitle organization likely to conflict in the same file.

## Design Choice

Use an in-repo lightweight module framework. Do not build a dynamic plugin marketplace or external package system.

Modules stay as Python code inside this repository. `main` or a temporary integration branch imports the modules explicitly, registers them, and controls enablement order. This is enough to make branches composable while keeping deployment and debugging simple.

## Branch Strategy

`main` is not modified by the branch extraction work. It remains the reference for currently working stitched behavior.

New composable branches use this shape:

- `feature/telepiplex-core` is extracted from `main` and contains only the core runtime and module framework.
- `feature/115` is based on `feature/telepiplex-core` and adds the 115 module.
- `feature/media-search` is based on `feature/telepiplex-core` and adds the media search module.
- `feature/renaming` is based on `feature/telepiplex-core` and adds the renaming module.

An integration branch can be created from `feature/telepiplex-core` to verify:

```text
core + 115 + media-search + renaming ~= current main business flow
```

The integration branch is for validation only unless the user explicitly asks to merge it into `main`.

## Core Module Contract

`feature/telepiplex-core` owns:

- process startup
- config loading
- safe config logging
- Telegram application lifecycle
- Telegram command/menu aggregation
- user validation
- message queue runtime
- save-directory helpers
- module registry
- download request contract
- download completion event contract
- post-download pipeline contract

Core does not own:

- 115 auth or offline delivery
- Prowlarr search
- TVDB/Douban/AI media lookup
- media renaming
- media-library scan behavior
- future audio cleanup
- future subtitle search or organization

### Module Registration

Each module exposes one registration function:

```python
def register_module(registry):
    registry.add_config_sections([])
```

The registry supports:

- `add_commands(commands)`
- `add_handlers(register_handlers)`
- `add_startup_hook(hook)`
- `add_config_sections(section_names)`
- `set_download_provider(provider)`
- `add_post_download_processor(processor, priority)`

The registry is in-repo and synchronous by default. It should not perform import-time network calls or long-running initialization.

### DownloadRequest

Search and direct-download modules communicate through a stable request object:

```python
DownloadRequest(
    link: str,
    selected_path: str,
    user_id: int,
    naming_metadata: dict | None = None,
    metadata: dict | None = None,
    source: str = "",
)
```

The contract means "please deliver this link into the selected storage path." It does not imply the delivery provider is 115.

### DownloadCompletedEvent

Download providers publish a completion event after the raw download is available:

```python
DownloadCompletedEvent(
    link: str,
    selected_path: str,
    user_id: int,
    final_path: str,
    resource_name: str,
    naming_metadata: dict | None = None,
    metadata: dict | None = None,
    provider: str = "115",
)
```

`final_path` points to the raw downloaded folder or the wrapper folder created for a single file. Post-download processors consume and may update this event.

### PostDownloadResult

Post-download processors return a result:

```python
PostDownloadResult(
    handled: bool,
    final_path: str | None = None,
    message: str | None = None,
    should_stop: bool = False,
    metadata: dict | None = None,
)
```

`handled=True` means the processor made a meaningful change. `should_stop=True` means later processors should not run because the file has reached a terminal state. A failed processor must log the exception and return a non-terminal result so fallback handling can continue.

## 115 Module Contract

`feature/115` owns:

- 115 OpenAPI initialization
- 115 auth and token config
- `/auth`
- `/config` entries needed for 115 tokens
- `/reload` integration with 115 token reload
- `/magnet` and `/m`
- save-directory selection
- direct 115 offline delivery
- single-file wrapper folder creation
- failed offline retry handling
- cloud offline task cleanup

The 115 module does not own:

- search UI
- Prowlarr adapter
- TVDB/Douban/AI metadata lookup
- renaming decisions
- subtitle organization
- audio cleanup

After successful offline delivery, the 115 module creates `DownloadCompletedEvent` and passes it to the core post-download pipeline.

If no post-download processor handles the event, the 115 module moves the raw result to the configured unorganized path and sends the same class of user-facing notification current `main` sends.

## Media Search Module Contract

`feature/media-search` owns:

- `/search`
- `/s`
- Prowlarr adapter
- release scoring
- metadata URL detection
- Douban/IMDb/TVDB/TMDB/MovieDB link parsing
- search entry confirmation
- search state and TTL
- building `DownloadRequest`

The media search module does not import `app.handlers.download_handler` and does not call a concrete `download_task`. It submits a `DownloadRequest` through the core download dispatcher.

The dispatcher fails clearly if no download provider is registered.

The media search module preserves metadata needed by renaming:

- `naming_metadata`
- `metadata`
- `release_title`
- `external_ids`
- `selected_scope`
- `season_number`
- `episode_number`

## Renaming Module Contract

`feature/renaming` owns the post-download media renaming and organization stage.

It is created by extracting the already working behavior from current `main`, primarily:

- `app/utils/media_naming.py`
- `app/utils/tvdb_rename.py`
- TVDB candidate validation used for episode rename plans
- AI-assisted TVDB episode mapping
- filename-derived fallback metadata
- merge priority for `naming_metadata`, `metadata`, and filename metadata
- movie and episode folder naming
- top-level Chinese plus English folder grammar using `中文 ◈ English`
- collection parent folders
- TVDB season folders
- unorganized fallback decision signals
- media-library refresh request after successful organization

The renaming module does not own:

- 115 auth
- raw offline delivery
- search UI
- future subtitle search
- future audio-track cleanup

The renaming module registers one or more post-download processors. Its first processor should attempt TVDB/AI episode organization when metadata supports it. Its second processor should attempt generic movie or episode auto-rename from Douban/search/filename metadata.

If renaming succeeds, it returns `should_stop=True` so later processors do not operate on stale paths. If renaming cannot determine a safe target, it returns `handled=False` and lets fallback or later processors continue.

## Future Module Fit

The same pipeline supports future modules:

- `feature/audio-cleanup` registers a post-download processor after renaming if it expects final paths, or before renaming if it needs raw release folders.
- `feature/subtitle-organizer` registers a post-download processor after renaming so subtitles can use final media names and paths.

Processor priority makes order explicit. A proposed default order is:

```text
100  renaming.tvdb_episode
110  renaming.generic_media
200  audio_cleanup
300  subtitle_organizer
900  fallback_unorganized
```

The fallback unorganized processor may live in 115 or core integration code, but the behavior should remain equivalent to current `main`.

## Configuration Contract

Core loads the full config file. Modules declare which sections they consume.

Initial section ownership:

- core: `bot_token`, `allowed_user`, `category_folder`
- 115: `115_app_id`, `access_token`, `refresh_token`, `open115`
- media-search: `search.prowlarr`, `metadata.tvdb`, `ai`
- renaming: `media.unorganized_path`, `media.plex`, `metadata.tvdb`, `ai`, `category_folder[].plex_library_id`

`media.plex.library_id` is not part of the public config contract. Plex scan routing uses `category_folder[].plex_library_id`.

Each feature branch may include a minimal config example for its module, but the integration config example is the union of enabled module sections.

## Error Handling

Module failures must be isolated.

- A missing download provider produces a clear user-facing message.
- A failed 115 delivery stays in the 115 failure and retry path.
- A failed renaming processor logs the error and allows unorganized fallback.
- A missing optional TVDB, AI, or Plex config disables the relevant optional branch without preventing raw download delivery.
- A media-library scan failure must not mark the download itself as failed.

## Testing Requirements

Each module branch must have targeted surface tests:

- core exposes only runtime commands and module registry contracts.
- 115 exposes only auth/config/reload/magnet/direct delivery surfaces.
- media-search exposes only search and request handoff surfaces.
- renaming exposes only post-download processor and naming-plan surfaces.

Integration validation must prove:

- media-search can submit `DownloadRequest` without importing the 115 module.
- 115 can deliver a request and emit `DownloadCompletedEvent`.
- renaming can consume the event and produce the same target naming shape as current `main`.
- disabling renaming still results in unorganized fallback.
- enabling all four modules reconstructs the intended current `main` flow.

Verification commands for each branch:

```bash
python3 -m unittest <targeted tests>
python3 -m py_compile $(git ls-files '*.py')
git diff --check
```

## Non-Goals

This design does not require:

- external plugin packaging
- runtime module discovery from installed packages
- a database migration
- merging anything into `main` during the extraction round
- rewriting the whole Telegram bot UI
- making every branch independently production-complete

The goal is composable code boundaries and testable business contracts.
