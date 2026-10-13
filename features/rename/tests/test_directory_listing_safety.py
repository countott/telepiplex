from pathlib import Path, PurePosixPath

import pytest

from telepiplex_rename.file_executor import (
    _complete_directory_items,
    _fresh_directory_items,
    cleanup_source_directories,
    execute_file_resolutions,
)
from telepiplex_rename.file_plan import FileResolution


def _resolution():
    return FileResolution(
        "video", "/Downloads/Release/Show.mkv", "resolved",
        {"source": "tvdb", "external_id": "123"}, {},
        "/Series/Show/Show.mkv", "move_only", (),
    )


def _node(i):
    return {"file_id": str(i), "fn": f"{i}.mkv", "size": 1024, "sha1": f"sha-{i}"}


class ListingStorage:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.deleted = []
        self.exists = True

    def get_file_info(self, path):
        return {"file_id": "release", "file_category": "0"} if self.exists else None

    def get_file_list(self, params):
        self.calls.append(dict(params))
        return self.response(params) if callable(self.response) else self.response

    def delete_single_file(self, path):
        self.deleted.append(path)
        self.exists = False
        return True


@pytest.mark.parametrize("response", [
    {"list": [], "count": 1, "offset": 0},
    {"list": [], "state": False},
    {"data": {"list": [], "count": 1}},
    {"count": 0, "data": {"list": [], "count": 1}},
    {"offset": 0, "data": {"list": [], "offset": 1}},
    {"list": [], "has_more": "false"},
    {"list": [], "count": True},
    {"list": [], "count": -1},
    {"success": False, "data": {"list": []}},
    {"code": 50001, "list": []},
    {"state": True, "data": {"code": 50001, "list": []}},
])
def test_contradictory_empty_directory_never_deletes(response):
    storage = ListingStorage(response)
    summary = cleanup_source_directories(
        storage, [_resolution()], selected_root="/Downloads/Release",
        include_selected_root=True,
    )
    assert storage.deleted == []
    assert not summary.complete
    assert summary.outcomes[0].state == "lookup_failed"


@pytest.mark.parametrize("reader", [_complete_directory_items, _fresh_directory_items])
@pytest.mark.parametrize("response", [
    {"list": [_node(1)], "count": 2, "has_more": False},
    {"count": 1, "data": {"count": 2, "list": [_node(1)]}},
    {"offset": 0, "data": {"offset": 1, "list": [_node(1)]}},
    {"state": False, "list": [_node(1)]},
    {"code": 50001, "list": [_node(1)]},
    {"has_more": False, "data": {"has_more": True, "list": [_node(1)]}},
])
def test_every_directory_reader_rejects_incomplete_or_contradictory_pages(reader, response):
    storage = ListingStorage(response)
    assert reader(storage, "directory") is None


@pytest.mark.parametrize("reader", [_complete_directory_items, _fresh_directory_items])
def test_list_only_short_pages_are_read_until_empty_tail(reader):
    rows = [_node(i) for i in range(300)]
    storage = ListingStorage(lambda params: rows[params["offset"]:params["offset"] + 137])
    assert reader(storage, "directory") == rows
    assert [call["offset"] for call in storage.calls] == [0, 137, 274, 300]


@pytest.mark.parametrize("reader", [_complete_directory_items, _fresh_directory_items])
def test_cross_page_overlap_cannot_be_complete(reader):
    storage = ListingStorage(lambda params: {
        "list": [_node(1), _node(2)] if params["offset"] == 0 else [_node(2), _node(3)],
        "has_more": params["offset"] == 0,
    })
    assert reader(storage, "directory") is None


@pytest.mark.parametrize("reader", [_complete_directory_items, _fresh_directory_items])
def test_count_changes_during_scan_are_rejected(reader):
    storage = ListingStorage(lambda params: {
        "list": [_node(1)] if params["offset"] == 0 else [_node(2)],
        "count": 2 if params["offset"] == 0 else 3,
        "offset": params["offset"],
        "has_more": params["offset"] == 0,
    })
    assert reader(storage, "directory") is None


