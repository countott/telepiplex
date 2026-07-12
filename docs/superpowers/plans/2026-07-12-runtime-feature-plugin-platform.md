# Runtime Feature Plugin Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a core-only Docker runtime that installs, supervises, routes, upgrades, drains, rolls back, and removes versioned Feature subprocesses without restarting core.

**Architecture:** Core owns Telegram and a durable plugin control plane. Every Feature is installed from a verified `.tpx` artifact into a private venv, runs as a subprocess, and communicates through bounded NDJSON over a Unix socket. This plan delivers the core platform and a reference echo Feature; the four business Feature migrations each receive a follow-on branch plan after this contract is executable.

**Tech Stack:** Python 3.12, python-telegram-bot 22.3, PyYAML, jsonschema, SQLite, Unix domain sockets, `venv`, `pip --no-index`, unittest/pytest, Docker.

## Global Constraints

- Work only in `feature/telepiplex-core` for this plan.
- Do not modify or merge `main`.
- Do not copy business Feature code into the core image.
- Core API starts at `1.0`; incompatible manifests fail closed.
- Feature packages are immutable `.tpx` ZIP artifacts with member checksums.
- Feature processes never import Telegram, `app.init`, core internals, or another Feature.
- Feature lifecycle actions must not restart the core process.
- All production behavior changes start with a failing test.
- Commit after every independently verified task.
- Do not push without explicit user approval.

---

### Task 1: Manifest and contract primitives

**Files:**
- Create: `app/core/plugin_contract.py`
- Create: `app/core/plugin_manifest.py`
- Test: `tests/test_plugin_manifest.py`

**Interfaces:**
- Produces: `CORE_API_VERSION = "1.0"`.
- Produces: `ContractError(code: str, message: str)`.
- Produces: `PluginManifest.from_mapping(value: dict) -> PluginManifest`.
- Produces: immutable `CapabilityDeclaration`, `CommandDeclaration`, and `PluginManifest` dataclasses.
- Produces: `PluginManifest.supports_core(version: str) -> bool`.

- [x] **Step 1: Write manifest RED tests**

Test a valid manifest, missing identity, invalid semantic version, unsupported
core range, duplicate capabilities, duplicate commands, unsafe entry points,
and capability names outside `^[a-z][a-z0-9_.-]{1,63}$`.

```python
manifest = PluginManifest.from_mapping({
    "plugin_id": "echo",
    "name": "Echo",
    "version": "1.0.0",
    "core_api": ">=1.0,<2.0",
    "entry_point": "telepiplex_echo.runtime:main",
    "provides": [{"name": "demo.echo", "exclusive": True}],
    "requires": [],
    "subscribes": [],
    "publishes": [],
    "commands": [{"name": "echo", "description": "Echo text"}],
    "source": {"repository": "origin", "branch": "feature/echo", "commit": "a" * 40},
})
assert manifest.plugin_id == "echo"
assert manifest.supports_core("1.0")
```

