import copy
import threading
from urllib.parse import urlsplit

import pytest
import requests

from telepiplex_download.client import Open115Client


SOURCE = "/Downloads/Show.mkv"
TARGET_DIR = "/Series/Show"
TARGET = TARGET_DIR + "/Show.mkv"
FINGERPRINT = {"file_category": "1", "sha1": "a" * 40, "size_byte": 1024}


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
    def acquire(self, endpoint_class):
        return 0.0


class MoveSession:
    def __init__(self, mode="correct"):
        self.mode = mode
        self.files = {
            SOURCE: {"file_id": "source", **FINGERPRINT},
            "/Downloads": {"file_id": "downloads", "file_category": "0"},
            TARGET_DIR: {"file_id": "target-dir", "file_category": "0"},
        }
        self.calls = []
        self.copied = False
        self.target_reads = 0
        self.deleted_ids = []

    def request(self, method, url, **kwargs):
        endpoint = urlsplit(url).path
        self.calls.append((method, endpoint, {**copy.deepcopy(kwargs), "_after_copy": self.copied}))
        if endpoint == "/open/folder/get_info":
            if method == "POST":
                path = kwargs["data"]["path"]
            else:
                assert method == "GET" and "file_id" in kwargs["params"]
                path = next((path for path, info in self.files.items()
                             if info["file_id"] == kwargs["params"]["file_id"]), "")
            if path == TARGET and self.copied:
                self.target_reads += 1
                if self.mode == "delayed" and self.target_reads == 3:
                    self.files[TARGET] = {"file_id": "copy", **FINGERPRINT}
                if self.mode == "timeout":
                    raise requests.ReadTimeout("target unavailable")
            value = copy.deepcopy(self.files.get(path))
            return Response({"state": True, "code": 0, "data": [value] if value else []})
        if endpoint == "/open/ufile/copy":
            self.copied = True
            if self.mode in {"correct", "replace_source", "delete_rejected", "source_retained", "target_missing_after_delete", "target_changed_after_delete"}:
                self.files[TARGET] = {"file_id": "copy", **FINGERPRINT}
            elif self.mode == "wrong":
                self.files[TARGET] = {"file_id": "foreign", **FINGERPRINT, "sha1": "b" * 40}
            if self.mode == "replace_source":
                self.files[SOURCE] = {"file_id": "replacement", **FINGERPRINT}
            return Response({"state": True, "code": 0})
        if endpoint == "/open/ufile/delete":
            file_id = str(kwargs["data"]["file_ids"])
            self.deleted_ids.append(file_id)
            if self.mode == "delete_rejected":
                return Response({"state": False, "code": 50001})
            if self.mode != "source_retained":
                self.files = {path: info for path, info in self.files.items() if info["file_id"] != file_id}
            if self.mode == "target_missing_after_delete":
                self.files.pop(TARGET, None)
            if self.mode == "target_changed_after_delete":
                self.files[TARGET] = {"file_id": "replacement-target", **FINGERPRINT, "sha1": "b" * 40}
            return Response({"state": True, "code": 0})
        raise AssertionError(endpoint)

    def copy_calls(self):
        return sum(endpoint == "/open/ufile/copy" for _, endpoint, _ in self.calls)


def make_client(mode="correct"):
    session = MoveSession(mode)
    client = Open115Client(
        {"access_token": "test", "timeout": 30, "request_interval": 0},
        session=session, pacer=NoWait(),
    )
    return client, session


@pytest.mark.parametrize("mode", ["missing", "wrong", "timeout"])
def test_unverified_copy_never_deletes_source(mode):
    client, session = make_client(mode)
    result = client.move_file_detailed(SOURCE, TARGET_DIR)
    assert not result["source_deleted"]
    assert result["state"] != "moved"
    assert SOURCE in session.files
    assert session.deleted_ids == []
    assert session.copy_calls() == 1
    assert session.target_reads <= 3