@pytest.mark.parametrize("reader", [_complete_directory_items, _fresh_directory_items])
def test_underreported_count_does_not_hide_an_unexpected_next_page(reader):
    storage = ListingStorage(lambda params: {
        "list": [_node(params["offset"] + 1)], "count": 1, "offset": params["offset"],
    })
    assert reader(storage, "directory") is None
    assert [call["offset"] for call in storage.calls] == [0, 1]


def test_real_115_client_preserves_outer_count_before_directory_cleanup(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "download" / "src"))
    from telepiplex_download.client import Open115Client

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

    class Session:
        deleted = False
        delete_calls = []

        def request(self, method, url, **kwargs):
            if url.endswith("/open/folder/get_info"):
                assert method == "POST" and kwargs["data"]["path"] == "/Downloads/Release"
                return Response({"state": True, "code": 0, "data": [] if self.deleted else [{"file_id": "release", "file_category": "0"}]})
            if url.endswith("/open/ufile/files"):
                return Response({"state": True, "code": 0, "data": [], "count": 1, "offset": 0})
            assert url.endswith("/open/ufile/delete")
            self.delete_calls.append(kwargs["data"]["file_ids"])
            self.deleted = True
            return Response({"state": True, "code": 0})

    session = Session()
    client = Open115Client({"access_token": "test", "request_interval": 0}, session=session, pacer=NoWait())
    summary = cleanup_source_directories(client, [_resolution()], selected_root="/Downloads/Release", include_selected_root=True)
    assert session.delete_calls == []
    assert not summary.complete and summary.outcomes[0].state == "lookup_failed"


def test_real_client_directory_cleanup_binds_listed_id_despite_cache_invalidation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "download" / "src"))
    from telepiplex_download.client import Open115Client

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

    class Session:
        replacement = False
        deleted_ids = []

        def request(self, method, url, **kwargs):
            if url.endswith("/open/folder/get_info"):
                provider_id = "replacement" if self.replacement else "listed-original"
                value = [] if self.deleted_ids else [{"file_id": provider_id, "file_category": "0"}]
                return Response({"state": True, "code": 0, "data": value})
            if url.endswith("/open/ufile/files"):
                assert kwargs["params"]["cid"] == "listed-original"
                self.replacement = True
                # A successful unrelated mutation invalidates the provider's
                # path cache while the organizer still holds the old identity.
                client._request("POST", "/open/folder/add", data={"pid": "elsewhere", "file_name": "unrelated"})
                return Response({"state": True, "code": 0, "data": [], "count": 0, "offset": 0})
            if url.endswith("/open/ufile/delete"):
                self.deleted_ids.append(kwargs["data"]["file_ids"])
            return Response({"state": True, "code": 0})

    session = Session()
    client = Open115Client({"access_token": "test"}, session=session, pacer=NoWait())
    summary = cleanup_source_directories(client, [_resolution()], selected_root="/Downloads/Release", include_selected_root=True)
    assert session.deleted_ids == []
    assert not summary.complete and summary.failed_directories == 1


def test_duplicate_copy_recovery_binds_source_id_when_preflight_path_was_replaced(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "download" / "src"))
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "download" / "tests"))
    from test_legacy_move_verification import make_client, SOURCE, TARGET, FINGERPRINT
    from telepiplex_rename.file_executor import FileTransactionSnapshot

    client, session = make_client()
    source_info = dict(session.files[SOURCE])
    session.files[TARGET] = {"file_id": "copy", **FINGERPRINT}
    resolution = FileResolution("source", SOURCE, "resolved", {"source": "tvdb", "external_id": "123"}, {},
                                TARGET, "recover_duplicate_copy", (), {"sha1": FINGERPRINT["sha1"], "size": 1024})
    snapshot = FileTransactionSnapshot.from_provider_facts(
        {SOURCE: source_info, TARGET: session.files[TARGET], "/Downloads": session.files["/Downloads"]},
        {"/Downloads": "downloads"},
    )
    session.files[SOURCE] = {"file_id": "replacement", **FINGERPRINT}
    result = execute_file_resolutions(client, [resolution], selected_root="/Downloads", preflight=snapshot)
    assert result.failed_files == 1 and session.deleted_ids == []
    assert session.files[SOURCE]["file_id"] == "replacement"


