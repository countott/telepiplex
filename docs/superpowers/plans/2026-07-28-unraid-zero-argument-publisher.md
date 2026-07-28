# telepiplex Unraid Zero-Argument Publisher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `telepiplex Publish` a zero-argument Unraid User Script that automatically reads and publishes every missing Host and Feature release identity.

**Architecture:** Keep the existing guarded Unraid Git workflow, but replace the manual Host-version argument with a strict parser for `app/115bot.py::get_version()`. Treat Host exactly like each Feature: publish its current tag only when absent, preserve immutable existing tags, push `main` first, then push every missing tag individually.

**Tech Stack:** Bash, Unraid User Scripts metadata, Python 3.12 `unittest`/`pytest`, fake-Git integration tests.

## Global Constraints

- Product-facing text and generated metadata use lowercase `telepiplex`.
- The script requires no arguments, ignores any persisted legacy arguments, and uses the fixed commit message `update telepiplex`.
- Host version comes only from the unique `v<major>.<minor>.<patch>-host` literal inside `app/115bot.py::get_version()`.
- Feature versions continue to require matching `manifest.yaml` and `pyproject.toml` SemVer values.
- Existing remote tags are immutable and are skipped, never deleted, moved, or overwritten.
- `main` is pushed before tags; every missing tag is pushed in its own Git command.
- Mac `/Users/young/Documents/telepiplex` does not run Git, create Git metadata, or publish.
- The final handoff goes through Syncthing to `/mnt/user/archives/life hacker/telepiplex`.

---

### Task 1: Lock the zero-argument publisher contract

**Files:**
- Modify: `tests/test_unraid_publish_script.py`
- Reference: `docs/superpowers/specs/2026-07-28-unraid-zero-argument-publisher-design.md`

**Interfaces:**
- Consumes: `UnraidPublishScriptTest._run_script(...)` and the existing fake `git` executable.
- Produces: `_run_script(changed_path, remote_tags, script_args=(), host_source=...)`, plus assertions for Host auto-discovery, persisted-argument compatibility, metadata removal, fixed commit text, and pre-mutation validation.

- [ ] **Step 1: Make the test repository contain the Host version source**

Change the helper signature and repository setup to:

```python
def _run_script(
    self,
    *,
    changed_path,
    remote_tags,
    script_args=(),
    host_source=textwrap.dedent(
        '''\
        def get_version(md_format=False):
            version = "v3.4.9-host"
            return version
        '''
    ),
):
    ...
    app = repository / "app"
    app.mkdir()
    (app / "115bot.py").write_text(host_source, encoding="utf-8")
    git_log.touch()
```

Invoke the script without manufacturing the old single-string argument:

```python
result = subprocess.run(
    ["bash", str(SCRIPT), *script_args],
    cwd=ROOT,
    env=env,
    capture_output=True,
    text=True,
)
```

- [ ] **Step 2: Convert the individual-tag test to zero arguments**

Remove `host_version="3.4.9"` from
`test_host_and_multiple_features_are_pushed_as_individual_tag_events`.
Keep `telepiplex-v3.4.8` as the prior remote Host identity and retain the exact
expected push order:

```python
self.assertEqual(
    tag_pushes,
    [
        "push origin refs/tags/telepiplex-v3.4.9",
        "push origin refs/tags/download-v1.0.5",
        "push origin refs/tags/rename-v1.0.4",
        "push origin refs/tags/sync-v1.0.2",
        "push origin refs/tags/caption-v0.1.2",
    ],
)
self.assertIn("commit -m update telepiplex", git_log.read_text(encoding="utf-8"))
```

- [ ] **Step 3: Keep Feature-only tests independent from Host publication**

Add the current Host tag to the `remote_tags` fixtures in
`test_published_feature_changes_can_enter_main_without_a_new_tag` and
`test_unpublished_feature_version_is_tagged`:

```python
"host refs/tags/telepiplex-v3.4.9"
```

This makes those tests assert only their named Feature behavior while also
verifying that an existing Host tag is skipped without error.

- [ ] **Step 4: Add metadata and persisted-argument tests**

Add:

