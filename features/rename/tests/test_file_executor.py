from pathlib import PurePosixPath
import pytest

import telepiplex_rename.file_executor as file_executor

from telepiplex_rename.file_executor import (
    cleanup_empty_source_directories,
    cleanup_source_directories,
    execute_file_resolutions,
)
from telepiplex_rename.file_plan import FileResolution
from telepiplex_rename.operations import RenameOperationJournal


def _resolution(
    source_id,
    source,
    target,
    action,
    *,
    status="resolved",
    source_fingerprint=None,
):
    return FileResolution(
        source_id=source_id,
        source_path=source,
        status=status,
        work_identity={"source": "tvdb", "external_id": "123"},
        item_identity={"season_number": 7, "episode_number": 1},
        target_path=target,
        action=action,
        reason_codes=(),
        source_fingerprint=dict(source_fingerprint or {}),
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
    storage = StatefulStorage(
        files=[(source, "veep-1")],
        directories=["/TV/Veep"],
    )

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
        directories=["/Downloads"],
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
    storage = MissingTargetStorage(
        files=[(source, "episode")],
        directories=["/Downloads"],
    )

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


def test_move_claiming_source_deleted_fails_when_source_is_still_present():
    class RetainedSourceStorage(StatefulStorage):
        def move_file_detailed(self, source, target_dir):
            self.moves.append((source, target_dir))
            target = str(PurePosixPath(target_dir) / PurePosixPath(source).name)
            self.directories.add(target_dir)
            self.files[target] = dict(self.files[source])
            return {
                "state": "moved",
                "copied": True,
                "source_deleted": True,
                "source_path": source,
                "target_path": target,
            }

    source = "/Downloads/episode.mkv"
    target = "/TV/Show/episode.mkv"
    storage = RetainedSourceStorage(
        files=[(source, "episode")],
        directories=["/Downloads"],
    )

    summary = execute_file_resolutions(
        storage,
        [_resolution("episode", source, target, "move_only")],
        selected_root="/Downloads",
    )

    assert summary.failed_files == 1
    assert summary.outcomes[0].reason_codes == (
        "source_still_present_after_move",
    )


def test_same_hash_target_recovers_interrupted_copy_by_deleting_source():
    class RecoverableStorage(StatefulStorage):
        def __init__(self):
            super().__init__(directories=["/Downloads"])
            self.files = {
                "/Downloads/episode.mkv": {
                    "file_id": "source",
                    "file_category": "1",
                    "sha1": "same-hash",
                    "size": 4096,
                },
                "/TV/Show/episode.mkv": {
                    "file_id": "copied-target",
                    "file_category": "1",
                    "sha1": "same-hash",
                    "size": 4096,
                },
            }

        def delete_single_file(self, path):
            self.deleted.append(path)
            return self.files.pop(path, None) is not None

    storage = RecoverableStorage()
    resolution = _resolution(
        "source",
        "/Downloads/episode.mkv",
        "/TV/Show/episode.mkv",
        "recover_duplicate_copy",
        source_fingerprint={"sha1": "same-hash", "size": 4096},
    )

    summary = execute_file_resolutions(
        storage,
        [resolution],
        selected_root="/Downloads",
    )

    assert summary.organized_files == 1
    assert summary.failed_files == 0
    assert storage.deleted == ["/Downloads/episode.mkv"]
    assert "/Downloads/episode.mkv" not in storage.files
    assert "/TV/Show/episode.mkv" in storage.files


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


def test_sixty_five_no_op_files_with_unverifiable_parent_fall_back_to_exact_batch():
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
    assert storage.batch_calls[0] == ("/TV/Veep",)
    assert set(storage.batch_calls[1]) == {*paths, "/TV/Veep"}
    assert storage.individual_calls == []


def test_sixty_five_moves_use_three_native_batches_and_no_legacy_moves():
    class NativeBatchStorage(StatefulStorage):
        def __init__(self, files):
            super().__init__(files=files, directories=["/Downloads", "/TV"])
            self.native_calls = []

        def create_dir_recursive(self, path):
            self.directories.add(path)
            return {"file_id": f"dir:{path}", "file_category": "0"}

        def move_files_by_id(self, file_ids, target_dir_id):
            self.native_calls.append((tuple(file_ids), target_dir_id))
            target_dir = str(target_dir_id)[4:]
            for file_id in file_ids:
                source = next(
                    path for path, info in self.files.items()
                    if info["file_id"] == file_id
                )
                target = str(PurePosixPath(target_dir) / PurePosixPath(source).name)
                self.files[target] = self.files.pop(source)
            return {"state": "submitted", "submitted": True}

    sources = [
        f"/Downloads/episode-{index:03d}.mkv"
        for index in range(65)
    ]
    storage = NativeBatchStorage([
        (source, f"episode-{index}")
        for index, source in enumerate(sources)
    ])
    resolutions = [
        _resolution(
            f"episode-{index}",
            source,
            f"/TV/Show/{PurePosixPath(source).name}",
            "move_only",
        )
        for index, source in enumerate(sources)
    ]

    summary = execute_file_resolutions(
        storage,
        resolutions,
        selected_root="/Downloads",
        move_batch_size=32,
    )

    assert summary.organized_files == 65
    assert summary.failed_files == 0
    assert [len(call[0]) for call in storage.native_calls] == [32, 32, 1]
    assert {call[1] for call in storage.native_calls} == {"dir:/TV/Show"}
    assert storage.moves == []


def test_native_move_reconciles_observed_state_instead_of_response_boolean():
    class ReconciledStorage(StatefulStorage):
        def __init__(self, *, apply_move):
            super().__init__(
                files=[("/Downloads/episode.mkv", "episode")],
                directories=["/Downloads", "/TV"],
            )
            self.apply_move = apply_move

        def create_dir_recursive(self, path):
            self.directories.add(path)
            return {"file_id": f"dir:{path}", "file_category": "0"}

        def move_files_by_id(self, file_ids, target_dir_id):
            if self.apply_move:
                self.files["/TV/Show/episode.mkv"] = self.files.pop(
                    "/Downloads/episode.mkv"
                )
            return {"state": "provider_rejected", "submitted": False}

    resolution = _resolution(
        "episode",
        "/Downloads/episode.mkv",
        "/TV/Show/episode.mkv",
        "move_only",
    )

    applied = execute_file_resolutions(
        ReconciledStorage(apply_move=True),
        [resolution],
        selected_root="/Downloads",
    )
    not_applied = execute_file_resolutions(
        ReconciledStorage(apply_move=False),
        [resolution],
        selected_root="/Downloads",
    )

    assert applied.outcomes[0].state == "organized"
    assert not_applied.outcomes[0].state == "failed"
    assert not_applied.outcomes[0].reason_codes == (
        "target_missing_after_move",
    )


def test_incompatible_native_provider_falls_back_to_legacy_move():
    class LegacyFallbackStorage(StatefulStorage):
        def __init__(self):
            super().__init__(
                files=[("/Downloads/episode.mkv", "episode")],
                directories=["/Downloads", "/TV"],
            )

        def create_dir_recursive(self, path):
            self.directories.add(path)
            return {"file_id": f"dir:{path}", "file_category": "0"}

        def move_files_by_id(self, _file_ids, _target_dir_id):
            raise AttributeError("native move unavailable")

    storage = LegacyFallbackStorage()
    summary = execute_file_resolutions(
        storage,
        [_resolution(
            "episode",
            "/Downloads/episode.mkv",
            "/TV/Show/episode.mkv",
            "move_only",
        )],
        selected_root="/Downloads",
    )

    assert summary.outcomes[0].state == "organized"
    assert storage.moves == [("/Downloads/episode.mkv", "/TV/Show")]


def test_batch_snapshot_is_never_used_for_post_move_target_verification():
    class BatchStorage(StatefulStorage):
        def __init__(self, files):
            super().__init__(files=files)
            self.batch_calls = 0
            self.individual_calls = []

        def get_file_info_batch(self, paths):
            self.batch_calls += 1
            return {
                path: StatefulStorage.get_file_info(self, path)
                for path in paths
            }

        def get_file_info(self, path):
            self.individual_calls.append(path)
            return super().get_file_info(path)

    source = "/Downloads/Veep.S07E01.mkv"
    target = "/TV/Veep/Veep S07E01.mkv"
    storage = BatchStorage([(source, "veep-1")])
    storage.directories.add("/Downloads")

    summary = execute_file_resolutions(
        storage,
        [_resolution("veep-1", source, target, "rename_and_move")],
        selected_root="/Downloads",
    )

    assert summary.organized_files == 1
    assert storage.batch_calls == 1
    assert target in storage.individual_calls
    assert storage.individual_calls[-1] == "/Downloads/Veep S07E01.mkv"


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


def test_cleanup_treats_directory_retained_for_unresolved_file_as_complete():
    root = "/Downloads/Honey"
    unresolved = f"{root}/Honey.S01E25.mkv"
    storage = StatefulStorage(
        files=[(unresolved, "episode-25")],
        directories=[root],
    )
    kept = _resolution(
        "episode-25",
        unresolved,
        "",
        "keep_original",
        status="ambiguous",
    )

    summary = cleanup_source_directories(
        storage,
        [kept],
        selected_root=root,
        include_selected_root=True,
    )

    assert summary.complete is True
    assert summary.retained_directories == 1
    assert summary.outcomes[0].state == "retained_unresolved"
    assert storage.deleted == []


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


def test_manual_cleanup_deletes_empty_selected_work_group_root():
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
        include_selected_root=True,
        protected_roots=("/Series",),
    )

    assert summary.deleted_directories == 1
    assert summary.failed_directories == 0
    assert summary.complete is True
    assert storage.deleted == ["/Series/UserSelected"]


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