@pytest.mark.parametrize("mode", ["target_missing_after_delete", "target_changed_after_delete"])
def test_post_delete_target_verification_cannot_report_a_false_move_success(mode):
    client, session = make_client(mode)
    result = client.move_file_detailed(SOURCE, TARGET_DIR)
    assert result["state"] == "copy_failed"
    assert result["source_deleted"] is True
    assert session.deleted_ids == ["source"]
    assert result["error"] == "target_identity_unverified_after_delete"


def test_delayed_target_is_verified_before_captured_source_id_is_deleted():
    client, session = make_client("delayed")
    result = client.move_file_detailed(SOURCE, TARGET_DIR)
    assert result["state"] == "moved"
    assert session.target_reads >= 3
    assert session.deleted_ids == ["source"]
    assert SOURCE not in session.files and session.files[TARGET]["sha1"] == FINGERPRINT["sha1"]
    assert session.copy_calls() == 1


def test_changed_source_identity_after_copy_is_preserved():
    client, session = make_client("replace_source")
    result = client.move_file_detailed(SOURCE, TARGET_DIR)
    assert not result["source_deleted"]
    assert session.deleted_ids == []
    assert session.files[SOURCE]["file_id"] == "replacement"


@pytest.mark.parametrize("mode", ["delete_rejected", "source_retained"])
def test_source_absence_is_required_before_move_success(mode):
    client, session = make_client(mode)
    result = client.move_file_detailed(SOURCE, TARGET_DIR)
    assert not result["source_deleted"]
    assert result["state"] != "moved"
    assert SOURCE in session.files and TARGET in session.files


def test_identical_prior_copy_is_recovered_without_another_copy_request():
    client, session = make_client()
    session.files[TARGET] = {"file_id": "prior-copy", **FINGERPRINT}
    result = client.move_file_detailed(SOURCE, TARGET_DIR)
    assert result["state"] == "moved"
    assert session.copy_calls() == 0
    assert session.deleted_ids == ["source"]


def test_foreign_target_is_retained_before_copy_or_delete():
    client, session = make_client()
    session.files[TARGET] = {"file_id": "foreign", **FINGERPRINT, "size_byte": 2048}
    result = client.move_file_detailed(SOURCE, TARGET_DIR)
    assert not result["source_deleted"]
    assert session.copy_calls() == 0 and session.deleted_ids == []
    assert session.files[TARGET]["file_id"] == "foreign"


@pytest.mark.parametrize("patch", [{"sha1": ""}, {"size_byte": None}, {"size_byte": False}, {"size_byte": -1}])
def test_incomplete_source_fingerprint_cannot_authorize_copy_delete(patch):
    client, session = make_client()
    session.files[SOURCE].update(patch)
    result = client.move_file_detailed(SOURCE, TARGET_DIR)
    assert not result["source_deleted"]
    assert session.copy_calls() == 0 and session.deleted_ids == []


def test_target_read_timeout_is_local_and_does_not_change_other_threads():
    client, session = make_client("timeout")
    result = client.move_file_detailed(SOURCE, TARGET_DIR)
    assert not result["source_deleted"]
    target_timeouts = [kw["timeout"] for method, endpoint, kw in session.calls
                       if (kw.get("data") or {}).get("path") == TARGET and kw["_after_copy"]]
    assert all(0 < timeout <= 10 for timeout in target_timeouts)
    assert client.timeout == 30
    thread = threading.Thread(target=lambda: client.get_file_info("/Downloads"))
    thread.start()
    thread.join(1)
    assert not thread.is_alive()
    assert session.calls[-1][2]["timeout"] == 30


