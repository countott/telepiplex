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


class FailingProviderClient:
    async def request(self, method, params, *, deadline, idempotency_key=""):
        from app.runtime.plugin_contract import ContractError

        raise ContractError(
            "metadata_source_unavailable",
            "metadata providers are temporarily unavailable",
        )


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
        self.milestones = []
        self.operation_sink = AsyncMock(return_value={"accepted": True, "revision": 1})
        self.broker = RuntimeBroker(
            self.router,
            self.journal,
            root / "host.sock",
            notification_sink=lambda user_id, text: self.notifications.append((user_id, text)),
            milestone_sink=lambda plugin_id, payload: self.milestones.append(
                (plugin_id, payload)
            ),
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

    async def test_provider_error_code_survives_the_full_feature_to_feature_route(self):
        from telepiplex_plugin_sdk import HostClient, FeatureError

        self.router.activate(
            "search",
            manifest("search", provides=("media.search",)),
            FailingProviderClient(),
        )
        self.broker.register(
            "rename",
            "rename-token",
            manifest("rename", requires=("media.search",)),
        )

        with self.assertRaises(FeatureError) as raised:
            await HostClient(
                self.broker.socket_path,
                "rename-token",
            ).call_capability(
                "media.search",
                "resolve_metadata",
                {"query": "Honey and Clover"},
                deadline=1,
            )

        self.assertEqual(
            raised.exception.code,
            "metadata_source_unavailable",
        )
        self.assertEqual(
            raised.exception.message,
            "metadata providers are temporarily unavailable",
        )

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

    async def test_feature_publishes_operation_owned_photo_milestone(self):
        from telepiplex_plugin_sdk import HostClient

        self.broker.register("search", "milestone-token", manifest("search"))
        client = HostClient(self.broker.socket_path, "milestone-token")

        result = await client.publish_operation_milestone(
            "op-1",
            "media-douban-35981510",
            "繁花 (Blossoms Shanghai)\n2023｜中国大陆｜剧集｜全剧",
            photo_url="https://img.example/blossoms.jpg",
            deadline=1,
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(
            self.milestones,
            [(
                "search",
                {
                    "operation_id": "op-1",
                    "milestone_id": "media-douban-35981510",
                    "mode": "identity",
                    "text": "繁花 (Blossoms Shanghai)\n2023｜中国大陆｜剧集｜全剧",
                    "photo_url": "https://img.example/blossoms.jpg",
                },
            )],
        )

    async def test_feature_can_seal_operation_stage_with_semantic_mode(self):
        from telepiplex_plugin_sdk import HostClient

        self.broker.register("download", "seal-token", manifest("download"))
        client = HostClient(self.broker.socket_path, "seal-token")

        result = await client.seal_operation_stage(
            "op-1",
            "download-completed",
            "✅ 115 下载完成",
            deadline=1,
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(self.milestones, [(
            "download",
            {
                "operation_id": "op-1",
                "milestone_id": "download-completed",
                "mode": "stage",
                "text": "✅ 115 下载完成",
                "photo_url": "",
            },
        )])

    async def test_operation_milestone_preserves_coordinator_error_code(self):
        from app.runtime.interaction_coordinator import InteractionError
        from telepiplex_plugin_sdk import FeatureError, HostClient

        def reject_milestone(_plugin_id, _payload):
            raise InteractionError(
                "owner_mismatch",
                "operation belongs to another Feature",
            )

        self.broker.milestone_sink = reject_milestone
        self.broker.register("search", "milestone-error-token", manifest("search"))

        with self.assertRaises(FeatureError) as raised:
            await HostClient(
                self.broker.socket_path,
                "milestone-error-token",
            ).publish_operation_milestone(
                "op-1",
                "media-owner-check",
                "媒体身份",
                deadline=1,
            )

        self.assertEqual(raised.exception.code, "owner_mismatch")
        self.assertEqual(
            raised.exception.message,
            "operation belongs to another Feature",
        )

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
