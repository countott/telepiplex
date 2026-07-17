import unittest
from copy import deepcopy
from datetime import date

from telepiplex_media_search.series_scope import (
    SeriesScopeError,
    apply_series_scope,
    series_scope_options,
)


def contract(*, seasons=(1,), scope="movie_or_series", season_number=None, episode_number=None):
    items = []
    for season in seasons:
        for episode in range(1, 4):
            items.append({
                "item_id": f"{season}-{episode}",
                "content_role": "main_episode",
                "season_number": season,
                "episode_number": episode,
                "aired": "2026-01-01" if episode < 3 else "2027-01-01",
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
        "evidence": {"decision": {
            "scope": scope,
            "season_number": season_number,
            "episode_number": episode_number,
        }},
    }


class SeriesScopeTest(unittest.TestCase):
    def test_one_and_multiple_season_options(self):
        self.assertEqual(
            series_scope_options(contract(seasons=(1,))),
            ("whole_series", "episode"),
        )
        self.assertEqual(
            series_scope_options(contract(seasons=(1, 2))),
            ("whole_series", "season", "episode"),
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
                contract(seasons=(1,)),
                "episode",
                season_number=1,
                episode_number=3,
                today=date(2026, 7, 16),
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