```python
def test_user_script_is_zero_argument_and_ignores_legacy_arguments(self):
    source = SCRIPT.read_text(encoding="utf-8")
    self.assertNotIn("#argumentDescription=", source)
    self.assertNotIn("#argumentDefault=", source)

    result, git_log = self._run_script(
        changed_path="",
        remote_tags="\n".join(
            (
                "host refs/tags/telepiplex-v3.4.9",
                "a refs/tags/download-v1.0.5",
                "b refs/tags/search-v1.1.0",
                "c refs/tags/rename-v1.0.4",
                "d refs/tags/sync-v1.0.2",
                "e refs/tags/caption-v0.1.2",
            )
        ),
        script_args=("PUBLISH 3.4.9 release telepiplex 3.4.9",),
    )

    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertIn("已忽略 User Scripts 保留的旧参数", result.stdout)
    self.assertFalse(
        any(
            line.startswith(("commit ", "push "))
            for line in git_log.read_text(encoding="utf-8").splitlines()
        )
    )
```

- [ ] **Step 5: Add strict Host source validation tests**

Add:

```python
def test_invalid_host_version_source_fails_before_git_mutation(self):
    cases = {
        "missing": "def get_version():\n    return 'unknown'\n",
        "malformed": (
            "def get_version():\n"
            '    version = "v3.4-host"\n'
            "    return version\n"
        ),
        "duplicate": (
            "def get_version():\n"
            '    version = "v3.4.9-host"\n'
            '    version = "v3.4.10-host"\n'
            "    return version\n"
        ),
    }
    for name, host_source in cases.items():
        with self.subTest(name=name):
            result, git_log = self._run_script(
                changed_path="",
                remote_tags="",
                host_source=host_source,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Host 版本", result.stderr)
            self.assertFalse(
                any(
                    line.startswith(("add ", "commit ", "push "))
                    for line in git_log.read_text(encoding="utf-8").splitlines()
                )
            )
```

- [ ] **Step 6: Run the focused tests and verify RED**

