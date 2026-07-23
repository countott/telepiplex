# Open115 Root-Relative Save Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Telegram-incompatible `/`-prefixed save-directory input with one canonical root-relative path contract and publish the reviewed Open115 1.2.2 follow-up.

**Architecture:** Centralize path validation and canonicalization in `directories.py`, then reuse it from both persistent configuration normalization and the Telegram directory editor. Keep stored paths root-relative while preserving the existing download boundary that prepends `/` before calling the 115 API.

**Tech Stack:** Python 3.12, `unittest`, JSON Schema, YAML, Telepiplex Feature SDK, GitHub Actions Feature release workflow.

## Global Constraints

- Modify only `feature/download`; do not change Telepiplex command routing.
- Accept `series/live action` and `series/live action/`; store both as `series/live action`.
- Reject leading `/`, empty segments, `.` segments, `..` segments, multiline values, and canonical duplicates.
- Preserve case and spaces inside path segments.
- Keep 115 API paths absolute by retaining `_start_download` boundary normalization.
- Do not add legacy absolute-path migration because the deployed `save_directories` is empty.
- Release version is exactly `1.2.2`; immutable `1.2.1` was an intermediate release superseded after code review.

---

### Task 1: Canonical root-relative configuration contract

**Files:**
- Modify: `tests/test_feature_runtime.py`
- Modify: `src/telepiplex_download/directories.py`
- Modify: `config.schema.json`

**Interfaces:**
- Consumes: `_single_line(value, field="path") -> str` from `directories.py`.
- Produces: `normalize_save_directory_path(value: object) -> str`.
- Produces: `normalize_save_directories(value: object) -> list[dict[str, str]]` with canonical root-relative paths.

- [ ] **Step 1: Write failing persistence and Schema tests**

Add these focused tests to `FeatureConfigStoreTest`:

```python
def test_save_directory_writeback_normalizes_root_relative_paths(self):
    from telepiplex_download.config_store import FeatureConfigStore

    with tempfile.TemporaryDirectory() as directory:
        store = FeatureConfigStore(Path(directory) / "config.yaml")
        updated = store.write_save_directories([
            {"name": "剧集", "path": " series/live action/ "},
            {"name": "电影", "path": "movies"},
        ])

        self.assertEqual(updated["save_directories"], [
            {"name": "剧集", "path": "series/live action"},
            {"name": "电影", "path": "movies"},
        ])

def test_save_directory_writeback_rejects_command_and_unsafe_paths(self):
    from telepiplex_download.config_store import FeatureConfigStore

    invalid_paths = (
        "/series",
        "/",
        "series//live action",
        ".",
        "..",
        "./series",
        "series/../live action",
        "series/./live action",
    )
    with tempfile.TemporaryDirectory() as directory:
        store = FeatureConfigStore(Path(directory) / "config.yaml")
        for value in invalid_paths:
            with self.subTest(value=value), self.assertRaises(ValueError):
                store.write_save_directories([{"name": "剧集", "path": value}])
```

Update the existing `test_save_directory_writeback_preserves_config_and_private_permissions` fixture to write `series/` and `movies`, then assert `series` and `movies` are stored. Update the existing invalid-entry table so `series` is valid syntax and `/series` is the invalid command-style case.

Extend `FeatureSourceContractTest.test_schema_declares_custom_config_command_registered_by_manifest` with:

```python
path_pattern = schema["properties"]["save_directories"]["items"][
    "properties"
]["path"]["pattern"]
for value in ("series/live action", "series/live action/"):
    self.assertIsNotNone(re.fullmatch(path_pattern, value))
for value in ("/series", "/", "series//live", ".", "series/../live"):
    self.assertIsNone(re.fullmatch(path_pattern, value))
```

Add `import re` to the test module.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest \
  tests.test_feature_runtime.FeatureConfigStoreTest.test_save_directory_writeback_normalizes_root_relative_paths \
  tests.test_feature_runtime.FeatureConfigStoreTest.test_save_directory_writeback_rejects_command_and_unsafe_paths \
  tests.test_feature_runtime.FeatureSourceContractTest.test_schema_declares_custom_config_command_registered_by_manifest -v
