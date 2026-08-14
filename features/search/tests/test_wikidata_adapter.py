import unittest
from unittest.mock import Mock, patch

from telepiplex_search.adapters.wikidata import (
    enrich_wikidata_entities,
    is_media_work,
)


class WikidataAdapterTest(unittest.TestCase):
    def test_classifies_structural_media_types_without_description_guessing(self):
        self.assertEqual(
            is_media_work({"instance_of": ["Q5398426"]}),
            "series",
        )
        self.assertEqual(
            is_media_work({"instance_of": ["Q11424"]}),
            "movie",
        )
        self.assertEqual(
            is_media_work({"instance_of": ["Q20650540"]}),
            "movie",
        )
        self.assertEqual(
            is_media_work({"instance_of": ["Q21198342"]}),
            "",
        )
        self.assertEqual(
            is_media_work({"instance_of": ["Q5"]}),
            "",
        )

    @patch("telepiplex_search.adapters.wikidata.requests.get")
    def test_normalizes_labels_aliases_year_country_and_structure(self, get_mock):
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "entities": {
                "Q74801": {
                    "id": "Q74801",
                    "labels": {
                        "zh-hans": {"value": "副总统"},
                        "en": {"value": "Veep"},
                    },
                    "aliases": {
                        "zh": [{"value": "副人之仁"}],
                    },
                    "claims": {
                        "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q63952888"}}}}],
                        "P577": [{"mainsnak": {"datavalue": {"value": {"time": "+2012-04-22T00:00:00Z"}}}}],
                        "P495": [{"mainsnak": {"datavalue": {"value": {"id": "Q30"}}}}],
                        "P364": [{"mainsnak": {"datavalue": {"value": {"id": "Q5287"}}}}],
                        "P4969": [{"mainsnak": {"datavalue": {"value": {"id": "Q100944081"}}}}],
                        "P527": [{"mainsnak": {"datavalue": {"value": {"id": "Q1339165"}}}}],
                        "P2437": [{"mainsnak": {"datavalue": {"value": {"amount": "+7"}}}}],
                        "P1113": [{"mainsnak": {"datavalue": {"value": {"amount": "+65"}}}}],
                        "P345": [{"mainsnak": {"datavalue": {"value": "tt1759761"}}}],
                    },
                }
            }
        }
        get_mock.return_value = response

        entities = enrich_wikidata_entities(["Q74801"])

        self.assertEqual(entities["Q74801"]["chinese_title"], "副总统")
        self.assertEqual(entities["Q74801"]["english_title"], "Veep")
        self.assertIn("副人之仁", entities["Q74801"]["aliases"])
        self.assertEqual(entities["Q74801"]["media_type"], "series")
        self.assertEqual(entities["Q74801"]["year"], "2012")
        self.assertEqual(entities["Q74801"]["countries"], ["Q30"])
        self.assertEqual(entities["Q74801"]["season_count"], 7)
        self.assertEqual(entities["Q74801"]["episode_count"], 65)
        self.assertEqual(
            entities["Q74801"]["external_ids"]["imdb"],
            "tt1759761",
        )
        self.assertEqual(entities["Q74801"]["original_language"], "ja")
        self.assertIn("anime", entities["Q74801"]["genres"])
        self.assertEqual(
            entities["Q74801"]["adaptation_ids"],
            ["Q100944081"],
        )
        self.assertEqual(
            entities["Q74801"]["part_ids"],
            ["Q1339165"],
        )


if __name__ == "__main__":
    unittest.main()
