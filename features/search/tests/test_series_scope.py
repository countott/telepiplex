import unittest
from copy import deepcopy
from datetime import date

from telepiplex_search.series_scope import (
    SeriesScopeError,
    apply_inventory_probe_scope,
    apply_series_scope,
    series_inventory,
    series_seasons,
    series_scope_options,
)


def contract(
    *,
    seasons=(1,),
    scope="movie_or_series",
    season_number=None,
    episode_number=None,
    ongoing=False,
):
    items = []
    for season in seasons:
        for episode in range(1, 4):
            items.append({
                "item_id": f"{season}-{episode}",
                "content_role": "main_episode",
                "season_number": season,
                "episode_number": episode,
                "aired": (
                    "2027-01-01"
                    if ongoing and episode == 3
                    else "2026-01-01"
                ),
            })
    return {
        "identity": {"english_title": "The Glory", "year": "2022"},
        "retrieval": {"media_type": "series", "scope": "work", "query": ""},
        "placement": {
            "library_type": "series",
            "category_kind": "live_action_series",
            "mapping_kind": "standalone",
            "season_number": None,
            "episode_number": None,
        },
        "items": items,
        "evidence": {
            "decision": {
                "scope": scope,
                "season_number": season_number,
                "episode_number": episode_number,
            },
            "series_inventory": {
                "season_totals": {season: 3 for season in seasons},
            },
        },
    }


class SeriesScopeTest(unittest.TestCase):
    def test_inventory_probe_keeps_honey_and_clover_38_files_without_air_dates(self):
        value = contract(seasons=())
        value["identity"].update({
            "chinese_title": "蜂蜜与四叶草",
            "english_title": "Honey and Clover",
        })
        value["items"] = [
            {
                "item_id": f"s{season}e{episode}",
                "content_role": "main_episode",
                "season_number": season,
                "episode_number": episode,
                "aired": "",
            }
            for season, total in ((1, 26), (2, 12))
            for episode in range(1, total + 1)
        ]
        probe = {
            "content_shape": "multi_season_episode_pack",
            "observed_seasons": [1, 2],
            "observed_episodes": [
                {"season_number": season, "episode_number": episode}
                for season, total in ((1, 26), (2, 12))
                for episode in range(1, total + 1)
            ],
            "video_count": 38,
        }

        scoped = apply_inventory_probe_scope(value, probe)

        self.assertEqual(len(scoped["items"]), 38)
        self.assertEqual(
            {item["season_number"] for item in scoped["items"]},
            {1, 2},
        )
        self.assertEqual(
            scoped["identity"]["chinese_title"],
            "蜂蜜与四叶草",
        )
        self.assertEqual(
            scoped["evidence"]["decision"]["scope_source"],
            "file_probe",
        )

    def test_inventory_probe_reports_exact_missing_coordinate(self):
        value = contract(seasons=(1,))
        probe = {
            "content_shape": "single_episode",
            "observed_episodes": [{
                "season_number": 1,
                "episode_number": 9,
            }],
        }

        with self.assertRaisesRegex(
            SeriesScopeError,
            r"probe_inventory_mismatch missing=S01E09",
        ):
            apply_inventory_probe_scope(value, probe)

    def test_one_and_multiple_season_options(self):
        self.assertEqual(
            series_scope_options(contract(seasons=(1,))),
            ("whole_series",),
        )
        self.assertEqual(
            series_scope_options(contract(seasons=(1, 2))),
            ("whole_series", "season", "episode"),
        )

    def test_season_count_only_does_not_claim_a_verified_whole_season(self):
        value = contract(seasons=())
        value["identity"]["season_count"] = 3

        self.assertEqual(series_seasons(value), (1, 2, 3))
        self.assertEqual(
            series_scope_options(value),
            ("season",),
        )
        with self.assertRaisesRegex(SeriesScopeError, "season_incomplete"):
            apply_series_scope(
                value,
                "season",
                season_number=2,
            )

    def test_explicit_season_requires_all_or_single_episode_choice(self):
        self.assertEqual(
            series_scope_options(
                contract(seasons=(1,), scope="season", season_number=1)
            ),
            ("season_all", "season_episode"),
        )

    def test_explicit_episode_builds_exact_query(self):
        value = apply_series_scope(
            contract(
                seasons=(1,),
                scope="episode",
                season_number=1,
                episode_number=2,
            ),
            "episode",
            season_number=1,
            episode_number=2,
            today=date(2026, 7, 16),
        )

        self.assertEqual(value["retrieval"]["query"], "The Glory S01E02")
        self.assertEqual(len(value["items"]), 1)

    def test_whole_series_query_does_not_use_first_episode(self):
        value = apply_series_scope(
            contract(seasons=(1,)),
            "whole_series",
            today=date(2026, 7, 16),
        )

        self.assertEqual(value["retrieval"]["query"], "The Glory")
        self.assertNotIn("S01E01", value["retrieval"]["query"])

    def test_unreleased_episode_is_rejected(self):
        with self.assertRaisesRegex(SeriesScopeError, "episode_not_aired"):
            apply_series_scope(
                contract(seasons=(1,), ongoing=True),
                "episode",
                season_number=1,
                episode_number=3,
                today=date(2026, 7, 16),
            )

    def test_missing_date_is_unknown_not_aired(self):
        value = contract(seasons=())
        value["items"] = [{
            "item_id": "s1e1",
            "content_role": "main_episode",
            "season_number": 1,
            "episode_number": 1,
            "aired": "",
        }]
        value["evidence"]["series_inventory"] = {
            "season_totals": {1: 1},
        }

        inventory = series_inventory(value, today=date(2026, 8, 14))

        self.assertEqual(inventory.aired_by_season, {})
        self.assertEqual(inventory.state_by_season, {1: "unknown"})

    def test_one_hundred_years_is_completed_then_incomplete(self):
        value = contract(seasons=())
        value["items"] = [
            *({
                "item_id": f"s1e{number}",
                "content_role": "main_episode",
                "season_number": 1,
                "episode_number": number,
                "aired": "2024-12-11",
            } for number in range(1, 9)),
            *({
                "item_id": f"s2e{number}",
                "content_role": "main_episode",
                "season_number": 2,
                "episode_number": number,
                "aired": "2026-08-05" if number < 8 else "2026-08-26",
            } for number in range(1, 9)),
        ]
        value["evidence"]["series_inventory"] = {
            "season_totals": {1: 8, 2: 8},
        }

        inventory = series_inventory(value, today=date(2026, 8, 14))

        self.assertEqual(
            inventory.state_by_season,
            {1: "completed", 2: "incomplete"},
        )
        self.assertEqual(inventory.aired_by_season[2], tuple(range(1, 8)))

    def test_incomplete_inventory_hides_unsafe_aggregate_scopes(self):
        value = contract(seasons=(1,), ongoing=True)

        self.assertEqual(series_scope_options(value), ("episode",))
        with self.assertRaisesRegex(SeriesScopeError, "series_incomplete"):
            apply_series_scope(
                value,
                "whole_series",
                today=date(2026, 8, 14),
            )
        with self.assertRaisesRegex(SeriesScopeError, "season_incomplete"):
            apply_series_scope(
                value,
                "season",
                season_number=1,
                today=date(2026, 8, 14),
            )

    def test_scope_application_does_not_mutate_original(self):
        original = contract(seasons=(1,))
        snapshot = deepcopy(original)

        apply_series_scope(
            original,
            "season",
            season_number=1,
            today=date(2026, 7, 16),
        )

        self.assertEqual(original, snapshot)


if __name__ == "__main__":
    unittest.main()
