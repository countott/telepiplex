import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from telepiplex_plugin_sdk.media_metadata import attach_media_metadata

from telepiplex_renaming.models import DownloadCompletedEvent
from telepiplex_renaming.processor import process_generic_media, process_tvdb_episode
from telepiplex_renaming.service import RenamingFeature


ROOT = Path(__file__).resolve().parents[1]


class FakeStorage:
    def __init__(self, items):
        self.items = items
        self.renamed = []
        self.moved = []
        self.deleted = []
        self.created = []

    def get_file_info(self, path):
        if path in {"/Downloads/Release", "/Downloads/Series.Release"}:
            return {"file_id": "root", "file_category": "0"}
        return None

    def get_file_list(self, params):
        return self.items if params.get("cid") == "root" else []

    def create_dir_recursive(self, path):
        self.created.append(path)
        return {"file_id": "target"}

    def rename(self, path, name):
        self.renamed.append((path, name))
        return True

    def move_file(self, source, target):
        self.moved.append((source, target))
        return True

    def delete_single_file(self, path):
        self.deleted.append(path)
        return True


def movie_contract():
    return {
        "schema_version": 1,
        "metadata_id": "movie-1",
        "confirmed": True,
        "identity": {
            "chinese_title": "中文电影",
            "english_title": "English Movie",
            "year": "2024",
            "content_kind": "movie",
            "external_ids": {},
        },
        "relation": {"target_series": None, "source": "confirmed"},
        "placement": {
            "category_kind": "live_action_movie",
            "library_type": "movie",
            "mapping_kind": "standalone",
            "season_number": None,
            "episode_number": None,
        },
        "evidence": {}, "warnings": [], "items": [],
    }


def series_contract():
    return {
        "schema_version": 1,
        "metadata_id": "series-1",
        "confirmed": True,
        "identity": {
            "chinese_title": "中文剧集",
            "english_title": "English Series",
            "year": "2024",
            "content_kind": "main_episode",
            "external_ids": {},
        },
        "relation": {"target_series": None, "source": "confirmed"},
        "placement": {
            "category_kind": "live_action_series",
            "library_type": "series",
            "mapping_kind": "standalone",
            "season_number": None,
            "episode_number": None,
        },
        "evidence": {}, "warnings": [],
        "items": [{
            "item_id": "e1", "content_role": "main_episode",
            "season_number": 1, "episode_number": 1,
        }],
    }


class RenamingProcessorTest(unittest.TestCase):
    def test_ordinary_movie_keeps_largest_video_and_deletes_everything_else(self):
        storage = FakeStorage([
            {"fn": "Movie.2024.1080p.mkv", "fid": "1", "fc": "1", "fs": 1_000_000},
            {"fn": "sample.mp4", "fid": "2", "fc": "1", "fs": 1_000},
            {"fn": "subtitle.srt", "fid": "3", "fc": "1", "fs": 100},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024.1080p",
            naming_metadata=None,
            metadata=attach_media_metadata({}, movie_contract()),
            storage=storage,
        )

        result = process_generic_media(event)

        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/Movies/中文电影 (English Movie)")
        self.assertIn("/Downloads/Release/sample.mp4", storage.deleted)
        self.assertIn("/Downloads/Release", storage.deleted)
        self.assertNotIn("/Downloads/Release/Movie.2024.1080p.mkv", storage.deleted)
        self.assertEqual(storage.moved[-1][1], "/Movies/中文电影 (English Movie)")

    @patch("telepiplex_renaming.processor.infer_tvdb_episode_plan_with_ai")
    def test_normal_series_filename_mapping_precedes_ai_and_deletes_extra_video(self, ai_mock):
        storage = FakeStorage([
            {"fn": "English.Series.S01E01.mkv", "fid": "1", "fc": "1", "fs": 1_000_000},
            {"fn": "sample.S00E99.mp4", "fid": "2", "fc": "1", "fs": 1_000},
            {"fn": "English.Series.S01E01.srt", "fid": "3", "fc": "1", "fs": 100},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release", resource_name="English.Series.S01E01",
            naming_metadata={
                "source": "confirmed", "chinese_title": "中文剧集",
                "english_title": "English Series", "release_title": "English.Series.S01E01",
            },
            metadata=attach_media_metadata({}, series_contract()),
            storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/Series/中文剧集 (English Series)")
        ai_mock.assert_not_called()
        self.assertIn("/Downloads/Series.Release/sample.S00E99.mp4", storage.deleted)
        self.assertIn("/Downloads/Series.Release", storage.deleted)
        self.assertTrue(storage.moved[-1][1].endswith("English Series Season 01"))


class FakeCore:
    def __init__(self):
        self.storage = FakeStorage([
            {"fn": "Movie.2024.mkv", "fid": "1", "fc": "1", "fs": 1000},
            {"fn": "sample.mp4", "fid": "2", "fc": "1", "fs": 1},
        ])
        self.events = []
        self.notifications = []

    async def call_capability(self, capability, method, payload, **_kwargs):
        self.assert_capability = capability
        value = getattr(self.storage, method)(*(payload.get("args") or []), **(payload.get("kwargs") or {}))
        return {"value": value}

    async def publish_event(self, event_type, payload, **kwargs):
        self.events.append((event_type, payload, kwargs))
        return {"event_id": "organized-1"}

    async def notify_user(self, user_id, text, **kwargs):
        self.notifications.append((user_id, text, kwargs))
        return {"accepted": True}


class RenamingFeatureTest(unittest.IsolatedAsyncioTestCase):
    async def test_download_event_calls_storage_rpc_and_publishes_media_organized(self):
        core = FakeCore()
        feature = RenamingFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            core=core,
        )
        result = await feature.download_completed({
            "event_id": "event-1",
            "payload": {
                "job_id": "job-1",
                "link": "magnet:?x",
                "selected_path": "/Movies",
                "user_id": 123,
                "final_path": "/Downloads/Release",
                "resource_name": "Movie.2024",
                "provider": "open115",
                "media_metadata": movie_contract(),
            },
        })

        self.assertTrue(result["organized"])
        self.assertEqual(core.assert_capability, "storage.provider")
        self.assertEqual(core.events[0][0], "media.organized")
        self.assertEqual(core.events[0][1]["job_id"], "job-1")
        self.assertEqual(core.events[0][1]["final_path"], "/Movies/中文电影 (English Movie)")
        self.assertIn("整理完成", core.notifications[0][1])


class FeatureSourceContractTest(unittest.TestCase):
    def test_source_has_no_core_telegram_or_init_imports(self):
        forbidden = []
        for path in (ROOT / "src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = ([item.name for item in node.names] if isinstance(node, ast.Import)
                         else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                forbidden.extend(name for name in names if name.split(".", 1)[0] in {"app", "init", "telegram"})
        self.assertEqual(forbidden, [])


if __name__ == "__main__":
    unittest.main()
