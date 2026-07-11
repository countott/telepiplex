# Plex Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a composable Plex management module that runs after successful renaming, persists resumable jobs, exposes a secured Streamable HTTP MCP server, and optionally lets the existing AI drive the same tools through `/plex`.

**Architecture:** Extend the core registry with a completion-hook phase, then enqueue an idempotent SQLite-backed Plex job after `renaming.*` finishes. A shared `PlexManagementService` orchestrates Plex, TMDB, and Fanart adapters; the automatic pipeline, MCP tools, Telegram callbacks, and AI tool calls all use that service.

**Tech Stack:** Python 3.12, `python-telegram-bot`, `Requests`, `plexapi==4.18.0`, MCP Python SDK v1 (`mcp>=1.26,<2`), FastMCP, Streamable HTTP, SQLite, `unittest`.

## Global Constraints

- Work only on `feature/plex-management`, based on local `main` commit `8fcf48b`.
- Keep `category_folder[].plex_library_id` as the Plex routing contract; do not add `media.plex.library_id`, `path_map`, or `save_root`.
- Run Plex automation only after a successful `renaming.*` terminal processor, never after `open115.unorganized_fallback`.
- Plex failures must never turn a completed 115 download or rename into a failure.
- Matching policy: accept matching external IDs; auto-fix one exact external-ID candidate; pause for Telegram confirmation otherwise.
- Chinese metadata: refresh and verify `zh-CN`; never inject or lock TMDB title or summary fields.
- Poster order: TMDB `iso_639_1=null`, then Fanart.tv `lang=00`, then manual Plex candidates; never use AI vision.
- Audio: use TMDB `original_language`; rank codec tier, channels, bitrate, then current selection.
- Subtitle: external `languageCode=chi`, then embedded `chi`, otherwise leave unchanged; never download subtitles here.
- MCP non-loopback listeners require Bearer authentication; external writes require a single-use ten-minute confirmation token.
- Preserve existing search, metadata-backfill, and TVDB-renaming AI prompts and behavior.
- Vendor only relevant logic from upstream commit `0723c720498c4ab1eaf3a22ffc7b5a451985f0c0` and preserve its MIT license.
- Use TDD for every task and commit each independently testable deliverable.

---

## File map

**Create**

- `app/services/plex_rules.py`: Pure matching, poster, audio, and subtitle rules.
- `app/repositories/plex_jobs.py`: SQLite job and confirmation-token persistence.
- `app/adapters/plex.py`: PlexAPI wrapper and normalized Plex domain data.
- `app/adapters/tmdb.py`: TMDB details and textless-poster queries.
- `app/adapters/fanart.py`: Fanart.tv textless-poster queries.
- `app/services/plex_management.py`: Job orchestration and resumable pipeline.
- `app/mcp/__init__.py`: MCP package marker.
- `app/mcp/plex_server.py`: FastMCP tools, auth middleware, and server lifecycle.
- `app/services/plex_ai.py`: Existing-AI tool-call dispatcher.
- `app/handlers/plex_handler.py`: `/plex`, match confirmation, and write confirmation handlers.
- `app/modules/plex_management.py`: Module registration and completion hook.
- `tests/test_download_completion_hooks.py`: Core completion-hook contract.
- `tests/test_plex_jobs.py`: SQLite state, idempotency, and token tests.
- `tests/test_plex_rules.py`: Pure Plex decision rules.
- `tests/test_plex_adapters.py`: Plex, TMDB, and Fanart adapter tests.
- `tests/test_plex_management.py`: Orchestration and failure-policy tests.
- `tests/test_plex_mcp.py`: MCP tools and auth tests.
- `tests/test_plex_ai.py`: AI tool-call loop tests.
- `tests/test_plex_management_integration.py`: Full fake-service pipeline.
- `THIRD_PARTY_LICENSES/plex-mcp-server-MIT.txt`: Upstream attribution.

**Modify**

- `app/core/module_registry.py`: Completion context and completion hooks.
- `app/115bot.py`: Default module list and catalog entry.
- `app/config.yaml.example`: Plex management, MCP, AI, TMDB, and Fanart configuration.
- `config/config.yaml.example`: Byte-identical configuration copy.
- `app/handlers/config_handler.py`: Optional configuration inputs for new credentials and settings.
- `requirements.txt`: PlexAPI and MCP runtime dependencies.
- `tests/test_composable_core.py`: Core registry regression assertions.
- `tests/test_composable_integration.py`: Stable module composition and order.
- `tests/test_bot_runtime_startup.py`: Default module load behavior.
- `tests/test_config_template_contract.py`: Template identity and new keys.
- `README.md`: New module, `/plex`, MCP endpoint, and configuration.

---

### Task 1: Add the post-download completion-hook contract

**Files:**
- Modify: `app/core/module_registry.py:32-164`
- Create: `tests/test_download_completion_hooks.py`
- Modify: `tests/test_composable_core.py`

**Interfaces:**
- Produces: `DownloadPipelineCompletion(event, result, terminal_processor)`.
- Produces: `ModuleRegistry.add_download_completion_hook(hook, name)`.
- Completion hook signature: `hook(completion: DownloadPipelineCompletion) -> None`.

- [ ] **Step 1: Write failing completion-hook tests**