def test_cleanup_requires_directory_absence_after_provider_delete_success():
    class StickyDirectoryStorage(StatefulStorage):
        def delete_single_file(self, path):
            self.deleted.append(path)
            return True

    storage = StickyDirectoryStorage(directories=["/Downloads/Release"])
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
    assert summary.outcomes[0].reason_code == "directory_still_present"


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


def test_file_transaction_snapshot_is_deeply_immutable_and_normalized():
    facts = {
        "/Downloads/./Release/episode.mkv": {
            "file_id": "episode",
            "sha1": "ABCDEF",
            "size": "4096",
            "mutable": {"ignored": True},
        },
        "/Series/Show/episode.mkv": None,
        "/Downloads/Release": {"file_id": "source-dir"},
    }
    parents = {"/Downloads/Release/.": "source-dir"}

    snapshot = file_executor.FileTransactionSnapshot.from_provider_facts(
        facts,
        parents,
    )
    facts["/Downloads/./Release/episode.mkv"]["file_id"] = "changed"
    parents["/Downloads/Release/."] = "changed-dir"

    assert snapshot.file_info["/Downloads/Release/episode.mkv"] == (
        file_executor.PreflightFileInfo("episode", "abcdef", 4096)
    )
    assert snapshot.file_info["/Series/Show/episode.mkv"] is None
    assert snapshot.source_parent_ids["/Downloads/Release"] == "source-dir"
    with pytest.raises(TypeError):
        snapshot.file_info["/Downloads/Release/episode.mkv"] = None
    with pytest.raises(TypeError):
        snapshot.source_parent_ids["/Downloads/Release"] = "other"
    with pytest.raises(KeyError):
        snapshot.require_file_info("/never/queried.mkv")


def test_snapshot_uses_size_byte_when_provider_also_returns_human_size():
    snapshot = file_executor.FileTransactionSnapshot.from_provider_facts(
        {
            "/Downloads/episode.mkv": {
                "file_id": "episode",
                "sha1": "ABCDEF",
                "size": "11.57GB",
                "size_byte": 12_419_471_418,
            },
        },
        {},
    )

    assert snapshot.file_info["/Downloads/episode.mkv"] == (
        file_executor.PreflightFileInfo(
            "episode",
            "abcdef",
            12_419_471_418,
        )
    )


def test_native_snapshot_uses_captured_parent_id_without_parent_reread():
    class NativeStorage(StatefulStorage):
        def __init__(self):
            super().__init__(
                files=[("/Downloads/episode.mkv", "episode")],
                directories=["/Downloads", "/Series"],
            )
            self.info_reads = []

        def get_file_info(self, path):
            self.info_reads.append(path)
            return super().get_file_info(path)

        def create_dir_recursive(self, path):
            self.directories.add(path)
            return {"file_id": f"dir:{path}", "file_category": "0"}

        def move_files_by_id(self, file_ids, target_dir_id):
            assert file_ids == ["episode"]
            assert target_dir_id == "dir:/Series/Show"
            self.files["/Series/Show/episode.mkv"] = self.files.pop(
                "/Downloads/episode.mkv"
            )
            return {"state": "submitted"}

    storage = NativeStorage()
    snapshot = file_executor.FileTransactionSnapshot.from_provider_facts(
        {
                "/Downloads/episode.mkv": {"file_id": "episode"},
                "/Series/Show/episode.mkv": None,
                "/Downloads": {"file_id": "dir:/Downloads"},
            },
            {"/Downloads": "dir:/Downloads"},
        )

    summary = execute_file_resolutions(
        storage,
        [_resolution(
            "episode",
            "/Downloads/episode.mkv",
            "/Series/Show/episode.mkv",
            "move_only",
        )],
        selected_root="/Downloads",
        preflight=snapshot,
    )

    assert summary.organized_files == 1
    assert "/Downloads" not in storage.info_reads


def test_empty_captured_parent_id_fails_closed_without_parent_reread():
    class NativeStorage(StatefulStorage):
        def __init__(self):
            super().__init__(files=[("/Downloads/episode.mkv", "episode")])
            self.info_reads = []
            self.native_calls = 0

        def get_file_info(self, path):
            self.info_reads.append(path)
            return super().get_file_info(path)

        def move_files_by_id(self, _file_ids, _target_dir_id):
            self.native_calls += 1

    storage = NativeStorage()
    snapshot = file_executor.FileTransactionSnapshot.from_provider_facts(
        {
            "/Downloads/episode.mkv": {"file_id": "episode"},
            "/Series/Show/episode.mkv": None,
            "/Downloads": None,
        },
        {"/Downloads": ""},
    )

    summary = execute_file_resolutions(
        storage,
        [_resolution(
            "episode",
            "/Downloads/episode.mkv",
            "/Series/Show/episode.mkv",
            "move_only",
        )],
        selected_root="/Downloads",
        preflight=snapshot,
    )

    assert summary.outcomes[0].reason_codes == (
        "source_directory_unverifiable",
    )
    assert storage.native_calls == 0
    assert "/Downloads" not in storage.info_reads


def test_snapshot_source_identity_mismatch_stops_before_mutation():
    class NativeStorage(StatefulStorage):
        def __init__(self):
            super().__init__(files=[("/Downloads/episode.mkv", "episode")])
            self.native_calls = 0

        def move_files_by_id(self, _file_ids, _target_dir_id):
            self.native_calls += 1

    storage = NativeStorage()
    snapshot = file_executor.FileTransactionSnapshot.from_provider_facts(
        {
            "/Downloads/episode.mkv": {"file_id": "replacement"},
            "/Series/Show/episode.mkv": None,
            "/Downloads": {"file_id": "source-dir"},
        },
        {"/Downloads": "source-dir"},
    )

    summary = execute_file_resolutions(
        storage,
        [_resolution(
            "episode",
            "/Downloads/episode.mkv",
            "/Series/Show/episode.mkv",
            "move_only",
        )],
        selected_root="/Downloads",
        preflight=snapshot,
    )

    assert summary.outcomes[0].reason_codes == ("source_identity_changed",)
    assert storage.native_calls == 0
    assert storage.renames == []


def test_foreign_target_appearing_after_snapshot_is_not_overwritten():
    class NoOverwriteStorage(StatefulStorage):
        def __init__(self):
            super().__init__(
                files=[
                    ("/Downloads/episode.mkv", "episode"),
                    ("/Series/Show/episode.mkv", "foreign"),
                ],
                directories=["/Downloads", "/Series/Show"],
            )

        def create_dir_recursive(self, path):
            return {"file_id": f"dir:{path}", "file_category": "0"}

        def move_files_by_id(self, file_ids, target_dir_id):
            assert file_ids == ["episode"]
            assert target_dir_id == "dir:/Series/Show"
            return {"state": "submitted"}

    storage = NoOverwriteStorage()
    snapshot = file_executor.FileTransactionSnapshot.from_provider_facts(
        {
            "/Downloads/episode.mkv": {"file_id": "episode"},
            "/Series/Show/episode.mkv": None,
            "/Downloads": {"file_id": "source-dir"},
        },
        {"/Downloads": "source-dir"},
    )

    summary = execute_file_resolutions(
        storage,
        [_resolution(
            "episode",
            "/Downloads/episode.mkv",
            "/Series/Show/episode.mkv",
            "move_only",
        )],
        selected_root="/Downloads",
        preflight=snapshot,
    )

    assert summary.failed_files == 1
    assert storage.files["/Series/Show/episode.mkv"]["file_id"] == "foreign"
    assert storage.files["/Downloads/episode.mkv"]["file_id"] == "episode"


