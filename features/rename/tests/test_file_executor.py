from pathlib import PurePosixPath

from telepiplex_rename.file_executor import (
    cleanup_empty_source_directories,
    cleanup_source_directories,
    execute_file_resolutions,
)
from telepiplex_rename.file_plan import FileResolution
from telepiplex_rename.operations import RenameOperationJournal


def _resolution(source_id, source, target, action, *, status="resolved"):
    return FileResolution(
        source_id=source_id,
        source_path=source,
        status=status,
        work_identity={"source": "tvdb", "external_id": "123"},
        item_identity={"season_number": 7, "episode_number": 1},
        target_path=target,
        action=action,
        reason_codes=(),
    )


class StatefulStorage:
    def __init__(self, files=(), directories=(), fail_moves=()):
        self.files = {
            path: {"file_id": source_id, "file_category": "1"}
            for path, source_id in files
        }
        self.directories = set(directories)
        self.fail_moves = set(fail_moves)
        self.renames = []
        self.moves = []
        self.deleted = []

    def get_file_info(self, path):
        if path in self.files:
            return dict(self.files[path])
        if path in self.directories:
            return {"file_id": f"dir:{path}", "file_category": "0"}
        return None

    def rename(self, source, new_name):
        self.renames.append((source, new_name))
        if source not in self.files:
            return False
        target = str(PurePosixPath(source).parent / new_name)
        self.files[target] = self.files.pop(source)
        return True

    def move_file_detailed(self, source, target_dir):
        self.moves.append((source, target_dir))
        target = str(PurePosixPath(target_dir) / PurePosixPath(source).name)
        if source in self.fail_moves or source not in self.files:
            return {
                "state": "copy_failed",
                "copied": False,
                "source_deleted": False,
                "source_path": source,
                "target_path": target,
            }
        self.directories.add(target_dir)
        self.files[target] = self.files.pop(source)
        return {
            "state": "moved",
            "copied": True,
            "source_deleted": True,
            "source_path": source,
            "target_path": target,
        }

    def get_file_list(self, params):
        directory = str(params["cid"])[4:]
        children = []
        for path, info in self.files.items():
            if str(PurePosixPath(path).parent) == directory:
                children.append({"fn": PurePosixPath(path).name, **info})
        for path in self.directories:
            if path != directory and str(PurePosixPath(path).parent) == directory:
                children.append({
                    "fn": PurePosixPath(path).name,
                    "file_id": f"dir:{path}",
                    "file_category": "0",
                })
        return children[: params.get("limit", 1000)]

    def delete_single_file(self, path):
        self.deleted.append(path)
        if path not in self.directories:
            return False
        self.directories.remove(path)
        return True


def test_veep_same_directory_is_rename_only_without_copy_or_delete():
    source = "/TV/Veep Season 07/Veep.S07E01.mkv"
    target = "/TV/Veep Season 07/Veep S07E01.mkv"
    storage = StatefulStorage(
        files=[(source, "veep-1")],
        directories=["/TV/Veep Season 07"],
    )

    summary = execute_file_resolutions(
        storage,
        [_resolution("veep-1", source, target, "rename_only")],
        selected_root="/TV/Veep Season 07",
    )

    assert summary.organized_files == 1
    assert summary.failed_files == 0
    assert storage.renames == [(source, "Veep S07E01.mkv")]
    assert storage.moves == []
    assert storage.deleted == []
    assert storage.files[target]["file_id"] == "veep-1"


def test_rename_and_move_stops_when_rename_already_reaches_final_target():
    source = "/TV/Veep/old.mkv"
    target = "/TV/Veep/Veep S07E01.mkv"
    storage = StatefulStorage(files=[(source, "veep-1")])

    summary = execute_file_resolutions(
        storage,
        [_resolution("veep-1", source, target, "rename_and_move")],
        selected_root="/TV/Veep",
    )

    assert summary.outcomes[0].state == "organized"
    assert storage.renames == [(source, "Veep S07E01.mkv")]
    assert storage.moves == []


