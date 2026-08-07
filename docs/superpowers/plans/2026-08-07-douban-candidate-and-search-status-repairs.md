# Douban Candidate and Search Status Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct searcher arithmetic, reconcile Douban titles after endpoint merging, standardize candidate labels, and make poster grids observable with safe text fallback.

**Architecture:** Keep each repair at its current ownership boundary. The search Feature owns provider normalization, candidate copy, and searcher reporting; the Host owns remote image fetching, placeholder rendering, failure logging, and Telegram fallback.

**Tech Stack:** Python 3, unittest/pytest, Pillow, urllib, python-telegram-bot.

## Global Constraints

- Product-facing text uses lowercase `telepiplex`.
- Do not change `media_metadata v1` schema or confirmed identity presentation.
- Do not change versions, config templates, release workflows, or generated `build/` artifacts.
- Do not run Git or create `.git`/`.worktrees` on the Mac.
- Use `apply_patch` for all file edits.
- Every production change follows a witnessed failing test, minimal implementation, and passing regression run.

---

### Task 1: Compute Searcher Status Values

**Files:**
- Modify: `features/search/tests/test_release_report.py`
- Modify: `features/search/src/telepiplex_search/release_report.py:293-333`

**Interfaces:**
- Consumes: `indexer_summary` fields `completed_indexers`, `total_indexers`, and `down_indexers`.
- Produces: user copy `搜索器 {successful_completed}/{available}，失败 {failed}`.

- [ ] **Step 1: Change the release-report expectations to literal computed values**

Cover final success, partial failure, zero searchers, and media-scope table cases.
The three-searcher/two-failure literal must be `搜索器 0/1，失败 2`.

- [ ] **Step 2: Run the release-report tests and verify RED**

Run:

```bash
cd /Users/young/Documents/telepiplex/features/search
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_release_report.py
```

Expected: assertions fail because output still contains the literal subtraction expression and `离线`.

- [ ] **Step 3: Implement the computed status line**

In `format_release_report`, derive:

```python
failed = len(down)
available = max(0, total - failed)
successful_completed = max(0, completed - failed)
```

Render `搜索器 {successful_completed}/{available}，失败 {failed}`.

- [ ] **Step 4: Run the release-report tests and verify GREEN**

Run the command from Step 2. Expected: all tests pass.

### Task 2: Reconcile Derived Douban Titles After Merge

**Files:**
- Modify: `features/search/tests/test_douban_adapter.py`
- Modify: `features/search/src/telepiplex_search/adapters/douban.py:369-414`

**Interfaces:**
- Consumes: normalized facts for one Douban subject where later facts may add `original_title` and `english_title`.
- Produces: one merged fact with recomputed `chinese_title` and coherent `title`.

- [ ] **Step 1: Add a regression test for a summary/detail split**

The summary fixture supplies `title="想见你 想見你"` without `original_title`.
The detail fixture supplies `title="想见你 想見你"`,
`original_title="想見你"`, and alias `Someday or One Day`.
Assert literal merged values:

```python
self.assertEqual(fact["chinese_title"], "想见你")
self.assertEqual(fact["original_title"], "想見你")
self.assertEqual(fact["official_english_title"], "Someday or One Day")
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
cd /Users/young/Documents/telepiplex/features/search
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_douban_adapter.py -k merged_title
```

Expected: `chinese_title` is still `想见你 想見你`.

- [ ] **Step 3: Reconcile title fields once merging is complete**

After scalar/list merging and conflict collection, run `_chinese_title_part` against
the merged Chinese/display title and merged `original_title`. Set the general
`title` to the merged English title when present, otherwise to the corrected
Chinese title.

- [ ] **Step 4: Run all Douban adapter tests and verify GREEN**

Run:

```bash
cd /Users/young/Documents/telepiplex/features/search
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_douban_adapter.py
```

Expected: all tests pass.

### Task 3: Standardize Candidate Body, Detail, and Button Labels

**Files:**
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `features/search/tests/test_search_usability.py`
- Modify: `features/search/src/telepiplex_search/service.py:95-105`
- Modify: `features/search/src/telepiplex_search/service.py:1689-1801`
- Modify: `features/search/src/telepiplex_search/service.py:1803-1885`

**Interfaces:**
- Produces: `_candidate_display_title(identity: dict, component_limit: int | None = None) -> str`.
- Consumers: candidate grid body, candidate grid buttons, and candidate detail cards.

- [ ] **Step 1: Add candidate-label behavior tests**

Use literal assertions for:

```text
想见你 (想見你) 2019
让子弹飞 2010
```

