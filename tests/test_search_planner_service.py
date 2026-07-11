import unittest
from unittest.mock import Mock, patch

from app.services.search_planner import SearchPlanningError, build_confirmable_plan
from app.utils.search_plan import TemporarySpecialAllocator


class SearchPlannerServiceTest(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.search_planner.infer_download_plan_with_ai")
    @patch("app.services.search_planner.infer_search_hypotheses_with_ai")
    async def test_all_providers_run_and_soft_failures_reach_second_ai(
        self, hypothesis_mock, plan_mock
    ):
        hypothesis_mock.return_value = {
            "status": "ok",
            "hypotheses": [],
            "source_queries": {
                "wikipedia": ["想见你"],
                "douban": ["想见你"],
                "tvdb": ["Someday or One Day"],
            },
            "warnings": [],
        }
        plan_mock.return_value = {
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
                "source": "ai",
            },
            "placement": {
                "library_type": "series",
                "category_kind": "live_action_series",
                "season_number": 0,
                "episode_number": None,
                "mapping_kind": "temporary_related_special",
                "mapping_source": "local_allocator",
            },
            "source_entry": {
                "title": "想见你 (电影)",
                "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
                "provider": "wikipedia",
                "availability": "server_down",
                "verification": "ai_supplied_unverified",
            },
            "prowlarr_queries": ["Someday or One Day The Movie 2022"],
            "evidence": {},
            "warnings": ["未实时验证"],
            "confirmed": False,
        }
        providers = {
            "wikipedia": Mock(
                return_value={
                    "source": "wikipedia",
                    "status": "server_down",
                    "facts": [],
                    "source_urls": [],
                    "error": "dns",
                }
            ),
            "douban": Mock(
                return_value={
                    "source": "douban",
                    "status": "server_down",
                    "facts": [],
                    "source_urls": [],
                    "error": "dns",
                }
            ),
            "tvdb": Mock(
                return_value={
                    "source": "tvdb",
                    "status": "server_down",
                    "facts": [],
                    "source_urls": [],
                    "error": "dns",
                }
            ),
        }

        plan = await build_confirmable_plan(
            "想见你",
            "plan-a",
            providers,
            lambda _draft: {100},
            TemporarySpecialAllocator(),
        )

        self.assertEqual(plan["placement"]["episode_number"], 101)
        self.assertEqual(len(plan_mock.call_args.args[0]["sources"]), 3)
        for provider in providers.values():
            provider.assert_called_once()

    @patch(
        "app.services.search_planner.infer_search_hypotheses_with_ai",
        return_value=None,
    )
    async def test_missing_first_ai_raises_before_providers(self, _hypothesis_mock):
        provider = Mock()
        with self.assertRaisesRegex(
            SearchPlanningError, "ai_hypothesis_unavailable"
        ):
            await build_confirmable_plan(
                "想见你",
                "plan-a",
                {"wikipedia": provider},
                lambda _draft: set(),
                TemporarySpecialAllocator(),
            )
        provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
