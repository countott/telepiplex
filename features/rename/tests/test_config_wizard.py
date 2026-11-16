import unittest
from unittest.mock import patch


class RenameConfigWizardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from telepiplex_rename.service import RenameFeature

        self.feature = RenameFeature(
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
            host=None,
        )
        self.owner = {"chat_id": 10, "user_id": 1}

    async def _start(self):
        return await self.feature.command({
            **self.owner,
            "command": "rename_config",
            "args": [],
        })

    async def _confirm(self):
        return await self.feature.callback({
            **self.owner,
            "namespace": "rename",
            "payload": "config:confirm",
        })

    async def test_entry_exposes_only_active_ai_configuration(self):
        result = await self._start()

        self.assertEqual(result["session"]["state"], "open")
        buttons = result["actions"][0]["data"]["keyboard"]
        self.assertEqual(
            [row[0]["text"] for row in buttons],
            ["AI", "退出"],
        )
        text = result["actions"][0]["text"]
        for hidden in ("TVDB", "timeout", "阈值", "未整理", "selection", "MCP"):
            self.assertNotIn(hidden, text)
        self.assertNotIn("old-tvdb-key", text)
        self.assertNotIn("old-ai-key", text)

    async def test_stale_tvdb_button_cannot_submit_configuration(self):
        await self._start()
        await self.feature.callback({
            **self.owner,
            "namespace": "rename",
            "payload": "config:tvdb",
        })
        await self.feature.callback({
            **self.owner,
            "namespace": "rename",
            "payload": "config:boolean:off",
        })
        result = await self._confirm()

        self.assertNotIn("config_patch", result)

    async def test_basic_connections_open_host_editor_and_stale_confirm_cannot_write(self):
        for action,target in [["ai","rename_ai"]]:
            await self._start()
            result = await self.feature.callback({**self.owner, 'namespace':'rename', 'payload':'config:'+action})
            self.assertEqual(result['config_navigation'],target)
            self.assertEqual(result['session']['state'],'close')
            self.assertNotIn('config_patch',result)
            self.assertNotIn('config_patch',await self._confirm())

    async def test_exit_closes_without_submitting_patch(self):
        await self._start()

        result = await self.feature.callback({
            **self.owner,
            "namespace": "rename",
            "payload": "config:cancel",
        })

        self.assertEqual(result["session"]["state"], "close")
        self.assertEqual(
            result["actions"][0]["text"],
            "已退出 rename 配置。",
        )
        self.assertNotIn("config_patch", result)

    async def test_expired_confirmation_cannot_submit_patch(self):
        with patch(
            "telepiplex_rename.config_wizard.time.monotonic",
            return_value=100,
        ):
            await self._start()
            await self.feature.callback({
                **self.owner,
                "namespace": "rename",
                "payload": "config:ai",
            })
            await self.feature.callback({
                **self.owner,
                "namespace": "rename",
                "payload": "config:boolean:off",
            })

        with patch(
            "telepiplex_rename.config_wizard.time.monotonic",
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
        result = await self.feature.callback({**self.owner,"namespace":"rename","payload":"config:ai"})
        self.assertEqual(result['session']['state'],'close')
        self.assertEqual(len(exits(result)),0)


if __name__ == "__main__":
    unittest.main()
