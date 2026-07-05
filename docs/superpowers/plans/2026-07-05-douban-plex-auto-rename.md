# Douban Plex Auto Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically organize 115 offline results from Douban-backed and plain-title `/s` searches into Plex-friendly Chinese-title and English-title folders.

**Architecture:** Add a small pure helper module for metadata and naming decisions. Thread Douban metadata, exact-match Douban reverse-lookup metadata, or plain search-query metadata through `search_handler` into `download_task`, then let `download_handler` attempt auto-rename before falling back to the current manual TMDB flow.

**Tech Stack:** Python 3, `unittest`, existing Telegram handler code, existing OpenAPI 115 wrapper.

---

### Task 1: Plex Naming Helpers

**Files:**
- Create: `app/utils/plex_naming.py`
- Test: `tests/test_plex_auto_rename.py`

- [ ] Write failing tests for `parse_episode_marker`, `build_plex_naming_plan`, release-title English inference, and missing-metadata fallback.
- [ ] Run `python3 -m unittest tests.test_plex_auto_rename` and confirm the helper imports fail.
- [ ] Implement the helper module with conservative sanitizing and common `S01E02` / `1x02` / Chinese season-episode parsing.
- [ ] Re-run the helper tests and confirm they pass.

### Task 2: Douban Metadata Propagation

**Files:**
- Modify: `app/handlers/search_handler.py`
- Test: `tests/test_search_handler.py`

- [ ] Write failing tests that `_resolve_search_request` returns Douban Chinese and English title metadata, exact-match Douban reverse-lookup metadata for plain `/s title`, and plain `/s title` fallback metadata.
- [ ] Run `python3 -m unittest tests.test_search_handler` and confirm the new assertions fail.
- [ ] Replace the title-only resolver with a search request object while preserving existing query behavior.
- [ ] Pass metadata and selected release title into `download_task`.
- [ ] Re-run search tests.

### Task 3: Auto-Rename Download Result

**Files:**
- Modify: `app/handlers/download_handler.py`
- Test: `tests/test_download_task_startup.py`

- [ ] Write a failing mocked 115 test proving `download_task(..., plex_metadata=...)` creates Chinese and English folders, renames the main file, creates STRM files, and sends a completed message.
- [ ] Run `python3 -m unittest tests.test_download_task_startup` and confirm failure.
- [ ] Implement auto-rename after offline success and before manual rename task creation.
- [ ] Preserve manual fallback for missing metadata or 115 failures.
- [ ] Run targeted tests, `py_compile`, and diff whitespace check.
