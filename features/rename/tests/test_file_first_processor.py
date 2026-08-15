from telepiplex_plugin_sdk.media_metadata import attach_media_metadata
from telepiplex_plugin_sdk import FeatureError

from telepiplex_rename.context import runtime_context
from telepiplex_rename.models import DownloadCompletedEvent
from telepiplex_rename.processor import process_tvdb_episode
from telepiplex_rename.processor import process_file_first_media

from tests.test_file_executor import StatefulStorage


def _series_contract(*episodes):
    return {
        "schema_version": 1,
        "metadata_id": "series-1",
        "confirmed": True,
        "identity": {
            "chinese_title": "中文剧集",
            "english_title": "English Series",
            "year": "2024",
            "content_kind": "main_episode",
            "external_ids": {"tvdb": "123"},
        },
        "relation": {"target_series": None, "source": "confirmed"},
        "placement": {
            "category_kind": "live_action_series",
            "library_type": "series",
            "mapping_kind": "standalone",
            "season_number": None,
            "episode_number": None,
        },
        "evidence": {},
        "warnings": [],
        "items": [{
            "item_id": f"episode-{season}-{episode}",
            "content_role": "main_episode",
            "season_number": season,
            "episode_number": episode,
        } for season, episode in episodes],
    }


def _event(root, tree, *episodes):
    runtime_context.configure({
        "media": {"unorganized_path": "/Unorganized"},
        "selection": {
            "unmatched_large_ratio": 0.25,
            "unmatched_large_min_bytes": 300_000_000,
        },
        "ai": {},
    })
    return DownloadCompletedEvent(
        link="magnet:?x",
        selected_path="/Series",
        user_id=1,
        final_path=root,
        resource_name="English.Series.S01",
        naming_metadata={"english_title": "English Series"},
        metadata=attach_media_metadata({}, _series_contract(*episodes)),
        file_tree=tree,
    )


def test_restored_series_in_canonical_directory_is_rename_only():
    root = "/Series/中文剧集 (English Series)/English Series Season 01"
    source = f"{root}/English.Series.S01E01.mkv"
    target = f"{root}/English Series S01E01.mkv"
    storage = StatefulStorage(
        files=[(source, "episode-1")],
        directories=[root],
    )
    event = _event(root, [{
        "file_id": "episode-1",
        "path": source,
        "relative_path": "English.Series.S01E01.mkv",
        "is_dir": False,
    }], (1, 1))
    event.storage = storage

    result = process_tvdb_episode(event)

    assert result.handled is True
    assert storage.renames == [(source, "English Series S01E01.mkv")]
    assert storage.moves == []
    assert storage.deleted == []
    assert target in storage.files


def test_automatic_download_deletes_verified_empty_release_root():
    root = "/Downloads/House.Release"
    source = f"{root}/English.Series.S01E01.mkv"
    storage = StatefulStorage(
        files=[(source, "episode-1")],
        directories=[root, "/Series"],
    )
    event = _event(root, [{
        "file_id": "episode-1",
        "path": source,
        "relative_path": "English.Series.S01E01.mkv",
        "is_dir": False,
    }], (1, 1))
    event.storage = storage

    result = process_tvdb_episode(event)

    assert root not in storage.directories
    assert root in storage.deleted
    assert result.file_results["cleanup"] == {
        "candidate_directories": 1,
        "deleted_directories": 1,
        "retained_directories": 0,
        "failed_directories": 0,
        "complete": True,
        "deleted_paths": [root],
        "failures": [],
    }
    assert "媒体整理结果" in result.message
    assert "TVDB 整理完成" not in result.message


def test_inventory_run_preserves_user_selected_scan_root():
    root = "/Series/UserSelected"
    source = f"{root}/English.Series.S01E01.mkv"
    storage = StatefulStorage(
        files=[(source, "episode-1")],
        directories=[root, "/Series"],
    )
    event = _event(root, [{
        "file_id": "episode-1",
        "path": source,
        "relative_path": "English.Series.S01E01.mkv",
        "is_dir": False,
    }], (1, 1))
    event.provider = "inventory"
    event.storage = storage

    result = process_tvdb_episode(event)

    assert root in storage.directories
    assert root not in storage.deleted
    assert result.file_results["cleanup"]["deleted_directories"] == 0
    assert result.file_results["cleanup"]["complete"] is True


