import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

from telepiplex_media_search.service import MediaSearchFeature
from telepiplex_media_search.source_tools import (
    FIRST_ROUND_TOOL,
    TARGETED_TOOLS,
    SourceToolGateway,
    ToolValidationError,
)


def _arguments():
    return {
        "intent": {
            "title_hints": ["蝙蝠侠：侠影之谜", "Batman Begins"],
            "media_type_hint": "movie",
            "year_hint": "2005",
            "scope": "work",
            "season_number": None,
            "episode_number": None,
        },
        "source_queries": {
            "wikipedia_zh": ["蝙蝠侠：侠影之谜"],
            "wikipedia_en": ["Batman Begins"],
            "douban": ["蝙蝠侠：侠影之谜"],
            "tvdb": ["Batman Begins"],
        },
    }


class SourceToolSchemaTest(unittest.TestCase):
    def test_registered_tools_have_closed_object_schemas(self):
        tools = [FIRST_ROUND_TOOL, *TARGETED_TOOLS]

        self.assertEqual(
            FIRST_ROUND_TOOL["function"]["name"],
            "search_media_sources",
        )
        self.assertEqual(
            {item["function"]["name"] for item in TARGETED_TOOLS},
            {
                "lookup_wikipedia_entity",
                "lookup_douban_subject",
                "lookup_tvdb_entity",
                "lookup_tvdb_episodes",
            },
        )
        for tool in tools:
            parameters = tool["function"]["parameters"]
            self.assertEqual(parameters["type"], "object")
            self.assertFalse(parameters["additionalProperties"])


class SourceToolGatewayTest(unittest.IsolatedAsyncioTestCase):
    async def test_first_round_starts_all_three_sources_before_any_finishes(self):
        barrier = threading.Barrier(3)
        received = {}

        def provider(name):
            def call(payload):
                received[name] = payload
                barrier.wait(timeout=2)
                return {
                    "source": name,
                    "status": "not_found",
                    "facts": [],
                    "source_urls": [],
                }

            return call

        gateway = SourceToolGateway(
            {
                "wikipedia": provider("wikipedia"),
                "douban": provider("douban"),
                "tvdb": provider("tvdb"),
            },
            config={
                "metadata": {
                    "tvdb": {"enable": True, "api_key": "server-secret"},
                },
            },
        )

        result = await asyncio.wait_for(
            gateway.search_media_sources("蝙蝠侠：谍影之谜", _arguments()),
            timeout=3,
        )

        self.assertEqual(
            {item["source"] for item in result["sources"]},
            {"wikipedia", "douban", "tvdb"},
        )
        self.assertEqual(
            received["wikipedia"]["source_queries"]["wikipedia_zh"],
            ["蝙蝠侠：侠影之谜"],
        )
        self.assertEqual(
            received["wikipedia"]["source_queries"]["wikipedia_en"],
            ["Batman Begins"],
        )
        self.assertEqual(
            result["sources"][2]["credential_state"],
            "configured",
        )

    async def test_first_round_rejects_model_raw_query_and_sensitive_fields(self):
        gateway = SourceToolGateway({})
        for key, value in (
            ("raw_query", "rewritten"),
            ("api_key", "secret"),
            ("headers", {"Authorization": "Bearer secret"}),
            ("url", "https://example.invalid"),
        ):
            arguments = _arguments()
            arguments[key] = value
            with self.subTest(key=key):
                with self.assertRaises(ToolValidationError):
                    await gateway.search_media_sources("original", arguments)

    async def test_first_round_rejects_too_many_or_oversized_queries(self):
        gateway = SourceToolGateway({})
        too_many = _arguments()
        too_many["source_queries"]["douban"] = ["1", "2", "3", "4"]
        with self.assertRaises(ToolValidationError):
            await gateway.search_media_sources("query", too_many)

        too_long = _arguments()
        too_long["source_queries"]["tvdb"] = ["x" * 161]
        with self.assertRaises(ToolValidationError):
            await gateway.search_media_sources("query", too_long)

    async def test_tool_result_drops_raw_bodies_and_sensitive_values(self):
        def wikipedia(_payload):
            return {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "title": "Batman Begins",
                    "english_title": "Batman Begins",
                    "year": "2005",
                    "media_type": "movie",
                    "wikibase_item": "Q166262",
                    "url": "https://en.wikipedia.org/wiki/Batman_Begins",
                    "extract": "very long raw page body",
                    "headers": {"Authorization": "secret"},
                    "api_key": "secret",
                }],
                "source_urls": [
                    "https://en.wikipedia.org/wiki/Batman_Begins",
                ],
            }

        gateway = SourceToolGateway({"wikipedia": wikipedia})
        result = await gateway.search_media_sources("Batman", _arguments())
        encoded = repr(result)

        self.assertNotIn("raw page body", encoded)
        self.assertNotIn("Authorization", encoded)
        self.assertNotIn("server-secret", encoded)
        fact = next(
            item for item in result["sources"]
            if item["source"] == "wikipedia"
        )["facts"][0]
        self.assertEqual(fact["wikibase_item"], "Q166262")
        self.assertEqual(fact["english_title"], "Batman Begins")

    async def test_targeted_lookup_rejects_unknown_fact_reference(self):
        gateway = SourceToolGateway(
            {},
            targeted_handlers={
                "lookup_wikipedia_entity": lambda _arguments: {},
            },
        )

        with self.assertRaises(ToolValidationError):
            await gateway.execute_targeted(
                "lookup_wikipedia_entity",
                {
                    "fact_ids": ["wikipedia:invented"],
                    "queries": ["Batman Begins"],
                },
                known_facts={"wikipedia:Q166262": {}},
            )

    @patch(
        "telepiplex_media_search.service.build_confirmable_search_plan",
        new_callable=AsyncMock,
    )
    async def test_feature_plain_text_build_passes_server_gateway(self, build):
        build.return_value = {"plan_id": "p-text", "candidates": []}
        feature = MediaSearchFeature(config={}, core=Mock())

        result = await feature._build_plan("Batman", "p-text")

        self.assertEqual(result["plan_id"], "p-text")
        gateway = build.call_args.kwargs["source_gateway"]
        self.assertIsInstance(gateway, SourceToolGateway)
        self.assertEqual(
            set(gateway.providers),
            {"wikipedia", "douban", "tvdb"},
        )


if __name__ == "__main__":
    unittest.main()
