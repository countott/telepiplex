# Media Unifier Deprecation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mark `feature/media-unifier` as deprecated, establish `feature/plex-management` as the supported post-renaming replacement, and archive the local legacy branch without changing remote refs.

**Architecture:** The stable runtime already loads `app.modules.plex_management` immediately after `app.modules.renaming`; this change locks that behavior with a regression test and documents the branch replacement on `main`. The legacy branch receives a visible README warning in its own temporary worktree, then is locally renamed to `archive/deprecated-media-unifier` with no remote push or deletion.

**Tech Stack:** Python 3.12 `unittest`, Markdown, Git branches and worktrees.

## Global Constraints

- Preserve `feature/plex-management` and `/Users/young/Documents/telepiplex/.worktrees/plex-management` unchanged.
- Keep the stable runtime order exactly `app.modules.renaming`, then `app.modules.plex_management`.
- Do not add a runtime alias or compatibility shim named `media-unifier`.
- Rename only the local legacy branch to `archive/deprecated-media-unifier`.
- Do not push, delete, or rename any remote reference.

---

### Task 1: Lock the replacement contract on `main`

**Files:**
- Modify: `tests/test_bot_runtime_startup.py`
- Modify: `tests/test_deployment_contract.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `DEFAULT_ENABLED_MODULES` from `app/115bot.py` through `load_bot_module()`.
- Produces: A tested public contract that Plex management immediately follows renaming and replaces the deprecated media-unifier branch.

- [ ] **Step 1: Add the runtime-order characterization test**

Add this method to `BotRuntimeStartupTest` in `tests/test_bot_runtime_startup.py`:

```python
def test_plex_management_immediately_follows_renaming(self):
    bot_module = load_bot_module()
    modules = list(bot_module.DEFAULT_ENABLED_MODULES)

    renaming_index = modules.index("app.modules.renaming")
    self.assertEqual(
        modules[renaming_index + 1],
        "app.modules.plex_management",
    )
```

- [ ] **Step 2: Add the failing README replacement-contract test**

Add this method to `DeploymentContractTest` in `tests/test_deployment_contract.py`:

```python
def test_readme_marks_media_unifier_deprecated_and_names_replacement(self):
    source = (ROOT / "README.md").read_text(encoding="utf-8")

    self.assertIn("`feature/media-unifier`（已废弃）", source)
    self.assertIn("由 `feature/plex-management` 替代", source)
    self.assertIn("`app.modules.renaming` → `app.modules.plex_management`", source)
```

- [ ] **Step 3: Run the focused tests and verify the new documentation assertion fails**

Run:

```bash
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m unittest tests/test_bot_runtime_startup.py tests/test_deployment_contract.py
```

Expected: the module-order test passes; `test_readme_marks_media_unifier_deprecated_and_names_replacement` fails because the README has not yet declared the legacy branch deprecated.

- [ ] **Step 4: Update the branch-position section in `README.md`**

Replace the Plex branch bullet and add the deprecated legacy bullet:

```markdown
- `feature/media-unifier`（已废弃）：旧媒体整理/扫库实验分支；由 `feature/plex-management` 替代，不再继续开发。
- `feature/plex-management`：Plex 管理、MCP 与可选 AI 工具调用能力分支；稳定管线顺序为 `app.modules.renaming` → `app.modules.plex_management`。
```

- [ ] **Step 5: Run the focused tests and verify they pass**

Run:

```bash
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m unittest tests/test_bot_runtime_startup.py tests/test_deployment_contract.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit the main-branch contract**

```bash
git add README.md tests/test_bot_runtime_startup.py tests/test_deployment_contract.py
git commit -m "docs: deprecate media-unifier pipeline"
```

---

### Task 2: Mark and locally archive the legacy branch

**Files:**
- Modify on the legacy branch: `README.md`

**Interfaces:**
- Consumes: local branch `feature/media-unifier` at its current historical tip.
- Produces: local branch `archive/deprecated-media-unifier` containing a visible deprecation notice.

- [ ] **Step 1: Verify the temporary worktree directory is ignored**

Run from `/Users/young/Documents/telepiplex`:

```bash
git check-ignore -q .worktrees
```

Expected: exit code 0.

- [ ] **Step 2: Create a temporary worktree for the legacy branch**

```bash
git worktree add .worktrees/media-unifier-deprecation feature/media-unifier
```

Expected: the worktree opens on `feature/media-unifier` without changing `main` or the Plex management worktree.

- [ ] **Step 3: Add a visible README deprecation notice in the temporary worktree**

Insert this block immediately after `# Telepiplex` in `.worktrees/media-unifier-deprecation/README.md`:

```markdown
> [!WARNING]
> 此分支已废弃，不再继续开发。请使用 `feature/plex-management`；该模块在稳定运行管线中位于 `app.modules.renaming` 之后。
```

- [ ] **Step 4: Verify the notice and commit it on the legacy branch**

Run:

```bash
git -C .worktrees/media-unifier-deprecation diff --check
git -C .worktrees/media-unifier-deprecation diff -- README.md
git -C .worktrees/media-unifier-deprecation add README.md
git -C .worktrees/media-unifier-deprecation commit -m "docs: mark media-unifier deprecated"
```

Expected: one documentation-only commit succeeds on `feature/media-unifier`.

- [ ] **Step 5: Remove the temporary worktree before renaming the checked-out branch**

```bash
git worktree remove .worktrees/media-unifier-deprecation
git worktree prune
```

Expected: the temporary worktree is removed; the existing Plex management worktree remains registered.

- [ ] **Step 6: Rename the local branch and detach it from the old upstream name**

```bash
git branch -m feature/media-unifier archive/deprecated-media-unifier
git branch --unset-upstream archive/deprecated-media-unifier
```

Expected: the local legacy branch is named `archive/deprecated-media-unifier`; `origin/feature/media-unifier` is unchanged.

---

### Task 3: Verify final branch and workspace state

**Files:**
- Verify only; modify no files.

**Interfaces:**
- Consumes: the commits and branch rename from Tasks 1 and 2.
- Produces: evidence that the replacement contract and branch boundaries are correct.

- [ ] **Step 1: Run the focused main tests**

```bash
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m unittest tests/test_bot_runtime_startup.py tests/test_deployment_contract.py tests/test_composable_integration.py
```

Expected: all tests pass.

- [ ] **Step 2: Verify Python syntax and whitespace**

```bash
git ls-files -z '*.py' | xargs -0 \
  /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check HEAD~1..HEAD
```

Expected: both commands exit 0 with no errors.

- [ ] **Step 3: Verify exact local branch state**

```bash
git branch --list feature/media-unifier
git branch --list archive/deprecated-media-unifier
git show archive/deprecated-media-unifier:README.md | rg "此分支已废弃|feature/plex-management|app.modules.renaming"
git rev-parse origin/feature/media-unifier
git worktree list
```

Expected:

- No local `feature/media-unifier` branch is listed.
- `archive/deprecated-media-unifier` exists and contains the notice.
- The remote-tracking `origin/feature/media-unifier` ref still exists at its original commit.
- The main worktree and `.worktrees/plex-management` are present; no temporary media-unifier worktree remains.

- [ ] **Step 4: Verify both persistent worktrees are clean**

```bash
git status --short --branch
git -C .worktrees/plex-management status --short --branch
```

Expected: `main` and `feature/plex-management` have no uncommitted changes.