```python
class DownloadCompletionHookTest(unittest.TestCase):
    def test_hook_runs_after_terminal_processor_with_final_path(self):
        registry = ModuleRegistry()
        seen = []
        registry.add_post_download_processor(
            lambda event: PostDownloadResult(True, final_path="/organized", should_stop=True),
            priority=100,
            name="renaming.generic_media",
        )
        registry.add_download_completion_hook(seen.append, "plex.management")
        result = registry.run_post_download_pipeline(make_event("/raw"))
        self.assertEqual(result.final_path, "/organized")
        self.assertEqual(seen[0].event.final_path, "/organized")
        self.assertEqual(seen[0].terminal_processor, "renaming.generic_media")

    def test_hook_failure_does_not_change_primary_result(self):
        registry = ModuleRegistry()
        registry.add_post_download_processor(
            lambda event: PostDownloadResult(True, final_path="/organized", should_stop=True),
            priority=100,
            name="renaming.generic_media",
        )
        registry.add_download_completion_hook(
            lambda completion: (_ for _ in ()).throw(RuntimeError("plex down")),
            "plex.management",
        )
        self.assertEqual(registry.run_post_download_pipeline(make_event("/raw")).final_path, "/organized")
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `python3 -m unittest tests/test_download_completion_hooks.py tests/test_composable_core.py`

Expected: import or attribute failure for `DownloadPipelineCompletion` or `add_download_completion_hook`.

- [ ] **Step 3: Implement the completion context and hook phase**

```python
@dataclass(frozen=True)
class DownloadPipelineCompletion:
    event: DownloadCompletedEvent
    result: PostDownloadResult
    terminal_processor: str | None = None

def add_download_completion_hook(self, hook: Callable, name: str):
    self.download_completion_hooks.append((str(name), hook))

def _run_download_completion_hooks(self, completion):
    for name, hook in self.download_completion_hooks:
        try:
            hook(completion)
        except Exception as exc:
            _log_completion_hook_failure(name, exc)
```

Change `run_post_download_pipeline()` to break instead of immediately
returning on `should_stop`, update `event.final_path`, construct the completion,
run hooks exactly once, and then return the original final result.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m unittest tests/test_download_completion_hooks.py tests/test_composable_core.py`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/core/module_registry.py tests/test_download_completion_hooks.py tests/test_composable_core.py
git commit -m "feat: add download completion hooks"
```

---

### Task 2: Implement SQLite Plex job persistence

**Files:**
- Create: `app/repositories/__init__.py`
- Create: `app/repositories/plex_jobs.py`
- Create: `tests/test_plex_jobs.py`

**Interfaces:**
- Produces: `PlexJobRepository(database_path: str)`.
- Produces: `create_or_get(idempotency_key: str, payload: dict) -> dict`.
- Produces: `update(job_id: int, *, state=None, rating_key=None, step_results=None, error=None) -> dict`.
- Produces: `get(job_id: int) -> dict | None`, `list(limit=50) -> list[dict]`.
- Produces: `issue_confirmation(job_id, action, payload, ttl_seconds=600) -> str`.
- Produces: `consume_confirmation(token, action) -> dict | None`.

- [ ] **Step 1: Write failing repository tests**

```python
def test_create_or_get_deduplicates_active_and_completed_jobs(self):
    first = self.repo.create_or_get("115:/Movies/Cars", {"final_path": "/Movies/Cars"})
    second = self.repo.create_or_get("115:/Movies/Cars", {"final_path": "/Movies/Cars"})
    self.assertEqual(first["id"], second["id"])

def test_confirmation_token_is_single_use(self):
    job = self.repo.create_or_get("key", {"final_path": "/x"})
    token = self.repo.issue_confirmation(job["id"], "fix_match", {"rating_key": "42"})
    self.assertEqual(self.repo.consume_confirmation(token, "fix_match")["rating_key"], "42")
    self.assertIsNone(self.repo.consume_confirmation(token, "fix_match"))
```

- [ ] **Step 2: Verify the tests fail**

Run: `python3 -m unittest tests/test_plex_jobs.py`

Expected: `ModuleNotFoundError: app.repositories.plex_jobs`.

- [ ] **Step 3: Implement schema and atomic repository methods**

Create tables `plex_jobs` and `plex_confirmations`. Store payload and step
results as UTF-8 JSON. Use `BEGIN IMMEDIATE` for create-or-get and token
consumption. Store timestamps as UTC epoch seconds. Hash confirmation tokens
with SHA-256 before persistence; return only the raw random token.

```python
JOB_SCHEMA = """
CREATE TABLE IF NOT EXISTS plex_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    step_results_json TEXT NOT NULL DEFAULT '{}',
    rating_key TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plex_confirmations (
    token_hash TEXT PRIMARY KEY,
    job_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    expires_at REAL NOT NULL,
    consumed_at REAL
);
"""

class PlexJobRepository:
    ACTIVE_STATES = {
        "queued", "scanning", "locating", "matching",
        "waiting_match_confirmation", "localizing", "artwork", "streams",
    }

    def __init__(self, database_path):
        self.database_path = str(database_path)
        self._initialize()

    def issue_confirmation(self, job_id, action, payload, ttl_seconds=600):
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        self._insert_confirmation(
            token_hash, int(job_id), str(action), payload,
            self._clock() + int(ttl_seconds),
        )
        return raw_token
```

- [ ] **Step 4: Run repository tests**

Run: `python3 -m unittest tests/test_plex_jobs.py`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/repositories tests/test_plex_jobs.py
git commit -m "feat: persist Plex management jobs"
```

---

### Task 3: Implement pure Plex decision rules

