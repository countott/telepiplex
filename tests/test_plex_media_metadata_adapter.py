import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.adapters.plex import PlexAdapter


class PlexMediaMetadataAdapterTest(unittest.TestCase):
    def _adapter(self):
        episode = Mock()
        episode.ratingKey = "100"
        episode.title = "Episode 100"
        episode.originalTitle = ""
        episode.year = 2022
        episode.type = "episode"
        episode.summary = ""
        episode.guids = []
        part = Mock()
        part.id = 1
        part.file = (
            "/mnt/media/真人剧集/想见你 (Someday or One Day)/"
            "Someday or One Day Season 00/Someday or One Day S00E100.mkv"
        )
        part.audioStreams.return_value = []
        part.subtitleStreams.return_value = []
        episode.media = [SimpleNamespace(parts=[part])]
        episode.reload.return_value = episode

        show = Mock()
        show.guids = [SimpleNamespace(id="tvdb://series-1")]
        show.episode.return_value = episode

        section = Mock()
        section.getGuid.return_value = show
        section.search.return_value = [show]

        adapter = PlexAdapter.__new__(PlexAdapter)
        adapter.server = Mock()
        adapter.server.library.sectionByID.return_value = section
        adapter.server.fetchItem.return_value = episode
        return adapter, section, show, episode

    def test_tvdb_series_id_bypasses_localized_title_and_year_search(self):
        adapter, section, show, _episode = self._adapter()

        result = adapter.find_series_episode(
            "13",
            tvdb_series_id="series-1",
            title="A Title That Is Not In Plex",
            year="1900",
            season_number=0,
            episode_number=100,
            expected_final_paths=[
                "/真人剧集/想见你 (Someday or One Day)/"
                "Someday or One Day Season 00/Someday or One Day S00E100.mkv"
            ],
        )

        self.assertEqual(result["rating_key"], "100")
        section.getGuid.assert_called_once_with("tvdb://series-1")
        section.search.assert_not_called()
        show.episode.assert_called_once_with(season=0, episode=100)

    def test_missing_tvdb_series_id_falls_back_to_title_and_year(self):
        adapter, section, show, _episode = self._adapter()

        result = adapter.find_series_episode(
            "13",
            title="Someday or One Day",
            year="2019",
            season_number=0,
            episode_number=100,
        )

        self.assertEqual(result["rating_key"], "100")
        section.getGuid.assert_not_called()
        section.search.assert_called_once_with(
            title="Someday or One Day",
            libtype="show",
            year=2019,
        )
        show.episode.assert_called_once_with(season=0, episode=100)

    def test_find_series_episode_rejects_wrong_media_part_path(self):
        adapter, _section, _show, _episode = self._adapter()

        result = adapter.find_series_episode(
            "13",
            tvdb_series_id="series-1",
            title="Someday or One Day",
            year="2019",
            season_number=0,
            episode_number=100,
            expected_final_paths=["/真人剧集/wrong/S00E100.mkv"],
        )

        self.assertIsNone(result)

    def test_edit_custom_episode_writes_only_confirmed_supported_fields(self):
        adapter, _section, _show, episode = self._adapter()

        adapter.edit_custom_episode_metadata(
            "100",
            title="想见你：电影版",
            summary="电影版延续电视剧故事。",
            original_release_date="2022-12-24",
            year="2022",
        )

        episode.editTitle.assert_called_once_with("想见你：电影版", locked=True)
        episode.editSummary.assert_called_once_with("电影版延续电视剧故事。", locked=True)
        episode.editOriginallyAvailable.assert_called_once_with(
            "2022-12-24", locked=True
        )
        episode.editField.assert_not_called()

    def test_edit_custom_episode_uses_year_when_release_date_is_absent(self):
        adapter, _section, _show, episode = self._adapter()

        adapter.edit_custom_episode_metadata("100", year="2022")

        episode.editField.assert_called_once_with("year", 2022, locked=True)


if __name__ == "__main__":
    unittest.main()
