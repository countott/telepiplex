import unittest

from telepiplex_search.release_identity import (
    deduplicate_releases,
    stable_release_id,
)


class ReleaseIdentityTest(unittest.TestCase):
    def test_same_magnet_keeps_identity_across_rank_updates(self):
        first = {
            "title": "First title",
            "magnet_url": "magnet:?xt=urn:btih:" + "a" * 40,
            "indexer": "A",
        }
        later = {
            "title": "Renamed title",
            "magnet_url": "magnet:?xt=urn:btih:" + "a" * 40,
            "indexer": "B",
        }

        self.assertEqual(stable_release_id(first), stable_release_id(later))
        self.assertEqual(len(stable_release_id(first)), 16)

    def test_distinct_magnets_do_not_collapse(self):
        first = {"magnet_url": "magnet:?xt=urn:btih:" + "a" * 40}
        second = {"magnet_url": "magnet:?xt=urn:btih:" + "b" * 40}

        self.assertNotEqual(stable_release_id(first), stable_release_id(second))
        self.assertEqual(
            deduplicate_releases([first, first, second]),
            [first, second],
        )

    def test_same_infohash_uses_maximum_parsable_seeder_count(self):
        magnet = "magnet:?xt=urn:btih:" + "c" * 40
        merged = deduplicate_releases([
            {"magnet_url": magnet, "title": "Movie", "seeders": 0},
            {"magnet_url": magnet, "title": "Movie", "seeders": "2"},
            {"magnet_url": magnet, "title": "Movie", "seeders": 1},
        ])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["seeders"], 2)
        self.assertEqual(merged[0]["_explicit_seeders"], [0, 2, 1])


if __name__ == "__main__":
    unittest.main()
