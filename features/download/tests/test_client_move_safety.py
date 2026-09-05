from telepiplex_download.client import Open115Client, Open115Error
import pytest
import requests
import threading


class RecordingOpen115Client(Open115Client):
    def __init__(self):
        super().__init__({"access_token": "test", "request_interval": 0})
        self.mutations = []

    def create_dir_recursive(self, path):
        self.mutations.append(("create_dir_recursive", path))
        return {"file_id": "target"}

    def copy_file(self, source_path, target_path):
        self.mutations.append(("copy_file", source_path, target_path))
        return True

    def delete_single_file(self, path):
        self.mutations.append(("delete_single_file", path))
        return True


def test_move_file_detailed_treats_effective_same_path_as_no_op():
    client = RecordingOpen115Client()
    source = "/TV/Veep Season 07/Veep S07E01.mkv"

    result = client.move_file_detailed(source, "/TV/Veep Season 07/")

    assert result == {
        "state": "no_op",
        "copied": False,
        "source_deleted": False,
        "source_path": source,
        "target_path": source,
    }
    assert client.mutations == []


def test_move_file_reports_same_path_no_op_as_success():
    client = RecordingOpen115Client()

    moved = client.move_file(
        "/TV/Veep Season 07/Veep S07E01.mkv",
        "/TV/Veep Season 07",
    )

    assert moved is True
    assert client.mutations == []


def test_native_batch_move_uses_official_endpoint_without_copy_delete():
    class NativeMoveClient(RecordingOpen115Client):
        def _request(
            self, method, path, *, params=None, data=None, files=None, retry=True
        ):
            self.mutations.append(("request", method, path, data, files))
            return {"state": True, "code": 0, "data": {}}

    client = NativeMoveClient()

    result = client.move_files_by_id(
        ["episode-1", "episode-2", "episode-1"],
        "season-1",
    )

    assert result == {
        "state": "submitted",
        "submitted": True,
        "file_ids": ["episode-1", "episode-2"],
        "target_dir_id": "season-1",
        "provider_code": "0",
    }
    assert client.mutations == [(
        "request",
        "POST",
        "/open/ufile/move",
        None,
        {
            "file_ids": (None, "episode-1,episode-2"),
            "to_cid": (None, "season-1"),
        },
    )]


def test_native_batch_move_rejects_empty_or_oversized_ids():
    client = RecordingOpen115Client()

    with pytest.raises(ValueError, match="1 through 100"):
        client.move_files_by_id([], "season-1")
    with pytest.raises(ValueError, match="1 through 100"):
        client.move_files_by_id(
            [f"episode-{index}" for index in range(101)],
            "season-1",
        )
    with pytest.raises(ValueError, match="target directory"):
        client.move_files_by_id(["episode-1"], "")


def test_file_tree_preserves_provider_sha1_for_identity_recovery():
    client = Open115Client({"access_token": "test", "request_interval": 0})
    client.get_file_info = lambda _path: {
        "file_id": "root",
        "file_category": "0",
    }
    client.get_file_list = lambda _params: {"list": []} if _params.get("offset", 0) else {"list": [{
        "fn": "Veep.S07E01.mkv",
        "fid": "episode-1",
        "fc": "1",
        "fs": 4096,
        "sha1": "ABCDEF0123456789",
    }]}

    tree = client.get_file_tree("/Downloads/Veep")

    assert tree == [{
        "name": "Veep.S07E01.mkv",
        "relative_path": "Veep.S07E01.mkv",
        "path": "/Downloads/Veep/Veep.S07E01.mkv",
        "is_dir": False,
        "file_id": "episode-1",
        "size": 4096,
        "sha1": "ABCDEF0123456789",
    }]


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200
        self.headers = {}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _NoWaitPacer:
    def acquire(self, _endpoint_class):
        return 0.0

    def observe_throttle(self, _endpoint_class, _retry_after):
        return 0.0


