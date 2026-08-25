from collections import Counter
from pathlib import PurePosixPath

from telepiplex_rename.content_probe import build_metadata_probe
from telepiplex_rename.file_executor import (
    cleanup_source_directories,
    execute_file_resolutions,
)
from telepiplex_rename.file_plan import FileResolution
from telepiplex_rename.models import DownloadCompletedEvent
from telepiplex_rename.operations import RenameOperationJournal
from telepiplex_rename.processor import process_file_first_media
from telepiplex_rename.service import StorageProxy


def _probe(*paths: str, resource_name: str = "Honey and Clover") -> dict:
    return build_metadata_probe({
        "resource_name": resource_name,
        "file_tree": [
            {"relative_path": path, "is_dir": False}
            for path in paths
        ],
    })


def test_honey_and_clover_s1_dash_episode_is_not_season_range():
    probe = _probe("Honey and Clover S1 - 01.mkv")

    assert probe["observed_seasons"] == [1]
    assert probe["observed_episodes"] == [{
        "season_number": 1,
        "episode_number": 1,
    }]


def test_explicit_second_season_marker_expands_range():
    probe = _probe(
        resource_name="Honey and Clover S1-S2",
    )

    assert probe["observed_seasons"] == [1, 2]


def test_honey_and_clover_38_file_regression_has_only_two_seasons():
    paths = [
        f"Season 1/Honey and Clover S1 - {episode:02d}.mkv"
        for episode in range(1, 25)
    ] + [
        f"Season 2/Honey and Clover S2 - {episode:02d}.mkv"
        for episode in range(1, 15)
    ]

    probe = _probe(*paths)

    assert probe["observed_seasons"] == [1, 2]
    assert probe["observed_episodes"] == [
        {"season_number": season, "episode_number": episode}
        for season, total in ((1, 24), (2, 14))
        for episode in range(1, total + 1)
    ]


def test_episode_marker_parser_pressure_10_000_names():
    for index in range(10_000):
        season = index % 20 + 1
        episode = index % 99 + 1
        kind = index % 4
        if kind == 0:
            path = f"Show S{season} - {episode:02d}.mkv"
            expected_seasons = [season]
        elif kind == 1:
            path = f"Show.S{season:02d}E{episode:02d}.mkv"
            expected_seasons = [season]
        elif kind == 2:
            end = min(season + 1, 20)
            path = f"Show S{season}-S{end}.mkv"
            expected_seasons = list(range(season, end + 1))
        else:
            path = f"Show Season {season}.mkv"
            expected_seasons = [season]

        probe = _probe(path, resource_name="Show")

        assert 0 not in probe["observed_seasons"]
        assert probe["observed_seasons"] == expected_seasons


class _PressureStorage:
    def __init__(self, files: dict[str, str], directories: set[str]):
        self.files = {
            path: {"file_id": source_id, "file_category": "1"}
            for path, source_id in files.items()
        }
        self.directories = set(directories)
        self.child_files = Counter(
            str(PurePosixPath(path).parent) for path in self.files
        )
        self.fail_deletes = set()
        self.moves = 0
        self.delete_attempts = 0

    def get_file_info(self, path):
        if path in self.files:
            return dict(self.files[path])
        if path in self.directories:
            return {"file_id": f"dir:{path}", "file_category": "0"}
        return None

    def create_dir_recursive(self, path):
        self.directories.add(path)
        return True

    def move_file_detailed(self, source, target_dir):
        self.moves += 1
        target = str(PurePosixPath(target_dir) / PurePosixPath(source).name)
        info = self.files.pop(source)
        self.child_files[str(PurePosixPath(source).parent)] -= 1
        self.files[target] = info
        self.child_files[target_dir] += 1
        self.directories.add(target_dir)
        return {
            "state": "moved",
            "copied": True,
            "source_deleted": True,
            "source_path": source,
            "target_path": target,
        }

    def get_file_list(self, params):
        path = str(params["cid"])[4:]
        return ([{"file_id": "retained"}]
                if self.child_files[path] > 0 else [])

    def delete_single_file(self, path):
        self.delete_attempts += 1
        if path in self.fail_deletes:
            return False
        self.directories.discard(path)
        return True


