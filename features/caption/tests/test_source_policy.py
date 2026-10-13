from __future__ import annotations

from types import SimpleNamespace

import pytest

from telepiplex_caption.models import MediaQuery
from telepiplex_caption.source_policy import source_search_decision


def provider(name, **config):
    return SimpleNamespace(name=name, config=config)


@pytest.mark.parametrize("name", ["assrt", "subhd", "xunlei", "r3sub"])
def test_balanced_keeps_public_chinese_sources(name):
    assert source_search_decision(provider(name), MediaQuery("Example", original_language="en")).search


@pytest.mark.parametrize("profile", ["balanced", "all"])
@pytest.mark.parametrize("name", ["opensubtitles", "subdl", "subsource"])
def test_missing_key_cannot_consume_either_profiles_search_slots(name, profile):
    decision = source_search_decision(provider(name, api_key="  "), MediaQuery("Example"), profile=profile)
    assert not decision.search and decision.status == "skipped" and "API Key" in decision.message
    assert source_search_decision(provider(name, api_key="personal-key"), MediaQuery("Example"), profile=profile).search


def test_subdl_and_subsource_are_both_eligible_when_authorized():
    query = MediaQuery("Example")
    assert all(source_search_decision(provider(name, api_key="key"), query).search for name in ("subdl", "subsource"))


@pytest.mark.parametrize("profile", ["balanced", "all"])
def test_hash_and_archive_require_query_or_config_evidence(profile):
    query = MediaQuery("Example")
    assert not source_search_decision(provider("shooter"), query, profile=profile).search
    assert source_search_decision(provider("shooter"), MediaQuery("Example", shooter_hash="hash"), profile=profile).search
    assert not source_search_decision(provider("local_archive", roots=[]), query, profile=profile).search
    assert source_search_decision(provider("local_archive", roots=["/archive"]), query, profile=profile).search


@pytest.mark.parametrize("name", ["zimuku", "subf2m"])
def test_fragile_keyword_sources_remain_available_in_all(name):
    decision = source_search_decision(provider(name, enabled=True), MediaQuery("Example"))
    assert not decision.search and "详情链接" in decision.message
    assert source_search_decision(provider(name), MediaQuery("Example"), profile="all").search


def test_yysub_requires_cookie_only_in_balanced():
    name = "yysub"
    query = MediaQuery("Example")
    assert not source_search_decision(provider(name, cookie=" "), query).search
    assert source_search_decision(provider(name, cookie="session=authorized"), query).search
    assert source_search_decision(provider(name), query, profile="all").search


def test_lwltv_cookie_cannot_substitute_for_interactive_verification():
    query = MediaQuery("Example")
    decision = source_search_decision(provider("lwltv", cookie="session=authorized"), query)
    assert not decision.search and "浏览器验证" in decision.message
    assert source_search_decision(provider("lwltv"), query, profile="all").search


def test_r3sub_is_reserved_for_movies_in_balanced():
    assert source_search_decision(provider("r3sub"), MediaQuery("Example")).search
    for media_type in ("series", "tv"):
        query = MediaQuery("Example", media_type=media_type)
        assert not source_search_decision(provider("r3sub"), query).search
        assert source_search_decision(provider("r3sub"), query, profile="all").search


def test_addic7ed_is_routed_by_media_type_instead_of_original_language():
    for media_type in ("series", "tv"):
        assert source_search_decision(provider("addic7ed"), MediaQuery("Example", media_type=media_type, original_language="zh")).search
    assert not source_search_decision(provider("addic7ed"), MediaQuery("Example", original_language="en")).search
    assert source_search_decision(provider("addic7ed"), MediaQuery("Example"), profile="all").search


@pytest.mark.parametrize("name", ["nekomoe", "mingy", "kitauji", "haruhana"])
@pytest.mark.parametrize("category_kind", ["live_action_movie", "live_action_series"])
def test_confirmed_live_action_excludes_animation_catalogs(name, category_kind):
    query = SimpleNamespace(category_kind=category_kind, original_language="ja")
    assert not source_search_decision(provider(name), query).search
    assert source_search_decision(provider(name), query, profile="all").search


@pytest.mark.parametrize("name", ["nekomoe", "mingy", "kitauji", "haruhana"])
@pytest.mark.parametrize("category_kind", ["animated_movie", "animated_series", "", "unknown"])
def test_animation_and_unknown_categories_keep_catalog_coverage(name, category_kind):
    query = SimpleNamespace(category_kind=category_kind, original_language="en")
    assert source_search_decision(provider(name), query).search
    assert source_search_decision(provider(name), MediaQuery("Example", original_language="zh")).search


@pytest.mark.parametrize("profile", ["balanced", "all"])
@pytest.mark.parametrize("name", ["assrt", "shooter", "opensubtitles", "local_archive", "fixture"])
def test_explicit_disable_takes_priority_over_readiness_and_profile(name, profile):
    decision = source_search_decision(provider(name, enabled=False), MediaQuery("Example"), profile=profile)
    assert not decision.search and decision.status == "disabled"


def test_unknown_adapter_without_config_retains_search_contract():
    assert source_search_decision(SimpleNamespace(name="fixture"), MediaQuery("Example")).search


def test_invalid_profile_does_not_silently_expand_or_narrow_coverage():
    with pytest.raises(ValueError, match="invalid_subtitle_source_profile"):
        source_search_decision(provider("assrt"), MediaQuery("Example"), profile="typo")
