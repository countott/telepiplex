import unittest
from unittest.mock import patch


class SearchConfigWizardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from telepiplex_search.service import SearchFeature

        self.feature = SearchFeature(
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
                    "tmdb": {
                        "enable": True,
                        "api_key": "old-tmdb-key",
                        "timeout": 15,
                    },
                    "tvdb": {
                        "enable": True,
                        "api_key": "old-tvdb-key",
                        "subscriber_pin": "old-pin",
                        "timeout": 15,
                    },
                },
            },
            host=None,
        )
        self.owner = {"chat_id": 10, "user_id": 1}

    async def _start(self):
        return await self.feature.command({
            **self.owner,
            "command": "search_config",
            "args": [],
        })

    async def _confirm(self):
        return await self.feature.callback({
            **self.owner,
            "namespace": "search",
            "payload": "config:confirm",
        })

    async def test_entry_only_exposes_public_sections(self):
        result = await self._start()

        self.assertEqual(result["session"]["state"], "open")
        text = result["actions"][0]["text"]
        buttons = result["actions"][0]["data"]["keyboard"]
        self.assertEqual(
            [button[0]["text"] for button in buttons],
            ["Prowlarr", "TMDB", "TVDB", "Provider 接口参数", "退出"],
        )
        for internal in ("timeout", "分类", "阈值", "MCP", "Indexer"):
            self.assertNotIn(internal, text)
        self.assertNotIn("old-prowlarr-key", text)
        self.assertNotIn("old-tvdb-key", text)
        self.assertNotIn("old-tmdb-key", text)

    async def test_basic_connections_open_host_editor_and_stale_confirm_cannot_write(self):
        for action,target in [["prowlarr","search_prowlarr"],["tvdb","search_tvdb"]]:
            await self._start()
            result = await self.feature.callback({**self.owner, 'namespace':'search', 'payload':'config:'+action})
            self.assertEqual(result['config_navigation'],target)
            self.assertEqual(result['session']['state'],'close')
            self.assertNotIn('config_patch',result)
            self.assertNotIn('config_patch',await self._confirm())

    async def test_tmdb_flow_updates_only_local_enable_gate(self):
        await self._start()
        await self.feature.callback({
            **self.owner,
            "namespace": "search",
            "payload": "config:tmdb",
        })
        await self.feature.callback({
            **self.owner,
            "namespace": "search",
            "payload": "config:boolean:on",
        })
        result = await self._confirm()

        self.assertEqual(result["config_patch"], {
            "metadata": {
                "tmdb": {
                    "enable": True,
                },
            },
        })

    async def test_cancel_and_expired_local_confirmation_do_not_submit_patch(self):
        for expired in (True,False):
            with patch("telepiplex_search.config_wizard.time.monotonic",return_value=100):
                await self._start()
                await self.feature.callback({**self.owner,'payload':'config:tmdb'})
                await self.feature.callback({**self.owner,'payload':'config:boolean:off'})
                if not expired:
                    await self.feature.callback({**self.owner,'payload':'config:cancel'})
            with patch("telepiplex_search.config_wizard.time.monotonic",return_value=2000):
                self.assertNotIn('config_patch',await self._confirm())

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
        self.assertEqual(len(exits(result)), 1)
        result = await self.feature.callback({**self.owner,"namespace":"search","payload":"config:prowlarr"})
        self.assertEqual(result['session']['state'],'close')
        self.assertEqual(len(exits(result)),0)


if __name__ == "__main__":
    unittest.main()
