# Unraid Individual Release Tag Push Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Unraid `telepiplex Publish` script push every release tag in a separate Git command so GitHub creates one `push` event per Host or Feature release.

**Architecture:** Extend the existing fake-Git integration test to reproduce one Host plus four pending Feature tags and assert five deterministic one-ref pushes. Replace the script's single multi-ref atomic push with a fail-fast loop while retaining current local tag creation, remote-tag discovery, rerun recovery, and final remote verification.

**Tech Stack:** Bash, Python 3.12 `unittest`/`pytest`, fake Git executable.

## Global Constraints

- Do not run Git, create Git metadata, publish, or connect the Mac workspace to GitHub.
- Product-facing text uses lowercase `telepiplex`; technical tag families remain unchanged.
- Every release-tag push command contains exactly one `refs/tags/<tag>` ref.
- Keep deterministic order: Host first when requested, then `download`, `search`, `rename`, `sync`, and `caption` when their versions are pending.
- Stop on the first failed tag push; rerunning the script resumes from remote tag state.
- Do not add `workflow_dispatch` or change GitHub Actions workflow triggers.

---

### Task 1: Specify individual tag-push behavior

**Files:**
- Modify: `tests/test_unraid_publish_script.py`

**Interfaces:**
- Consumes: `_run_script(changed_path, remote_tags, host_version)` with the real publisher executed against a fake Git binary.
- Produces: A regression test proving a five-tag release emits five one-ref pushes and no multi-ref atomic push.

- [ ] **Step 1: Let the test helper request a Host release**

Change the helper signature and invocation to:

```python
def _run_script(self, *, changed_path, remote_tags, host_version="-"):
    ...
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"PUBLISH {host_version} test publish",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
```

Keep existing callers on the default `-`.

- [ ] **Step 2: Allow the fake Git boundary to observe either old or new push shapes**

Before the old atomic tag-push case, add:

```bash
"push origin refs/tags/"*)
  ;;
```

This keeps the red test focused on the publisher's observed commands rather than an unsupported fake command.

- [ ] **Step 3: Add the five-tag regression test**

Add:

```python
def test_host_and_multiple_features_are_pushed_as_individual_tag_events(self):
    result, git_log = self._run_script(
        changed_path="app/115bot.py",
        host_version="3.4.8",
        remote_tags="\n".join(
            (
                "a refs/tags/telepiplex-v3.4.7",
                "b refs/tags/search-v1.1.0",
            )
        ),
    )

    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    tag_pushes = [
        line
        for line in git_log.read_text(encoding="utf-8").splitlines()
        if line.startswith("push ") and "refs/tags/" in line
    ]
    self.assertEqual(
        tag_pushes,
        [
            "push origin refs/tags/telepiplex-v3.4.8",
            "push origin refs/tags/download-v1.0.4",
            "push origin refs/tags/rename-v1.0.3",
            "push origin refs/tags/sync-v1.0.1",
            "push origin refs/tags/caption-v0.1.1",
        ],
    )
    self.assertTrue(all(line.count("refs/tags/") == 1 for line in tag_pushes))
    self.assertFalse(any(line.startswith("push --atomic ") for line in tag_pushes))
```

- [ ] **Step 4: Run the new test and verify the old publisher fails**

Run:

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_unraid_publish_script.py::UnraidPublishScriptTest::test_host_and_multiple_features_are_pushed_as_individual_tag_events
```

Expected: one assertion failure showing the old `push --atomic origin` command contains five tag refs.

### Task 2: Push release tags one at a time

**Files:**
- Modify: `scripts/unraid/telepiplex-publish.sh`
- Modify: `tests/test_unraid_publish_script.py`

**Interfaces:**
- Consumes: Ordered `TAG_REFS` populated from validated `PENDING_TAGS`.
- Produces: One external `git push origin "$tag_ref"` command per release tag.

- [ ] **Step 1: Replace the multi-ref atomic push**

Replace:

```bash
"${GIT[@]}" push \
  --atomic \
  origin \
  "${TAG_REFS[@]}"
```

with:

```bash
for tag_ref in "${TAG_REFS[@]}"; do
  "${GIT[@]}" push \
    origin \
    "$tag_ref"
done
```

Update the step label to `逐个推送发布标签`.

- [ ] **Step 2: Remove the obsolete atomic fake-Git case**

Delete the fake command case matching:

```bash
"push --atomic origin refs/tags/"*)
  ;;
```

The fake should now accept only the one-ref tag-push shape.

- [ ] **Step 3: Run all publisher tests**

Run:

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_unraid_publish_script.py
```

Expected: all tests pass.

- [ ] **Step 4: Validate Bash syntax**

Run:

```bash
bash -n scripts/unraid/telepiplex-publish.sh
```

Expected: exit status `0` with no output.

### Task 3: Verify the complete local workspace

**Files:**
- Verify: `scripts/unraid/telepiplex-publish.sh`
- Verify: `tests/test_unraid_publish_script.py`
- Verify: `docs/superpowers/specs/2026-07-28-unraid-individual-tag-push-design.md`
- Verify: `docs/superpowers/plans/2026-07-28-unraid-individual-tag-push.md`

**Interfaces:**
- Consumes: The completed regression and script change.
- Produces: Fresh evidence suitable for Syncthing handoff to the Unraid publisher.

- [ ] **Step 1: Run the full Host and five-Feature suites**

Run:

```bash
set -e
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests
for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider tests
  )
done
```

- [ ] **Step 2: Recheck workspace boundaries**

Run:

```bash
test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

Expected: all checks pass.

- [ ] **Step 3: Hand off the controlled script**

List every changed file and validation result. Ask the user to wait for
Syncthing `Up to Date / 最新`, then replace the actual Unraid User Scripts copy
with `scripts/unraid/telepiplex-publish.sh`. Stop before running the publisher
or any Git command.