- [x] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_plugin_manifest -v`

Expected: import failure for `app.core.plugin_manifest`.

- [x] **Step 3: Implement strict manifest parsing**

Use only parsed argv/module strings; reject whitespace, `/`, `\\`, `..`, empty
lists with invalid members, duplicate declarations, and unrecognized provider
modes. Implement the supported API range for the exact grammar
`>=MAJOR.MINOR,<MAJOR.MINOR` without adding a packaging-library dependency.

- [x] **Step 4: Verify GREEN**

Run: `python3 -m unittest tests.test_plugin_manifest -v`

Expected: all manifest tests pass.

- [x] **Step 5: Commit**

```bash
git add app/core/plugin_contract.py app/core/plugin_manifest.py tests/test_plugin_manifest.py
git commit -m "feat(core): define Feature plugin contract"
```

### Task 2: Deterministic artifact builder and verifier

**Files:**
- Create: `app/core/plugin_artifact.py`
- Create: `tools/build_tpx.py`
- Test: `tests/test_plugin_artifact.py`

**Interfaces:**
- Consumes: `PluginManifest.from_mapping`.
- Produces: `build_tpx(source_dir: Path, output: Path) -> Path`.
- Produces: `verify_tpx(path: Path, expected_sha256: str = "") -> VerifiedArtifact`.
- Produces: `VerifiedArtifact(path, sha256, manifest, members)`.

- [x] **Step 1: Write artifact RED tests**

Create fixture archives in a temporary directory. Prove deterministic output,
required-member enforcement, member checksum verification, expected archive
digest verification, maximum member/package size, duplicate member rejection,
absolute path rejection, `../` rejection, and symlink rejection.

```python
first = build_tpx(source, temp / "first.tpx")
second = build_tpx(source, temp / "second.tpx")
assert first.read_bytes() == second.read_bytes()
assert verify_tpx(first).manifest.plugin_id == "echo"
```

- [x] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_plugin_artifact -v`

Expected: import failure for `app.core.plugin_artifact`.

- [x] **Step 3: Implement deterministic ZIP construction**

Sort member names, use a fixed ZIP timestamp, store POSIX permissions without
symlink bits, generate `checksums.sha256` for every member except itself, and
write through a temporary file followed by `os.replace`.

- [x] **Step 4: Implement safe verification**

Read and validate the archive without extracting. Reject encrypted entries,
links, unsafe names, unexpected top-level files, missing required members,
digest mismatches, and manifests that fail Task 1 validation.

- [x] **Step 5: Verify GREEN and CLI behavior**

Run:

```bash
python3 -m unittest tests.test_plugin_artifact -v
python3 tools/build_tpx.py --help
```

Expected: tests pass and help names `SOURCE_DIR` and `OUTPUT.tpx`.

- [x] **Step 6: Commit**

```bash
git add app/core/plugin_artifact.py tools/build_tpx.py tests/test_plugin_artifact.py
git commit -m "feat(core): build and verify immutable Feature artifacts"
```

### Task 3: Persistent plugin store and schema-owned configuration

**Files:**
- Create: `app/core/plugin_store.py`
- Test: `tests/test_plugin_store.py`
- Modify: `requirements.txt`
- Modify: `app/init.py`
- Modify: `app/config.yaml.example`
- Modify: `config/config.yaml.example`
- Delete: `config/modules/core.yaml.example`
- Test: `tests/test_category_route_startup.py`
- Test: `tests/test_config_template_contract.py`
- Test: `tests/test_telepiplex_core_surface.py`

**Interfaces:**
- Consumes: `VerifiedArtifact`.
- Produces: `PluginStore(root: Path)`.
- Produces: `PluginStore.stage(artifact) -> StagedRelease`.
- Produces: `PluginStore.activate(staged) -> ActiveRelease`.
- Produces: `PluginStore.active(plugin_id) -> ActiveRelease | None`.
- Produces: `PluginStore.list_installed() -> list[InstalledPlugin]`.
- Produces: `PluginStore.validate_config(release, value) -> dict`.

- [x] **Step 1: Write plugin-store RED tests**

Prove exact `/config/plugins/<id>` layout, safe extraction, default config
creation, JSON Schema validation, atomic `active.json`, preservation of the
previous release, corrupt active-record quarantine, and no writes outside the
temporary plugin root.

- [x] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_plugin_store -v`

Expected: import failure for `app.core.plugin_store`.

- [x] **Step 3: Add the schema dependency and core-only config**

Add `jsonschema>=4.23,<5` to `requirements.txt`. Replace `modules.enabled` with:

```yaml
plugins:
  root: /config/plugins
  catalog: /config/plugins/catalog.yaml
  install_timeout: 300
  startup_timeout: 30
  drain_timeout: 120
  stabilize_seconds: 10
  restart_limit: 3
