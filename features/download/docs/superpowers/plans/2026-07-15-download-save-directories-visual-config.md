# download Save Directories Visual Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Telegram `/config` workflow that manages `save_directories` one entry at a time, saves the final list atomically, updates the running Feature immediately, and releases download 1.2.0.

**Architecture:** Keep the existing download custom command and operation model. Add shared directory normalization in a focused module, extend `FeatureConfigStore` with a field-scoped atomic writer, and extend `DownloadFeature` with a staged paginated directory editor whose working copy is committed only on explicit confirmation. Existing `/auth`, download, and Token persistence behavior remains independent.

**Tech Stack:** Python 3.9+, `asyncio`, PyYAML, `unittest`, Telepiplex Plugin SDK, Telepiplex `tools/build_feature.py`.

## Global Constraints

- Modify only `feature/download` source and its local build output.
- Preserve the current `save_directories` item shape: `{name: string, path: absolute string}`.
- `/auth` continues to open the authorization chooser directly.
- `/config` opens the new configuration home with authorization and save-directory choices.
- Directory changes remain staged until “保存并完成”; exit, `/q`, and timeout discard them.
- Saving replaces only `save_directories` and preserves Token, auth mode, and all other configuration keys.
- No Token or configuration value may be included in errors or logs.
- Pagination must keep every Telegram keyboard at ten rows or fewer.
- Release version is `1.2.0`; `config_schema_version` remains `1` because the schema shape is unchanged.

---

### Task 1: Directory validation and atomic persistence

**Files:**
- Create: `src/telepiplex_download/directories.py`
- Modify: `src/telepiplex_download/config_store.py`
- Modify: `tests/test_feature_runtime.py`

**Interfaces:**
- Produces: `normalize_save_directories(value: object) -> list[dict[str, str]]`.
- Produces: `FeatureConfigStore.write_save_directories(directories: object) -> dict`.
- Preserves: `FeatureConfigStore.write_tokens(...)` and the existing `0600` atomic replacement behavior.

- [ ] **Step 1: Write failing normalization and persistence tests**

Add tests that call the real store and assert normalized output, preservation, validation, and file mode:

```python
def test_save_directory_writeback_preserves_tokens_and_uses_private_permissions(self):
    from telepiplex_download.config_store import FeatureConfigStore

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.yaml"
        path.write_text(
            "access_token: access\nrefresh_token: refresh\ncustom: keep\n"
            "save_directories: []\n",
            encoding="utf-8",
        )
        store = FeatureConfigStore(path)

        updated = store.write_save_directories([
            {"name": " 剧集 ", "path": " /Series "},
            {"name": "电影", "path": "/Movies"},
        ])

        self.assertEqual(updated["save_directories"], [
            {"name": "剧集", "path": "/Series"},
            {"name": "电影", "path": "/Movies"},
        ])
        self.assertEqual(updated["access_token"], "access")
        self.assertEqual(updated["refresh_token"], "refresh")
        self.assertEqual(updated["custom"], "keep")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

def test_save_directory_writeback_rejects_invalid_and_duplicate_entries(self):
    from telepiplex_download.config_store import FeatureConfigStore

    with tempfile.TemporaryDirectory() as directory:
        store = FeatureConfigStore(Path(directory) / "config.yaml")
        invalid = (
            None,
            [{"name": "", "path": "/Series"}],
            [{"name": "剧集", "path": "Series"}],
            [{"name": "剧集", "path": "/A"}, {"name": "剧集", "path": "/B"}],
            [{"name": "A", "path": "/Series"}, {"name": "B", "path": "/Series"}],
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                store.write_save_directories(value)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_feature_runtime.FeatureConfigStoreTest.test_save_directory_writeback_preserves_tokens_and_uses_private_permissions \
  tests.test_feature_runtime.FeatureConfigStoreTest.test_save_directory_writeback_rejects_invalid_and_duplicate_entries -v
```

Expected: both tests fail because `write_save_directories` does not exist.

- [ ] **Step 3: Implement shared normalization and the field-scoped writer**

Create `directories.py` with a defensive normalizer:

```python
from __future__ import annotations


def _single_line(value, *, field: str) -> str:
    text = str(value or "").strip()
    if not text or "\n" in text or "\r" in text:
        raise ValueError(f"download save directory {field} must be one non-empty line")
    return text


def normalize_save_directories(value) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("download save_directories must be a list")
    normalized = []
    names = set()
    paths = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"name", "path"}:
            raise ValueError("download save directory must contain only name and path")
        name = _single_line(item.get("name"), field="name")
        path = _single_line(item.get("path"), field="path")
        if not path.startswith("/"):
            raise ValueError("download save directory path must be absolute")
        if name in names or path in paths:
            raise ValueError("download save directory name and path must be unique")
        names.add(name)
        paths.add(path)
        normalized.append({"name": name, "path": path})
    return normalized
```

Import the function in `config_store.py` and add:

```python
def write_save_directories(self, directories) -> dict:
    normalized = normalize_save_directories(directories)
    with self._lock:
        config = self._read_unlocked()
        config["save_directories"] = normalized
        self._write_unlocked(config)
        return dict(config)
```

- [ ] **Step 4: Run the persistence tests and verify GREEN**

Run the Step 2 command again.

Expected: both tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/telepiplex_download/directories.py \
  src/telepiplex_download/config_store.py tests/test_feature_runtime.py
git commit -m "feat(download): persist visual save directories"
```

### Task 2: Staged visual directory manager

**Files:**
- Modify: `src/telepiplex_download/service.py`
- Modify: `tests/test_feature_runtime.py`

**Interfaces:**
- Consumes: `normalize_save_directories(value)` and `FeatureConfigStore.write_save_directories(...)` from Task 1.
- Produces: `/config` home callbacks under `download:config:*`.
- Produces: paginated directory callbacks `config:item:<index>` and `config:page:<page>` plus add/edit/delete/save actions.
- Preserves: `download:auth:*`, `download:path:*`, `download:exit`, `/auth`, `/magnet`, `/m`, and `/q`.

- [ ] **Step 1: Update the fake store and write failing entry/navigation tests**

Extend `FakeConfigStore` with `directory_writes` and `write_save_directories`:

```python
def __init__(self, config):
    self.config = dict(config)
    self.writes = []
    self.directory_writes = []
    self.fail_writes = False

def write_save_directories(self, directories):
    if self.fail_writes:
        raise RuntimeError("config=secret-value")
    normalized = normalize_save_directories(directories)
    self.config["save_directories"] = normalized
    self.directory_writes.append(normalized)
    return dict(self.config)
```

Add this test helper to `DownloadFeatureTest`:

```python
async def _open_directory_config(self):
    await self.feature.command({
        "command": "config", "user_id": 1, "chat_id": 10,
    })
    return await self.feature.callback({
        "payload": "config:directories", "user_id": 1, "chat_id": 10,
    })
```

Then replace the old test that expected `/config` and `/auth` to be identical:

```python
async def test_config_opens_home_while_auth_opens_authorization_directly(self):
    config = await self.feature.command({
        "command": "config", "user_id": 1, "chat_id": 10,
    })
    config_buttons = [
        button["callback_data"]
        for row in config["actions"][0]["data"]["keyboard"]
        for button in row
    ]
    self.assertEqual(config_buttons, [
        "download:config:auth",
        "download:config:directories",
        "download:exit",
    ])

    auth = await self.feature.command({
        "command": "auth", "user_id": 1, "chat_id": 10,
    })
    auth_buttons = [
        button["callback_data"]
        for row in auth["actions"][0]["data"]["keyboard"]
        for button in row
    ]
    self.assertEqual(auth_buttons, [
        "download:auth:direct", "download:auth:scan", "download:exit",
    ])

async def test_directory_list_is_paginated_with_bounded_keyboard(self):
    self.feature.config["save_directories"] = [
        {"name": f"目录{index}", "path": f"/Path{index}"}
        for index in range(7)
    ]
    await self.feature.command({
        "command": "config", "user_id": 1, "chat_id": 10,
    })
    response = await self.feature.callback({
        "payload": "config:directories", "user_id": 1, "chat_id": 10,
    })
    keyboard = response["actions"][0]["data"]["keyboard"]
    self.assertLessEqual(len(keyboard), 10)
    self.assertIn("download:config:page:1", str(keyboard))
    self.assertNotIn("目录6", str(keyboard))
