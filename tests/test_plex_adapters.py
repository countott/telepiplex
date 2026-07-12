import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class PlexAdapterTest(unittest.TestCase):
    @patch("telepiplex_plex.adapters.plex.PlexServer")
    def test_server_status_returns_plain_identity(self, plex_server):
        from telepiplex_plex.adapters.plex import PlexAdapter

        server = plex_server.return_value
        server.friendlyName = "Living Room Plex"
        server.version = "1.41.5"
        server.machineIdentifier = "machine-1"

        self.assertEqual(
            PlexAdapter("http://plex:32400", "token").server_status(),
            {
                "online": True,
                "name": "Living Room Plex",
                "version": "1.41.5",
                "machine_identifier": "machine-1",
            },
        )

    @patch("telepiplex_plex.adapters.plex.PlexServer")
    def test_scan_targets_library_section(self, plex_server):
        from telepiplex_plex.adapters.plex import PlexAdapter

        section = plex_server.return_value.library.sectionByID.return_value

        PlexAdapter("http://plex:32400", "token").scan_library("12")

        plex_server.return_value.library.sectionByID.assert_called_once_with(12)
        section.update.assert_called_once_with()

    @patch("telepiplex_plex.adapters.plex.PlexServer")
    def test_get_item_normalizes_guids_and_media_streams(self, plex_server):
        from telepiplex_plex.adapters.plex import PlexAdapter

        audio = Mock(
            id=21,
            languageCode="jpn",
            codec="truehd",
            displayTitle="Japanese (TRUEHD 7.1)",
            channels=8,
            bitrate=4000,
            selected=True,
        )
        subtitle = Mock(
            id=31,
            languageCode="chi",
            codec="ass",
            displayTitle="Chinese",
            selected=False,
            key="/library/streams/31",
            transient=False,
        )
        part = Mock(id=11, file="/media/Movie/Movie.mkv")
        part.audioStreams.return_value = [audio]
        part.subtitleStreams.return_value = [subtitle]
        item = Mock(
            ratingKey="42",
            title="电影",
            year=2024,
            type="movie",
            summary="中文简介",
            guids=[Mock(id="tmdb://20")],
            media=[Mock(parts=[part])],
        )
        plex_server.return_value.fetchItem.return_value = item

        result = PlexAdapter("http://plex:32400", "token").get_item("42")

        self.assertEqual(result["rating_key"], "42")
        self.assertEqual(result["guids"], ["tmdb://20"])
        self.assertEqual(result["parts"][0]["audio_streams"][0]["language_code"], "jpn")
        self.assertTrue(result["parts"][0]["subtitle_streams"][0]["external"])

    @patch("telepiplex_plex.adapters.plex.PlexServer")
    def test_library_queries_return_plain_data_and_recent_keys(self, plex_server):
        from telepiplex_plex.adapters.plex import PlexAdapter

        section = Mock(key=12, title="电影", type="movie", locations=["/media/Movies"])
        section.recentlyAdded.return_value = [Mock(ratingKey="41"), Mock(ratingKey="42")]
        plex_server.return_value.library.sections.return_value = [section]
        plex_server.return_value.library.sectionByID.return_value = section
        adapter = PlexAdapter("http://plex:32400", "token")

        libraries = adapter.list_libraries()

        self.assertEqual(libraries[0]["id"], "12")
        self.assertEqual(libraries[0]["locations"], ["/media/Movies"])
        self.assertEqual(adapter.snapshot_recent("12"), {"41", "42"})

    @patch("telepiplex_plex.adapters.plex.PlexServer")
    def test_locate_candidates_returns_only_new_recent_items(self, plex_server):
        from telepiplex_plex.adapters.plex import PlexAdapter

        old = Mock(ratingKey="41", title="旧片", year=2020, type="movie", summary="", guids=[], media=[])
        new = Mock(ratingKey="42", title="新片", year=2024, type="movie", summary="", guids=[], media=[])
        section = plex_server.return_value.library.sectionByID.return_value
        section.recentlyAdded.return_value = [new, old]

        candidates = PlexAdapter("http://plex:32400", "token").locate_candidates(
            "12",
            before_rating_keys={"41"},
        )

        self.assertEqual([item["rating_key"] for item in candidates], ["42"])

    @patch("telepiplex_plex.adapters.plex.PlexServer")
    def test_match_refresh_and_artwork_operations(self, plex_server):
        from telepiplex_plex.adapters.plex import PlexAdapter

        candidate = Mock(guid="tmdb://20", name="电影", year="2024", score=98)
        reloaded = Mock(
            ratingKey="42", title="电影", year=2024, type="movie", summary="中文", guids=[], media=[]
        )
        item = plex_server.return_value.fetchItem.return_value
        item.matches.return_value = [candidate]
        item.reload.return_value = reloaded
        item.posters.return_value = [
            Mock(key="/poster/1", provider="com.plexapp.agents.themoviedb", ratingKey="metadata://1", thumb="/thumb/1", selected=True)
        ]
        adapter = PlexAdapter("http://plex:32400", "token")

        matches = adapter.list_match_candidates("42", title="电影", year=2024)
        fixed = adapter.fix_match("42", "tmdb://20")
        localized = adapter.refresh_zh_cn("42")
        posters = adapter.list_posters("42")
        uploaded = adapter.set_poster_url("42", "https://image.example/poster.jpg")

        self.assertEqual(matches[0]["guid"], "tmdb://20")
        item.fixMatch.assert_called_once_with(candidate)
        self.assertEqual(fixed["rating_key"], "42")
        item.editAdvanced.assert_called_once_with(languageOverride="zh-CN")
        item.refresh.assert_called_once_with()
        self.assertEqual(localized["summary"], "中文")
        self.assertTrue(posters[0]["selected"])
        item.uploadPoster.assert_called_once_with(url="https://image.example/poster.jpg")
        self.assertEqual(uploaded["rating_key"], "42")

    @patch("telepiplex_plex.adapters.plex.PlexServer")
    def test_stream_operations_target_part_and_stream_ids(self, plex_server):
        from telepiplex_plex.adapters.plex import PlexAdapter

        audio = Mock(id=21, languageCode="jpn", codec="truehd", channels=8, bitrate=4000, selected=False)
        subtitle = Mock(id=31, languageCode="chi", codec="ass", key="/stream/31", selected=False)
        part = Mock(id=11, file="/media/movie.mkv")
        part.audioStreams.return_value = [audio]
        part.subtitleStreams.return_value = [subtitle]
        item = Mock(ratingKey="42", title="电影", year=2024, type="movie", summary="", guids=[], media=[Mock(parts=[part])])
        plex_server.return_value.fetchItem.return_value = item
        adapter = PlexAdapter("http://plex:32400", "token")

        streams = adapter.list_streams("42")
        adapter.select_audio("42", "11", "21")
        adapter.select_subtitle("42", "11", "31")

        self.assertEqual(streams[0]["id"], 11)
        part.setSelectedAudioStream.assert_called_once_with(audio)
        part.setSelectedSubtitleStream.assert_called_once_with(subtitle)


