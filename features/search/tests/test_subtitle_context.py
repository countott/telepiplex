from telepiplex_search.subtitle_context import build_subtitle_context


def test_movie_runtime_is_expected_only_and_does_not_change_naming_identity():
    identity = {"content_kind": "movie", "original_language": "en", "runtime_minutes": 169}
    result = build_subtitle_context({"identity": identity}, {"entity_snapshot": {"external_ids": {"imdb": "tt0816692"}}})
    assert result == {"original_language": "en", "imdb_id": "tt0816692", "expected_duration_seconds": 10140}
    assert "expected_duration_seconds" not in identity


def test_series_average_or_season_duration_is_never_an_episode_duration():
    result = build_subtitle_context({"identity": {"content_kind": "series", "runtime_minutes": 45}, "items": [
        {"season_number": 1, "episode_number": 1, "runtime_minutes": 57},
        {"season_number": 1, "episode_number": 2},
        {"season_number": 2, "runtime_minutes": 450},
        {"season_number": True, "episode_number": 3, "runtime_minutes": 20},
    ]}, {})
    assert result["episode_durations"] == {"S01E01": 3420}
    assert "expected_duration_seconds" not in result


def test_unknown_or_invalid_runtime_stays_unknown():
    for value in (None, True, "unknown", -1, float("nan"), float("inf"), 99999):
        result = build_subtitle_context({"identity": {"content_kind": "movie", "runtime_minutes": value}}, {})
        assert "expected_duration_seconds" not in result
