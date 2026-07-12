import asyncio
import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


class FakeCore:
    def __init__(self):
        self.events = []
        self.notifications = []

    async def publish_event(self, event_type, payload, **kwargs):
        self.events.append((event_type, payload, kwargs))
        return {"event_id": "event-1"}

    async def notify_user(self, user_id, text, **kwargs):
        self.notifications.append((user_id, text, kwargs))
        return {"accepted": True}


class FakeRuntime:
    def __init__(self):
        self.tasks = {}

    def spawn(self, awaitable, *, task_id):
        self.tasks[task_id] = awaitable


class FakeClient:
    def __init__(self):
        self.renamed = []
        self.deleted_tasks = []

    def add_offline_task(self, link, selected_path):
        return True

    def wait_for_download(self, link, **kwargs):
        return {
            "resource_name": "Show.S01E01.mkv",
            "info_hash": "hash-1",
            "progress": 100,
        }

    def is_directory(self, path):
        return False

    def create_dir_recursive(self, path):
        return {"file_id": "dir-1"}

    def move_file(self, source, target):
        return True

    def rename(self, source, leaf):
        self.renamed.append((source, leaf))
        return True

    def del_offline_task(self, info_hash, del_source_file=0):
        self.deleted_tasks.append((info_hash, del_source_file))
        return True

    def get_file_info(self, path):
        return {"path": path, "file_id": "1"}


class Open115FeatureTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from telepiplex_open115.service import Open115Feature

        self.core = FakeCore()
        self.runtime = FakeRuntime()
        self.client = FakeClient()
        self.feature = Open115Feature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            core=self.core,
            client=self.client,
        )
        self.feature.bind_runtime(self.runtime)

    async def asyncTearDown(self):
        for task in self.runtime.tasks.values():
            if asyncio.iscoroutine(task):
                task.close()

    async def test_submit_returns_job_and_background_publishes_completion(self):
        result = await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "a" * 40,
                "selected_path": "/Downloads",
                "user_id": 123,
                "target_folder_name": "中文名 (English)",
                "media_metadata": {"schema_version": 1, "metadata_id": "m1"},
            },
            "context": {"idempotency_key": "plan-1"},
        })

        self.assertTrue(result["accepted"])
        self.assertEqual(result["job_id"], "plan-1")
        await self.runtime.tasks.pop("plan-1")
        event_type, payload, kwargs = self.core.events[0]
        self.assertEqual(event_type, "download.completed")
        self.assertEqual(payload["job_id"], "plan-1")
        self.assertEqual(payload["final_path"], "/Downloads/中文名 (English)")
        self.assertEqual(payload["media_metadata"]["metadata_id"], "m1")
        self.assertEqual(kwargs["idempotency_key"], "plan-1:completed")
        self.assertEqual(self.client.deleted_tasks, [("hash-1", 0)])

    async def test_storage_capability_is_an_explicit_whitelist(self):
        result = await self.feature.storage_capability({
            "method": "get_file_info",
            "payload": {"args": ["/Downloads/Show"]},
        })
        self.assertEqual(result["value"]["file_id"], "1")

        with self.assertRaisesRegex(Exception, "not allowed"):
            await self.feature.storage_capability({
                "method": "__getattribute__",
                "payload": {"args": ["access_token"]},
            })

    async def test_magnet_command_uses_session_and_namespaced_callback(self):
        self.feature.config["save_directories"] = [
            {"name": "剧集", "path": "/Series"},
        ]
        command = await self.feature.command({
            "command": "magnet",
            "args": ["magnet:?xt=urn:btih:" + "b" * 40],
            "user_id": 1,
            "chat_id": 10,
        })
        self.assertEqual(command["session"]["state"], "open")
        callback_data = command["actions"][0]["data"]["keyboard"][0][0]["callback_data"]
        self.assertEqual(callback_data, "open115:path:0")

        callback = await self.feature.callback({
            "namespace": "open115",
            "payload": "path:0",
            "user_id": 1,
            "chat_id": 10,
        })
        self.assertEqual(callback["session"]["state"], "open")
        followup = await self.feature.message({
            "text": "-",
            "user_id": 1,
            "chat_id": 10,
        })
        self.assertEqual(followup["session"]["state"], "close")
        self.assertEqual(len(self.runtime.tasks), 1)


class FeatureSourceContractTest(unittest.TestCase):
    def test_source_has_no_core_telegram_or_init_imports(self):
        forbidden = []
        for path in (ROOT / "src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                else:
                    names = []
                forbidden.extend(
                    name for name in names
                    if name.split(".", 1)[0] in {"app", "init", "telegram"}
                )
        self.assertEqual(forbidden, [])


if __name__ == "__main__":
    unittest.main()
