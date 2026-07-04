import unittest
import sys
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init

from app.adapters.prowlarr import ProwlarrConfigError, ProwlarrRequestError, search_prowlarr


class ProwlarrAdapterTest(unittest.TestCase):
    def setUp(self):
        init.bot_config = {
            "search": {
                "enable": True,
                "prowlarr": {
                    "base_url": "http://192.168.7.7:9696/",
                    "api_key": "secret",
                    "timeout": 20,
                    "indexer_ids": "-2",
                    "categories": {"movie": 2000, "tv": 5000},
                    "result_limit": 8,
                },
            }
        }

    @patch("app.adapters.prowlarr.requests.get")
    def test_search_prowlarr_calls_api_and_normalizes_results(self, get_mock):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {
                "title": "The Grand Budapest Hotel 2014 1080p WEB-DL",
                "magnetUrl": "magnet:?xt=urn:btih:ABC",
                "downloadUrl": "https://example/download",
                "size": 8589934592,
                "seeders": 32,
                "indexer": "Indexer A",
                "publishDate": "2026-01-02T03:04:05Z",
                "protocol": "torrent",
                "guidUrl": "https://example/info",
            }
        ]
        get_mock.return_value = response

        results = search_prowlarr("The Grand Budapest Hotel 2014")

        get_mock.assert_called_once_with(
            "http://192.168.7.7:9696/api/v1/search",
            headers={"X-Api-Key": "secret"},
            params={
                "query": "The Grand Budapest Hotel 2014",
                "indexerIds": "-2",
                "categories": 2000,
                "type": "search",
            },
            timeout=20,
        )
        self.assertEqual(
            results,
            [
                {
                    "title": "The Grand Budapest Hotel 2014 1080p WEB-DL",
                    "download_url": "magnet:?xt=urn:btih:ABC",
                    "magnet_url": "magnet:?xt=urn:btih:ABC",
                    "size": 8589934592,
                    "seeders": 32,
                    "indexer": "Indexer A",
                    "publish_date": "2026-01-02T03:04:05Z",
                    "protocol": "torrent",
                    "info_url": "https://example/info",
                }
            ],
        )

    def test_search_prowlarr_raises_readable_error_when_disabled(self):
        init.bot_config["search"]["enable"] = False

        with self.assertRaisesRegex(ProwlarrConfigError, "搜索功能未开启"):
            search_prowlarr("movie")

    def test_search_prowlarr_raises_readable_error_when_missing_config(self):
        init.bot_config["search"]["prowlarr"]["api_key"] = ""

        with self.assertRaisesRegex(ProwlarrConfigError, "search.prowlarr.base_url 或 api_key 未配置"):
            search_prowlarr("movie")

    @patch("app.adapters.prowlarr.requests.get")
    def test_search_prowlarr_wraps_request_failures(self, get_mock):
        get_mock.side_effect = Exception("connection refused")

        with self.assertRaisesRegex(ProwlarrRequestError, "Prowlarr 请求失败"):
            search_prowlarr("movie")


if __name__ == "__main__":
    unittest.main()
