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


if __name__ == "__main__":
    unittest.main()
