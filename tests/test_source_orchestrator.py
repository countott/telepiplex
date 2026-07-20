import json
import unittest

from telepiplex_media_search.source_orchestrator import orchestrate_sources
from telepiplex_media_search.source_tools import SourceToolGateway


def _response(message):
    return {"choices": [{"message": message}]}


def _tool_call(name, arguments, number=1):
    return {
        "id": f"call-{number}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def _first_arguments():
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


def _final_payload():
    return {
        "status": "insufficient_evidence",
        "intent": _first_arguments()["intent"],
        "equivalence_edges": [],
        "candidate_assessments": [],
        "recommended_next_action": "stop",
    }


def _assistant_tool(name, arguments, number=1):
    return _response({
        "role": "assistant",
        "content": None,
        "tool_calls": [_tool_call(name, arguments, number)],
    })


def _assistant_final():
    return _response({
        "role": "assistant",
        "content": json.dumps(_final_payload(), ensure_ascii=False),
    })


class ScriptedAi:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.responses.pop(0)


def _gateway():
    def provider(name):
        return lambda _payload: {
            "source": name,
            "status": "not_found",
            "facts": [],
            "source_urls": [],
        }

    return SourceToolGateway(
        {
            "wikipedia": provider("wikipedia"),
            "douban": provider("douban"),
            "tvdb": provider("tvdb"),
        },
        targeted_handlers={
            "lookup_wikipedia_entity": lambda _arguments: {
                "source": "wikipedia",
                "status": "not_found",
                "facts": [],
                "source_urls": [],
            },
            "lookup_douban_subject": lambda _arguments: {
                "source": "douban",
                "status": "not_found",
                "facts": [],
                "source_urls": [],
            },
            "lookup_tvdb_entity": lambda _arguments: {
                "source": "tvdb",
                "status": "not_found",
                "facts": [],
                "source_urls": [],
            },
        },
    )


class SourceOrchestratorTest(unittest.IsolatedAsyncioTestCase):
    async def test_first_action_is_forced_to_search_media_sources(self):
        ai = ScriptedAi([
            _assistant_tool("search_media_sources", _first_arguments()),
            _assistant_final(),
        ])

        outcome = await orchestrate_sources(
            "蝙蝠侠：谍影之谜",
            _gateway(),
            ai_call=ai,
            config={"protocol": "openai_tools_v1"},
        )

        first_kwargs = ai.calls[0][1]
        self.assertEqual(
            first_kwargs["tool_choice"]["function"]["name"],
            "search_media_sources",
        )
        self.assertEqual(
            [item["function"]["name"] for item in first_kwargs["tools"]],
            ["search_media_sources"],
        )
        self.assertEqual(outcome.status, "insufficient_evidence")
        self.assertEqual(outcome.targeted_rounds, 0)

    async def test_one_first_action_correction_then_protocol_fallback(self):
        invalid = _response({
            "role": "assistant",
            "content": json.dumps(_final_payload()),
        })
        ai = ScriptedAi([invalid, invalid])

        outcome = await orchestrate_sources(
            "Batman",
            _gateway(),
            ai_call=ai,
            config={"protocol": "openai_tools_v1"},
        )

        self.assertEqual(outcome.status, "fallback")
        self.assertEqual(outcome.fallback_reason, "tool_protocol_invalid")
        self.assertEqual(len(ai.calls), 2)
        self.assertIn(
            "protocol_correction",
            repr(ai.calls[1][0]),
        )

    async def test_ai_can_choose_two_targeted_rounds_then_stop(self):
        targeted = {
            "fact_ids": [],
            "queries": ["Batman Begins"],
        }
        ai = ScriptedAi([
            _assistant_tool("search_media_sources", _first_arguments()),
            _assistant_tool("lookup_wikipedia_entity", targeted, 2),
            _assistant_tool("lookup_wikipedia_entity", targeted, 3),
            _assistant_final(),
        ])

        outcome = await orchestrate_sources(
            "Batman",
            _gateway(),
            ai_call=ai,
            config={
                "protocol": "openai_tools_v1",
                "max_targeted_rounds": 2,
                "max_tools_per_round": 3,
            },
        )

        self.assertEqual(outcome.status, "insufficient_evidence")
        self.assertEqual(outcome.targeted_rounds, 2)
        self.assertEqual(len(ai.calls), 4)

    async def test_third_targeted_round_is_rejected(self):
        targeted = {
            "fact_ids": [],
            "queries": ["Batman Begins"],
        }
        ai = ScriptedAi([
            _assistant_tool("search_media_sources", _first_arguments()),
            _assistant_tool("lookup_wikipedia_entity", targeted, 2),
            _assistant_tool("lookup_wikipedia_entity", targeted, 3),
            _assistant_tool("lookup_wikipedia_entity", targeted, 4),
        ])

        outcome = await orchestrate_sources(
            "Batman",
            _gateway(),
            ai_call=ai,
            config={
                "protocol": "openai_tools_v1",
                "max_targeted_rounds": 2,
                "max_tools_per_round": 3,
            },
        )

        self.assertEqual(outcome.status, "fallback")
        self.assertEqual(outcome.fallback_reason, "tool_budget_exceeded")
        self.assertEqual(outcome.targeted_rounds, 2)

    async def test_more_than_three_calls_in_one_round_is_rejected(self):
        arguments = {
            "fact_ids": [],
            "queries": ["Batman Begins"],
        }
        too_many = _response({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                _tool_call("lookup_wikipedia_entity", arguments, number)
                for number in range(2, 6)
            ],
        })
        ai = ScriptedAi([
            _assistant_tool("search_media_sources", _first_arguments()),
            too_many,
        ])

        outcome = await orchestrate_sources(
            "Batman",
            _gateway(),
            ai_call=ai,
            config={
                "protocol": "openai_tools_v1",
                "max_targeted_rounds": 2,
                "max_tools_per_round": 3,
            },
        )

        self.assertEqual(outcome.fallback_reason, "tool_budget_exceeded")
        self.assertEqual(outcome.targeted_rounds, 0)

    async def test_private_vendor_protocol_is_not_attempted(self):
        ai = ScriptedAi([])

        outcome = await orchestrate_sources(
            "Batman",
            _gateway(),
            ai_call=ai,
            config={"protocol": "vendor_private"},
        )

        self.assertEqual(outcome.status, "fallback")
        self.assertEqual(outcome.fallback_reason, "tooling_unsupported")
        self.assertEqual(ai.calls, [])


if __name__ == "__main__":
    unittest.main()
