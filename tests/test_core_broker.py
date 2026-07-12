import tempfile
import unittest
from pathlib import Path


class ProviderClient:
    def __init__(self):
        self.calls = []

    async def request(self, method, params, *, deadline, idempotency_key=""):
        self.calls.append((method, params, deadline, idempotency_key))
        return {"provider": "open115", "payload": params["payload"]}


def manifest(plugin_id, *, provides=(), requires=(), publishes=(), subscribes=()):
    from app.core.plugin_manifest import PluginManifest

    return PluginManifest.from_mapping({
        "plugin_id": plugin_id,
        "name": plugin_id,
        "version": "1.0.0",
        "core_api": ">=1.0,<2.0",
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


class CoreBrokerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.core.capability_router import CapabilityRouter
        from app.core.core_broker import CoreBroker
        from app.core.event_journal import EventJournal

        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.router = CapabilityRouter()
        self.journal = EventJournal(root / "core.db")
        self.notifications = []
        self.broker = CoreBroker(
            self.router,
            self.journal,
            root / "core.sock",
            notification_sink=lambda user_id, text: self.notifications.append((user_id, text)),
        )
        await self.broker.start()

    async def asyncTearDown(self):
        await self.broker.close()
        self.journal.close()
        self.temp.cleanup()

    async def test_feature_calls_only_declared_required_capability(self):
        from telepiplex_plugin_sdk import CoreClient, FeatureError

        provider = ProviderClient()
        self.router.activate(
            "open115",
            manifest("open115", provides=("download.provider",)),
            provider,
        )
        caller = manifest("media-search", requires=("download.provider",))
        self.broker.register("media-search", "caller-token", caller)
        client = CoreClient(self.broker.socket_path, "caller-token")

        result = await client.call_capability(
            "download.provider",
            "submit",
            {"url": "magnet:?xt=test"},
            deadline=2,
            idempotency_key="plan-1",
        )
        self.assertEqual(result["provider"], "open115")
        self.assertEqual(provider.calls[0][3], "plan-1")
        self.assertLessEqual(provider.calls[0][2], 2)

        undeclared = manifest("echo")
        self.broker.register("echo", "echo-token", undeclared)
        with self.assertRaises(FeatureError) as raised:
            await CoreClient(self.broker.socket_path, "echo-token").call_capability(
                "download.provider", "submit", {}, deadline=1
            )
        self.assertEqual(raised.exception.code, "capability_not_declared")

    async def test_feature_publishes_only_declared_event_and_token_is_revocable(self):
        from telepiplex_plugin_sdk import CoreClient, FeatureError

        self.journal.set_subscriptions("renaming", ["download.completed"])
        publisher = manifest("open115", publishes=("download.completed",))
        self.broker.register("open115", "publisher-token", publisher)
        client = CoreClient(self.broker.socket_path, "publisher-token")

        event = await client.publish_event(
            "download.completed",
            {"path": "/downloads/show"},
            idempotency_key="download-1",
            deadline=1,
        )
        self.assertTrue(event["event_id"])
        self.assertEqual(len(self.journal.pending("renaming")), 1)

        with self.assertRaises(FeatureError) as raised:
            await client.publish_event("media.organized", {}, deadline=1)
        self.assertEqual(raised.exception.code, "event_not_declared")

        self.broker.unregister("publisher-token")
        with self.assertRaises(FeatureError) as raised:
            await client.publish_event("download.completed", {}, deadline=1)
        self.assertEqual(raised.exception.code, "unauthorized")

    async def test_authenticated_feature_can_send_bounded_user_notification(self):
        from telepiplex_plugin_sdk import CoreClient, FeatureError

        self.broker.register("open115", "notify-token", manifest("open115"))
        client = CoreClient(self.broker.socket_path, "notify-token")
        result = await client.notify_user(123, "下载完成", deadline=1)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.notifications, [(123, "下载完成")])

        with self.assertRaises(FeatureError) as raised:
            await client.notify_user(123, "x" * 5000, deadline=1)
        self.assertEqual(raised.exception.code, "invalid_notification")


if __name__ == "__main__":
    unittest.main()
