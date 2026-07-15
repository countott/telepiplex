import asyncio
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SDK_SOURCE = ROOT / "sdk/src"
if str(SDK_SOURCE) not in sys.path:
    sys.path.insert(0, str(SDK_SOURCE))


class FeatureSdkRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.temp.name) / "runtime.sock"

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def _start(
        self,
        capability,
        *,
        messages=None,
        operation_control=None,
        operation_snapshot=None,
    ):
        from telepiplex_plugin_sdk.runtime import FeatureRuntime

        runtime = FeatureRuntime(
            manifest={"plugin_id": "echo", "version": "1.0.0"},
            token="token",
            capabilities={"demo.echo": capability},
            messages=messages,
            operation_control=operation_control,
            operation_snapshot=operation_snapshot,
        )
        task = asyncio.create_task(runtime.serve(self.socket_path))
        for _ in range(100):
            if self.socket_path.exists():
                break
            await asyncio.sleep(0.01)
        self.addAsyncCleanup(self._cleanup_runtime, runtime, task)
        return runtime, task

    async def _cleanup_runtime(self, runtime, task):
        await runtime.close()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_drain_blocks_new_business_calls_and_reports_active_work(self):
        from app.core.plugin_contract import ContractError
        from app.core.plugin_rpc import RpcClient

        started = asyncio.Event()
        release = asyncio.Event()

        async def held(request):
            started.set()
            await release.wait()
            return {"value": request["payload"]["value"]}

        runtime, _task = await self._start(held)
        client = RpcClient(self.socket_path, "token")
        active_call = asyncio.create_task(client.request(
            "capability.call",
            {"capability": "demo.echo", "method": "run", "payload": {"value": 1}},
            deadline=2,
        ))
        await started.wait()

        drained = await client.request("drain", {}, deadline=1)
        health = await client.request("health", {}, deadline=1)

        self.assertEqual(drained["state"], "draining")
        self.assertEqual(drained["active_tasks"], 1)
        self.assertEqual(health["state"], "draining")
        with self.assertRaises(ContractError) as raised:
            await client.request(
                "capability.call",
                {"capability": "demo.echo", "method": "run", "payload": {"value": 2}},
                deadline=1,
            )
        self.assertEqual(raised.exception.code, "busy")

        release.set()
        self.assertEqual((await active_call)["value"], 1)
        self.assertEqual(runtime.active_tasks, 0)
        resumed = await client.request("resume", {}, deadline=1)
        self.assertEqual(resumed["state"], "healthy")

    async def test_shutdown_closes_server_and_removes_socket(self):
        from app.core.plugin_rpc import RpcClient

        async def echo(request):
            return request["payload"]

        _runtime, task = await self._start(echo)
        client = RpcClient(self.socket_path, "token")

        result = await client.request("shutdown", {}, deadline=1)
        await asyncio.wait_for(task, timeout=1)

        self.assertEqual(result["state"], "stopped")
        self.assertFalse(self.socket_path.exists())

    def test_runtime_context_exposes_core_client(self):
        from telepiplex_plugin_sdk import CoreClient, RuntimeContext

        core = CoreClient(self.socket_path, "token")
        context = RuntimeContext(
            manifest={"plugin_id": "echo"},
            token="token",
            socket_path=self.socket_path,
            core_socket_path=self.socket_path,
            config_path=Path("/config/echo.yaml"),
            state_path=Path("/config/state"),
            core=core,
        )
        self.assertIs(context.core, core)

    async def test_message_dispatch_uses_session_handler(self):
        from app.core.plugin_rpc import RpcClient

        async def echo(request):
            return request["payload"]

        async def message(request):
            return {"actions": [{"kind": "send_message", "text": request["text"]}]}

        await self._start(echo, messages=message)
        result = await RpcClient(self.socket_path, "token").request(
            "message.dispatch",
            {"text": "follow up", "user_id": 1, "chat_id": 10},
            deadline=1,
        )
        self.assertEqual(result["actions"][0]["text"], "follow up")

    async def test_spawned_background_work_is_visible_to_drain_and_health(self):
        from app.core.plugin_rpc import RpcClient

        release = asyncio.Event()

        async def echo(request):
            return request["payload"]

        runtime, _task = await self._start(echo)
        runtime.spawn(release.wait(), task_id="download-1")
        client = RpcClient(self.socket_path, "token")
        health = await client.request("health", {}, deadline=1)
        drained = await client.request("drain", {}, deadline=1)
        self.assertEqual(health["active_tasks"], 1)
        self.assertEqual(drained["interrupted_task_ids"], ["download-1"])

        release.set()
        for _ in range(100):
            if (await client.request("health", {}, deadline=1))["active_tasks"] == 0:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(runtime.active_tasks, 0)

    async def test_operation_control_dispatches_to_registered_handler(self):
        from app.core.plugin_rpc import RpcClient

        seen = []

        async def echo(request):
            return request["payload"]

        async def control(request):
            seen.append(request)
            return {
                "operation_id": request["operation_id"],
                "state": "cancelling",
                "revision": 2,
            }

        await self._start(echo, operation_control=control)
        result = await RpcClient(self.socket_path, "token").request(
            "operation.control",
            {"operation_id": "op-1", "action": "cancel", "revision": 1},
            deadline=1,
        )

        self.assertEqual(result["state"], "cancelling")
        self.assertEqual(seen[0]["action"], "cancel")

    async def test_operation_snapshot_dispatches_to_registered_handler(self):
        from app.core.plugin_rpc import RpcClient

        async def echo(request):
            return request["payload"]

        async def snapshot(request):
            return {"operations": [{"operation_id": request["operation_id"]}]}

        await self._start(echo, operation_snapshot=snapshot)
        result = await RpcClient(self.socket_path, "token").request(
            "operation.snapshot",
            {"operation_id": "op-1"},
            deadline=1,
        )

        self.assertEqual(result["operations"], [{"operation_id": "op-1"}])


if __name__ == "__main__":
    unittest.main()
