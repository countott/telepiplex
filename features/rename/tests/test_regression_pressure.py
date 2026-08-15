from collections import Counter
from pathlib import PurePosixPath

from telepiplex_rename.content_probe import build_metadata_probe
from telepiplex_rename.file_executor import (
    cleanup_source_directories,
    execute_file_resolutions,
)
from telepiplex_rename.file_plan import FileResolution


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