def test_bound_rename_journal_identity_mismatch_prevents_native_move():
    journal = RenameOperationJournal()

    class MismatchedRenameStorage(StatefulStorage):
        def __init__(self):
            super().__init__(files=[("/Downloads/old.mkv", "episode")])
            self.journal = journal
            self.native_calls = 0

        def rename(self, source, new_name):
            renamed = super().rename(source, new_name)
            journal.record_rename(
                source_path=source,
                target_path=str(PurePosixPath(source).parent / new_name),
                source_id="episode",
                target_id="foreign",
            )
            return renamed

        def move_files_by_id(self, _file_ids, _target_dir_id):
            self.native_calls += 1

    storage = MismatchedRenameStorage()
    snapshot = file_executor.FileTransactionSnapshot.from_provider_facts(
        {
            "/Downloads/old.mkv": {"file_id": "episode"},
            "/Series/Show/episode.mkv": None,
            "/Downloads": {"file_id": "source-dir"},
        },
        {"/Downloads": "source-dir"},
    )

    summary = execute_file_resolutions(
        storage,
        [_resolution(
            "episode",
            "/Downloads/old.mkv",
            "/Series/Show/episode.mkv",
            "rename_and_move",
        )],
        selected_root="/Downloads",
        journal=journal,
        preflight=snapshot,
    )

    assert summary.outcomes[0].reason_codes == ("target_identity_changed",)
    assert storage.native_calls == 0
    assert "/Downloads/episode.mkv" in storage.files


def test_native_copy_that_retains_source_fails_fresh_reconciliation():
    class CopyStorage(StatefulStorage):
        def __init__(self):
            super().__init__(
                files=[("/Downloads/episode.mkv", "episode")],
                directories=["/Downloads"],
            )

        def create_dir_recursive(self, path):
            self.directories.add(path)
            return {"file_id": f"dir:{path}", "file_category": "0"}

        def move_files_by_id(self, _file_ids, _target_dir_id):
            self.files["/Series/Show/episode.mkv"] = dict(
                self.files["/Downloads/episode.mkv"]
            )
            return {"state": "submitted"}

    storage = CopyStorage()
    summary = execute_file_resolutions(
        storage,
        [_resolution(
            "episode",
            "/Downloads/episode.mkv",
            "/Series/Show/episode.mkv",
            "move_only",
        )],
        selected_root="/Downloads",
    )

    assert summary.outcomes[0].reason_codes == (
        "source_still_present_after_move",
    )
    assert "/Downloads/episode.mkv" in storage.files


def test_native_fresh_listing_failure_never_uses_preflight_as_postcondition():
    class UnreadableListingStorage(StatefulStorage):
        def __init__(self):
            super().__init__(
                files=[("/Downloads/episode.mkv", "episode")],
                directories=["/Downloads"],
            )

        def create_dir_recursive(self, path):
            self.directories.add(path)
            return {"file_id": f"dir:{path}", "file_category": "0"}

        def move_files_by_id(self, _file_ids, _target_dir_id):
            self.files["/Series/Show/episode.mkv"] = self.files.pop(
                "/Downloads/episode.mkv"
            )

        def get_file_list(self, _params):
            raise RuntimeError("provider listing unavailable")

    summary = execute_file_resolutions(
        UnreadableListingStorage(),
        [_resolution(
            "episode",
            "/Downloads/episode.mkv",
            "/Series/Show/episode.mkv",
            "move_only",
        )],
        selected_root="/Downloads",
    )

    assert summary.outcomes[0].reason_codes == ("fresh_listing_failed",)


class _FreshGateStorage(StatefulStorage):
    """Storage whose live listing may diverge from a supplied preflight."""

    def __init__(self, *, files=(), directories=(), failing_directory_ids=()):
        super().__init__(files=files, directories=directories)
        self.failing_directory_ids = set(failing_directory_ids)
        self.native_calls = []

    def get_file_list(self, params):
        if params["cid"] in self.failing_directory_ids:
            raise RuntimeError("fresh listing unavailable")
        return super().get_file_list(params)

    def move_files_by_id(self, file_ids, target_dir_id):
        self.native_calls.append((tuple(file_ids), target_dir_id))
        target_dir = str(target_dir_id)[4:]
        for file_id in file_ids:
            source = next(
                path for path, info in self.files.items()
                if info["file_id"] == file_id
            )
            target = str(PurePosixPath(target_dir) / PurePosixPath(source).name)
            self.files[target] = self.files.pop(source)
        return {"state": "submitted"}


def _native_gate_snapshot(*resolutions):
    source_parent = str(PurePosixPath(resolutions[0].source_path).parent)
    facts = {
        source_parent: {"file_id": f"dir:{source_parent}"},
    }
    for resolution in resolutions:
        facts[resolution.source_path] = {"file_id": resolution.source_id}
        facts[resolution.target_path] = None
    return file_executor.FileTransactionSnapshot.from_provider_facts(
        facts,
        {source_parent: f"dir:{source_parent}"},
    )


def _native_snapshot_from_storage(storage, *resolutions):
    return file_executor.build_file_transaction_snapshot(
        storage,
        file_paths=[
            path
            for resolution in resolutions
            for path in (resolution.source_path, resolution.target_path)
        ],
        source_parent_paths=[
            str(PurePosixPath(resolution.source_path).parent)
            for resolution in resolutions
        ],
    )


def test_native_transaction_guard_rejects_same_logical_target_before_mutation():
    first = _resolution(
        "first",
        "/Downloads/A/first.mkv",
        "/Series/Show/collision.mkv",
        "rename_and_move",
    )
    second = _resolution(
        "second",
        "/Downloads/B/second.mkv",
        "/Series/Show/collision.mkv",
        "rename_and_move",
    )
    safe = _resolution(
        "safe",
        "/Downloads/C/safe.mkv",
        "/Series/Show/safe.mkv",
        "move_only",
    )
    storage = _FreshGateStorage(
        files=[
            (first.source_path, first.source_id),
            (second.source_path, second.source_id),
            (safe.source_path, safe.source_id),
        ],
        directories=[
            "/Downloads/A", "/Downloads/B", "/Downloads/C", "/Series/Show",
        ],
    )

    summary = execute_file_resolutions(
        storage,
        [first, second, safe],
        selected_root="/Downloads",
        move_batch_size=1,
        preflight=_native_snapshot_from_storage(storage, first, second, safe),
    )

    assert [(item.source_id, item.state) for item in summary.outcomes] == [
        ("first", "failed"),
        ("second", "failed"),
        ("safe", "organized"),
    ]
    assert all(
        item.reason_codes == ("planned_target_collision",)
        for item in summary.outcomes[:2]
    )
    assert storage.renames == []
    assert storage.native_calls == [(('safe',), 'dir:/Series/Show')]
    assert first.source_path in storage.files
    assert second.source_path in storage.files


def test_native_transaction_guard_rejects_duplicate_source_id_before_submission():
    first = _resolution(
        "duplicate",
        "/Downloads/A/one.mkv",
        "/Series/Show/one.mkv",
        "move_only",
    )
    second = _resolution(
        "duplicate",
        "/Downloads/B/two.mkv",
        "/Series/Show/two.mkv",
        "move_only",
    )
    safe = _resolution(
        "safe",
        "/Downloads/C/safe.mkv",
        "/Series/Show/safe.mkv",
        "move_only",
    )
    storage = _FreshGateStorage(
        files=[
            (first.source_path, first.source_id),
            (second.source_path, second.source_id),
            (safe.source_path, safe.source_id),
        ],
        directories=[
            "/Downloads/A", "/Downloads/B", "/Downloads/C", "/Series/Show",
        ],
    )

    summary = execute_file_resolutions(
        storage,
        [first, second, safe],
        selected_root="/Downloads",
        move_batch_size=1,
        preflight=_native_snapshot_from_storage(storage, first, second, safe),
    )

    assert [(item.source_id, item.state) for item in summary.outcomes] == [
        ("duplicate", "failed"),
        ("duplicate", "failed"),
        ("safe", "organized"),
    ]
    assert all(
        item.reason_codes == ("duplicate_source_id_in_transaction",)
        for item in summary.outcomes[:2]
    )
    assert storage.renames == []
    assert storage.native_calls == [(('safe',), 'dir:/Series/Show')]