def test_one_failed_move_does_not_stop_an_unrelated_file():
    first = "/Downloads/first.mkv"
    second = "/Downloads/second.mkv"
    storage = StatefulStorage(
        files=[(first, "first"), (second, "second")],
        fail_moves=[first],
    )
    resolutions = [
        _resolution("first", first, "/TV/A/first.mkv", "move_only"),
        _resolution("second", second, "/TV/B/second.mkv", "move_only"),
    ]

    summary = execute_file_resolutions(
        storage,
        resolutions,
        selected_root="/Downloads",
    )

    assert [(item.source_id, item.state) for item in summary.outcomes] == [
        ("first", "failed"),
        ("second", "organized"),
    ]
    assert summary.failed_files == 1
    assert summary.organized_files == 1
    assert first in storage.files
    assert "/TV/B/second.mkv" in storage.files


def test_provider_move_success_without_readable_target_is_not_verified():
    class MissingTargetStorage(StatefulStorage):
        def move_file_detailed(self, source, target_dir):
            self.moves.append((source, target_dir))
            self.files.pop(source, None)
            return {
                "state": "moved",
                "copied": True,
                "source_deleted": True,
                "source_path": source,
                "target_path": f"{target_dir}/{PurePosixPath(source).name}",
            }

    source = "/Downloads/episode.mkv"
    target = "/TV/Show/episode.mkv"
    storage = MissingTargetStorage(files=[(source, "episode")])

    summary = execute_file_resolutions(
        storage,
        [_resolution("episode", source, target, "move_only")],
        selected_root="/Downloads",
    )

    assert summary.organized_files == 0
    assert summary.failed_files == 1
    assert summary.outcomes[0].reason_codes == (
        "target_missing_after_move",
    )


def test_keep_and_no_op_resolutions_invoke_no_mutation():
    source = "/Downloads/unknown.srt"
    storage = StatefulStorage(files=[(source, "subtitle")])
    kept = _resolution(
        "subtitle", source, "", "keep_original", status="ambiguous"
    )
    no_op = _resolution("same", "/TV/same.mkv", "/TV/same.mkv", "no_op")
    storage.files["/TV/same.mkv"] = {
        "file_id": "same", "file_category": "1",
    }

    summary = execute_file_resolutions(
        storage,
        [kept, no_op],
        selected_root="/Downloads",
    )

    assert [(item.source_id, item.state) for item in summary.outcomes] == [
        ("subtitle", "kept"),
        ("same", "no_op"),
    ]
    assert storage.renames == []
    assert storage.moves == []
    assert storage.deleted == []


def test_replay_accepts_verified_target_identity_without_mutation():
    source = "/Downloads/old.mkv"
    target = "/TV/Veep/Veep S07E01.mkv"
    storage = StatefulStorage(files=[(target, "veep-1")])
    journal = RenameOperationJournal()

    summary = execute_file_resolutions(
        storage,
        [_resolution("veep-1", source, target, "rename_and_move")],
        selected_root="/Downloads",
        journal=journal,
    )

    assert summary.outcomes[0].state == "no_op"
    assert summary.canonical_no_ops == 1
    assert storage.renames == []
    assert storage.moves == []
    assert journal.file_transitions[-1]["stage"] == "verified"


def test_sixty_five_no_op_files_use_one_initial_batch_lookup():
    class BatchStorage(StatefulStorage):
        def __init__(self, files):
            super().__init__(files=files)
            self.batch_calls = []
            self.individual_calls = []

        def get_file_info_batch(self, paths):
            self.batch_calls.append(tuple(paths))
            return {
                path: dict(self.files[path]) if path in self.files else None
                for path in paths
            }

        def get_file_info(self, path):
            self.individual_calls.append(path)
            return super().get_file_info(path)

    paths = [f"/TV/Veep/Veep S07E{index:02d}.mkv" for index in range(1, 66)]
    storage = BatchStorage([(path, f"veep-{index}") for index, path in enumerate(paths)])
    resolutions = [
        _resolution(f"veep-{index}", path, path, "no_op")
        for index, path in enumerate(paths)
    ]

    summary = execute_file_resolutions(
        storage,
        resolutions,
        selected_root="/TV/Veep",
    )

    assert summary.canonical_no_ops == 65
    assert len(storage.batch_calls) == 1
    assert set(storage.batch_calls[0]) == set(paths)
    assert storage.individual_calls == []


