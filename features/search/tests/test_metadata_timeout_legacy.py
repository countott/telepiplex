"""Freeze v2 HTTP timeout behavior until v3 migrates old effective values."""
from unittest.mock import Mock
from pathlib import Path

import pytest

from telepiplex_search.adapters import anilist, tmdb, tvdb
from telepiplex_search.context import runtime_context


@pytest.mark.parametrize("configured,want", [(1, 5), (5, 5), (60, 60), (300, 60)])
@pytest.mark.parametrize("provider", ["tmdb", "tvdb", "anilist"])
def test_metadata_http_timeout_retains_legacy_clamp(monkeypatch, provider, configured, want):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    from app.runtime.config_projection import ProjectionContract, project_feature_config
    runtime_context.configure(project_feature_config("search", {}, document={
        "config_version": 2, "shared": {"tmdb": {"api_key": "fixture"}}, "features": {"search": {"metadata": {provider: {
            "enable": True, "api_key": "fixture", "timeout": configured,
        }}}},
    }, contract=ProjectionContract({})))
    response = Mock(status_code=200)
    response.json.return_value = {"data": {"token": "fixture-token"}}
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs["timeout"])
        return response
    monkeypatch.setattr(tmdb.requests, "get", request)
    monkeypatch.setattr(tmdb.requests, "post", request)
    if provider == "tmdb":
        tmdb._tmdb_get("/search/movie")
    elif provider == "anilist":
        anilist._anilist_post("query {}", {})
    else:
        tvdb._clear_token_cache()
        tvdb._tvdb_get("/search")
    assert calls == ([want, want] if provider == "tvdb" else [want])