def test_native_transaction_guard_rejects_physical_target_directory_aliases():
    first = _resolution(
        "first",
        "/Downloads/A/episode.mkv",
        "/Series/First/episode.mkv",
        "move_only",
    )
    second = _resolution(
        "second",
        "/Downloads/B/episode.mkv",
        "/Series/Second/episode.mkv",
        "move_only",
    )

    class AliasedTargetStorage(_FreshGateStorage):
        def get_file_info(self, path):
            if path in {"/Series/First", "/Series/Second"}:
                return {"file_id": "dir:/Series/Shared", "file_category": "0"}
            return super().get_file_info(path)

        def get_file_list(self, params):
            if params["cid"] == "dir:/Series/Shared":
                return []
            return super().get_file_list(params)

    storage = AliasedTargetStorage(
        files=[
            (first.source_path, first.source_id),
            (second.source_path, second.source_id),
        ],
        directories=[
            "/Downloads/A", "/Downloads/B", "/Series/First", "/Series/Second",
        ],
    )

    summary = execute_file_resolutions(
        storage,
        [first, second],
        selected_root="/Downloads",
        move_batch_size=1,
        preflight=_native_snapshot_from_storage(storage, first, second),
    )

    assert [item.state for item in summary.outcomes] == ["failed", "failed"]
    assert all(
        item.reason_codes == ("planned_target_collision",)
        for item in summary.outcomes
    )
    assert storage.renames == []
    assert storage.native_calls == []


def test_native_gate_rejects_stale_source_identity_name_and_foreign_target():
    stale_id = _resolution(
        "stale-id",
        "/Downloads/stale-id.mkv",
        "/Series/Show/stale-id.mkv",
        "move_only",
    )
    stale_name = _resolution(
        "stale-name",
        "/Downloads/stale-name.mkv",
        "/Series/Show/stale-name.mkv",
        "move_only",
    )
    foreign_target = _resolution(
        "foreign-source",
        "/Downloads/foreign-target.mkv",
        "/Series/Show/foreign-target.mkv",
        "move_only",
    )
    storage = _FreshGateStorage(
        files=[
            ("/Downloads/stale-id.mkv", "replacement"),
            ("/Downloads/stale-name-renamed.mkv", "stale-name"),
            ("/Downloads/foreign-target.mkv", "foreign-source"),
            ("/Series/Show/foreign-target.mkv", "foreign"),
        ],
        directories=["/Downloads", "/Series/Show"],
    )

    summary = execute_file_resolutions(
        storage,
        [stale_id, stale_name, foreign_target],
        selected_root="/Downloads",
        preflight=_native_gate_snapshot(
            stale_id, stale_name, foreign_target,
        ),
    )

    assert summary.failed_files == 3
    assert storage.native_calls == []
    assert "/Downloads/stale-id.mkv" in storage.files
    assert "/Downloads/stale-name-renamed.mkv" in storage.files
    assert storage.files["/Series/Show/foreign-target.mkv"]["file_id"] == "foreign"


def test_native_gate_marks_exact_target_replay_organized_without_move():
    resolution = _resolution(
        "episode",
        "/Downloads/episode.mkv",
        "/Series/Show/episode.mkv",
        "move_only",
    )
    journal = RenameOperationJournal()
    storage = _FreshGateStorage(
        files=[("/Series/Show/episode.mkv", "episode")],
        directories=["/Downloads", "/Series/Show"],
    )

    summary = execute_file_resolutions(
        storage,
        [resolution],
        selected_root="/Downloads",
        journal=journal,
        preflight=_native_gate_snapshot(resolution),
    )

    assert summary.outcomes[0].state == "organized"
    assert summary.outcomes[0].observed_path == "/Series/Show/episode.mkv"
    assert storage.native_calls == []
    assert journal.file_transitions[-1]["stage"] == "verified"


def test_native_gate_replay_rejects_foreign_replacement_at_original_source_name():
    resolution = _resolution(
        "episode",
        "/Downloads/episode.mkv",
        "/Series/Show/episode.mkv",
        "move_only",
    )
    storage = _FreshGateStorage(
        files=[("/Downloads/episode.mkv", "episode")],
        directories=["/Downloads", "/Series/Show"],
    )
    snapshot = file_executor.build_file_transaction_snapshot(
        storage,
        file_paths=[resolution.source_path, resolution.target_path],
        source_parent_paths=["/Downloads"],
    )
    storage.files[resolution.target_path] = storage.files.pop(
        resolution.source_path
    )
    storage.files[resolution.source_path] = {
        "file_id": "foreign-replacement",
        "file_category": "1",
    }

    summary = execute_file_resolutions(
        storage,
        [resolution],
        selected_root="/Downloads",
        preflight=snapshot,
    )

    assert summary.outcomes[0].state == "failed"
    assert storage.native_calls == []
    assert storage.files[resolution.source_path]["file_id"] == (
        "foreign-replacement"
    )
    assert storage.files[resolution.target_path]["file_id"] == "episode"


def test_native_gate_submits_safe_peer_but_omits_rejected_same_target_chunk():
    stale = _resolution(
        "stale",
        "/Downloads/stale.mkv",
        "/Series/Show/stale.mkv",
        "move_only",
    )
    foreign_target = _resolution(
        "foreign-source",
        "/Downloads/foreign-target.mkv",
        "/Series/Show/foreign-target.mkv",
        "move_only",
    )
    safe = _resolution(
        "safe",
        "/Downloads/safe.mkv",
        "/Series/Show/safe.mkv",
        "move_only",
    )
    storage = _FreshGateStorage(
        files=[
            ("/Downloads/stale.mkv", "replacement"),
            ("/Downloads/foreign-target.mkv", "foreign-source"),
            ("/Downloads/safe.mkv", "safe"),
            ("/Series/Show/foreign-target.mkv", "foreign"),
        ],
        directories=["/Downloads", "/Series/Show"],
    )

    summary = execute_file_resolutions(
        storage,
        [stale, foreign_target, safe],
        selected_root="/Downloads",
        preflight=_native_gate_snapshot(stale, foreign_target, safe),
    )

    assert [(item.source_id, item.state) for item in summary.outcomes] == [
        ("stale", "failed"),
        ("foreign-source", "failed"),
        ("safe", "organized"),
    ]
    assert storage.native_calls == [(('safe',), 'dir:/Series/Show')]
    assert "/Downloads/stale.mkv" in storage.files
    assert "/Downloads/foreign-target.mkv" in storage.files
    assert "/Series/Show/safe.mkv" in storage.files


def test_native_gate_rejects_duplicate_source_name_but_moves_safe_peer():
    ambiguous = _resolution(
        "episode",
        "/Downloads/episode.mkv",
        "/Series/Show/episode.mkv",
        "move_only",
    )
    safe = _resolution(
        "safe",
        "/Downloads/safe.mkv",
        "/Series/Show/safe.mkv",
        "move_only",
    )

    class DuplicateSourceNameStorage(_FreshGateStorage):
        def get_file_list(self, params):
            items = super().get_file_list(params)
            if params["cid"] == "dir:/Downloads":
                return [*items, {
                    "file_id": "foreign-duplicate",
                    "file_category": "1",
                    "fn": "episode.mkv",
                }]
            return items

    storage = DuplicateSourceNameStorage(
        files=[
            ("/Downloads/episode.mkv", "episode"),
            ("/Downloads/safe.mkv", "safe"),
        ],
        directories=["/Downloads", "/Series/Show"],
    )

    summary = execute_file_resolutions(
        storage,
        [ambiguous, safe],
        selected_root="/Downloads",
        preflight=_native_gate_snapshot(ambiguous, safe),
    )

    assert [(item.source_id, item.state) for item in summary.outcomes] == [
        ("episode", "failed"),
        ("safe", "organized"),
    ]
    assert storage.native_calls == [(('safe',), 'dir:/Series/Show')]
    assert "/Downloads/episode.mkv" in storage.files
    assert "/Series/Show/safe.mkv" in storage.files