class TmdbAdapterTest(unittest.TestCase):
    @patch("telepiplex_plex.adapters.tmdb.requests.get")
    def test_details_and_textless_posters_use_bearer_and_filter_null_language(self, get):
        from telepiplex_plex.adapters.tmdb import TmdbAdapter

        details_response = Mock()
        details_response.json.return_value = {"id": 20, "original_language": "ja"}
        images_response = Mock()
        images_response.json.return_value = {
            "posters": [
                {"file_path": "/a.jpg", "iso_639_1": None, "vote_average": 8.1},
                {"file_path": "/b.jpg", "iso_639_1": "en", "vote_average": 9.0},
            ]
        }
        get.side_effect = [details_response, images_response]
        adapter = TmdbAdapter("secret", timeout=9)

        details = adapter.details("movie", "20")
        posters = adapter.textless_posters("movie", "20")

        self.assertEqual(details["original_language"], "ja")
        self.assertEqual([p["url"] for p in posters], ["https://image.tmdb.org/t/p/original/a.jpg"])
        self.assertEqual(get.call_args_list[0].kwargs["headers"], {"Authorization": "Bearer secret"})
        self.assertEqual(get.call_args_list[1].kwargs["params"], {"include_image_language": "null"})
        self.assertEqual(get.call_args_list[1].kwargs["timeout"], 9)
        details_response.raise_for_status.assert_called_once_with()
        images_response.raise_for_status.assert_called_once_with()


class FanartAdapterTest(unittest.TestCase):
    @patch("telepiplex_plex.adapters.fanart.requests.get")
    def test_textless_posters_use_correct_external_id_and_filter_lang_00(self, get):
        from telepiplex_plex.adapters.fanart import FanartAdapter

        response = get.return_value
        response.json.return_value = {
            "tvposter": [
                {"url": "https://fanart/a.jpg", "lang": "00", "likes": "4"},
                {"url": "https://fanart/b.jpg", "lang": "en", "likes": "9"},
            ]
        }

        posters = FanartAdapter("fan-key", timeout=7).textless_posters(
            "show", {"tmdb": "20", "tvdb": "30"}
        )

        self.assertEqual([p["url"] for p in posters], ["https://fanart/a.jpg"])
        self.assertEqual(get.call_args.args[0], "https://webservice.fanart.tv/v3/tv/30")
        self.assertEqual(get.call_args.kwargs["params"], {"api_key": "fan-key"})
        self.assertEqual(get.call_args.kwargs["timeout"], 7)
        response.raise_for_status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
