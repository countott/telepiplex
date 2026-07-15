import asyncio
import ast
import tempfile
import threading
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
        self.reports = []

    async def publish_event(self, event_type, payload, **kwargs):
        if self.fail_publish:
            raise RuntimeError("core unavailable")
        self.events.append((event_type, payload, kwargs))
        return {"event_id": "event-1"}

    async def notify_user(self, user_id, text, **kwargs):
        self.notifications.append((user_id, text, kwargs))
        return {"accepted": True}

    async def report_operation(self, report, **kwargs):
        self.reports.append(dict(report))
        return {
            "accepted": True,
            "operation_id": report["operation_id"],
            "state": report["state"],
            "revision": report["revision"],
        }


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
        self.deleted_files = []
        self.tokens = ("", "")

    def add_offline_task(self, link, selected_path):
        return True

    def wait_for_download(self, link, **kwargs):
        progress_callback = kwargs.get("progress_callback")
        if progress_callback:
            progress_callback({
                "resource_name": "Show.S01E01.mkv",
                "info_hash": "hash-1",
                "progress": 50,
            })
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
        cancel_event = kwargs.get("cancel_event")
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("authorization cancelled")
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

    async def test_download_reports_stages_and_hands_same_operation_to_renaming(self):
        result = await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "1" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-download-1",
                "operation_revision": 0,
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "download-operation-1"},
        })

        self.assertEqual(result["operation_id"], "op-download-1")
        await self.runtime.tasks.pop("download-operation-1")

        stages = [report["stage"] for report in self.core.reports]
        for stage in (
            "preparing_submission",
            "submitted",
            "downloading",
            "reading_files",
            "handoff_renaming",
        ):
            self.assertIn(stage, stages)
        self.assertEqual(self.core.reports[-1]["state"], "handed_off")
        self.assertEqual(self.core.reports[-1]["next_plugin_id"], "renaming")
        self.assertEqual(self.core.events[0][1]["operation_id"], "op-download-1")
        self.assertEqual(self.core.events[0][1]["chat_id"], 10)

    async def test_cancelled_download_deletes_known_offline_record_once_not_media(self):
        class BlockingClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.wait_started = threading.Event()

            def wait_for_download(self, link, **kwargs):
                kwargs["progress_callback"]({
                    "resource_name": "Show.partial",
                    "info_hash": "known-hash",
                    "progress": 5,
                })
                self.wait_started.set()
                cancel_event = kwargs["cancel_event"]
                while not cancel_event.wait(0.01):
                    pass
                raise RuntimeError("cancelled")

        client = BlockingClient()
        self.feature.client = client
        await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "2" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-cancel-1",
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "cancel-job-1"},
        })
        task = asyncio.create_task(self.runtime.tasks.pop("cancel-job-1"))
        self.assertTrue(await asyncio.to_thread(client.wait_started.wait, 1))

        accepted = await self.feature.operation_control({
            "operation_id": "op-cancel-1",
            "action": "cancel",
            "revision": self.core.reports[-1]["revision"],
        })
        await task

        self.assertEqual(accepted["operation"]["state"], "cancelling")
        self.assertEqual(client.deleted_tasks, [("known-hash", 0)])
        self.assertEqual(client.deleted_files, [])
        self.assertEqual(self.core.reports[-1]["state"], "cancelled")
        self.assertEqual(
            self.core.reports[-1]["details"]["offline_task_record"],
            "deleted",
        )

    async def test_cancel_before_info_hash_keeps_offline_record_without_retry(self):
        class BlockingSubmitClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.add_started = threading.Event()
                self.release_add = threading.Event()

            def add_offline_task(self, link, selected_path):
                self.add_started.set()
                self.release_add.wait(1)
                return True

        client = BlockingSubmitClient()
        self.feature.client = client
        await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "4" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-cancel-unknown",
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "cancel-job-unknown"},
        })
        task = asyncio.create_task(self.runtime.tasks.pop("cancel-job-unknown"))
        self.assertTrue(await asyncio.to_thread(client.add_started.wait, 1))
        report_count = len(self.core.reports)

        await self.feature.operation_control({
            "operation_id": "op-cancel-unknown",
            "action": "cancel",
            "revision": self.core.reports[-1]["revision"],
        })
        client.release_add.set()
        await task

        self.assertEqual(client.deleted_tasks, [])
        self.assertFalse(any(
            report["state"] == "running"
            for report in self.core.reports[report_count:]
        ))
        self.assertEqual(self.core.reports[-1]["state"], "cancelled")
        self.assertEqual(
            self.core.reports[-1]["details"]["offline_task_record"],
            "retained",
        )
        self.assertIn("记录已保留", self.core.reports[-1]["status_text"])

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
        self.assertEqual(command["operation"]["state"], "awaiting_input")
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
        self.assertEqual(callback["operation"]["state"], "running")
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
                ["open115:auth:direct", "open115:auth:scan", "open115:exit"],
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

    async def test_each_open_auth_and_path_step_has_one_explicit_exit(self):
        def exit_count(response):
            return sum(
                button.get("text") == "退出"
                for action in response.get("actions", [])
                for row in (action.get("data") or {}).get("keyboard", [])
                for button in row
            )

        self.feature.config["save_directories"] = [
            {"name": "剧集", "path": "/Series"},
        ]
        path = await self.feature.command({
            "command": "magnet",
            "args": ["magnet:?xt=urn:btih:" + "3" * 40],
            "user_id": 1,
            "chat_id": 10,
        })
        self.assertEqual(exit_count(path), 1)

        choose = await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(exit_count(choose), 1)
        access = await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(exit_count(access), 1)
        invalid_access = await self.feature.message({
            "text": "", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(exit_count(invalid_access), 1)
        refresh = await self.feature.message({
            "text": "access", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(exit_count(refresh), 1)
        invalid_refresh = await self.feature.message({
            "text": "your_refresh_token", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(exit_count(invalid_refresh), 1)

    async def test_explicit_exit_clears_session_and_terminalizes_operation(self):
        await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })

        result = await self.feature.callback({
            "payload": "exit", "user_id": 1, "chat_id": 10,
        })

        self.assertEqual(result["session"]["state"], "close")
        self.assertEqual(result["operation"]["state"], "cancelled")
        self.assertNotIn((10, 1), self.feature.sessions)

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

    async def test_scan_authorization_can_be_cancelled_before_token_write(self):
        self.feature.config_store = FakeConfigStore({"app_id": "app-1"})
        await self.feature.command({
            "command": "auth", "user_id": 9, "chat_id": 10,
        })
        scan = await self.feature.callback({
            "payload": "auth:scan", "user_id": 9, "chat_id": 10,
        })
        operation_id = scan["operation"]["operation_id"]

        accepted = await self.feature.operation_control({
            "operation_id": operation_id,
            "action": "cancel",
            "revision": scan["operation"]["revision"],
        })
        auth_task_id = next(
            key for key in self.runtime.tasks if key.startswith("open115-auth-")
        )
        await self.runtime.tasks.pop(auth_task_id)

        self.assertEqual(accepted["operation"]["state"], "cancelling")
        self.assertEqual(self.feature.config_store.writes, [])
        self.assertEqual(self.core.reports[-1]["state"], "cancelled")

    async def test_operation_snapshot_returns_current_non_terminal_tasks(self):
        response = await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })

        snapshot = await self.feature.operation_snapshot({
            "operation_id": response["operation"]["operation_id"],
        })

        self.assertEqual(snapshot["operations"], [response["operation"]])


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
        commands = [item["name"] for item in manifest["commands"]]
        self.assertNotIn("config", commands)
        self.assertIn("auth", commands)
        self.assertEqual(manifest["version"], "1.1.0")
        self.assertEqual(manifest["core_api"], ">=1.1,<2.0")
        self.assertEqual(project["project"]["version"], "1.1.0")
        self.assertEqual(
            project["project"]["dependencies"][0],
            "telepiplex-plugin-sdk==1.1.0",
        )
        self.assertIn("dist/open115-1.1.0.tpx", readme)

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