**Files:**
- Create: `app/services/__init__.py`
- Create: `app/services/plex_rules.py`
- Create: `tests/test_plex_rules.py`

**Interfaces:**
- Produces: `external_ids_match(expected: dict, actual_guids: list[str]) -> bool`.
- Produces: `choose_exact_match(expected: dict, candidates: list[dict]) -> dict | None`.
- Produces: `choose_textless_poster(tmdb: list[dict], fanart: list[dict]) -> dict | None`.
- Produces: `choose_original_audio(streams: list[dict], original_language: str) -> dict | None`.
- Produces: `choose_chi_subtitle(streams: list[dict]) -> dict | None`.

- [ ] **Step 1: Write failing rule tests**

```python
def test_unique_external_id_candidate_is_selected(self):
    candidates = [
        {"rating_key": "1", "guids": ["tmdb://10"]},
        {"rating_key": "2", "guids": ["tmdb://20"]},
    ]
    self.assertEqual(choose_exact_match({"tmdb": "20"}, candidates)["rating_key"], "2")

def test_audio_prefers_lossless_then_channels_then_bitrate(self):
    streams = [
        {"id": 1, "language_code": "jpn", "codec": "eac3", "channels": 8, "bitrate": 1536},
        {"id": 2, "language_code": "jpn", "codec": "truehd", "channels": 6, "bitrate": 4000},
    ]
    self.assertEqual(choose_original_audio(streams, "ja")["id"], 2)

def test_subtitle_prefers_external_chi_then_embedded(self):
    streams = [
        {"id": 1, "language_code": "chi", "external": False},
        {"id": 2, "language_code": "chi", "external": True, "transient": False},
    ]
    self.assertEqual(choose_chi_subtitle(streams)["id"], 2)
```

- [ ] **Step 2: Verify failure**

Run: `python3 -m unittest tests/test_plex_rules.py`

Expected: missing module or function failures.

- [ ] **Step 3: Implement normalized, deterministic rules**

Normalize IMDb, TMDB, and TVDB GUID formats. Normalize ISO 639-1 and ISO
639-2 audio codes. Define explicit audio tiers and stable tuple sorting.
Poster selection must filter `iso_639_1 is None` and `lang == "00"` before
ranking; never inspect pixels.

```python
LANGUAGE_CODES = {
    "en": "eng", "eng": "eng", "ja": "jpn", "jpn": "jpn",
    "ko": "kor", "kor": "kor", "zh": "chi", "zho": "chi", "chi": "chi",
}
AUDIO_TIERS = {
    "truehd": 300, "dts-hd ma": 300, "flac": 300, "pcm": 300, "lpcm": 300,
    "eac3 atmos": 220, "eac3": 210, "dts": 200,
    "ac3": 110, "aac": 100,
}

def choose_original_audio(streams, original_language):
    target = LANGUAGE_CODES.get(str(original_language).lower(), str(original_language).lower())
    candidates = [s for s in streams if LANGUAGE_CODES.get(str(s.get("language_code", "")).lower(), str(s.get("language_code", "")).lower()) == target]
    if not candidates:
        return None
    def rank(s):
        return (
            AUDIO_TIERS.get(str(s.get("codec_profile") or s.get("codec") or "").lower(), 0),
            int(s.get("channels") or 0),
            int(s.get("bitrate") or 0),
            bool(s.get("selected")),
        )
    ranked = sorted(candidates, key=rank, reverse=True)
    return None if len(ranked) > 1 and rank(ranked[0]) == rank(ranked[1]) else ranked[0]

def choose_chi_subtitle(streams):
    chinese = [s for s in streams if str(s.get("language_code") or "").lower() == "chi"]
    selected_external = next((s for s in chinese if s.get("external") and s.get("selected")), None)
    if selected_external:
        return selected_external
    external = sorted((s for s in chinese if s.get("external") and not s.get("transient")), key=lambda s: int(s["id"]))
    embedded = sorted((s for s in chinese if not s.get("external")), key=lambda s: int(s["id"]))
    return (external or embedded or [None])[0]
```

- [ ] **Step 4: Run rule tests**

Run: `python3 -m unittest tests/test_plex_rules.py`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/services tests/test_plex_rules.py
git commit -m "feat: define Plex management rules"
```

---

### Task 4: Add Plex, TMDB, and Fanart adapters with attribution

**Files:**
- Create: `app/adapters/plex.py`
- Create: `app/adapters/tmdb.py`
- Create: `app/adapters/fanart.py`
- Create: `tests/test_plex_adapters.py`
- Create: `THIRD_PARTY_LICENSES/plex-mcp-server-MIT.txt`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `PlexAdapter(base_url, token, timeout=30)`.
- Produces Plex methods: `server_status`, `list_libraries`, `snapshot_recent`,
  `scan_library`, `locate_candidates`, `get_item`, `list_match_candidates`,
  `fix_match`, `refresh_zh_cn`, `list_posters`, `set_poster_url`,
  `list_streams`, `select_audio`, `select_subtitle`.
- Produces: `TmdbAdapter(api_key, timeout=15).details(media_type, tmdb_id)` and
  `.textless_posters(media_type, tmdb_id)`.
- Produces: `FanartAdapter(api_key, timeout=15).textless_posters(media_type, external_ids)`.

- [ ] **Step 1: Add adapter contract tests using mocks**

```python
@patch("app.adapters.plex.PlexServer")
def test_scan_targets_library_section(self, plex_server):
    section = plex_server.return_value.library.sectionByID.return_value
    PlexAdapter("http://plex:32400", "token").scan_library("12")
    section.update.assert_called_once_with()