def test_old_provider_without_identity_bound_delete_is_retained_fail_closed():
    storage = ListingStorage({"list": [], "count": 0, "offset": 0})
    summary = cleanup_source_directories(storage, [_resolution()], selected_root="/Downloads/Release", include_selected_root=True)
    assert storage.deleted == [] and not summary.complete


@pytest.mark.parametrize("count", [1, 31, 32, 33, 99, 100, 101, 1000])
def test_default_batch_is_100_but_explicit_32_is_preserved(count):
    from tests.test_file_executor import _FreshGateStorage, _native_snapshot_from_storage, _resolution as make

    for batch in (None, 32):
        effective_batch = batch or 100
        expected = [min(effective_batch, count - offset) for offset in range(0, count, effective_batch)]
        files = [(f"/Downloads/{i}.mkv", str(i)) for i in range(count)]
        storage = _FreshGateStorage(files=files, directories=["/Downloads", "/Series/Show"])
        resolutions = [make(i, source, "/Series/Show/" + PurePosixPath(source).name, "move_only") for source, i in files]
        kwargs = {} if batch is None else {"move_batch_size": batch}
        result = execute_file_resolutions(
            storage, resolutions, selected_root="/Downloads",
            preflight=_native_snapshot_from_storage(storage, *resolutions), **kwargs,
        )
        assert result.organized_files == count and result.failed_files == 0
        assert [len(ids) for ids, _ in storage.native_calls] == expected
        assert {path: info["file_id"] for path, info in storage.files.items()} == {
            "/Series/Show/" + PurePosixPath(source).name: source_id for source, source_id in files
        }


def test_default_100_partial_provider_failure_is_reconciled_without_resubmitting():
    from tests.test_file_executor import _FreshGateStorage, _native_gate_snapshot, _resolution as make

    class PartialStorage(_FreshGateStorage):
        def move_files_by_id(self, ids, target):
            super().move_files_by_id(ids[:40], target)
            return {"state": "provider_rejected"}

    files = [(f"/Downloads/{i}.mkv", str(i)) for i in range(101)]
    storage = PartialStorage(files=files, directories=["/Downloads", "/Series/Show"])
    resolutions = [make(i, source, "/Series/Show/" + PurePosixPath(source).name, "move_only") for source, i in files]
    result = execute_file_resolutions(
        storage, resolutions, selected_root="/Downloads", preflight=_native_gate_snapshot(*resolutions),
    )
    assert result.organized_files == 41 and result.failed_files == 60
    assert len(storage.native_calls) == 2
    assert sum(path.startswith("/Downloads/") for path in storage.files) == 60
    assert len(storage.files) == 101


def test_cancel_after_accepted_100_batch_recovers_without_repeating_that_batch():
    from tests.test_file_executor import _FreshGateStorage, _native_gate_snapshot, _resolution as make
    from telepiplex_rename.operations import OperationCancelled

    class CancellableStorage(_FreshGateStorage):
        stopped = False
        stop_after_move = True

        def get_file_list(self, params):
            if self.stopped:
                raise OperationCancelled()
            return super().get_file_list(params)

        def move_files_by_id(self, ids, target):
            result = super().move_files_by_id(ids, target)
            self.stopped = self.stop_after_move
            return result

    files = [(f"/Downloads/{i}.mkv", str(i)) for i in range(101)]
    storage = CancellableStorage(files=files, directories=["/Downloads", "/Series/Show"])
    resolutions = [make(i, source, "/Series/Show/" + PurePosixPath(source).name, "move_only") for source, i in files]
    preflight = _native_gate_snapshot(*resolutions)
    with pytest.raises(OperationCancelled):
        execute_file_resolutions(storage, resolutions, selected_root="/Downloads", preflight=preflight)
    assert [len(ids) for ids, _ in storage.native_calls] == [100]
    assert sum(path.startswith("/Downloads/") for path in storage.files) == 1
    storage.stopped = storage.stop_after_move = False
    recovered = execute_file_resolutions(storage, resolutions, selected_root="/Downloads", preflight=preflight)
    assert recovered.organized_files == 101 and recovered.failed_files == 0
    assert [len(ids) for ids, _ in storage.native_calls] == [100, 1]
