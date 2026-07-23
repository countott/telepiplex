import asyncio
import ast
import re
import tempfile
import threading
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]


class FakeHost:
    def __init__(self):
        self.events = []
        self.notifications = []
        self.fail_publish = False
        self.reports = []

    async def publish_event(self, event_type, payload, **kwargs):
        if self.fail_publish:
            raise RuntimeError("host unavailable")
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
        self.added = []
        self.tokens = ("", "")

    def add_offline_task(self, link, selected_path):
        self.added.append((link, selected_path))
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
        self.directory_writes = []
        self.fail_writes = False
        self.fail_directory_writes = False

    def read(self):
        return dict(self.config)

    def snapshot(self):
        return {"exists": True, "config": dict(self.config)}

    def restore(self, snapshot):
        self.config = dict(snapshot["config"])
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

    def write_save_directories(self, directories):
        from telepiplex_download.directories import normalize_save_directories

        if self.fail_directory_writes:
            raise RuntimeError("config=secret-value")
        normalized = normalize_save_directories(directories)
        self.config["save_directories"] = normalized
        self.directory_writes.append(normalized)
        return dict(self.config)


class DownloadFeatureTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from telepiplex_download.service import DownloadFeature

        self.host = FakeHost()
        self.runtime = FakeRuntime()
        self.client = FakeClient()
        self.feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host,
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

    async def _open_directory_config(self):
        await self.feature.command({
            "command": "config", "user_id": 1, "chat_id": 10,
        })
        return await self.feature.callback({
            "payload": "config:directories", "user_id": 1, "chat_id": 10,
        })

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
        event_type, payload, kwargs = self.host.events[0]
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

    async def test_download_reports_stages_and_hands_same_operation_to_rename(self):
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

        stages = [report["stage"] for report in self.host.reports]
        for stage in (
            "preparing_submission",
            "submitted",
            "downloading",
            "reading_files",
            "handoff_rename",
        ):
            self.assertIn(stage, stages)
        self.assertEqual(self.host.reports[-1]["state"], "handed_off")
        self.assertEqual(self.host.reports[-1]["next_plugin_id"], "rename")
        self.assertEqual(self.host.events[0][1]["operation_id"], "op-download-1")
        self.assertEqual(self.host.events[0][1]["chat_id"], 10)

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
            "revision": self.host.reports[-1]["revision"],
        })
        await task

        self.assertEqual(accepted["operation"]["state"], "cancelling")
        self.assertEqual(client.deleted_tasks, [("known-hash", 0)])
        self.assertEqual(client.deleted_files, [])
        self.assertEqual(self.host.reports[-1]["state"], "cancelled")
        self.assertEqual(
            self.host.reports[-1]["details"]["offline_task_record"],
            "deleted",
        )

    async def test_lost_response_retry_preserves_live_cancel_owner(self):
        class BlockingClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.wait_started = threading.Event()

            def wait_for_download(self, link, **kwargs):
                kwargs["progress_callback"]({
                    "resource_name": "Show.partial",
                    "info_hash": "retry-known-hash",
                    "progress": 5,
                })
                self.wait_started.set()
                kwargs["cancel_event"].wait(1)
                raise RuntimeError("cancelled")

        client = BlockingClient()
        self.feature.client = client
        request = {
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "3" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-retry-cancel",
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "retry-cancel-job"},
        }

        first = await self.feature.download_capability(request)
        task = asyncio.create_task(self.runtime.tasks.pop("retry-cancel-job"))
        self.assertTrue(await asyncio.to_thread(client.wait_started.wait, 1))
        original_event = self.feature.operations["op-retry-cancel"][
            "cancel_event"
        ]

        retried = await self.feature.download_capability(request)
        self.assertTrue(retried["duplicate"])
        self.assertIs(
            self.feature.operations["op-retry-cancel"]["cancel_event"],
            original_event,
        )
        await self.feature.operation_control({
            "operation_id": "op-retry-cancel",
            "action": "cancel",
            "revision": retried["operation"]["revision"],
        })
        await task

        self.assertEqual(client.deleted_tasks, [("retry-known-hash", 0)])
        self.assertEqual(client.deleted_files, [])
        self.assertEqual(self.host.reports[-1]["state"], "cancelled")

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
        report_count = len(self.host.reports)

        await self.feature.operation_control({
            "operation_id": "op-cancel-unknown",
            "action": "cancel",
            "revision": self.host.reports[-1]["revision"],
        })
        client.release_add.set()
        await task

        self.assertEqual(client.deleted_tasks, [])
        self.assertFalse(any(
            report["state"] == "running"
            for report in self.host.reports[report_count:]
        ))
        self.assertEqual(self.host.reports[-1]["state"], "cancelled")
        self.assertEqual(
            self.host.reports[-1]["details"]["offline_task_record"],
            "retained",
        )
        self.assertIn("记录已保留", self.host.reports[-1]["status_text"])

    async def test_source_can_cancel_before_rename_accepts_handoff(self):
        await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "5" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-provisional-handoff",
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "provisional-handoff"},
        })
        operation = self.feature.operations["op-provisional-handoff"]
        operation["info_hash"] = "known-handoff-hash"
        self.feature._advance_operation(
            "op-provisional-handoff",
            state="handed_off",
            stage="handoff_rename",
            status_text="正在交给 rename。",
            control="cancel",
            next_plugin_id="rename",
        )

        result = await self.feature.operation_control({
            "operation_id": "op-provisional-handoff",
            "action": "cancel",
            "revision": operation["revision"],
        })

        self.assertEqual(result["operation"]["state"], "cancelled")
        self.assertEqual(self.client.deleted_tasks, [("known-handoff-hash", 0)])
        self.assertEqual(self.client.deleted_files, [])

    async def test_download_flow_emits_sanitized_runtime_logs(self):
        magnet = "magnet:?xt=urn:btih:" + "f" * 40

        with self.assertLogs("telepiplex.download", level="INFO") as captured:
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
        self.assertIn("download_download_started", output)
        self.assertIn("download_download_completed", output)
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
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        jobs = DownloadJobStore(Path(self._testMethodName + ".db"))
        self.addCleanup(Path(self._testMethodName + ".db").unlink, missing_ok=True)
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host, client=self.client, jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        request = {"method": "submit", "payload": {
            "link": "magnet:?xt=urn:btih:" + "c" * 40,
            "selected_path": "/Downloads",
        }, "context": {"idempotency_key": "durable-1"}}

        await feature.download_capability(request)
        await runtime.tasks.pop("durable-1")
        report_count = len(self.host.reports)
        duplicate = await feature.download_capability(request)

        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["state"], "completed")
        self.assertEqual(runtime.tasks, {})
        self.assertEqual(len(self.host.reports), report_count)

    async def test_concurrent_same_job_starts_once_with_one_operation_identity(self):
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host, client=self.client, jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        original_report = feature._report_operation
        report_started = asyncio.Event()
        release_report = asyncio.Event()

        async def blocking_report(*args, **kwargs):
            report_started.set()
            await release_report.wait()
            return await original_report(*args, **kwargs)

        feature._report_operation = blocking_report
        request = {"method": "submit", "payload": {
            "link": "magnet:?xt=urn:btih:" + "8" * 40,
            "selected_path": "/Downloads",
            "chat_id": 10,
            "user_id": 1,
        }, "context": {"idempotency_key": "concurrent-one"}}
        first_task = asyncio.create_task(feature.download_capability(request))
        await report_started.wait()
        duplicate = await feature.download_capability(request)
        release_report.set()
        first = await first_task

        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(first["operation_id"], duplicate["operation_id"])
        self.assertEqual(list(runtime.tasks), ["concurrent-one"])
        await runtime.tasks.pop("concurrent-one")
        self.assertEqual(len(self.client.added), 1)

    async def test_lost_running_report_response_still_starts_executor_once(self):
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host, client=self.client, jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        original_host_report = self.host.report_operation
        host_report_attempts = 0

        async def unavailable_once_then_accept(report, **kwargs):
            nonlocal host_report_attempts
            host_report_attempts += 1
            if host_report_attempts == 1:
                raise RuntimeError("Host still unavailable")
            return await original_host_report(report, **kwargs)

        self.host.report_operation = unavailable_once_then_accept
        original_report = feature._report_operation
        lost = False

        async def lose_first_response(*args, **kwargs):
            nonlocal lost
            if not lost:
                lost = True
                raise RuntimeError("Host response lost")
            return await original_report(*args, **kwargs)

        feature._report_operation = lose_first_response
        request = {"method": "submit", "payload": {
            "link": "magnet:?xt=urn:btih:" + "7" * 40,
            "selected_path": "/Downloads",
            "operation_id": "op-lost-running-response",
            "operation_revision": 5,
            "chat_id": 10,
            "user_id": 1,
        }, "context": {"idempotency_key": "lost-running-response"}}

        accepted = await feature.download_capability(request)
        duplicate = await feature.download_capability(request)

        self.assertTrue(accepted["accepted"])
        self.assertTrue(accepted["report_pending"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(list(runtime.tasks), ["lost-running-response"])
        self.assertEqual(jobs.get("lost-running-response")["state"], "running")
        await runtime.tasks.pop("lost-running-response")
        self.assertEqual(len(self.client.added), 1)
        self.assertEqual(jobs.get("lost-running-response")["state"], "completed")

    async def test_unconfirmed_pending_ownership_never_submits_offline_task(self):
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        class RejectedHost(FakeHost):
            async def report_operation(self, report, **kwargs):
                self.reports.append(dict(report))
                return {
                    "accepted": False,
                    "state": "cancelled",
                    "revision": report["revision"],
                }

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        host = RejectedHost()
        jobs = DownloadJobStore(path)
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=host, client=self.client, jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        original_report = feature._report_operation
        first = True

        async def unavailable_once(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                raise RuntimeError("Host unavailable before ownership claim")
            return await original_report(*args, **kwargs)

        feature._report_operation = unavailable_once
        accepted = await feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "6" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-unconfirmed-owner",
                "operation_revision": 5,
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "unconfirmed-owner"},
        })

        self.assertTrue(accepted["report_pending"])
        with self.assertRaises(Exception):
            await runtime.tasks.pop("unconfirmed-owner")
        self.assertEqual(self.client.added, [])
        self.assertEqual(jobs.get("unconfirmed-owner")["state"], "failed")

    async def test_cancelled_persisted_job_never_restarts_or_reports_running(self):
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        payload = {
            "link": "magnet:?xt=urn:btih:" + "9" * 40,
            "selected_path": "/Downloads",
            "operation_id": "op-durable-cancelled",
            "chat_id": 10,
            "user_id": 1,
        }
        jobs.create_or_get("durable-cancelled", payload)
        jobs.update("durable-cancelled", "cancelled", error="cancelled")
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host, client=self.client, jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)

        duplicate = await feature.download_capability({
            "method": "submit",
            "payload": payload,
            "context": {"idempotency_key": "durable-cancelled"},
        })

        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["state"], "cancelled")
        self.assertEqual(runtime.tasks, {})
        self.assertEqual(self.host.reports, [])

    async def test_completion_publish_failure_is_not_mislabeled_as_download_failure(self):
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host, client=self.client, jobs=jobs,
        )
        runtime = FakeRuntime(); feature.bind_runtime(runtime)
        self.host.fail_publish = True
        await feature.download_capability({"method": "submit", "payload": {
            "link": "magnet:?xt=urn:btih:" + "d" * 40,
            "selected_path": "/Downloads",
            "operation_id": "op-outbox",
            "operation_revision": 4,
            "chat_id": 10,
            "user_id": 1,
        }, "context": {"idempotency_key": "outbox-1"}})

        await runtime.tasks.pop("outbox-1")

        downloaded = jobs.get("outbox-1")
        self.assertEqual(downloaded["state"], "downloaded")
        self.assertEqual(downloaded["result"]["operation_id"], "op-outbox")
        self.assertEqual(
            downloaded["result"]["operation_revision"],
            feature.operations["op-outbox"]["revision"],
        )
        self.assertEqual(
            (await feature.operation_snapshot({"operation_id": "op-outbox"}))[
                "operations"
            ][0]["state"],
            "handed_off",
        )

        self.host.fail_publish = False
        restored = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host,
            client=self.client,
            jobs=jobs,
        )
        restored_runtime = FakeRuntime()
        restored.bind_runtime(restored_runtime)
        self.assertEqual(
            restored.operations["op-outbox"]["state"], "handed_off"
        )
        await restored_runtime.tasks.pop("outbox-1")
        self.assertEqual(jobs.get("outbox-1")["state"], "completed")
        self.assertEqual(self.host.events[-1][1]["operation_id"], "op-outbox")

    async def test_interrupted_external_transfer_requires_manual_retry(self):
        from telepiplex_download.jobs import DownloadJobStore

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
            {"name": "剧集", "path": "series/live action"},
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
        self.assertEqual(callback_data, "download:path:0")

        callback = await self.feature.callback({
            "namespace": "download",
            "payload": "path:0",
            "user_id": 1,
            "chat_id": 10,
            "update_id": 22,
        })
        self.assertEqual(callback["session"]["state"], "close")
        self.assertIn("已加入 115 下载队列", callback["actions"][0]["text"])
        self.assertEqual(callback["operation"]["state"], "running")
        self.assertEqual(len(self.runtime.tasks), 1)
        task_id = next(iter(self.runtime.tasks))
        await self.runtime.tasks.pop(task_id)
        self.assertEqual(self.client.added[0][1], "/series/live action")

    async def test_config_opens_home_while_auth_opens_authorization_directly(self):
        config = await self.feature.command({
            "command": "config", "user_id": 1, "chat_id": 10,
        })
        config_buttons = [
            button["callback_data"]
            for row in config["actions"][0]["data"]["keyboard"]
            for button in row
        ]
        self.assertEqual(config_buttons, [
            "download:config:auth",
            "download:config:directories",
            "download:exit",
        ])
        self.assertIn("保存目录：0 个", config["actions"][0]["text"])

        from_config = await self.feature.callback({
            "payload": "config:auth", "user_id": 1, "chat_id": 10,
        })
        self.assertIn(
            "download:auth:direct",
            str(from_config["actions"][0]["data"]["keyboard"]),
        )

        auth = await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })
        auth_buttons = [
            button["callback_data"]
            for row in auth["actions"][0]["data"]["keyboard"]
            for button in row
        ]
        self.assertEqual(auth_buttons, [
            "download:auth:direct", "download:auth:scan", "download:exit",
        ])

    async def test_directory_list_is_paginated_with_bounded_keyboard(self):
        directories = [
            {"name": f"目录{index}", "path": f"Path{index}"}
            for index in range(7)
        ]
        self.feature.config["save_directories"] = directories
        self.feature.config_store.config["save_directories"] = directories

        response = await self._open_directory_config()

        keyboard = response["actions"][0]["data"]["keyboard"]
        self.assertLessEqual(len(keyboard), 10)
        self.assertIn("download:config:page:1", str(keyboard))
        self.assertIn("目录4", str(keyboard))
        self.assertNotIn("目录5", str(keyboard))

        next_page = await self.feature.callback({
            "payload": "config:page:1", "user_id": 1, "chat_id": 10,
        })
        next_keyboard = next_page["actions"][0]["data"]["keyboard"]
        self.assertLessEqual(len(next_keyboard), 10)
        self.assertIn("download:config:page:0", str(next_keyboard))
        self.assertIn("目录5", str(next_keyboard))
        self.assertIn("目录6", str(next_keyboard))

    async def test_directory_editor_rejects_negative_item_index(self):
        directories = [
            {"name": "A", "path": "A"},
            {"name": "B", "path": "B"},
        ]
        self.feature.config["save_directories"] = directories
        self.feature.config_store.config["save_directories"] = directories
        await self._open_directory_config()

        response = await self.feature.callback({
            "payload": "config:item:-1", "user_id": 1, "chat_id": 10,
        })

        self.assertIn("不可用", response["actions"][0]["text"])
        self.assertEqual(self.feature.sessions[(10, 1)]["stage"], "directory_list")
        self.assertNotIn("selected_index", self.feature.sessions[(10, 1)])

    async def test_config_out_of_order_callback_keeps_home_navigation(self):
        await self.feature.command({
            "command": "config", "user_id": 1, "chat_id": 10,
        })

        response = await self.feature.callback({
            "payload": "config:save", "user_id": 1, "chat_id": 10,
        })

        keyboard = response["actions"][0]["data"]["keyboard"]
        self.assertIn("download:config:auth", str(keyboard))
        self.assertIn("download:config:directories", str(keyboard))
        self.assertNotIn("download:config:back", str(keyboard))
        self.assertEqual(self.feature.sessions[(10, 1)]["stage"], "config_home")

    async def test_magnet_rejects_negative_directory_index(self):
        self.feature.config["save_directories"] = [
            {"name": "A", "path": "A"},
            {"name": "B", "path": "B"},
        ]
        await self.feature.command({
            "command": "magnet",
            "args": ["magnet:?xt=urn:btih:" + "5" * 40],
            "user_id": 1,
            "chat_id": 10,
        })

        response = await self.feature.callback({
            "payload": "path:-1", "user_id": 1, "chat_id": 10,
        })

        self.assertIn("不可用", response["actions"][0]["text"])
        self.assertEqual(response["session"]["state"], "close")
        self.assertEqual(response["operation"]["state"], "cancelled")
        self.assertNotIn((10, 1), self.feature.sessions)
        self.assertEqual(self.runtime.tasks, {})

    async def test_directory_working_copy_add_edit_delete_and_save(self):
        original = [
            {"name": "剧集", "path": "series"},
            {"name": "删除项", "path": "delete"},
        ]
        self.feature.config["save_directories"] = original
        self.feature.config_store.config["save_directories"] = original
        await self._open_directory_config()

        name_prompt = await self.feature.callback({
            "payload": "config:add", "user_id": 1, "chat_id": 10,
        })
        self.assertIn("第一步", name_prompt["actions"][0]["text"])
        self.assertIn("只用于按钮展示", name_prompt["actions"][0]["text"])
        path_prompt = await self.feature.message({
            "text": "真人电影", "user_id": 1, "chat_id": 10,
        })
        self.assertIn("第二步", path_prompt["actions"][0]["text"])
        self.assertIn("单级目录", path_prompt["actions"][0]["text"])
        self.assertIn("真人电影", path_prompt["actions"][0]["text"])
        self.assertIn("series/live action", path_prompt["actions"][0]["text"])
        added = await self.feature.message({
            "text": "真人电影", "user_id": 1, "chat_id": 10,
        })
        self.assertIn("真人电影", str(
            added["actions"][0]["data"]["keyboard"]
        ))

        await self.feature.callback({
            "payload": "config:item:0", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "config:edit:name", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "电视剧", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "config:item:0", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "config:edit:path", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "tv/live action/", "user_id": 1, "chat_id": 10,
        })

        await self.feature.callback({
            "payload": "config:item:1", "user_id": 1, "chat_id": 10,
        })
        confirm = await self.feature.callback({
            "payload": "config:delete", "user_id": 1, "chat_id": 10,
        })
        self.assertIn("确认删除", confirm["actions"][0]["text"])
        self.assertEqual(len(
            self.feature.sessions[(10, 1)]["working_directories"]
        ), 3)
        await self.feature.callback({
            "payload": "config:delete:confirm", "user_id": 1, "chat_id": 10,
        })

        saved = await self.feature.callback({
            "payload": "config:save", "user_id": 1, "chat_id": 10,
        })
        expected = [
            {"name": "电视剧", "path": "tv/live action"},
            {"name": "真人电影", "path": "真人电影"},
        ]
        self.assertEqual(saved["session"]["state"], "close")
        self.assertEqual(self.feature.config_store.directory_writes, [expected])
        self.assertEqual(self.feature.config["save_directories"], expected)
        self.assertEqual(saved["operation"]["state"], "completed")

        magnet = await self.feature.command({
            "command": "magnet",
            "args": ["magnet:?xt=urn:btih:" + "4" * 40],
            "user_id": 1,
            "chat_id": 10,
        })
        magnet_keyboard = magnet["actions"][0]["data"]["keyboard"]
        self.assertIn("电视剧", str(magnet_keyboard))
        self.assertIn("真人电影", str(magnet_keyboard))

    async def test_directory_input_rejects_invalid_and_duplicate_values(self):
        original = [{"name": "剧集", "path": "series/live action"}]
        self.feature.config["save_directories"] = original
        self.feature.config_store.config["save_directories"] = original
        await self._open_directory_config()
        await self.feature.callback({
            "payload": "config:add", "user_id": 1, "chat_id": 10,
        })

        invalid_name = await self.feature.message({
            "text": "line-one\nline-two", "user_id": 1, "chat_id": 10,
        })
        self.assertIn("名称", invalid_name["actions"][0]["text"])
        await self.feature.message({
            "text": "电影", "user_id": 1, "chat_id": 10,
        })
        leading_slash = await self.feature.message({
            "text": "/movies", "user_id": 1, "chat_id": 10,
        })
        duplicate_path = await self.feature.message({
            "text": "series/live action/", "user_id": 1, "chat_id": 10,
        })
        self.assertIn("不要以 / 开头", leading_slash["actions"][0]["text"])
        self.assertIn("重复", duplicate_path["actions"][0]["text"])
        await self.feature.message({
            "text": "movies", "user_id": 1, "chat_id": 10,
        })

        await self.feature.callback({
            "payload": "config:add", "user_id": 1, "chat_id": 10,
        })
        duplicate_name = await self.feature.message({
            "text": "电影", "user_id": 1, "chat_id": 10,
        })
        self.assertIn("重复", duplicate_name["actions"][0]["text"])
        self.assertEqual(self.feature.config_store.directory_writes, [])

    async def test_directory_exit_and_q_discard_working_copy(self):
        for use_q in (False, True):
            with self.subTest(use_q=use_q):
                await self._open_directory_config()
                await self.feature.callback({
                    "payload": "config:add", "user_id": 1, "chat_id": 10,
                })
                await self.feature.message({
                    "text": "电影", "user_id": 1, "chat_id": 10,
                })
                await self.feature.message({
                    "text": "/Movies", "user_id": 1, "chat_id": 10,
                })
                if use_q:
                    response = await self.feature.command({
                        "command": "q", "user_id": 1, "chat_id": 10,
                    })
                else:
                    response = await self.feature.callback({
                        "payload": "exit", "user_id": 1, "chat_id": 10,
                    })
                self.assertEqual(response["session"]["state"], "close")
                self.assertEqual(self.feature.config_store.directory_writes, [])

    async def test_directory_session_timeout_discards_working_copy(self):
        from telepiplex_download import service

        with patch.object(service, "SESSION_TTL_SECONDS", 0):
            await self._open_directory_config()
            await asyncio.sleep(0.01)

        self.assertNotIn((10, 1), self.feature.sessions)
        self.assertEqual(self.feature.config_store.directory_writes, [])
        self.assertEqual(self.host.reports[-1]["state"], "cancelled")
        self.assertIn("目录配置", self.host.reports[-1]["status_text"])

    async def test_directory_save_failure_retains_old_config_and_working_copy(self):
        original = [{"name": "剧集", "path": "series"}]
        self.feature.config["save_directories"] = original
        self.feature.config_store.config["save_directories"] = original
        self.feature.config_store.fail_directory_writes = True
        await self._open_directory_config()
        await self.feature.callback({
            "payload": "config:add", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "电影", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "movies", "user_id": 1, "chat_id": 10,
        })

        response = await self.feature.callback({
            "payload": "config:save", "user_id": 1, "chat_id": 10,
        })

        self.assertEqual(response["session"]["state"], "open")
        self.assertEqual(response["operation"]["state"], "awaiting_input")
        self.assertEqual(self.feature.config["save_directories"], original)
        self.assertEqual(self.feature.sessions[(10, 1)]["stage"], "directory_list")
        self.assertEqual(len(
            self.feature.sessions[(10, 1)]["working_directories"]
        ), 2)
        self.assertNotIn("secret-value", str(response))

    async def test_direct_token_wizard_writes_only_after_refresh_and_activates_client(self):
        await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
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
            "command": "auth", "user_id": 1, "chat_id": 10,
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
            {"name": "剧集", "path": "series"},
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
            "command": "auth", "user_id": 1, "chat_id": 10,
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
            "command": "auth", "user_id": 1, "chat_id": 10,
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

    async def test_partial_token_write_failure_restores_exact_snapshot(self):
        class PartialWriteStore(FakeConfigStore):
            def write_tokens(self, *args, **kwargs):
                super().write_tokens(*args, **kwargs)
                raise OSError("chmod failed after replace")

        old = {
            "auth_mode": "direct",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "custom": "preserved",
        }
        self.feature.config_store = PartialWriteStore(old)
        self.feature.config.update(old)
        self.client.tokens = ("old-access", "old-refresh")
        await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
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
        self.assertEqual(self.feature.config_store.config, old)
        self.assertEqual(self.client.tokens, ("old-access", "old-refresh"))
        self.assertEqual(response["operation"]["state"], "awaiting_input")
        self.assertIn("原配置已恢复", response["operation"]["status_text"])

    async def test_rollback_after_token_write_before_terminal_commit_restores(self):
        old = {
            "auth_mode": "direct",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
        }
        self.feature.config_store = FakeConfigStore(old)
        self.feature.config.update(old)
        self.client.tokens = ("old-access", "old-refresh")
        await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "access-new", "user_id": 1, "chat_id": 10,
        })
        original_persist = self.feature._persist_tokens
        persisted = asyncio.Event()
        release = asyncio.Event()

        async def pause_after_persist(*args, **kwargs):
            result = await original_persist(*args, **kwargs)
            persisted.set()
            await release.wait()
            return result

        self.feature._persist_tokens = pause_after_persist
        completing = asyncio.create_task(self.feature.message({
            "text": "refresh-new", "user_id": 1, "chat_id": 10,
        }))
        await persisted.wait()
        operation_id = next(iter(self.feature.operations))

        accepted = await self.feature.operation_control({
            "operation_id": operation_id,
            "action": "rollback",
            "revision": self.feature.operations[operation_id]["revision"],
        })
        release.set()
        response = await completing

        self.assertEqual(accepted["operation"]["state"], "rolling_back")
        self.assertEqual(response["operation"]["state"], "rolled_back")
        self.assertEqual(self.feature.config_store.config, old)
        self.assertEqual(self.client.tokens, ("old-access", "old-refresh"))

        retried = await self.feature.operation_control({
            "operation_id": operation_id,
            "action": "rollback",
            "revision": response["operation"]["revision"],
        })
        self.assertEqual(retried["operation"]["state"], "rolled_back")

    async def test_pending_access_token_expires_without_writing(self):
        from telepiplex_download import service

        with patch.object(service, "SESSION_TTL_SECONDS", 0):
            await self.feature.command({
                "command": "auth", "user_id": 1, "chat_id": 10,
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
            "command": "auth", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "access-pending", "user_id": 1, "chat_id": 10,
        })
        self.feature.config["save_directories"] = [
            {"name": "剧集", "path": "series"},
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
            "command": "auth", "user_id": 9, "chat_id": 10,
        })
        scan = await self.feature.callback({
            "payload": "auth:scan", "user_id": 9, "chat_id": 10,
        })
        self.assertEqual(scan["actions"][0]["parse_mode"], "HTML")
        self.assertIn("<pre>", scan["actions"][0]["text"])
        auth_task_id = next(key for key in self.runtime.tasks if key.startswith("download-auth-"))
        await self.runtime.tasks.pop(auth_task_id)
        self.assertEqual(
            self.feature.config_store.writes[-1],
            ("scan-access", "scan-refresh", "scan"),
        )
        self.assertNotIn("scan-access", str(self.host.notifications))

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
            key for key in self.runtime.tasks if key.startswith("download-auth-")
        )
        await self.runtime.tasks.pop(auth_task_id)

        self.assertEqual(accepted["operation"]["state"], "cancelling")
        self.assertEqual(self.feature.config_store.writes, [])
        self.assertEqual(self.host.reports[-1]["state"], "cancelled")

    async def test_scan_token_persistence_cancel_restores_exact_snapshot(self):
        class BlockingConfigStore(FakeConfigStore):
            def __init__(self, config):
                super().__init__(config)
                self.written = threading.Event()
                self.release = threading.Event()

            def write_tokens(self, *args, **kwargs):
                result = super().write_tokens(*args, **kwargs)
                self.written.set()
                self.release.wait(1)
                return result

        old = {
            "app_id": "app-1",
            "auth_mode": "direct",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "custom": "preserved",
        }
        store = BlockingConfigStore(old)
        self.feature.config_store = store
        self.feature.config.update(old)
        self.client.tokens = ("old-access", "old-refresh")
        await self.feature.command({
            "command": "auth", "user_id": 9, "chat_id": 10,
        })
        scan = await self.feature.callback({
            "payload": "auth:scan", "user_id": 9, "chat_id": 10,
        })
        operation_id = scan["operation"]["operation_id"]
        task_id = next(
            key for key in self.runtime.tasks if key.startswith("download-auth-")
        )
        task = asyncio.create_task(self.runtime.tasks.pop(task_id))
        self.assertTrue(await asyncio.to_thread(store.written.wait, 1))

        accepted = await self.feature.operation_control({
            "operation_id": operation_id,
            "action": "rollback",
            "revision": self.feature.operations[operation_id]["revision"],
        })
        store.release.set()
        await task

        self.assertEqual(accepted["operation"]["state"], "rolling_back")
        self.assertEqual(store.config, old)
        self.assertEqual(self.feature.config, {
            "download_timeout": 30,
            "poll_interval": 0.01,
            **old,
        })
        self.assertEqual(self.client.tokens, ("old-access", "old-refresh"))
        self.assertEqual(self.host.reports[-1]["state"], "rolled_back")

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
        from telepiplex_download.config_store import FeatureConfigStore

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

    def test_save_directory_writeback_preserves_config_and_private_permissions(self):
        from telepiplex_download.config_store import FeatureConfigStore

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                "access_token: access\n"
                "refresh_token: refresh\n"
                "custom: keep\n"
                "save_directories: []\n",
                encoding="utf-8",
            )
            store = FeatureConfigStore(path)

            updated = store.write_save_directories([
                {"name": " 剧集 ", "path": " series/ "},
                {"name": "电影", "path": "movies"},
            ])

            self.assertEqual(updated["save_directories"], [
                {"name": "剧集", "path": "series"},
                {"name": "电影", "path": "movies"},
            ])
            self.assertEqual(updated["access_token"], "access")
            self.assertEqual(updated["refresh_token"], "refresh")
            self.assertEqual(updated["custom"], "keep")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_save_directory_writeback_rejects_invalid_and_duplicate_entries(self):
        from telepiplex_download.config_store import FeatureConfigStore

        with tempfile.TemporaryDirectory() as directory:
            store = FeatureConfigStore(Path(directory) / "config.yaml")
            invalid = (
                None,
                [{"name": "", "path": "series"}],
                [{"name": "剧集", "path": "/series"}],
                [{"name": "剧集", "path": "a", "extra": True}],
                [
                    {"name": "剧集", "path": "a"},
                    {"name": "剧集", "path": "b"},
                ],
                [
                    {"name": "A", "path": "series"},
                    {"name": "B", "path": "series/"},
                ],
            )
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    store.write_save_directories(value)

    def test_save_directory_writeback_normalizes_root_relative_paths(self):
        from telepiplex_download.config_store import FeatureConfigStore

        with tempfile.TemporaryDirectory() as directory:
            store = FeatureConfigStore(Path(directory) / "config.yaml")
            updated = store.write_save_directories([
                {"name": "剧集", "path": " series/live action/ "},
                {"name": "电影", "path": "movies"},
            ])

            self.assertEqual(updated["save_directories"], [
                {"name": "剧集", "path": "series/live action"},
                {"name": "电影", "path": "movies"},
            ])

    def test_save_directory_writeback_rejects_command_and_unsafe_paths(self):
        from telepiplex_download.config_store import FeatureConfigStore

        invalid_paths = (
            "/series",
            "/",
            "series//live action",
            "series//",
            "series///",
            ".",
            "..",
            "./series",
            "series/../live action",
            "series/./live action",
        )
        with tempfile.TemporaryDirectory() as directory:
            store = FeatureConfigStore(Path(directory) / "config.yaml")
            for value in invalid_paths:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    store.write_save_directories([{
                        "name": "剧集",
                        "path": value,
                    }])