def test_mixed_unrelated_video_stays_while_matching_work_organizes():
    root = "/Downloads/Mixed"
    english = f"{root}/English.Series.S01E01.mkv"
    honey = f"{root}/Honey.and.Clover.S01E01.mkv"
    storage = StatefulStorage(
        files=[(english, "english"), (honey, "honey")],
        directories=[root],
    )
    event = _event(root, [{
        "file_id": "english",
        "path": english,
        "relative_path": "English.Series.S01E01.mkv",
        "is_dir": False,
    }, {
        "file_id": "honey",
        "path": honey,
        "relative_path": "Honey.and.Clover.S01E01.mkv",
        "is_dir": False,
    }], (1, 1))
    event.storage = storage

    result = process_tvdb_episode(event)

    assert result.handled is True
    assert honey in storage.files
    assert all("Honey" not in source for source, _target in storage.moves)
    assert storage.deleted == []


def test_target_conflict_is_local_and_source_is_not_moved_to_unorganized():
    root = "/Downloads/Conflict"
    source = f"{root}/English.Series.S01E01.mkv"
    target = (
        "/Series/中文剧集 (English Series)/English Series Season 01/"
        "English Series S01E01.mkv"
    )
    storage = StatefulStorage(
        files=[(source, "source"), (target, "existing")],
        directories=[root, str(target.rsplit("/", 1)[0])],
    )
    event = _event(root, [{
        "file_id": "source",
        "path": source,
        "relative_path": "English.Series.S01E01.mkv",
        "is_dir": False,
    }], (1, 1))
    event.storage = storage

    result = process_tvdb_episode(event)

    assert result.handled is True
    assert result.final_path == root
    assert source in storage.files
    assert storage.renames == []
    assert storage.moves == []
    assert storage.deleted == []
    assert "目标冲突" in result.message


def test_one_failed_episode_does_not_stop_the_next_episode():
    root = "/Downloads/TwoEpisodes"
    first = f"{root}/English.Series.S01E01.mkv"
    second = f"{root}/English.Series.S01E02.mkv"
    renamed_first = f"{root}/English Series S01E01.mkv"
    storage = StatefulStorage(
        files=[(first, "first"), (second, "second")],
        directories=[root],
        fail_moves=[renamed_first],
    )
    event = _event(root, [{
        "file_id": "first",
        "path": first,
        "relative_path": "English.Series.S01E01.mkv",
        "is_dir": False,
    }, {
        "file_id": "second",
        "path": second,
        "relative_path": "English.Series.S01E02.mkv",
        "is_dir": False,
    }], (1, 1), (1, 2))
    event.storage = storage

    result = process_tvdb_episode(event)

    target_second = (
        "/Series/中文剧集 (English Series)/English Series Season 01/"
        "English Series S01E02.mkv"
    )
    assert result.handled is True
    assert renamed_first in storage.files
    assert target_second in storage.files
    assert "失败 1" in result.message
    assert result.file_results == {
        "pipeline_version": "file-first-v1",
        "media_files_total": 2,
        "organized_files": 1,
        "canonical_no_ops": 0,
        "kept_unresolved": 0,
        "target_conflicts": 0,
        "failed_files": 1,
        "verified_work_groups": 1,
        "successful_files": [{
            "source_id": "second",
            "state": "organized",
            "final_path": target_second,
        }],
        "files": [{
            "source_id": "first",
            "state": "failed",
            "source_path": first,
            "target_path": (
                "/Series/中文剧集 (English Series)/"
                "English Series Season 01/English Series S01E01.mkv"
            ),
            "observed_path": renamed_first,
            "reason_codes": ["move_failed"],
        }, {
            "source_id": "second",
            "state": "organized",
            "source_path": second,
            "target_path": target_second,
            "observed_path": target_second,
            "reason_codes": [],
        }],
        "warnings": [{
            "source_id": "first",
            "state": "failed",
            "observed_path": renamed_first,
            "reason_codes": ["move_failed"],
        }],
        "cleanup": {
            "candidate_directories": 1,
            "deleted_directories": 0,
            "retained_directories": 1,
            "failed_directories": 0,
            "complete": False,
            "deleted_paths": [],
            "failures": [],
        },
    }


def test_incomplete_snapshot_is_rejected_before_storage_mutation():
    root = "/Downloads/Incomplete"
    source = f"{root}/English.Series.S01E01.mkv"
    storage = StatefulStorage(files=[(source, "episode")])
    event = _event(root, [{
        "file_id": "episode",
        "path": source,
        "relative_path": "English.Series.S01E01.mkv",
        "is_dir": False,
    }], (1, 1))
    event.snapshot_complete = False
    event.storage = storage

    try:
        process_file_first_media(
            event,
            operations=[{
                "source_path": source,
                "final_path": "/Series/Show/English Series S01E01.mkv",
            }],
            work_identity={"metadata_id": "series-1"},
        )
    except FeatureError as exc:
        assert exc.code == "inventory_tree_incomplete"
    else:
        raise AssertionError("incomplete snapshot was accepted")

    assert storage.renames == []
    assert storage.moves == []
    assert storage.deleted == []
