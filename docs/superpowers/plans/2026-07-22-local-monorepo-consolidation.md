# Local Monorepo Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the Mac project from a Git-worktree-dependent checkout into a clean local monorepo that Syncthing can send to the sole Git workspace on Unraid.

**Architecture:** Keep Telepiplex Telepiplex at the project root and place each standalone Feature below `features/<plugin_id>`. Remove all local Git/worktree metadata and generated backup/build debris, while adapting tag-driven GitHub Actions to build Feature source from the single `main` tree.

**Tech Stack:** Python 3.12, pytest, PyYAML, GitHub Actions YAML, Docker source files, Syncthing ignore patterns.

## Global Constraints

- Work only in `/Users/young/Documents/telepiplex` on the Mac.
- Run no Git command of any kind.
- Make no network request and contact no GitHub endpoint.
- Do not publish, tag, commit, push, create a branch, or create a PR.
- Preserve `/Users/young/Documents/telepiplex/.stfolder`.
- Preserve `/Users/young/Documents/telepiplex/.venv` as a local-only runtime.
- Use `/Users/young/Documents/telepiplex/.stignore` to keep local state out of Syncthing.
- Preserve tag-triggered immutable publication; do not add publication on every `main` push.
- Do not rename technical module identities or apply user-facing display-name changes.
- Replace the plan skill's commit checkpoints with filesystem verification checkpoints because Git is prohibited in this workspace.

---

### Task 1: Establish the local/Syncthing boundary

**Files:**
- Create: `.stignore`
- Create or replace from Telepiplex: `.gitignore`

**Interfaces:**
- Consumes: Syncthing send-only folder rooted at the project directory.
- Produces: Ignore rules that exclude local metadata and generated output.

- [ ] **Step 1: Write `.stignore`**

Include `.git`, `.worktrees`, `.venv`, `.pytest_cache`, `__pycache__`, `*.pyc`,
`build`, `dist`, `*.egg-info`, `.idea`, `.vscode`, `.superpowers/sdd`, and
`.DS_Store` patterns while leaving `.stfolder` untouched.

- [ ] **Step 2: Verify ignore coverage without invoking Git**

Run:

```bash
rg -n '^\.git$|^\.worktrees$|^\.venv$|__pycache__|\.egg-info|\.DS_Store' .stignore
```

Expected: every prohibited local-state family is printed.

### Task 2: Migrate clean Telepiplex source to the root

**Files:**
- Create/replace from `.worktrees/telepiplex`: `.dockerignore`, `.github/`,
  `Dockerfile`, `LICENSE`, `README.md`, `README_EN.md`, `app/`, `build.sh`,
  `config/`, `docker-compose.yaml`, `examples/`, `requirements.txt`, `sdk/`,
  `tests/`, `tools/`, and `update.md`
- Merge: `docs/`

**Interfaces:**
- Consumes: authored Telepiplex files from the old local worktree directory.
- Produces: a runnable Telepiplex project rooted at the Syncthing folder.

- [ ] **Step 1: Copy authored Telepiplex files with explicit exclusions**

Use a local filesystem copy that excludes all metadata and generated-output
families listed in Task 1. Do not copy `.git`, `.superpowers/sdd`, caches, or IDE
state.

- [ ] **Step 2: Verify Telepiplex root surface**

Run:

```bash
test -f Dockerfile && test -f app/init.py && test -f sdk/pyproject.toml && test -f tools/build_feature.py
```

Expected: exit status 0 and no output.

### Task 3: Migrate four standalone Features

**Files:**
- Create: `features/download/`
- Create: `features/search/`
- Create: `features/rename/`
- Create: `features/sync/`

**Interfaces:**
- Consumes: authored files from the matching old worktree directory.
- Produces: four self-contained Feature build roots, each with `manifest.yaml`,
  `pyproject.toml`, `src/`, and `tests/`.

- [ ] **Step 1: Copy each Feature with explicit exclusions**

Exclude `.git`, `.pytest_cache`, `__pycache__`, `build`, `dist`, `*.egg-info`,
`.DS_Store`, `.idea`, `.vscode`, and `.superpowers/sdd`.

- [ ] **Step 2: Verify all Feature surfaces**

Run:

```bash
for module in download search rename sync; do
  test -f "features/$module/manifest.yaml"
  test -f "features/$module/pyproject.toml"
  test -d "features/$module/src"
  test -d "features/$module/tests"
done
```

Expected: exit status 0 and no output.

### Task 4: Convert release source identity from branches to monorepo paths

**Files:**
- Modify: `features/download/manifest.yaml`
- Modify: `features/search/manifest.yaml`
- Modify: `features/rename/manifest.yaml`
- Modify: `features/sync/manifest.yaml`
- Modify: `tools/update_feature_catalog.py`
- Modify: `tools/generate_release_catalog.py`
- Modify: `.github/workflows/release-feature.yml`
- Modify: `tests/test_feature_catalog_updater.py`
- Modify: `tests/test_release_catalog_generator.py`
- Modify: `tests/test_release_workflow.py`

**Interfaces:**
- Consumes: Feature tag prefix and `plugin_id`.
- Produces: deterministic `plugin_id -> features/<plugin_id>` source-directory
  mapping and `source.branch: main` artifact identity.