```

Keep the two example files byte-identical.

- [x] **Step 4: Implement store transactions and config validation**

Extract verified members into `.staging/<uuid>`, validate
`config.default.yaml` against `config.schema.json`, atomically move to
`releases/<version>`, and write `active.json` through fsync + `os.replace`.

- [x] **Step 5: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_plugin_store tests.test_config_template_contract -v
cmp -s app/config.yaml.example config/config.yaml.example
```

Expected: tests pass and `cmp` exits 0.

- [x] **Step 6: Commit**

```bash
git add app/core/plugin_store.py requirements.txt app/config.yaml.example config/config.yaml.example tests/test_plugin_store.py
git commit -m "feat(core): persist Feature releases and owned config"
```

### Task 4: Unix RPC protocol and Feature SDK runtime

**Files:**
- Create: `app/core/plugin_rpc.py`
- Create: `sdk/pyproject.toml`
- Create: `sdk/src/telepiplex_plugin_sdk/__init__.py`
- Create: `sdk/src/telepiplex_plugin_sdk/runtime.py`
- Create: `sdk/src/telepiplex_plugin_sdk/types.py`
- Test: `tests/test_plugin_rpc.py`
- Test: `tests/test_plugin_sdk_runtime.py`

**Interfaces:**
- Produces: `RpcClient(socket_path, token, max_frame_bytes=1048576)`.
- Produces: `await RpcClient.request(method, params, *, deadline, idempotency_key="") -> dict`.
- Produces: SDK `FeatureRuntime(manifest, handlers)` with lifecycle,
  capability, event, command, callback, and config methods.
- Produces: stable error codes `invalid_request`, `unauthorized`, `not_found`,
  `deadline_exceeded`, `busy`, and `internal_error`.

- [ ] **Step 1: Write RPC RED tests**

Run an SDK server in a temporary Unix socket and prove handshake token
validation, request IDs, deadlines, Unicode payloads, maximum frame rejection,
unknown method errors, sanitized internal errors, concurrent requests, drain
state, and graceful shutdown.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_plugin_rpc tests.test_plugin_sdk_runtime -v`

Expected: imports fail.

- [ ] **Step 3: Implement the SDK server**

Use `asyncio.start_unix_server`, one NDJSON envelope per line, `hmac.compare_digest`
for the startup token, bounded reads, and typed response actions. Do not import
anything from `app` in `sdk/src`.

- [ ] **Step 4: Implement the core client**

Use `asyncio.open_unix_connection`; apply `asyncio.timeout` from the requested
deadline and always close the writer in `finally`. Convert protocol errors to
`ContractError` without exposing tokens or raw tracebacks.

- [ ] **Step 5: Verify GREEN and SDK independence**

Run:

```bash
python3 -m unittest tests.test_plugin_rpc tests.test_plugin_sdk_runtime -v
! rg -n "from app|import app|import init|telegram" sdk/src
```

Expected: tests pass and the forbidden-import scan returns no matches.

- [ ] **Step 6: Commit**

```bash
git add app/core/plugin_rpc.py sdk tests/test_plugin_rpc.py tests/test_plugin_sdk_runtime.py
git commit -m "feat(core): add Unix RPC Feature SDK"
```

### Task 5: Capability router and durable event journal

**Files:**
- Create: `app/core/capability_router.py`
- Create: `app/core/event_journal.py`
- Test: `tests/test_capability_router.py`
- Test: `tests/test_event_journal.py`

**Interfaces:**
- Produces: `CapabilityRouter.activate(plugin_id, manifest, client)`.
- Produces: `CapabilityRouter.deactivate(plugin_id)`.
- Produces: `await CapabilityRouter.call(capability, method, payload, context) -> dict`.
- Produces: `EventJournal.publish(event_type, payload, idempotency_key) -> str`.
- Produces: `EventJournal.pending(plugin_id, limit=100) -> list[EventDelivery]`.
- Produces: `EventJournal.ack(event_id, plugin_id) -> bool`.

- [ ] **Step 1: Write router and journal RED tests**

Cover one exclusive provider, ambiguous provider rejection, missing required
capability, activation rollback, dependent blocked state, context propagation,
event fan-out, duplicate idempotency keys, per-subscriber acknowledgement, and
pending delivery after a simulated process restart.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_capability_router tests.test_event_journal -v`

