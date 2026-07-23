import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class SyncConfigWizardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from telepiplex_sync.feature import SyncFeature

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.feature = SyncFeature(
            config={
                "plex": {
                    "base_url": "http://old-plex:32400",
                    "token": "old-plex-token",
                    "timeout": 15,
                },
                "tmdb": {"api_key": "old-tmdb-key", "timeout": 15},
                "fanart": {"api_key": "old-fanart-key", "timeout": 15},
                "mcp": {"enabled": True, "auth_token": "never-show"},
            },
            host=None,
            state_path=Path(self.temp.name) / "state",
        )
        self.owner = {"chat_id": 10, "user_id": 1}

    async def _start(self):
        return await self.feature.command({
            **self.owner,
            "command": "sync_config",
            "args": [],
        })

    async def _confirm(self):
        return await self.feature.callback({
            **self.owner,
            "namespace": "sync",
            "payload": "config:confirm",
        })

    async def test_entry_works_without_initializing_plex_service_and_hides_internals(self):
        result = await self._start()

        self.assertEqual(result["session"]["state"], "open")
        buttons = result["actions"][0]["data"]["keyboard"]
        self.assertEqual(
            [row[0]["text"] for row in buttons],
            ["Plex", "TMDB", "Fanart", "退出"],
        )
        text = result["actions"][0]["text"]
        for hidden in ("AI", "MCP", "timeout", "轮询", "max_tool_rounds"):
            self.assertNotIn(hidden, text)
        for secret in (
            "old-plex-token", "old-tmdb-key", "old-fanart-key",
            "never-show",
        ):
            self.assertNotIn(secret, text)
        self.assertIsNone(self.feature.service)

    async def test_plex_flow_returns_only_address_and_token(self):
        await self._start()
        await self.feature.callback({
            **self.owner, "namespace": "sync", "payload": "config:plex"
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
                    "namespace": "sync",
                    "payload": f"config:{section}",
                })
                await self.feature.message({**self.owner, "text": "-"})
                result = await self._confirm()
                self.assertEqual(result["config_patch"], {
                    section: {"api_key": expected},
                })

    async def test_removed_ai_section_cannot_start_a_configuration_flow(self):
        await self._start()
        result = await self.feature.callback({
            **self.owner, "namespace": "sync", "payload": "config:ai"
        })

        self.assertNotIn("config_patch", result)
        self.assertIn("不匹配", result["actions"][0]["text"])

    async def test_expired_confirmation_cannot_submit_patch(self):
        with patch(
            "telepiplex_sync.config_wizard.time.monotonic",
            return_value=100,
        ):
            await self._start()
            await self.feature.callback({
                **self.owner, "namespace": "sync", "payload": "config:tmdb"
            })
            await self.feature.message({**self.owner, "text": "new-key"})

        with patch(
            "telepiplex_sync.config_wizard.time.monotonic",
            return_value=2000,
        ):
            expired = await self._confirm()

        self.assertNotIn("config_patch", expired)
        self.assertEqual(expired["session"]["state"], "close")

    async def test_every_open_configuration_step_has_one_exit(self):
        def exits(result):
            return [
                button
                for action in result.get("actions", [])
                for row in (action.get("data") or {}).get("keyboard", [])
                for button in row
                if button.get("text") == "退出"
            ]

        result = await self._start()
        self.assertEqual(result["operation"]["state"], "awaiting_input")
        self.assertEqual(len(exits(result)), 1)
        result = await self.feature.callback({
            **self.owner, "namespace": "sync", "payload": "config:plex",
        })
        self.assertEqual(len(exits(result)), 1)
        result = await self.feature.message({**self.owner, "text": "bad-url"})
        self.assertEqual(len(exits(result)), 1)


if __name__ == "__main__":
    unittest.main()
