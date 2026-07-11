import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.handlers import search_handler


class SearchDownloadPlanFlowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        search_handler.pending_search_tasks.clear()
        search_handler.pending_entry_confirmations.clear()

    def tearDown(self):
        search_handler.pending_search_tasks.clear()
        search_handler.pending_entry_confirmations.clear()

    def _plan(self):
        return {
            "schema_version": 1,
            "plan_id": "plan-a",
            "display_title": "想见你",
            "english_title": "Someday or One Day The Movie",
            "year": "2022",
            "content_identity": "extension_movie",
            "relation": {
                "type": "sequel",
                "target_series_title": "Someday or One Day",
                "target_series_year": "2019",
                "source": "wikipedia",
            },
            "placement": {
                "library_type": "series",
                "category_kind": "live_action_series",
                "season_number": 0,
                "episode_number": 100,
                "mapping_kind": "temporary_related_special",
                "mapping_source": "local_allocator",
            },
            "source_entry": {
                "title": "想见你 (电影)",
                "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
                "provider": "wikipedia",
                "availability": "ok",
                "verification": "verified",
            },
            "prowlarr_queries": ["Someday or One Day The Movie 2022"],
            "evidence": {},
            "warnings": [],
            "confirmed": False,
        }

    @patch.object(
        search_handler, "_resolve_plan_selected_path", return_value="/真人剧集"
    )
    @patch.object(search_handler, "build_confirmable_plan", new_callable=AsyncMock)
    async def test_start_search_shows_one_full_plan_confirmation(
        self, planner_mock, _path_mock
    ):
        planner_mock.return_value = self._plan()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        context = SimpleNamespace()

        state = await search_handler._start_entry_resolution(
            update, context, "想见你"
        )

        self.assertEqual(state, search_handler.SEARCH_CONFIRM_DOWNLOAD_PLAN)
        text = update.message.reply_text.await_args.args[0]
        self.assertIn("S00E100", text)
        self.assertIn("来源条目", text)
        self.assertIn("https://zh.wikipedia.org/", text)
        self.assertIn("Someday or One Day The Movie 2022", text)

    @patch.object(
        search_handler,
        "_resolve_selected_link",
        new_callable=AsyncMock,
        return_value="magnet:?xt=urn:btih:" + "a" * 40,
    )
    @patch.object(search_handler, "_submit_download_request")
    async def test_release_pick_dispatches_without_directory_callback(
        self, submit_mock, _resolve_mock
    ):
        task_id = "task-a"
        plan = self._plan()
        plan["confirmed"] = True
        search_handler.pending_search_tasks[task_id] = {
            "created_at": search_handler.time.time(),
            "user_id": 1,
            "query": plan["prowlarr_queries"][0],
            "selected_path": "/真人剧集",
            "download_plan": plan,
            "results": [
                {
                    "title": "release",
                    "magnet_url": "magnet:?xt=urn:btih:" + "a" * 40,
                }
            ],
            "metadata": {"source": "confirmed"},
            "naming_metadata": {"source": "confirmed"},
        }
        callback = SimpleNamespace(
            data=f"search_pick:{task_id}:0",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=callback, effective_user=SimpleNamespace(id=1)
        )
        context = SimpleNamespace(
            user_data={}, application=SimpleNamespace(bot_data={})
        )

        state = await search_handler.select_search_result(update, context)

        self.assertEqual(state, search_handler.ConversationHandler.END)
        request = submit_mock.call_args.args[1]
        self.assertEqual(request.selected_path, "/真人剧集")
        self.assertTrue(request.metadata["download_plan"]["confirmed"])


if __name__ == "__main__":
    unittest.main()
