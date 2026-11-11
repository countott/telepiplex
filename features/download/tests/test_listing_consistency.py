"""An inconsistent listing is not treated as a transient success."""

import pytest

from telepiplex_download.client import Open115Client, Open115Error


def test_inconsistent_count_stops_without_reading_away_the_evidence(monkeypatch):
    monkeypatch.setattr("telepiplex_download.client.time.sleep", lambda _: None)
    observations = []
    client = Open115Client({}, on_observation=lambda event, facts: observations.append((event, facts)))
    responses = iter([
        {"state": True, "code": 0, "count": 1, "offset": 0, "data": []},
        {"state": True, "code": 0, "count": 0, "offset": 0, "data": []},
    ])
    calls = []
    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return next(responses)
    client._request = request
    params = {"cid": "folder", "offset": 0, "limit": 1, "show_dir": 1}
    with pytest.raises(Open115Error):
        client.get_file_list(params)
    assert len(calls) == 1
    assert all(method == "GET" for method, _, _ in calls)
    query = calls[0][2]["params"]
    assert (query["cur"], query["custom_order"], query["o"], query["asc"]) == (1, 1, "file_name", 1)
    assert "cur" not in params
    assert observations[0][1]["declared_count"] == 1
    assert observations[0][1]["retryable"] is False


@pytest.mark.parametrize("response", [
    {"state": True, "code": 0, "data": [], "count": 1, "offset": 0},
    {"state": True, "code": 0, "data": [{"fid": "x"}], "count": 0, "offset": 0},
    {"state": True, "code": 0, "data": [], "has_more": True},
])
def test_inconsistency_fails_closed_after_one_read(monkeypatch, response):
    monkeypatch.setattr("telepiplex_download.client.time.sleep", lambda _: None)
    client = Open115Client({})
    calls = []
    def request(method, path, **kwargs):
        calls.append(method)
        return response
    client._request = request
    with pytest.raises(Open115Error) as failure:
        client.get_file_list({"cid": "folder", "offset": 0})
    assert failure.value.code == "file_tree_incomplete"
    assert calls == ["GET"]
