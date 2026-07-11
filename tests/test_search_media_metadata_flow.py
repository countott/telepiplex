import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.handlers import search_handler
from app.utils.search_plan import TemporarySpecialAllocator


class SearchMediaMetadataFlowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        search_handler.pending_search_tasks.clear()
        search_handler.pending_entry_confirmations.clear()
        self._previous_allocator = search_handler.temporary_special_allocator
        search_handler.temporary_special_allocator = TemporarySpecialAllocator()

    def tearDown(self):
        search_handler.pending_search_tasks.clear()
        search_handler.pending_entry_confirmations.clear()
        search_handler.temporary_special_allocator = self._previous_allocator

    def _hypotheses(self):
        return {
            "status": "ok",
            "hypotheses": [{
                "title": "想见你",
                "year": "2022",
                "content_identity": "extension_movie",
                "scope": "movie",
                "season_number": None,
                "episode_number": None,
                "possible_related_series": ["Someday or One Day"],
                "explicit_facts": [],
                "inferred_facts": [],
            }],
            "source_queries": {
                "wikipedia": ["想见你 电影"],
                "douban": ["想见你 2022"],
                "tvdb": ["Someday or One Day"],
            },
            "warnings": [],
        }

    def _plan(self):
        return {
            "plan_id": "plan-a",
            "media_metadata": {
                "schema_version": 1,
                "metadata_id": "",
                "confirmed": False,
                "identity": {
                    "chinese_title": "想见你",
                    "english_title": "Someday or One Day The Movie",
                    "year": "2022",
                    "content_kind": "extension_movie",
                    "summary": "电影版延续电视剧故事。",
                    "original_release_date": "2022-12-24",
                    "poster_url": "https://image.example/poster.jpg",
                    "poster_source": "douban",
                    "external_ids": {},
                },
                "relation": {
                    "type": "sequel",
                    "target_series": {
                        "chinese_title": "想见你",
                        "english_title": "Someday or One Day",
                        "year": "2019",
                        "external_ids": {},
                    },
                    "source": "wikipedia",
                },
                "placement": {
                    "library_type": "series",
                    "category_kind": "live_action_series",
                    "season_number": 0,
                    "episode_number": None,
                    "mapping_kind": "temporary_related_special",
                    "mapping_source": "local_allocator",
                    "tvdb_episode_id": "",
                },
                "source_entry": {
                    "title": "想见你 (电影)",
                    "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
                    "provider": "wikipedia",
                    "verification": "verified",
                },
                "items": [],
                "evidence": {},
                "warnings": [],
            },
            "prowlarr_queries": ["Someday or One Day The Movie 2022"],
        }

    def _provider(self, name, timeline=None):
        def provider(_hypotheses):
            if timeline is not None:
                timeline.append(f"provider:{name}")
            return {
                "source": name,
                "status": "ok",
                "facts": [{"title": "想见你"}],
                "source_urls": [],
                "error": "",
            }

        return provider

    async def test_one_confirmation_precedes_prowlarr_and_release_dispatches_contract(self):
        timeline = []

        def hypothesis_ai(_raw_query):
            timeline.append("ai:hypothesis")
            return self._hypotheses()

        def metadata_ai(_context):
            timeline.append("ai:media_metadata")
            return self._plan()

        async def confirmation_reply(*args, **kwargs):
            timeline.append("confirmation")

        async def prowlarr_search(*_args, **_kwargs):
            timeline.append("prowlarr")
            return [{
                "title": "Someday.or.One.Day.The.Movie.2022.1080p.WEB-DL",
                "magnet_url": "magnet:?xt=urn:btih:" + "a" * 40,
                "seeders": 10,
                "size": 1024,
            }]

        async def keep_metadata(naming_metadata, metadata):
            return naming_metadata, metadata

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=99),
            message=SimpleNamespace(
                reply_text=AsyncMock(side_effect=confirmation_reply),
            ),
            callback_query=None,
        )
        context = SimpleNamespace(
            user_data={},
            application=SimpleNamespace(bot_data={}),
            bot=SimpleNamespace(send_message=AsyncMock()),
        )
        submit_mock = Mock(side_effect=lambda *_args: timeline.append("submit"))
        category_config = {
            "category_folder": [{
                "kind": "live_action_series",
                "name": "显示名可变",
                "path": "/真人剧集",
                "plex_library_id": "13",
            }]
        }

        with (
            patch(
                "app.services.search_planner.infer_search_hypotheses_with_ai",
                side_effect=hypothesis_ai,
            ),
            patch(
                "app.services.search_planner.infer_media_metadata_draft_with_ai",
                side_effect=metadata_ai,
            ),
            patch.object(
                search_handler,
                "_wikipedia_plan_provider",
                side_effect=self._provider("wikipedia", timeline),
            ),
            patch.object(
                search_handler,
                "_douban_plan_provider",
                side_effect=self._provider("douban", timeline),
            ),
            patch.object(
                search_handler,
                "_tvdb_plan_provider",
                side_effect=self._provider("tvdb", timeline),
            ),
            patch.object(search_handler, "_occupied_special_numbers", return_value=set()),
            patch.object(search_handler.init, "bot_config", category_config),
            patch.object(
                search_handler,
                "_search_prowlarr_with_progress",
                new_callable=AsyncMock,
                side_effect=prowlarr_search,
            ) as prowlarr_mock,
            patch.object(search_handler, "rank_releases", side_effect=lambda items, _limit: items),
            patch.object(search_handler, "get_prowlarr_indexer_summary", return_value={}),
            patch.object(
                search_handler,
                "_backfill_missing_chinese_title",
                new_callable=AsyncMock,
                side_effect=keep_metadata,
            ),
            patch.object(search_handler, "_reply_or_send", new_callable=AsyncMock),
            patch.object(search_handler, "_send_search_message", new_callable=AsyncMock),
            patch.object(
                search_handler,
                "_resolve_selected_link",
                new_callable=AsyncMock,
                return_value="magnet:?xt=urn:btih:" + "a" * 40,
            ),
            patch.object(search_handler, "_submit_download_request", submit_mock),
        ):
            state = await search_handler._start_entry_resolution(
                update,
                context,
                "想见你",
            )

            self.assertEqual(state, search_handler.SEARCH_CONFIRM_MEDIA_METADATA)
            self.assertEqual(timeline.count("confirmation"), 1)
            self.assertNotIn("prowlarr", timeline)
            self.assertNotIn("submit", timeline)
            prowlarr_mock.assert_not_awaited()
            submit_mock.assert_not_called()

            confirmation_call = update.message.reply_text.await_args
            confirmation_text = confirmation_call.args[0]
            self.assertIn("S00E100", confirmation_text)
            self.assertIn("https://zh.wikipedia.org/", confirmation_text)
            self.assertIn("Someday or One Day The Movie 2022", confirmation_text)
            keyboard = confirmation_call.kwargs["reply_markup"].inline_keyboard
            self.assertEqual(len(keyboard), 1)
            self.assertEqual(len(keyboard[0]), 2)
            self.assertEqual(
                [button.text for button in keyboard[0]],
                ["确认并搜索", "取消"],
            )

            plan_id = next(iter(search_handler.pending_entry_confirmations))
            update.callback_query = SimpleNamespace(
                data=f"plan_confirm:{plan_id}",
                answer=AsyncMock(),
                edit_message_text=AsyncMock(),
            )
            state = await search_handler.confirm_media_metadata_callback(
                update,
                context,
            )
            self.assertEqual(state, search_handler.SEARCH_SELECT_RESULT)
            self.assertEqual(timeline.count("confirmation"), 1)
            self.assertIn("prowlarr", timeline)
            self.assertNotIn("submit", timeline)

            task_id = next(iter(search_handler.pending_search_tasks))
            update.callback_query = SimpleNamespace(
                data=f"search_pick:{task_id}:0",
                answer=AsyncMock(),
                edit_message_text=AsyncMock(),
            )
            state = await search_handler.select_search_result(update, context)

        self.assertEqual(state, search_handler.ConversationHandler.END)
        request = submit_mock.call_args.args[1]
        self.assertEqual(request.selected_path, "/真人剧集")
        self.assertIn("media_metadata", request.metadata)
        self.assertNotIn("_".join(("download", "plan")), request.metadata)
        self.assertEqual(request.metadata["media_metadata"]["metadata_id"], plan_id)
        self.assertTrue(request.metadata["media_metadata"]["confirmed"])
        self.assertEqual(timeline.count("confirmation"), 1)
        self.assertLess(timeline.index("ai:hypothesis"), timeline.index("ai:media_metadata"))
        for provider_name in ("wikipedia", "douban", "tvdb"):
            provider_event = f"provider:{provider_name}"
            self.assertEqual(timeline.count(provider_event), 1)
            self.assertLess(timeline.index("ai:hypothesis"), timeline.index(provider_event))
            self.assertLess(timeline.index(provider_event), timeline.index("ai:media_metadata"))
        self.assertLess(timeline.index("ai:media_metadata"), timeline.index("confirmation"))
        self.assertLess(timeline.index("confirmation"), timeline.index("prowlarr"))
        self.assertLess(timeline.index("prowlarr"), timeline.index("submit"))

    async def test_temporary_occupancy_failure_stops_before_prowlarr(self):
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        context = SimpleNamespace()
        prowlarr_mock = AsyncMock()

        with (
            patch(
                "app.services.search_planner.infer_search_hypotheses_with_ai",
                return_value=self._hypotheses(),
            ),
            patch(
                "app.services.search_planner.infer_media_metadata_draft_with_ai",
                return_value=self._plan(),
            ),
            patch.object(
                search_handler,
                "_wikipedia_plan_provider",
                side_effect=self._provider("wikipedia"),
            ),
            patch.object(
                search_handler,
                "_douban_plan_provider",
                side_effect=self._provider("douban"),
            ),
            patch.object(
                search_handler,
                "_tvdb_plan_provider",
                side_effect=self._provider("tvdb"),
            ),
            patch.object(
                search_handler,
                "_occupied_special_numbers",
                side_effect=RuntimeError("storage unavailable"),
            ),
            patch.object(search_handler, "_search_prowlarr_with_progress", prowlarr_mock),
        ):
            state = await search_handler._start_entry_resolution(
                update,
                context,
                "想见你",
            )

        self.assertEqual(state, search_handler.ConversationHandler.END)
        self.assertIn(
            "temporary_occupancy_unavailable",
            update.message.reply_text.await_args.args[0],
        )
        prowlarr_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
