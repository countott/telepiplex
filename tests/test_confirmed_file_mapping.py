import unittest

from app.utils.confirmed_file_mapping import (
    map_confirmed_files,
    unresolved_mapping_context,
)


class ConfirmedFileMappingTest(unittest.TestCase):
    def _contract(self):
        return {
            "schema_version": 1,
            "metadata_id": "metadata-series",
            "confirmed": True,
            "identity": {
                "chinese_title": "测试剧",
                "english_title": "Test Show",
                "year": "2026",
                "content_kind": "series",
                "external_ids": {},
            },
            "relation": {
                "type": "primary",
                "target_series": {},
                "source": "user",
            },
            "placement": {
                "library_type": "series",
                "category_kind": "live_action_series",
                "season_number": None,
                "episode_number": None,
                "mapping_kind": "standalone",
                "mapping_source": "user",
                "tvdb_episode_id": "",
            },
            "source_entry": {},
            "items": [
                {
                    "item_id": "episode-1",
                    "content_role": "main_episode",
                    "season_number": 1,
                    "episode_number": 1,
                },
                {
                    "item_id": "episode-2",
                    "content_role": "main_episode",
                    "season_number": 1,
                    "episode_number": 2,
                },
            ],
            "evidence": {},
            "warnings": [],
        }

    @staticmethod
    def _files(*paths):
        return [
            {
                "name": path.rsplit("/", 1)[-1],
                "relative_path": path,
                "is_dir": False,
                "size": 1024,
            }
            for path in paths
        ]

    def test_rules_map_sxxeyy_and_nxeyy_without_ai(self):
        result = map_confirmed_files(
            self._contract(),
            self._files("Disc/Test.Show.S01E01.mkv", "Test.Show.1x02.mkv"),
        )

        self.assertEqual(result["state"], "completed")
        self.assertEqual(
            [
                (item["source_file"], item["season_number"], item["episode_number"])
                for item in result["mappings"]
            ],
            [
                ("Disc/Test.Show.S01E01.mkv", 1, 1),
                ("Test.Show.1x02.mkv", 1, 2),
            ],
        )
        self.assertTrue(all(item["mapping_source"] == "rule" for item in result["mappings"]))
        self.assertEqual(result["missing_items"], [])
        self.assertEqual(result["unexpected_sources"], [])

    def test_unique_source_hint_maps_before_ai(self):
        contract = self._contract()
        contract["items"][0]["source_hint"] = "episode-one-final.mkv"
        contract["items"] = contract["items"][:1]

        result = map_confirmed_files(
            contract,
            self._files("nested/episode-one-final.mkv"),
        )

        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["mappings"][0]["item_id"], "episode-1")
        self.assertEqual(result["mappings"][0]["mapping_source"], "rule")

    def test_partial_coverage_exposes_missing_and_unexpected(self):
        result = map_confirmed_files(
            self._contract(),
            self._files("Test.Show.S01E01.mkv", "Bonus.Feature.mkv"),
        )

        self.assertEqual(result["state"], "partial")
        self.assertEqual([item["item_id"] for item in result["missing_items"]], ["episode-2"])
        self.assertEqual(result["unexpected_sources"], ["Bonus.Feature.mkv"])

        context = unresolved_mapping_context(self._contract(), self._files(
            "Test.Show.S01E01.mkv",
            "Bonus.Feature.mkv",
        ), result)
        self.assertEqual(
            [item["relative_path"] for item in context["file_tree"]],
            ["Bonus.Feature.mkv"],
        )
        self.assertEqual(
            [item["item_id"] for item in context["confirmed_items"]],
            ["episode-2"],
        )

    def test_valid_ai_mapping_only_fills_unresolved_real_target(self):
        result = map_confirmed_files(
            self._contract(),
            self._files("Test.Show.S01E01.mkv", "Episode.Two.Final.mkv"),
            ai_episode_map=[{
                "source_file": "Episode.Two.Final.mkv",
                "season_number": 1,
                "episode_number": 2,
            }],
        )

        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["mappings"][1]["mapping_source"], "ai")
        self.assertEqual(result["mappings"][1]["item_id"], "episode-2")

    def test_ai_invented_source_and_out_of_contract_target_are_rejected(self):
        result = map_confirmed_files(
            self._contract(),
            self._files("Unknown.One.mkv"),
            ai_episode_map=[
                {
                    "source_file": "Invented.mkv",
                    "season_number": 1,
                    "episode_number": 1,
                },
                {
                    "source_file": "Unknown.One.mkv",
                    "season_number": 9,
                    "episode_number": 9,
                },
            ],
        )

        self.assertEqual(result["state"], "failed")
        self.assertEqual(
            [item["reason"] for item in result["rejected"]],
            ["source_not_unresolved", "target_not_unresolved"],
        )
        self.assertEqual(result["unexpected_sources"], ["Unknown.One.mkv"])

    def test_ai_duplicate_source_and_target_are_rejected(self):
        result = map_confirmed_files(
            self._contract(),
            self._files("One.mkv", "Two.mkv"),
            ai_episode_map=[
                {"source_file": "One.mkv", "season_number": 1, "episode_number": 1},
                {"source_file": "One.mkv", "season_number": 1, "episode_number": 2},
                {"source_file": "Two.mkv", "season_number": 1, "episode_number": 1},
            ],
        )

        self.assertEqual(len(result["mappings"]), 1)
        self.assertEqual(
            [item["reason"] for item in result["rejected"]],
            ["source_already_mapped", "target_already_mapped"],
        )
        self.assertEqual(result["state"], "partial")

    def test_locked_special_without_items_still_has_one_expected_target(self):
        contract = self._contract()
        contract["identity"]["content_kind"] = "extension_movie"
        contract["relation"]["target_series"] = {
            "chinese_title": "测试剧",
            "english_title": "Test Show",
            "year": "2026",
            "external_ids": {},
        }
        contract["placement"].update({
            "mapping_kind": "temporary_related_special",
            "season_number": 0,
            "episode_number": 100,
        })
        contract["source_entry"] = {
            "title": "测试电影",
            "url": "https://example.test/movie",
        }
        contract["items"] = []

        result = map_confirmed_files(
            contract,
            self._files("Test.Show.S00E100.mkv"),
        )

        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["mappings"][0]["item_id"], "S00E100")

    def test_non_video_nodes_are_not_mapping_sources_or_unexpected_videos(self):
        files = self._files("Test.Show.S01E01.mkv")
        files.append({
            "name": "Test.Show.S01E01.srt",
            "relative_path": "Test.Show.S01E01.srt",
            "is_dir": False,
            "is_video": False,
            "size": 2048,
        })
        contract = self._contract()
        contract["items"] = contract["items"][:1]

        result = map_confirmed_files(contract, files)

        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["unexpected_sources"], [])

    def test_video_below_minimum_size_is_never_available_to_rule_or_ai(self):
        contract = self._contract()
        contract["items"] = contract["items"][:1]
        files = self._files("Test.Show.S01E01.mkv")
        files[0]["size"] = 100 * 1024 * 1024

        result = map_confirmed_files(
            contract,
            files,
            ai_episode_map=[{
                "source_file": "Test.Show.S01E01.mkv",
                "season_number": 1,
                "episode_number": 1,
            }],
            minimum_video_size=400 * 1024 * 1024,
        )

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["mappings"], [])
        self.assertEqual(result["unexpected_sources"], [])
        self.assertEqual(result["ineligible_sources"], ["Test.Show.S01E01.mkv"])
        self.assertEqual(result["rejected"][0]["reason"], "source_not_unresolved")


if __name__ == "__main__":
    unittest.main()
