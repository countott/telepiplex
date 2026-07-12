import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml


class VersionClient:
    def __init__(self, version):
        self.version = version

    async def request(self, _method, params, *, deadline, idempotency_key=""):
        return {"version": self.version, "payload": params.get("payload")}


class FakeSupervisor:
    def __init__(self):
        self.active = {}
        self.instances = []
        self.drained = []
        self.stopped = []
        self.resumed = []
        self.unhealthy_versions = set()

    async def start(self, release, *, shadow=False):
        process = SimpleNamespace(
            plugin_id=release.plugin_id,
            release=release,
            client=VersionClient(release.version),
            state="healthy",
            shadow=shadow,
            restart_count=0,
            last_error="",
        )
        self.instances.append(process)
        if not shadow:
            self.active[release.plugin_id] = process
        return process

    def promote(self, process):
        process.shadow = False
        self.active[process.plugin_id] = process

    def process(self, plugin_id):
        return self.active.get(plugin_id)

    async def health(self, process):
        state = "failed" if process.release.version in self.unhealthy_versions else process.state
        return SimpleNamespace(
            state=state,
            active_tasks=0,
            restart_count=0,
            last_error="health failed" if state == "failed" else "",
        )

    async def drain(self, process, timeout):
        self.drained.append((process.plugin_id, process.release.version, timeout))
        process.state = "draining"
        return SimpleNamespace(
            state="draining",
            active_tasks=0,
            interrupted_task_ids=(),
        )

    async def resume(self, process):
        self.resumed.append((process.plugin_id, process.release.version))
        process.state = "healthy"
        return SimpleNamespace(state="healthy", active_tasks=0)

    async def stop(self, process, timeout=10):
        self.stopped.append((process.plugin_id, process.release.version))
        process.state = "stopped"
        if self.active.get(process.plugin_id) is process:
            self.active.pop(process.plugin_id, None)

    async def close_all(self):
        self.active.clear()


class PluginManagerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.core.capability_router import CapabilityRouter
        from app.core.event_journal import EventJournal
        from app.core.plugin_manager import PluginManager
        from app.core.plugin_store import PluginStore

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = PluginStore(self.root / "plugins")
        self.supervisor = FakeSupervisor()
        self.router = CapabilityRouter()
        self.journal = EventJournal(self.root / "core.db")
        self.manager = PluginManager(
            store=self.store,
            supervisor=self.supervisor,
            router=self.router,
            journal=self.journal,
            venv_installer=self._install_venv,
            stabilize_seconds=0,
            drain_timeout=0.2,
        )

    async def asyncTearDown(self):
        self.journal.close()
        await self.supervisor.close_all()
        self.temp.cleanup()

    async def _install_venv(self, staged):
        executable = staged.path / "venv/bin/python"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)

    def _artifact(
        self,
        plugin_id="echo",
        version="1.0.0",
        *,
        core_api=">=1.0,<2.0",
        provides=(("demo.echo", True),),
        requires=(),
        commands=("echo",),
        commit="a" * 40,
    ):
        from app.core.plugin_artifact import build_tpx

        source = self.root / f"source-{plugin_id}-{version}"
        (source / "wheelhouse").mkdir(parents=True)
        manifest = {
            "plugin_id": plugin_id,
            "name": plugin_id,
            "version": version,
            "core_api": core_api,
            "entry_point": f"telepiplex_{plugin_id.replace('-', '_')}.runtime:main",
            "provides": [
                {"name": name, "exclusive": exclusive}
                for name, exclusive in provides
            ],
            "requires": list(requires),
            "subscribes": [],
            "publishes": [],
            "commands": [
                {"name": name, "description": name}
                for name in commands
            ],
            "callbacks": [],
            "source": {
                "repository": "origin",
                "branch": f"feature/{plugin_id}",
                "commit": commit,
            },
        }
        (source / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8"
        )
        (source / "plugin.whl").write_bytes(b"plugin")
        (source / "wheelhouse/sdk.whl").write_bytes(b"sdk")
        (source / "config.schema.json").write_text(
            json.dumps({"type": "object", "additionalProperties": False}),
            encoding="utf-8",
        )
        (source / "config.default.yaml").write_text("{}\n", encoding="utf-8")
        return build_tpx(source, self.root / f"{plugin_id}-{version}.tpx")

    async def test_install_activates_only_after_health_and_route_validation(self):
        result = await self.manager.install(self._artifact())

        self.assertEqual(result.state, "active")
        self.assertEqual(result.plugin_id, "echo")
        self.assertEqual(result.version, "1.0.0")
        self.assertTrue(self.store.active("echo").enabled)
        self.assertEqual(self.router.snapshot.capabilities["demo.echo"].plugin_id, "echo")
        self.assertEqual(self.supervisor.process("echo").release.version, "1.0.0")

    async def test_incompatible_core_and_venv_failure_leave_no_active_record(self):
        from app.core.plugin_manager import PluginOperationError, PluginManager

        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.install(self._artifact(core_api=">=2.0,<3.0"))
        self.assertEqual(raised.exception.code, "incompatible_core")
        self.assertIsNone(self.store.active("echo"))

        async def fail_install(_staged):
            raise RuntimeError("pip api_key=secret")

        failing = PluginManager(
            store=self.store,
            supervisor=self.supervisor,
            router=self.router,
            journal=self.journal,
            venv_installer=fail_install,
            stabilize_seconds=0,
        )
        with self.assertRaises(PluginOperationError) as raised:
            await failing.install(self._artifact(plugin_id="broken", provides=(), commands=()))
        self.assertEqual(raised.exception.code, "install_failed")
        self.assertNotIn("secret", str(raised.exception))
        self.assertIsNone(self.store.active("broken"))

    async def test_missing_capability_stops_shadow_and_does_not_activate(self):
        from app.core.plugin_manager import PluginOperationError

        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.install(self._artifact(
                plugin_id="consumer",
                provides=(),
                requires=("storage.provider",),
                commands=("consume",),
            ))

        self.assertEqual(raised.exception.code, "missing_capability")
        self.assertIsNone(self.store.active("consumer"))
        self.assertIn(("consumer", "1.0.0"), self.supervisor.stopped)
        self.assertIsNone(self.router.command_route("consume"))

    async def test_disable_and_enable_switch_routes_without_core_restart(self):
        import os

        core_pid = os.getpid()
        await self.manager.install(self._artifact())

        disabled = await self.manager.disable("echo")
        self.assertEqual(disabled.state, "disabled")
        self.assertFalse(self.store.active("echo").enabled)
        self.assertNotIn("demo.echo", self.router.snapshot.capabilities)

        enabled = await self.manager.enable("echo")
        self.assertEqual(enabled.state, "active")
        self.assertTrue(self.store.active("echo").enabled)
        self.assertIn("demo.echo", self.router.snapshot.capabilities)
        self.assertEqual(os.getpid(), core_pid)

    async def test_update_drains_old_switches_atomically_and_supports_rollback(self):
        await self.manager.install(self._artifact("echo", "1.0.0", commit="a" * 40))
        old = self.supervisor.process("echo")

        updated = await self.manager.update(
            self._artifact("echo", "2.0.0", commit="b" * 40)
        )

        self.assertEqual(updated.version, "2.0.0")
        self.assertEqual(self.store.active("echo").previous_version, "1.0.0")
        self.assertEqual(old.state, "stopped")
        self.assertEqual(self.router.snapshot.capabilities["demo.echo"].client.version, "2.0.0")

        rolled_back = await self.manager.rollback("echo")
        self.assertEqual(rolled_back.version, "1.0.0")
        self.assertEqual(self.store.active("echo").previous_version, "2.0.0")
        self.assertEqual(self.router.snapshot.capabilities["demo.echo"].client.version, "1.0.0")

    async def test_failed_stabilization_restores_old_routes_process_and_record(self):
        from app.core.plugin_manager import PluginOperationError

        await self.manager.install(self._artifact("echo", "1.0.0"))
        old = self.supervisor.process("echo")
        self.supervisor.unhealthy_versions.add("2.0.0")

        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.update(self._artifact("echo", "2.0.0", commit="b" * 40))

        self.assertEqual(raised.exception.code, "stabilization_failed")
        self.assertEqual(self.store.active("echo").version, "1.0.0")
        self.assertEqual(self.router.snapshot.capabilities["demo.echo"].client.version, "1.0.0")
        self.assertEqual(old.state, "healthy")
        self.assertIn(("echo", "1.0.0"), self.supervisor.resumed)
        new = next(item for item in self.supervisor.instances if item.release.version == "2.0.0")
        self.assertEqual(new.state, "stopped")

    async def test_remove_refuses_provider_required_by_an_active_feature(self):
        from app.core.plugin_manager import PluginOperationError

        await self.manager.install(self._artifact(
            plugin_id="storage",
            provides=(("storage.provider", True),),
            commands=("storage",),
        ))
        await self.manager.install(self._artifact(
            plugin_id="consumer",
            provides=(),
            requires=("storage.provider",),
            commands=("consume",),
            commit="b" * 40,
        ))

        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.remove("storage")

        self.assertEqual(raised.exception.code, "required_by_plugin")
        self.assertIn("consumer", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
