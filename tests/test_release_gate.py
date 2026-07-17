import unittest

from telepiplex_media_search.release_gate import gate_releases


def release(title, suffix):
    return {
        "title": title,
        "magnet_url": (
            "magnet:?xt=urn:btih:"
            + (suffix * 40)[:40]
        ),
    }


def series_contract(
    *,
    scope,
    expected_seasons,
    season=None,
    episode=None,
):
    items = []
    for season_number in expected_seasons:
        items.append({
            "item_id": f"{season_number}-1",
            "content_role": "main_episode",
            "season_number": season_number,
            "episode_number": 1,
            "aired": "2020-01-01",
        })
    return {
        "identity": {
            "english_title": "The Office US",
            "official_english_title": "The Office US",
            "year": "2005",
            "aliases": ["The Office US"],
        },
        "retrieval": {
            "media_type": "series",
            "scope": scope,
            "query": "The Office US",
        },
        "placement": {
            "library_type": "series",
            "season_number": season,
            "episode_number": episode,
        },
        "items": items,
        "evidence": {"decision": {
            "scope": scope,
            "season_number": season,
            "episode_number": episode,
        }},
    }


class ReleaseGateTest(unittest.TestCase):
    def test_office_wife_does_not_match_the_office(self):
        contract = series_contract(
            scope="season",
            expected_seasons=(1,),
            season=1,
        )
        contract["identity"]["english_title"] = "The Office"
        contract["identity"]["official_english_title"] = "The Office"
        contract["identity"]["aliases"] = ["The Office"]

        result = gate_releases(
            [release("The.Office.Wife.2025.1080p", "a")],
            contract,
        )

        self.assertEqual(result.eligible, ())
        self.assertEqual(result.rejection_counts["identity_mismatch"], 1)

    def test_single_season_series_s01_is_whole_series(self):
        result = gate_releases(
            [release("The.Office.US.S01.1080p", "a")],
            series_contract(
                scope="whole_series",
                expected_seasons=(1,),
            ),
        )

        self.assertEqual(result.eligible[0]["scope_label"], "全剧（S01）")

    def test_nine_season_range_is_complete_without_complete_keyword(self):
        result = gate_releases(
            [release("The.Office.US.S01-S09.1080p", "a")],
            series_contract(
                scope="whole_series",
                expected_seasons=tuple(range(1, 10)),
            ),
        )

        self.assertEqual(len(result.eligible), 1)
        self.assertEqual(
            result.eligible[0]["release_scope"],
            "multi_season_pack",
        )

    def test_partial_extra_and_special_ranges_are_rejected(self):
        target = series_contract(
            scope="whole_series",
            expected_seasons=tuple(range(1, 10)),
        )
        items = [
            release("The.Office.US.S01-S08", "a"),
            release("The.Office.US.S02-S09", "b"),
            release("The.Office.US.S01-S10", "c"),
            release("The.Office.US.S00-S09", "d"),
            release("The.Office.US.Complete.Series.Extras", "e"),
        ]

        result = gate_releases(items, target)

        self.assertEqual(result.eligible, ())
        self.assertEqual(result.rejection_counts["scope_mismatch"], 3)
        self.assertEqual(
            result.rejection_counts["unsupported_special_content"],
            2,
        )

    def test_season_results_do_not_mix_scopes(self):
        result = gate_releases(
            [
                release("The.Office.US.S01", "a"),
                release("The.Office.US.S01E01", "b"),
                release("The.Office.US.S01-S09", "c"),
            ],
            series_contract(
                scope="season",
                expected_seasons=(1, 2),
                season=1,
            ),
        )

        self.assertEqual(
            [item["title"] for item in result.eligible],
            ["The.Office.US.S01"],
        )

    def test_episode_only_accepts_exact_single_episode(self):
        result = gate_releases(
            [
                release("The.Office.US.S01E01", "a"),
                release("The.Office.US.1x01", "b"),
                release("The.Office.US.S01E01-E02", "c"),
                release("The.Office.US.S01", "d"),
            ],
            series_contract(
                scope="episode",
                expected_seasons=(1,),
                season=1,
                episode=1,
            ),
        )

        self.assertEqual(len(result.eligible), 2)
        self.assertEqual(result.rejection_counts["scope_mismatch"], 2)

    def test_movie_title_may_contain_special_but_extras_are_rejected(self):
        contract = {
            "identity": {
                "english_title": "Midnight Special",
                "official_english_title": "Midnight Special",
                "year": "2016",
            },
            "retrieval": {"media_type": "movie", "scope": "movie"},
            "placement": {"library_type": "movie"},
            "items": [],
            "evidence": {"decision": {"scope": "movie"}},
        }

        result = gate_releases(
            [
                release("Midnight.Special.2016.1080p", "a"),
                release("Midnight.Special.2016.Extras", "b"),
            ],
            contract,
        )

        self.assertEqual(len(result.eligible), 1)
        self.assertEqual(
            result.rejection_counts["unsupported_special_content"],
            1,
        )

    def test_year_mismatch_missing_link_and_duplicate_are_reported(self):
        first = release("Midnight.Special.2016.1080p", "a")
        result = gate_releases(
            [
                first,
                dict(first),
                {"title": "Midnight.Special.2016.720p"},
                release("Midnight.Special.2015.1080p", "b"),
            ],
            {
                "identity": {
                    "english_title": "Midnight Special",
                    "year": "2016",
                },
                "retrieval": {"media_type": "movie", "scope": "movie"},
                "placement": {"library_type": "movie"},
                "items": [],
                "evidence": {"decision": {"scope": "movie"}},
            },
        )

        self.assertEqual(len(result.eligible), 1)
        self.assertEqual(result.rejection_counts["duplicate"], 1)
        self.assertEqual(result.rejection_counts["missing_download_url"], 1)
        self.assertEqual(result.rejection_counts["year_mismatch"], 1)


if __name__ == "__main__":
    unittest.main()