Expected: module import failures.

- [ ] **Step 3: Implement atomic route snapshots**

Build a candidate route table, validate command/callback/capability conflicts
and all required capabilities, then swap the immutable snapshot under a lock.
Never mutate live routes before validation succeeds.

- [ ] **Step 4: Implement SQLite journal transactions**

Use WAL, foreign keys, unique `(event_type, idempotency_key)`, and delivery rows
keyed by `(event_id, plugin_id)`. `ack` may transition only `pending` or
`delivering` rows to `acked`.

- [ ] **Step 5: Verify GREEN**

Run: `python3 -m unittest tests.test_capability_router tests.test_event_journal -v`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/core/capability_router.py app/core/event_journal.py tests/test_capability_router.py tests/test_event_journal.py
git commit -m "feat(core): route capabilities and journal Feature events"
```

### Task 6: Subprocess supervisor and health isolation

**Files:**
- Create: `app/core/plugin_supervisor.py`
- Test: `tests/test_plugin_supervisor.py`

**Interfaces:**
- Consumes: `ActiveRelease`, `RpcClient`, `PluginManifest`.
- Produces: `PluginSupervisor.start(release, *, shadow=False) -> PluginProcess`.
- Produces: `await PluginSupervisor.health(plugin_id) -> PluginHealth`.
- Produces: `await PluginSupervisor.drain(plugin_id, timeout) -> DrainResult`.
- Produces: `await PluginSupervisor.stop(plugin_id, timeout=10) -> None`.
- Produces states: `starting`, `healthy`, `draining`, `stopped`, `failed`,
  `quarantined`.

- [ ] **Step 1: Write supervisor RED tests**

Use executable fixture scripts, not mocks, to prove argv is not shell-expanded,
startup token is not logged, socket cleanup, handshake timeout, healthy start,
unexpected exit restart with bounded backoff, quarantine after three failures,
drain result propagation, and unrelated-process survival.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_plugin_supervisor -v`

Expected: import failure for `app.core.plugin_supervisor`.

- [ ] **Step 3: Implement safe process launch**

Launch with `asyncio.create_subprocess_exec` and a fixed argv list. Set only
documented `TPX_*` environment variables, create socket directories mode 0700,
capture bounded stdout/stderr lines, and sanitize token-like values.

- [ ] **Step 4: Implement monitoring and lifecycle**

Poll handshake/health until the startup deadline, monitor exits, apply bounded
backoff, and quarantine after `restart_limit`. Drain before TERM; use KILL only
after the shutdown deadline and record all unfinished task IDs as interrupted.

- [ ] **Step 5: Verify GREEN**

Run: `python3 -m unittest tests.test_plugin_supervisor -v`

Expected: all supervisor tests pass and no child process remains.

- [ ] **Step 6: Commit**

```bash
git add app/core/plugin_supervisor.py tests/test_plugin_supervisor.py tests/fixtures/plugin_processes
git commit -m "feat(core): supervise isolated Feature processes"
```

### Task 7: Transactional plugin manager lifecycle

**Files:**
- Create: `app/core/plugin_manager.py`
- Test: `tests/test_plugin_manager.py`

**Interfaces:**
- Consumes: artifact verifier, store, supervisor, router, and journal.
- Produces async `install`, `enable`, `disable`, `update`, `rollback`, `remove`,
  `status`, `doctor`, and `restore_active` methods.
- Produces: `PluginOperationResult(state, plugin_id, version, message, details)`.

- [ ] **Step 1: Write lifecycle RED tests**