Run:

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_unraid_publish_script.py
```

Expected: failures show that the script still requires `PUBLISH HOST_VERSION`
arguments, still exposes argument metadata, and does not read the Host version
from `app/115bot.py`.

- [ ] **Step 7: Record the local red-test checkpoint**

Do not commit. Record the exact failing test count and failure reasons for the
implementation handoff.

---

### Task 2: Implement automatic Host discovery and zero-argument execution

**Files:**
- Modify: `scripts/unraid/telepiplex-publish.sh`
- Test: `tests/test_unraid_publish_script.py`

**Interfaces:**
- Consumes: zero command-line arguments and `app/115bot.py::get_version()`.
- Produces: `HOST_VERSION=<major>.<minor>.<patch>`, `HOST_TAG=telepiplex-v$HOST_VERSION`, fixed `COMMIT_MESSAGE='update telepiplex'`, and the existing `PENDING_TAGS` publication sequence.

- [ ] **Step 1: Remove the User Scripts argument UI**

Delete:

```bash
#argumentDescription=参数：PUBLISH Host版本 提交说明；Host版本填 - 表示不发布 Host
#argumentDefault=PUBLISH - update telepiplex
```

Keep the remaining User Scripts metadata unchanged.

- [ ] **Step 2: Replace argument parsing with persisted-argument compatibility**

Delete `usage()`, `ARGS`, `CONFIRM`, the manual `HOST_VERSION` assignment, and
the old Host-version conditional validation. Add immediately after `die()`:

```bash
if (($# > 0)); then
  echo '提示：已忽略 User Scripts 保留的旧参数；版本将从源码自动读取。'
fi

COMMIT_MESSAGE='update telepiplex'
```

Do not inspect or echo the legacy argument values.

- [ ] **Step 3: Parse the Host version before any Git mutation**

After `cd "$REPO"` and the `.git` / `.stfolder` existence checks, add:

```bash
HOST_VERSION_SOURCE='app/115bot.py'

[[ -f "$HOST_VERSION_SOURCE" ]] ||
  die "缺少文件：$HOST_VERSION_SOURCE"

HOST_VERSION_MATCHES="$(
  awk '
    /^def get_version\(/ {
      in_get_version=1
      next
    }

    in_get_version && /^[^[:space:]]/ {
      in_get_version=0
    }

    in_get_version {
      print
    }
  ' "$HOST_VERSION_SOURCE" |
    sed -nE \
      's/^[[:space:]]*version[[:space:]]*=[[:space:]]*"v((0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*))-host"[[:space:]]*$/\1/p'
)"

HOST_VERSION_COUNT="$(
  awk 'NF { count++ } END { print count + 0 }' <<<"$HOST_VERSION_MATCHES"
)"

((HOST_VERSION_COUNT == 1)) ||
  die '无法从 app/115bot.py 的 get_version() 唯一读取 Host 版本'

HOST_VERSION="$HOST_VERSION_MATCHES"

is_semver "$HOST_VERSION" ||
  die "Host 版本无效：$HOST_VERSION"
```

This Bash 3.2-compatible parser is scoped to `get_version()`, accepts only the
current double-quoted `vX.Y.Z-host` contract, counts matches explicitly, and
makes missing, malformed, or duplicate identities fatal before `git add`,
commit, or push.

- [ ] **Step 4: Make Host tag discovery idempotent**

Replace the manual `HOST_VERSION != '-'` block with:

```bash
HOST_TAG="telepiplex-v$HOST_VERSION"

if remote_tag_exists "$HOST_TAG"; then
  HOST_RELEASE_STATE='远端标签已存在，不重复发布'
else
  assert_newer_than_remote telepiplex "$HOST_VERSION"
  PENDING_TAGS+=("$HOST_TAG")
  HOST_RELEASE_STATE='待发布'
fi
```

Before Feature inspection, print:

```bash
echo '[2/5] 检查 Host 与 Feature 版本...'
echo "Host：${HOST_VERSION}（${HOST_RELEASE_STATE}）"
```

- [ ] **Step 5: Run Bash syntax and focused tests**

Run:

```bash
bash -n scripts/unraid/telepiplex-publish.sh

PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_unraid_publish_script.py
```

Expected: Bash exits `0`; all publisher tests pass.

- [ ] **Step 6: Audit the removed manual interface**

Run:

```bash
rg -n \
  'argumentDescription|argumentDefault|PUBLISH HOST_VERSION|HOST_VERSION 填|PUBLISH -' \
  scripts/unraid/telepiplex-publish.sh tests/test_unraid_publish_script.py
```

Expected: no matches in the active script; any test match is only a deliberate
persisted-argument compatibility fixture.

- [ ] **Step 7: Record the local implementation checkpoint**

Do not commit. Record the two modified files and the exact focused validation
results.

---

### Task 3: Verify the complete release contract and hand off

**Files:**
- Verify: `scripts/unraid/telepiplex-publish.sh`
- Verify: `tests/test_unraid_publish_script.py`
- Verify: `docs/superpowers/specs/2026-07-28-unraid-zero-argument-publisher-design.md`
- Verify: `docs/superpowers/plans/2026-07-28-unraid-zero-argument-publisher.md`

**Interfaces:**
- Consumes: the zero-argument publisher and all current Host/Feature versions.
- Produces: complete local verification evidence and a Syncthing/User Scripts replacement handoff.

- [ ] **Step 1: Run the complete Host test suite**

Run:

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests
```

Expected: all Host tests pass, with only explicitly reported existing skips.

- [ ] **Step 2: Run all five Feature suites**

Run:

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider tests
  )
done
```

Expected: all five Feature suites pass.

- [ ] **Step 3: Verify workspace boundaries**

Run:

```bash
cd /Users/young/Documents/telepiplex
test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

Expected: all three commands exit `0`.

- [ ] **Step 4: Self-review the final diff without Git**

Read the complete modified script and tests with `sed`/`nl`. Confirm:

- no manual argument metadata or parser remains;
- Host version is validated before `git add`;
- an existing Host tag is skipped;
- a missing Host tag is first in `PENDING_TAGS`;
- all tag pushes remain one ref per command;
- errors remain fail-fast and do not delete or overwrite tags.

- [ ] **Step 5: Hand off through Syncthing**

List every created and modified file with its purpose and every command actually
run with its result. Ask the user to:

1. wait for Syncthing `Up to Date / 最新`;
2. fully replace the actual Unraid User Scripts copy with
   `scripts/unraid/telepiplex-publish.sh`;
3. open `telepiplex Publish` and click `Run`; any parameter value retained by
   User Scripts is ignored;
4. confirm the output lists `telepiplex-v3.4.9` when that remote tag is still
   missing.

Do not run Git or publish from the Mac.
