import unittest


class FakeClient:
    def __init__(self):
        self.calls = []

    async def request(self, method, params, *, deadline, idempotency_key=""):
        self.calls.append((method, params, deadline, idempotency_key))
        return {"provider": params["capability"], "payload": params["payload"]}


class CapabilityRouterTest(unittest.IsolatedAsyncioTestCase):
    def _manifest(
        self,
        plugin_id,
        *,
        provides=(),
        requires=(),
        commands=(),
        callbacks=(),
    ):
        from app.core.plugin_manifest import PluginManifest

        return PluginManifest.from_mapping({
            "plugin_id": plugin_id,
            "name": plugin_id,
            "version": "1.0.0",
            "core_api": ">=1.0,<2.0",
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
            "callbacks": list(callbacks),
            "source": {
                "repository": "origin",
                "branch": f"feature/{plugin_id}",
                "commit": "a" * 40,
            },
        })

    async def test_routes_capability_with_context_and_idempotency(self):
        from app.core.capability_router import CapabilityRouter

        router = CapabilityRouter()
        provider = FakeClient()
        router.activate(
            "open115",
            self._manifest("open115", provides=(("download.provider", True),)),
            provider,
        )
        router.activate(
            "media-search",
            self._manifest("media-search", requires=("download.provider",), commands=("search",)),
            FakeClient(),
        )

        result = await router.call(
            "download.provider",
            "submit",
            {"url": "magnet:?x"},
            {"trace_id": "trace-1", "deadline": 2, "idempotency_key": "job-1"},
        )

        self.assertEqual(result["payload"]["url"], "magnet:?x")
        method, params, deadline, idempotency_key = provider.calls[0]
        self.assertEqual(method, "capability.call")
        self.assertEqual(params["method"], "submit")
        self.assertEqual(params["context"]["trace_id"], "trace-1")
        self.assertEqual(deadline, 2)
        self.assertEqual(idempotency_key, "job-1")
        self.assertEqual(router.command_route("search").plugin_id, "media-search")

    async def test_missing_requirement_refuses_activation_without_mutating_routes(self):
        from app.core.capability_router import CapabilityRouter, RoutingError

        router = CapabilityRouter()
        with self.assertRaises(RoutingError) as raised:
            router.activate(
                "media-search",
                self._manifest("media-search", requires=("download.provider",)),
                FakeClient(),
            )

        self.assertEqual(raised.exception.code, "missing_capability")
        self.assertEqual(router.plugin_status("media-search")["state"], "absent")
        self.assertEqual(router.snapshot.plugin_ids, ())

    async def test_exclusive_capability_and_command_conflicts_are_atomic(self):
        from app.core.capability_router import CapabilityRouter, RoutingError

        router = CapabilityRouter()
        first = self._manifest(
            "first",
            provides=(("storage.provider", True),),
            commands=("files",),
            callbacks=("files",),
        )
        router.activate("first", first, FakeClient())
        baseline = router.snapshot

        for manifest, expected_code in (
            (
                self._manifest("second", provides=(("storage.provider", True),)),
                "capability_conflict",
            ),
            (
                self._manifest("second", commands=("files",)),
                "command_conflict",
            ),
            (
                self._manifest("second", callbacks=("files",)),
                "callback_conflict",
            ),
        ):
            with self.subTest(expected_code=expected_code), self.assertRaises(RoutingError) as raised:
                router.activate("second", manifest, FakeClient())
            self.assertEqual(raised.exception.code, expected_code)
            self.assertIs(router.snapshot, baseline)

    async def test_prepared_routes_do_not_switch_until_committed(self):
        from app.core.capability_router import CapabilityRouter

        router = CapabilityRouter()
        manifest = self._manifest(
            "echo",
            provides=(("demo.echo", True),),
            commands=("echo",),
        )

        prepared = router.prepare_activation("echo", manifest, FakeClient())

        self.assertEqual(router.snapshot.plugin_ids, ())
        self.assertIsNone(router.command_route("echo"))
        router.commit(prepared)
        self.assertEqual(router.snapshot.plugin_ids, ("echo",))
        self.assertEqual(router.command_route("echo").plugin_id, "echo")

    async def test_provider_loss_blocks_dependents_but_not_unrelated_plugins(self):
        from app.core.capability_router import CapabilityRouter, RoutingError

        router = CapabilityRouter()
        router.activate(
            "open115",
            self._manifest("open115", provides=(("storage.provider", True),)),
            FakeClient(),
        )
        router.activate(
            "renaming",
            self._manifest(
                "renaming",
                provides=(("media.organizer", True),),
                requires=("storage.provider",),
                commands=("rename",),
            ),
            FakeClient(),
        )
        router.activate(
            "echo",
            self._manifest("echo", provides=(("demo.echo", True),), commands=("echo",)),
            FakeClient(),
        )

        router.deactivate("open115")

        status = router.plugin_status("renaming")
        self.assertEqual(status["state"], "blocked")
        self.assertEqual(status["missing_capabilities"], ["storage.provider"])
        self.assertIsNone(router.command_route("rename"))
        self.assertEqual(router.command_route("echo").plugin_id, "echo")
        with self.assertRaises(RoutingError) as raised:
            await router.call("media.organizer", "run", {}, {"deadline": 1})
        self.assertEqual(raised.exception.code, "capability_unavailable")


if __name__ == "__main__":
    unittest.main()