```

Expected: the canonicalization test fails because relative paths are rejected; invalid `/series` and Schema cases also expose the old absolute-path contract.

- [ ] **Step 3: Implement the minimal shared path normalizer**

Add to `src/telepiplex_download/directories.py`:

```python
def normalize_save_directory_path(value) -> str:
    path = _single_line(value, field="path")
    if path.startswith("/"):
        raise ValueError(
            "download save directory path must start from the 115 root folder "
            "without a leading slash"
        )
    path = path.rstrip("/")
    parts = path.split("/")
    if not path or any(not part or part in {".", ".."} for part in parts):
        raise ValueError(
            "download save directory path must contain safe root-relative segments"
        )
    return path
```

Replace the direct path `_single_line` and `startswith("/")` check in `normalize_save_directories` with:

```python
path = normalize_save_directory_path(item.get("path"))
```

Change `config.schema.json` path validation to:

```json
"path": {
  "type": "string",
  "pattern": "^(?!/)(?!\\s)(?!.*\\s/?$)(?!.*//)(?!.*(?:^|/)\\.\\.?(?:/|$))[^\\r\\n/]+(?:/[^\\r\\n/]+)*/?$"
}
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the Step 2 command again.

Expected: all three tests pass.

- [ ] **Step 5: Commit the configuration contract**

```bash
git add tests/test_feature_runtime.py src/telepiplex_download/directories.py config.schema.json
git commit -m "fix(download): use root-relative save paths"
```

---

### Task 2: Telegram input, prompts, and download boundary

**Files:**
- Modify: `tests/test_feature_runtime.py`
- Modify: `src/telepiplex_download/service.py`

**Interfaces:**
- Consumes: `normalize_save_directory_path(value) -> str` from Task 1.
- Produces: `_directory_path(value, directories, exclude_index=None) -> str` using the shared contract.
- Preserves: `_start_download(payload, call_context)` converts a stored root-relative path to an API absolute path.

- [ ] **Step 1: Write failing Telegram workflow tests**

Update `test_directory_working_copy_add_edit_delete_and_save` so its original paths and user messages are root-relative. Send `movies/live action/` when adding and `tv/live action/` when editing, then assert the saved paths are `movies/live action` and `tv/live action`.

Update every `save_directories` fixture used by configuration, pagination, callback, and `/magnet` tests from `/Name` to `Name`. Keep direct `download.provider` request payloads named `selected_path` absolute because they exercise the existing capability boundary rather than the Telegram configuration contract.

Update `test_directory_input_rejects_invalid_and_duplicate_values` to:

```python
original = [{"name": "剧集", "path": "series/live action"}]
```

After entering the new directory name, assert `/movies` produces an error containing `不要以 / 开头`, assert `series/live action/` produces the duplicate-path error, and then submit `movies` successfully.

After `config:add` advances from name to path, assert the prompt contains both `115 根文件夹` and `series/live action`.

Update `test_magnet_command_uses_session_and_namespaced_callback` to configure `series/live action`, await the spawned task after the callback, and assert:

```python
self.assertEqual(self.client.added[0][1], "/series/live action")
```

- [ ] **Step 2: Run the workflow tests and verify RED**

Run:

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest \
  tests.test_feature_runtime.DownloadFeatureTest.test_directory_working_copy_add_edit_delete_and_save \
  tests.test_feature_runtime.DownloadFeatureTest.test_directory_input_rejects_invalid_and_duplicate_values \
  tests.test_feature_runtime.DownloadFeatureTest.test_magnet_command_uses_session_and_namespaced_callback -v
```

Expected: directory add/edit tests fail because `_directory_path` still requires `/`; the prompt assertion fails on the old absolute-path wording.

- [ ] **Step 3: Reuse the shared normalizer and update prompts**

Import the helper in `service.py`:

```python
from .directories import normalize_save_directories, normalize_save_directory_path
```

Replace `_directory_path` parsing with:

```python
raw_path = str(value or "").strip()
if raw_path.startswith("/"):
    raise ValueError(
        "目录路径请从 115 根文件夹开始填写，不要以 / 开头。"
    )