def test_native_gate_rejects_selected_id_already_at_target_under_another_name():
    unsafe = _resolution(
        "episode",
        "/Downloads/episode.mkv",
        "/Series/Show/episode.mkv",
        "move_only",
    )
    safe = _resolution(
        "safe",
        "/Downloads/safe.mkv",
        "/Series/Show/safe.mkv",
        "move_only",
    )

    class TargetIdentityCollisionStorage(_FreshGateStorage):
        def get_file_list(self, params):
            items = super().get_file_list(params)
            if params["cid"] == "dir:/Series/Show":
                return [*items, {
                    "file_id": "episode",
                    "file_category": "1",
                    "fn": "episode-other-name.mkv",
                }]
            return items

    storage = TargetIdentityCollisionStorage(
        files=[
            ("/Downloads/episode.mkv", "episode"),
            ("/Downloads/safe.mkv", "safe"),
        ],
        directories=["/Downloads", "/Series/Show"],
    )

    summary = execute_file_resolutions(
        storage,
        [unsafe, safe],
        selected_root="/Downloads",
        preflight=_native_gate_snapshot(unsafe, safe),
    )

    assert [(item.source_id, item.state) for item in summary.outcomes] == [
        ("episode", "failed"),
        ("safe", "organized"),
    ]
    assert storage.native_calls == [(('safe',), 'dir:/Series/Show')]
    assert "/Downloads/episode.mkv" in storage.files
    assert "/Series/Show/safe.mkv" in storage.files


def test_native_gate_rejects_selected_id_duplicated_under_second_source_name(
    monkeypatch,
):
    unsafe = _resolution(
        "episode",
        "/Downloads/episode.mkv",
        "/Series/Show/episode.mkv",
        "move_only",
    )
    safe = _resolution(
        "safe",
        "/Downloads/safe.mkv",
        "/Series/Show/safe.mkv",
        "move_only",
    )

    storage = _FreshGateStorage(
        files=[
            ("/Downloads/episode.mkv", "episode"),
            ("/Downloads/safe.mkv", "safe"),
        ],
        directories=["/Downloads", "/Series/Show"],
    )
    complete_items = file_executor._complete_directory_items

    def duplicate_source_id(storage_arg, directory_id):
        items = complete_items(storage_arg, directory_id)
        if directory_id == "dir:/Downloads":
            return [*items, {
                "file_id": "episode",
                "file_category": "1",
                "fn": "episode-other-name.mkv",
            }]
        return items

    monkeypatch.setattr(
        file_executor,
        "_complete_directory_items",
        duplicate_source_id,
    )

    summary = execute_file_resolutions(
        storage,
        [unsafe, safe],
        selected_root="/Downloads",
        preflight=_native_gate_snapshot(unsafe, safe),
    )

    assert [(item.source_id, item.state) for item in summary.outcomes] == [
        ("episode", "failed"),
        ("safe", "organized"),
    ]
    assert storage.native_calls == [(('safe',), 'dir:/Series/Show')]
    assert "/Downloads/episode.mkv" in storage.files
    assert "/Series/Show/safe.mkv" in storage.files


def test_native_gate_uses_post_rename_current_name_before_native_move():
    resolution = _resolution(
        "episode",
        "/Downloads/release-name.mkv",
        "/Series/Show/canonical-name.mkv",
        "rename_and_move",
    )
    storage = _FreshGateStorage(
        files=[("/Downloads/release-name.mkv", "episode")],
        directories=["/Downloads", "/Series/Show"],
    )
    snapshot = file_executor.build_file_transaction_snapshot(
        storage,
        file_paths=[resolution.source_path, resolution.target_path],
        source_parent_paths=["/Downloads"],
    )

    summary = execute_file_resolutions(
        storage,
        [resolution],
        selected_root="/Downloads",
        preflight=snapshot,
    )

    assert summary.outcomes[0].state == "organized"
    assert storage.renames == [(
        "/Downloads/release-name.mkv", "canonical-name.mkv",
    )]
    assert storage.native_calls == [(('episode',), 'dir:/Series/Show')]
    assert "/Series/Show/canonical-name.mkv" in storage.files


def test_native_gate_listing_failure_blocks_only_that_target_group():
    blocked = _resolution(
        "blocked",
        "/Downloads/blocked.mkv",
        "/Series/A/blocked.mkv",
        "move_only",
    )
    allowed = _resolution(
        "allowed",
        "/Downloads/allowed.mkv",
        "/Series/B/allowed.mkv",
        "move_only",
    )
    storage = _FreshGateStorage(
        files=[
            ("/Downloads/blocked.mkv", "blocked"),
            ("/Downloads/allowed.mkv", "allowed"),
        ],
        directories=["/Downloads", "/Series/A", "/Series/B"],
        failing_directory_ids={"dir:/Series/A"},
    )

    summary = execute_file_resolutions(
        storage,
        [blocked, allowed],
        selected_root="/Downloads",
        preflight=_native_gate_snapshot(blocked, allowed),
    )

    assert [(item.source_id, item.state) for item in summary.outcomes] == [
        ("blocked", "failed"),
        ("allowed", "organized"),
    ]
    assert summary.outcomes[0].reason_codes == ("fresh_listing_failed",)
    assert storage.native_calls == [(('allowed',), 'dir:/Series/B')]
    assert "/Downloads/blocked.mkv" in storage.files
    assert "/Series/B/allowed.mkv" in storage.files


def _snapshot(source, target, source_info, *, parent_id="source-dir"):
    parent = str(PurePosixPath(source).parent)
    return file_executor.FileTransactionSnapshot.from_provider_facts(
        {source: source_info, target: None, parent: {"file_id": parent_id}},
        {parent: parent_id},
    )


def test_idless_fingerprintless_source_cannot_authorize_same_dir_rename():
    source = "/Downloads/old.mkv"
    target = "/Downloads/new.mkv"
    storage = StatefulStorage(directories=["/Downloads"])
    storage.files[source] = {"file_category": "1"}

    summary = execute_file_resolutions(
        storage,
        [_resolution("expected", source, target, "rename_only")],
        selected_root="/Downloads",
        preflight=_snapshot(source, target, {}),
    )

    assert summary.failed_files == 1
    assert summary.outcomes[0].reason_codes == ("source_identity_changed",)
    assert storage.renames == []
    assert source in storage.files


def test_idless_source_requires_complete_matching_sha1_and_size():
    source = "/Downloads/old.mkv"
    target = "/Downloads/new.mkv"
    storage = StatefulStorage(directories=["/Downloads"])
    storage.files[source] = {
        "file_category": "1",
        "sha1": "same-sha1",
        "size": 4096,
    }
    resolution = _resolution(
        "expected",
        source,
        target,
        "rename_only",
        source_fingerprint={"sha1": "same-sha1", "size": 4096},
    )

    summary = execute_file_resolutions(
        storage,
        [resolution],
        selected_root="/Downloads",
        preflight=_snapshot(
            source,
            target,
            {"sha1": "same-sha1", "size": 4096},
        ),
    )

    assert summary.organized_files == 1
    assert storage.renames == [(source, "new.mkv")]


@pytest.mark.parametrize(
    ("expected_fingerprint", "observed_fact"),
    [
        ({"sha1": "same-sha1"}, {"sha1": "same-sha1", "size": 4096}),
        (
            {"sha1": "same-sha1", "size": 4096},
            {"sha1": "same-sha1"},
        ),
    ],
)
def test_idless_partial_fingerprint_cannot_authorize_mutation(
    expected_fingerprint,
    observed_fact,
):
    source = "/Downloads/old.mkv"
    target = "/Downloads/new.mkv"
    storage = StatefulStorage(directories=["/Downloads"])
    storage.files[source] = {"file_category": "1", **observed_fact}

    summary = execute_file_resolutions(
        storage,
        [_resolution(
            "expected",
            source,
            target,
            "rename_only",
            source_fingerprint=expected_fingerprint,
        )],
        selected_root="/Downloads",
        preflight=_snapshot(source, target, observed_fact),
    )

    assert summary.outcomes[0].reason_codes == ("source_identity_changed",)
    assert storage.renames == []


def test_idless_fingerprintless_no_op_is_not_verified():
    path = "/Downloads/episode.mkv"
    storage = StatefulStorage(directories=["/Downloads"])
    storage.files[path] = {"file_category": "1"}
    snapshot = file_executor.FileTransactionSnapshot.from_provider_facts(
        {
            path: {},
            "/Downloads": {"file_id": "source-dir"},
        },
        {"/Downloads": "source-dir"},
    )

    summary = execute_file_resolutions(
        storage,
        [_resolution("expected", path, path, "no_op")],
        selected_root="/Downloads",
        preflight=snapshot,
    )

    assert summary.canonical_no_ops == 0
    assert summary.failed_files == 1
    assert storage.renames == []


