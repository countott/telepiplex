from dataclasses import replace

import pytest

from telepiplex_caption.matching import episode_coordinates, match_candidate, match_document
from telepiplex_caption.models import MediaQuery, SubtitleCandidate, SubtitleDocument


def test_serializable_models_accept_provider_extra_fields():
    query = MediaQuery.from_dict({"title": "Example", "aliases": ["示例"], "unused": True})
    assert query.to_dict()["title"] == "Example"
    assert match_candidate(query, SubtitleCandidate("fixture", "1", "示例.2020")).accepted


def test_original_title_and_chinese_aliases_match():
    query = MediaQuery("示例", original_title="The Example", year=2020, aliases=("範例",))
    assert match_candidate(query, SubtitleCandidate("fixture", "1", "The.Example.2020.1080p")).accepted
    assert match_candidate(query, SubtitleCandidate("fixture", "1", "范例 2020")).accepted
    assert not match_candidate(query, SubtitleCandidate("fixture", "1", "Other Film 2020")).accepted


def test_provider_tv_media_type_matches_sdk_series():
    assert match_candidate(MediaQuery("Example", media_type="series"), SubtitleCandidate("fixture", "1", "Example", media_type="tv")).accepted


@pytest.mark.parametrize("title", ["The Science of Interstellar", "Interstellar: The Documentary", "Interstellar 2", "星际穿越的科学"])
def test_related_documentary_or_sequel_is_not_the_requested_film(title):
    query = MediaQuery("Interstellar", aliases=("星际穿越",), year=2014)
    assert not match_candidate(query, SubtitleCandidate("fixture", "1", title)).accepted


def test_title_with_numeric_year_and_release_alias_remains_matchable():
    assert match_candidate(MediaQuery("2001: A Space Odyssey", year=1968), SubtitleCandidate("fixture", "1", "2001: A Space Odyssey.1968.BluRay")).accepted
    assert match_candidate(MediaQuery("Interstellar", aliases=("星际穿越",), year=2014), SubtitleCandidate("fixture", "1", "星际穿越/Interstellar.IMAX.2014.BluRay")).accepted


@pytest.mark.parametrize("title,year,release", [("Reply 1988", 2015, "Reply.1988.S01E01"), ("2001: A Space Odyssey", 1968, "2001.A.Space.Odyssey.1968"), ("Blade Runner 2049", 2017, "Blade.Runner.2049.2017")])
def test_numeric_title_does_not_supply_false_release_year(title, year, release):
    query = MediaQuery(title, year=year)
    candidate = SubtitleCandidate("fixture", "1", title, release_name=release)
    assert match_candidate(query, candidate).accepted
    assert match_document(query, candidate, SubtitleDocument(release + ".chi.srt", "srt", b"")).accepted
    assert "year_mismatch" in match_candidate(query, replace(candidate, release_name=title + ".2023")).reasons
    assert "document_year_mismatch" in match_document(query, candidate, SubtitleDocument(title + ".2023.srt", "srt", b"")).reasons


@pytest.mark.parametrize("title,release", [("Spider-Man: Homecoming", "Spider.Man.Homecoming.2017.BluRay"), ("Amélie", "Amelie.2001.BluRay"), ("Schindler's List", "Schindlers.List.1993.BluRay")])
def test_release_punctuation_and_accents_do_not_change_identity(title, release):
    assert match_candidate(MediaQuery(title), SubtitleCandidate("fixture", "1", release)).accepted


def test_split_disc_subtitles_require_explicit_matching_disc_video():
    candidate = SubtitleCandidate("fixture", "1", "Example.2020.CD1")
    assert "partial_disc_subtitle" in match_candidate(MediaQuery("Example", year=2020), candidate).reasons
    assert match_candidate(MediaQuery("Example", year=2020, release_name="Example.2020.CD1.mkv"), candidate).accepted


@pytest.mark.parametrize("field,value,reason", [("year", 2021, "year_mismatch"), ("season", 2, "season_mismatch"), ("episode", 7, "episode_mismatch")])
def test_wrong_work_coordinates_rejected(field, value, reason):
    query = MediaQuery("Example", year=2020, media_type="tv", season=1, episode=2)
    candidate = SubtitleCandidate("fixture", "1", "Example", year=2020, season=1, episode=2)
    assert reason in match_candidate(query, replace(candidate, **{field: value})).reasons


@pytest.mark.parametrize("release", ["Example.2020.BluRay", "Example.2020.WEB-DL.Extended"])
def test_source_or_cut_conflict_rejected(release):
    query = MediaQuery("Example", year=2020, release_name="Example.2020.WEB-DL")
    candidate = SubtitleCandidate("fixture", "1", "Example 2020", release_name=release)
    assert not match_candidate(query, candidate).accepted


def test_whole_season_archive_selects_only_correct_episode():
    query = MediaQuery("Example", media_type="tv", season=1, episode=2)
    candidate = SubtitleCandidate("fixture", "1", "Example S01")
    assert match_candidate(query, candidate).accepted
    assert match_document(query, candidate, SubtitleDocument("Example.S01E02.chs.srt", "srt", b"")).accepted
    assert "document_episode_mismatch" in match_document(query, candidate, SubtitleDocument("Example.S01E03.srt", "srt", b"")).reasons
    assert "document_episode_unverified" in match_document(query, candidate, SubtitleDocument("chs.srt", "srt", b"")).reasons


