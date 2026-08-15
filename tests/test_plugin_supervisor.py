import asyncio
import os
import json
import logging
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/plugin_processes/fake_python.py"


class PluginSupervisorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.supervisors = []

    async def asyncTearDown(self):
        for supervisor in self.supervisors:
            await supervisor.close_all()
        for handler in list(logging.getLogger().handlers):
            if getattr(handler, "_telepiplex_handler_kind", ""):
                logging.getLogger().removeHandler(handler)
                handler.close()
        self.temp.cleanup()

    def _release(self, plugin_id, root_name="plugins"):
        from app.runtime.plugin_manifest import PluginManifest
        from app.runtime.plugin_store import ActiveRelease

        manifest = PluginManifest.from_mapping({
            "plugin_id": plugin_id,
            "name": plugin_id,
            "version": "1.0.0",
            "host_api": ">=1.0,<2.0",
            "entry_point": f"telepiplex_{plugin_id}.runtime:main",
            "provides": [],
            "requires": [],
            "subscribes": [],
            "publishes": [],
            "commands": [],
            "callbacks": [],
            "source": {
                "repository": "origin",
                "branch": f"feature/{plugin_id}",
                "commit": "a" * 40,
            },
        })
        path = self.root / root_name / plugin_id / "releases/1.0.0"
        executable = path / "venv/bin/python"
        executable.parent.mkdir(parents=True)
        shutil.copy2(FIXTURE, executable)
        executable.chmod(0o755)
        return ActiveRelease(
            plugin_id=plugin_id,
            version="1.0.0",
            path=path,
            manifest=manifest,
            artifact_sha256="a" * 64,
        )

    def _supervisor(self, **kwargs):
        from app.runtime.plugin_supervisor import PluginSupervisor

        supervisor = PluginSupervisor(
            startup_timeout=kwargs.pop("startup_timeout", 5),
            restart_limit=kwargs.pop("restart_limit", 2),
            restart_backoff=kwargs.pop("restart_backoff", 0.01),
            runtime_root=kwargs.pop("runtime_root", self.root / "runtime"),
            **kwargs,
        )
        self.supervisors.append(supervisor)
        return supervisor

    def _log_session(self, session_id="SUPERVISOR"):
        from app.utils.logger import create_log_session, configure_root_logger

        session = create_log_session(self.root, session_id=session_id)
        configure_root_logger(session=session)
        return session

    async def test_starts_real_child_health_drains_and_stops(self):
        import asyncio

        supervisor = self._supervisor()
        host_pid = os.getpid()

        process = await supervisor.start(self._release("healthy"))

        self.assertEqual(process.state, "healthy")
        self.assertNotEqual(process.pid, host_pid)
        health = await supervisor.health("healthy")
        self.assertEqual(health.state, "healthy")
        started_at = asyncio.get_running_loop().time()
        drained = await supervisor.drain("healthy", timeout=1)
        self.assertEqual(drained.state, "draining")
        self.assertEqual(drained.active_tasks, 0)
        self.assertEqual(drained.interrupted_task_ids, ())
        self.assertGreaterEqual(asyncio.get_running_loop().time() - started_at, 0.04)
        resumed = await supervisor.resume("healthy")
        self.assertEqual(resumed.state, "healthy")
        await supervisor.stop("healthy")
        self.assertEqual(process.state, "stopped")
        self.assertEqual(os.getpid(), host_pid)

    async def test_startup_token_is_redacted_from_captured_logs(self):
        supervisor = self._supervisor()
        process = await supervisor.start(self._release("secretlog"))
        await self._wait_for(lambda: process.logs)

        output = "\n".join(process.logs)
        self.assertNotIn(process.startup_token, output)
        self.assertIn("***redacted***", output)

    async def test_captured_logs_are_persisted_to_runtime_log(self):
        session = self._log_session()
        supervisor = self._supervisor(log_session=session)
        process = await supervisor.start(self._release("healthy"))
        runtime_log = session.directory / "feature-healthy.human.log"

        await self._wait_for(runtime_log.exists)
        await self._wait_for(lambda: runtime_log.read_text(encoding="utf-8").strip() != "")

        output = runtime_log.read_text(encoding="utf-8")
        self.assertIn("feature_runtime_started", output)
        self.assertIn("plugin_id=healthy", output)

    async def test_structured_feature_log_levels_are_preserved_in_runtime_log(self):
        session = self._log_session()
        supervisor = self._supervisor(log_session=session)
        process = await supervisor.start(self._release("severitylogs"))
        runtime_log = session.directory / "feature-severitylogs.machine.jsonl"

        await self._wait_for(lambda: len(process.logs) >= 5)
        await self._wait_for(runtime_log.exists)
        await self._wait_for(
            lambda: "structured critical" in runtime_log.read_text(encoding="utf-8")
        )

        output = runtime_log.read_text(encoding="utf-8")
        events = [json.loads(line) for line in output.splitlines() if line.strip()]
        by_message = {event["event"]["message"]: event for event in events}
        self.assertEqual(by_message["[2026-07-26 08:00:00] [WARNING] [feature.example] structured warning"]["level"], "WARNING")
        self.assertEqual(by_message["[2026-07-26 08:00:01] [ERROR] [feature.example] structured error"]["level"], "ERROR")
        self.assertEqual(by_message["[2026-07-26 08:00:02] [CRITICAL] [feature.example] structured critical"]["level"], "CRITICAL")
        self.assertEqual(by_message["plain stdout"]["level"], "INFO")
        self.assertEqual(by_message["plain stderr"]["level"], "WARNING")

    async def test_feature_transport_is_fanned_out_with_the_same_event_id_in_one_session_folder(self):
        session = self._log_session("TRANSPORT")
        supervisor = self._supervisor(log_session=session)
        process = await supervisor.start(self._release("diagnosticlog"))
        feature_machine = session.directory / "feature-diagnosticlog.machine.jsonl"

        await self._wait_for(feature_machine.exists)
        await self._wait_for(
            lambda: "EVT-FEATURE-TRANSPORT-1" in feature_machine.read_text(encoding="utf-8")
        )

        feature_events = [
            json.loads(line)
            for line in feature_machine.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        global_events = [
            json.loads(line)
            for line in session.machine_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        feature_event = next(
            event for event in feature_events
            if event["event_id"] == "EVT-FEATURE-TRANSPORT-1"
        )
        global_event = next(
            event for event in global_events
            if event["event_id"] == "EVT-FEATURE-TRANSPORT-1"
        )
        self.assertEqual(feature_event["event_id"], global_event["event_id"])
        self.assertEqual(feature_event["identity"]["session_id"], "TRANSPORT")
        self.assertEqual(global_event["identity"]["session_id"], "TRANSPORT")
        self.assertEqual(feature_event["identity"]["trace_id"], "TRC-FEATURE-1")
        self.assertEqual(
            feature_event["facts"]["input"]["args"],
            ["access_token=***redacted***"],
        )
        self.assertNotIn(
            "transport-secret-value",
            feature_machine.read_text(encoding="utf-8"),
        )
        self.assertNotIn("transport-secret-value", "\n".join(process.logs))
        self.assertEqual(feature_machine.parent, session.directory)
        self.assertTrue((session.directory / "feature-diagnosticlog.human.log").is_file())
        gap = next(
            event for event in feature_events
            if event["event"]["name"] == "diagnostics.event_gap"
        )
        self.assertEqual(gap["facts"]["input"]["expected_sequence"], 1)
        self.assertEqual(gap["facts"]["input"]["received_sequence"], 9)

    async def test_oversized_legacy_stdout_lines_do_not_abandon_capture_or_block_health(self):
        supervisor = self._supervisor(startup_timeout=2)
        process = await supervisor.start(self._release("oversizeflood"))

        await self._wait_for(
            lambda: any("oversize flood complete" in line for line in process.logs),
            timeout=2,
        )
        health = await asyncio.wait_for(supervisor.health("oversizeflood"), timeout=1)

        self.assertEqual(process.state, "healthy")
        self.assertEqual(health.state, "healthy")
        self.assertTrue(all(not task.done() for task in process.log_tasks))
        self.assertTrue(any(
            "Feature output line omitted bytes=" in line
            for line in process.logs
        ))

    async def test_startup_timeout_terminates_child_and_leaves_no_registration(self):
        from app.runtime.plugin_supervisor import SupervisorError

        supervisor = self._supervisor(startup_timeout=0.05)
        with self.assertRaises(SupervisorError) as raised:
            await supervisor.start(self._release("nosocket"))

        self.assertEqual(raised.exception.code, "startup_failed")
        self.assertIsNone(supervisor.process("nosocket"))

    async def test_repeated_crash_is_quarantined_without_stopping_healthy_peer(self):
        supervisor = self._supervisor(restart_limit=2, restart_backoff=0.01)
        healthy = await supervisor.start(self._release("healthy"))
        crashy = await supervisor.start(self._release("crashy"))

        await self._wait_for(lambda: crashy.state == "quarantined", timeout=2)

        self.assertEqual(crashy.restart_count, 2)
        self.assertEqual(crashy.state, "quarantined")
        self.assertEqual((await supervisor.health("healthy")).state, "healthy")
        self.assertEqual(healthy.state, "healthy")

    async def test_process_launch_does_not_interpret_shell_characters_in_paths(self):
        supervisor = self._supervisor()
        release = self._release("healthy", root_name="plugins;touch SHOULD_NOT_EXIST")
        marker = self.root / "SHOULD_NOT_EXIST"

        process = await supervisor.start(release, shadow=True)

        self.assertEqual(process.state, "healthy")
        self.assertFalse(marker.exists())
        self.assertEqual(process.argv[1:], ("-m", "telepiplex_plugin_sdk.runner"))
        self.assertLess(len(str(process.socket_path).encode("utf-8")), 104)

    async def test_registers_and_revokes_rotating_token_with_runtime_broker(self):
        broker = Mock()
        broker.socket_path = self.root / "runtime/host.sock"
        supervisor = self._supervisor(broker=broker)
        release = self._release("healthy")

        process = await supervisor.start(release)
        broker.register.assert_called_once_with(
            "healthy", process.startup_token, release.manifest
        )

        await supervisor.stop(process)
        broker.unregister.assert_called_with(process.startup_token)

    async def test_route_client_follows_client_rotation_after_restart(self):
        from app.runtime.plugin_supervisor import RoutedPluginClient

        from unittest.mock import AsyncMock
        first = Mock()
        first.request = AsyncMock(return_value="first")
        second = Mock()
        second.request = AsyncMock(return_value="second")
        process = Mock(client=first)
        routed = RoutedPluginClient(process)

        self.assertEqual(await routed.request("health", {}), "first")
        process.client = second
        self.assertEqual(await routed.request("health", {}), "second")

    async def test_successful_crash_restart_notifies_reconciliation_listener(self):
        import asyncio

        reconciled = asyncio.Event()
        observed = []

        async def listener(process):
            observed.append(process.plugin_id)
            reconciled.set()

        supervisor = self._supervisor(restart_listener=listener)
        process = await supervisor.start(self._release("healthy"))
        process.child.terminate()

        await asyncio.wait_for(reconciled.wait(), timeout=2)

        self.assertEqual(observed, ["healthy"])
        self.assertEqual(process.state, "healthy")
        self.assertEqual(process.restart_count, 1)

    async def _wait_for(self, predicate, timeout=1):
        import asyncio

        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