def test_empty_expected_and_observed_ids_are_not_exact_replay_evidence():
    source = "/Downloads/missing.mkv"
    target = "/Series/Show/unknown.mkv"
    storage = StatefulStorage(directories=["/Downloads"])
    storage.files[target] = {"file_category": "1"}
    snapshot = file_executor.FileTransactionSnapshot.from_provider_facts(
        {
            source: None,
            target: {},
            "/Downloads": {"file_id": "source-dir"},
        },
        {"/Downloads": "source-dir"},
    )

    summary = execute_file_resolutions(
        storage,
        [_resolution("", source, target, "rename_and_move")],
        selected_root="/Downloads",
        preflight=snapshot,
    )

    assert summary.canonical_no_ops == 0
    assert summary.failed_files == 1


def test_same_dir_rename_fresh_idless_target_needs_complete_fingerprint():
    class LosesIdentityStorage(StatefulStorage):
        def rename(self, source, new_name):
            renamed = super().rename(source, new_name)
            target = str(PurePosixPath(source).parent / new_name)
            self.files[target] = {"file_category": "1"}
            return renamed

    source = "/Downloads/old.mkv"
    target = "/Downloads/new.mkv"
    storage = LosesIdentityStorage(
        files=[(source, "expected")],
        directories=["/Downloads"],
    )

    summary = execute_file_resolutions(
        storage,
        [_resolution("expected", source, target, "rename_only")],
        selected_root="/Downloads",
        preflight=_snapshot(source, target, {"file_id": "expected"}),
    )

    assert summary.organized_files == 0
    assert summary.outcomes[0].reason_codes == ("target_identity_changed",)
    assert storage.renames == [(source, "new.mkv")]


def test_native_idless_fingerprintless_source_performs_zero_mutation():
    class NativeStorage(StatefulStorage):
        def __init__(self):
            super().__init__(directories=["/Downloads"])
            self.files["/Downloads/episode.mkv"] = {"file_category": "1"}
            self.native_calls = 0

        def create_dir_recursive(self, path):
            self.created = getattr(self, "created", []) + [path]
            return {"file_id": f"dir:{path}", "file_category": "0"}

        def move_files_by_id(self, _file_ids, _target_dir_id):
            self.native_calls += 1

    source = "/Downloads/episode.mkv"
    target = "/Series/Show/episode.mkv"
    storage = NativeStorage()

    summary = execute_file_resolutions(
        storage,
        [_resolution("expected", source, target, "move_only")],
        selected_root="/Downloads",
        preflight=_snapshot(source, target, {}),
    )

    assert summary.outcomes[0].reason_codes == ("source_identity_changed",)
    assert storage.native_calls == 0
    assert getattr(storage, "created", []) == []


def test_empty_parent_blocks_same_directory_rename_before_mutation():
    source = "/Downloads/old.mkv"
    target = "/Downloads/new.mkv"
    storage = StatefulStorage(
        files=[(source, "expected")],
        directories=["/Downloads"],
    )

    summary = execute_file_resolutions(
        storage,
        [_resolution("expected", source, target, "rename_only")],
        selected_root="/Downloads",
        preflight=_snapshot(
            source,
            target,
            {"file_id": "expected"},
            parent_id="",
        ),
    )

    assert summary.outcomes[0].reason_codes == (
        "source_directory_unverifiable",
    )
    assert storage.renames == []


@pytest.mark.parametrize("bound_journal", [False, True])
def test_native_exact_target_replay_precedes_empty_parent_validation(
    bound_journal,
):
    source = "/Downloads/already-gone.mkv"
    target = "/Series/Show/episode.mkv"
    journal = RenameOperationJournal() if bound_journal else None

    class NativeReplayStorage(StatefulStorage):
        def __init__(self):
            super().__init__(files=[(target, "expected")])
            self.journal = journal
            self.native_calls = 0

        def move_files_by_id(self, _file_ids, _target_dir_id):
            self.native_calls += 1

    storage = NativeReplayStorage()
    snapshot = file_executor.FileTransactionSnapshot.from_provider_facts(
        {
            source: None,
            target: {"file_id": "expected"},
            "/Downloads": None,
        },
        {"/Downloads": ""},
    )

    summary = execute_file_resolutions(
        storage,
        [_resolution("expected", source, target, "rename_and_move")],
        selected_root="/Downloads",
        journal=journal,
        preflight=snapshot,
    )

    assert summary.canonical_no_ops == 1
    assert summary.outcomes[0].reason_codes == ("target_identity_verified",)
    assert storage.native_calls == 0
    assert storage.renames == []


def test_partial_batch_mapping_fails_snapshot_before_mutation():
    class PartialBatchStorage:
        def __init__(self):
            self.mutations = 0

        def get_file_info_batch(self, paths):
            return {paths[0]: {"file_id": "source"}}

        def rename(self, *_args):
            self.mutations += 1

    storage = PartialBatchStorage()

    with pytest.raises(ValueError, match="missing"):
        file_executor.build_file_transaction_snapshot(
            storage,
            file_paths=["/source.mkv", "/target.mkv"],
            source_parent_paths=[],
        )

    assert storage.mutations == 0


def test_explicit_none_batch_value_is_queried_absence():
    class CompleteBatchStorage:
        def get_file_info_batch(self, paths):
            return {path: None for path in paths}

    snapshot = file_executor.build_file_transaction_snapshot(
        CompleteBatchStorage(),
        file_paths=["/target.mkv"],
        source_parent_paths=[],
    )

    assert "/target.mkv" in snapshot.file_info
    assert snapshot.file_info["/target.mkv"] is None


class _DirectorySnapshotStorage:
    """Controlled 115-shaped storage for snapshot preflight tests."""

    def __init__(self, info, pages):
        self.info = dict(info)
        self.pages = dict(pages)
        self.batch_requests = []
        self.list_requests = []

    def get_file_info_batch(self, paths):
        requested = tuple(paths)
        self.batch_requests.append(requested)
        return {path: self.info.get(path) for path in requested}

    def get_file_list(self, params):
        request = dict(params)
        self.list_requests.append(request)
        response = self.pages[(request["cid"], request["offset"])]
        return response


def _directory_snapshot_info(*, target_parent=True):
    info = {
        "/Downloads/Release": {"file_id": "source-dir", "file_category": "0"},
        "/Downloads/Release/one.mkv": {
            "file_id": "one",
            "sha1": "SHA-ONE",
            "size": 11,
        },
        "/Downloads/Release/two.mkv": {
            "file_id": "two",
            "sha1": "SHA-TWO",
            "size": 22,
        },
    }
    if target_parent:
        info["/Series/Show"] = {"file_id": "target-dir", "file_category": "0"}
    return info


def _directory_item(provider_id, name, sha1, size):
    return {
        "file_id": provider_id,
        "fn": name,
        "sha1": sha1,
        "size": size,
    }


def test_directory_snapshot_uses_complete_shared_parent_lists_for_preflight_facts():
    storage = _DirectorySnapshotStorage(
        _directory_snapshot_info(),
        {
            ("source-dir", 0): [
                _directory_item("one", "one.mkv", "SHA-ONE", 11),
                _directory_item("two", "two.mkv", "SHA-TWO", 22),
            ],
            ("target-dir", 0): [],
        },
    )

    snapshot = file_executor.build_file_transaction_snapshot(
        storage,
        file_paths=(
            "/Downloads/Release/one.mkv",
            "/Downloads/Release/two.mkv",
            "/Series/Show/one.mkv",
            "/Series/Show/two.mkv",
        ),
        source_parent_paths=("/Downloads/Release",),
    )

    assert snapshot.require_file_info("/Downloads/Release/one.mkv") == (
        file_executor.PreflightFileInfo("one", "sha-one", 11)
    )
    assert snapshot.require_file_info("/Downloads/Release/two.mkv") == (
        file_executor.PreflightFileInfo("two", "sha-two", 22)
    )
    assert snapshot.require_file_info("/Series/Show/one.mkv") is None
    assert snapshot.require_file_info("/Series/Show/two.mkv") is None
    assert snapshot.require_source_parent_id("/Downloads/Release") == "source-dir"
    assert storage.batch_requests == [
        ("/Downloads/Release", "/Series/Show"),
    ]
    assert [request["cid"] for request in storage.list_requests] == [
        "source-dir",
        "target-dir",
    ]


