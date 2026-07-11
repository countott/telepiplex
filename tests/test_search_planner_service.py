import unittest
from unittest.mock import Mock, patch

from app.services.search_planner import (
    SearchPlanningError,
    build_confirmable_search_plan,
)
from app.utils.search_plan import TemporarySpecialAllocator


class SearchPlannerServiceTest(unittest.IsolatedAsyncioTestCase):
    def _hypotheses(self):
        return {
            "status": "ok",
            "hypotheses": [],
            "source_queries": {
                "wikipedia": ["想见你"],
                "douban": ["想见你"],
                "tvdb": ["Someday or One Day"],
            },
            "warnings": [],
        }

    def _draft(self):
        return {
            "plan_id": "ignored-by-service",
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
                    "source": "ai",
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
                    "availability": "server_down",
                    "verification": "ai_supplied_unverified",
                },
                "items": [],
                "evidence": {},
                "warnings": ["Wikipedia 未实时验证"],
            },
            "prowlarr_queries": ["Someday or One Day The Movie 2022"],
        }

    def _down_providers(self):
        return {
            name: Mock(return_value={
                "source": name,
                "status": "server_down",
                "facts": [],
                "source_urls": [],
                "error": "dns",
            })
            for name in ("wikipedia", "douban", "tvdb")
        }

    @patch("app.services.search_planner.infer_media_metadata_draft_with_ai")
    @patch("app.services.search_planner.infer_search_hypotheses_with_ai")
    async def test_all_providers_run_and_soft_failures_reach_second_ai(
        self, hypothesis_mock, metadata_mock
    ):
        hypothesis_mock.return_value = self._hypotheses()
        metadata_mock.return_value = self._draft()
        providers = self._down_providers()

        with patch("app.services.search_planner._log_info") as log_mock:
            plan = await build_confirmable_search_plan(
                "想见你",
                "plan-a",
                providers,
                lambda _contract: {100},
                TemporarySpecialAllocator(),
            )

        contract = plan["media_metadata"]
        self.assertEqual(contract["placement"]["episode_number"], 101)
        self.assertEqual(contract["warnings"], ["Wikipedia 未实时验证"])
        sources = metadata_mock.call_args.args[0]["sources"]
        self.assertEqual(
            {item["source"]: item["status"] for item in sources},
            {
                "wikipedia": "server_down",
                "douban": "server_down",
                "tvdb": "server_down",
            },
        )
        self.assertEqual(
            contract["evidence"]["provider_statuses"],
            {
                "wikipedia": "server_down",
                "douban": "server_down",
                "tvdb": "server_down",
            },
        )
        for provider in providers.values():
            provider.assert_called_once()
        log_text = "\n".join(call.args[0] for call in log_mock.call_args_list)
        self.assertIn("ai_stage=hypothesis status=ok", log_text)
        self.assertIn("source=wikipedia status=server_down", log_text)
        self.assertIn("ai_stage=media_metadata status=ok", log_text)
        self.assertIn("metadata_id=plan-a", log_text)

    @patch(
        "app.services.search_planner.infer_search_hypotheses_with_ai",
        return_value=None,
    )
    async def test_missing_first_ai_raises_before_providers(self, _hypothesis_mock):
        provider = Mock()
        with self.assertRaisesRegex(
            SearchPlanningError, "ai_hypothesis_unavailable"
        ):
            await build_confirmable_search_plan(
                "想见你",
                "plan-a",
                {"wikipedia": provider},
                lambda _contract: set(),
                TemporarySpecialAllocator(),
            )
        provider.assert_not_called()

    @patch(
        "app.services.search_planner.infer_media_metadata_draft_with_ai",
        return_value=None,
    )
    @patch("app.services.search_planner.infer_search_hypotheses_with_ai")
    async def test_missing_second_ai_raises_after_all_evidence(
        self, hypothesis_mock, _metadata_mock
    ):
        hypothesis_mock.return_value = self._hypotheses()
        providers = self._down_providers()
        with self.assertRaisesRegex(
            SearchPlanningError, "ai_media_metadata_unavailable"
        ):
            await build_confirmable_search_plan(
                "想见你",
                "plan-a",
                providers,
                lambda _contract: set(),
                TemporarySpecialAllocator(),
            )
        for provider in providers.values():
            provider.assert_called_once()

    @patch("app.services.search_planner.infer_media_metadata_draft_with_ai")
    @patch("app.services.search_planner.infer_search_hypotheses_with_ai")
    async def test_verified_official_hint_cannot_be_downgraded(
        self, hypothesis_mock, metadata_mock
    ):
        hypothesis_mock.return_value = self._hypotheses()
        draft = self._draft()
        draft["media_metadata"]["relation"]["target_series"]["external_ids"] = {
            "tvdb": "series-1"
        }
        draft["media_metadata"]["evidence"]["tvdb_official_special"] = {
            "series_id": "series-1",
            "episode_id": "episode-5",
        }
        metadata_mock.return_value = draft
        providers = self._down_providers()
        providers["tvdb"] = Mock(return_value={
            "source": "tvdb",
            "status": "ok",
            "facts": [{
                "episodes_by_series": {
                    "series-1": [{"tvdb_episode_id": "episode-5"}]
                }
            }],
            "source_urls": ["https://thetvdb.com/series/series-1"],
        })

        with self.assertRaisesRegex(SearchPlanningError, "invalid_media_metadata"):
            await build_confirmable_search_plan(
                "想见你",
                "plan-a",
                providers,
                lambda _contract: set(),
                TemporarySpecialAllocator(),
            )

    @patch("app.services.search_planner.infer_media_metadata_draft_with_ai")
    @patch("app.services.search_planner.infer_search_hypotheses_with_ai")
    async def test_verified_official_mapping_with_matching_ids_passes(
        self, hypothesis_mock, metadata_mock
    ):
        hypothesis_mock.return_value = self._hypotheses()
        draft = self._draft()
        contract = draft["media_metadata"]
        contract["relation"]["target_series"]["external_ids"] = {
            "tvdb": "series-1"
        }
        contract["placement"].update({
            "episode_number": 5,
            "mapping_kind": "tvdb_official",
            "mapping_source": "tvdb",
            "tvdb_episode_id": "episode-5",
        })
        contract["evidence"]["tvdb_official_special"] = {
            "series_id": "series-1",
            "episode_id": "episode-5",
        }
        metadata_mock.return_value = draft
        providers = self._down_providers()
        providers["tvdb"] = Mock(return_value={
            "source": "tvdb",
            "status": "ok",
            "facts": [{
                "episodes_by_series": {
                    "series-1": [{"tvdb_episode_id": "episode-5"}]
                }
            }],
            "source_urls": ["https://thetvdb.com/series/series-1"],
        })
        occupied_loader = Mock(side_effect=AssertionError(
            "official mappings must not inspect temporary occupancy"
        ))

        plan = await build_confirmable_search_plan(
            "想见你",
            "plan-a",
            providers,
            occupied_loader,
            TemporarySpecialAllocator(),
        )

        self.assertEqual(
            plan["media_metadata"]["placement"]["mapping_kind"],
            "tvdb_official",
        )
        self.assertEqual(
            plan["media_metadata"]["evidence"]["verified_tvdb_episode_keys"],
            ["series-1:episode-5"],
        )
        occupied_loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
