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


if __name__ == "__main__":
    unittest.main()