def test_one_http_timeout_can_consume_entire_verification_budget(monkeypatch):
    client, session = make_client("timeout")
    clock = [100.0]
    original = session.request

    def request(method, url, **kwargs):
        if (kwargs.get("data") or {}).get("path") == TARGET and session.copied:
            clock[0] += kwargs["timeout"]
        return original(method, url, **kwargs)

    session.request = request
    monkeypatch.setattr("telepiplex_download.client.time.monotonic", lambda: clock[0])
    result = client.move_file_detailed(SOURCE, TARGET_DIR)
    assert not result["source_deleted"]
    assert clock[0] == 110.0
    assert session.target_reads == 1 and session.deleted_ids == []
    assert client.timeout == 30


def test_target_verification_budget_is_isolated_while_other_thread_reads():
    client, session = make_client()
    entered, release = threading.Event(), threading.Event()
    original = session.request
    output = {}

    def request(method, url, **kwargs):
        if (kwargs.get("data") or {}).get("path") == TARGET and session.copied:
            entered.set()
            assert release.wait(2)
        return original(method, url, **kwargs)

    session.request = request
    worker = threading.Thread(target=lambda: output.update(client.move_file_detailed(SOURCE, TARGET_DIR)))
    worker.start()
    try:
        assert entered.wait(1)
        client.get_file_info("/Downloads")
        assert session.calls[-1][2]["timeout"] == 30
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and output["state"] == "moved"


@pytest.mark.parametrize("replacement", [{"file_id": "replacement", **FINGERPRINT}, {"file_id": "replacement"}])
def test_delete_uses_captured_id_if_path_is_replaced_after_source_recheck(replacement):
    client, session = make_client()
    original = session.request
    replaced = False

    def request(method, url, **kwargs):
        nonlocal replaced
        response = original(method, url, **kwargs)
        if (kwargs.get("data") or {}).get("path") == SOURCE and session.copied and not replaced:
            old = session.files.pop(SOURCE)
            session.files["/Elsewhere/original.mkv"] = old
            session.files[SOURCE] = dict(replacement)
            replaced = True
        return response

    session.request = request
    result = client.move_file_detailed(SOURCE, TARGET_DIR)
    assert session.deleted_ids == ["source"]
    assert session.files[SOURCE]["file_id"] == "replacement"
    assert session.files[TARGET]["sha1"] == FINGERPRINT["sha1"]
    assert result["source_deleted"]


def test_path_info_uses_post_form_and_normalizes_single_array_as_read():
    client, session = make_client()
    assert client.get_file_info(SOURCE) == session.files[SOURCE]
    assert session.calls[0][0] == "POST"
    assert session.calls[0][2]["data"] == {"path": SOURCE}
    assert client._classify_endpoint("POST", "/open/folder/get_info") == "storage.read"


def test_id_info_uses_get_and_normalizes_single_array():
    client, session = make_client()
    assert client.get_file_info_by_id("source") == session.files[SOURCE]
    assert session.calls[0][0] == "GET" and session.calls[0][2]["params"] == {"file_id": "source"}


@pytest.mark.parametrize("path", [SOURCE, TARGET])
def test_provider_error_is_unknown_and_cannot_authorize_copy_delete(path):
    client, session = make_client()
    original = session.request

    def request(method, url, **kwargs):
        if (kwargs.get("data") or {}).get("path") == path:
            return Response({"state": False, "code": 50001, "data": []})
        return original(method, url, **kwargs)

    session.request = request
    from telepiplex_download.client import Open115Error
    with pytest.raises(Open115Error):
        client.move_file_detailed(SOURCE, TARGET_DIR)
    assert SOURCE in session.files and session.copy_calls() == 0 and session.deleted_ids == []


@pytest.mark.parametrize("sha1", ["not-a-sha1", "a" * 39, "g" * 40, "a" * 41])
def test_malformed_sha1_cannot_authorize_copy_delete(sha1):
    client, session = make_client()
    session.files[SOURCE]["sha1"] = sha1
    result = client.move_file_detailed(SOURCE, TARGET_DIR)
    assert not result["source_deleted"] and session.copy_calls() == 0 and session.deleted_ids == []


