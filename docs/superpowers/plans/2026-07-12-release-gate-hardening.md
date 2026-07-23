# Release Gate Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three verified Telepiplex Feature release blockers while preserving the current Host API and all Feature business behavior.

**Architecture:** Enforce active-consumer compatibility while the router snapshot is still only prepared, classify untyped Feature failures as retryable, and validate dependency isolation at requirements input, plugin wheel metadata, and final wheelhouse boundaries. Each change is protected by a regression test that must fail before production code changes.

**Tech Stack:** Python 3.12, `asyncio`, `unittest`, `packaging>=24,<27`, immutable `.tpx` ZIP artifacts, wheel `METADATA`, Unix RPC.

## Global Constraints

- Modify only `main`.
- Do not change Host API version, Feature manifests, or Feature business logic.
- Do not add an RPC retryability field.
- Preserve ordinary named third-party dependencies.
- Reject every `telepiplex-*` distribution except `telepiplex-plugin-sdk`.
- Build all four Feature artifacts from clean source worktrees after implementation.

---

### Task 1: Preserve Consumers During Provider Update

**Files:**
- Modify: `tests/test_plugin_manager.py`
- Modify: `app/runtime/capability_router.py:63-81`

**Interfaces:**
- Consumes: `CapabilityRouter.prepare_activation(plugin_id, manifest, client) -> PreparedRoutes`
- Produces: routing error code `dependent_capability_lost` before route commit or old-process drain.

- [ ] **Step 1: Write the failing regression test**

Add `test_update_rejects_provider_capability_loss_that_blocks_consumer`. Install Provider v1 with `download.provider` and `storage.provider`, install a consumer requiring `storage.provider`, then update Provider v2 with only `download.provider`. Assert `PluginOperationError.code == "dependent_capability_lost"`, Provider v1 remains active and healthy, the consumer remains active, and its command route remains registered.

- [ ] **Step 2: Run the targeted test and verify RED**

Run:

```bash
PYTHONPATH=app:sdk/src python3 -m unittest tests.test_plugin_manager.PluginManagerTest.test_update_rejects_provider_capability_loss_that_blocks_consumer -v
```

Expected: FAIL because update currently returns success.

- [ ] **Step 3: Implement the prepared-snapshot invariant**

In `prepare_activation()`, compare current unblocked registrations with the candidate `snapshot.blocked`. If any previously unblocked plugin other than the plugin being activated becomes blocked, raise:

```python
RoutingError(
    "dependent_capability_lost",
    "activation would block active Features: consumer (storage.provider)",
)
```

Perform this check before returning `PreparedRoutes`, leaving the current snapshot untouched.

- [ ] **Step 4: Run the targeted manager tests and verify GREEN**

Run:

```bash
PYTHONPATH=app:sdk/src python3 -m unittest tests.test_plugin_manager -q
```

Expected: all manager tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_plugin_manager.py app/runtime/capability_router.py
git commit -m "fix(runtime): preserve consumers during provider updates"
```

### Task 2: Keep Untyped Internal Event Failures Retryable

**Files:**
- Modify: `tests/test_event_dispatcher.py`
- Modify: `app/runtime/event_dispatcher.py:10-13`

**Interfaces:**
- Consumes: SDK error code `internal_error` and `EventDispatcher.deliver_once()`.
- Produces: `internal_error` deliveries remain pending without poison-attempt consumption.

- [ ] **Step 1: Write the failing retry regression test**

Add a client that returns `ContractError("internal_error", "temporary failure")` twice and succeeds on the third call. With `max_attempts=2`, assert the event remains pending and has no dead letter after two calls, then is acknowledged on the third call. Change the existing deterministic poison fixture to return `invalid_request` so dead-letter behavior remains covered.

- [ ] **Step 2: Run the targeted test and verify RED**

Run:

```bash
PYTHONPATH=app:sdk/src python3 -m unittest tests.test_event_dispatcher.EventDispatcherTest.test_internal_error_does_not_consume_poison_attempt_budget -v
```

Expected: FAIL because the event is dead-lettered after the second call.

- [ ] **Step 3: Remove `internal_error` from terminal poison codes**

Keep explicit deterministic codes in `_POISON_CODES`; remove only `internal_error`.

- [ ] **Step 4: Run dispatcher tests and verify GREEN**

Run:

```bash
PYTHONPATH=app:sdk/src python3 -m unittest tests.test_event_dispatcher -q
```

Expected: all dispatcher tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_event_dispatcher.py app/runtime/event_dispatcher.py
git commit -m "fix(runtime): retry untyped Feature event failures"
```

### Task 3: Close Feature Dependency Isolation Bypasses

**Files:**
- Modify: `requirements.txt`
- Modify: `tests/test_feature_builder.py`
- Modify: `tools/build_feature.py:1-140`

**Interfaces:**
- Consumes: `requirements-feature.txt`, built `plugin.whl`, and final `wheelhouse/*.whl`.
- Produces: `FeatureBuildError` for unsafe requirement sources, sibling `Requires-Dist`, malformed wheel metadata, or sibling wheels.

