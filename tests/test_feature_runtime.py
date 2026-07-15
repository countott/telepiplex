import asyncio
import ast
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]


class FakeCore:
    def __init__(self):
        self.events = []
        self.notifications = []
        self.fail_publish = False

    async def publish_event(self, event_type, payload, **kwargs):
        if self.fail_publish:
            raise RuntimeError("core unavailable")
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
        self.moved = []
        self.deleted_tasks = []
        self.tokens = ("", "")

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
        self.moved.append((source, target))
        return True

    def rename(self, source, leaf):
        self.renamed.append((source, leaf))
        return True

    def del_offline_task(self, info_hash, del_source_file=0):
        self.deleted_tasks.append((info_hash, del_source_file))
        return True

    def get_file_info(self, path):
        return {"path": path, "file_id": "1"}

    def get_file_tree(self, path):
        return [{
            "name": "Show.S01E01.mkv",
            "relative_path": "Show.S01E01.mkv",
            "path": path,
            "is_dir": False,
            "file_id": "1",
            "size": 1024,
        }]

    def set_tokens(self, access_token, refresh_token):
        self.tokens = (access_token, refresh_token)

    def create_device_authorization(self, app_id):
        return {
            "uid": "device-1",
            "qrcode": "https://115.com/scan/device-1",
            "code_verifier": "verifier",
            "time": 1,
            "sign": "sign",
        }

    def complete_device_authorization(self, authorization, **kwargs):
        return {"access_token": "scan-access", "refresh_token": "scan-refresh"}


