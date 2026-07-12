import unittest

from telepiplex_media_search.search_plan import (
    TemporarySpecialAllocator,
    confirm_media_metadata,
    finalize_search_plan,
    validate_draft_search_plan,
)


class SearchPlanTest(unittest.TestCase):
    def _draft(self):
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
                "evidence": {
                    "provider_statuses": {
                        "wikipedia": "ok",
                        "douban": "ok",
                        "tvdb": "not_found",
                    },
                    "provider_support": {
                        "wikipedia": {
                            "has_facts": True,
                            "source_urls": [
                                "https://zh.wikipedia.org/wiki/想見你_(電影)"
                            ],
                            "stable_ids": ["Q115000000"],
                        },
                        "douban": {
                            "has_facts": True,
                            "source_urls": [],
                            "stable_ids": [],
                        },
                        "tvdb": {
                            "has_facts": False,
                            "source_urls": [],
                            "stable_ids": [],
                        },
                    },
                },
                "warnings": [],
            },
            "prowlarr_queries": ["Someday or One Day The Movie 2022"],
        }

    def test_finalize_allocates_then_confirm_returns_only_core_contract(self):
        draft = self._draft()
        final = finalize_search_plan(draft, TemporarySpecialAllocator(), {100})
        contract = confirm_media_metadata(final)
        self.assertEqual(final["media_metadata"]["placement"]["episode_number"], 101)
        self.assertEqual(contract["metadata_id"], "plan-a")
        self.assertTrue(contract["confirmed"])
        self.assertNotIn("prowlarr_queries", contract)
        self.assertNotIn("plan_id", contract)

    def test_allocator_starts_at_100_and_skips_occupied_and_reserved_values(self):
        allocator = TemporarySpecialAllocator()
        self.assertEqual(allocator.reserve("plan-a", "show-a", set()), 100)
        self.assertEqual(allocator.reserve("plan-a", "show-a", {100, 101}), 100)
        self.assertEqual(allocator.reserve("plan-b", "show-a", {100, 102}), 101)
        self.assertEqual(allocator.reserve("plan-c", "show-a", {100, 101, 102}), 103)
        self.assertEqual(allocator.reserve("plan-d", "show-b", set()), 100)
        self.assertEqual(
            TemporarySpecialAllocator().reserve("after-restart", "show-a", set()),
            100,
        )

    def test_draft_requires_search_queries_and_findable_source(self):
        draft = self._draft()
        draft["prowlarr_queries"] = []
        self.assertIsNone(validate_draft_search_plan(draft))
        draft = self._draft()
        draft["media_metadata"]["source_entry"]["url"] = ""
        self.assertIsNone(validate_draft_search_plan(draft))

    def test_queries_are_normalized_before_first_query_is_consumed(self):
        draft = self._draft()
        draft["prowlarr_queries"] = ["", "  valid query  "]
        normalized = validate_draft_search_plan(draft)
        self.assertEqual(normalized["prowlarr_queries"], ["valid query"])

    def test_temporary_source_down_requires_explicit_unverified_warning(self):
        draft = self._draft()
        draft["media_metadata"]["evidence"]["provider_statuses"][
            "wikipedia"
        ] = "server_down"
        draft["media_metadata"]["source_entry"].update({
            "availability": "server_down",
            "verification": "ai_supplied_unverified",
        })
        self.assertIsNone(validate_draft_search_plan(draft))
        draft["media_metadata"]["warnings"] = [
            "Wikipedia不可用，来源条目由AI提供，未实时验证。"
        ]
        self.assertIsNotNone(validate_draft_search_plan(draft))

        draft["media_metadata"]["source_entry"]["verification"] = "claimed"
        self.assertIsNone(validate_draft_search_plan(draft))

    def test_temporary_requires_known_provider_in_injected_statuses(self):
        draft = self._draft()
        draft["media_metadata"]["source_entry"]["provider"] = ""
        self.assertIsNone(validate_draft_search_plan(draft))

        draft = self._draft()
        draft["media_metadata"]["source_entry"]["provider"] = "invented"
        self.assertIsNone(validate_draft_search_plan(draft))

    def test_temporary_ok_provider_requires_actual_support(self):
        draft = self._draft()
        draft["media_metadata"]["evidence"]["provider_support"]["wikipedia"] = {
            "has_facts": False,
            "source_urls": [],
            "stable_ids": [],
        }
        self.assertIsNone(validate_draft_search_plan(draft))

    def test_temporary_ok_provider_rejects_unrelated_facts_and_source_url(self):
        draft = self._draft()
        draft["media_metadata"]["evidence"]["provider_support"]["wikipedia"] = {
            "has_facts": True,
            "source_urls": ["https://zh.wikipedia.org/wiki/無關作品"],
            "stable_ids": ["Q999999"],
        }

        self.assertIsNone(validate_draft_search_plan(draft))

        draft["media_metadata"]["source_entry"]["url"] = (
            "HTTPS://ZH.WIKIPEDIA.ORG/wiki/無關作品/"
        )
        self.assertIsNotNone(validate_draft_search_plan(draft))

    def test_temporary_ok_provider_accepts_normalized_matching_source_url(self):
        draft = self._draft()
        draft["media_metadata"]["evidence"]["provider_support"]["wikipedia"] = {
            "has_facts": False,
            "source_urls": [
                "https://zh.wikipedia.org/wiki/想見你_(電影)"
            ],
            "stable_ids": [],
        }
        draft["media_metadata"]["source_entry"]["url"] = (
            "HTTPS://ZH.WIKIPEDIA.ORG/wiki/想見你_(電影)/"
        )
        self.assertIsNotNone(validate_draft_search_plan(draft))

    def test_temporary_ok_provider_external_id_must_match_stable_id(self):
        draft = self._draft()
        source_entry = draft["media_metadata"]["source_entry"]
        source_entry["url"] = ""
        source_entry["external_id"] = "Q-INVENTED"
        draft["media_metadata"]["evidence"]["provider_support"]["wikipedia"] = {
            "has_facts": True,
            "source_urls": ["https://zh.wikipedia.org/wiki/無關作品"],
            "stable_ids": ["Q115000000"],
        }

        self.assertIsNone(validate_draft_search_plan(draft))

        source_entry["external_id"] = "q115000000"
        self.assertIsNotNone(validate_draft_search_plan(draft))

    def test_temporary_ok_provider_rejects_malformed_stable_id_container(self):
        for malformed in ({}, "", 0, False):
            with self.subTest(malformed=malformed):
                draft = self._draft()
                draft["media_metadata"]["evidence"]["provider_support"][
                    "wikipedia"
                ]["stable_ids"] = malformed
                self.assertIsNone(validate_draft_search_plan(draft))

    def test_official_tvdb_hint_cannot_be_downgraded(self):
        draft = self._draft()
        candidates = [{
            "series_id": "series-1",
            "episode_id": "episode-5",
            "name": "Someday or One Day: The Movie",
            "season_number": 0,
        }]
        draft["media_metadata"]["evidence"][
            "verified_tvdb_special_candidates"
        ] = candidates
        draft["media_metadata"]["evidence"][
            "tvdb_official_special_candidates"
        ] = candidates
        self.assertIsNone(validate_draft_search_plan(draft))

    def test_standalone_drafts_cover_all_four_categories(self):
        pairs = {
            "live_action_series": "series",
            "live_action_movie": "movie",
            "animated_movie": "movie",
            "animated_series": "series",
        }
        for category_kind, library_type in pairs.items():
            with self.subTest(category_kind=category_kind):
                draft = self._draft()
                draft["media_metadata"]["relation"]["target_series"] = {}
                draft["media_metadata"]["placement"].update({
                    "library_type": library_type,
                    "category_kind": category_kind,
                    "season_number": None,
                    "episode_number": None,
                    "mapping_kind": "standalone",
                    "mapping_source": "ai",
                })
                if library_type == "series":
                    draft["media_metadata"]["identity"]["content_kind"] = "series"
                    draft["media_metadata"]["items"] = [{
                        "content_role": "main_episode",
                        "season_number": 1,
                        "episode_number": 1,
                    }]
                self.assertIsNotNone(
                    finalize_search_plan(draft, TemporarySpecialAllocator(), set())
                )


if __name__ == "__main__":
    unittest.main()
