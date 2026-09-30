from copy import deepcopy

import pytest

from telepiplex_caption.metadata import episode_coordinates, make_query, metadata_query, same_work, video_items
from telepiplex_plugin_sdk.media_metadata_v2 import build_media_metadata_v2_id


def media_metadata(*, media_type="movie", season=None, episode=None, confirmed=True):
    provider = "tmdb_movie" if media_type == "movie" else "tmdb_tv"
    value = {"schema_version": 2, "confirmed": confirmed,
        "identity": {"primary_ref": {"provider": provider, "id": "123"}, "provider_refs": {provider: "123"},
                     "media_type": media_type, "title_zh": "示例", "title_en": "Example", "title_original": "Example Original", "year": 2020},
        "scope": {"kind": "movie" if media_type == "movie" else "episode" if episode is not None else "season" if season is not None else "whole_series",
                  "season_number": season, "episode_number": episode},
        "placement": {"category_kind": "live_action_movie" if media_type == "movie" else "live_action_series"}}
    value["metadata_id"] = build_media_metadata_v2_id(value)
    return value


def test_confirmed_identity_preserves_aliases_and_subtitle_context_without_video():
    metadata = media_metadata()
    query = make_query(metadata, {"original_language": "en", "imdb_id": "tt123"})
    assert query.title == "Example"
    assert query.original_title == "Example Original"
    assert query.aliases == ("示例", "Example", "Example Original")
    assert query.tmdb_id == "123" and query.imdb_id == "tt123"
    assert query.original_language == "en" and query.video_path == ""
    assert query.duration_seconds is None


@pytest.mark.parametrize("season,episode", [(None, None), (2, None), (2, 3)])
def test_query_only_scope_is_preserved(season, episode):
    query = make_query(media_metadata(media_type="series", season=season, episode=episode))
    assert (query.season, query.episode) == (season, episode)
    assert query.media_type == "series"


def test_video_release_and_explicit_episode_override_broad_download_scope():
    query = make_query(media_metadata(media_type="series", season=1), {"original_language": "ja"},
        {"name": "Example.S01E03.WEB-DL.mkv", "path": "/电视/Example.S01E03.WEB-DL.mkv", "duration_seconds": 1200, "fps": 23.976, "size": 1234})
    assert (query.season, query.episode) == (1, 3)
    assert query.release_name == "Example.S01E03.WEB-DL.mkv"
    assert query.duration_seconds == 1200 and query.fps == 23.976 and query.file_size == 1234


def test_unnumbered_video_under_broad_series_scope_requires_mapping():
    with pytest.raises(ValueError, match="episode_mapping_unresolved"):
        make_query(media_metadata(media_type="series"), {}, {"path": "/电视/unknown.mkv"})


@pytest.mark.parametrize("metadata", [{}, media_metadata(confirmed=False)])
def test_unconfirmed_or_invalid_metadata_is_rejected(metadata):
    with pytest.raises(ValueError, match="confirmed_media_metadata_required"):
        make_query(metadata)


def test_original_language_is_never_inferred_from_title_language():
    assert make_query(media_metadata()).original_language == ""


def test_metadata_runtime_never_becomes_probed_video_duration():
    query = make_query(media_metadata(), {"expected_duration_seconds": 6000})
    assert query.expected_duration_seconds == 6000
    assert query.duration_seconds is None
    series = media_metadata(media_type="series", season=1, episode=2)
    query = make_query(series, {"expected_duration_seconds": 6000,
        "episode_durations": {"S01E01": 3000, "S01E02": 1800}})
    assert query.expected_duration_seconds == 1800
    assert make_query(series, {"expected_duration_seconds": 6000}).expected_duration_seconds is None


def test_invalid_duration_values_are_ignored():
    for value in (True, "bad", float("inf"), -1, 0):
        query = make_query(media_metadata(), {"expected_duration_seconds": value},
            {"path": "/movie.mkv", "duration_seconds": value})
        assert query.duration_seconds is None and query.expected_duration_seconds is None


def test_metadata_refresh_requires_matching_provider_identity_and_media_type():
    original = media_metadata()
    title_change = deepcopy(original)
    title_change["identity"]["title_en"] = "Another localized title"
    assert same_work(original, title_change)
    other = media_metadata()
    other["identity"]["provider_refs"]["tmdb_movie"] = "456"
    assert not same_work(original, other)
    assert not same_work(original, media_metadata(media_type="series"))
    unrelated_ref = deepcopy(original)
    unrelated_ref["identity"]["provider_refs"] = {"douban_subject": "123"}
    assert not same_work(original, unrelated_ref)


def test_resolution_query_keeps_episode_scope():
    assert metadata_query(media_metadata(media_type="series", season=2, episode=3)) == "Example 2020 S02E03"


def test_video_inventory_never_includes_subtitles_or_directories():
    rows = [{"name": "film.MKV", "path": "/film.MKV"}, {"name": "film.ass"}, {"name": "directory.mp4", "is_dir": True}, "invalid"]
    assert video_items(rows) == [rows[0]]


def test_movie_does_not_inherit_misleading_episode_tags():
    query = make_query(media_metadata(), {}, {"path": "/电影/Example.S01E02.mkv"})
    assert query.season is None and query.episode is None


@pytest.mark.parametrize("name", ["Example.S01E01-E02.mkv", "Example.S01E01E02.mkv", "Example.S01E01-02.mkv", "Example.1x01-02.mkv"])
def test_multiepisode_file_requires_manual_split_mapping(name):
    with pytest.raises(ValueError, match="multi_episode_video_unsupported"):
        make_query(media_metadata(media_type="series", season=1, episode=1), {}, {"path": "/电视/" + name})


def test_resolution_suffix_is_not_mistaken_for_second_episode():
    query = make_query(media_metadata(media_type="series"), {}, {"path": "/电视/Example.S01E01.1080p.mkv"})
    assert (query.season, query.episode) == (1, 1)


def test_explicit_episode_metadata_does_not_guess_unnumbered_video():
    with pytest.raises(ValueError, match="episode_mapping_unresolved"):
        make_query(media_metadata(media_type="series", season=1, episode=1), {}, {"path": "/电视/unknown.mkv"})


def test_sample_and_extra_locations_are_skipped_without_title_substring_matching():
    names = ["/电影/Example/sample.mkv", "/电影/Example/Extras/interview.mp4", "/电影/Example/Example-sample.mkv", "/电影/Trailer (2015)/Trailer.2015.mkv", "/电影/Trailer Park.2020.mkv"]
    rows = [{"path": name} for name in names]
    assert video_items(rows) == rows[3:]
    with pytest.raises(ValueError, match="non_feature_video"):
        make_query(media_metadata(), {}, rows[1])


@pytest.mark.parametrize("value,expected", [("Example.S01E02", (1, 2)), ("Example.2x03", (2, 3)), ("unknown", (None, None))])
def test_episode_coordinates(value, expected):
    assert episode_coordinates(value) == expected
