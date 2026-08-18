from telepiplex_download.client import Open115Client
import pytest


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
    client.get_file_list = lambda _params: {"list": [{
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
