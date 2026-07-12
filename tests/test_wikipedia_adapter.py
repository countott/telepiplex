import unittest
from unittest.mock import Mock, patch

from telepiplex_media_search.adapters.wikipedia import lookup_wikipedia_evidence


class WikipediaAdapterTest(unittest.TestCase):
    @patch("telepiplex_media_search.adapters.wikipedia.requests.get")
    def test_returns_extract_and_findable_page_url(self, get_mock):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "query": {
                "pages": {
                    "1": {
                        "title": "想見你 (電影)",
                        "extract": "2022年上映，為電視劇《想見你》的同名續篇電影。",
                        "pageprops": {"wikibase_item": "Q115000000"},
                        "fullurl": "https://zh.wikipedia.org/wiki/想見你_(電影)",
                    }
                }
            }
        }
        get_mock.return_value = response

        result = lookup_wikipedia_evidence(["想见你 电影 2022"], languages=("zh",))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["facts"][0]["wikibase_item"], "Q115000000")
        self.assertIn("續篇電影", result["facts"][0]["extract"])
        self.assertEqual(
            result["source_urls"],
            ["https://zh.wikipedia.org/wiki/想見你_(電影)"],
        )

    @patch("telepiplex_media_search.adapters.wikipedia.requests.get", side_effect=OSError("dns failed"))
    def test_server_failure_is_soft_evidence(self, _get_mock):
        result = lookup_wikipedia_evidence(["想见你"], languages=("zh",))
        self.assertEqual(result["status"], "server_down")
        self.assertEqual(result["facts"], [])
        self.assertIn("dns failed", result["error"])


if __name__ == "__main__":
    unittest.main()