class RuntimeStartupTest(unittest.TestCase):
    @staticmethod
    def _context(root: Path):
        return SimpleNamespace(
            manifest={"plugin_id": "download", "version": "1.0.0"},
            token="runtime-token",
            socket_path=root / "runtime.sock",
            host_socket_path=root / "host.sock",
            config_path=root / "config.yaml",
            state_path=root / "state",
            host=FakeHost(),
        )

    def test_runtime_startup_persists_canonical_save_directories(self):
        from telepiplex_download.runtime import main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self._context(root)
            context.config_path.write_text(
                yaml.safe_dump({
                    "save_directories": [
                        {"name": "剧集", "path": "series/live action/"},
                    ],
                }, allow_unicode=True),
                encoding="utf-8",
            )

            runtime = main(context)
            feature = runtime.commands["config"].__self__
            expected = [
                {"name": "剧集", "path": "series/live action"},
            ]

            self.assertEqual(feature.config["save_directories"], expected)
            on_disk = yaml.safe_load(
                context.config_path.read_text(encoding="utf-8")
            )
            self.assertEqual(on_disk["save_directories"], expected)

    def test_runtime_startup_rejects_canonical_duplicate_directories(self):
        from telepiplex_download.runtime import main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self._context(root)
            context.config_path.write_text(
                yaml.safe_dump({
                    "save_directories": [
                        {"name": "剧集", "path": "series/live action"},
                        {"name": "电影", "path": "series/live action/"},
                    ],
                }, allow_unicode=True),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unique"):
                main(context)


class FeatureSourceContractTest(unittest.TestCase):
    def test_schema_declares_custom_config_command_registered_by_manifest(self):
        schema = yaml.safe_load((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text(encoding="utf-8"))
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertEqual(schema["x-telepiplex-config-command"], "config")
        path_pattern = schema["properties"]["save_directories"]["items"][
            "properties"
        ]["path"]["pattern"]
        for value in ("series/live action", "series/live action/"):
            self.assertIsNotNone(re.fullmatch(path_pattern, value))
        for value in ("/series", "/", "series//live", ".", "series/../live"):
            self.assertIsNone(re.fullmatch(path_pattern, value))
        commands = [item["name"] for item in manifest["commands"]]
        self.assertNotIn("config", commands)
        self.assertIn("auth", commands)
        self.assertEqual(manifest["version"], "1.0.0")
        self.assertEqual(manifest["host_api"], ">=1.1,<2.0")
        self.assertEqual(manifest["config_schema_version"], 1)
        self.assertEqual(manifest["state_schema_version"], 1)
        self.assertEqual(project["project"]["version"], "1.0.0")
        self.assertEqual(
            project["project"]["dependencies"][0],
            "telepiplex-plugin-sdk==1.1.0",
        )
        self.assertIn("/tmp/download-1.0.0.tpx", readme)
        self.assertNotIn("dist/download-1.0.0.tpx", readme)
        self.assertIn("逐条新增、编辑和删除", readme)
        self.assertIn("series/live action", readme)
        self.assertIn("单级目录", readme)
        self.assertIn("真人电影", readme)
        self.assertIn("不要以 / 开头", readme)

    def test_source_has_no_host_telegram_or_init_imports(self):
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