class FakeConfigStore:
    def __init__(self, config):
        self.config = dict(config)
        self.writes = []
        self.fail_writes = False

    def read(self):
        return dict(self.config)

    def write_tokens(self, access_token, refresh_token, *, auth_mode):
        if self.fail_writes:
            raise RuntimeError("token=secret-value")
        self.config.update({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "auth_mode": auth_mode,
        })
        self.writes.append((access_token, refresh_token, auth_mode))
        return dict(self.config)


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
            config_store=FakeConfigStore({}),
        )
        self.feature.bind_runtime(self.runtime)

    async def asyncTearDown(self):
        for handle in getattr(self.feature, "session_expiry_handles", {}).values():
            handle.cancel()
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
                "release": {"title": "Show.S01E01.1080p", "indexer": "Prowlarr"},
            },
            "context": {"idempotency_key": "plan-1"},
        })

        self.assertTrue(result["accepted"])
        self.assertEqual(result["job_id"], "plan-1")
        await self.runtime.tasks.pop("plan-1")
        event_type, payload, kwargs = self.core.events[0]
        self.assertEqual(event_type, "download.completed")
        self.assertEqual(payload["job_id"], "plan-1")
        self.assertEqual(payload["download_root"], "/Downloads/Show.S01E01.mkv")
        self.assertEqual(payload["final_path"], payload["download_root"])
        self.assertEqual(payload["resource_name"], "Show.S01E01.mkv")
        self.assertEqual(payload["file_tree"][0]["path"], payload["download_root"])
        self.assertEqual(payload["release"]["title"], "Show.S01E01.1080p")
        self.assertEqual(payload["media_metadata"]["metadata_id"], "m1")
        self.assertEqual(kwargs["idempotency_key"], "plan-1:completed")
        self.assertEqual(self.client.renamed, [])
        self.assertEqual(self.client.moved, [])
        self.assertEqual(self.client.deleted_tasks, [("hash-1", 0)])

    async def test_download_flow_emits_sanitized_runtime_logs(self):
        magnet = "magnet:?xt=urn:btih:" + "f" * 40

        with self.assertLogs("telepiplex.open115", level="INFO") as captured:
            result = await self.feature.download_capability({
                "method": "submit",
                "payload": {
                    "link": magnet,
                    "selected_path": "/Downloads",
                    "user_id": 123,
                },
                "context": {"idempotency_key": "log-job-1"},
            })
            self.assertEqual(result["job_id"], "log-job-1")
            await self.runtime.tasks.pop("log-job-1")

        output = "\n".join(captured.output)
        self.assertIn("open115_download_started", output)
        self.assertIn("open115_download_completed", output)
        self.assertIn("selected_path=/Downloads", output)
        self.assertNotIn(magnet, output)

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

    async def test_completed_job_is_persistently_idempotent(self):
        from telepiplex_open115.jobs import DownloadJobStore
        from telepiplex_open115.service import Open115Feature

        jobs = DownloadJobStore(Path(self._testMethodName + ".db"))
        self.addCleanup(Path(self._testMethodName + ".db").unlink, missing_ok=True)
        feature = Open115Feature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            core=self.core, client=self.client, jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        request = {"method": "submit", "payload": {
            "link": "magnet:?xt=urn:btih:" + "c" * 40,
            "selected_path": "/Downloads",
        }, "context": {"idempotency_key": "durable-1"}}

        await feature.download_capability(request)
        await runtime.tasks.pop("durable-1")
        duplicate = await feature.download_capability(request)

        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["state"], "completed")
        self.assertEqual(runtime.tasks, {})

    async def test_completion_publish_failure_is_not_mislabeled_as_download_failure(self):
        from telepiplex_open115.jobs import DownloadJobStore
        from telepiplex_open115.service import Open115Feature

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        feature = Open115Feature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            core=self.core, client=self.client, jobs=jobs,
        )
        runtime = FakeRuntime(); feature.bind_runtime(runtime)
        self.core.fail_publish = True
        await feature.download_capability({"method": "submit", "payload": {
            "link": "magnet:?xt=urn:btih:" + "d" * 40,
            "selected_path": "/Downloads",
        }, "context": {"idempotency_key": "outbox-1"}})

        await runtime.tasks.pop("outbox-1")

        self.assertEqual(jobs.get("outbox-1")["state"], "downloaded")

    async def test_interrupted_external_transfer_requires_manual_retry(self):
        from telepiplex_open115.jobs import DownloadJobStore

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        jobs.create_or_get("crashed", {"link": "magnet:?x"})
        jobs.update("crashed", "running")

        restarted = DownloadJobStore(path)

        self.assertEqual(restarted.get("crashed")["state"], "interrupted")
        self.assertEqual(restarted.resumable(), [])

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
            "update_id": 22,
        })
        self.assertEqual(callback["session"]["state"], "close")
        self.assertIn("已加入 115 下载队列", callback["actions"][0]["text"])
        self.assertEqual(len(self.runtime.tasks), 1)

    async def test_config_and_auth_offer_token_entry_and_scan_routes(self):
        for command in ("config", "auth"):
            response = await self.feature.command({
                "command": command,
                "user_id": 1,
                "chat_id": 10,
            })
            self.assertEqual(response["session"]["state"], "open")
            keyboard = response["actions"][0]["data"]["keyboard"]
            self.assertEqual(
                [button["callback_data"] for row in keyboard for button in row],
                ["open115:auth:direct", "open115:auth:scan"],
            )
            self.assertIn("Access / Refresh Token", str(keyboard))

    async def test_direct_token_wizard_writes_only_after_refresh_and_activates_client(self):
        await self.feature.command({
            "command": "config", "user_id": 1, "chat_id": 10,
        })
        direct = await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(direct["session"]["state"], "open")
        self.assertIn("Access token", direct["actions"][0]["text"])

        access = await self.feature.message({
            "text": "access-new", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(access["session"]["state"], "open")
        self.assertIn("Refresh token", access["actions"][0]["text"])
        self.assertEqual(self.feature.config_store.writes, [])

        completed = await self.feature.message({
            "text": "refresh-new", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(completed["session"]["state"], "close")
        self.assertEqual(self.feature.config_store.writes, [
            ("access-new", "refresh-new", "direct"),
        ])
        self.assertEqual(self.client.tokens, ("access-new", "refresh-new"))
        self.assertNotIn("access-new", str(completed))
        self.assertNotIn("refresh-new", str(completed))

    async def test_direct_token_wizard_rejects_invalid_values_without_writing(self):
        await self.feature.command({
            "command": "config", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })

        for invalid in ("", "your_access_token", "line-one\nline-two"):
            response = await self.feature.message({
                "text": invalid, "user_id": 1, "chat_id": 10,
            })
            self.assertEqual(response["session"]["state"], "open")
            if invalid:
                self.assertNotIn(invalid, str(response))
        self.assertEqual(self.feature.config_store.writes, [])

        await self.feature.message({
            "text": "access-valid", "user_id": 1, "chat_id": 10,
        })
        response = await self.feature.message({
            "text": "refresh-one\nrefresh-two", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(response["session"]["state"], "open")
        self.assertEqual(self.feature.config_store.writes, [])
        self.assertNotIn("access-valid", str(response))

    async def test_direct_token_wizard_cancel_discards_pending_access(self):
        await self.feature.command({
            "command": "config", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "access-pending", "user_id": 1, "chat_id": 10,
        })

        response = await self.feature.command({
            "command": "q", "user_id": 1, "chat_id": 10,
        })

        self.assertEqual(response["session"]["state"], "close")
        self.assertNotIn((10, 1), self.feature.sessions)
        self.assertEqual(self.feature.config_store.writes, [])
        self.assertNotIn("access-pending", str(response))

    async def test_direct_token_write_failure_preserves_client_and_secret(self):
        self.client.tokens = ("old-access", "old-refresh")
        self.feature.config_store.fail_writes = True
        await self.feature.command({
            "command": "config", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "access-new", "user_id": 1, "chat_id": 10,
        })

        response = await self.feature.message({
            "text": "refresh-new", "user_id": 1, "chat_id": 10,
        })

        self.assertEqual(response["session"]["state"], "open")
        self.assertEqual(self.client.tokens, ("old-access", "old-refresh"))
        self.assertEqual(self.feature.config_store.writes, [])
        self.assertNotIn("access-new", str(response))
        self.assertNotIn("refresh-new", str(response))
        self.assertNotIn("secret-value", str(response))

    async def test_pending_access_token_expires_without_writing(self):
        from telepiplex_open115 import service

        with patch.object(service, "SESSION_TTL_SECONDS", 0):
            await self.feature.command({
                "command": "config", "user_id": 1, "chat_id": 10,
            })
            await self.feature.callback({
                "payload": "auth:direct", "user_id": 1, "chat_id": 10,
            })
            await self.feature.message({
                "text": "access-pending", "user_id": 1, "chat_id": 10,
            })
            await asyncio.sleep(0.01)

        self.assertNotIn((10, 1), self.feature.sessions)
        self.assertEqual(self.feature.config_store.writes, [])

    async def test_magnet_session_replaces_and_clears_pending_access_token(self):
        await self.feature.command({
            "command": "config", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "access-pending", "user_id": 1, "chat_id": 10,
        })
        self.feature.config["save_directories"] = [
            {"name": "剧集", "path": "/Series"},
        ]

        response = await self.feature.command({
            "command": "magnet",
            "args": ["magnet:?xt=urn:btih:" + "e" * 40],
            "user_id": 1,
            "chat_id": 10,
        })

        self.assertEqual(response["session"]["state"], "open")
        self.assertEqual(self.feature.sessions[(10, 1)]["stage"], "path")
        self.assertNotIn((10, 1), self.feature.session_expiry_handles)
        self.assertNotIn("access-pending", str(self.feature.sessions))

    async def test_scan_authorization_remains_independent_and_secret_safe(self):
        self.feature.config_store = FakeConfigStore({"app_id": "app-1"})
        await self.feature.command({
            "command": "config", "user_id": 9, "chat_id": 10,
        })
        scan = await self.feature.callback({
            "payload": "auth:scan", "user_id": 9, "chat_id": 10,
        })
        self.assertEqual(scan["actions"][0]["parse_mode"], "HTML")
        self.assertIn("<pre>", scan["actions"][0]["text"])
        auth_task_id = next(key for key in self.runtime.tasks if key.startswith("open115-auth-"))
        await self.runtime.tasks.pop(auth_task_id)
        self.assertEqual(
            self.feature.config_store.writes[-1],
            ("scan-access", "scan-refresh", "scan"),
        )
        self.assertNotIn("scan-access", str(self.core.notifications))


class FeatureConfigStoreTest(unittest.TestCase):
    def test_token_writeback_preserves_config_and_uses_private_permissions(self):
        from telepiplex_open115.config_store import FeatureConfigStore

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("app_id: app-1\nsave_directories: []\n", encoding="utf-8")
            store = FeatureConfigStore(path)
            updated = store.write_tokens("access", "refresh", auth_mode="scan")

            on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["app_id"], "app-1")
            self.assertEqual(on_disk["access_token"], "access")
            self.assertEqual(on_disk["refresh_token"], "refresh")
            self.assertEqual(updated["auth_mode"], "scan")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class FeatureSourceContractTest(unittest.TestCase):
    def test_schema_declares_custom_config_command_registered_by_manifest(self):
        schema = yaml.safe_load((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text(encoding="utf-8"))
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertEqual(schema["x-telepiplex-config-command"], "config")
        self.assertIn("config", [item["name"] for item in manifest["commands"]])
        self.assertEqual(manifest["version"], "1.0.3")
        self.assertEqual(project["project"]["version"], "1.0.3")
        self.assertIn("dist/open115-1.0.3.tpx", readme)

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