def test_file_execution_and_cleanup_pressure_10_000_files_500_directories():
    files = {}
    directories = {"/Downloads", "/Series"}
    resolutions = []
    for index in range(10_000):
        directory = f"/Downloads/Release-{index % 500:03d}"
        source = f"{directory}/episode-{index:05d}.mkv"
        target = f"/Series/Pressure/episode-{index:05d}.mkv"
        source_id = f"source-{index:05d}"
        directories.add(directory)
        files[source] = source_id
        resolutions.append(FileResolution(
            source_id=source_id,
            source_path=source,
            status="resolved",
            work_identity={"external_id": "pressure"},
            item_identity={"episode_number": index + 1},
            target_path=target,
            action="move_only",
            reason_codes=(),
        ))
    storage = _PressureStorage(files, directories)

    execution = execute_file_resolutions(
        storage,
        resolutions,
        selected_root="/Downloads",
    )

    assert execution.organized_files == 10_000
    assert execution.failed_files == 0
    assert storage.moves == 10_000

    for index in range(50):
        path = f"/Downloads/Release-{index:03d}/retained.nfo"
        storage.files[path] = {
            "file_id": f"retained-{index}",
            "file_category": "1",
        }
        storage.child_files[str(PurePosixPath(path).parent)] += 1
    storage.fail_deletes = {
        f"/Downloads/Release-{index:03d}"
        for index in range(50, 75)
    }

    cleanup = cleanup_source_directories(
        storage,
        resolutions,
        selected_root="/Downloads",
        include_selected_root=False,
        protected_roots=("/Series",),
    )

    assert cleanup.candidate_directories == 500
    assert cleanup.deleted_directories == 425
    assert cleanup.retained_directories == 50
    assert cleanup.failed_directories == 25
    assert cleanup.complete is False
    assert "/Downloads" in storage.directories
    assert "/Series" in storage.directories

    moves_before_replay = storage.moves
    replay = execute_file_resolutions(
        storage,
        resolutions,
        selected_root="/Downloads",
    )

    assert replay.canonical_no_ops == 10_000
    assert replay.failed_files == 0
    assert storage.moves == moves_before_replay


class _LogicalStorageProxy(StorageProxy):
    """Journal-enabled outer capability recorder for the attachment flow."""

    def __init__(self, files, directories):
        journal = RenameOperationJournal()
        super().__init__(None, None, journal=journal)
        self.files = {
            path: {
                "file_id": source_id,
                "file_category": "1",
                "sha1": f"sha1-{source_id}",
                "size": 4096,
            }
            for path, source_id in files
        }
        self.directories = set(directories)
        self.calls = []
        self.phase = "pre_move"

    def _info(self, path):
        if path in self.files:
            return dict(self.files[path])
        if path in self.directories:
            return {"file_id": f"dir:{path}", "file_category": "0"}
        return None

    def _storage_call(self, method, args, kwargs):
        self.calls.append((self.phase, method, args, kwargs))
        if method == "get_file_info_batch":
            return {path: self._info(path) for path in args[0]}
        if method == "get_file_info":
            return self._info(args[0])
        if method == "rename":
            source, new_name = args
            target = str(PurePosixPath(source).parent / new_name)
            if source not in self.files or target in self.files:
                return False
            self.files[target] = self.files.pop(source)
            return True
        if method == "create_dir_recursive":
            path = args[0]
            self.directories.add(path)
            return {"file_id": f"dir:{path}", "file_category": "0"}
        if method == "move_files_by_id":
            file_ids, target_dir_id = args
            self.phase = "post_move"
            target_dir = str(target_dir_id)[4:]
            for source_id in file_ids:
                source = next(
                    path for path, info in self.files.items()
                    if info["file_id"] == source_id
                )
                target = str(
                    PurePosixPath(target_dir) / PurePosixPath(source).name
                )
                if target not in self.files:
                    self.files[target] = self.files.pop(source)
            return {"state": "submitted", "submitted": True}
        if method == "get_file_list":
            params = args[0]
            directory = str(params["cid"])[4:]
            items = [
                {"fn": PurePosixPath(path).name, **info}
                for path, info in self.files.items()
                if str(PurePosixPath(path).parent) == directory
            ]
            items.extend({
                "fn": PurePosixPath(path).name,
                "file_id": f"dir:{path}",
                "file_category": "0",
            } for path in self.directories if (
                path != directory
                and str(PurePosixPath(path).parent) == directory
            ))
            return items
        if method == "delete_single_file":
            path = args[0]
            if any(
                str(PurePosixPath(value).parent) == path
                for value in (*self.files, *self.directories)
                if value != path
            ):
                return False
            self.directories.discard(path)
            return True
        raise AssertionError(f"unexpected storage method: {method}")


