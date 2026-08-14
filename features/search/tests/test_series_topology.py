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
    def test_divergent_orders_select_one_complete_profile_never_intersection(self):
        tvdb = _episodes("tvdb", {1: 20, 2: 21, 3: 22})
        tmdb = _episodes("tmdb", {1: 366, 2: 40})

        selected = select_series_topology(
            {"tvdb": tvdb, "tmdb": tmdb},
            trusted_episode_count=406,
        )

        self.assertEqual(selected.provider, "tmdb")
        self.assertEqual(len(selected.items), 406)
        self.assertEqual(selected.season_totals, {1: 366, 2: 40})
        self.assertNotEqual(len(selected.items), 41)

    def test_identical_coordinates_merge_downstream_ids(self):
        tvdb = _episodes("tvdb", {1: 2})
        tmdb = _episodes("tmdb", {1: 2})

        selected = select_series_topology(
            {"tvdb": tvdb, "tmdb": tmdb},
        )

        self.assertEqual(selected.provider, "tvdb_tmdb")
        self.assertEqual(len(selected.items), 2)
        self.assertTrue(all(item["tvdb_episode_id"] for item in selected.items))
        self.assertTrue(all(item["tmdb_episode_id"] for item in selected.items))

    def test_unscored_divergent_orders_fail_instead_of_truncating(self):
        with self.assertRaises(ProviderOrderConflict):
            select_series_topology({
                "tvdb": _episodes("tvdb", {1: 2}),
                "tmdb": _episodes("tmdb", {1: 1, 2: 1}),
            })


if __name__ == "__main__":
    unittest.main()
