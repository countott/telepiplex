import json
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from telepiplex_search.config_wizard import API_PROVIDERS
from telepiplex_search.adapters import prowlarr
from telepiplex_search.service import SearchFeature


class ProviderConfigTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner = {"user_id": 1, "chat_id": 10}
        self.feature = SearchFeature(config={
            "search": {"prowlarr": {"api_key": "original-prowlarr-secret", "timeout": 200}},
            "metadata": {"tmdb": {"api_key": "original-tmdb-secret"},
                         "tvdb": {"subscriber_pin": "original-pin"}},
        }, host=None)
        await self.feature.command({**self.owner, "command": "search_config"})
        await self.click("apis")

    async def click(self, action, **owner):
        return await self.feature.callback({**self.owner, **owner, "payload": "config:" + action})

    async def set_field(self, provider, name, value):
        await self.click("api:" + provider)
        await self.click(f"field:{provider}:{name}")
        return await self.feature.message({**self.owner, "text": value})

    async def save(self):
        pending = await self.click("api_save")
        self.assertNotIn("config_patch", pending)
        return await self.click("confirm")

    async def test_batch_edits_across_providers_return_only_staged_fields(self):
        await self.set_field("tmdb","enable","off")
        await self.click("apis")
        await self.set_field("prowlarr", "timeout", "123")
        self.assertEqual(self.feature.config["search"]["prowlarr"]["timeout"], 200)
        saved = await self.save()
        self.assertEqual(saved["config_patch"], {
            "metadata": {"tmdb": {"enable":False}},
            "search": {"prowlarr": {"timeout": 123}},
        })
        self.assertEqual(saved["actions"], [])
        self.assertEqual(saved["session"]["state"], "close")

    async def test_secret_clear_keep_and_enable_are_explicit(self):
        for provider,name,target in [('tvdb','subscriber_pin','search_tvdb'),('prowlarr','api_key','search_prowlarr'),('tmdb','timeout','search_tmdb_endpoint'),('anilist','endpoint','search_anilist')]:
            await self.feature.command({**self.owner,'command':'search_config'})
            result=await self.click(f'field:{provider}:{name}')
            self.assertEqual(result['config_navigation'],target)
            self.assertNotIn('config_patch',result)
            self.assertNotIn('original-pin',repr(result))

    async def test_public_api_endpoint_languages_and_indexer_types(self):
        await self.set_field("wikipedia", "languages", "zh, en, zh")
        await self.set_field("prowlarr", "indexer_ids", "3,1,3")
        await self.set_field("prowlarr", "first_wave_indexer_ids", "[3, 1, 3]")
        await self.set_field("prowlarr", "wave_delay", "0.5")
        patch = (await self.save())["config_patch"]
        self.assertEqual(patch["metadata"]["wikipedia"]["languages"], ["zh", "en"])
        self.assertEqual(patch["search"]["prowlarr"], {
            "indexer_ids": "3,1", "first_wave_indexer_ids": [3, 1], "wave_delay": 0.5,
        })

    async def test_saved_zero_cache_ttl_reaches_the_provider(self):
        await self.set_field("douban", "cache_ttl", "0")
        configured = (await self.save())["config_patch"]
        reloaded = SearchFeature(config=configured, host=None)
        with patch("telepiplex_search.service.lookup_douban_evidence", return_value={}) as lookup:
            reloaded._douban_provider({"source_queries": {"douban": ["Test Movie"]}})
        self.assertEqual(lookup.call_args.kwargs["cache_ttl"], 0)

    async def test_saved_status_timeout_reaches_the_http_request(self):
        for value in ("1", "300"):
            with self.subTest(timeout=value):
                await self.feature.command({**self.owner, "command": "search_config"})
                await self.click("apis")
                await self.set_field("prowlarr", "status_timeout", value)
                configured = (await self.save())["config_patch"]
                configured["search"]["prowlarr"].update(base_url="http://prowlarr:9696", api_key="test-key")
                with patch.object(prowlarr.runtime_context, "config", configured), patch.object(prowlarr.requests, "get") as get:
                    get.return_value.json.return_value = []
                    prowlarr.list_prowlarr_indexers()
                self.assertEqual(get.call_args.kwargs["timeout"], float(value))

    async def test_invalid_inputs_do_not_change_the_draft(self):
        for provider, field, invalid in (
            ("prowlarr", "timeout", "nan"), ("prowlarr", "timeout", "inf"),
            ("prowlarr", "timeout", "601"), ("prowlarr", "result_limit", "1.5"),
            ("prowlarr", "first_wave_indexer_ids", "-2"), ("prowlarr", "indexer_ids", "0"),
        ):
            with self.subTest(field=field, invalid=invalid):
                response = await self.set_field(provider, field, invalid)
                self.assertIn("输入无效", response["actions"][0]["text"])
                self.assertFalse(self.feature.config_wizard.sessions[(10, 1)].get("api_patch"))

    async def test_foreign_owner_cancel_and_stale_save_never_commit(self):
        await self.set_field("prowlarr", "timeout", "300")
        foreign = await self.click("api_save", user_id=2)
        self.assertEqual(foreign["session"]["state"], "close")
        await self.click("cancel")
        stale = await self.click("confirm")
        self.assertNotIn("config_patch", stale)
        self.assertEqual(self.feature.config["metadata"]["tmdb"]["api_key"], "original-tmdb-secret")

    async def test_provider_list_contains_only_supported_adapters(self):
        result = await self.click("apis")
        names = [row[0]["text"] for row in result["actions"][0]["data"]["keyboard"]]
        self.assertEqual(names[:-1], ["Prowlarr", "TMDB", "TVDB", "AniList", "Wikipedia", "豆瓣"])

    async def test_config_operation_shows_the_wizard_text_in_a_text_segment(self):
        results = [await self.feature.command({**self.owner, "command": "search_config"})]
        results.append(await self.click("apis"))
        results.append(await self.click("api:prowlarr"))
        results.append(await self.click("field:prowlarr:timeout"))
        results.append(await self.feature.message({**self.owner, "text": "nan"}))
        results.append(await self.feature.message({**self.owner, "text": "123"}))
        results.append(await self.set_field("prowlarr", "result_limit", "8"))
        results.append(await self.click("api_save"))
        for result in results:
            with self.subTest(stage=result["operation"]["stage"]):
                self.assertEqual(result["operation"]["segment"]["presentation_kind"], "text")
                self.assertEqual(result["operation"]["status_text"], result["actions"][0]["text"])
                self.assertNotIn("new-private-token", repr(result["operation"]))
                self.assertNotIn("original-prowlarr-secret", repr(result["operation"]))

    def test_editable_fields_and_defaults_match_the_public_schema(self):
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "config.schema.json").read_text())
        for provider, (_, path, fields) in API_PROVIDERS.items():
            section = schema
            for part in path:
                section = section["properties"][part]
            for name, (_, kind, default, minimum, maximum) in fields.items():
                with self.subTest(provider=provider, field=name):
                    field_schema = section["properties"][name]
                    Draft202012Validator(field_schema).validate(default)
                    if minimum is not None:
                        self.assertEqual(field_schema["minimum"], minimum)
                        self.assertEqual(field_schema["maximum"], maximum)
                    if kind == "secret":
                        self.assertTrue(field_schema["writeOnly"])

    def test_advanced_prompt_uses_the_release_contract(self):
        import tempfile
        import yaml
        from telepiplex_search.config_wizard import provider_contract
        source=Path(__file__).resolve().parents[1]
        defaults=yaml.safe_load((source/'config.default.yaml').read_text())
        schema=json.loads((source/'config.schema.json').read_text())
        defaults['search']['prowlarr']['result_limit']=20
        schema['properties']['search']['properties']['prowlarr']['properties']['result_limit']['maximum']=25
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'config.default.yaml').write_text(yaml.safe_dump(defaults))
            (root/'config.schema.json').write_text(json.dumps(schema))
            self.assertEqual(provider_contract(root)['prowlarr'][2]['result_limit'][2:],(20,1,25))
