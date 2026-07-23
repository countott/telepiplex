import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock


class ProviderClient:
    def __init__(self):
        self.calls = []

    async def request(self, method, params, *, deadline, idempotency_key=""):
        self.calls.append((method, params, deadline, idempotency_key))
        return {"provider": "download", "payload": params["payload"]}


def manifest(plugin_id, *, provides=(), requires=(), publishes=(), subscribes=()):
    from app.runtime.plugin_manifest import PluginManifest

    return PluginManifest.from_mapping({
        "plugin_id": plugin_id,
        "name": plugin_id,
        "version": "1.0.0",
        "host_api": ">=1.0,<2.0",
        "entry_point": f"telepiplex_{plugin_id.replace('-', '_')}.runtime:main",
        "provides": [{"name": name, "exclusive": True} for name in provides],
        "requires": list(requires),
        "subscribes": list(subscribes),
        "publishes": list(publishes),
        "commands": [],
        "callbacks": [],
        "source": {
            "repository": "origin",
            "branch": f"feature/{plugin_id}",
            "commit": "a" * 40,
        },
    })


class RuntimeBrokerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.runtime_broker import RuntimeBroker
        from app.runtime.event_journal import EventJournal

        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.router = CapabilityRouter()
        self.journal = EventJournal(root / "host.db")
        self.notifications = []
        self.operation_sink = AsyncMock(return_value={"accepted": True, "revision": 1})
        self.broker = RuntimeBroker(
            self.router,
            self.journal,
            root / "host.sock",
            notification_sink=lambda user_id, text: self.notifications.append((user_id, text)),
            operation_sink=self.operation_sink,
        )
        await self.broker.start()

    async def asyncTearDown(self):
        await self.broker.close()
        self.journal.close()
        self.temp.cleanup()

    async def test_feature_calls_only_declared_required_capability(self):
        from telepiplex_plugin_sdk import HostClient, FeatureError

        provider = ProviderClient()
        self.router.activate(
            "download",
            manifest("download", provides=("download.provider",)),
            provider,
        )
        caller = manifest("search", requires=("download.provider",))
        self.broker.register("search", "caller-token", caller)
        client = HostClient(self.broker.socket_path, "caller-token")

        result = await client.call_capability(
            "download.provider",
            "submit",
            {"url": "magnet:?xt=test"},
            deadline=2,
            idempotency_key="plan-1",
        )
        self.assertEqual(result["provider"], "download")
        self.assertEqual(provider.calls[0][3], "plan-1")
        self.assertLessEqual(provider.calls[0][2], 2)

        undeclared = manifest("echo")
        self.broker.register("echo", "echo-token", undeclared)
        with self.assertRaises(FeatureError) as raised:
            await HostClient(self.broker.socket_path, "echo-token").call_capability(
                "download.provider", "submit", {}, deadline=1
            )
        self.assertEqual(raised.exception.code, "capability_not_declared")

    async def test_feature_publishes_only_declared_event_and_token_is_revocable(self):
        from telepiplex_plugin_sdk import HostClient, FeatureError

        self.journal.set_subscriptions("rename", ["download.completed"])
        publisher = manifest("download", publishes=("download.completed",))
        self.broker.register("download", "publisher-token", publisher)
        client = HostClient(self.broker.socket_path, "publisher-token")

        event = await client.publish_event(
            "download.completed",
            {"path": "/downloads/show"},
            idempotency_key="download-1",
            deadline=1,
        )
        self.assertTrue(event["event_id"])
        self.assertEqual(len(self.journal.pending("rename")), 1)

        with self.assertRaises(FeatureError) as raised:
            await client.publish_event("media.organized", {}, deadline=1)
        self.assertEqual(raised.exception.code, "event_not_declared")

        self.broker.unregister("publisher-token")
        with self.assertRaises(FeatureError) as raised:
            await client.publish_event("download.completed", {}, deadline=1)
        self.assertEqual(raised.exception.code, "unauthorized")

    async def test_authenticated_feature_can_send_bounded_user_notification(self):
        from telepiplex_plugin_sdk import HostClient, FeatureError

        self.broker.register("download", "notify-token", manifest("download"))
        client = HostClient(self.broker.socket_path, "notify-token")
        result = await client.notify_user(123, "下载完成", deadline=1)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.notifications, [(123, "下载完成")])

        with self.assertRaises(FeatureError) as raised:
            await client.notify_user(123, "x" * 5000, deadline=1)
        self.assertEqual(raised.exception.code, "invalid_notification")

    async def test_operation_report_uses_authenticated_feature_identity(self):
        from telepiplex_plugin_sdk import HostClient

        self.broker.register("echo", "echo-token", manifest("echo"))
        result = await HostClient(
            self.broker.socket_path, "echo-token"
        ).report_operation({
            "operation_id": "op-1",
            "chat_id": 10,
            "user_id": 1,
            "state": "running",
            "stage": "planning",
            "status_text": "规划中",
            "control": "cancel",
            "revision": 1,
        })

        self.assertTrue(result["accepted"])
        self.assertEqual(self.operation_sink.await_args.args[0], "echo")
        self.assertEqual(self.operation_sink.await_args.args[1]["operation_id"], "op-1")


if __name__ == "__main__":
    unittest.main()
