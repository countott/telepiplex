import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class PlexConfigWizardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from telepiplex_plex.feature import PlexFeature

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.feature = PlexFeature(
            config={
                "plex": {
                    "base_url": "http://old-plex:32400",
                    "token": "old-plex-token",
                    "timeout": 15,
                },
                "tmdb": {"api_key": "old-tmdb-key", "timeout": 15},
                "fanart": {"api_key": "old-fanart-key", "timeout": 15},
                "ai": {
                    "enabled": True,
                    "api_url": "https://old-ai.example/v1",
                    "api_key": "old-ai-key",
                    "model": "old-model",
                    "timeout": 30,
                    "max_tool_rounds": 3,
                },
                "mcp": {"enabled": True, "auth_token": "never-show"},
            },
            core=None,
            state_path=Path(self.temp.name) / "state",
        )
        self.owner = {"chat_id": 10, "user_id": 1}

    async def _start(self):
        return await self.feature.command({
            **self.owner,
            "command": "plex_config",
            "args": [],
        })

    async def _confirm(self):
        return await self.feature.callback({
            **self.owner,
            "namespace": "plex",
            "payload": "config:confirm",
        })

    async def test_entry_works_without_initializing_plex_service_and_hides_internals(self):
        result = await self._start()

        self.assertEqual(result["session"]["state"], "open")
        buttons = result["actions"][0]["data"]["keyboard"]
        self.assertEqual(
            [row[0]["text"] for row in buttons],
            ["Plex", "TMDB", "Fanart", "AI", "取消"],
        )
        text = result["actions"][0]["text"]
        for hidden in ("MCP", "timeout", "轮询", "max_tool_rounds"):
            self.assertNotIn(hidden, text)
        for secret in (
            "old-plex-token", "old-tmdb-key", "old-fanart-key",
            "old-ai-key", "never-show",
        ):
            self.assertNotIn(secret, text)
        self.assertIsNone(self.feature.service)

    async def test_plex_flow_returns_only_address_and_token(self):
        await self._start()
        await self.feature.callback({
            **self.owner, "namespace": "plex", "payload": "config:plex"
        })
        await self.feature.message({
            **self.owner, "text": "http://plex:32400"
        })
        pending = await self.feature.message({
            **self.owner, "text": "new-plex-token"
        })
        self.assertNotIn("config_patch", pending)
        result = await self._confirm()

        self.assertEqual(result["config_patch"], {
            "plex": {
                "base_url": "http://plex:32400",
                "token": "new-plex-token",
            },
        })
        self.assertEqual(result["session"]["state"], "close")

    async def test_tmdb_and_fanart_each_return_only_api_key(self):
        for section, expected in (
            ("tmdb", "old-tmdb-key"),
            ("fanart", "old-fanart-key"),
        ):
            with self.subTest(section=section):
                await self._start()
                await self.feature.callback({
                    **self.owner,
                    "namespace": "plex",
                    "payload": f"config:{section}",
                })
                await self.feature.message({**self.owner, "text": "-"})
                result = await self._confirm()
                self.assertEqual(result["config_patch"], {
                    section: {"api_key": expected},
                })

    async def test_ai_flow_uses_enabled_schema_key_and_public_fields_only(self):
        await self._start()
        await self.feature.callback({
            **self.owner, "namespace": "plex", "payload": "config:ai"
        })
        await self.feature.callback({
            **self.owner,
            "namespace": "plex",
            "payload": "config:boolean:on",
        })
        await self.feature.message({
            **self.owner, "text": "https://ai.example/v1"
        })
        await self.feature.message({**self.owner, "text": "new-ai-key"})
        await self.feature.message({**self.owner, "text": "new-model"})
        result = await self._confirm()

        self.assertEqual(result["config_patch"], {
            "ai": {
                "enabled": True,
                "api_url": "https://ai.example/v1",
                "api_key": "new-ai-key",
                "model": "new-model",
            },
        })

    async def test_ai_can_be_disabled_without_exposing_mcp_or_thresholds(self):
        await self._start()
        await self.feature.callback({
            **self.owner, "namespace": "plex", "payload": "config:ai"
        })
        pending = await self.feature.callback({
            **self.owner,
            "namespace": "plex",
            "payload": "config:boolean:off",
        })
        self.assertNotIn("config_patch", pending)
        result = await self._confirm()

        self.assertEqual(result["config_patch"], {"ai": {"enabled": False}})

    async def test_expired_confirmation_cannot_submit_patch(self):
        with patch(
            "telepiplex_plex.config_wizard.time.monotonic",
            return_value=100,
        ):
            await self._start()
            await self.feature.callback({
                **self.owner, "namespace": "plex", "payload": "config:tmdb"
            })
            await self.feature.message({**self.owner, "text": "new-key"})

        with patch(
            "telepiplex_plex.config_wizard.time.monotonic",
            return_value=2000,
        ):
            expired = await self._confirm()

        self.assertNotIn("config_patch", expired)
        self.assertEqual(expired["session"]["state"], "close")


if __name__ == "__main__":
    unittest.main()
