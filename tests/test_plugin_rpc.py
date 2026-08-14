import asyncio
import logging
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SDK_SOURCE = ROOT / "sdk/src"
if str(SDK_SOURCE) not in sys.path:
    sys.path.insert(0, str(SDK_SOURCE))


class PluginRpcClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.temp.name) / "echo.sock"
        self.loop_errors = []
        self.loop = asyncio.get_running_loop()
        self.previous_exception_handler = self.loop.get_exception_handler()
        self.loop.set_exception_handler(
            lambda _loop, context: self.loop_errors.append(context)
        )

    async def asyncTearDown(self):
        self.loop.set_exception_handler(self.previous_exception_handler)
        self.temp.cleanup()

    async def _server(self, capabilities=None, max_frame_bytes=1024 * 1024):
        from telepiplex_plugin_sdk.runtime import FeatureRuntime

        runtime = FeatureRuntime(
            manifest={"plugin_id": "echo", "version": "1.0.0", "host_api": ">=1.0,<2.0"},
            token="secret-token",
            capabilities=capabilities or {},
            max_frame_bytes=max_frame_bytes,
        )
        task = asyncio.create_task(runtime.serve(self.socket_path))
        for _ in range(100):
            if self.socket_path.exists():
                break
            await asyncio.sleep(0.01)
        self.addAsyncCleanup(runtime.close)
        self.addAsyncCleanup(self._await_task, task)
        return runtime, task

    async def _await_task(self, task):
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_handshake_requires_token_and_preserves_unicode(self):
        from app.runtime.plugin_contract import ContractError
        from app.runtime.plugin_rpc import RpcClient

        await self._server()
        client = RpcClient(self.socket_path, "secret-token")
        result = await client.request("handshake", {"message": "你好"}, deadline=1)

        self.assertEqual(result["plugin_id"], "echo")
        self.assertEqual(result["version"], "1.0.0")
        self.assertEqual(result["echo"], "你好")

        wrong = RpcClient(self.socket_path, "wrong-token")
        with self.assertRaises(ContractError) as raised:
            await wrong.request("handshake", {}, deadline=1)
        self.assertEqual(raised.exception.code, "unauthorized")

    async def test_unknown_method_and_internal_error_use_stable_sanitized_codes(self):
        from app.runtime.plugin_contract import ContractError
        from app.runtime.plugin_rpc import RpcClient

        async def explode(_request):
            raise RuntimeError("api_key=secret-value")

        await self._server({"demo.explode": explode})
        client = RpcClient(self.socket_path, "secret-token")

        with self.assertRaises(ContractError) as raised:
            await client.request("invented.method", {}, deadline=1)
        self.assertEqual(raised.exception.code, "not_found")

        with self.assertRaises(ContractError) as raised:
            await client.request(
                "capability.call",
                {"capability": "demo.explode", "method": "run", "payload": {}},
                deadline=1,
            )
        self.assertEqual(raised.exception.code, "internal_error")
        self.assertNotIn("secret-value", str(raised.exception))

    async def test_client_enforces_deadline_and_frame_limit(self):
        from app.runtime.plugin_contract import ContractError
        from app.runtime.plugin_rpc import RpcClient

        cancelled = asyncio.Event()

        async def slow(_request):
            try:
                await asyncio.sleep(0.2)
                return {"ok": True}
            except asyncio.CancelledError:
                cancelled.set()
                raise

        await self._server({"demo.slow": slow}, max_frame_bytes=512)
        client = RpcClient(self.socket_path, "secret-token", max_frame_bytes=512)

        with self.assertRaises(ContractError) as raised:
            await client.request(
                "capability.call",
                {"capability": "demo.slow", "method": "run", "payload": {}},
                deadline=0.01,
            )
        self.assertEqual(raised.exception.code, "deadline_exceeded")
        await asyncio.wait_for(cancelled.wait(), timeout=0.5)

        with self.assertRaises(ContractError) as raised:
            await client.request("handshake", {"value": "x" * 1000}, deadline=1)
        self.assertEqual(raised.exception.code, "frame_too_large")
        await asyncio.sleep(0.25)
        self.assertEqual(self.loop_errors, [])

    async def test_concurrent_requests_receive_their_own_results(self):
        from app.runtime.plugin_rpc import RpcClient

        async def echo(request):
            await asyncio.sleep(0.01)
            return {"value": request["payload"]["value"]}

        await self._server({"demo.echo": echo})
        client = RpcClient(self.socket_path, "secret-token")

        results = await asyncio.gather(*[
            client.request(
                "capability.call",
                {"capability": "demo.echo", "method": "run", "payload": {"value": index}},
                deadline=1,
            )
            for index in range(10)
        ])

        self.assertEqual([item["value"] for item in results], list(range(10)))

    async def test_rpc_envelope_binds_trace_parent_span_operation_and_request_in_feature(self):
        from app.runtime.plugin_rpc import RpcClient
        from telepiplex_plugin_sdk.diagnostics import (
            bind_diagnostic_context,
            current_diagnostic_context,
        )

        observed = {}

        async def inspect_context(request):
            observed.update(current_diagnostic_context())
            return {"payload": request["payload"]}

        await self._server({"demo.inspect": inspect_context})
        client = RpcClient(self.socket_path, "secret-token")
        with bind_diagnostic_context(
            trace_id="TRC-RPC-1",
            span_id="SPN-PARENT-1",
            operation_id="operation-rpc-1",
        ):
            result = await client.request(
                "capability.call",
                {"capability": "demo.inspect", "method": "run", "payload": {"value": 9}},
                deadline=1,
            )

        self.assertEqual(result, {"payload": {"value": 9}})
        self.assertEqual(observed["trace_id"], "TRC-RPC-1")
        self.assertEqual(observed["parent_span_id"], "SPN-PARENT-1")
        self.assertEqual(observed["operation_id"], "operation-rpc-1")
        self.assertTrue(observed["span_id"].startswith("SPN-"))
        self.assertTrue(observed["request_id"])
        self.assertNotEqual(observed["span_id"], observed["parent_span_id"])

    async def test_rpc_client_logs_typed_start_and_completion_with_duration(self):
        from app.runtime.plugin_rpc import RpcClient
        from telepiplex_plugin_sdk.diagnostics import bind_diagnostic_context

        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        await self._server()
        logger = logging.getLogger("telepiplex.rpc.feature")
        original_level = logger.level
        logger.setLevel(logging.INFO)
        capture = Capture()
        logger.addHandler(capture)
        self.addCleanup(logger.removeHandler, capture)
        self.addCleanup(logger.setLevel, original_level)

        with bind_diagnostic_context(trace_id="TRC-RPC-LOG", operation_id="operation-log"):
            result = await RpcClient(self.socket_path, "secret-token").request(
                "handshake",
                {"message": "你好"},
                deadline=1,
            )

        started = next(
            record for record in records
            if getattr(record, "event_name", "") == "rpc.feature.started"
        )
        completed = next(
            record for record in records
            if getattr(record, "event_name", "") == "rpc.feature.completed"
        )
        self.assertEqual(result["plugin_id"], "echo")
        self.assertEqual(started.diagnostic_fields["transport"]["method"], "handshake")
        self.assertEqual(
            started.diagnostic_fields["transport"]["request_id"],
            completed.diagnostic_fields["transport"]["request_id"],
        )
        self.assertEqual(completed.diagnostic_fields["status"], "completed")
        self.assertGreaterEqual(completed.diagnostic_fields["duration_ms"], 0)


if __name__ == "__main__":
    unittest.main()