@patch("app.adapters.tmdb.requests.get")
def test_tmdb_filters_null_language_posters(self, get):
    get.return_value.json.return_value = {"posters": [
        {"file_path": "/a.jpg", "iso_639_1": None},
        {"file_path": "/b.jpg", "iso_639_1": "en"},
    ]}
    self.assertEqual(len(TmdbAdapter("key").textless_posters("movie", "1")), 1)
```

- [ ] **Step 2: Verify failure**

Run: `python3 -m unittest tests/test_plex_adapters.py`

Expected: missing adapter modules.

- [ ] **Step 3: Add dependencies and MIT attribution**

Append:

```text
plexapi==4.18.0
mcp>=1.26,<2
uvicorn==0.40.0
```

The attribution file must include the upstream MIT license text, repository
URL, source commit, and the statement `Modified for Telepiplex`.

- [ ] **Step 4: Implement focused adapters**

Adapt only the approved upstream Plex behaviors. Convert PlexAPI objects into
plain dictionaries before returning them to services. All HTTP calls must use
configured timeouts and `raise_for_status()`. Do not log tokens or URLs that
contain tokens.

```python
class PlexAdapter:
    def __init__(self, base_url, token, timeout=30):
        self.server = PlexServer(str(base_url).rstrip("/"), str(token), timeout=int(timeout))

    def scan_library(self, library_id):
        self.server.library.sectionByID(int(library_id)).update()

    def refresh_zh_cn(self, rating_key):
        item = self.server.fetchItem(int(rating_key))
        item.editAdvanced(languageOverride="zh-CN")
        item.refresh()
        return self._item_dict(item.reload())

    def select_audio(self, part, stream_id):
        stream = next(stream for stream in part.audioStreams() if int(stream.id) == int(stream_id))
        part.setSelectedAudioStream(stream)

    def select_subtitle(self, part, stream_id):
        stream = next(stream for stream in part.subtitleStreams() if int(stream.id) == int(stream_id))
        part.setSelectedSubtitleStream(stream)

