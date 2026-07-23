import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ECHO_SOURCE = ROOT / "examples/echo_feature"
SDK_SOURCE = ROOT / "sdk"


class PluginRuntimeEndToEndTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.runtime_broker import RuntimeBroker
        from app.runtime.event_dispatcher import EventDispatcher
        from app.runtime.event_journal import EventJournal
        from app.runtime.plugin_manager import PluginManager
        from app.runtime.plugin_store import PluginStore
        from app.runtime.plugin_supervisor import PluginSupervisor

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.wheels = self.root / "wheels"
        self.wheels.mkdir()
        self.sdk_wheel = await asyncio.to_thread(self._build_wheel, SDK_SOURCE, self.wheels / "sdk")
        self.router = CapabilityRouter()
        self.journal = EventJournal(self.root / "host.db")
        self.dispatcher = EventDispatcher(self.router, self.journal, retry_interval=0.01)
        self.broker = RuntimeBroker(
            self.router,
            self.journal,
            self.root / "runtime/host.sock",
            dispatcher=self.dispatcher,
        )
        self.supervisor = PluginSupervisor(
            startup_timeout=5,
            restart_limit=1,
            restart_backoff=0.01,
            runtime_root=self.root / "runtime",
            broker=self.broker,
        )
        self.manager = PluginManager(
            store=PluginStore(self.root / "plugins"),
            supervisor=self.supervisor,
            router=self.router,
            journal=self.journal,
            broker=self.broker,
            install_timeout=30,
            drain_timeout=2,
            stabilize_seconds=0,
        )
        await self.manager.start()

    async def asyncTearDown(self):
        if hasattr(self, "manager"):
            await self.manager.close()
        self.temp.cleanup()

    def _build_wheel(self, source: Path, output: Path) -> Path:
        output.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(output),
                str(source),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        wheels = list(output.glob("*.whl"))
        self.assertEqual(len(wheels), 1)
        return wheels[0]

    async def _artifact(self, version: str, commit: str) -> Path:
        from app.runtime.plugin_artifact import build_tpx

        build_source = self.root / f"echo-build-{version}"
        shutil.copytree(ECHO_SOURCE, build_source)
        pyproject = build_source / "pyproject.toml"
        pyproject.write_text(
            pyproject.read_text(encoding="utf-8").replace(
                'version = "1.0.0"', f'version = "{version}"'
            ),
            encoding="utf-8",
        )
        plugin_wheel = await asyncio.to_thread(
            self._build_wheel,
            build_source,
            self.wheels / f"echo-{version}",
        )
        package = self.root / f"echo-package-{version}"
        (package / "wheelhouse").mkdir(parents=True)
        shutil.copy2(plugin_wheel, package / "plugin.whl")
        shutil.copy2(self.sdk_wheel, package / "wheelhouse" / self.sdk_wheel.name)
        manifest = {
            "plugin_id": "echo",
            "name": "Echo",
            "version": version,
            "host_api": ">=1.0,<2.0",
            "entry_point": "telepiplex_echo.runtime:main",
            "provides": [{"name": "demo.echo", "exclusive": True}],
            "requires": [],
            "subscribes": [],
            "publishes": ["demo.echoed"],
            "commands": [{"name": "echo", "description": "Echo text"}],
            "callbacks": ["echo"],
            "source": {
                "repository": "origin",
                "branch": "feature/echo",
                "commit": commit,
            },
        }
        (package / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8"
        )
        (package / "config.schema.json").write_text(json.dumps({
            "type": "object",
            "properties": {"prefix": {"type": "string"}},
            "required": ["prefix"],
            "additionalProperties": False,
        }), encoding="utf-8")
        (package / "config.default.yaml").write_text("prefix: Echo\n", encoding="utf-8")
        return build_tpx(package, self.root / f"echo-{version}.tpx")

    async def test_full_feature_lifecycle_keeps_host_pid_and_inflight_work(self):
        host_pid = os.getpid()
        v1 = await self._artifact("1.0.0", "a" * 40)
        v2 = await self._artifact("2.0.0", "b" * 40)

        installed = await self.manager.install(v1)
        self.assertEqual(installed.version, "1.0.0")
        first = await self.router.call(
            "demo.echo", "echo", {"text": "hello"}, {"deadline": 3}
        )
        self.assertEqual(first, {"text": "hello", "version": "1.0.0"})

        process = self.supervisor.process("echo")
        process.child.kill()
        async with asyncio.timeout(5):
            while process.restart_count < 1 or process.state != "healthy":
                await asyncio.sleep(0.02)
        recovered = await self.router.call(
            "demo.echo", "echo", {"text": "recovered"}, {"deadline": 3}
        )
        self.assertEqual(recovered["text"], "recovered")

        self.journal.set_subscriptions("audit", ["demo.echoed"])
        published = await self.router.call(
            "demo.echo",
            "echo",
            {"text": "journal", "publish": True},
            {"deadline": 3, "idempotency_key": "echo-publish-1"},
        )
        self.assertTrue(published["event_id"])
        self.assertEqual(self.journal.pending("audit")[0].payload["text"], "journal")

        route = self.router.command_route("echo")
        command = await route.client.request(
            "command.dispatch",
            {"command": "echo", "args": ["hello"]},
            deadline=3,
        )
        self.assertEqual(command["actions"][0]["text"], "1.0.0: hello")

        inflight = asyncio.create_task(self.router.call(
            "demo.echo",
            "echo",
            {"text": "held", "delay": 0.3},
            {"deadline": 3, "idempotency_key": "held-1"},
        ))
        await asyncio.sleep(0.05)
        updated = await self.manager.update(v2)

        self.assertEqual((await inflight)["version"], "1.0.0")
        self.assertEqual(updated.version, "2.0.0")
        second = await self.router.call(
            "demo.echo", "echo", {"text": "new"}, {"deadline": 3}
        )
        self.assertEqual(second["version"], "2.0.0")

        rolled_back = await self.manager.rollback("echo")
        self.assertEqual(rolled_back.version, "1.0.0")
        self.assertEqual((await self.router.call(
            "demo.echo", "echo", {"text": "back"}, {"deadline": 3}
        ))["version"], "1.0.0")

        self.assertEqual((await self.manager.disable("echo")).state, "disabled")
        self.assertNotIn("demo.echo", self.router.snapshot.capabilities)
        self.assertEqual((await self.manager.enable("echo")).state, "active")
        self.assertIn("demo.echo", self.router.snapshot.capabilities)
        self.assertEqual((await self.manager.remove("echo")).state, "removed")
        self.assertEqual(self.router.snapshot.plugin_ids, ())
        self.assertEqual(os.getpid(), host_pid)

    async def test_feature_runtime_logs_dispatches_with_redaction(self):
        artifact = await self._artifact("1.0.0", "c" * 40)

        await self.manager.install(artifact)
        route = self.router.command_route("echo")
        await route.client.request(
            "command.dispatch",
            {"command": "echo", "args": ["access_token=secret-token-value"]},
            deadline=3,
        )

        runtime_log = self.root / "plugins" / "echo" / "state" / "logs" / "runtime.log"
        async with asyncio.timeout(5):
            while not runtime_log.exists():
                await asyncio.sleep(0.02)
        async with asyncio.timeout(5):
            while True:
                text = runtime_log.read_text(encoding="utf-8")
                if "feature_dispatch_start" in text:
                    break
                await asyncio.sleep(0.02)

        text = runtime_log.read_text(encoding="utf-8")
        self.assertIn("feature_dispatch_start", text)
        self.assertIn("feature_dispatch_finish", text)
        self.assertIn("command.dispatch", text)
        self.assertIn("***redacted***", text)
        self.assertNotIn("secret-token-value", text)


if __name__ == "__main__":
    unittest.main()