def test_directory_snapshot_keeps_one_file_per_directory_on_exact_reads():
    storage = _DirectorySnapshotStorage(
        {
            "/Downloads/Release": {"file_id": "source-dir", "file_category": "0"},
            "/Downloads/Release/one.mkv": {
                "file_id": "one", "sha1": "SHA-ONE", "size": 11,
            },
            "/Series/Show/one.mkv": None,
        },
        {},
    )

    snapshot = file_executor.build_file_transaction_snapshot(
        storage,
        file_paths=(
            "/Downloads/Release/one.mkv",
            "/Series/Show/one.mkv",
        ),
        source_parent_paths=("/Downloads/Release",),
    )

    assert snapshot.require_file_info("/Downloads/Release/one.mkv").provider_id == "one"
    assert snapshot.require_file_info("/Series/Show/one.mkv") is None
    assert storage.batch_requests == [(
        "/Downloads/Release/one.mkv",
        "/Series/Show/one.mkv",
        "/Downloads/Release",
    )]
    assert storage.list_requests == []


def test_directory_snapshot_allows_absent_target_parent_to_prove_target_absence():
    info = _directory_snapshot_info(target_parent=False)
    info["/Series/Show/one.mkv"] = None
    info["/Series/Show/two.mkv"] = None
    storage = _DirectorySnapshotStorage(
        info,
        {
            ("source-dir", 0): [
                _directory_item("one", "one.mkv", "SHA-ONE", 11),
                _directory_item("two", "two.mkv", "SHA-TWO", 22),
            ],
        },
    )

    snapshot = file_executor.build_file_transaction_snapshot(
        storage,
        file_paths=(
            "/Downloads/Release/one.mkv",
            "/Downloads/Release/two.mkv",
            "/Series/Show/one.mkv",
            "/Series/Show/two.mkv",
        ),
        source_parent_paths=("/Downloads/Release",),
    )

    assert snapshot.require_file_info("/Series/Show/one.mkv") is None
    assert snapshot.require_file_info("/Series/Show/two.mkv") is None
    assert storage.batch_requests == [
        ("/Downloads/Release", "/Series/Show"),
    ]
    assert [request["cid"] for request in storage.list_requests] == ["source-dir"]


def test_directory_snapshot_reads_all_pages_before_projecting_requested_child():
    first_page = [
        _directory_item(f"filler-{index}", f"filler-{index}.mkv", "filler", 1)
        for index in range(999)
    ] + [_directory_item("one", "one.mkv", "SHA-ONE", 11)]
    storage = _DirectorySnapshotStorage(
        _directory_snapshot_info(),
        {
            ("source-dir", 0): {"list": first_page, "has_more": True},
            ("source-dir", 1000): {"list": [
                _directory_item("two", "two.mkv", "SHA-TWO", 22),
            ]},
            ("target-dir", 0): [],
        },
    )

    snapshot = file_executor.build_file_transaction_snapshot(
        storage,
        file_paths=(
            "/Downloads/Release/one.mkv",
            "/Downloads/Release/two.mkv",
            "/Series/Show/one.mkv",
            "/Series/Show/two.mkv",
        ),
        source_parent_paths=("/Downloads/Release",),
    )

    assert snapshot.require_file_info("/Downloads/Release/two.mkv").provider_id == "two"
    assert [request["offset"] for request in storage.list_requests] == [0, 1000, 0]


@pytest.mark.parametrize(
    ("source_pages", "reason"),
    [
        (
            {
                0: {"list": [], "has_more": True},
            },
            "empty page with has_more",
        ),
        (
            {
                0: {"list": [
                    _directory_item("one", "one.mkv", "SHA-ONE", 11),
                ], "has_more": True},
                1: {"list": [
                    _directory_item("one", "one.mkv", "SHA-ONE", 11),
                ], "has_more": True},
            },
            "repeated page",
        ),
        (
            {
                0: {"list": [
                    _directory_item("one", "one.mkv", "SHA-ONE", 11),
                    _directory_item("one", "two.mkv", "SHA-TWO", 22),
                ]},
            },
            "conflicting duplicate id",
        ),
        (
            {
                0: {"list": [
                    {"file_id": "one", "fn": "one.mkv"},
                    _directory_item("two", "two.mkv", "SHA-TWO", 22),
                ]},
            },
            "missing sha and size",
        ),
    ],
)
def test_directory_snapshot_discards_untrusted_listing_and_uses_exact_fallback(
    source_pages,
    reason,
):
    pages = {
        ("source-dir", offset): response
        for offset, response in source_pages.items()
    }
    storage = _DirectorySnapshotStorage(_directory_snapshot_info(), pages)
    storage.pages[("target-dir", 0)] = []

    snapshot = file_executor.build_file_transaction_snapshot(
        storage,
        file_paths=(
            "/Downloads/Release/one.mkv",
            "/Downloads/Release/two.mkv",
            "/Series/Show/one.mkv",
            "/Series/Show/two.mkv",
        ),
        source_parent_paths=("/Downloads/Release",),
    )

    assert snapshot.require_file_info("/Downloads/Release/one.mkv").provider_id == "one", reason
    assert snapshot.require_file_info("/Downloads/Release/two.mkv").provider_id == "two", reason
    assert storage.batch_requests[-1] == (
        "/Downloads/Release/one.mkv",
        "/Downloads/Release/two.mkv",
        "/Series/Show/one.mkv",
        "/Series/Show/two.mkv",
        "/Downloads/Release",
    ), reason


def test_directory_snapshot_unrelated_malformed_item_falls_back_without_raising():
    storage = _DirectorySnapshotStorage(
        _directory_snapshot_info(),
        {
            ("source-dir", 0): [
                _directory_item("one", "one.mkv", "SHA-ONE", 11),
                _directory_item("two", "two.mkv", "SHA-TWO", 22),
                {
                    "file_id": "unrelated",
                    "fn": "metadata.nfo",
                    "sha1": [],
                    "size": 1,
                },
            ],
            ("target-dir", 0): [],
        },
    )

    snapshot = file_executor.build_file_transaction_snapshot(
        storage,
        file_paths=(
            "/Downloads/Release/one.mkv",
            "/Downloads/Release/two.mkv",
            "/Series/Show/one.mkv",
            "/Series/Show/two.mkv",
        ),
        source_parent_paths=("/Downloads/Release",),
    )

    assert snapshot.require_file_info("/Downloads/Release/one.mkv").provider_id == "one"
    assert storage.batch_requests[-1] == (
        "/Downloads/Release/one.mkv",
        "/Downloads/Release/two.mkv",
        "/Series/Show/one.mkv",
        "/Series/Show/two.mkv",
        "/Downloads/Release",
    )


def test_directory_snapshot_semantically_repeated_volatile_page_falls_back_to_exact():
    volatile_one = {
        "volatile": "new-token",
        "size": 11,
        "sha1": "SHA-ONE",
        "fn": "one.mkv",
        "file_id": "one",
    }
    volatile_two = {
        "volatile": "new-token",
        "size": 22,
        "sha1": "SHA-TWO",
        "fn": "two.mkv",
        "file_id": "two",
    }
    storage = _DirectorySnapshotStorage(
        _directory_snapshot_info(),
        {
            ("source-dir", 0): {
                "list": [
                    _directory_item("one", "one.mkv", "SHA-ONE", 11),
                    _directory_item("two", "two.mkv", "SHA-TWO", 22),
                ],
                "has_more": True,
            },
            ("source-dir", 2): {"list": [volatile_two, volatile_one]},
            ("target-dir", 0): [],
        },
    )

    snapshot = file_executor.build_file_transaction_snapshot(
        storage,
        file_paths=(
            "/Downloads/Release/one.mkv",
            "/Downloads/Release/two.mkv",
            "/Series/Show/one.mkv",
            "/Series/Show/two.mkv",
        ),
        source_parent_paths=("/Downloads/Release",),
    )

    assert snapshot.require_file_info("/Series/Show/one.mkv") is None
    assert storage.batch_requests[-1] == (
        "/Downloads/Release/one.mkv",
        "/Downloads/Release/two.mkv",
        "/Series/Show/one.mkv",
        "/Series/Show/two.mkv",
        "/Downloads/Release",
    )


