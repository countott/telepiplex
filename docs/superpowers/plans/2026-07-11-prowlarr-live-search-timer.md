# Prowlarr Live Search Timer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace 30-second Prowlarr progress messages with one Telegram status message that counts from 0 once per second until the search completes.

**Architecture:** Keep the existing background Prowlarr search task and result pipeline. Retain the initial Telegram `Message`, edit that same message from a monotonic elapsed clock, and isolate edit failures so progress display cannot cancel the search.

**Tech Stack:** Python 3.12, `asyncio`, python-telegram-bot, `unittest`, `unittest.mock`

## Global Constraints

- Work only in the local checkout; do not push to a remote.
- Start the visible timer at exactly 0 seconds and update it once per second.
- Do not change Prowlarr timeouts, category selection, result ranking, or download dispatch.
- Use one status message; do not send periodic progress messages.
- A Telegram edit failure must not cancel or discard the Prowlarr search result.

---

### Task 1: Add the live Prowlarr search timer

**Files:**
- Create: `tests/test_prowlarr_search_progress.py`
- Modify: `app/handlers/search_handler.py:62`
- Modify: `app/handlers/search_handler.py:1308-1376`

**Interfaces:**
- Consumes: `_reply_or_send(update, context, text, **kwargs)` and `_search_prowlarr_release_categories(query: str, media_type: str = "") -> list[dict]`.
- Produces: `_build_prowlarr_progress_text(query: str, elapsed_seconds: int, completed: bool = False) -> str`.
- Produces: `_edit_prowlarr_progress_message(status_message, text: str) -> None`, an async best-effort editor.
- Updates: `_search_prowlarr_with_progress(..., status_message=None, progress_interval: float = 1, clock=time.monotonic) -> list[dict]`.

- [x] **Step 1: Write failing tests for the 0-second initial message and status-message handoff**

```python
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.handlers import search_handler


class ProwlarrSearchProgressTest(unittest.IsolatedAsyncioTestCase):
    @patch.object(search_handler, "get_prowlarr_indexer_summary", return_value={})
    @patch.object(search_handler, "_send_search_message", new_callable=AsyncMock)
    @patch.object(search_handler, "_search_prowlarr_with_progress", new_callable=AsyncMock)
    @patch.object(search_handler, "_reply_or_send", new_callable=AsyncMock)
    async def test_search_starts_at_zero_and_reuses_status_message(
        self, reply_mock, progress_mock, send_mock, _summary_mock
    ):
        status_message = SimpleNamespace(edit_text=AsyncMock())
        reply_mock.return_value = status_message
        progress_mock.return_value = []
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=1),
            effective_user=SimpleNamespace(id=2),
            callback_query=None,
            message=SimpleNamespace(),
        )
        context = SimpleNamespace(bot=SimpleNamespace())

        await search_handler._send_search_results(update, context, "Example", metadata={"media_type": "movie"})

        self.assertIn("已等待 0 秒", reply_mock.await_args.args[2])
        self.assertIs(progress_mock.await_args.kwargs["status_message"], status_message)
        send_mock.assert_awaited_once()
```

- [x] **Step 2: Run the initial-message test and verify RED**

Run: `python3 -m unittest tests.test_prowlarr_search_progress.ProwlarrSearchProgressTest.test_search_starts_at_zero_and_reuses_status_message -v`

Expected: FAIL because the initial message does not contain `已等待 0 秒` and `_search_prowlarr_with_progress` does not receive `status_message`.

- [x] **Step 3: Write failing tests for monotonic per-second updates, completion, and edit-failure isolation**

