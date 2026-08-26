import copy
import unittest

from telepiplex_plugin_sdk.media_metadata_v2 import (
    build_media_metadata_v2_id,
)
from telepiplex_rename.media_metadata_v2 import (
    naming_identity_from_v2,
    observed_episode_plan,
    scope_allows_coordinate,
)


def contract(kind="whole_series"):
    value = {
        "schema_version": 2,
        "confirmed": True,
        "identity": {
            "primary_ref": {"provider": "wikidata", "id": "Q1"},
            "provider_refs": {"wikidata": "Q1"},
            "media_type": "series",
            "title_zh": "中文剧集",
            "title_en": "English Series",
            "title_original": "English Series",
            "year": 2024,
        },
        "scope": {
            "kind": kind,
            "season_number": 2 if kind in {"season", "episode"} else None,
            "episode_number": 3 if kind == "episode" else None,
        },
        "placement": {"category_kind": "live_action_series"},
    }
    value["metadata_id"] = build_media_metadata_v2_id(value)
    return value


class RenameMediaMetadataV2Test(unittest.TestCase):
    def test_naming_and_scope_are_pure_and_do_not_need_provider_inventory(self):
        value = contract("whole_series")
        before = copy.deepcopy(value)

        naming = naming_identity_from_v2(value)
        plan = observed_episode_plan(value, [
            {"relative_path": "Show.S01E01.mkv", "is_dir": False},
            {"relative_path": "Show.S02E03.mkv", "is_dir": False},
        ])

        self.assertEqual(naming["chinese_title"], "中文剧集")
        self.assertEqual(naming["english_title"], "English Series")
        self.assertEqual(
            [(item["season_number"], item["episode_number"]) for item in plan["episode_map"]],
            [(1, 1), (2, 3)],
        )
        self.assertEqual(value, before)

    def test_game_life_uses_verified_english_not_japanese_original(self):
        value = contract()
        value["identity"].update({
            "title_zh": "游戏人生",
            "title_en": "No Game, No Life",
            "title_original": "ノーゲーム・ノーライフ",
        })

        naming = naming_identity_from_v2(value)

        self.assertEqual(naming["english_title"], "No Game, No Life")
        self.assertNotIn("ノーゲーム", naming["english_title"])

    def test_private_v1_adapter_is_removed(self):
        import telepiplex_rename.media_metadata_v2 as media_metadata_v2

        self.assertFalse(hasattr(media_metadata_v2, "private_v1_adapter_from_v2"))

    def test_season_and_episode_scopes_never_broaden(self):
        self.assertTrue(scope_allows_coordinate(contract("season"), 2, 9))
        self.assertFalse(scope_allows_coordinate(contract("season"), 1, 9))
        self.assertTrue(scope_allows_coordinate(contract("episode"), 2, 3))
        self.assertFalse(scope_allows_coordinate(contract("episode"), 2, 4))

    def test_duplicate_and_unparseable_files_stay_unresolved(self):
        plan = observed_episode_plan(contract(), [
            {"relative_path": "a/Show.S01E01.mkv", "is_dir": False},
            {"relative_path": "b/Show.S01E01.mp4", "is_dir": False},
            {"relative_path": "Show.Special.mkv", "is_dir": False},
        ])

        self.assertEqual(plan["episode_map"], [])
        self.assertEqual(
            {item["reason_code"] for item in plan["unresolved"]},
            {"duplicate_coordinate", "coordinate_unparseable"},
        )


if __name__ == "__main__":
    unittest.main()