def test_file_info_batch_normalizes_deduplicates_orders_and_isolates_failures():
    class PartialSession:
        def __init__(self):
            self.reads = []
            self.path_counts = {}

        def request(self, method, _url, **kwargs):
            assert method == "GET"
            path = kwargs["params"]["path"]
            self.reads.append(path)
            self.path_counts[path] = self.path_counts.get(path, 0) + 1
            if path == "/raises":
                raise requests.ReadTimeout("provider timeout")
            if path == "/rejected":
                return _Response({"state": False, "code": 50001})
            if path == "/malformed":
                return _Response({"state": True, "code": 0, "data": []})
            if path == "/empty" and self.path_counts[path] == 1:
                return _Response({"state": True, "code": 0, "data": {}})
            if path == "/empty-id" and self.path_counts[path] == 1:
                return _Response({
                    "state": True,
                    "code": 0,
                    "data": {"file_id": "", "name": "partial"},
                })
            if path == "/partial" and self.path_counts[path] == 1:
                return _Response({
                    "state": True,
                    "code": 0,
                    "data": {"name": "identity missing"},
                })
            if path == "/fid":
                return _Response({
                    "state": True,
                    "code": 0,
                    "data": {"fid": "fid-1"},
                })
            if path == "/cid":
                return _Response({
                    "state": True,
                    "code": 0,
                    "data": {"cid": 42},
                })
            if path == "/id":
                return _Response({
                    "state": True,
                    "code": 0,
                    "data": {"id": "generic-1"},
                })
            return _Response({
                "state": True,
                "code": 0,
                "data": {"file_id": f"id:{path}"},
            })

    session = PartialSession()
    client = Open115Client(
        {"access_token": "access", "storage_read_workers": 4},
        session=session,
        pacer=_NoWaitPacer(),
    )

    result = client.get_file_info_batch([
        "good",
        "/good/",
        "/rejected",
        "/malformed",
        "/raises",
        "/empty",
        "/empty-id",
        "/partial",
        "/fid",
        "/cid",
        "/id",
    ])

    assert list(result) == [
        "/good",
        "/rejected",
        "/malformed",
        "/raises",
        "/empty",
        "/empty-id",
        "/partial",
        "/fid",
        "/cid",
        "/id",
    ]
    assert result == {
        "/good": {"file_id": "id:/good"},
        "/rejected": None,
        "/malformed": None,
        "/raises": None,
        "/empty": None,
        "/empty-id": None,
        "/partial": None,
        "/fid": {"fid": "fid-1"},
        "/cid": {"cid": 42},
        "/id": {"id": "generic-1"},
    }
    assert sorted(session.reads) == sorted(result)

    assert client.get_file_info("/empty") == {"file_id": "id:/empty"}
    assert client.get_file_info("/empty-id") == {"file_id": "id:/empty-id"}
    assert client.get_file_info("/partial") == {"file_id": "id:/partial"}
    assert session.path_counts["/empty"] == 2
    assert session.path_counts["/empty-id"] == 2
    assert session.path_counts["/partial"] == 2


def test_file_info_batch_rejects_more_than_32_inputs_before_provider_work():
    client = Open115Client(
        {"access_token": "access"},
        session=object(),
        pacer=_NoWaitPacer(),
    )

    with pytest.raises(ValueError, match="32"):
        client.get_file_info_batch([f"/{index}" for index in range(33)])


def test_storage_mutation_generation_prevents_late_read_from_refilling_cache():
    class GenerationSession:
        def __init__(self):
            self.read_started = threading.Event()
            self.release_read = threading.Event()
            self.read_count = 0

        def request(self, method, url, **kwargs):
            if method == "POST":
                return _Response({"state": True, "code": 0, "data": {}})
            assert url.endswith("/open/folder/get_info")
            self.read_count += 1
            if self.read_count == 1:
                self.read_started.set()
                assert self.release_read.wait(1)
                file_id = "before-mutation"
            else:
                file_id = "after-mutation"
            return _Response({
                "state": True,
                "code": 0,
                "data": {"file_id": file_id},
            })

    session = GenerationSession()
    client = Open115Client(
        {"access_token": "access"},
        session=session,
        pacer=_NoWaitPacer(),
    )
    first = {}
    worker = threading.Thread(
        target=lambda: first.setdefault("value", client.get_file_info("/episode.mkv"))
    )
    worker.start()
    assert session.read_started.wait(1)

    moved = client.move_files_by_id(["episode-1"], "season-1")
    session.release_read.set()
    worker.join(1)

    assert moved["submitted"] is True
    assert first["value"]["file_id"] == "before-mutation"
    assert client.get_file_info("/episode.mkv")["file_id"] == "after-mutation"
    assert session.read_count == 2