def test_package_different_film_is_rejected():
    query = MediaQuery("Example", year=2020)
    candidate = SubtitleCandidate("fixture", "1", "Example 2020")
    result = match_document(query, candidate, SubtitleDocument("Other.Movie.2020.srt", "srt", b""))
    assert "document_title_mismatch" in result.reasons


def test_tv_archive_cannot_include_another_show_with_same_episode():
    query = MediaQuery("Example", media_type="tv", season=1, episode=2)
    candidate = SubtitleCandidate("fixture", "1", "Example S01")
    assert "document_title_mismatch" in match_document(query, candidate, SubtitleDocument("Other.Series.S01E02.srt", "srt", b"")).reasons


def test_generic_language_member_inherits_package_identity():
    query = MediaQuery("Example", year=2020)
    candidate = SubtitleCandidate("fixture", "1", "Example 2020")
    assert match_document(query, candidate, SubtitleDocument("Simplified Chinese.srt", "srt", b"")).accepted


def test_opaque_numeric_download_inherits_only_matched_candidate_identity():
    query = MediaQuery("Interstellar", year=2014)
    document = SubtitleDocument("1774886230488.ass", "ass", b"")
    assert match_document(query, SubtitleCandidate("subhd", "1", "Interstellar 2014"), document).accepted
    assert not match_document(query, SubtitleCandidate("subhd", "1", "Other Movie 2014"), document).accepted
    assert not match_document(query, SubtitleCandidate("subhd", "1", "Interstellar 2014"), SubtitleDocument("Other.Movie.ass", "ass", b"")).accepted


def test_id_mismatch_wins_even_when_title_matches():
    query = MediaQuery("Example", imdb_id="tt123")
    candidate = SubtitleCandidate("fixture", "1", "Example", metadata={"imdb_id": "tt456"})
    assert not match_candidate(query, candidate).accepted


def test_series_episode_release_year_may_differ_with_verified_parent_identity():
    query = MediaQuery("Example", media_type="series", year=2020, season=2, episode=1, imdb_id="tt123")
    candidate = SubtitleCandidate("fixture", "1", "Example.S02E01.2022", year=2022, metadata={"imdb_id": "tt123"})
    match = match_candidate(query, candidate)
    assert match.accepted and "series_year_differs" in match.warnings
    assert match_document(query, candidate, SubtitleDocument("Example.S02E01.2022.srt", "srt", b"")).accepted
    assert not match_candidate(query, replace(candidate, metadata={})).accepted


def test_hash_identity_allows_unlabelled_result_only_with_query_hash():
    candidate = SubtitleCandidate("shooter", "1", "subtitle", metadata={"hash_match": True})
    assert match_candidate(MediaQuery("Example", shooter_hash="abc"), candidate).accepted
    assert not match_candidate(MediaQuery("Example"), candidate).accepted


@pytest.mark.parametrize("text,season,episodes", [("Example.S01E02", 1, {2}), ("Example.S01E01-E03", 1, {1, 2, 3}), ("Example.2x03", 2, {3}), ("第2季 第3集", 2, {3}), ("Example.S02", 2, set())])
def test_episode_formats(text, season, episodes):
    assert episode_coordinates(text) == (season, episodes)


def test_explicit_member_episode_wins_over_parent_season_pack_range():
    name = "Example.S01E01-E12/Example.E03.srt"
    assert episode_coordinates(name) == (1, {3})
    candidate = SubtitleCandidate("fixture", "1", "Example S01")
    query = MediaQuery("Example", media_type="series", season=1, episode=3)
    assert match_document(query, candidate, SubtitleDocument(name, "srt", b"")).accepted
    assert not match_document(replace(query, episode=2), candidate, SubtitleDocument(name, "srt", b"")).accepted


@pytest.mark.parametrize("filename,media_type", [("Example.2020.BluRay-002.ass", "movie"), ("Example.S01E01-001_track3_chi.ssa", "series")])
def test_numbered_split_candidate_and_archive_member_require_matching_split_video(filename, media_type):
    query = MediaQuery("Example", media_type=media_type)
    candidate = SubtitleCandidate("fixture", "1", "Example", release_name=filename)
    assert "likely_split_subtitle" in match_candidate(query, candidate).reasons
    plain_candidate = replace(candidate, release_name="")
    assert "likely_split_subtitle" in match_document(query, plain_candidate, SubtitleDocument(filename, "ass", b"")).reasons


def test_anime_absolute_episode_suffix_is_not_treated_as_movie_fragment():
    query = MediaQuery("Example", media_type="series")
    result = match_candidate(query, SubtitleCandidate("fixture", "1", "Example", release_name="Example - 001.ass"))
    assert "likely_split_subtitle" not in result.reasons


def test_encore_requires_explicit_matching_video_edition():
    query = MediaQuery("Example", media_type="series", season=1, episode=1)
    candidate = SubtitleCandidate("fixture", "1", "Example", season=1, episode=1)
    document = SubtitleDocument("Example.S01E01.Encore.SC.ass", "ass", b"")
    assert "document_edition_mismatch" in match_document(query, candidate, document).reasons
    assert match_document(replace(query, release_name="Example.S01E01.Encore.mkv"), replace(candidate, release_name="Example.Encore"), document).accepted