try:
    path = normalize_save_directory_path(raw_path)
except ValueError as exc:
    raise ValueError(
        "目录路径只能包含有效的根目录相对路径段。"
    ) from exc
```

Keep the existing canonical duplicate check after this parsing block.

Change both add and edit prompts to:

```text
请从 115 根文件夹开始填写保存路径，例如 series/live action（末尾 / 可省略）。
```

- [ ] **Step 4: Run the workflow tests and full Open115 suite**

Run the Step 2 command, then:

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest discover -s tests -t . -v
```

Expected: the three workflow tests pass and the full suite passes with no failures.

- [ ] **Step 5: Commit the Telegram behavior**

```bash
git add tests/test_feature_runtime.py src/telepiplex_download/service.py
git commit -m "fix(download): accept Telegram-safe directory paths"
```

---

### Task 3: Version 1.2.2, documentation, build, and publication

**Files:**
- Modify: `tests/test_feature_runtime.py`
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Create: `dist/download-1.2.2.tpx` outside the Feature branch worktree using the Telepiplex builder.

**Interfaces:**
- Consumes: canonical root-relative path behavior from Tasks 1 and 2.
- Produces: immutable Open115 `1.2.2` identity and `download-v1.2.2` GitHub Release/catalog entry.

- [ ] **Step 1: Write the failing version and documentation contract assertions**

Change the existing source contract assertions to expect:

```python
self.assertEqual(manifest["version"], "1.2.2")
self.assertEqual(project["project"]["version"], "1.2.2")
self.assertIn("series/live action", readme)
self.assertIn("不要以 / 开头", readme)
```

- [ ] **Step 2: Run the source contract test and verify RED**

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_feature_runtime.FeatureSourceContractTest -v
```

Expected: failure because manifest and project versions, plus the README build artifact path, still identify immutable 1.2.1.

- [ ] **Step 3: Bump version and document the new path rule**

Set `manifest.yaml` and `pyproject.toml` to `1.2.2`. Update README to state that Telegram directory paths start from the 115 root folder, use `series/live action` as the example, may end with one `/`, and must not start with `/` because Telegram reserves that prefix for commands.

- [ ] **Step 4: Run all verification and commit the release identity**

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest discover -s tests -t . -v
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile src/telepiplex_download/*.py
git diff --check
```

Expected: all tests pass and compilation and diff checks exit zero.

Commit the release identity:

```bash
git add tests/test_feature_runtime.py manifest.yaml pyproject.toml README.md
git commit -m "chore(download): prepare 1.2.2"
```

- [ ] **Step 5: Build, push, tag, and verify the remote release**

Build from the Telepiplex worktree using the verified source commit after the release commit:

```bash
PYTHONPATH=.:sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tools/build_tpx.py \
  /Users/young/Documents/telepiplex/features/download \
  /Users/young/Documents/telepiplex/dist/download-1.2.2.tpx
```

Expected: all tests pass, compilation and diff checks exit zero, and `verify_tpx` reports Open115 1.2.2 with the current `feature/download` source commit.

```bash
git push origin feature/download
```

Create annotated tag `download-v1.2.2` on the current `main` release-infrastructure commit and push it. Wait for `Publish one Telepiplex Feature` to finish successfully. Verify the public Release contains `download-1.2.2.tpx`, `catalog.yaml`, and `catalog.yaml.sha256`; download and run Telepiplex `verify_tpx`; confirm its source commit equals remote `feature/download`; finally confirm `origin/catalog` contains Open115 1.2.2 with the same SHA-256 and source commit.

### Code-review follow-up included in 1.2.2

The immutable 1.2.1 publication exposed two review findings that are resolved before 1.2.2 publication:

- Permit at most one optional trailing slash; reject `series//` and `series///` instead of silently canonicalizing them.
- Normalize and persist disk configuration during runtime startup, and reject entries that become duplicates after canonicalization.