def test_correct_target_arriving_after_total_budget_cannot_authorize_delete(monkeypatch):
    client, session = make_client()
    clock = [100.0]
    original = session.request

    def request(method, url, **kwargs):
        response = original(method, url, **kwargs)
        if (kwargs.get("data") or {}).get("path") == TARGET and session.copied:
            clock[0] += 11
        return response

    session.request = request
    monkeypatch.setattr("telepiplex_download.client.time.monotonic", lambda: clock[0])
    result = client.move_file_detailed(SOURCE, TARGET_DIR)
    assert not result["source_deleted"] and SOURCE in session.files and session.deleted_ids == []


def test_listing_keeps_the_outer_provider_integrity_envelope():
    client, session = make_client()
    payload = {"state": True, "code": 0, "data": [], "count": 0, "offset": 0}
    session.request = lambda *args, **kwargs: Response(payload)
    assert client.get_file_list({"cid": "release", "offset": 0, "limit": 1}) == payload


@pytest.mark.parametrize("payload", [
    {"state": False, "code": 50001, "data": []},
    {"state": False, "code": 0, "data": []},
    {"state": True, "code": 50001, "data": []},
    {"success": False, "code": 0, "data": []},
])
def test_listing_provider_failure_is_never_an_empty_success(payload):
    from telepiplex_download.client import Open115Error
    client, session = make_client()
    session.request = lambda *args, **kwargs: Response(payload)
    with pytest.raises(Open115Error):
        client.get_file_list({"cid": "release", "offset": 0})


def test_direct_directory_name_consumer_understands_preserved_listing_envelope():
    client, session = make_client()
    original = session.request

    def request(method, url, **kwargs):
        if url.endswith("/open/ufile/files"):
            return Response({"state": True, "code": 0, "count": 1, "offset": 0,
                             "data": [{"fn": "Show.mkv", "file_id": "source"}]})
        return original(method, url, **kwargs)

    session.request = request
    assert client.get_files_from_dir("/Downloads") == ["Show.mkv"]


@pytest.mark.parametrize("payload", [
    {"state": True, "code": 0, "data": [], "has_more": True},
    {"state": True, "code": 0, "data": {"code": 50001, "data": []}},
    {"state": True, "code": 0, "list": [], "data": [{"file_id": "source", "fn": "Show.mkv", **FINGERPRINT}]},
])
def test_real_sdk_tree_rejects_every_failed_or_conflicting_wire_wrapper(payload):
    from telepiplex_download.client import Open115Error
    client, session = make_client()
    original = session.request
    session.request = lambda method, url, **kw: Response(payload) if url.endswith("/open/ufile/files") else original(method, url, **kw)
    with pytest.raises(Open115Error):
        client.get_file_tree("/Downloads")


def test_real_sdk_tree_rejects_unfulfilled_continuation_without_global_scan_state():
    from telepiplex_download.client import Open115Error
    client, session = make_client()
    original = session.request

    def request(method, url, **kwargs):
        if url.endswith("/open/ufile/files"):
            return Response({"state": True, "code": 0, "data": [{"file_id": "source", "fn": "Show.mkv", **FINGERPRINT}], "has_more": True} if not kwargs["params"]["offset"] else {"state": True, "code": 0, "data": []})
        return original(method, url, **kwargs)

    session.request = request
    with pytest.raises(Open115Error):
        client.get_file_tree("/Downloads")


def test_strict_info_rejects_success_flag_with_nonzero_provider_error():
    from telepiplex_download.client import Open115Error
    client, session = make_client()
    original = session.request

    def request(method, url, **kwargs):
        response = original(method, url, **kwargs)
        return Response({**response.payload, "state": True, "code": 50001}) if url.endswith("/open/folder/get_info") else response

    session.request = request
    with pytest.raises(Open115Error):
        client.move_file_detailed(SOURCE, TARGET_DIR)
    assert session.copy_calls() == 0 and session.deleted_ids == []


