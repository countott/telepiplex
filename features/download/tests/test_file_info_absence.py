"""Missing destination paths must allow creation without hiding provider errors."""

from unittest.mock import patch

import pytest

from telepiplex_download.client import Open115Client, Open115Error
from telepiplex_download.observability import emit_download_observation


class Response:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class NoWait:
    def acquire(self, _endpoint_class):
        return 0


@pytest.mark.parametrize("code", [430004, "430004"])
@pytest.mark.parametrize("data", [None, {}, []])
def test_file_info_accepts_only_explicit_provider_not_found(code, data):
    client = Open115Client({"access_token": "test"}, pacer=NoWait())
    client._request = lambda *_args, **_kwargs: {
        "state": False, "code": code, "message": "文件（夹）不存在或已删除。", "data": data,
    }
    assert client.get_file_info("/new/season") is None
    assert client.get_file_info_by_id("deleted-id") is None


@pytest.mark.parametrize("payload", [
    {"state": False, "code": 40140125, "data": []},
    {"state": False, "code": 50001, "data": []},
    {"state": False, "code": 20004, "data": []},
    {"state": True, "code": 430004, "data": []},
    {"state": False, "success": True, "code": 430004},
    {"state": False, "code": 430004, "data": {"file_id": "existing"}},
    {"state": False, "code": 430004, "data": [{"file_id": "existing"}]},
    {"state": False, "code": 430004, "file_id": "existing", "data": []},
    {"state": False, "code": 430004, "data": {"error": {"code": 50001}}},
    {"state": False, "code": 430004, "error": {"code": 50001}},
    {"state": False, "code": 430004, "errno": 50001},
    {"state": False, "code": True, "data": []},
    {"state": False, "code": "430004\nprivate", "data": []},
])
def test_file_info_rejects_unknown_or_contradictory_absence(payload):
    client = Open115Client({"access_token": "test"}, pacer=NoWait())
    client._request = lambda *_args, **_kwargs: payload
    with pytest.raises(Open115Error):
        client.get_file_info("/unknown")


def test_failed_batch_lookup_is_not_returned_as_absence():
    client = Open115Client({"access_token": "test"}, pacer=NoWait())
    client._request = lambda *_args, **_kwargs: {"state": False, "code": 50001, "data": []}
    with pytest.raises(Open115Error) as caught:
        client.get_file_info_batch(["/unknown"])
    assert caught.value.code == "50001"


def test_new_logged_season_path_can_be_created_after_explicit_absence():
    target = "/真人剧集/火线 (2002) ⋯ The Wire/The Wire Season 05"

    class Session:
        def __init__(self):
            self.folders = {"/真人剧集": {"file_id": "library", "file_category": "0"}}
            self.created = []
            self.reads = []

        def request(self, method, url, **kwargs):
            data = kwargs.get("data") or {}
            if url.endswith("/open/folder/get_info"):
                assert method == "POST"
                path = data["path"]
                self.reads.append(path)
                if path in self.folders:
                    return Response({"state": True, "code": 0, "data": self.folders[path]})
                return Response({"state": False, "code": 430004, "data": []})
            assert method == "POST" and url.endswith("/open/folder/add")
            self.created.append((data["pid"], data["file_name"]))
            parent = next(path for path, info in self.folders.items() if info["file_id"] == data["pid"])
            info = {"file_id": f"created-{len(self.created)}", "file_category": "0"}
            self.folders[parent + "/" + data["file_name"]] = info
            return Response({"state": True, "code": 0, "data": info})

    session = Session()
    client = Open115Client({"access_token": "test"}, session=session, pacer=NoWait())
    assert client.get_file_info(target) is None
    assert client.create_dir_recursive(target) == {"file_id": "created-2", "file_category": "0"}
    assert session.created == [("library", "火线 (2002) ⋯ The Wire"), ("created-1", "The Wire Season 05")]
    assert client.get_file_info(target)["file_id"] == "created-2"


def test_request_observation_retains_safe_provider_code_without_response_body():
    class Session:
        def request(self, *_args, **_kwargs):
            return Response({"state": False, "code": 50001, "message": "/private/path"})

    client = Open115Client(
        {"access_token": "test"}, session=Session(), pacer=NoWait(),
        on_observation=lambda event, facts: emit_download_observation(event, **facts),
    )
    with patch("telepiplex_download.observability.logger") as logger:
        with pytest.raises(Open115Error, match="provider code 50001"):
            client.get_file_info("/private/path")
        record = logger.info.call_args.kwargs["extra"]
    assert record["diagnostic_fields"]["output"]["provider_code"] == "50001"
    assert "private" not in repr(record)


@pytest.mark.parametrize("unsafe_code", ["/private/path", "50001\nprivate", True, {}, "1" * 11])
def test_observation_drops_non_numeric_or_unbounded_provider_code(unsafe_code):
    with patch("telepiplex_download.observability.logger") as logger:
        emit_download_observation("download.request.completed", provider_code=unsafe_code)
    assert logger.info.call_args.kwargs["extra"]["diagnostic_fields"]["output"] == {}