Cover successful install, checksum failure, incompatible core API, pip failure,
config failure, handshake failure, missing capability, command conflict,
enable/disable without core restart, update shadow start, drain timeout with
interrupted tasks, atomic route switch, stabilization failure rollback,
explicit rollback, remove refusal while required, and startup quarantine.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_plugin_manager -v`

Expected: import failure for `app.core.plugin_manager`.

- [ ] **Step 3: Implement private venv installation**

Create `<release>/venv` with `python -m venv`; invoke that venv's pip with
`--no-index --find-links <release>/wheelhouse <release>/plugin.whl`. Capture
bounded output, enforce `install_timeout`, and delete a failed staged release.

- [ ] **Step 4: Implement activation transactions**

Start shadow, verify handshake/health, build a candidate route snapshot, drain
the old process, switch routes and `active.json`, observe stabilization, then
stop the old process. Reverse every completed step on failure.

- [ ] **Step 5: Implement status, doctor, and restore**

Report installed/active versions, source SHA, process state, health, provided
and missing capabilities, pending events, last error, and rollback target.
Restore active releases independently so one corrupt plugin cannot stop core.

- [ ] **Step 6: Verify GREEN**

Run: `python3 -m unittest tests.test_plugin_manager -v`

Expected: all lifecycle tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/core/plugin_manager.py tests/test_plugin_manager.py
git commit -m "feat(core): manage transactional Feature lifecycle"
```

### Task 8: Telegram plugin control plane and dynamic gateway

**Files:**
- Create: `app/handlers/__init__.py`
- Create: `app/handlers/plugin_handler.py`
- Modify: `app/115bot.py`
- Test: `tests/test_plugin_handler.py`
- Test: `tests/test_bot_runtime_startup.py`

**Interfaces:**
- Consumes: `PluginManager` and router command/callback snapshots.
- Produces: `/plugin` administrator command.
- Produces: generic unknown-command and callback gateways that dispatch typed
  envelopes and render validated response actions.

- [ ] **Step 1: Write handler RED tests**

Prove administrator authorization, exact parsing for all lifecycle subcommands,
no shell syntax, progress/final messages, dynamic command routing after install,
route removal after disable, callback namespace routing, response-action
validation, Telegram length bounds, and manager failures returning sanitized
errors without stopping polling.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_plugin_handler tests.test_bot_runtime_startup -v`

Expected: `/plugin` and dynamic gateways are absent.

- [ ] **Step 3: Implement the control handler**

Use `context.args`, an explicit subcommand dispatch dictionary, and one
operation lock per plugin. Never pass user input to a shell. Restrict lifecycle
methods to `allowed_user` and render status from typed manager results.

- [ ] **Step 4: Implement permanent dynamic gateways**

Register core commands first, then one generic command gateway and one generic
callback gateway. Query current immutable routes per update so a newly
installed Feature is immediately reachable without Telegram handler reload.

- [ ] **Step 5: Wire core startup and shutdown**

Construct `PluginManager` from core config, store it in `application.bot_data`,
call `restore_active()` after Telegram startup, and drain/stop all Features in
the application's shutdown callback.

- [ ] **Step 6: Verify GREEN**

Run: `python3 -m unittest tests.test_plugin_handler tests.test_bot_runtime_startup -v`

Expected: tests pass and no in-process business module is loaded by default.

- [ ] **Step 7: Commit**

```bash
git add app/handlers app/115bot.py tests/test_plugin_handler.py tests/test_bot_runtime_startup.py
git commit -m "feat(core): control hot-pluggable Features from Telegram"
```

### Task 9: Reference echo Feature and no-restart end-to-end proof

**Files:**
- Create: `examples/echo_feature/manifest.yaml`
- Create: `examples/echo_feature/config.schema.json`
- Create: `examples/echo_feature/config.default.yaml`
- Create: `examples/echo_feature/pyproject.toml`
- Create: `examples/echo_feature/src/telepiplex_echo/__init__.py`
- Create: `examples/echo_feature/src/telepiplex_echo/runtime.py`
- Create: `tests/test_plugin_runtime_e2e.py`

**Interfaces:**
- Provides exclusive capability `demo.echo` and Telegram command `/echo`.
- Implements lifecycle health/drain/shutdown through the SDK.

- [ ] **Step 1: Write E2E RED test**

Start a core manager in a temporary root, record the current core PID, build and
install echo v1, call `/echo`, update to v2 with an in-flight request, observe
drain and route switch, rollback to v1, disable, enable, remove, and assert the
core PID never changes.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_plugin_runtime_e2e -v`

