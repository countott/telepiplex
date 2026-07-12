import os
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
        self.temp.cleanup()

    def _release(self, plugin_id, root_name="plugins"):
        from app.core.plugin_manifest import PluginManifest
        from app.core.plugin_store import ActiveRelease

        manifest = PluginManifest.from_mapping({
            "plugin_id": plugin_id,
            "name": plugin_id,
            "version": "1.0.0",
            "core_api": ">=1.0,<2.0",
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
        from app.core.plugin_supervisor import PluginSupervisor

        supervisor = PluginSupervisor(
            startup_timeout=kwargs.pop("startup_timeout", 1),
            restart_limit=kwargs.pop("restart_limit", 2),
            restart_backoff=kwargs.pop("restart_backoff", 0.01),
            runtime_root=kwargs.pop("runtime_root", self.root / "runtime"),
            **kwargs,
        )
        self.supervisors.append(supervisor)
        return supervisor

    async def test_starts_real_child_health_drains_and_stops(self):
        import asyncio

        supervisor = self._supervisor()
        core_pid = os.getpid()

        process = await supervisor.start(self._release("healthy"))

        self.assertEqual(process.state, "healthy")
        self.assertNotEqual(process.pid, core_pid)
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
        self.assertEqual(os.getpid(), core_pid)

    async def test_startup_token_is_redacted_from_captured_logs(self):
        supervisor = self._supervisor()
        process = await supervisor.start(self._release("secretlog"))
        await self._wait_for(lambda: process.logs)

        output = "\n".join(process.logs)
        self.assertNotIn(process.startup_token, output)
        self.assertIn("***redacted***", output)

    async def test_startup_timeout_terminates_child_and_leaves_no_registration(self):
        from app.core.plugin_supervisor import SupervisorError

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

    async def test_registers_and_revokes_rotating_token_with_core_broker(self):
        broker = Mock()
        broker.socket_path = self.root / "runtime/core.sock"
        supervisor = self._supervisor(broker=broker)
        release = self._release("healthy")

        process = await supervisor.start(release)
        broker.register.assert_called_once_with(
            "healthy", process.startup_token, release.manifest
        )

        await supervisor.stop(process)
        broker.unregister.assert_called_with(process.startup_token)

    async def test_route_client_follows_client_rotation_after_restart(self):
        from app.core.plugin_supervisor import RoutedPluginClient

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

    async def _wait_for(self, predicate, timeout=1):
        import asyncio

        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
