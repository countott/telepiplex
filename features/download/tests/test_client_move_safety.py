from telepiplex_download.client import Open115Client


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
