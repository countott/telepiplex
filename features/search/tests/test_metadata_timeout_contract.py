"""HTTP boundary checks for the declared seconds contract."""
from unittest.mock import Mock

import pytest

from telepiplex_search.adapters import anilist, tmdb, tvdb
from telepiplex_search.context import runtime_context


@pytest.mark.parametrize("provider", ["tmdb", "tvdb", "anilist"])
@pytest.mark.parametrize("value", [1, 5, 60, 300, None, True, False, 0, -1, 301, 10**400, float("nan"), float("inf"), "secret-invalid", [], {}])
def test_declared_seconds_reach_http_or_fail_without_request(monkeypatch, provider, value):
    runtime_context.configure({"metadata": {provider: {"enable": True, "api_key": "fixture", "timeout": value}}})
    response = Mock()
    response.json.return_value = {"data": {"token": "fixture"}}
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs["timeout"])
        return response
    monkeypatch.setattr(tmdb.requests, "get", request)
    monkeypatch.setattr(tmdb.requests, "post", request)
    tvdb._clear_token_cache()
    invoke, error = {
        "tmdb": (lambda: tmdb._tmdb_get("/search/movie"), tmdb.TmdbConfigError),
        "tvdb": (lambda: tvdb._tvdb_get("/search"), tvdb.TvdbConfigError),
        "anilist": (lambda: anilist._anilist_post("query {}", {}), anilist.AniListConfigError),
    }[provider]
    if type(value) in (int, float) and value in (1, 5, 60, 300):
        invoke()
        assert calls == ([value, value] if provider == "tvdb" else [value])
    else:
        with pytest.raises(error, match=f"metadata.{provider}.timeout") as caught:
            invoke()
        assert "secret-invalid" not in str(caught.value)
        assert calls == []


@pytest.mark.parametrize('provider', ['tmdb', 'tvdb', 'anilist'])
@pytest.mark.parametrize('malformed', [None, [], 'private-invalid'])
def test_malformed_provider_settings_fail_with_safe_field_error(monkeypatch, provider, malformed):
    runtime_context.configure({'metadata': {provider: malformed}})
    def no_request(*args, **kwargs):
        pytest.fail('invalid provider configuration issued HTTP')
    monkeypatch.setattr(tmdb.requests, 'get', no_request)
    monkeypatch.setattr(tmdb.requests, 'post', no_request)
    parser, error = {'tmdb': (tmdb._get_tmdb_config, tmdb.TmdbConfigError),
                     'tvdb': (tvdb._get_tvdb_config, tvdb.TvdbConfigError),
                     'anilist': (anilist._get_anilist_config, anilist.AniListConfigError)}[provider]
    with pytest.raises(error, match=f'metadata.{provider}') as caught:
        parser()
    assert 'private-invalid' not in str(caught.value)


@pytest.mark.parametrize('provider', ['tmdb', 'tvdb', 'anilist'])
def test_missing_timeout_alone_defaults_to_fifteen(provider):
    runtime_context.configure({'metadata': {provider: {'enable': True, 'api_key': 'fixture'}}})
    parser = {'tmdb': tmdb._get_tmdb_config, 'tvdb': tvdb._get_tvdb_config,
              'anilist': anilist._get_anilist_config}[provider]
    assert parser()['timeout'] == 15
