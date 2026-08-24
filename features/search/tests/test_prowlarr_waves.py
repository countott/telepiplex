import unittest


from telepiplex_search.prowlarr_waves import plan_prowlarr_waves


def indexer(indexer_id, name):
    return {"id": indexer_id, "name": name}


class ProwlarrWavePlannerTest(unittest.TestCase):
    def test_explicit_ids_win_over_positive_scores(self):
        indexers = [
            indexer(1, "M-Team"),
            indexer(2, "TorrentLeech"),
            indexer(3, "Other"),
        ]

        first, remaining = plan_prowlarr_waves(
            indexers,
            explicit_ids=[3],
            indexer_scores={"M-Team": 30, "TorrentLeech": 10},
        )

        self.assertEqual([item["id"] for item in first], [3])
        self.assertEqual([item["id"] for item in remaining], [1, 2])

    def test_stale_explicit_ids_fall_back_to_positive_names(self):
        indexers = [
            indexer(1, " M-Team "),
            indexer(2, "Other"),
        ]

        first, remaining = plan_prowlarr_waves(
            indexers,
            explicit_ids=[999, True, -2],
            indexer_scores={"m-team": "30", "Other": 0},
        )

        self.assertEqual([item["id"] for item in first], [1])
        self.assertEqual([item["id"] for item in remaining], [2])

    def test_invalid_or_nonpositive_scores_use_compatibility_wave(self):
        indexers = [indexer(1, "A"), indexer(2, "B")]

        first, remaining = plan_prowlarr_waves(
            indexers,
            explicit_ids=[],
            indexer_scores={
                "A": True,
                "B": "not-a-number",
                "C": -1,
            },
        )

        self.assertEqual([item["id"] for item in first], [1, 2])
        self.assertEqual(tuple(remaining), ())

    def test_deduplicates_by_id_and_preserves_stable_partition_order(self):
        indexers = [
            indexer(3, "fast"),
            indexer(1, "slow"),
            indexer(3, "duplicate"),
            indexer(2, "FAST"),
            {"id": "invalid", "name": "ignored"},
        ]

        first, remaining = plan_prowlarr_waves(
            indexers,
            explicit_ids=[],
            indexer_scores={" fast ": 1},
        )

        self.assertEqual([item["id"] for item in first], [3, 2])
        self.assertEqual([item["id"] for item in remaining], [1])
        self.assertEqual(
            [item["id"] for item in (*first, *remaining)],
            [3, 2, 1],
        )


if __name__ == "__main__":
    unittest.main()