Expected: echo artifact/source is absent.

- [ ] **Step 3: Implement the reference Feature**

Return a typed `send_message` action from the command handler and a JSON result
from `demo.echo`. Include a deterministic delay option so the test can hold one
request in flight during update.

- [ ] **Step 4: Build SDK and plugin wheels for the fixture**

Use isolated temporary build directories. Put both wheels in the `.tpx`
wheelhouse so the same offline artifact is installed by the manager.

- [ ] **Step 5: Verify GREEN**

Run: `python3 -m unittest tests.test_plugin_runtime_e2e -v`

Expected: all lifecycle states pass, the core PID is constant, and no child
process survives cleanup.

- [ ] **Step 6: Commit**

```bash
git add examples/echo_feature tests/test_plugin_runtime_e2e.py
git commit -m "test(core): prove no-restart Feature lifecycle"
```

### Task 10: Core-only Docker runtime and full verification

**Files:**
- Modify: `Dockerfile`
- Modify: `Dockerfile.local`
- Modify: `docker-compose.yaml`
- Modify: `README.md`
- Modify: `README_EN.md`
- Test: `tests/test_deployment_contract.py`
- Test: `tests/test_telepiplex_core_surface.py`

**Interfaces:**
- Produces a core-only image with venv support, no business Feature source, and
  persistent `/config/plugins` storage.

- [ ] **Step 1: Write deployment RED tests**

Assert the image contains core/SDK/installer only, includes the runtime packages
needed for venv installation, declares `/config`, does not list business
modules as defaults, and documents `.tpx` build/install/update/rollback flows.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_deployment_contract tests.test_telepiplex_core_surface -v`

Expected: assertions fail against the old in-process module docs/config.

- [ ] **Step 3: Update Docker and documentation**

Install core dependencies, copy `app`, `sdk`, and `tools`, create
`/config/plugins` with the runtime user permissions, expose no Feature ports,
and keep `CMD ["python", "115bot.py"]`. Document that Docker always pulls core
and Features are installed at runtime.

- [ ] **Step 4: Run the full verification matrix**

Run:

```bash
python3 -m unittest discover -s tests -t . -q
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
python3 -m pip check
git diff --check
```

Also compile every tracked Python file in memory, parse all YAML/JSON manifests
and schemas, scan `feature/telepiplex-core` for business module entry points,
and confirm the other Feature worktrees remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile Dockerfile.local docker-compose.yaml README.md README_EN.md tests/test_deployment_contract.py tests/test_telepiplex_core_surface.py
git commit -m "feat(core): ship core-only hot Feature runtime"
```

## Follow-on Migration Plans

After this plan is green, write and execute one plan per source branch in this
order:

1. `feature/115`: package `download.provider` and `storage.provider`.
2. `feature/media-search`: replace Telegram/core imports with command envelopes
   and `download.provider` RPC.
3. `feature/renaming`: consume durable `download.completed`, call
   `storage.provider`, and publish `media.organized`.
4. `feature/plex-management`: consume `media.organized`, preserve interrupted
   job semantics, and expose `plex.management`.
5. Cross-branch artifact matrix: build all four `.tpx` files and run the entire
   business flow in a core-only container without `main`.