def test_post_delete_source_and_target_share_a_bounded_verification_budget(monkeypatch):
    client, session = make_client()
    original = session.request
    clock = [100.0]
    source_timeouts = []

    def request(method, url, **kwargs):
        response = original(method, url, **kwargs)
        if (kwargs.get("data") or {}).get("path") == SOURCE and session.deleted_ids:
            source_timeouts.append(kwargs["timeout"])
            clock[0] += 11
        return response

    session.request = request
    monkeypatch.setattr("telepiplex_download.client.time.monotonic", lambda: clock[0])
    result = client.move_file_detailed(SOURCE, TARGET_DIR)
    assert result["state"] != "moved"
    assert result["source_deleted"] is True
    assert source_timeouts and 0 < source_timeouts[0] <= 10


def test_original_id_moved_elsewhere_is_not_mistaken_for_a_deleted_source():
    client, session = make_client()
    original = session.request

    def request(method, url, **kwargs):
        if url.endswith("/open/ufile/delete"):
            session.deleted_ids.append(kwargs["data"]["file_ids"])
            session.files["/Elsewhere/original.mkv"] = session.files.pop(SOURCE)
            session.files[SOURCE] = {"file_id": "replacement", **FINGERPRINT}
            return Response({"state": True, "code": 0})
        return original(method, url, **kwargs)

    session.request = request
    result = client.move_file_detailed(SOURCE, TARGET_DIR)
    assert result["state"] != "moved" and result["source_deleted"] is False
    assert session.files[SOURCE]["file_id"] == "replacement"
    assert session.files["/Elsewhere/original.mkv"]["file_id"] == "source"


def test_sdk_scan_rejects_nonempty_page_after_explicit_last_page():
    from telepiplex_download.client import Open115Error
    client, session = make_client()
    original = session.request

    def request(method, url, **kwargs):
        if url.endswith("/open/ufile/files"):
            offset = kwargs["params"]["offset"]
            return Response({"state": True, "code": 0, "data": [] if offset >= 2 else [
                {"file_id": "unexpected" if offset else "source", "fn": "extra.mkv" if offset else "Show.mkv", **FINGERPRINT}
            ], **({} if offset else {"has_more": False})})
        return original(method, url, **kwargs)

    session.request = request
    with pytest.raises(Open115Error):
        client.get_file_tree("/Downloads")


@pytest.mark.parametrize("second_page", [
    {"state": True, "code": 0, "data": [{"file_id": "extra", "fn": "extra.mkv", **FINGERPRINT}]},
    {"list": [], "data": {"list": [], "count": 2}},
])
def test_sdk_scan_keeps_count_from_every_equivalent_alias(second_page):
    from telepiplex_download.client import Open115Error
    client, session = make_client()
    original = session.request
    first = [{"file_id": "source", "fn": "Show.mkv", **FINGERPRINT}]

    def request(method, url, **kwargs):
        if url.endswith("/open/ufile/files"):
            offset = kwargs["params"]["offset"]
            return Response({"state": True, "code": 0, "list": first, "data": {"list": first, "count": 1}} if not offset else second_page if offset == 1 else {"state": True, "code": 0, "data": []})
        return original(method, url, **kwargs)

    session.request = request
    with pytest.raises(Open115Error):
        client.get_file_tree("/Downloads")


@pytest.mark.parametrize("payload", [None, [], {"state": False, "code": 50001, "data": []}, {"state": True, "code": 50001, "data": []}, {"state": True, "code": 0, "data": {}}])
def test_public_path_info_distinguishes_failed_lookup_from_successful_absence(payload):
    from telepiplex_download.client import Open115Error
    client, session = make_client()
    session.request = lambda *args, **kwargs: Response(payload)
    with pytest.raises(Open115Error):
        client.get_file_info(SOURCE)
