import unittest

from app.utils.confirmed_download_plan import (
    extract_confirmed_download_plan,
    locked_episode,
)


class ConfirmedDownloadPlanTest(unittest.TestCase):
    def test_extracts_only_confirmed_schema_v1_plan(self):
        plan = {
            "schema_version": 1,
            "plan_id": "plan-a",
            "confirmed": True,
            "placement": {
                "library_type": "series",
                "season_number": 0,
                "episode_number": 100,
                "mapping_kind": "temporary_related_special",
            },
            "relation": {"target_series_title": "Someday or One Day"},
            "source_entry": {
                "title": "想见你 (电影)",
                "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
            },
        }
        extracted = extract_confirmed_download_plan({"download_plan": plan})
        self.assertEqual(locked_episode(extracted), (0, 100))
        plan["confirmed"] = False
        self.assertIsNone(extract_confirmed_download_plan({"download_plan": plan}))

    def test_temporary_plan_without_source_locator_is_rejected(self):
        plan = {
            "schema_version": 1,
            "confirmed": True,
            "placement": {
                "library_type": "series",
                "season_number": 0,
                "episode_number": 100,
                "mapping_kind": "temporary_related_special",
            },
            "source_entry": {"title": "想见你 (电影)", "url": ""},
        }
        self.assertIsNone(extract_confirmed_download_plan({"download_plan": plan}))


if __name__ == "__main__":
    unittest.main()