- [ ] **Step 1: Write failing dependency-boundary tests**

Add tests proving:

```python
validate_feature_requirements("-r sibling.txt\n")
validate_feature_requirements("https://example.test/telepiplex_download.whl\n")
validate_feature_requirements("./telepiplex_download.whl\n")
```

all raise `FeatureBuildError`. Add helper-generated wheels whose `METADATA` declares `Requires-Dist: telepiplex-download` or `Name: telepiplex-download`, and assert plugin metadata and final wheelhouse validation reject them. Retain a positive assertion for `requests>=2` and `telepiplex-plugin-sdk==1.0.0`.

- [ ] **Step 2: Run builder tests and verify RED**

Run:

```bash
PYTHONPATH=app:sdk/src python3 -m unittest tests.test_feature_builder -q
```

Expected: new bypass tests fail because indirections and wheel metadata are not inspected.

- [ ] **Step 3: Implement fail-closed dependency validation**

Declare `packaging>=24,<27` in `requirements.txt`, then add focused helpers in `tools/build_feature.py`:

```python
_DISTRIBUTION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _validate_distribution_name(name: str):
    normalized = re.sub(r"[-_.]+", "-", name).casefold()
    if normalized.startswith("telepiplex-") and normalized != "telepiplex-plugin-sdk":
        raise FeatureBuildError(
            f"forbidden Feature distribution dependency: {normalized}"
        )


def validate_feature_requirements(source: str):
    for raw in str(source or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement as exc:
            raise FeatureBuildError(
                "Feature requirements must use named distributions"
            ) from exc
        if requirement.url is not None:
            raise FeatureBuildError(
                "Feature requirements must not use direct references"
            )
        _validate_distribution_name(requirement.name)


def _wheel_metadata(path: Path):
    with zipfile.ZipFile(path) as wheel:
        members = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
        if len(members) != 1:
            raise FeatureBuildError("wheel must contain exactly one METADATA member")
        return Parser().parsestr(wheel.read(members[0]).decode("utf-8"))


def validate_plugin_wheel(path: Path):
    metadata = _wheel_metadata(path)
    for requirement in metadata.get_all("Requires-Dist", []):
        match = _DISTRIBUTION_NAME.match(requirement.strip())
        if match is None:
            raise FeatureBuildError("plugin wheel has an invalid Requires-Dist")
        _validate_distribution_name(match.group(0))


def validate_wheelhouse(path: Path):
    for wheel in sorted(path.glob("*.whl")):
        name = str(_wheel_metadata(wheel).get("Name") or "").strip()
        if not name:
            raise FeatureBuildError("wheel metadata is missing Name")
        _validate_distribution_name(name)
```

Reject pip option lines, URL/direct references, local archive/path forms, and sibling distribution names. Read exactly one `.dist-info/METADATA` per wheel with `zipfile` and `email.parser`; validate plugin `Requires-Dist` headers and every final wheelhouse `Name` header. Call plugin validation after `_run_wheel()` and wheelhouse validation immediately before `build_tpx()`.

- [ ] **Step 4: Run builder tests and build the echo artifact**

Run:

```bash
PYTHONPATH=app:sdk/src python3 -m unittest tests.test_feature_builder -q
```

Expected: all builder tests pass, including the existing installable echo `.tpx` build.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt tests/test_feature_builder.py tools/build_feature.py
git commit -m "fix(runtime): close Feature dependency isolation bypasses"
```

### Task 4: Release Verification

**Files:**
- Verify only; no planned production changes.

**Interfaces:**
- Consumes: current Telepiplex branch and all four clean Feature worktrees.
- Produces: evidence-backed push/no-push verdict.

- [ ] **Step 1: Run full Telepiplex verification**

```bash
PYTHONPATH=app:sdk/src python3 -m unittest discover -s tests -t . -q
PYTHONPATH=app:sdk/src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
python3 -m pip check
git diff --check
```

- [ ] **Step 2: Run all Feature test suites**

Run each Feature worktree with `PYTHONPATH=src:../../sdk/src` and `python3 -m unittest discover -s tests -t . -q`.

- [ ] **Step 3: Build and verify all four `.tpx` artifacts**

Use `tools/build_feature.py` against download, search, rename, and sync. Verify checksums, manifest source branch/commit, member allowlist, and absence of sibling Feature wheels.

- [ ] **Step 4: Run the real runtime matrix**

Install all four artifacts into one fresh Telepiplex manager using a short Unix runtime path. Assert all Features are healthy, capability and command routes exist, download removal is dependency-protected, a second manager restores all Features from persisted state, and Telepiplex PID remains unchanged.

- [ ] **Step 5: Request independent read-only review**

Review only the three release-gate fixes and their regression coverage. Any Critical or Important issue blocks publication.

- [ ] **Step 6: Report exact publication state**

Confirm clean worktrees, remote divergence, exact branch heads, remaining Docker-environment gap, and whether the branches are ready to push. Do not push unless explicitly requested.
