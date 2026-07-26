import unittest
from unittest.mock import patch

import requests

from telepiplex_search.adapters import prowlarr
from telepiplex_search.context import runtime_context


class ProwlarrAdapterTest(unittest.TestCase):
    def setUp(self):
        runtime_context.configure({
            "search": {
                "prowlarr": {
                    "base_url": "http://prowlarr:9696",
                    "api_key": "configured",
                    "timeout": 200,
                    "indexer_timeout": 75,
                },
            },
        })

    @patch.object(
        prowlarr.requests,
        "get",
        side_effect=requests.Timeout("slow indexer"),
    )
    def test_timeout_preserves_structured_reason(self, _get):
        with self.assertRaises(prowlarr.ProwlarrRequestError) as raised:
            prowlarr.search_prowlarr("Constantine", "movie")

        self.assertEqual(raised.exception.kind, "timeout")
        self.assertEqual(raised.exception.http_status, 0)
        self.assertTrue(raised.exception.retryable)
        self.assertIn("已等待 200 秒", str(raised.exception))
        self.assertEqual(
            raised.exception.as_dict()["message"],
            str(raised.exception),
        )

    @patch.object(prowlarr.requests, "get")
    def test_http_error_preserves_status_and_provider_message(self, get):
        response = get.return_value
        response.status_code = 503
        response.raise_for_status.side_effect = requests.HTTPError(
            "upstream unavailable",
            response=response,
        )

        with self.assertRaises(prowlarr.ProwlarrRequestError) as raised:
            prowlarr.search_prowlarr("Constantine", "movie")

        self.assertEqual(raised.exception.kind, "server_error")
        self.assertEqual(raised.exception.http_status, 503)
        self.assertTrue(raised.exception.retryable)
        self.assertIn("upstream unavailable", str(raised.exception))

    @patch.object(prowlarr.requests, "get")
    def test_enabled_indexers_honor_configured_ids(self, get):
        runtime_context.config["search"]["prowlarr"]["indexer_ids"] = "1,3"
        response = get.return_value
        response.json.return_value = [
            {"id": 1, "name": "Fast", "enable": True},
            {"id": 2, "name": "Excluded", "enable": True},
            {"id": 3, "name": "Disabled", "enable": False},
        ]

        indexers = prowlarr.list_prowlarr_indexers()

        self.assertEqual(indexers, [{"id": 1, "name": "Fast"}])

    @patch.object(prowlarr.requests, "get")
    def test_single_indexer_search_uses_its_id_and_short_timeout(self, get):
        response = get.return_value
        response.json.return_value = [{
            "title": "Constantine.2005.1080p",
            "indexer": "Fast",
            "infoHash": "a" * 40,
        }]

        results = prowlarr.search_prowlarr_indexer(
            "Constantine 2005",
            "movie",
            17,
        )

        self.assertEqual(len(results), 1)
        _url, kwargs = get.call_args
        self.assertEqual(kwargs["params"]["indexerIds"], "17")
        self.assertEqual(kwargs["timeout"], 75)

    @patch.object(prowlarr.requests, "get")
    def test_series_media_type_uses_configured_tv_category(self, get):
        runtime_context.config["search"]["prowlarr"]["categories"] = {
            "movie": 2001,
            "tv": 5001,
            "series": 5999,
        }
        get.return_value.json.return_value = []

        prowlarr.search_prowlarr("Someday or One Day S01", "series")

        _url, kwargs = get.call_args
        self.assertEqual(kwargs["params"]["categories"], 5001)


if __name__ == "__main__":
    unittest.main()