def _logical_file_first_fixture(parent_paths):
    files = []
    tree = []
    operations = []
    for index, parent in enumerate(parent_paths, 1):
        source_id = f"episode-{index:02d}"
        source_name = f"Release.S01E{index:02d}.mkv"
        target_name = f"Show S01E{index:02d}.mkv"
        source = f"{parent}/{source_name}"
        target = f"/Series/Show/{target_name}"
        files.append((source, source_id))
        tree.append({
            "file_id": source_id,
            "path": source,
            "relative_path": source.removeprefix("/Downloads/Release/")
            if source.startswith("/Downloads/Release/") else source_name,
            "name": source_name,
            "is_dir": False,
            "size": 4096,
            "sha1": f"sha1-{source_id}",
        })
        operations.append({"source_path": source, "final_path": target})
    directories = {"/Downloads/Release", "/Series"} | set(parent_paths)
    storage = _LogicalStorageProxy(files, directories)
    event = DownloadCompletedEvent(
        link="magnet:?fixture",
        selected_path="/Series",
        user_id=1,
        final_path="/Downloads/Release",
        download_root="/Downloads/Release",
        resource_name="Release",
        file_tree=tree,
        storage=storage,
    )
    result = process_file_first_media(
        event,
        operations=operations,
        work_identity={"external_id": "series-1"},
    )
    return storage, result


def test_attachment_equivalent_transaction_uses_directory_snapshot_and_fresh_native_gate_without_child_preflight_reads():
    storage, result = _logical_file_first_fixture(
        ["/Downloads/Release"] * 16
    )
    counts = Counter(method for _phase, method, _args, _kwargs in storage.calls)

    assert result["organized_files"] == 16
    assert result["cleanup"]["complete"] is True
    assert counts == {
        "get_file_info_batch": 1,
        "get_file_info": 35,
        "rename": 16,
        "create_dir_recursive": 1,
        "move_files_by_id": 1,
        "get_file_list": 6,
        "delete_single_file": 1,
    }
    assert len(storage.calls) == 61
    snapshot_paths = [
        path
        for _phase, method, args, _kwargs in storage.calls
        if method == "get_file_info_batch"
        for path in args[0]
    ]
    assert snapshot_paths == ["/Downloads/Release", "/Series/Show"]
    pre_submit_lists = [
        args[0]
        for phase, method, args, _kwargs in storage.calls
        if phase == "pre_move" and method == "get_file_list"
    ]
    assert Counter(params["cid"] for params in pre_submit_lists) == {
        "dir:/Downloads/Release": 2,
        "dir:/Series/Show": 1,
    }
    post_move_lists = [
        args[0]
        for phase, method, args, _kwargs in storage.calls
        if phase == "post_move" and method == "get_file_list"
    ]
    assert Counter(params["cid"] for params in post_move_lists) == {
        "dir:/Downloads/Release": 2,
        "dir:/Series/Show": 1,
    }
    assert all(
        phase == "post_move"
        for phase, method, _args, _kwargs in storage.calls
        if method == "delete_single_file"
    )


def test_two_interleaved_source_parents_are_order_independent_at_72_calls_with_fresh_native_gate():
    parents = [
        f"/Downloads/Release/Part-{index % 2 + 1}"
        for index in range(16)
    ]
    storage, result = _logical_file_first_fixture(parents)
    counts = Counter(method for _phase, method, _args, _kwargs in storage.calls)

    assert result["organized_files"] == 16
    assert result["cleanup"]["complete"] is True
    assert counts == {
        "get_file_info_batch": 1,
        "get_file_info": 39,
        "rename": 16,
        "create_dir_recursive": 1,
        "move_files_by_id": 1,
        "get_file_list": 11,
        "delete_single_file": 3,
    }
    assert len(storage.calls) == 72
    snapshot_paths = [
        path
        for _phase, method, args, _kwargs in storage.calls
        if method == "get_file_info_batch"
        for path in args[0]
    ]
    assert set(snapshot_paths) == {
        "/Downloads/Release/Part-1",
        "/Downloads/Release/Part-2",
        "/Series/Show",
    }
    pre_submit_lists = [
        args[0]
        for phase, method, args, _kwargs in storage.calls
        if phase == "pre_move" and method == "get_file_list"
    ]
    assert Counter(params["cid"] for params in pre_submit_lists) == {
        "dir:/Downloads/Release/Part-1": 2,
        "dir:/Downloads/Release/Part-2": 2,
        "dir:/Series/Show": 1,
    }
    post_move_lists = [
        args[0]
        for phase, method, args, _kwargs in storage.calls
        if phase == "post_move" and method == "get_file_list"
    ]
    assert Counter(params["cid"] for params in post_move_lists) == {
        "dir:/Downloads/Release": 1,
        "dir:/Downloads/Release/Part-1": 2,
        "dir:/Downloads/Release/Part-2": 2,
        "dir:/Series/Show": 1,
    }
