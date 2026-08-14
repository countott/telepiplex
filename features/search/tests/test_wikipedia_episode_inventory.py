import unittest
from pathlib import Path

from telepiplex_search.wikipedia_episode_inventory import (
    merge_wikipedia_episode_results,
    parse_wikipedia_episode_html,
)


FIXTURES = Path(__file__).parent / "fixtures" / "wikipedia"


def parsed(name, *, language, revision_id):
    return parse_wikipedia_episode_html(
        (FIXTURES / name).read_text(encoding="utf-8"),
        language=language,
        source_url=f"https://{language}.wikipedia.org/wiki/sample",
        revision_id=revision_id,
    )


class WikipediaEpisodeInventoryTest(unittest.TestCase):
    def test_flat_chinese_episode_table_is_partial_not_complete(self):
        result = parsed(
            "one_hundred_years_zh.html",
            language="zh",
            revision_id=93821395,
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(
            [item["overall_number"] for item in result["items"]],
            [1, 8, 9, 15],
        )
        self.assertTrue(all(
            item["season_number"] is None
            and item["episode_number"] is None
            for item in result["items"]
        ))

    def test_english_overview_and_season_tables_are_complete(self):
        result = parsed(
            "one_hundred_years_en.html",
            language="en",
            revision_id=1367933110,
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["season_totals"], {1: 8, 2: 8})
        items = {
            (item["season_number"], item["episode_number"]): item
            for item in result["items"]
        }
        self.assertEqual(len(items), 16)
        self.assertEqual(items[(2, 8)]["air_date"], "2026-08-26")

    def test_same_qid_english_table_completes_flat_chinese_table(self):
        zh = parsed(
            "one_hundred_years_zh.html",
            language="zh",
            revision_id=93821395,
        )
        en = parsed(
            "one_hundred_years_en.html",
            language="en",
            revision_id=1367933110,
        )
        zh["wikibase_item"] = en["wikibase_item"] = "Q124175370"

        result = merge_wikipedia_episode_results(
            zh,
            en,
            expected_qid="Q124175370",
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["season_totals"], {1: 8, 2: 8})
        self.assertEqual(len(result["items"]), 16)
        self.assertEqual(
            result["source_revisions"],
            {"zh": 93821395, "en": 1367933110},
        )

    def test_recognized_episode_table_without_valid_rows_is_parse_error(self):
        result = parse_wikipedia_episode_html(
            '<table class="wikiepisodetable"><tr><th>Title</th></tr></table>',
            language="en",
            source_url="https://en.wikipedia.org/wiki/broken",
            revision_id=1,
        )

        self.assertEqual(result["status"], "parse_error")
        self.assertEqual(result["error"], "wikipedia_parse_error")

    def test_root_page_exposes_only_explicit_episode_list_links(self):
        result = parse_wikipedia_episode_html(
            """
            <table class="infobox">
              <tr><th>Episode list</th><td>
                <a href="/wiki/List_of_Bleach_episodes"
                   title="List of Bleach episodes">List of episodes</a>
              </td></tr>
            </table>
            <a href="/wiki/Bleach_(manga)" title="Bleach manga">Manga</a>
            """,
            language="en",
            source_url="https://en.wikipedia.org/wiki/Bleach_(TV_series)",
            revision_id=10,
        )

        self.assertEqual(result["status"], "absent")
        self.assertEqual(result["episode_list_links"], [{
            "title": "List of Bleach episodes",
            "href": "/wiki/List_of_Bleach_episodes",
        }])

    def test_qid_mismatch_is_a_fact_conflict(self):
        zh = parsed(
            "one_hundred_years_zh.html",
            language="zh",
            revision_id=1,
        )
        en = parsed(
            "one_hundred_years_en.html",
            language="en",
            revision_id=2,
        )
        zh["wikibase_item"] = "Q124175370"
        en["wikibase_item"] = "Q1"

        result = merge_wikipedia_episode_results(
            zh,
            en,
            expected_qid="Q124175370",
        )

        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["error"], "wikipedia_fact_conflict")

    def test_explicit_continuation_parts_form_release_seasons(self):
        source = {
            "status": "partial",
            "items": [{
                "season_number": season,
                "episode_number": episode,
                "overall_number": overall,
                "source_section": f"Season {season}",
                "air_date": "2004-01-01",
            } for season, episode, overall in (
                (1, 1, 1), (1, 2, 2), (2, 1, 3),
            )] + [{
                "season_number": None,
                "episode_number": None,
                "overall_number": overall,
                "source_section": section,
                "air_date": "2024-01-01",
            } for overall, section in (
                (4, "Part 1: The Beginning"),
                (5, "Part 1: The Beginning"),
                (6, "Part 2: The Return"),
            )],
            "season_totals": {1: 2, 2: 1},
            "source_language": "en",
            "source_url": "https://en.wikipedia.org/wiki/List_of_Example_episodes",
            "revision_id": 1,
            "wikibase_item": "Q1",
            "error": "",
        }

        result = merge_wikipedia_episode_results(
            source,
            expected_qid="Q1",
        )

        self.assertEqual(result["topology_kind"], "continuation_parts")
        self.assertEqual(result["season_totals"], {1: 3, 2: 2, 3: 1})
        self.assertEqual(
            [
                (item["season_number"], item["episode_number"])
                for item in result["items"]
            ],
            [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (3, 1)],
        )


if __name__ == "__main__":
    unittest.main()
