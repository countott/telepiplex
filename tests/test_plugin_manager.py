import json
import asyncio
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

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
        self.busy_versions = set()

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
            active_tasks=int(process.release.version in self.busy_versions),
            interrupted_task_ids=("active-job",) if process.release.version in self.busy_versions else (),
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
        config_schema=None,
        config_default=None,
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
            json.dumps(config_schema or {"type": "object", "additionalProperties": False}),
            encoding="utf-8",
        )
        (source / "config.default.yaml").write_text(
            yaml.safe_dump(config_default or {}, sort_keys=True),
            encoding="utf-8",
        )
        return build_tpx(source, self.root / f"{plugin_id}-{version}.tpx")

    @staticmethod
    def _editable_config():
        return (
            {
                "type": "object",
                "properties": {"prefix": {"type": "string"}},
                "required": ["prefix"],
                "additionalProperties": False,
            },
            {"prefix": "old"},
        )

    async def test_install_activates_only_after_health_and_route_validation(self):
        result = await self.manager.install(self._artifact())

        self.assertEqual(result.state, "active")
        self.assertEqual(result.plugin_id, "echo")
        self.assertEqual(result.version, "1.0.0")
        self.assertTrue(self.store.active("echo").enabled)
        self.assertEqual(self.router.snapshot.capabilities["demo.echo"].plugin_id, "echo")
        self.assertEqual(self.supervisor.process("echo").release.version, "1.0.0")

    async def test_config_view_and_configure_reload_running_feature_atomically(self):
        schema, default = self._editable_config()
        await self.manager.install(self._artifact(
            config_schema=schema,
            config_default=default,
        ))
        old = self.supervisor.process("echo")

        view = self.manager.config("echo")
        self.assertEqual(view["config"], {"prefix": "old"})
        self.assertEqual(view["schema"]["properties"]["prefix"]["type"], "string")

        result = await self.manager.configure("echo", {"prefix": "new"})

        self.assertEqual(result.state, "active")
        self.assertTrue(result.details["restarted"])
        self.assertEqual(self.store.read_config(self.store.active("echo")), {"prefix": "new"})
        self.assertEqual(old.state, "stopped")
        self.assertIsNot(self.supervisor.process("echo"), old)

    async def test_reload_config_reads_manual_yaml_and_restarts_running_feature(self):
        schema, default = self._editable_config()
        await self.manager.install(self._artifact(
            config_schema=schema,
            config_default=default,
        ))
        old = self.supervisor.process("echo")
        config_path = self.root / "plugins/echo/config.yaml"
        config_path.write_text("prefix: manually-edited\n", encoding="utf-8")

        result = await self.manager.reload_config("echo")

        self.assertEqual(result.state, "active")
        self.assertTrue(result.details["restarted"])
        self.assertEqual(
            self.store.read_config(self.store.active("echo")),
            {"prefix": "manually-edited"},
        )
        self.assertEqual(old.state, "stopped")

    async def test_reload_config_rejects_invalid_manual_yaml_without_stopping_feature(self):
        from app.core.plugin_manager import PluginOperationError

        schema, default = self._editable_config()
        await self.manager.install(self._artifact(
            config_schema=schema,
            config_default=default,
        ))
        old = self.supervisor.process("echo")
        config_path = self.root / "plugins/echo/config.yaml"
        config_path.write_text("prefix: [\n", encoding="utf-8")

        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.reload_config("echo")

        self.assertEqual(raised.exception.code, "invalid_config")
        self.assertIs(self.supervisor.process("echo"), old)
        self.assertEqual(old.state, "healthy")

    async def test_reload_config_restores_last_running_config_when_shadow_is_unhealthy(self):
        from app.core.plugin_manager import PluginOperationError

        schema, default = self._editable_config()
        await self.manager.install(self._artifact(
            config_schema=schema,
            config_default=default,
        ))
        old = self.supervisor.process("echo")
        config_path = self.root / "plugins/echo/config.yaml"
        config_path.write_text("prefix: manually-edited\n", encoding="utf-8")
        self.supervisor.unhealthy_versions.add("1.0.0")

        with self.assertRaises(PluginOperationError):
            await self.manager.reload_config("echo")

        self.assertEqual(yaml.safe_load(config_path.read_text()), {"prefix": "old"})
        self.assertIs(self.supervisor.process("echo"), old)
        self.assertEqual(old.state, "healthy")

    async def test_config_state_reports_custom_command_and_invalid_live_config(self):
        schema, default = self._editable_config()
        schema["x-telepiplex-config-command"] = "configure_echo"
        await self.manager.install(self._artifact(
            commands=("echo", "configure_echo"),
            config_schema=schema,
            config_default=default,
        ))

        state = self.manager.config_state("echo")
        self.assertTrue(state["configurable"])
        self.assertEqual(state["command"], "configure_echo")
        self.assertEqual(state["state"], "configurable")

        config_path = self.root / "plugins/echo/config.yaml"
        config_path.write_text("unknown: true\n", encoding="utf-8")
        broken = self.manager.config_state("echo")
        self.assertFalse(broken["configurable"])
        self.assertEqual(broken["state"], "invalid_config")
        self.assertEqual(broken["error_code"], "invalid_config")

    async def test_config_state_reports_invalid_config_even_when_route_is_unavailable(self):
        schema, default = self._editable_config()
        schema["x-telepiplex-config-command"] = "configure_echo"
        await self.manager.install(self._artifact(
            commands=("echo", "configure_echo"),
            config_schema=schema,
            config_default=default,
        ))
        self.router.deactivate("echo")
        config_path = self.root / "plugins/echo/config.yaml"
        config_path.write_text("unknown: true\n", encoding="utf-8")

        state = self.manager.config_state("echo")

        self.assertEqual(state["state"], "invalid_config")
        self.assertEqual(state["error_code"], "invalid_config")

    async def test_configure_refuses_busy_feature_without_changing_config(self):
        from app.core.plugin_manager import PluginOperationError

        schema, default = self._editable_config()
        await self.manager.install(self._artifact(
            config_schema=schema,
            config_default=default,
        ))
        old = self.supervisor.process("echo")
        self.supervisor.busy_versions.add("1.0.0")

        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.configure("echo", {"prefix": "new"})

        self.assertEqual(raised.exception.code, "drain_timeout")
        self.assertEqual(self.store.read_config(self.store.active("echo")), {"prefix": "old"})
        self.assertIs(self.supervisor.process("echo"), old)
        self.assertIn(("echo", "1.0.0"), self.supervisor.resumed)

    async def test_configure_failed_shadow_restores_old_config_and_route(self):
        from app.core.plugin_manager import PluginOperationError

        schema, default = self._editable_config()
        await self.manager.install(self._artifact(
            config_schema=schema,
            config_default=default,
        ))
        old = self.supervisor.process("echo")
        self.supervisor.unhealthy_versions.add("1.0.0")

        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.configure("echo", {"prefix": "new"})

        self.assertEqual(raised.exception.code, "stabilization_failed")
        self.assertEqual(self.store.read_config(self.store.active("echo")), {"prefix": "old"})
        self.assertIs(self.supervisor.process("echo"), old)
        self.assertEqual(old.state, "healthy")
        self.assertIn(("echo", "1.0.0"), self.supervisor.resumed)

    async def test_configure_rejects_invalid_value_before_draining(self):
        from app.core.plugin_manager import PluginOperationError

        schema, default = self._editable_config()
        await self.manager.install(self._artifact(
            config_schema=schema,
            config_default=default,
        ))

        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.configure("echo", {"prefix": 123})

        self.assertEqual(raised.exception.code, "invalid_config")
        self.assertEqual(self.supervisor.drained, [])
        self.assertEqual(self.store.read_config(self.store.active("echo")), {"prefix": "old"})

    async def test_install_resolves_name_and_uses_catalog_digest(self):
        from app.core.plugin_catalog import ResolvedArtifact

        artifact = self._artifact()
        calls = []

        class Resolver:
            async def resolve(_self, reference):
                calls.append(reference)
                return ResolvedArtifact(artifact, "")

        self.manager._artifact_resolver = Resolver()
        result = await self.manager.install("echo@1.0.0")

        self.assertEqual(calls, ["echo@1.0.0"])
        self.assertEqual(result.plugin_id, "echo")

    async def test_artifact_verification_and_extraction_do_not_block_core_loop(self):
        original_stage = self.store.stage

        def slow_stage(artifact):
            time.sleep(0.1)
            return original_stage(artifact)

        self.store.stage = slow_stage
        ticks = []
        running = True

        async def heartbeat():
            while running:
                ticks.append(asyncio.get_running_loop().time())
                await asyncio.sleep(0.01)

        ticker = asyncio.create_task(heartbeat())
        try:
            await self.manager.install(self._artifact(
                plugin_id="responsive",
                provides=(("demo.responsive", True),),
                commands=("responsive",),
            ))
        finally:
            running = False
            await ticker

        self.assertGreaterEqual(len(ticks), 5)

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

    async def test_update_adds_nested_defaults_without_overwriting_user_values(self):
        old_schema = {
            "type": "object",
            "properties": {
                "service": {
                    "type": "object",
                    "properties": {
                        "api_key": {"type": "string"},
                        "endpoint": {"type": "string"},
                        "categories": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["api_key", "endpoint", "categories"],
                    "additionalProperties": False,
                }
            },
            "required": ["service"],
            "additionalProperties": False,
        }
        new_schema = deepcopy(old_schema)
        new_schema["properties"]["service"]["properties"]["timeout"] = {
            "type": "integer"
        }
        new_schema["properties"]["service"]["required"].append("timeout")
        old_default = {
            "service": {
                "api_key": "",
                "endpoint": "https://old",
                "categories": ["old-default"],
            }
        }
        new_default = {
            "service": {
                "api_key": "",
                "endpoint": "https://new",
                "categories": ["new-default"],
                "timeout": 30,
            }
        }
        await self.manager.install(self._artifact(
            "echo", "1.0.0", config_schema=old_schema,
            config_default=old_default,
        ))
        await self.manager.configure("echo", {
            "service": {
                "api_key": "operator-secret",
                "endpoint": "https://operator",
                "categories": ["operator"],
            }
        })
        started_configs = []
        original_start = self.supervisor.start

        async def observe_start(release, *, shadow=False):
            started_configs.append(self.store.read_config(release))
            return await original_start(release, shadow=shadow)

        self.supervisor.start = observe_start

        result = await self.manager.update(self._artifact(
            "echo", "2.0.0", commit="b" * 40,
            config_schema=new_schema, config_default=new_default,
        ))

        active = self.store.active("echo")
        self.assertEqual(self.store.read_config(active), {
            "service": {
                "api_key": "operator-secret",
                "endpoint": "https://operator",
                "categories": ["operator"],
                "timeout": 30,
            }
        })
        example = self.root / "plugins/echo/config.yaml.example"
        self.assertEqual(yaml.safe_load(example.read_text()), new_default)
        self.assertEqual(result.details["config_added_keys"], ["service.timeout"])
        self.assertEqual(started_configs, [self.store.read_config(active)])

    async def test_update_reports_manual_migration_without_exposing_values(self):
        old_schema, old_default = self._editable_config()
        new_schema = deepcopy(old_schema)
        new_schema["properties"]["prefix"] = {"type": "integer"}
        await self.manager.install(self._artifact(
            "echo", "1.0.0", config_schema=old_schema,
            config_default=old_default,
        ))
        await self.manager.configure("echo", {"prefix": "operator-secret"})

        from app.core.plugin_manager import PluginOperationError
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.update(self._artifact(
                "echo", "2.0.0", commit="b" * 40,
                config_schema=new_schema, config_default={"prefix": 1},
            ))

        self.assertEqual(raised.exception.code, "config_migration_required")
        self.assertNotIn("operator-secret", str(raised.exception))
        self.assertEqual(self.store.active("echo").version, "1.0.0")
        self.assertEqual(
            self.store.read_config(self.store.active("echo")),
            {"prefix": "operator-secret"},
        )

    async def test_update_reports_migration_required_for_damaged_active_config(self):
        old_schema, old_default = self._editable_config()
        await self.manager.install(self._artifact(
            "echo", "1.0.0", config_schema=old_schema,
            config_default=old_default,
        ))
        config_path = self.root / "plugins/echo/config.yaml"

        from app.core.plugin_manager import PluginOperationError
        cases = (
            ("2.0.0", "malformed"),
            ("3.0.0", "unreadable"),
            ("4.0.0", "missing"),
        )
        for version, damaged in cases:
            with self.subTest(damaged=damaged):
                if config_path.is_dir():
                    config_path.rmdir()
                if damaged == "malformed":
                    config_path.write_text("prefix: [\n", encoding="utf-8")
                else:
                    config_path.unlink(missing_ok=True)
                    if damaged == "unreadable":
                        config_path.mkdir()
                with self.assertRaises(PluginOperationError) as raised:
                    await self.manager.update(self._artifact(
                        "echo", version, commit="b" * 40,
                        config_schema=old_schema, config_default=old_default,
                    ))
                self.assertEqual(
                    raised.exception.code,
                    "config_migration_required",
                )

    async def test_rollback_restores_config_before_new_keys_were_added(self):
        old_schema, old_default = self._editable_config()
        new_schema = deepcopy(old_schema)
        new_schema["properties"]["timeout"] = {"type": "integer"}
        new_schema["required"].append("timeout")
        await self.manager.install(self._artifact(
            "echo", "1.0.0", config_schema=old_schema,
            config_default=old_default,
        ))
        await self.manager.configure("echo", {"prefix": "operator"})
        await self.manager.update(self._artifact(
            "echo", "2.0.0", commit="b" * 40,
            config_schema=new_schema,
            config_default={"prefix": "v2", "timeout": 30},
        ))

        result = await self.manager.rollback("echo")

        active = self.store.active("echo")
        self.assertEqual(result.version, "1.0.0")
        self.assertEqual(self.store.read_config(active), {"prefix": "operator"})
        self.assertEqual(
            yaml.safe_load((
                self.root / "plugins/echo/config.yaml.example"
            ).read_text()),
            old_default,
        )

    async def test_rollback_reports_migration_required_for_damaged_snapshot(self):
        old_schema, old_default = self._editable_config()
        new_schema = deepcopy(old_schema)
        new_schema["properties"]["timeout"] = {"type": "integer"}
        new_schema["required"].append("timeout")
        await self.manager.install(self._artifact(
            "echo", "1.0.0", config_schema=old_schema,
            config_default=old_default,
        ))
        await self.manager.update(self._artifact(
            "echo", "2.0.0", commit="b" * 40,
            config_schema=new_schema,
            config_default={"prefix": "v2", "timeout": 30},
        ))
        snapshot = self.store.active("echo").path / ".rollback-config.yaml"
        snapshot.write_text("prefix: [\n", encoding="utf-8")

        from app.core.plugin_manager import PluginOperationError
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.rollback("echo")

        self.assertEqual(raised.exception.code, "config_migration_required")

    async def test_failed_update_restores_config_after_default_migration(self):
        old_schema, old_default = self._editable_config()
        new_schema = deepcopy(old_schema)
        new_schema["properties"]["timeout"] = {"type": "integer"}
        new_schema["required"].append("timeout")
        new_default = {"prefix": "v2", "timeout": 30}
        await self.manager.install(self._artifact(
            "echo", "1.0.0", config_schema=old_schema,
            config_default=old_default,
        ))
        await self.manager.configure("echo", {"prefix": "operator"})
        self.supervisor.unhealthy_versions.add("2.0.0")

        from app.core.plugin_manager import PluginOperationError
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.update(self._artifact(
                "echo", "2.0.0", commit="b" * 40,
                config_schema=new_schema, config_default=new_default,
            ))

        self.assertEqual(raised.exception.code, "stabilization_failed")
        active = self.store.active("echo")
        self.assertEqual(active.version, "1.0.0")
        self.assertEqual(self.store.read_config(active), {"prefix": "operator"})
        example = self.root / "plugins/echo/config.yaml.example"
        self.assertEqual(yaml.safe_load(example.read_text()), old_default)

    async def test_failed_config_restore_reports_activation_rollback_failure(self):
        old_schema, old_default = self._editable_config()
        new_schema = deepcopy(old_schema)
        new_schema["properties"]["timeout"] = {"type": "integer"}
        new_schema["required"].append("timeout")
        await self.manager.install(self._artifact(
            "echo", "1.0.0", config_schema=old_schema,
            config_default=old_default,
        ))
        await self.manager.configure("echo", {"prefix": "operator"})
        self.supervisor.unhealthy_versions.add("2.0.0")

        from app.core.plugin_manager import PluginOperationError
        from app.core.plugin_store import StoreError
        self.store.write_config = Mock(side_effect=StoreError(
            "config_write_failed",
            "cannot restore plugin config",
        ))
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.update(self._artifact(
                "echo", "2.0.0", commit="b" * 40,
                config_schema=new_schema,
                config_default={"prefix": "v2", "timeout": 30},
            ))

        self.assertEqual(raised.exception.code, "activation_rollback_failed")

    async def test_failed_new_process_stop_reports_activation_rollback_failure(self):
        old_schema, old_default = self._editable_config()
        new_schema = deepcopy(old_schema)
        new_schema["properties"]["timeout"] = {"type": "integer"}
        new_schema["required"].append("timeout")
        await self.manager.install(self._artifact(
            "echo", "1.0.0", config_schema=old_schema,
            config_default=old_default,
        ))
        self.supervisor.unhealthy_versions.add("2.0.0")
        original_stop = self.supervisor.stop

        async def fail_new_process_stop(process, timeout=10):
            if process.release.version == "2.0.0":
                raise RuntimeError("stop failed")
            return await original_stop(process, timeout=timeout)

        self.supervisor.stop = fail_new_process_stop

        from app.core.plugin_manager import PluginOperationError
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.update(self._artifact(
                "echo", "2.0.0", commit="b" * 40,
                config_schema=new_schema,
                config_default={"prefix": "v2", "timeout": 30},
            ))

        self.assertEqual(raised.exception.code, "activation_rollback_failed")
        self.assertIn("new-process", raised.exception.details["failed_steps"])

    async def test_active_consistency_checks_store_process_route_manifest_and_schema(self):
        await self.manager.install(self._artifact("echo", "1.0.0", commit="a" * 40))
        updated = await self.manager.update(
            self._artifact("echo", "2.0.0", commit="b" * 40)
        )

        active = self.store.active(updated.plugin_id)
        self.manager.assert_active_consistency(active)

        self.router.deactivate("echo")
        from app.core.plugin_manager import PluginOperationError
        with self.assertRaises(PluginOperationError) as raised:
            self.manager.assert_active_consistency(active)
        self.assertEqual(raised.exception.code, "activation_inconsistent")

    async def test_update_rejects_provider_capability_loss_that_blocks_consumer(self):
        from app.core.plugin_manager import PluginOperationError

        await self.manager.install(self._artifact(
            plugin_id="provider",
            version="1.0.0",
            provides=(("download.provider", True), ("storage.provider", True)),
            commands=("provider",),
        ))
        provider_v1 = self.supervisor.process("provider")
        await self.manager.install(self._artifact(
            plugin_id="consumer",
            provides=(),
            requires=("storage.provider",),
            commands=("consume",),
            commit="b" * 40,
        ))

        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.update(self._artifact(
                plugin_id="provider",
                version="2.0.0",
                provides=(("download.provider", True),),
                commands=("provider",),
                commit="c" * 40,
            ))

        self.assertEqual(raised.exception.code, "dependent_capability_lost")
        self.assertEqual(self.store.active("provider").version, "1.0.0")
        self.assertEqual(provider_v1.state, "healthy")
        self.assertIs(self.supervisor.process("provider"), provider_v1)
        self.assertTrue(self.store.active("consumer").enabled)
        self.assertIsNotNone(self.router.command_route("consume"))

    async def test_failed_stabilization_restores_old_routes_process_and_record(self):
        from app.core.plugin_manager import PluginOperationError

        schema, _default = self._editable_config()
        await self.manager.install(self._artifact(
            "echo", "1.0.0", config_schema=schema,
            config_default={"prefix": "v1"},
        ))
        old = self.supervisor.process("echo")
        self.supervisor.unhealthy_versions.add("2.0.0")

        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.update(self._artifact(
                "echo", "2.0.0", commit="b" * 40,
                config_schema=schema, config_default={"prefix": "v2"},
            ))

        self.assertEqual(raised.exception.code, "stabilization_failed")
        self.assertEqual(self.store.active("echo").version, "1.0.0")
        self.assertEqual(self.router.snapshot.capabilities["demo.echo"].client.version, "1.0.0")
        self.assertEqual(old.state, "healthy")
        self.assertIn(("echo", "1.0.0"), self.supervisor.resumed)
        new = next(item for item in self.supervisor.instances if item.release.version == "2.0.0")
        self.assertEqual(new.state, "stopped")
        example = self.root / "plugins/echo/config.yaml.example"
        self.assertEqual(yaml.safe_load(example.read_text()), {"prefix": "v1"})

    async def test_update_refuses_to_stop_non_idempotent_work_that_did_not_drain(self):
        from app.core.plugin_manager import PluginOperationError

        await self.manager.install(self._artifact("echo", "1.0.0"))
        old = self.supervisor.process("echo")
        self.supervisor.busy_versions.add("1.0.0")

        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.update(self._artifact("echo", "2.0.0", commit="b" * 40))

        self.assertEqual(raised.exception.code, "drain_timeout")
        self.assertEqual(self.store.active("echo").version, "1.0.0")
        self.assertEqual(old.state, "healthy")
        self.assertIn(("echo", "1.0.0"), self.supervisor.resumed)

    async def test_disable_refuses_while_active_work_did_not_drain(self):
        from app.core.plugin_manager import PluginOperationError

        await self.manager.install(self._artifact())
        self.supervisor.busy_versions.add("1.0.0")
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.disable("echo")
        self.assertEqual(raised.exception.code, "drain_timeout")
        self.assertTrue(self.store.active("echo").enabled)
        self.assertIn("demo.echo", self.router.snapshot.capabilities)

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

    async def test_remove_large_release_tree_does_not_block_core_loop(self):
        await self.manager.install(self._artifact(
            plugin_id="removable",
            provides=(("demo.removable", True),),
            commands=("removable",),
        ))
        original_remove = self.store.remove_plugin

        def slow_remove(plugin_id):
            time.sleep(0.1)
            return original_remove(plugin_id)

        self.store.remove_plugin = slow_remove
        ticks = []
        running = True

        async def heartbeat():
            while running:
                ticks.append(asyncio.get_running_loop().time())
                await asyncio.sleep(0.01)

        ticker = asyncio.create_task(heartbeat())
        try:
            await self.manager.remove("removable")
        finally:
            running = False
            await ticker

        self.assertGreaterEqual(len(ticks), 5)

    async def test_restore_starts_providers_before_alphabetically_earlier_consumers(self):
        await self.manager.install(self._artifact(
            plugin_id="zzz-provider", provides=(("storage.provider", True),),
            commands=("storage",),
        ))
        await self.manager.install(self._artifact(
            plugin_id="aaa-consumer", provides=(), requires=("storage.provider",),
            commands=("consume",), commit="b" * 40,
        ))
        await self.supervisor.close_all()

        from app.core.capability_router import CapabilityRouter
        from app.core.plugin_manager import PluginManager

        self.router = CapabilityRouter()
        self.supervisor = FakeSupervisor()
        self.manager = PluginManager(
            store=self.store, supervisor=self.supervisor, router=self.router,
            journal=self.journal, venv_installer=self._install_venv,
            stabilize_seconds=0,
        )
        restored = await self.manager.restore_active()

        self.assertEqual([item.plugin_id for item in restored], [
            "zzz-provider", "aaa-consumer",
        ])
        self.assertIsNotNone(self.router.command_route("consume"))
        self.assertTrue(self.store.active("aaa-consumer").enabled)

    async def test_available_updates_compares_only_active_versions(self):
        await self.manager.install(self._artifact(plugin_id="echo", version="1.0.0"))

        class Resolver:
            def __init__(self):
                self.calls = []

            async def available_updates(self, installed, core_api_version):
                self.calls.append((installed, core_api_version))
                return [SimpleNamespace(
                    plugin_id="echo",
                    current_version="1.0.0",
                    target_version="1.1.0",
                )]

        resolver = Resolver()
        self.manager._artifact_resolver = resolver

        updates = await self.manager.available_updates()

        self.assertEqual(len(updates), 1)
        self.assertEqual(resolver.calls, [({"echo": "1.0.0"}, "1.1")])

    async def test_available_updates_is_empty_for_basic_resolver(self):
        self.manager._artifact_resolver = SimpleNamespace(resolve=None)

        self.assertEqual(await self.manager.available_updates(), [])

    async def test_available_plugins_uses_installed_ids_and_live_capabilities(self):
        await self.manager.install(self._artifact(plugin_id="echo", version="1.0.0"))

        class Resolver:
            def __init__(self):
                self.calls = []
                self.refreshed = 0

            async def refresh(self):
                self.refreshed += 1

            async def available_plugins(
                self,
                installed,
                core_api_version,
                *,
                available_capabilities,
            ):
                self.calls.append((
                    installed,
                    core_api_version,
                    available_capabilities,
                ))
                return [SimpleNamespace(plugin_id="new-feature", ready=True)]

        resolver = Resolver()
        self.manager._artifact_resolver = resolver

        candidates = await self.manager.available_plugins()

        self.assertEqual([item.plugin_id for item in candidates], ["new-feature"])
        self.assertEqual(resolver.refreshed, 1)
        self.assertEqual(resolver.calls, [(
            {"echo"},
            "1.1",
            {"demo.echo"},
        )])

    async def test_available_plugins_uses_cache_after_remote_refresh_failure(self):
        from app.core.plugin_catalog import CatalogError

        class CachedResolver:
            async def refresh(self):
                raise CatalogError("catalog_download_failed", "network down")

            async def available_plugins(
                self,
                installed,
                core_api_version,
                *,
                available_capabilities,
            ):
                return [SimpleNamespace(
                    plugin_id="cached",
                    reference="cached@1.0.0",
                    ready=True,
                )]

        self.manager._artifact_resolver = CachedResolver()

        candidates = await self.manager.available_plugins()

        self.assertEqual(candidates[0].reference, "cached@1.0.0")

    async def test_available_plugins_is_empty_for_basic_resolver(self):
        self.manager._artifact_resolver = SimpleNamespace(resolve=None)

        self.assertEqual(await self.manager.available_plugins(), [])


if __name__ == "__main__":
    unittest.main()