def test_directory_snapshot_malformed_has_more_falls_back_to_exact():
    storage = _DirectorySnapshotStorage(
        _directory_snapshot_info(),
        {
            ("source-dir", 0): {
                "list": [
                    _directory_item("one", "one.mkv", "SHA-ONE", 11),
                    _directory_item("two", "two.mkv", "SHA-TWO", 22),
                ],
                "has_more": "yes",
            },
            ("target-dir", 0): [],
        },
    )

    snapshot = file_executor.build_file_transaction_snapshot(
        storage,
        file_paths=(
            "/Downloads/Release/one.mkv",
            "/Downloads/Release/two.mkv",
            "/Series/Show/one.mkv",
            "/Series/Show/two.mkv",
        ),
        source_parent_paths=("/Downloads/Release",),
    )

    assert snapshot.require_file_info("/Series/Show/two.mkv") is None
    assert storage.batch_requests[-1] == (
        "/Downloads/Release/one.mkv",
        "/Downloads/Release/two.mkv",
        "/Series/Show/one.mkv",
        "/Series/Show/two.mkv",
        "/Downloads/Release",
    )


def test_unsupported_batch_stub_falls_back_but_other_errors_propagate():
    class UnsupportedBatchStorage:
        def __init__(self):
            self.reads = []

        def get_file_info_batch(self, _paths):
            raise NotImplementedError

        def get_file_info(self, path):
            self.reads.append(path)
            return None

    unsupported = UnsupportedBatchStorage()
    snapshot = file_executor.build_file_transaction_snapshot(
        unsupported,
        file_paths=["/one", "/two"],
        source_parent_paths=[],
    )
    assert unsupported.reads == ["/one", "/two"]
    assert tuple(snapshot.file_info) == ("/one", "/two")

    class BrokenBatchStorage(UnsupportedBatchStorage):
        def get_file_info_batch(self, _paths):
            raise TimeoutError("provider timeout")

    with pytest.raises(TimeoutError, match="provider timeout"):
        file_executor.build_file_transaction_snapshot(
            BrokenBatchStorage(),
            file_paths=["/one"],
            source_parent_paths=[],
        )

    class InvalidBatchStorage(UnsupportedBatchStorage):
        def get_file_info_batch(self, _paths):
            return []

    with pytest.raises(TypeError, match="mapping"):
        file_executor.build_file_transaction_snapshot(
            InvalidBatchStorage(),
            file_paths=["/one"],
            source_parent_paths=[],
        )


def test_snapshot_factory_rejects_conflicting_normalized_aliases():
    with pytest.raises(ValueError, match="conflicting"):
        file_executor.FileTransactionSnapshot.from_provider_facts(
            {
                "/a/./b": {"file_id": "first"},
                "/a/b": {"file_id": "second"},
            },
            {},
        )

    identical = file_executor.FileTransactionSnapshot.from_provider_facts(
        {
            "/a/./b": {"file_id": "same"},
            "/a/b": {"file_id": "same"},
        },
        {},
    )
    assert identical.file_info["/a/b"].provider_id == "same"


@pytest.mark.parametrize(
    "args",
    [
        ([], "sha", 1),
        ("id", {}, 1),
        ("id", "sha", []),
        (True, "sha", 1),
        ("id", "sha", False),
    ],
)
def test_preflight_file_info_rejects_mutable_fields(args):
    with pytest.raises(TypeError):
        file_executor.PreflightFileInfo(*args)


def test_preflight_file_info_normalizes_scalar_fields():
    value = file_executor.PreflightFileInfo(123, " ABCDEF ", "4096")

    assert value == file_executor.PreflightFileInfo("123", "abcdef", 4096)


def test_snapshot_rejects_container_provider_and_parent_ids():
    with pytest.raises(TypeError):
        file_executor.FileTransactionSnapshot.from_provider_facts(
            {"/file": {"file_id": ["bad"]}},
            {},
        )
    with pytest.raises(TypeError):
        file_executor.FileTransactionSnapshot(
            {"/file": file_executor.PreflightFileInfo("id", "", 0)},
            {"/parent": {"bad": "id"}},
        )
    with pytest.raises(TypeError):
        file_executor.FileTransactionSnapshot(
            {"/file": file_executor.PreflightFileInfo("id", "", 0)},
            {"/parent": 1.5},
        )


@pytest.mark.parametrize("native_stub", [False, True])
@pytest.mark.parametrize(
    ("target_sha1", "target_size", "expected_state"),
    [
        ("same-sha1", 4096, "organized"),
        ("different-sha1", 4096, "failed"),
        ("same-sha1", 0, "failed"),
    ],
)
def test_legacy_copy_target_uses_complete_content_equivalence(
    native_stub,
    target_sha1,
    target_size,
    expected_state,
):
    source = "/Downloads/episode.mkv"
    target = "/Series/Show/episode.mkv"

    class CopyDeleteStorage(StatefulStorage):
        def __init__(self):
            super().__init__(directories=["/Downloads"])
            self.files[source] = {
                "file_id": "source-id",
                "file_category": "1",
                "sha1": "same-sha1",
                "size": 4096,
            }
            self.copy_calls = 0
            self.native_calls = 0

        def create_dir_recursive(self, path):
            self.directories.add(path)
            return {"file_id": f"dir:{path}", "file_category": "0"}

        def move_file_detailed(self, current, target_dir):
            self.copy_calls += 1
            copied = str(PurePosixPath(target_dir) / PurePosixPath(current).name)
            self.files[copied] = {
                "file_id": "new-copy-id",
                "file_category": "1",
                "sha1": target_sha1,
                "size": target_size,
            }
            self.files.pop(current)
            return {
                "state": "moved",
                "copied": True,
                "source_deleted": True,
                "source_path": current,
                "target_path": copied,
            }

    if native_stub:
        class Storage(CopyDeleteStorage):
            def move_files_by_id(self, _file_ids, _target_dir_id):
                self.native_calls += 1
                raise NotImplementedError
    else:
        Storage = CopyDeleteStorage

    storage = Storage()
    resolution = _resolution(
        "source-id",
        source,
        target,
        "move_only",
        source_fingerprint={"sha1": "same-sha1", "size": 4096},
    )

    summary = execute_file_resolutions(
        storage,
        [resolution],
        selected_root="/Downloads",
    )

    assert summary.outcomes[0].state == expected_state
    assert storage.copy_calls == 1
    assert source not in storage.files
    assert storage.files[target]["file_id"] == "new-copy-id"
    assert storage.native_calls == int(native_stub)


@pytest.mark.parametrize("factory", [False, True])
@pytest.mark.parametrize("missing", [False, True])
def test_snapshot_parent_authority_must_match_projected_parent_fact(
    factory,
    missing,
):
    file_info = {} if missing else {
        "/Downloads": {"file_id": "dir:/Downloads"},
    }
    parent_ids = {"/Downloads": "dir:/Other"}

    with pytest.raises(ValueError, match="source parent"):
        if factory:
            file_executor.FileTransactionSnapshot.from_provider_facts(
                file_info,
                parent_ids,
            )
        else:
            projected = {
                path: file_executor.PreflightFileInfo(
                    value["file_id"],
                    "",
                    0,
                )
                for path, value in file_info.items()
            }
            file_executor.FileTransactionSnapshot(projected, parent_ids)


@pytest.mark.parametrize("parent_fact", [None, {"file_id": ""}])
def test_snapshot_parent_empty_authority_must_be_consistently_empty(parent_fact):
    snapshot = file_executor.FileTransactionSnapshot.from_provider_facts(
        {"/Downloads": parent_fact},
        {"/Downloads": ""},
    )

    assert snapshot.source_parent_ids["/Downloads"] == ""


def test_inconsistent_parent_snapshot_fails_before_executor_mutation():
    class RecordingStorage(StatefulStorage):
        def __init__(self):
            super().__init__(files=[("/Downloads/episode.mkv", "episode")])
            self.native_calls = 0

        def move_files_by_id(self, _file_ids, _target_dir_id):
            self.native_calls += 1

    storage = RecordingStorage()

    with pytest.raises(ValueError, match="source parent"):
        snapshot = file_executor.FileTransactionSnapshot.from_provider_facts(
            {
                "/Downloads/episode.mkv": {"file_id": "episode"},
                "/Series/Show/episode.mkv": None,
                "/Downloads": {"file_id": "dir:/Downloads"},
            },
            {"/Downloads": "dir:/Wrong"},
        )
        execute_file_resolutions(
            storage,
            [_resolution(
                "episode",
                "/Downloads/episode.mkv",
                "/Series/Show/episode.mkv",
                "move_only",
            )],
            selected_root="/Downloads",
            preflight=snapshot,
        )

    assert storage.native_calls == 0
    assert storage.renames == []
