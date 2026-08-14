import unittest

from telepiplex_plugin_sdk import FeatureError

from telepiplex_rename.service import (
    RenameFeature,
    _merge_verified_inventory_events,
)

from tests.test_feature_processor import FakeHost, FakeRuntime, FakeStorage


class MixedRootStorage(FakeStorage):
    def __init__(self):
        super().__init__([])

    def get_file_info(self, path):
        if path == "/Library":
            return {"file_id": "root", "file_category": "0"}
        return super().get_file_info(path)

    def get_file_list(self, params):
        return {
            "root": [{
                "file_id": "videos",
                "name": "Random Videos",
                "is_dir": True,
            }, {
                "file_id": "subtitles",
                "name": "Loose Subtitles",
                "is_dir": True,
            }, {
                "file_id": "mixed",
                "name": "Mixed",
                "is_dir": True,
            }],
            "videos": [{
                "file_id": "veep-video",
                "name": "Veep.S07E01.mkv",
                "is_dir": False,
                "size": 1000,
            }],
            "subtitles": [{
                "file_id": "veep-subtitle",
                "name": "Veep (2012) S07E01.CHS.srt",
                "is_dir": False,
                "size": 100,
            }],
            "mixed": [{
                "file_id": "honey-video",
                "name": "Honey and Clover S1 - 01.mkv",
                "is_dir": False,
                "size": 1000,
            }],
        }.get(params.get("cid"), [])


class FileFirstInventoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_inventory_scans_selected_root_once_and_groups_by_file_identity(self):
        feature = RenameFeature(
            config={
                "category_folder": [{
                    "kind": "live_action_series",
                    "name": "Library",
                    "path": "/Library",
                }],
                "storage_timeout": 3,
            },
            host=FakeHost(MixedRootStorage()),
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        owner = {"chat_id": 10, "user_id": 20}

        await feature.command({**owner, "command": "rename", "args": []})
        await feature.callback({**owner, "payload": "inventory:root:0"})
        await runtime.wait()

        session = feature.inventory_sessions[(10, 20)]
        self.assertEqual(session["stage"], "confirmation")
        self.assertEqual(session["counts"], {"pending": 2, "completed": 0})
        self.assertEqual(len(session["pending"]), 2)
        by_name = {item["resource_name"]: item for item in session["pending"]}
        self.assertEqual(set(by_name), {"Honey and Clover", "Veep"})
        self.assertEqual({
            node["file_id"] for node in by_name["Veep"]["file_tree"]
        }, {"veep-video", "veep-subtitle"})
        self.assertEqual({
            node["file_id"] for node in by_name["Honey and Clover"]["file_tree"]
        }, {"honey-video"})
        self.assertEqual({
            item["source_path"] for item in session["pending"]
        }, {"/Library"})
        self.assertTrue(all(
            item["job_id"].startswith("inventory:file-first-v1:")
            for item in session["pending"]
        ))


def test_verified_alias_groups_merge_into_one_downstream_event():
    contract = {
        "metadata_id": "series-veep",
        "confirmed": True,
        "identity": {
            "english_title": "Veep",
            "external_ids": {"tvdb": "75682"},
        },
    }
    payloads = [{
        "job_id": "english-title-group",
        "source_path": "/Library",
        "final_path": "/TV/副总统 (Veep)",
        "media_metadata": contract,
        "file_results": {
            "pipeline_version": "file-first-v1",
            "media_files_total": 1,
            "organized_files": 1,
            "canonical_no_ops": 0,
            "kept_unresolved": 0,
            "target_conflicts": 0,
            "failed_files": 0,
            "verified_work_groups": 1,
            "successful_files": [{
                "source_id": "video",
                "state": "organized",
                "final_path": "/TV/副总统 (Veep)/Veep S07E01.mkv",
            }],
        },
        "file_warnings": [],
    }, {
        "job_id": "chinese-title-group",
        "source_path": "/Library",
        "final_path": "/TV/副总统 (Veep)",
        "media_metadata": contract,
        "file_results": {
            "pipeline_version": "file-first-v1",
            "media_files_total": 1,
            "organized_files": 1,
            "canonical_no_ops": 0,
            "kept_unresolved": 0,
            "target_conflicts": 0,
            "failed_files": 0,
            "verified_work_groups": 1,
            "successful_files": [{
                "source_id": "subtitle",
                "state": "organized",
                "final_path": "/TV/副总统 (Veep)/Veep S07E01.chi.srt",
            }],
        },
        "file_warnings": [],
    }]

    merged = _merge_verified_inventory_events(payloads)

    assert len(merged) == 1
    identity_key, event = merged[0]
    assert identity_key == "metadata:series-veep"
    assert event["job_ids"] == [
        "chinese-title-group",
        "english-title-group",
    ]
    assert event["file_results"]["verified_work_groups"] == 1
    assert {
        item["source_id"]
        for item in event["file_results"]["successful_files"]
    } == {"video", "subtitle"}


class IncompleteSnapshotTest(unittest.IsolatedAsyncioTestCase):
    async def test_provider_declared_incomplete_page_is_rejected(self):
        class IncompleteStorage(FakeStorage):
            def __init__(self):
                super().__init__([])

            def get_file_list(self, _params):
                return {
                    "list": [],
                    "snapshot_complete": False,
                }

        feature = RenameFeature(
            config={"storage_timeout": 3},
            host=FakeHost(IncompleteStorage()),
        )

        with self.assertRaisesRegex(FeatureError, "incomplete"):
            await feature._inventory_directory_items("root")