Assert that the body and corresponding button both contain the same title form,
that a duplicate/missing original title does not create parentheses, and that
the official English title is not rendered on a separate candidate line.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd /Users/young/Documents/telepiplex/features/search
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_feature_service.py -k 'candidate_grid or candidate_detail'
```

Expected: current body uses full-width year parentheses, emits English on another line,
and buttons omit original title/year.

- [ ] **Step 3: Implement one shared candidate title formatter**

Normalize title equality with Unicode NFKC, case-folding, and alphanumeric
comparison. Format a meaningful distinct `original_title` in ASCII parentheses,
then append the year or `年份未知`. Apply component clipping before assembly so
long labels preserve the year and balanced parentheses.

- [ ] **Step 4: Use the formatter in every candidate rendering path**

Replace the separate title/year/English assembly in `_candidate_grid_action` and
both `_candidate_action` branches. Keep HTML escaping at the final HTML boundary.
Use the same formatted label in button text with its existing candidate number.
Keep `poster_items[*].title` as the unformatted Chinese title so a failed poster
card displays only the Chinese work title, not the original title or year.

- [ ] **Step 5: Run candidate and usability tests and verify GREEN**

Run:

```bash
cd /Users/young/Documents/telepiplex/features/search
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_feature_service.py tests/test_search_usability.py
```

Expected: all tests pass.

### Task 4: Make Poster Fetching Observable and Safely Degradable

**Files:**
- Modify: `Dockerfile`
- Modify: `tests/test_poster_grid.py`
- Modify: `tests/test_plugin_handler.py`
- Modify: `tests/test_interaction_handler.py`
- Modify: `app/runtime/poster_grid.py`
- Modify: `app/handlers/plugin_handler.py:1190-1227`

**Interfaces:**
- Produces: `PosterGridUnavailable`, raised when at least one remote URL was supplied but no image could be decoded.
- Produces: a JPEG grid when at least one image succeeds; failed cards use their `title` and retain their number footer.
- Consumers: plugin action rendering and operation rendering, both of which already own text fallback.

- [ ] **Step 1: Add poster-grid behavior tests**

Add literal behavior coverage for:

- Different titles with the same candidate number produce different placeholder pixels.
- Distinct Chinese characters produce distinct font masks.
- A Douban CDN request carries `Referer: https://movie.douban.com/`.
- One successful image plus one failed image still returns a JPEG grid.
- All supplied remote images failing raises `PosterGridUnavailable`.
- Exception/log text contains the failure category but no full URL.

- [ ] **Step 2: Run poster-grid tests and verify RED**

Run:

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_poster_grid.py
```

Expected: title does not affect placeholders, Douban headers are absent, and all
download failures currently return a successful placeholder-only JPEG.

- [ ] **Step 3: Implement classified downloads and title placeholders**

Install `fonts-noto-cjk` in the Core image and prefer the platform CJK font paths
before Latin-only fallbacks. Classify HTTP status, timeout, network, size, redirect, and decode failures without
embedding full URLs. Detect Douban hosts with `urllib.parse.urlsplit`; add the
provider request headers only for trusted `douban.com`/`doubanio.com` host suffixes.
Render wrapped candidate titles in the poster body and keep the numeric footer.

- [ ] **Step 4: Raise only when every supplied remote poster fails**

Track remote requests and successful decodes. Preserve partial grids. Raise
`PosterGridUnavailable` when `requested > 0 and successful == 0`.

- [ ] **Step 5: Add Host fallback and logging tests**

Make `build_poster_grid` raise in plugin and operation-render fixtures. Assert the
real renderer sends the original candidate text/buttons and does not send a photo.
Capture the Host logger and assert the event reports a sanitized failure category.

- [ ] **Step 6: Add sanitized plugin fallback logging**

In the plugin grid exception branch, log `poster_grid_unavailable` through
`_log_feature_event` with exception type/message, then use the existing text reply.
The operation renderer continues using `_render_error`, which already sanitizes URLs.

- [ ] **Step 7: Run Host poster and handler tests and verify GREEN**

Run:

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_poster_grid.py tests/test_plugin_handler.py tests/test_interaction_handler.py
```

Expected: all tests pass.

### Task 5: Integrated Verification

**Files:**
- Verify all files changed by Tasks 1-4.

**Interfaces:**
- Produces: locally verified source ready for Syncthing handoff.

- [ ] **Step 1: Run the complete Core and Feature suites**

Run:

```bash
cd /Users/young/Documents/telepiplex
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

Expected: every suite exits zero.

- [ ] **Step 2: Verify the Mac workspace boundary**

Run:

```bash
cd /Users/young/Documents/telepiplex
test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

Expected: exit zero.

- [ ] **Step 3: Report the Syncthing handoff**

List every modified/created file and its purpose, report exact test counts, and ask
the user to wait for Syncthing to show `Up to Date / 最新`. Do not publish or run Git.
