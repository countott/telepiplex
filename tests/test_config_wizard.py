import unittest
from unittest.mock import patch


class MediaSearchConfigWizardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from telepiplex_media_search.service import MediaSearchFeature

        self.feature = MediaSearchFeature(
            config={
                "search": {
                    "enable": True,
                    "prowlarr": {
                        "base_url": "http://old-prowlarr:9696",
                        "api_key": "old-prowlarr-key",
                        "timeout": 150,
                    },
                },
                "metadata": {
                    "tvdb": {
                        "enable": True,
                        "api_key": "old-tvdb-key",
                        "subscriber_pin": "old-pin",
                        "timeout": 15,
                    },
                },
                "ai": {
                    "enable": True,
                    "api_url": "https://old-ai.example/v1",
                    "api_key": "old-ai-key",
                    "model": "old-model",
                    "timeout": 60,
                },
            },
            core=None,
        )
        self.owner = {"chat_id": 10, "user_id": 1}

    async def _start(self):
        return await self.feature.command({
            **self.owner,
            "command": "media_search_config",
            "args": [],
        })

    async def _confirm(self):
        return await self.feature.callback({
            **self.owner,
            "namespace": "media-search",
            "payload": "config:confirm",
        })

    async def test_entry_only_exposes_public_sections(self):
        result = await self._start()

        self.assertEqual(result["session"]["state"], "open")
        text = result["actions"][0]["text"]
        buttons = result["actions"][0]["data"]["keyboard"]
        self.assertEqual(
            [button[0]["text"] for button in buttons],
            ["Prowlarr", "TVDB", "AI", "取消"],
        )
        for internal in ("timeout", "分类", "阈值", "MCP", "Indexer"):
            self.assertNotIn(internal, text)
        self.assertNotIn("old-prowlarr-key", text)
        self.assertNotIn("old-tvdb-key", text)
        self.assertNotIn("old-ai-key", text)

    async def test_prowlarr_flow_returns_only_public_patch_and_forces_enabled(self):
        await self._start()
        selected = await self.feature.callback({
            **self.owner,
            "namespace": "media-search",
            "payload": "config:prowlarr",
        })
        self.assertIn("Prowlarr 地址", selected["actions"][0]["text"])

        await self.feature.message({**self.owner, "text": "http://prowlarr:9696"})
        pending = await self.feature.message({
            **self.owner,
            "text": "new-prowlarr-key",
        })
        self.assertNotIn("config_patch", pending)
        self.assertEqual(pending["session"]["state"], "open")
        result = await self._confirm()

        self.assertEqual(result["session"]["state"], "close")
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["config_patch"], {
            "search": {
                "enable": True,
                "prowlarr": {
                    "base_url": "http://prowlarr:9696",
                    "api_key": "new-prowlarr-key",
                },
            },
        })

    async def test_tvdb_flow_supports_enable_and_preserving_secret_values(self):
        await self._start()
        await self.feature.callback({
            **self.owner,
            "namespace": "media-search",
            "payload": "config:tvdb",
        })
        await self.feature.callback({
            **self.owner,
            "namespace": "media-search",
            "payload": "config:boolean:on",
        })
        await self.feature.message({**self.owner, "text": "-"})
        await self.feature.message({**self.owner, "text": "new-pin"})
        result = await self._confirm()

        self.assertEqual(result["config_patch"], {
            "metadata": {
                "tvdb": {
                    "enable": True,
                    "api_key": "old-tvdb-key",
                    "subscriber_pin": "new-pin",
                },
            },
        })

    async def test_ai_flow_only_returns_enable_url_key_and_model(self):
        await self._start()
        await self.feature.callback({
            **self.owner,
            "namespace": "media-search",
            "payload": "config:ai",
        })
        await self.feature.callback({
            **self.owner,
            "namespace": "media-search",
            "payload": "config:boolean:on",
        })
        await self.feature.message({
            **self.owner, "text": "https://ai.example/v1"
        })
        await self.feature.message({**self.owner, "text": "new-ai-key"})
        await self.feature.message({**self.owner, "text": "gpt-example"})
        result = await self._confirm()

        self.assertEqual(result["config_patch"], {
            "ai": {
                "enable": True,
                "api_url": "https://ai.example/v1",
                "api_key": "new-ai-key",
                "model": "gpt-example",
            },
        })

    async def test_disable_finishes_without_requesting_hidden_fields(self):
        await self._start()
        await self.feature.callback({
            **self.owner,
            "namespace": "media-search",
            "payload": "config:ai",
        })
        pending = await self.feature.callback({
            **self.owner,
            "namespace": "media-search",
            "payload": "config:boolean:off",
        })
        self.assertNotIn("config_patch", pending)
        result = await self._confirm()

        self.assertEqual(result["config_patch"], {"ai": {"enable": False}})
        self.assertEqual(result["session"]["state"], "close")

    async def test_cancel_at_confirmation_does_not_submit_patch(self):
        await self._start()
        await self.feature.callback({
            **self.owner,
            "namespace": "media-search",
            "payload": "config:prowlarr",
        })
        await self.feature.message({**self.owner, "text": "http://prowlarr:9696"})
        pending = await self.feature.message({**self.owner, "text": "new-secret"})
        self.assertNotIn("new-secret", repr(pending))

        cancelled = await self.feature.callback({
            **self.owner,
            "namespace": "media-search",
            "payload": "config:cancel",
        })

        self.assertNotIn("config_patch", cancelled)
        self.assertEqual(cancelled["session"]["state"], "close")

    async def test_expired_confirmation_cannot_submit_patch(self):
        with patch(
            "telepiplex_media_search.config_wizard.time.monotonic",
            return_value=100,
        ):
            await self._start()
            await self.feature.callback({
                **self.owner,
                "namespace": "media-search",
                "payload": "config:prowlarr",
            })
            await self.feature.message({
                **self.owner, "text": "http://prowlarr:9696"
            })
            await self.feature.message({**self.owner, "text": "new-secret"})

        with patch(
            "telepiplex_media_search.config_wizard.time.monotonic",
            return_value=2000,
        ):
            expired = await self._confirm()

        self.assertNotIn("config_patch", expired)
        self.assertEqual(expired["session"]["state"], "close")


if __name__ == "__main__":
    unittest.main()
