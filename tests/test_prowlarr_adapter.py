import hashlib
import unittest
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init

from app.adapters.prowlarr import (
    ProwlarrConfigError,
    ProwlarrRequestError,
    get_prowlarr_indexer_summary,
    resolve_prowlarr_download_url,
    search_prowlarr,
)


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
        init.bot_config["search"]["prowlarr"]["timeout"] = 240
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {
                "title": "The Grand Budapest Hotel 2014 1080p WEB-DL",
                "magnetUrl": "magnet:?xt=urn:btih:8DF2ECE4F1739AB307C52E3FC9971E87E24B0A41",
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
            timeout=240,
        )
        self.assertEqual(
            results,
            [
                {
                    "title": "The Grand Budapest Hotel 2014 1080p WEB-DL",
                    "download_url": "magnet:?xt=urn:btih:8DF2ECE4F1739AB307C52E3FC9971E87E24B0A41",
                    "magnet_url": "magnet:?xt=urn:btih:8DF2ECE4F1739AB307C52E3FC9971E87E24B0A41",
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
    def test_search_prowlarr_uses_configured_timeout(self, get_mock):
        init.bot_config["search"]["prowlarr"]["timeout"] = 80
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = []
        get_mock.return_value = response

        search_prowlarr("Transformers: Dark of the Moon 2011")

        self.assertEqual(get_mock.call_args.kwargs["timeout"], 80)

    @patch("app.adapters.prowlarr.requests.get")
    def test_search_prowlarr_defaults_timeout_to_150_when_not_configured(self, get_mock):
        init.bot_config["search"]["prowlarr"].pop("timeout")
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = []
        get_mock.return_value = response

        search_prowlarr("Transformers: Dark of the Moon 2011")

        self.assertEqual(get_mock.call_args.kwargs["timeout"], 150)

    @patch("app.adapters.prowlarr.requests.get")
    def test_search_prowlarr_reports_timeout_with_wait_budget(self, get_mock):
        init.bot_config["search"]["prowlarr"]["timeout"] = 80
        get_mock.side_effect = requests.exceptions.Timeout("read timed out")

        with self.assertRaisesRegex(ProwlarrRequestError, "Prowlarr 查询超时.*80"):
            search_prowlarr("movie")

    @patch("app.adapters.prowlarr.requests.get")
    def test_search_prowlarr_wraps_request_failures(self, get_mock):
        get_mock.side_effect = Exception("connection refused")

        with self.assertRaisesRegex(ProwlarrRequestError, "Prowlarr 请求失败"):
            search_prowlarr("movie")

    @patch("app.adapters.prowlarr.requests.get")
    def test_get_prowlarr_indexer_summary_reports_sources_and_down_health(self, get_mock):
        indexers_response = Mock()
        indexers_response.raise_for_status.return_value = None
        indexers_response.json.return_value = [
            {"id": 1, "name": "UIndex", "enable": True},
            {"id": 2, "name": "TorrentLeech", "enable": True},
            {"id": 3, "name": "DisabledIndexer", "enable": False},
        ]
        health_response = Mock()
        health_response.raise_for_status.return_value = None
        health_response.json.return_value = [
            {"source": "TorrentLeech", "type": "error", "message": "Indexer unavailable"}
        ]
        get_mock.side_effect = [indexers_response, health_response]

        summary = get_prowlarr_indexer_summary(
            [
                {"indexer": "UIndex"},
                {"indexer": "UIndex"},
                {"indexer": "OtherIndexer"},
            ]
        )

        self.assertEqual(
            summary,
            {
                "enabled_indexers": ["UIndex", "TorrentLeech"],
                "result_sources": {"UIndex": 2, "OtherIndexer": 1},
                "down_indexers": [{"source": "TorrentLeech", "message": "Indexer unavailable"}],
                "error": "",
            },
        )
        self.assertEqual(get_mock.call_args_list[0].args[0], "http://192.168.7.7:9696/api/v1/indexer")
        self.assertEqual(get_mock.call_args_list[1].args[0], "http://192.168.7.7:9696/api/v1/health")

    @patch("app.adapters.prowlarr.requests.get")
    def test_resolve_prowlarr_download_url_converts_torrent_file_to_magnet(self, get_mock):
        info = b"d6:lengthi123e4:name9:movie.mkve"
        torrent = b"d8:announce14:http://tracker4:info" + info + b"e"
        expected_hash = hashlib.sha1(info).hexdigest().upper()
        response = Mock()
        response.raise_for_status.return_value = None
        response.content = torrent
        get_mock.return_value = response

        link = resolve_prowlarr_download_url(
            {
                "title": "Fallback Title",
                "download_url": "https://prowlarr.example/download?id=1",
                "protocol": "torrent",
            }
        )

        self.assertEqual(link, f"magnet:?xt=urn:btih:{expected_hash}&dn=movie.mkv")
        get_mock.assert_called_once_with("https://prowlarr.example/download?id=1", timeout=20, allow_redirects=False)

    @patch("app.adapters.prowlarr.requests.get")
    def test_resolve_prowlarr_download_url_defaults_timeout_to_search_default(self, get_mock):
        init.bot_config["search"]["prowlarr"].pop("timeout")
        info = b"d6:lengthi123e4:name9:movie.mkve"
        torrent = b"d8:announce14:http://tracker4:info" + info + b"e"
        response = Mock()
        response.raise_for_status.return_value = None
        response.content = torrent
        get_mock.return_value = response

        resolve_prowlarr_download_url(
            {
                "title": "Fallback Title",
                "download_url": "https://prowlarr.example/download?id=1",
                "protocol": "torrent",
            }
        )

        get_mock.assert_called_once_with("https://prowlarr.example/download?id=1", timeout=150, allow_redirects=False)

    @patch("app.adapters.prowlarr.requests.get")
    def test_resolve_prowlarr_download_url_returns_magnet_redirect_from_prowlarr_download(self, get_mock):
        magnet = "magnet:?xt=urn:btih:8DF2ECE4F1739AB307C52E3FC9971E87E24B0A41&dn=movie.mkv"
        response = Mock()
        response.status_code = 302
        response.headers = {"Location": magnet}
        get_mock.return_value = response

        link = resolve_prowlarr_download_url(
            {
                "title": "Redirect Title",
                "download_url": "https://prowlarr.example/download?id=1",
                "protocol": "torrent",
            }
        )

        self.assertEqual(link, magnet)
        get_mock.assert_called_once_with("https://prowlarr.example/download?id=1", timeout=20, allow_redirects=False)

    @patch("app.adapters.prowlarr.requests.get")
    def test_resolve_prowlarr_download_url_prefers_prowlarr_download_before_indexer_info_page(self, get_mock):
        info = b"d6:lengthi123e4:name9:movie.mkve"
        torrent = b"d8:announce14:http://tracker4:info" + info + b"e"
        expected_hash = hashlib.sha1(info).hexdigest().upper()
        response = Mock()
        response.raise_for_status.return_value = None
        response.content = torrent
        get_mock.return_value = response

        link = resolve_prowlarr_download_url(
            {
                "title": "Fallback Title",
                "download_url": "https://prowlarr.example/download?id=1",
                "info_url": "https://indexer.example/details/1",
                "protocol": "torrent",
            }
        )

        self.assertEqual(link, f"magnet:?xt=urn:btih:{expected_hash}&dn=movie.mkv")
        get_mock.assert_called_once_with("https://prowlarr.example/download?id=1", timeout=20, allow_redirects=False)

    @patch("app.adapters.prowlarr.requests.get")
    def test_resolve_prowlarr_download_url_falls_back_to_info_page_magnet_when_torrent_download_fails(self, get_mock):
        torrent_response = Mock()
        torrent_response.raise_for_status.side_effect = Exception("prowlarr download failed")
        info_page_response = Mock()
        info_page_response.raise_for_status.return_value = None
        info_page_response.text = (
            '<a href="magnet:?xt=urn:btih:8DF2ECE4F1739AB307C52E3FC9971E87E24B0A41'
            '&amp;dn=Vivre+sa+Vie">magnet</a>'
        )
        get_mock.side_effect = [torrent_response, info_page_response]

        link = resolve_prowlarr_download_url(
            {
                "title": "Vivre sa Vie",
                "download_url": "https://prowlarr.example/download?id=1",
                "info_url": "https://indexer.example/details/1",
                "protocol": "torrent",
            }
        )

        self.assertEqual(
            link,
            "magnet:?xt=urn:btih:8DF2ECE4F1739AB307C52E3FC9971E87E24B0A41&dn=Vivre+sa+Vie",
        )
        self.assertEqual(get_mock.call_count, 2)
        self.assertEqual(get_mock.call_args_list[0].args[0], "https://prowlarr.example/download?id=1")
        self.assertEqual(get_mock.call_args_list[1].args[0], "https://indexer.example/details/1")


if __name__ == "__main__":
    unittest.main()