class TmdbAdapter:
    def textless_posters(self, media_type, tmdb_id):
        response = requests.get(
            f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/images",
            headers={"Authorization": f"Bearer {self.api_key}"},
            params={"include_image_language": "null"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return [p for p in response.json().get("posters", []) if p.get("iso_639_1") is None]

class FanartAdapter:
    def textless_posters(self, media_type, external_ids):
        resource = "movies" if media_type == "movie" else "tv"
        media_id = external_ids.get("tmdb") if media_type == "movie" else external_ids.get("tvdb")
        response = requests.get(
            f"https://webservice.fanart.tv/v3/{resource}/{media_id}",
            params={"api_key": self.api_key},
            timeout=self.timeout,
        )
        response.raise_for_status()
        key = "movieposter" if media_type == "movie" else "tvposter"
        return [p for p in response.json().get(key, []) if p.get("lang") == "00"]
```

- [ ] **Step 5: Run adapter and dependency checks**

Run: `python3 -m unittest tests/test_plex_adapters.py && python3 -m pip check`

Expected: tests pass and `pip check` reports no broken requirements.

- [ ] **Step 6: Commit**

```bash
git add app/adapters requirements.txt tests/test_plex_adapters.py THIRD_PARTY_LICENSES
git commit -m "feat: add Plex metadata adapters"
```

---

### Task 5: Implement the resumable Plex management service

**Files:**
- Create: `app/services/plex_management.py`
- Create: `tests/test_plex_management.py`

**Interfaces:**
- Consumes: `PlexJobRepository`, `PlexAdapter`, `TmdbAdapter`, `FanartAdapter`, and `plex_rules`.
- Produces: `PlexManagementService.enqueue_completion(completion) -> dict | None`.
- Produces: `PlexManagementService.run_job(job_id: int) -> dict`.
- Produces: `confirm_match(job_id: int, rating_key: str) -> dict`.
- Produces: `retry_job(job_id: int) -> dict`.
- Produces: `inspect_item(rating_key: str) -> dict` and approved atomic operations used by MCP.
- Produces: `format_job_summary(job: dict) -> str`, called once when a job
  reaches `completed` or `failed`.
- Produces: `prepare_operation(action: str, payload: dict) -> dict` and
  `apply_operation(action: str, payload: dict, confirmation_token: str) -> dict`.
- Produces MCP reads: `server_status()`, `list_libraries()`,
  `inspect_item(rating_key)`, `list_match_candidates(rating_key)`,
  `list_artwork_candidates(rating_key)`, `get_job(job_id)`, and `list_jobs(limit=50)`.

- [ ] **Step 1: Write failing happy-path and failure-policy tests**

```python
def test_run_job_executes_steps_in_order(self):
    service = make_service()
    job = service.enqueue_completion(make_renaming_completion())
    result = service.run_job(job["id"])
    self.assertEqual(result["state"], "completed")
    self.assertEqual(self.plex.calls, [
        "snapshot_recent", "scan_library", "locate_candidates", "get_item",
        "refresh_zh_cn", "set_poster_url", "list_streams",
        "select_audio", "select_subtitle",
    ])

def test_artwork_failure_does_not_block_stream_selection(self):
    service = make_service(tmdb_error=RuntimeError("tmdb down"))
    result = service.run_job(service.enqueue_completion(make_renaming_completion())["id"])
    self.assertEqual(result["state"], "completed")
    self.assertEqual(result["step_results"]["artwork"]["status"], "warning")
    self.assertEqual(result["step_results"]["streams"]["status"], "success")
```

- [ ] **Step 2: Verify failure**

Run: `python3 -m unittest tests/test_plex_management.py`

Expected: missing service module.

- [ ] **Step 3: Implement state transitions and gating behavior**

Implement one method per pipeline step. Gating failures stop at connection,
library routing, item location, and unresolved matching. Localization,
artwork, audio, and subtitle steps independently record success, warning,
unchanged, or failure. Poll location with configurable interval and timeout;
inject the sleeper and clock for tests.

```python
GATING_STEPS = ("scanning", "locating", "matching")
OPTIONAL_STEPS = ("localizing", "artwork", "streams")

class WaitingForMatchConfirmation(RuntimeError):
    def __init__(self, candidates):
        super().__init__("Plex match confirmation required")
        self.candidates = list(candidates)

class PlexManagementService:
    def __init__(self, jobs, plex, tmdb=None, fanart=None, notifier=None, clock=time.time, sleeper=time.sleep):
        self.jobs = jobs
        self.plex = plex
        self.tmdb = tmdb
        self.fanart = fanart
        self.notifier = notifier
        self._clock = clock
        self._sleep = sleeper

# The following public methods are defined on PlexManagementService.
def run_job(self, job_id):
    job = self.jobs.get(job_id)
    for state, runner in (
        ("scanning", self._scan),
        ("locating", self._locate),
        ("matching", self._match),
        ("localizing", self._localize),
        ("artwork", self._artwork),
        ("streams", self._streams),
    ):
        if self._step_finished(job, state):
            continue
        self.jobs.update(job_id, state=state)
        try:
            step_result = runner(self.jobs.get(job_id))
        except WaitingForMatchConfirmation:
            return self.jobs.update(job_id, state="waiting_match_confirmation")
        except Exception as exc:
            if state in GATING_STEPS:
                return self.jobs.update(job_id, state="failed", error=self._safe_error(exc))
            step_result = {"status": "warning", "message": self._safe_error(exc)}
        job = self._record_step(job_id, state, step_result)
    completed = self.jobs.update(job_id, state="completed")
    self._notify_once(completed, self.format_job_summary(completed))
    return completed
```

- [ ] **Step 4: Implement restart and idempotent writes**

Before each write, inspect current Plex state. Persist each finished step before
starting the next. `retry_job()` clears only the failed step and later steps.

```python
def retry_job(self, job_id):
    job = self.jobs.get(job_id)
    restart_state = self._first_incomplete_or_failed_step(job)
    self.jobs.reset_from(job_id, restart_state)
    return self.run_job(job_id)

def confirm_match(self, job_id, rating_key):
    job = self.jobs.get(job_id)
    self.jobs.update(
        job_id,
        state="matching",
        rating_key=str(rating_key),
        step_results=self._merge_step(job, "matching", {"status": "confirmed"}),
    )
    return self.run_job(job_id)
```

- [ ] **Step 5: Run service tests**

Run: `python3 -m unittest tests/test_plex_management.py tests/test_plex_jobs.py tests/test_plex_rules.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/plex_management.py tests/test_plex_management.py
git commit -m "feat: orchestrate Plex management jobs"
```

---

### Task 6: Register the module and Telegram confirmation flow

**Files:**
- Create: `app/handlers/plex_handler.py`
- Create: `app/modules/plex_management.py`
- Modify: `app/115bot.py:29-49`
- Modify: `app/handlers/config_handler.py`
- Modify: `app/config.yaml.example`
- Modify: `config/config.yaml.example`
- Modify: `tests/test_composable_integration.py`
- Modify: `tests/test_bot_runtime_startup.py`
- Modify: `tests/test_config_template_contract.py`
- Create: `tests/test_plex_module.py`

**Interfaces:**
- Produces: `register_module(registry)`.
- Produces: `register_plex_handlers(application)`.
- Produces Telegram callbacks: `plex_match_confirm:<job_id>:<rating_key>` and
  `plex_write_confirm:<token>`.
- Produces: module-owned `ThreadPoolExecutor(max_workers=2)` for Plex jobs.

- [ ] **Step 1: Write failing module composition tests**

```python
def test_plex_module_registers_after_renaming(self):
    registry = ModuleRegistry()
    register_renaming(registry)
    register_plex_management(registry)
    self.assertEqual(registry.download_completion_hooks[0][0], "plex.management")
    self.assertIn("plex", [command.command for command in registry.bot_commands()])

def test_unorganized_completion_is_ignored(self):
    self.assertIsNone(on_download_completed(make_completion("open115.unorganized_fallback")))
```

- [ ] **Step 2: Verify failure**

Run: `python3 -m unittest tests/test_plex_module.py tests/test_composable_integration.py tests/test_config_template_contract.py`

Expected: missing module and missing config keys.

- [ ] **Step 3: Implement safe module registration**

Add `app.modules.plex_management` after `app.modules.renaming` in
`DEFAULT_ENABLED_MODULES` and `MODULE_CATALOG`. Register `/plex`, handlers,
configuration sections, the completion hook, and the optional MCP startup
hook. Missing Plex credentials or `management.enabled: false` makes the
completion hook a logged no-op.

```python
def register_module(registry):
    registry.add_commands([("plex", "管理 Plex 媒体库")])
    registry.add_handlers(register_plex_handlers)
    registry.add_config_sections([
        "media.plex", "media.plex.management", "media.plex.mcp",
        "media.plex.ai", "metadata.tmdb", "artwork.fanart",
    ])
    registry.add_download_completion_hook(on_download_completed, "plex.management")
    registry.add_startup_hook(start_plex_module_services)

def start_plex_module_services(application=None):
    service = get_plex_management_service()
    if service is None:
        return None
    service.resume_incomplete_jobs(plex_executor)
    return start_plex_mcp_server(service, service.mcp_config) if service.mcp_enabled else None

def on_download_completed(completion):
    if not str(completion.terminal_processor or "").startswith("renaming."):
        return None
    service = get_plex_management_service()
    if service is None or not service.enabled:
        return None
    job = service.enqueue_completion(completion)
    if job:
        plex_executor.submit(service.run_job, job["id"])
    return job
```

- [ ] **Step 4: Add exact configuration contract**

Add the approved `media.plex.management`, `media.plex.mcp`, `media.plex.ai`,
`metadata.tmdb`, and `artwork.fanart` keys to both templates. Extend `/config`
without removing the existing Plex base URL/token flow. Verify both templates
are byte-identical and parse with `yaml.safe_load`.

```yaml
media:
  plex:
    base_url: ""
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

- [ ] **Step 5: Implement match and write confirmation callbacks**

Match callbacks resume `waiting_match_confirmation` jobs. Write callbacks
consume the single-use repository token before applying a prepared operation.
Reject unauthorized users through `init.check_user`.

```python
async def handle_plex_match_confirmation(update, context):
    query = update.callback_query
    if not init.check_user(update.effective_user.id):
        await query.answer("无权操作", show_alert=True)
        return
    _, job_id, rating_key = query.data.split(":", 2)
    result = await asyncio.to_thread(get_plex_management_service().confirm_match, int(job_id), rating_key)
    await query.edit_message_text(format_job_status(result))
```

- [ ] **Step 6: Run module tests**

Run: `python3 -m unittest tests/test_plex_module.py tests/test_composable_integration.py tests/test_bot_runtime_startup.py tests/test_config_template_contract.py`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/115bot.py app/modules/plex_management.py app/handlers/plex_handler.py app/handlers/config_handler.py app/config.yaml.example config/config.yaml.example tests/test_plex_module.py tests/test_composable_integration.py tests/test_bot_runtime_startup.py tests/test_config_template_contract.py
git commit -m "feat: register Plex management module"
```

---

### Task 7: Expose secured Streamable HTTP MCP tools

**Files:**
- Create: `app/mcp/__init__.py`
- Create: `app/mcp/plex_server.py`
- Create: `tests/test_plex_mcp.py`

**Interfaces:**
- Produces: `create_plex_mcp(service, config) -> FastMCP`.
- Produces: `create_plex_mcp_app(service, config) -> ASGI app`.
- Produces: `start_plex_mcp_server(service, config) -> McpServerHandle | None`.
- MCP route: configured path, default `/mcp`.
- Auth header: `Authorization: Bearer <media.plex.mcp.auth_token>`.

- [ ] **Step 1: Write failing MCP tool and auth tests**

```python
def test_non_loopback_requires_token(self):
    with self.assertRaises(PlexMcpConfigError):
        create_plex_mcp_app(self.service, {"host": "0.0.0.0", "auth_token": ""})

def test_write_prepare_and_apply_use_single_use_token(self):
    preview = self.tools.plex_fix_match(job_id=1, rating_key="42")
    self.assertIn("confirmation_token", preview)
    applied = self.tools.plex_fix_match(job_id=1, rating_key="42", confirmation_token=preview["confirmation_token"])
    self.assertEqual(applied["status"], "applied")
```

- [ ] **Step 2: Verify failure**

Run: `python3 -m unittest tests/test_plex_mcp.py`

Expected: missing MCP module.

- [ ] **Step 3: Implement tool definitions over the shared service**

Register the seven approved read tools and eight approved write tools. Attach
read-only, idempotent, and destructive annotations where supported. Every
write tool uses the repository confirmation flow; tool arguments and results
never contain service credentials.

Read tools are `plex_server_status`, `plex_list_libraries`,
`plex_inspect_item`, `plex_list_match_candidates`,
`plex_list_artwork_candidates`, `plex_get_job`, and `plex_list_jobs`.
Write tools are `plex_scan_library`, `plex_fix_match`,
`plex_refresh_chinese_metadata`, `plex_set_textless_poster`,
`plex_select_original_audio`, `plex_select_chi_subtitle`,
`plex_run_management_pipeline`, and `plex_retry_job`.

```python
def create_plex_mcp(service, config):
    mcp = FastMCP("Telepiplex Plex", stateless_http=True, json_response=True)

    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
    def plex_server_status():
        return service.server_status()

    @mcp.tool(annotations={"readOnlyHint": False, "idempotentHint": True})
    def plex_retry_job(job_id: int, confirmation_token: str = ""):
        return prepare_or_apply(
            service,
            action="retry_job",
            payload={"job_id": int(job_id)},
            confirmation_token=confirmation_token,
        )

    register_remaining_approved_tools(mcp, service, prepare_or_apply)
    return mcp

def create_plex_mcp_app(service, config):
    return create_plex_mcp(service, config).streamable_http_app()

def prepare_or_apply(service, action, payload, confirmation_token=""):
    if confirmation_token:
        return service.apply_operation(action, payload, confirmation_token)
    return service.prepare_operation(action, payload)

def register_remaining_approved_tools(mcp, service, prepare):
    read_methods = {
        "plex_list_libraries": service.list_libraries,
        "plex_inspect_item": service.inspect_item,
        "plex_list_match_candidates": service.list_match_candidates,
        "plex_list_artwork_candidates": service.list_artwork_candidates,
        "plex_get_job": service.get_job,
        "plex_list_jobs": service.list_jobs,
    }
    write_actions = (
        "plex_scan_library", "plex_fix_match", "plex_refresh_chinese_metadata",
        "plex_set_textless_poster", "plex_select_original_audio",
        "plex_select_chi_subtitle", "plex_run_management_pipeline",
    )
    for name, method in read_methods.items():
        def read_tool(payload: dict | None = None, _method=method):
            return _method(**(payload or {}))
        mcp.tool(name=name, annotations={"readOnlyHint": True, "idempotentHint": True})(read_tool)
    for name in write_actions:
        def write_tool(payload: dict, confirmation_token: str = "", _name=name):
            return prepare(service, _name, payload, confirmation_token)
        mcp.tool(name=name, annotations={"readOnlyHint": False, "idempotentHint": True})(write_tool)
```

- [ ] **Step 4: Implement ASGI Bearer middleware and lifecycle**

Use FastMCP Streamable HTTP. Reject missing/invalid Bearer tokens before MCP
dispatch. Start Uvicorn in a daemon thread. If startup fails, log the sanitized
error and return `None` without affecting the bot.

```python
class BearerAuthMiddleware:
    def __init__(self, app, expected_token):
        self.app = app
        self.expected = f"Bearer {expected_token}"

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
            if not secrets.compare_digest(headers.get("authorization", ""), self.expected):
                response = Response("Unauthorized", status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

@dataclass
class McpServerHandle:
    server: uvicorn.Server
    thread: threading.Thread

class PlexMcpConfigError(ValueError):
    """Raised when the MCP listener would be exposed without authentication."""

def validate_mcp_config(config):
    host = str(config.get("host") or "127.0.0.1")
    token = str(config.get("auth_token") or "")
    if host not in {"127.0.0.1", "localhost", "::1"} and not token:
        raise PlexMcpConfigError("Non-loopback MCP listeners require auth_token")

def start_plex_mcp_server(service, config):
    validate_mcp_config(config)
    app = create_plex_mcp_app(service, config)
    secured = BearerAuthMiddleware(app, config["auth_token"]) if config.get("auth_token") else app
    server = uvicorn.Server(uvicorn.Config(secured, host=config["host"], port=int(config["port"]), log_level="warning"))
    thread = threading.Thread(target=server.run, name="plex-mcp", daemon=True)
    thread.start()
    return McpServerHandle(server=server, thread=thread)
```

- [ ] **Step 5: Run MCP tests**

Run: `python3 -m unittest tests/test_plex_mcp.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/mcp tests/test_plex_mcp.py
git commit -m "feat: expose Plex MCP server"
```

---

### Task 8: Add the optional `/plex` AI tool-call loop

**Files:**
- Create: `app/services/plex_ai.py`
- Modify: `app/handlers/plex_handler.py`
- Create: `tests/test_plex_ai.py`

**Interfaces:**
- Produces: `PlexAIOrchestrator(ai_config, tool_dispatcher, max_tool_rounds=3)`.
- Produces: `run(user_text: str) -> dict` with `message`, `tool_results`, and optional `confirmation`.
- Consumes the same tool schemas and dispatcher as `app/mcp/plex_server.py`.

- [ ] **Step 1: Write failing AI orchestration tests**

```python
def test_read_tool_call_executes_and_returns_final_message(self):
    ai = FakeAI([
        tool_call_response("plex_server_status", {}),
        text_response("Plex 正常运行"),
    ])
    result = PlexAIOrchestrator(ai.config, self.dispatcher).run("Plex 正常吗")
    self.assertEqual(result["message"], "Plex 正常运行")

def test_tool_round_limit_stops_loop(self):
    ai = FakeAI([tool_call_response("plex_server_status", {})] * 4)
    result = PlexAIOrchestrator(ai.config, self.dispatcher, max_tool_rounds=3).run("循环")
    self.assertEqual(result["error"], "tool_round_limit")
```

- [ ] **Step 2: Verify failure**

Run: `python3 -m unittest tests/test_plex_ai.py`

Expected: missing AI orchestrator.

- [ ] **Step 3: Implement a dedicated OpenAI-compatible tool-call client**

Do not change existing `chat_completion()` behavior. Build messages and tools
inside `plex_ai.py`, parse `choices[0].message.tool_calls`, dispatch sanitized
arguments, append tool results, and stop after three rounds. If no tool-call
shape is returned when a tool is required, return the explicit unsupported
message.

```python
def run(self, user_text):
    messages = [
        {"role": "system", "content": PLEX_AI_SYSTEM_PROMPT},
        {"role": "user", "content": str(user_text)},
    ]
    tool_results = []
    for round_index in range(self.max_tool_rounds):
        response = self.client.complete(messages=messages, tools=self.tool_schemas)
        message = response["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        if not calls:
            return {"message": str(message.get("content") or ""), "tool_results": tool_results}
        messages.append(message)
        for call in calls:
            name = call["function"]["name"]
            arguments = json.loads(call["function"].get("arguments") or "{}")
            result = self.dispatcher.dispatch(name, arguments)
            tool_results.append({"name": name, "result": result})
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(result, ensure_ascii=False),
            })
    return {"error": "tool_round_limit", "message": "Plex 工具调用超过三轮，已停止。", "tool_results": tool_results}
```

- [ ] **Step 4: Wire `/plex` without enabling it by default**

When `media.plex.ai.enabled` is false, `/plex` returns a concise disabled
message. Read tools reply directly. Write tools produce Telegram confirmation
buttons and never auto-apply.

```python
async def plex_command(update, context):
    service = get_plex_management_service()
    if service is None or not service.ai_enabled:
        await update.effective_message.reply_text("Plex AI 管理未启用。")
        return
    request_text = " ".join(context.args).strip()
    result = await asyncio.to_thread(service.ai.run, request_text)
    await send_plex_ai_result(update, result)
```

- [ ] **Step 5: Run AI and handler tests**

Run: `python3 -m unittest tests/test_plex_ai.py tests/test_plex_module.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/plex_ai.py app/handlers/plex_handler.py tests/test_plex_ai.py
git commit -m "feat: connect AI to Plex MCP tools"
```

---

### Task 9: Add end-to-end integration coverage and operational docs

**Files:**
- Create: `tests/test_plex_management_integration.py`
- Modify: `README.md`
- Modify: `tests/test_deployment_contract.py`

**Interfaces:**
- Consumes all earlier public interfaces.
- Produces a deployable, documented module with no new runtime secrets in source control.

- [ ] **Step 1: Write the failing fake-service integration test**

```python
def test_renaming_completion_runs_full_plex_pipeline(self):
    registry, fake_plex, notifications = build_integration_runtime()
    result = registry.run_post_download_pipeline(make_download_event())
    wait_for_jobs()
    self.assertEqual(result.final_path, "/真人电影/千与千寻 (Spirited Away)")
    self.assertEqual(fake_plex.selected_audio.language_code, "jpn")
    self.assertTrue(fake_plex.selected_subtitle.external)
    self.assertEqual(fake_plex.selected_subtitle.language_code, "chi")
    self.assertIn("Plex 媒体处理完成", notifications[-1])
```

- [ ] **Step 2: Verify failure**

Run: `python3 -m unittest tests/test_plex_management_integration.py`

Expected: an integration assertion fails until all components are wired.

- [ ] **Step 3: Complete wiring and documentation**

Document:

- `app.modules.plex_management` in the stable module list.
- Exact `/config/config.yaml` keys.
- `/plex` usage and AI tool-call requirement.
- MCP URL, port mapping, and Bearer header.
- TMDB and Fanart.tv key requirements.
- The post-renaming automatic pipeline and allowed skips.
- The fact that subtitle downloading belongs to another module.

Add this operational section and expand it with the exact YAML already defined
in Task 6:

```markdown
## Plex 管理模块

`app.modules.plex_management` 在重命名成功后异步执行 Plex 扫库、匹配确认、
中文元数据刷新、无字海报、原声音轨和中文字幕选择。Plex 处理失败不会回滚
115 下载或重命名。

启用 MCP 后，客户端连接 `http://HOST:8765/mcp` 并发送
`Authorization: Bearer YOUR_MCP_TOKEN`。非本机监听必须配置 Token。

`/plex` 仅在 `media.plex.ai.enabled: true` 且 AI 服务支持 OpenAI 兼容
`tool_calls` 时可用。字幕下载不属于此模块。
```

- [ ] **Step 4: Run integration and deployment tests**

Run: `python3 -m unittest tests/test_plex_management_integration.py tests/test_deployment_contract.py tests/test_composable_integration.py`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_plex_management_integration.py tests/test_deployment_contract.py README.md
git commit -m "docs: complete Plex management integration"
```

---

### Task 10: Run full verification and review the branch

**Files:**
- Verify all changed files.
- Modify only files needed to fix discovered regressions.

- [ ] **Step 1: Run the full unit-test suite**

Run: `python3 -m unittest discover -s tests`

Expected: all tests pass with no errors or failures.

- [ ] **Step 2: Compile every tracked Python file**

Run: `python3 -m py_compile $(git ls-files '*.py')`

Expected: exit code 0 and no output.

- [ ] **Step 3: Validate dependency consistency**

Run: `python3 -m pip check`

Expected: `No broken requirements found.`

- [ ] **Step 4: Validate templates and whitespace**

Run:

```bash
cmp app/config.yaml.example config/config.yaml.example
python3 -c 'import yaml; yaml.safe_load(open("app/config.yaml.example", encoding="utf-8")); yaml.safe_load(open("config/config.yaml.example", encoding="utf-8"))'
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check main...HEAD
```

Expected: every command exits 0 with no output.

- [ ] **Step 5: Inspect branch scope**

Run:

```bash
git status --short --branch
git log --oneline main..HEAD
git diff --stat main...HEAD
```

Expected: only the Plex management design, plan, code, tests, attribution,
configuration, and documentation are present. The unrelated untracked
`docs/superpowers/plans/2026-07-11-ai-wikipedia-download-planner.md` remains
untouched and uncommitted.

- [ ] **Step 6: Commit verification-only fixes if required**

If verification required code changes, rerun the affected focused tests and
commit only those fixes:

```bash
git add -u
git commit -m "fix: resolve Plex management regressions"
```

If no fixes were required, do not create an empty commit.
