import unittest

from telepiplex_search.scope_catalog import build_scope_catalog


class ScopeCatalogTest(unittest.TestCase):
    def test_tvdb_inventory_has_priority_and_specials_are_removed(self):
        catalog = build_scope_catalog(
            tvdb_items=[
                {"season_number": 0, "episode_number": 1},
                {"season_number": 1, "episode_number": 1},
                {"season_number": 1, "episode_number": 2},
            ],
            tmdb_items=[
                {"season_number": 1, "episode_number": 1},
                {"season_number": 2, "episode_number": 1},
            ],
            wikipedia_season_count=7,
        )

        self.assertEqual(catalog["source"], "tvdb")
        self.assertEqual(catalog["seasons"], [1])
        self.assertEqual(catalog["episodes_by_season"], {1: [1, 2]})
        self.assertTrue(catalog["episode_complete"])

    def test_tmdb_then_wikipedia_count_fallback(self):
        tmdb = build_scope_catalog(
            tvdb_items=[],
            tmdb_items=[
                {"season_number": 0, "episode_number": 1},
                {"season_number": 1, "episode_number": 1},
                {"season_number": 2, "episode_number": 1},
            ],
            wikipedia_season_count=7,
        )
        wiki = build_scope_catalog(
            tvdb_items=[],
            tmdb_items=[],
            wikipedia_season_count=3,
        )

        self.assertEqual(tmdb["source"], "tmdb")
        self.assertEqual(tmdb["seasons"], [1, 2])
        self.assertEqual(wiki["source"], "wikipedia")
        self.assertEqual(wiki["seasons"], [1, 2, 3])
        self.assertFalse(wiki["episode_complete"])

    def test_no_structure_never_invents_seasons(self):
        catalog = build_scope_catalog(
            tvdb_items=[],
            tmdb_items=[],
            wikipedia_season_count=None,
        )

        self.assertEqual(catalog["source"], "")
        self.assertEqual(catalog["seasons"], [])
        self.assertEqual(catalog["episodes_by_season"], {})


if __name__ == "__main__":
    unittest.main()