```

- [ ] **Step 2: Run the entry/navigation tests and verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_feature_runtime.DownloadFeatureTest.test_config_opens_home_while_auth_opens_authorization_directly \
  tests.test_feature_runtime.DownloadFeatureTest.test_directory_list_is_paginated_with_bounded_keyboard -v
```

Expected: failures because `/config` still opens the auth chooser and `config:directories` is not handled.

- [ ] **Step 3: Implement the config home and paginated working copy**

In `service.py`:

- import `deepcopy`, `math.ceil`, and `normalize_save_directories`;
- add `DIRECTORY_PAGE_SIZE = 5`;
- route `command == "config"` to `_start_config_session(request)` and `command == "auth"` to `_start_auth_session(request)`;
- make the config session store `stage="config_home"`, `operation_id`, and, after entering directory management, `working_directories=deepcopy(current)` and `page=0`;
- render at most five directory rows, one optional pagination row, and three action rows for add/save/exit;
- make `config:auth` switch the same operation into the existing auth chooser;
- reject `config:*` callbacks whose session stage does not match.

The root keyboard must be exactly:

```python
[
    [{"text": "授权配置", "callback_data": "download:config:auth"}],
    [{"text": "保存目录", "callback_data": "download:config:directories"}],
    self._exit_row(),
]
```

The list keyboard must use absolute working-copy indices, not names or paths, in callback data.

- [ ] **Step 4: Run the entry/navigation tests and verify GREEN**

Run the Step 2 command again.

Expected: both tests pass.

- [ ] **Step 5: Write failing add, edit, delete, validation, cancel, and save tests**

Add focused async tests that exercise real command/callback/message sequences:

```python
async def test_directory_working_copy_add_edit_delete_and_save(self):
    self.feature.config.update({"save_directories": [
        {"name": "剧集", "path": "/Series"},
        {"name": "删除项", "path": "/Delete"},
    ]})
    self.feature.config_store.config.update(self.feature.config)
    await self._open_directory_config()

    await self.feature.callback({"payload": "config:add", "user_id": 1, "chat_id": 10})
    await self.feature.message({"text": "电影", "user_id": 1, "chat_id": 10})
    await self.feature.message({"text": "/Movies", "user_id": 1, "chat_id": 10})

    await self.feature.callback({"payload": "config:item:0", "user_id": 1, "chat_id": 10})
    await self.feature.callback({"payload": "config:edit:name", "user_id": 1, "chat_id": 10})
    await self.feature.message({"text": "电视剧", "user_id": 1, "chat_id": 10})

    await self.feature.callback({"payload": "config:item:1", "user_id": 1, "chat_id": 10})
    await self.feature.callback({"payload": "config:delete", "user_id": 1, "chat_id": 10})
    await self.feature.callback({"payload": "config:delete:confirm", "user_id": 1, "chat_id": 10})

    saved = await self.feature.callback({
        "payload": "config:save", "user_id": 1, "chat_id": 10,
    })
    expected = [
        {"name": "电视剧", "path": "/Series"},
        {"name": "电影", "path": "/Movies"},
    ]
    self.assertEqual(saved["session"]["state"], "close")
    self.assertEqual(self.feature.config_store.directory_writes, [expected])
    self.assertEqual(self.feature.config["save_directories"], expected)
    self.assertEqual(saved["operation"]["state"], "completed")
```

Add separate tests for validation, cancellation, timeout, and persistence failure:

```python
async def test_directory_input_rejects_relative_and_duplicate_paths(self):
    self.feature.config["save_directories"] = [
        {"name": "剧集", "path": "/Series"},
    ]
    await self._open_directory_config()
    await self.feature.callback({
        "payload": "config:add", "user_id": 1, "chat_id": 10,
    })
    await self.feature.message({
        "text": "电影", "user_id": 1, "chat_id": 10,
    })
    relative = await self.feature.message({
        "text": "Movies", "user_id": 1, "chat_id": 10,
    })
    duplicate = await self.feature.message({
        "text": "/Series", "user_id": 1, "chat_id": 10,
    })

    self.assertEqual(relative["session"]["state"], "open")
    self.assertEqual(duplicate["session"]["state"], "open")
    self.assertIn("绝对路径", relative["actions"][0]["text"])
    self.assertIn("重复", duplicate["actions"][0]["text"])
    self.assertEqual(self.feature.config_store.directory_writes, [])

async def test_directory_exit_and_q_discard_working_copy(self):
    for payload in ("exit", None):
        with self.subTest(payload=payload):
            await self._open_directory_config()
            await self.feature.callback({
                "payload": "config:add", "user_id": 1, "chat_id": 10,
            })
            await self.feature.message({
                "text": "电影", "user_id": 1, "chat_id": 10,
            })
            if payload:
                response = await self.feature.callback({
                    "payload": payload, "user_id": 1, "chat_id": 10,
                })
            else:
                response = await self.feature.command({
                    "command": "q", "user_id": 1, "chat_id": 10,
                })
            self.assertEqual(response["session"]["state"], "close")
            self.assertEqual(self.feature.config_store.directory_writes, [])

async def test_directory_session_timeout_discards_working_copy(self):
    from telepiplex_download import service

    with patch.object(service, "SESSION_TTL_SECONDS", 0):
        await self._open_directory_config()
        await asyncio.sleep(0.01)

    self.assertNotIn((10, 1), self.feature.sessions)
    self.assertEqual(self.feature.config_store.directory_writes, [])
    self.assertEqual(self.host.reports[-1]["state"], "cancelled")

async def test_directory_save_failure_retains_old_config_and_retry_state(self):
    old = [{"name": "剧集", "path": "/Series"}]
    self.feature.config["save_directories"] = old
    self.feature.config_store.config["save_directories"] = old
    self.feature.config_store.fail_writes = True
    await self._open_directory_config()
    await self.feature.callback({
        "payload": "config:add", "user_id": 1, "chat_id": 10,
    })
    await self.feature.message({
        "text": "电影", "user_id": 1, "chat_id": 10,
    })
    await self.feature.message({
        "text": "/Movies", "user_id": 1, "chat_id": 10,
    })

    response = await self.feature.callback({
        "payload": "config:save", "user_id": 1, "chat_id": 10,
    })

    self.assertEqual(response["session"]["state"], "open")
    self.assertEqual(response["operation"]["state"], "awaiting_input")
    self.assertEqual(self.feature.config["save_directories"], old)
    self.assertEqual(self.feature.sessions[(10, 1)]["stage"], "directory_list")
    self.assertNotIn("secret-value", str(response))
```

- [ ] **Step 6: Run the new behavior tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_feature_runtime.DownloadFeatureTest -v
```

Expected: the newly added directory management tests fail on unimplemented callbacks/stages while existing download behavior remains passing.

- [ ] **Step 7: Implement staged add/edit/delete/save behavior**

Implement these session stages:

```text
config_home
directory_list
directory_add_name
directory_add_path
directory_item
directory_edit_name
directory_edit_path
directory_delete_confirm
```

Use `normalize_save_directories` on each candidate working copy. On validation failure, keep the stage and working copy and show a value-free error. On add/edit/delete success, return to `directory_list`. On save:

```python
await self._report_operation(
    operation_id,
    state="running",
    stage="config_persistence",
    status_text="正在保存 115 目录配置。",
    control="exit",
)
try:
    if self.config_store:
        updated = await asyncio.to_thread(
            self.config_store.write_save_directories,
            working_directories,
        )
    else:
        updated = dict(self.config)
        updated["save_directories"] = normalize_save_directories(
            working_directories
        )
except Exception as exc:
    logger.error(
        "download_directory_config_write_failed "
        f"error={type(exc).__name__}"
    )
    # restore directory_list/awaiting_input and retain the working copy
else:
    self.config.clear()
    self.config.update(updated)
    # close the session and complete the operation
```

Generalize session expiry messaging so directory sessions report “目录配置已超时并退出。” while auth sessions keep their existing wording. Reschedule the existing expiry handle after every directory interaction.

- [ ] **Step 8: Run the download feature tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_feature_runtime.DownloadFeatureTest -v
```

Expected: all `DownloadFeatureTest` tests pass.

- [ ] **Step 9: Commit Task 2**

