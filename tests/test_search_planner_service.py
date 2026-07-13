import unittest
from unittest.mock import Mock, patch

from telepiplex_media_search.planner import (
    SearchPlanningError,
    _provider_status_and_support,
    build_confirmable_search_plan,
)
from telepiplex_media_search.search_plan import TemporarySpecialAllocator
from tests.test_deterministic_planner import douban_movie, wikipedia_movie


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

    def _providers_with_tvdb_episode(
        self,
        *,
        season_number=0,
        episode_id="episode-5",
        name="someday or one day: THE MOVIE",
    ):
        providers = self._down_providers()
        providers["tvdb"] = Mock(return_value={
            "source": "tvdb",
            "status": "ok",
            "facts": [{
                "episodes_by_series": {
                    "series-1": [{
                        "tvdb_episode_id": episode_id,
                        "name": name,
                        "season_number": season_number,
                        "episode_number": 5,
                    }]
                }
            }],
            "source_urls": ["https://thetvdb.com/series/series-1"],
            "error": "",
        })
        return providers

    @patch("telepiplex_media_search.planner.infer_media_metadata_draft_with_ai")
    @patch("telepiplex_media_search.planner.infer_search_hypotheses_with_ai")
    async def test_unique_rule_plan_skips_both_ai_stages(
        self, hypothesis_mock, metadata_mock
    ):
        providers = {
            "wikipedia": Mock(return_value=wikipedia_movie()),
            "douban": Mock(return_value=douban_movie()),
            "tvdb": Mock(return_value={
                "source": "tvdb",
                "status": "disabled",
                "facts": [],
                "source_urls": [],
                "error": "missing key",
            }),
        }

        plan = await build_confirmable_search_plan(
            "盗梦空间 2010",
            "plan-rule",
            providers,
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(
            plan["media_metadata"]["evidence"]["decision"]["mode"],
            "deterministic",
        )
        hypothesis_mock.assert_not_called()
        metadata_mock.assert_not_called()
        for provider in providers.values():
            provider.assert_called_once()

    @patch("telepiplex_media_search.planner.infer_media_metadata_draft_with_ai")
    @patch("telepiplex_media_search.planner.infer_search_hypotheses_with_ai")
    async def test_all_providers_run_and_soft_failures_reach_second_ai(
        self, hypothesis_mock, metadata_mock
    ):
        hypothesis_mock.return_value = self._hypotheses()
        metadata_mock.return_value = self._draft()
        providers = self._down_providers()

        with patch("telepiplex_media_search.planner._log_info") as log_mock:
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
        self.assertEqual(
            contract["evidence"]["provider_support"],
            {
                "wikipedia": {
                    "has_facts": False,
                    "source_urls": [],
                    "stable_ids": [],
                },
                "douban": {
                    "has_facts": False,
                    "source_urls": [],
                    "stable_ids": [],
                },
                "tvdb": {
                    "has_facts": False,
                    "source_urls": [],
                    "stable_ids": [],
                },
            },
        )
        for provider in providers.values():
            self.assertEqual(provider.call_count, 2)
        log_text = "\n".join(call.args[0] for call in log_mock.call_args_list)
        self.assertIn("ai_stage=hypothesis status=ok", log_text)
        self.assertIn("source=wikipedia status=server_down", log_text)
        self.assertIn("ai_stage=media_metadata status=ok", log_text)
        self.assertIn("metadata_id=plan-a", log_text)

    @patch(
        "telepiplex_media_search.planner.infer_search_hypotheses_with_ai",
        return_value=None,
    )
    async def test_missing_first_ai_raises_after_rule_evidence(self, _hypothesis_mock):
        provider = Mock()
        with self.assertRaisesRegex(
            SearchPlanningError, "ai_unavailable_after_gate_failure"
        ):
            await build_confirmable_search_plan(
                "想见你",
                "plan-a",
                {"wikipedia": provider},
                lambda _contract: set(),
                TemporarySpecialAllocator(),
            )
        provider.assert_called_once()

    @patch(
        "telepiplex_media_search.planner.infer_media_metadata_draft_with_ai",
        return_value=None,
    )
    @patch("telepiplex_media_search.planner.infer_search_hypotheses_with_ai")
    async def test_missing_second_ai_raises_after_all_evidence(
        self, hypothesis_mock, _metadata_mock
    ):
        hypothesis_mock.return_value = self._hypotheses()
        providers = self._down_providers()
        with self.assertRaisesRegex(
            SearchPlanningError, "ai_invalid_after_gate_failure"
        ):
            await build_confirmable_search_plan(
                "想见你",
                "plan-a",
                providers,
                lambda _contract: set(),
                TemporarySpecialAllocator(),
            )
        for provider in providers.values():
            self.assertEqual(provider.call_count, 2)

    @patch("telepiplex_media_search.planner.infer_media_metadata_draft_with_ai")
    @patch("telepiplex_media_search.planner.infer_search_hypotheses_with_ai")
    async def test_matching_verified_s00_candidate_cannot_be_downgraded_when_ai_omits_hint(
        self, hypothesis_mock, metadata_mock
    ):
        hypothesis_mock.return_value = self._hypotheses()
        draft = self._draft()
        draft["media_metadata"]["identity"]["english_title"] = (
            "Someday or One Day"
        )
        draft["media_metadata"]["relation"]["target_series"]["external_ids"] = {
            "tvdb": "series-1"
        }
        metadata_mock.return_value = draft
        providers = self._providers_with_tvdb_episode()

        with self.assertRaisesRegex(SearchPlanningError, "invalid_media_metadata"):
            await build_confirmable_search_plan(
                "想见你",
                "plan-a",
                providers,
                lambda _contract: set(),
                TemporarySpecialAllocator(),
            )

    @patch("telepiplex_media_search.planner.infer_media_metadata_draft_with_ai")
    @patch("telepiplex_media_search.planner.infer_search_hypotheses_with_ai")
    async def test_matching_verified_s00_candidate_exact_official_mapping_passes(
        self, hypothesis_mock, metadata_mock
    ):
        hypothesis_mock.return_value = self._hypotheses()
        draft = self._draft()
        contract = draft["media_metadata"]
        contract["identity"]["english_title"] = "Someday or One Day"
        contract["relation"]["target_series"]["external_ids"] = {
            "tvdb": "series-1"
        }
        contract["placement"].update({
            "episode_number": 5,
            "mapping_kind": "tvdb_official",
            "mapping_source": "tvdb",
            "tvdb_episode_id": "episode-5",
        })
        metadata_mock.return_value = draft
        providers = self._providers_with_tvdb_episode()
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
            plan["media_metadata"]["evidence"][
                "tvdb_official_special_candidates"
            ],
            [{
                "series_id": "series-1",
                "episode_id": "episode-5",
                "name": "someday or one day: THE MOVIE",
                "season_number": 0,
            }],
        )
        occupied_loader.assert_not_called()

    @patch("telepiplex_media_search.planner.infer_media_metadata_draft_with_ai")
    @patch("telepiplex_media_search.planner.infer_search_hypotheses_with_ai")
    async def test_s01_episode_cannot_validate_as_tvdb_official(
        self, hypothesis_mock, metadata_mock
    ):
        hypothesis_mock.return_value = self._hypotheses()
        draft = self._draft()
        contract = draft["media_metadata"]
        contract["identity"]["english_title"] = "Someday or One Day"
        contract["relation"]["target_series"]["external_ids"] = {
            "tvdb": "series-1"
        }
        contract["placement"].update({
            "episode_number": 5,
            "mapping_kind": "tvdb_official",
            "mapping_source": "tvdb",
            "tvdb_episode_id": "episode-5",
        })
        metadata_mock.return_value = draft

        with self.assertRaisesRegex(SearchPlanningError, "invalid_media_metadata"):
            await build_confirmable_search_plan(
                "想见你",
                "plan-a",
                self._providers_with_tvdb_episode(season_number=1),
                lambda _contract: set(),
                TemporarySpecialAllocator(),
            )

    @patch("telepiplex_media_search.planner.infer_media_metadata_draft_with_ai")
    @patch("telepiplex_media_search.planner.infer_search_hypotheses_with_ai")
    async def test_unrelated_s00_candidate_does_not_force_queried_movie(
        self, hypothesis_mock, metadata_mock
    ):
        hypothesis_mock.return_value = self._hypotheses()
        draft = self._draft()
        draft["media_metadata"]["relation"]["target_series"]["external_ids"] = {
            "tvdb": "series-1"
        }
        metadata_mock.return_value = draft

        plan = await build_confirmable_search_plan(
            "想见你",
            "plan-a",
            self._providers_with_tvdb_episode(name="Behind the Scenes"),
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(
            plan["media_metadata"]["placement"]["mapping_kind"],
            "temporary_related_special",
        )
        self.assertEqual(
            plan["media_metadata"]["evidence"][
                "tvdb_official_special_candidates"
            ],
            [],
        )

    @patch("telepiplex_media_search.planner.infer_media_metadata_draft_with_ai")
    @patch("telepiplex_media_search.planner.infer_search_hypotheses_with_ai")
    async def test_provider_support_normalizes_actual_source_urls(
        self, hypothesis_mock, metadata_mock
    ):
        hypothesis_mock.return_value = self._hypotheses()
        draft = self._draft()
        source_entry = draft["media_metadata"]["source_entry"]
        source_entry.pop("availability", None)
        source_entry["verification"] = "verified"
        draft["media_metadata"]["warnings"] = []
        metadata_mock.return_value = draft
        providers = self._down_providers()
        providers["wikipedia"] = Mock(return_value={
            "source": "wikipedia",
            "status": "ok",
            "facts": [{
                "title": "想见你 (电影)",
                "original_url": "HTTPS://ZH.WIKIPEDIA.ORG/wiki/想見你_(電影)/#intro",
                "wikibase_item": "Q115000000",
            }],
            "source_urls": [],
            "error": "",
        })

        plan = await build_confirmable_search_plan(
            "想见你",
            "plan-a",
            providers,
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(
            plan["media_metadata"]["evidence"]["provider_support"][
                "wikipedia"
            ],
            {
                "has_facts": True,
                "source_urls": [
                    "https://zh.wikipedia.org/wiki/想見你_(電影)"
                ],
                "stable_ids": ["Q115000000"],
            },
        )

    @patch("telepiplex_media_search.planner.infer_media_metadata_draft_with_ai")
    @patch("telepiplex_media_search.planner.infer_search_hypotheses_with_ai")
    async def test_ok_provider_rejects_ai_url_unrelated_to_actual_evidence(
        self, hypothesis_mock, metadata_mock
    ):
        hypothesis_mock.return_value = self._hypotheses()
        draft = self._draft()
        source_entry = draft["media_metadata"]["source_entry"]
        source_entry.pop("availability", None)
        source_entry["verification"] = "verified"
        draft["media_metadata"]["warnings"] = []
        metadata_mock.return_value = draft
        providers = self._down_providers()
        providers["wikipedia"] = Mock(return_value={
            "source": "wikipedia",
            "status": "ok",
            "facts": [{
                "title": "无关作品",
                "url": "https://zh.wikipedia.org/wiki/無關作品",
                "wikibase_item": "Q999999",
            }],
            "source_urls": ["https://zh.wikipedia.org/wiki/無關作品"],
            "error": "",
        })

        with self.assertRaisesRegex(SearchPlanningError, "invalid_media_metadata"):
            await build_confirmable_search_plan(
                "想见你",
                "plan-a",
                providers,
                lambda _contract: set(),
                TemporarySpecialAllocator(),
            )

    def test_provider_support_collects_only_provider_specific_stable_ids(self):
        statuses, support = _provider_status_and_support([
            {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "url": "HTTPS://EN.WIKIPEDIA.ORG/wiki/Example/",
                    "wikibase_item": "Q42",
                    "id": "generic-wikipedia-id",
                }],
                "source_urls": [],
            },
            {
                "source": "douban",
                "status": "ok",
                "facts": [{
                    "subject_id": 35314632,
                    "external_ids": {"douban_subject": "30468961"},
                    "id": "generic-douban-id",
                }],
                "source_urls": [],
            },
            {
                "source": "tvdb",
                "status": "ok",
                "facts": [{
                    "movies": [{
                        "tvdb_movie_id": 123,
                        "id": "generic-tvdb-id",
                    }],
                    "episodes": [{"tvdb_episode_id": "episode-5"}],
                }],
                "source_urls": [],
            },
        ])

        self.assertEqual(
            statuses,
            {"wikipedia": "ok", "douban": "ok", "tvdb": "ok"},
        )
        self.assertEqual(
            support["wikipedia"],
            {
                "has_facts": True,
                "source_urls": ["https://en.wikipedia.org/wiki/Example"],
                "stable_ids": ["Q42"],
            },
        )
        self.assertEqual(support["douban"]["stable_ids"], ["35314632", "30468961"])
        self.assertEqual(support["tvdb"]["stable_ids"], ["123", "episode-5"])
        self.assertNotIn("generic-wikipedia-id", support["wikipedia"]["stable_ids"])
        self.assertNotIn("generic-douban-id", support["douban"]["stable_ids"])
        self.assertNotIn("generic-tvdb-id", support["tvdb"]["stable_ids"])


if __name__ == "__main__":
    unittest.main()