```python
    @patch.object(search_handler, "_search_prowlarr_release_categories")
    async def test_progress_edits_same_message_until_search_completes(self, _search_mock):
        result = [{"title": "Example.Release"}]
        fake_task = Mock()
        fake_task.result.return_value = result
        status_message = SimpleNamespace(edit_text=AsyncMock())

        def create_task(coroutine):
            coroutine.close()
            return fake_task

        with (
            patch.object(search_handler.asyncio, "create_task", side_effect=create_task),
            patch.object(
                search_handler.asyncio,
                "wait",
                new=AsyncMock(side_effect=[(set(), {fake_task}), ({fake_task}, set())]),
            ),
        ):
            actual = await search_handler._search_prowlarr_with_progress(
                SimpleNamespace(),
                SimpleNamespace(),
                "Example",
                status_message=status_message,
                progress_interval=1,
                media_type="movie",
                clock=Mock(side_effect=[100.0, 101.1, 102.2]),
            )

        self.assertEqual(actual, result)
        edits = [call.kwargs["text"] for call in status_message.edit_text.await_args_list]
        self.assertIn("已等待 1 秒", edits[0])
        self.assertIn("搜索完成", edits[1])
        self.assertIn("用时 2 秒", edits[1])

    async def test_edit_failure_does_not_cancel_search(self):
        result = [{"title": "Example.Release"}]
        fake_task = Mock()
        fake_task.result.return_value = result
        status_message = SimpleNamespace(edit_text=AsyncMock(side_effect=Exception("edit failed")))

        def create_task(coroutine):
            coroutine.close()
            return fake_task

        with (
            patch.object(search_handler.asyncio, "create_task", side_effect=create_task),
            patch.object(
                search_handler.asyncio,
                "wait",
                new=AsyncMock(side_effect=[(set(), {fake_task}), ({fake_task}, set())]),
            ),
        ):
            actual = await search_handler._search_prowlarr_with_progress(
                SimpleNamespace(),
                SimpleNamespace(),
                "Example",
                status_message=status_message,
                progress_interval=1,
                media_type="movie",
                clock=Mock(side_effect=[100.0, 101.1, 102.2]),
            )

        self.assertEqual(actual, result)
```

- [x] **Step 4: Run the timer tests and verify RED**

Run: `python3 -m unittest tests.test_prowlarr_search_progress -v`

Expected: FAIL because `status_message` is not accepted and the existing function sends a new progress message every 30 seconds.

- [x] **Step 5: Implement the minimal live-timer behavior**

In `app/handlers/search_handler.py`, set the progress interval to one second, add a pure text builder and best-effort message editor, measure elapsed time with `time.monotonic()`, and pass the initial `Message` into the progress loop:

```python
SEARCH_PROGRESS_INTERVAL_SECONDS = 1


def _build_prowlarr_progress_text(query: str, elapsed_seconds: int, completed: bool = False) -> str:
    if completed:
        return f"✅ Prowlarr 搜索完成：{query}\n用时 {elapsed_seconds} 秒。"
    return (
        f"⏳ Prowlarr 正在搜索：{query}\n"
        f"已等待 {elapsed_seconds} 秒。部分索引器需要 Cloudflare 解析，请继续等待。"
    )


async def _edit_prowlarr_progress_message(status_message, text: str):
    edit_text = getattr(status_message, "edit_text", None)
    if not callable(edit_text):
        return
    try:
        await edit_text(
            text=text,
            disable_web_page_preview=True,
            connect_timeout=TELEGRAM_SEND_TIMEOUT_SECONDS,
            read_timeout=TELEGRAM_SEND_TIMEOUT_SECONDS,
            write_timeout=TELEGRAM_SEND_TIMEOUT_SECONDS,
            pool_timeout=TELEGRAM_SEND_TIMEOUT_SECONDS,
        )
    except Exception as e:
        _log_warn(f"Telegram Prowlarr 搜索进度更新失败，继续执行搜索流程: {e}")
```

The progress loop starts a monotonic clock before waiting, edits the same message after each one-second timeout, and edits it once more after `search_task.result()` succeeds. `_send_search_results` sends `_build_prowlarr_progress_text(query, 0)` and passes its return value as `status_message=status_message`.

- [x] **Step 6: Run the focused test file and verify GREEN**

Run: `python3 -m unittest tests.test_prowlarr_search_progress -v`

Expected: all tests PASS with no warnings.

- [x] **Step 7: Run related regression and static checks**

Run: `python3 -m unittest tests.test_media_metadata_fusion tests.test_media_search_utils tests.test_media_search_surface -v`

Expected: all tests PASS.

Run: `python3 -m py_compile app/handlers/search_handler.py tests/test_prowlarr_search_progress.py`

Expected: exit code 0 and no output.

Run: `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check`

Expected: exit code 0 and no output.

- [x] **Step 8: Commit the local implementation**

```bash
git add app/handlers/search_handler.py tests/test_prowlarr_search_progress.py docs/superpowers/plans/2026-07-11-prowlarr-live-search-timer.md
git commit -m "Improve Prowlarr search progress timer"
```
