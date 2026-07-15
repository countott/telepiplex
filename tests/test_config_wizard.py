import unittest
from unittest.mock import patch


class RenamingConfigWizardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from telepiplex_renaming.service import RenamingFeature

        self.feature = RenamingFeature(
            config={
                "unorganized_path": "/Unorganized",
                "storage_timeout": 120,
                "metadata_timeout": 120,
                "selection": {"unmatched_large_ratio": 0.25},
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
            "command": "renaming_config",
            "args": [],
        })

    async def _confirm(self):
        return await self.feature.callback({
            **self.owner,
            "namespace": "renaming",
            "payload": "config:confirm",
        })

    async def test_entry_exposes_only_tvdb_and_ai(self):
        result = await self._start()

        self.assertEqual(result["session"]["state"], "open")
        buttons = result["actions"][0]["data"]["keyboard"]
        self.assertEqual(
            [row[0]["text"] for row in buttons],
            ["TVDB", "AI", "退出"],
        )
        text = result["actions"][0]["text"]
        for hidden in ("timeout", "阈值", "未整理", "selection", "MCP"):
            self.assertNotIn(hidden, text)
        self.assertNotIn("old-tvdb-key", text)
        self.assertNotIn("old-ai-key", text)

    async def test_tvdb_flow_returns_enable_key_and_pin_only(self):
        await self._start()
        await self.feature.callback({
            **self.owner,
            "namespace": "renaming",
            "payload": "config:tvdb",
        })
        await self.feature.callback({
            **self.owner,
            "namespace": "renaming",
            "payload": "config:boolean:on",
        })
        await self.feature.message({**self.owner, "text": "new-tvdb-key"})
        await self.feature.message({**self.owner, "text": "-"})
        result = await self._confirm()

        self.assertEqual(result["config_patch"], {
            "metadata": {
                "tvdb": {
                    "enable": True,
                    "api_key": "new-tvdb-key",
                    "subscriber_pin": "old-pin",
                },
            },
        })

    async def test_ai_flow_returns_enable_url_key_and_model_only(self):
        await self._start()
        await self.feature.callback({
            **self.owner,
            "namespace": "renaming",
            "payload": "config:ai",
        })
        await self.feature.callback({
            **self.owner,
            "namespace": "renaming",
            "payload": "config:boolean:on",
        })
        await self.feature.message({
            **self.owner, "text": "https://ai.example/v1"
        })
        await self.feature.message({**self.owner, "text": "-"})
        await self.feature.message({**self.owner, "text": "new-model"})
        result = await self._confirm()

        self.assertEqual(result["config_patch"], {
            "ai": {
                "enable": True,
                "api_url": "https://ai.example/v1",
                "api_key": "old-ai-key",
                "model": "new-model",
            },
        })

    async def test_sections_can_be_disabled_directly(self):
        for section, expected in (
            ("tvdb", {"metadata": {"tvdb": {"enable": False}}}),
            ("ai", {"ai": {"enable": False}}),
        ):
            with self.subTest(section=section):
                await self._start()
                await self.feature.callback({
                    **self.owner,
                    "namespace": "renaming",
                    "payload": f"config:{section}",
                })
                pending = await self.feature.callback({
                    **self.owner,
                    "namespace": "renaming",
                    "payload": "config:boolean:off",
                })
                self.assertNotIn("config_patch", pending)
                result = await self._confirm()
                self.assertEqual(result["config_patch"], expected)

    async def test_expired_confirmation_cannot_submit_patch(self):
        with patch(
            "telepiplex_renaming.config_wizard.time.monotonic",
            return_value=100,
        ):
            await self._start()
            await self.feature.callback({
                **self.owner,
                "namespace": "renaming",
                "payload": "config:ai",
            })
            await self.feature.callback({
                **self.owner,
                "namespace": "renaming",
                "payload": "config:boolean:off",
            })

        with patch(
            "telepiplex_renaming.config_wizard.time.monotonic",
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
        self.assertEqual(result["operation"]["control"], "exit")
        self.assertEqual(len(exits(result)), 1)
        result = await self.feature.callback({
            **self.owner, "namespace": "renaming", "payload": "config:ai",
        })
        self.assertEqual(len(exits(result)), 1)
        result = await self.feature.callback({
            **self.owner, "namespace": "renaming", "payload": "config:boolean:on",
        })
        self.assertEqual(len(exits(result)), 1)
        result = await self.feature.message({**self.owner, "text": "bad-url"})
        self.assertEqual(len(exits(result)), 1)


if __name__ == "__main__":
    unittest.main()