def test_batch_snapshot_is_never_used_for_post_move_target_verification():
    class BatchStorage(StatefulStorage):
        def __init__(self, files):
            super().__init__(files=files)
            self.batch_calls = 0
            self.individual_calls = []

        def get_file_info_batch(self, paths):
            self.batch_calls += 1
            return {
                path: dict(self.files[path]) if path in self.files else None
                for path in paths
            }

        def get_file_info(self, path):
            self.individual_calls.append(path)
            return super().get_file_info(path)

    source = "/Downloads/Veep.S07E01.mkv"
    target = "/TV/Veep/Veep S07E01.mkv"
    storage = BatchStorage([(source, "veep-1")])

    summary = execute_file_resolutions(
        storage,
        [_resolution("veep-1", source, target, "rename_and_move")],
        selected_root="/Downloads",
    )

    assert summary.organized_files == 1
    assert storage.batch_calls == 1
    assert storage.individual_calls[-1] == target


def test_cleanup_deletes_only_freshly_verified_empty_directories_bottom_up():
    storage = StatefulStorage(
        files=[("/Downloads/Keep/unresolved.srt", "kept")],
        directories=[
            "/Downloads",
            "/Downloads/Release",
            "/Downloads/Release/Season",
            "/Downloads/Keep",
        ],
    )
    moved = _resolution(
        "moved",
        "/Downloads/Release/Season/episode.mkv",
        "/TV/Show/episode.mkv",
        "move_only",
    )
    kept = _resolution(
        "kept",
        "/Downloads/Keep/unresolved.srt",
        "",
        "keep_original",
        status="ambiguous",
    )

    deleted = cleanup_empty_source_directories(
        storage,
        [moved, kept],
        selected_root="/Downloads",
    )

    assert deleted == [
        "/Downloads/Release/Season",
        "/Downloads/Release",
    ]
    assert "/Downloads" not in storage.deleted
    assert "/Downloads/Keep" not in storage.deleted


def test_automatic_cleanup_deletes_empty_release_root_but_protects_category():
    storage = StatefulStorage(
        directories=[
            "/Downloads/House.Release",
            "/Series",
        ],
    )
    moved = _resolution(
        "episode",
        "/Downloads/House.Release/episode.mkv",
        "/Series/House/episode.mkv",
        "move_only",
    )

    summary = cleanup_source_directories(
        storage,
        [moved],
        selected_root="/Downloads/House.Release",
        include_selected_root=True,
        protected_roots=("/Series",),
    )

    assert summary.deleted_directories == 1
    assert summary.failed_directories == 0
    assert summary.complete is True
    assert storage.deleted == ["/Downloads/House.Release"]
    assert "/Series" in storage.directories


def test_manual_cleanup_preserves_user_scan_root():
    storage = StatefulStorage(directories=["/Series/UserSelected"])
    moved = _resolution(
        "episode",
        "/Series/UserSelected/episode.mkv",
        "/Series/Show/episode.mkv",
        "move_only",
    )

    summary = cleanup_source_directories(
        storage,
        [moved],
        selected_root="/Series/UserSelected",
        include_selected_root=False,
        protected_roots=("/Series/UserSelected",),
    )

    assert summary.deleted_directories == 0
    assert summary.failed_directories == 0
    assert storage.deleted == []


def test_cleanup_delete_failure_is_reported_not_hidden():
    class DeleteFailureStorage(StatefulStorage):
        def delete_single_file(self, path):
            self.deleted.append(path)
            return False

    storage = DeleteFailureStorage(directories=["/Downloads/Release"])
    moved = _resolution(
        "episode",
        "/Downloads/Release/episode.mkv",
        "/Series/Show/episode.mkv",
        "move_only",
    )

    summary = cleanup_source_directories(
        storage,
        [moved],
        selected_root="/Downloads/Release",
        include_selected_root=True,
    )

    assert summary.complete is False
    assert summary.failed_directories == 1
    assert summary.outcomes[0].state == "delete_failed"


def test_cleanup_replay_treats_already_absent_source_as_complete():
    storage = StatefulStorage()
    moved = _resolution(
        "episode",
        "/Downloads/AlreadyGone/episode.mkv",
        "/Series/Show/episode.mkv",
        "move_only",
    )

    summary = cleanup_source_directories(
        storage,
        [moved],
        selected_root="/Downloads/AlreadyGone",
        include_selected_root=True,
    )

    assert summary.complete is True
    assert summary.failed_directories == 0
    assert summary.outcomes[0].state == "already_absent"
