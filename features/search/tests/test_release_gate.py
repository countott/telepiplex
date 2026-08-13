import unittest

from telepiplex_search.release_gate import gate_releases


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
    def test_veep_s01_passes_root_identity_and_season_scope(self):
        contract = series_contract(
            scope="season",
            expected_seasons=(1,),
            season=1,
        )
        contract["identity"].update({
            "english_title": "Veep",
            "official_english_title": "Veep",
            "aliases": ["Veep", "Veep Season 1"],
            "query_titles": ["Veep"],
        })
        contract["retrieval"].update({
            "query": "Veep S01",
            "queries": ["Veep S01", "Veep Season 01"],
        })

        result = gate_releases(
            [release("Veep.S01.1080p.WEB-DL", "a")],
            contract,
        )

        self.assertEqual(len(result.eligible), 1)
        self.assertEqual(result.rejection_counts, {})

    def test_alternative_verified_query_title_is_an_identity_alias(self):
        contract = series_contract(
            scope="season",
            expected_seasons=(1,),
            season=1,
        )
        contract["identity"]["query_titles"] = [
            "The Office US",
            "Das Buero",
        ]
        contract["retrieval"]["queries"] = [
            "The Office US S01",
            "Das Buero S01",
        ]

        result = gate_releases(
            [release("Das.Buero.S01.1080p", "a")],
            contract,
        )

        self.assertEqual(len(result.eligible), 1)

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

    def test_textual_season_markers_only_match_the_requested_season(self):
        result = gate_releases(
            [
                release("The.Office.US.Season.02.1080p", "a"),
                release("The.Office.US.Complete.Season.02.BDRip", "b"),
                release("The.Office.US.Season.01.1080p", "c"),
            ],
            series_contract(
                scope="season",
                expected_seasons=(1, 2),
                season=2,
            ),
        )

        self.assertEqual(
            [item["title"] for item in result.eligible],
            [
                "The.Office.US.Season.02.1080p",
                "The.Office.US.Complete.Season.02.BDRip",
            ],
        )
        self.assertEqual(result.rejection_counts["scope_mismatch"], 1)
        self.assertTrue(all(
            item["release_scope"] == "single_season_pack"
            for item in result.eligible
        ))

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

    def test_movie_release_without_year_is_rejected(self):
        contract = {
            "identity": {
                "english_title": "Backrooms",
                "official_english_title": "Backrooms",
                "year": "2022",
            },
            "retrieval": {"media_type": "movie", "scope": "movie"},
            "placement": {"library_type": "movie"},
            "items": [],
            "evidence": {"decision": {"scope": "movie"}},
        }

        result = gate_releases(
            [release("Backrooms.1080p.WEB-DL", "a")],
            contract,
        )

        self.assertEqual(result.eligible, ())
        self.assertEqual(result.rejection_counts["missing_year"], 1)

    def test_season_year_is_optional_and_accepts_series_or_season_year(self):
        contract = series_contract(
            scope="season",
            expected_seasons=(1, 2),
            season=2,
        )
        for item in contract["items"]:
            if item["season_number"] == 2:
                item["aired"] = "2006-09-21"

        result = gate_releases(
            [
                release("The.Office.US.S02.1080p", "a"),
                release("The.Office.US.2005.S02.1080p", "b"),
                release("The.Office.US.2006.S02.1080p", "c"),
                release("The.Office.US.2019.S02.1080p", "d"),
            ],
            contract,
        )

        self.assertEqual(
            [item["title"] for item in result.eligible],
            [
                "The.Office.US.S02.1080p",
                "The.Office.US.2005.S02.1080p",
                "The.Office.US.2006.S02.1080p",
            ],
        )
        self.assertEqual(result.rejection_counts["year_mismatch"], 1)

    def test_whole_series_year_is_optional_but_present_year_uses_verified_run(self):
        contract = series_contract(
            scope="whole_series",
            expected_seasons=(1, 2),
        )
        contract["items"][0]["aired"] = "2005-03-24"
        contract["items"][1]["aired"] = "2006-09-21"

        result = gate_releases(
            [
                release("The.Office.US.S01-S02.1080p", "a"),
                release("The.Office.US.2005-2006.S01-S02.1080p", "b"),
                release("The.Office.US.2006.S01-S02.1080p", "c"),
                release("The.Office.US.2019.S01-S02.1080p", "d"),
            ],
            contract,
        )

        self.assertEqual(len(result.eligible), 3)
        self.assertEqual(result.rejection_counts["year_mismatch"], 1)

    def test_whole_series_does_not_compare_to_premiere_year_without_run_inventory(self):
        contract = series_contract(
            scope="whole_series",
            expected_seasons=(),
        )

        result = gate_releases(
            [
                release(
                    "The.Office.US.Complete.Series.2013.1080p",
                    "a",
                ),
            ],
            contract,
        )

        self.assertEqual(len(result.eligible), 1)
        self.assertNotIn("year_mismatch", result.rejection_counts)

    def test_episode_year_is_optional_soft_evidence(self):
        contract = series_contract(
            scope="episode",
            expected_seasons=(1,),
            season=1,
            episode=1,
        )
        contract["items"][0]["aired"] = "2006-03-24"

        result = gate_releases(
            [
                release("The.Office.US.S01E01.1080p", "a"),
                release("The.Office.US.2006.S01E01.1080p", "b"),
            ],
            contract,
        )

        self.assertEqual(len(result.eligible), 2)

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
