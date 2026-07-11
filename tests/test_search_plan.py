import unittest

from app.utils.search_plan import (
    TemporarySpecialAllocator,
    attach_download_plan,
    confirm_download_plan,
    finalize_download_plan,
    validate_draft_download_plan,
)


class SearchPlanTest(unittest.TestCase):
    def _draft(self):
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
                "episode_number": None,
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

    def test_temporary_plan_requires_findable_source_entry(self):
        draft = self._draft()
        draft["source_entry"]["url"] = ""
        self.assertIsNone(validate_draft_download_plan(draft))

    def test_allocator_starts_at_100_and_skips_occupied_and_reserved(self):
        allocator = TemporarySpecialAllocator()
        self.assertEqual(allocator.reserve("plan-a", {100}), 101)
        self.assertEqual(allocator.reserve("plan-b", {100}), 102)
        self.assertEqual(allocator.reserve("plan-a", set()), 101)

    def test_finalize_then_confirm_and_attach_is_non_mutating(self):
        draft = self._draft()
        allocator = TemporarySpecialAllocator()
        final_plan = finalize_download_plan(draft, allocator, {100})
        confirmed = confirm_download_plan(final_plan)
        metadata = attach_download_plan({"source": "confirmed"}, confirmed)
        self.assertEqual(final_plan["placement"]["episode_number"], 101)
        self.assertFalse(final_plan["confirmed"])
        self.assertTrue(metadata["download_plan"]["confirmed"])
        self.assertEqual(draft["placement"]["episode_number"], None)

    def test_new_allocator_after_restart_has_no_old_reservations(self):
        allocator = TemporarySpecialAllocator()
        self.assertEqual(allocator.reserve("plan-a", set()), 100)
        restarted_allocator = TemporarySpecialAllocator()
        self.assertEqual(restarted_allocator.reserve("plan-b", set()), 100)


if __name__ == "__main__":
    unittest.main()
