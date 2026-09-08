import unittest

from telepiplex_search.series_topology import (
    ProviderOrderConflict,
    select_series_topology,
)


def _episodes(provider, seasons):
    return tuple(
        {
            "season_number": season,
            "episode_number": episode,
            "aired": "2024-01-01",
            f"{provider}_episode_id": f"{provider}-{season}-{episode}",
        }
        for season, total in seasons.items()
        for episode in range(1, total + 1)
    )


class SeriesTopologyTest(unittest.TestCase):
    def test_available_tvdb_order_is_not_replaced_by_larger_tmdb_profile(self):
        tvdb = _episodes("tvdb", {1: 20, 2: 21, 3: 22})
        tmdb = _episodes("tmdb", {1: 366, 2: 40})

        selected = select_series_topology(
            {"tvdb": tvdb, "tmdb": tmdb},
            trusted_episode_count=406,
        )

        self.assertEqual(selected.provider, "tvdb")
        self.assertEqual(len(selected.items), 63)
        self.assertEqual(selected.season_totals, {1: 20, 2: 21, 3: 22})
        self.assertNotEqual(len(selected.items), 41)

    def test_identical_coordinates_merge_downstream_ids(self):
        tvdb = _episodes("tvdb", {1: 2})
        tmdb = _episodes("tmdb", {1: 2})

        selected = select_series_topology(
            {"tvdb": tvdb, "tmdb": tmdb},
        )

        self.assertEqual(selected.provider, "tvdb")
        self.assertEqual(len(selected.items), 2)
        self.assertTrue(all(item["tvdb_episode_id"] for item in selected.items))
        self.assertTrue(all(item["tmdb_episode_id"] for item in selected.items))

    def test_available_tvdb_date_wins_over_different_tmdb_date(self):
        selected = select_series_topology({
            "tvdb": ({
                "season_number": 1,
                "episode_number": 1,
                "aired": "2005-04-14",
            },),
            "tmdb": ({
                "season_number": 1,
                "episode_number": 1,
                "aired": "2005-04-15",
            },),
        })

        self.assertEqual(selected.items[0]["aired"], "2005-04-14")
        self.assertFalse(selected.items[0].get("air_date_conflict", False))
        self.assertEqual(selected.items[0]["air_date_source"], "tvdb")

    def test_available_higher_priority_order_is_not_filled_with_lower_extra_seasons(self):
        selected = select_series_topology({
            "tvdb": _episodes("tvdb", {1: 2}),
            "tmdb": _episodes("tmdb", {1: 1, 2: 1}),
        }, requested_season_number=2)
        self.assertEqual(selected.provider, "tvdb")
        self.assertEqual([(x["season_number"], x["episode_number"])
                          for x in selected.items], [(1, 1), (1, 2)])
        self.assertTrue(all("tmdb_episode_id" not in x for x in selected.items))

    def test_wikipedia_is_authoritative_regardless_of_input_order(self):
        profiles = {
            "tmdb": _episodes("tmdb", {1: 3}),
            "tvdb": _episodes("tvdb", {1: 2}),
            "wikipedia": _episodes("wikipedia", {1: 1}),
        }
        selected = select_series_topology(profiles, trusted_episode_count=3)
        self.assertEqual(selected.provider, "wikipedia")
        self.assertEqual(len(selected.items), 1)
        self.assertEqual(selected.items[0]["wikipedia_episode_id"], "wikipedia-1-1")

    def test_empty_higher_priority_inventory_falls_back_in_order(self):
        for tvdb, expected in [(_episodes("tvdb", {1: 2}), "tvdb"), ((), "tmdb")]:
            with self.subTest(expected=expected):
                selected = select_series_topology({
                    "wikipedia": (), "tvdb": tvdb,
                    "tmdb": _episodes("tmdb", {1: 3}),
                })
                self.assertEqual(selected.provider, expected)
                self.assertEqual(len(selected.items), 2 if expected == "tvdb" else 3)

    def test_missing_dates_fall_back_without_changing_primary_order(self):
        for wiki_date, tvdb_date, expected, source in [
            ("2026-06-01", "2026-06-02", "2026-06-01", "wikipedia"),
            ("N/A", "2026-06-02", "2026-06-02", "tvdb"),
            (None, " n/a ", "2026-06-03", "tmdb"),
            ("", "", "2026-06-03", "tmdb"),
            ("2027-06-01", "2026-06-02", "2027-06-01", "wikipedia"),
        ]:
            with self.subTest(wiki=wiki_date, tvdb=tvdb_date):
                profiles = {name: list(_episodes(name, {1: 1}))
                            for name in ("wikipedia", "tvdb", "tmdb")}
                for name, aired in [("wikipedia", wiki_date), ("tvdb", tvdb_date),
                                    ("tmdb", "2026-06-03")]:
                    profiles[name][0]["aired"] = aired
                selected = select_series_topology(profiles)
                self.assertEqual(selected.provider, "wikipedia")
                self.assertEqual(selected.items[0]["aired"], expected)
                self.assertEqual(selected.items[0]["air_date_source"], source)
                self.assertEqual(profiles["wikipedia"][0]["aired"], wiki_date)

    def test_unmatched_numbering_cannot_supply_a_missing_date(self):
        primary = [{"season_number": 1, "episode_number": 1, "aired": "N/A"}]
        selected = select_series_topology({
            "wikipedia": primary,
            "tvdb": _episodes("tvdb", {1: 2}),
        })
        self.assertEqual(selected.items[0]["aired"], "")
        self.assertEqual(selected.items[0]["air_date_source"], "")

    def test_no_usable_inventory_fails_explicitly(self):
        with self.assertRaises(ProviderOrderConflict) as raised:
            select_series_topology({"wikipedia": (), "tvdb": (), "tmdb": ()})
        self.assertEqual(raised.exception.reason, "no_provider_profile")

    def test_stable_episode_id_supplies_missing_date_across_different_orders(self):
        selected = select_series_topology({
            "wikipedia": ({
                "season_number": 1, "episode_number": 1, "aired": "N/A",
                "tvdb_episode_id": "episode-1",
            },),
            "tvdb": (
                {"season_number": 2, "episode_number": 3, "aired": "2024-01-01",
                 "tvdb_episode_id": "episode-1", "season_total": 10},
                {"season_number": 2, "episode_number": 4, "aired": "2024-01-02",
                 "tvdb_episode_id": "episode-2", "season_total": 10},
            ),
        })
        self.assertEqual(selected.provider, "wikipedia")
        self.assertEqual(len(selected.items), 1)
        self.assertEqual(selected.items[0]["aired"], "2024-01-01")
        self.assertEqual(selected.items[0]["air_date_source"], "tvdb")
        self.assertEqual(selected.items[0]["season_number"], 1)
        self.assertEqual(selected.items[0]["episode_number"], 1)
        self.assertNotIn("season_total", selected.items[0])

    def test_conflicting_episode_ids_cannot_supply_dates_despite_same_coordinates(self):
        selected = select_series_topology({
            "wikipedia": ({"season_number": 1, "episode_number": 1,
                           "aired": "N/A", "tvdb_episode_id": "episode-1"},),
            "tvdb": ({"season_number": 1, "episode_number": 1,
                      "aired": "2024-01-01", "tvdb_episode_id": "episode-2"},),
        })
        self.assertEqual(selected.items[0]["aired"], "")
        self.assertEqual(selected.items[0]["tvdb_episode_id"], "episode-1")

    def test_primary_dates_do_not_require_unique_cross_site_ids(self):
        selected = select_series_topology({
            "wikipedia": (
                {"season_number": 1, "episode_number": 1, "aired": "2024-01-01",
                 "tvdb_episode_id": "duplicate"},
                {"season_number": 1, "episode_number": 2, "aired": "2024-01-02",
                 "tvdb_episode_id": "duplicate"},
            ),
        })
        self.assertEqual([item["aired"] for item in selected.items],
                         ["2024-01-01", "2024-01-02"])

    def test_duplicate_primary_ids_cannot_bridge_missing_dates_to_another_order(self):
        selected = select_series_topology({
            "wikipedia": (
                {"season_number": 1, "episode_number": 1, "aired": "N/A",
                 "tvdb_episode_id": "duplicate"},
                {"season_number": 1, "episode_number": 2, "aired": "N/A",
                 "tvdb_episode_id": "duplicate"},
            ),
            "tvdb": ({"season_number": 2, "episode_number": 3,
                      "aired": "2024-01-01", "tvdb_episode_id": "duplicate"},),
        })
        self.assertEqual([item["aired"] for item in selected.items], ["", ""])
        self.assertEqual(selected.diagnostics["date_fallback_count"], 0)


if __name__ == "__main__":
    unittest.main()
