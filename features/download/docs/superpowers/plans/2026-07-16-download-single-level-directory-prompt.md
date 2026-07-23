# Open115 Single-Level Directory Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the two-step Open115 directory editor explicitly accept a single root folder such as `真人电影` and publish Open115 1.0.0.

**Architecture:** Keep the existing `name` and `path` fields and the existing root-relative validator. Change only the Telegram copy so the first step is unmistakably a display label and the second step documents both single-level and multi-level paths, then lock the complete single-level workflow with an end-to-end test.

**Tech Stack:** Python 3.12, `unittest`, YAML, Telepiplex Feature SDK, GitHub Actions Feature release workflow.

## Global Constraints

- Modify only `feature/download`; do not change Telepiplex routing.
- Keep `save_directories` entries shaped as `{name: str, path: str}`.
- Accept `真人电影` and `真人电影/`; store both as `真人电影`.
- Keep leading `/`, unsafe segments, repeated separators, and duplicates invalid.
- Preserve the existing 115 API boundary that converts stored relative paths to absolute API paths.
- Release the reviewed result as exactly `1.0.0`.

---

### Task 1: Clarify and prove the single-level directory flow

**Files:**
- Modify: `tests/test_feature_runtime.py`
- Modify: `src/telepiplex_download/service.py`

**Interfaces:**
- Consumes: `DownloadFeature.message(request: dict) -> dict` during `directory_add_name` and `directory_add_path`.
- Produces: a working directory entry `{"name": "真人电影", "path": "真人电影"}` and explicit two-step prompt text.

- [ ] **Step 1: Write the failing interaction test**

Update `test_directory_working_copy_add_edit_delete_and_save` so the add flow sends `真人电影` as the display name, asserts that the next prompt contains `第二步` and `单级目录`, then sends `真人电影` as the path. Assert the working copy and saved config contain:

```python
{"name": "真人电影", "path": "真人电影"}
```

Also assert the initial add-name prompt contains `第一步` and states that the name is only for display.

- [ ] **Step 2: Run the focused test and verify RED**

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_feature_runtime.DownloadFeatureTest.test_directory_working_copy_add_edit_delete_and_save -v
```

Expected: FAIL because the current prompts do not identify the first/second steps or describe a single-level example.

- [ ] **Step 3: Implement the minimal prompt changes**

Change the add-name prompt to:

```text
第一步（显示名称）：请发送保存目录的显示名称，例如“真人电影”。该名称只用于按钮展示，不是保存路径。
```

Change add-path and edit-path prompts to communicate this exact contract:

```text
第二步（保存路径）：单级目录直接输入“真人电影”；多级目录可输入“series/live action”。不要以 / 开头，末尾 / 可省略。
```

Keep all callback payloads, stages and validation functions unchanged.

- [ ] **Step 4: Run focused and full tests to verify GREEN**

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_feature_runtime.DownloadFeatureTest.test_directory_working_copy_add_edit_delete_and_save -v
PYTHONPATH=src:/Users/young/Documents/telepiplex/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest discover -s tests -t . -v
```

Expected: focused test and all Open115 tests pass.

- [ ] **Step 5: Commit the behavior and regression test**

```bash
git add tests/test_feature_runtime.py src/telepiplex_download/service.py
git commit -m "fix(download): clarify single-level save paths"
```

---

### Task 2: Prepare and publish Open115 1.0.0

**Files:**
- Modify: `tests/test_feature_runtime.py`
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Create outside the Feature worktree: `/Users/young/Documents/telepiplex/dist/download-1.0.0.tpx`

**Interfaces:**
- Consumes: the verified single-level flow from Task 1.
- Produces: immutable Open115 `1.0.0` identity and `download-v1.0.0` Release/catalog entry.

- [ ] **Step 1: Write failing version and README contract assertions**

Change `FeatureSourceContractTest` to expect manifest/project version `1.0.0`, README build path `dist/download-1.0.0.tpx`, and README text containing both `单级目录` and `真人电影`.

- [ ] **Step 2: Run the source contract and verify RED**

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_feature_runtime.FeatureSourceContractTest -v
```

Expected: FAIL because the source still identifies 1.2.2 and README does not explicitly document the single-level workflow.

- [ ] **Step 3: Update version and README**

Set `manifest.yaml` and `pyproject.toml` to `1.0.0`. Update README with the two-step `真人电影` example and change its build artifact path to `dist/download-1.0.0.tpx`.

- [ ] **Step 4: Run release verification and commit**

```bash
PYTHONPATH=src:/Users/young/Documents/telepiplex/sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest discover -s tests -t . -v
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile src/telepiplex_download/*.py
git diff --check
git add tests/test_feature_runtime.py manifest.yaml pyproject.toml README.md
git commit -m "chore(download): prepare 1.0.0"
```

Expected: all tests pass, compilation and diff checks exit zero, and the release identity commit succeeds.

- [ ] **Step 5: Build, push, tag and verify the immutable release**

Build with Telepiplex `tools/build_feature.py`, verify the local artifact identifies Open115 1.0.0 and the final `feature/download` source commit, push `feature/download`, then create and push annotated tag `download-v1.0.0` on the current synchronized `main` release-infrastructure commit.

Wait for `Publish one Telepiplex Feature` to succeed. Verify the public release is neither draft nor prerelease and contains exactly `download-1.0.0.tpx`, `catalog.yaml`, and `catalog.yaml.sha256`. Download the Linux artifact, verify its checksum and manifest, and confirm `origin/catalog` contains Open115 1.0.0 with the same SHA-256, URL, branch and source commit.