```bash
git add src/telepiplex_download/service.py tests/test_feature_runtime.py
git commit -m "feat(download): add visual save directory manager"
```

### Task 3: Documentation and 1.2.0 version contract

**Files:**
- Modify: `README.md`
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `tests/test_feature_runtime.py`

**Interfaces:**
- Produces: package and manifest version `1.2.0`.
- Preserves: `config_schema_version: 1` and `state_schema_version: 1`.

- [ ] **Step 1: Update the source-contract test first**

Change both expected versions in `FeatureSourceContractTest` from `1.1.0` to `1.2.0`, and assert the schema/state versions remain `1`.

- [ ] **Step 2: Run the source-contract test and verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_feature_runtime.FeatureSourceContractTest.test_schema_declares_custom_config_command_registered_by_manifest -v
```

Expected: failure because manifest and package are still `1.1.0`.

- [ ] **Step 3: Bump versions and update README**

Set:

```yaml
# manifest.yaml
version: 1.2.0
```

```toml
# pyproject.toml
version = "1.2.0"
```

Update README to explain that `/config` opens authorization and save-directory management, `/auth` opens authorization directly, and directory edits are staged until explicit save. Change the build example to:

```bash
python /opt/telepiplex/tools/build_feature.py . dist/download-1.2.0.tpx
```

- [ ] **Step 4: Run the source-contract test and verify GREEN**

Run the Step 2 command again.

Expected: pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add README.md manifest.yaml pyproject.toml tests/test_feature_runtime.py
git commit -m "chore(download): prepare 1.2.0"
```

### Task 4: Full verification, push, and immutable build

**Files:**
- Verify: all tracked files in `feature/download`.
- Create outside source worktree: `/Users/young/Documents/telepiplex/dist/download-1.2.0.tpx`.

**Interfaces:**
- Consumes: clean committed `feature/download` HEAD.
- Produces: pushed `origin/feature/download` and an immutable `.tpx` whose manifest source commit is the pushed HEAD.

- [ ] **Step 1: Run focused and full verification**

Run:

```bash
python3 -m unittest tests.test_feature_runtime -v
python3 -m unittest discover -s tests -t . -v
python3 -m py_compile src/telepiplex_download/*.py tests/test_feature_runtime.py
git diff --check
```

Expected: all tests pass, compilation succeeds, and `git diff --check` is silent.

- [ ] **Step 2: Confirm release metadata and source cleanliness**

Run:

```bash
python3 - <<'PY'
import tomllib
from pathlib import Path
import yaml

manifest = yaml.safe_load(Path("manifest.yaml").read_text(encoding="utf-8"))
project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
assert manifest["version"] == "1.2.0"
assert project["project"]["version"] == "1.2.0"
assert manifest["config_schema_version"] == 1
assert manifest["state_schema_version"] == 1
PY
git status --short --branch
```

Expected: assertions pass and the source worktree is clean.

- [ ] **Step 3: Push the exact feature branch**

```bash
git push origin feature/download
git rev-parse HEAD
git ls-remote --heads origin feature/download
```

Expected: local HEAD and the remote branch SHA are identical.

- [ ] **Step 4: Build the immutable artifact from the pushed clean commit**

Run from `/Users/young/Documents/telepiplex`:

```bash
python3 tools/build_feature.py \
  /Users/young/Documents/telepiplex/features/download \
  /Users/young/Documents/telepiplex/dist/download-1.2.0.tpx
```

Expected: the command prints the absolute artifact path and exits successfully.

- [ ] **Step 5: Verify artifact identity and checksum**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
import zipfile
import yaml

artifact = Path("/Users/young/Documents/telepiplex/dist/download-1.2.0.tpx")
assert artifact.is_file() and artifact.stat().st_size > 0
with zipfile.ZipFile(artifact) as package:
    manifest = yaml.safe_load(package.read("manifest.yaml"))
assert manifest["plugin_id"] == "download"
assert manifest["version"] == "1.2.0"
assert manifest["source"]["branch"] == "feature/download"
print(artifact)
print(manifest["source"]["commit"])
PY
shasum -a 256 /Users/young/Documents/telepiplex/dist/download-1.2.0.tpx
```

Expected: artifact metadata matches download 1.2.0 and the embedded commit equals the pushed branch SHA.