def test_committed_storage_mutation_blocks_stale_cache_hits_until_linearized():
    class BlockingResponse(_Response):
        def __init__(self, payload, release):
            super().__init__(payload)
            self.release = release

        def json(self):
            assert self.release.wait(1)
            return self.payload

    class CommitWindowSession:
        def __init__(self):
            self.committed = threading.Event()
            self.release_response = threading.Event()
            self.read_count = 0

        def request(self, method, _url, **kwargs):
            if method == "POST":
                self.committed.set()
                return BlockingResponse(
                    {"state": True, "code": 0, "data": {}},
                    self.release_response,
                )
            self.read_count += 1
            file_id = "stale-before-commit" if self.read_count == 1 else "fresh-after-commit"
            return _Response({
                "state": True,
                "code": 0,
                "data": {"file_id": file_id},
            })

    session = CommitWindowSession()
    client = Open115Client(
        {"access_token": "access"},
        session=session,
        pacer=_NoWaitPacer(),
    )
    assert client.get_file_info("/episode.mkv") == {
        "file_id": "stale-before-commit"
    }
    mutation_result = {}
    mutation = threading.Thread(
        target=lambda: mutation_result.setdefault(
            "value", client.move_files_by_id(["episode-1"], "season-1")
        )
    )
    mutation.start()
    assert session.committed.wait(1)

    read_started = threading.Event()
    read_finished = threading.Event()
    read_result = {}

    def read_same_path():
        read_started.set()
        read_result["value"] = client.get_file_info("/episode.mkv")
        read_finished.set()

    reader = threading.Thread(target=read_same_path)
    reader.start()
    assert read_started.wait(1)
    try:
        assert not read_finished.wait(0.1), (
            "cache returned a stale fact after provider commit but before "
            "mutation linearization"
        )
    finally:
        session.release_response.set()
        mutation.join(1)
        reader.join(1)

    assert not mutation.is_alive()
    assert not reader.is_alive()
    assert mutation_result["value"]["submitted"] is True
    assert read_result["value"] == {"file_id": "fresh-after-commit"}
    assert session.read_count == 2


def test_rejected_storage_mutation_releases_readers_without_invalidating_cache():
    class RejectedSession:
        def __init__(self):
            self.read_count = 0

        def request(self, method, _url, **kwargs):
            if method == "POST":
                return _Response({"state": False, "code": 50001})
            self.read_count += 1
            return _Response({
                "state": True,
                "code": 0,
                "data": {"file_id": "unchanged"},
            })

    session = RejectedSession()
    client = Open115Client(
        {"access_token": "access"},
        session=session,
        pacer=_NoWaitPacer(),
    )
    cached = client.get_file_info("/episode.mkv")

    moved = client.move_files_by_id(["episode-1"], "season-1")

    assert moved["submitted"] is False
    assert client.get_file_info("/episode.mkv") is cached
    assert session.read_count == 1


def test_storage_mutation_exception_releases_readers_without_invalidating_cache():
    class RaisingSession:
        def __init__(self):
            self.read_count = 0

        def request(self, method, _url, **kwargs):
            if method == "POST":
                raise requests.ReadTimeout("mutation response lost")
            self.read_count += 1
            return _Response({
                "state": True,
                "code": 0,
                "data": {"file_id": "unchanged"},
            })

    session = RaisingSession()
    client = Open115Client(
        {"access_token": "access"},
        session=session,
        pacer=_NoWaitPacer(),
    )
    cached = client.get_file_info("/episode.mkv")

    with pytest.raises(Open115Error, match="ReadTimeout"):
        client.move_files_by_id(["episode-1"], "season-1")

    assert client.get_file_info("/episode.mkv") is cached
    assert session.read_count == 1