- [ ] **Step 1: Write failing monorepo workflow/catalog tests**

Tests must assert that all four manifests use `source.branch: main`, Feature
source mapping resolves to `features/<plugin_id>`, and the workflow contains no
feature-branch checkout or `feature-src` directory.

- [ ] **Step 2: Run the targeted tests and confirm they fail**

Run:

```bash
PYTHONPATH=.:sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest -q \
  tests/test_feature_catalog_updater.py \
  tests/test_release_catalog_generator.py \
  tests/test_release_workflow.py
```

Expected: failures reference old `feature/*` branch identities and the fixed
Feature-branch checkout.

- [ ] **Step 3: Implement the monorepo mapping**

Replace branch maps with a source-directory map, use `main` as the Feature
manifest/catalog branch identity, build from `features/<plugin_id>`, and use the
single checkout's commit as `source.commit`. Preserve all release tag patterns,
immutability checks, public artifact URL validation, and catalog publication.

- [ ] **Step 4: Re-run the targeted tests**

Run the command from Step 2.

Expected: all targeted tests pass.

### Task 5: Remove the obsolete local Git/worktree and generated-data chain

**Files/directories:**
- Delete: `.git/`
- Delete: `.worktrees/`
- Delete: root `.pytest_cache/`
- Delete: root `.DS_Store`
- Delete: root `dist/`
- Delete recursively from retained source: `__pycache__/`, `*.pyc`, `build/`,
  `dist/`, `*.egg-info`, `.DS_Store`, `.pytest_cache/`, `.idea/`, `.vscode/`,
  `.superpowers/sdd/`
- Preserve: `.stfolder/`, `.venv/`

**Interfaces:**
- Consumes: successfully migrated and targeted-test-verified monorepo.
- Produces: a Git-independent local source directory with no worktree pointers.

- [ ] **Step 1: Compare required authored-file counts before deletion**

Confirm each source worktree's non-generated authored files has a corresponding
destination file, excluding only the documented debris families.

- [ ] **Step 2: Delete the obsolete chain and debris locally**

Perform filesystem deletion only. Do not invoke Git tooling.

- [ ] **Step 3: Assert cleanup boundaries**

Run:

```bash
test ! -e .git && test ! -e .worktrees && test ! -e dist
test -d .stfolder && test -d .venv
! find . -path './.venv' -prune -o \( -name .git -o -name __pycache__ -o -name '*.pyc' -o -name '*.egg-info' \) -print | grep -q .
```

Expected: exit status 0 and no output.

### Task 6: Run full local verification

**Files:**
- Verify only; no publication files are generated inside the project tree.

**Interfaces:**
- Consumes: completed clean monorepo.
- Produces: evidence that Telepiplex, Features, manifests, workflows, and packaging work
  without local Git metadata.

- [ ] **Step 1: Run Telepiplex tests**

```bash
PYTHONPATH=.:sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest -q tests
```

Expected: all Telepiplex tests pass.

- [ ] **Step 2: Run Feature tests from each Feature root**

```bash
for module in download search rename sync; do
  (cd "features/$module" && PYTHONPATH=src:../../sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest -q tests)
done
```

Expected: all four test suites pass.

- [ ] **Step 3: Compile authored Python without persistent bytecode**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import ast
from pathlib import Path
for path in Path('.').rglob('*.py'):
    if '.venv' not in path.parts:
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
PY
```

Expected: exit status 0 and no output.

- [ ] **Step 4: Parse YAML contracts and verify source identity**

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
from pathlib import Path
import yaml
for path in Path('features').glob('*/manifest.yaml'):
    manifest = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert manifest['source']['branch'] == 'main', path
for path in Path('.github/workflows').glob('*.yml'):
    assert yaml.safe_load(path.read_text(encoding='utf-8'))
PY
```

Expected: exit status 0 and no output.

- [ ] **Step 5: Build and verify all Feature packages in a temporary directory**

Set `PIP_NO_INDEX=1`. Use `mktemp -d`, build the dependency-free echo Feature
through `tools/build_feature.py`, and verify it with
`app.runtime.plugin_artifact.verify_tpx`. For the four product Features, copy each
source tree to the temporary directory, remove only the temporary copy's
`requirements-feature.txt`, then build and verify the source wheel, SDK wheel,
manifest identity, and `.tpx` structure without downloading dependencies.
Delete the temporary directory afterward. Expected: five valid `.tpx`
artifacts, `source.branch: main`, and no project-local `dist/`.

- [ ] **Step 6: Scan for prohibited local publication linkage**

```bash
! find . -path './.venv' -prune -o -name .git -print | grep -q .
! rg -n --hidden --glob '!.venv/**' --glob '!.github/workflows/**' \
  'git@github\.com|ssh://git@github\.com|git push|git pull|git clone' \
  .stignore README.md README_EN.md build.sh docker-compose.yaml app config examples features sdk tests tools
```

Expected: exit status 0 and no active Mac-side SSH/publish commands in the
current operational source and documentation surface.

- [ ] **Step 7: Hand off without Git**

List changed, moved, and deleted paths; provide the local verification commands;
state that no Git or publication operation ran; remind the user to wait until
Syncthing reports `Up to Date / 最新` before reviewing on Unraid.
