import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init

from app.adapters.tvdb import (
    TvdbConfigError,
    _token_cache,
    get_tvdb_series_episodes,
    search_tvdb_series,
)


class TvdbAdapterTest(unittest.TestCase):
    def setUp(self):
        init.bot_config = {
            "metadata": {
                "tvdb": {
                    "enable": True,
                    "base_url": "https://api4.thetvdb.com/v4",
                    "api_key": "tvdb-key",
                    "subscriber_pin": "pin-123",
                    "timeout": 12,
                }
            }
        }
        _token_cache.update({"token": "", "created_at": 0.0, "api_key": "", "subscriber_pin": ""})

    @patch("app.adapters.tvdb.requests.get")
    @patch("app.adapters.tvdb.requests.post")
    def test_search_tvdb_series_logs_in_and_normalizes_results(self, post_mock, get_mock):
        login_response = Mock()
        login_response.raise_for_status.return_value = None
        login_response.json.return_value = {"data": {"token": "token-123"}}
        post_mock.return_value = login_response

        search_response = Mock()
        search_response.raise_for_status.return_value = None
        search_response.json.return_value = {
            "data": [
                {
                    "tvdb_id": "79349",
                    "name": "Dexter",
                    "year": "2006",
                    "type": "series",
                    "overview": "A crime drama.",
                    "aliases": ["嗜血法医"],
                }
            ]
        }
        get_mock.return_value = search_response

        results = search_tvdb_series("Dexter", year="2006")

        post_mock.assert_called_once_with(
            "https://api4.thetvdb.com/v4/login",
            json={"apikey": "tvdb-key", "pin": "pin-123"},
            timeout=12,
        )
        get_mock.assert_called_once_with(
            "https://api4.thetvdb.com/v4/search",
            headers={"Authorization": "Bearer token-123"},
            params={"query": "Dexter", "type": "series", "year": "2006"},
            timeout=12,
        )
        self.assertEqual(
            results,
            [
                {
                    "tvdb_series_id": "79349",
                    "name": "Dexter",
                    "year": "2006",
                    "type": "series",
                    "overview": "A crime drama.",
                    "aliases": ["嗜血法医"],
                }
            ],
        )

    @patch("app.adapters.tvdb.requests.get")
    @patch("app.adapters.tvdb.requests.post")
    def test_get_tvdb_series_episodes_uses_default_season_type(self, post_mock, get_mock):
        login_response = Mock()
        login_response.raise_for_status.return_value = None
        login_response.json.return_value = {"data": {"token": "token-123"}}
        post_mock.return_value = login_response

        episode_response = Mock()
        episode_response.raise_for_status.return_value = None
        episode_response.json.return_value = {
            "data": {
                "episodes": [
                    {
                        "id": 349232,
                        "name": "Dexter",
                        "seasonNumber": 1,
                        "number": 1,
                        "aired": "2006-10-01",
                    }
                ]
            }
        }
        get_mock.return_value = episode_response

        episodes = get_tvdb_series_episodes("79349")

        get_mock.assert_called_once_with(
            "https://api4.thetvdb.com/v4/series/79349/episodes/default",
            headers={"Authorization": "Bearer token-123"},
            params={"page": 0},
            timeout=12,
        )
        self.assertEqual(
            episodes,
            [
                {
                    "tvdb_episode_id": 349232,
                    "name": "Dexter",
                    "season_number": 1,
                    "episode_number": 1,
                    "aired": "2006-10-01",
                }
            ],
        )

    def test_search_tvdb_series_requires_config(self):
        init.bot_config = {"metadata": {"tvdb": {"enable": True, "api_key": ""}}}

        with self.assertRaisesRegex(TvdbConfigError, "metadata.tvdb.api_key 未配置"):
            search_tvdb_series("Dexter")


if __name__ == "__main__":
    unittest.main()
