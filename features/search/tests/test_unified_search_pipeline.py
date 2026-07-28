import unittest
from unittest.mock import Mock, patch

from telepiplex_search.deterministic import build_rule_hypotheses
from telepiplex_search.planner import (
    SearchPlanningError,
    _anchored_editor_context,
    _merge_evidence_passes,
    build_confirmable_search_plan,
)
from telepiplex_search.entity_graph import build_search_graph
from telepiplex_search.search_plan import TemporarySpecialAllocator


def _douban_fact():
    return {
        "subject_id": "11",
        "title": "布达佩斯大饭店",
        "chinese_title": "布达佩斯大饭店",
        "official_english_title": "The Grand Budapest Hotel",
        "original_title": "The Grand Budapest Hotel",
        "original_language": "en",
        "year": "2014",
        "media_type": "movie",
        "url": "https://movie.douban.com/subject/11/",
        "cover_url": "https://art.example/douban.jpg",
    }


def _tvdb_fact():
    return {
        "movies": [{
            "tvdb_movie_id": "77",
            "name": "The Grand Budapest Hotel",
            "chinese_title": "布达佩斯大饭店",
            "official_english_title": "The Grand Budapest Hotel",
            "original_title": "The Grand Budapest Hotel",
            "original_language": "en",
            "year": "2014",
            "url": "https://thetvdb.com/movies/77",
            "cover_url": "https://art.example/tvdb.jpg",
        }],
        "series": [],
        "episodes_by_series": {},
    }


def _wikipedia_fact():
    return {
        "wikibase_item": "Q77",
        "title": "The Grand Budapest Hotel",
        "chinese_title": "布达佩斯大饭店",
        "official_english_title": "The Grand Budapest Hotel",
        "original_title": "The Grand Budapest Hotel",
        "original_language": "en",
        "year": "2014",
        "media_type": "movie",
        "url": "https://en.wikipedia.org/wiki/The_Grand_Budapest_Hotel",
    }


def _binding(include_wikipedia=True):
    fact_bindings = [{
        "fact_id": "tvdb:77",
        "role": "movie",
        "season_number": None,
        "episode_number": None,
    }, {
        "fact_id": "douban:11",
        "role": "movie",
        "season_number": None,
        "episode_number": None,
    }]
    if include_wikipedia:
        fact_bindings.append({
            "fact_id": "wikipedia:Q77",
            "role": "movie",
            "season_number": None,
            "episode_number": None,
        })
    return {
        "status": "resolved",
        "candidates": [{
            "candidate_id": "grand-budapest",
            "anchor_fact_id": "tvdb:77",
            "identity_role": "movie",
            "intended_scope": "movie",
            "fact_bindings": fact_bindings,
            "ai_confidence": 0.97,
            "ai_reason": "These facts describe the requested film.",
        }],
    }


class UnifiedSearchPipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_text_search_calls_all_providers_and_freezes_ai_shortlist_links(self):
        calls = []
        ai_contexts = []

        def provider(name, facts):
            def search(_hypotheses):
                calls.append(name)
                return {"status": "ok", "facts": facts}
            return search

        def edit_candidates(context):
            ai_contexts.append(context)
            return _binding()

        plan = await build_confirmable_search_plan(
            "布达佩斯大饭店",
            "unified-1",
            {
                "wikipedia": provider("wikipedia", [_wikipedia_fact()]),
                "douban": provider("douban", [_douban_fact()]),
                "tvdb": provider("tvdb", [_tvdb_fact()]),
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            candidate_editor=edit_candidates,
        )

        self.assertCountEqual(calls, ["wikipedia", "douban", "tvdb"])
        self.assertEqual(len(ai_contexts), 1)
        self.assertEqual(
            {fact["fact_id"] for fact in ai_contexts[0]["facts"]},
            {"wikipedia:Q77", "douban:11", "tvdb:77"},
        )
        candidate = plan["candidates"][0]
        self.assertTrue(candidate["links_frozen"])
        self.assertEqual(len(candidate["source_links"]), 3)
        self.assertEqual(len(candidate["poster_assets"]), 2)
        self.assertEqual(candidate["ai_confidence"], 0.97)
        self.assertEqual(
            candidate["media_metadata"]["evidence"]["decision"]["mode"],
            "ai_fact_binding",
        )

    async def test_missing_provider_is_supplemented_then_ai_rebinds(self):
        wikipedia_calls = 0
        ai_calls = 0

        def wikipedia(_hypotheses):
            nonlocal wikipedia_calls
            wikipedia_calls += 1
            if wikipedia_calls == 1:
                return {"status": "not_found", "facts": []}
            return {"status": "ok", "facts": [_wikipedia_fact()]}

        def editor(context):
            nonlocal ai_calls
            ai_calls += 1
            return _binding(include_wikipedia=ai_calls > 1)

        plan = await build_confirmable_search_plan(
            "布达佩斯大饭店",
            "unified-2",
            {
                "wikipedia": wikipedia,
                "douban": lambda _query: {
                    "status": "ok",
                    "facts": [_douban_fact()],
                },
                "tvdb": lambda _query: {
                    "status": "ok",
                    "facts": [_tvdb_fact()],
                },
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            candidate_editor=editor,
        )

        self.assertEqual(wikipedia_calls, 2)
        self.assertEqual(ai_calls, 2)
        self.assertEqual(
            {item["provider"] for item in plan["candidates"][0]["source_links"]},
            {"wikipedia", "douban", "tvdb"},
        )
        self.assertEqual(
            plan["candidates"][0]["candidate_version"],
            "v1",
        )

    async def test_missing_tvdb_uses_candidate_bound_cross_language_title_and_year(self):
        tvdb_payloads = []
        editor_calls = 0

        wikipedia_fact = {
            "wikibase_item": "Q1770713",
            "title": "冰菓",
            "chinese_title": "冰果",
            "original_title": "氷菓",
            "original_language": "ja",
            "year": "2012",
            "media_type": "series",
            "url": "https://zh.wikipedia.org/wiki/冰菓",
        }
        douban_fact = {
            "subject_id": "10001418",
            "title": "冰果",
            "chinese_title": "冰果",
            "original_title": "氷菓",
            "original_language": "ja",
            "year": "2012",
            "media_type": "series",
            "url": "https://movie.douban.com/subject/10001418/",
        }
        tvdb_fact = {
            "movies": [],
            "series": [{
                "tvdb_series_id": "278127",
                "name": "Hyouka",
                "official_english_title": "Hyouka",
                "original_title": "氷菓",
                "original_language": "ja",
                "year": "2012",
                "url": "https://thetvdb.com/series/hyouka",
            }],
            "episodes_by_series": {},
        }

        def tvdb(payload):
            tvdb_payloads.append(payload)
            if len(tvdb_payloads) == 1:
                return {"source": "tvdb", "status": "not_found", "facts": []}
            return {"source": "tvdb", "status": "ok", "facts": [tvdb_fact]}

        def editor(_context):
            nonlocal editor_calls
            editor_calls += 1
            bindings = [{
                "fact_id": "wikipedia:Q1770713",
                "role": "series_root",
                "season_number": None,
                "episode_number": None,
            }, {
                "fact_id": "douban:10001418",
                "role": "series_root",
                "season_number": None,
                "episode_number": None,
            }]
            if editor_calls > 1:
                bindings.append({
                    "fact_id": "tvdb:278127",
                    "role": "series_root",
                    "season_number": None,
                    "episode_number": None,
                })
            return {
                "status": "resolved",
                "candidates": [{
                    "candidate_id": "hyouka-animation",
                    "anchor_fact_id": "douban:10001418",
                    "identity_role": "series_root",
                    "intended_scope": "whole_series",
                    "fact_bindings": bindings,
                    "ai_confidence": 0.96,
                    "ai_reason": "The verified facts describe the 2012 animation.",
                }],
            }

        plan = await build_confirmable_search_plan(
            "冰果",
            "unified-hyouka",
            {
                "wikipedia": lambda _payload: {
                    "source": "wikipedia",
                    "status": "ok",
                    "facts": [wikipedia_fact],
                },
                "douban": lambda _payload: {
                    "source": "douban",
                    "status": "ok",
                    "facts": [douban_fact],
                },
                "tvdb": tvdb,
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            candidate_editor=editor,
            supplement_query_editor=lambda _context: {
                "queries": [{
                    "candidate_id": "hyouka-animation",
                    "provider": "tvdb",
                    "title_hints": ["Hyouka"],
                }],
            },
        )

        self.assertEqual(len(tvdb_payloads), 2)
        self.assertIn({
            "title": "Hyouka",
            "year": "2012",
            "content_identity": "series",
            "scope": "whole_series",
            "season_number": None,
            "episode_number": None,
            "explicit_facts": [],
            "inferred_facts": ["candidate_source_supplement"],
        }, tvdb_payloads[1]["hypotheses"])
        self.assertEqual(
            plan["candidates"][0]["candidate_version"],
            "v1",
        )

    async def test_missing_provider_after_supplement_keeps_displayable_v0(self):
        def editor(_context):
            return _binding(include_wikipedia=False)

        plan = await build_confirmable_search_plan(
            "布达佩斯大饭店",
            "unified-v0",
            {
                "wikipedia": lambda _query: {
                    "source": "wikipedia",
                    "status": "server_down",
                    "facts": [],
                },
                "douban": lambda _query: {
                    "source": "douban",
                    "status": "ok",
                    "facts": [_douban_fact()],
                },
                "tvdb": lambda _query: {
                    "source": "tvdb",
                    "status": "ok",
                    "facts": [_tvdb_fact()],
                },
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            candidate_editor=editor,
        )

        candidate = plan["candidates"][0]
        self.assertEqual(candidate["candidate_version"], "v0")
        self.assertEqual(
            {item["provider"] for item in candidate["source_links"]},
            {"douban", "tvdb"},
        )
        self.assertIn(
            "wikipedia:server_down",
            candidate["unresolved_sources"],
        )
        self.assertTrue(candidate["selectable"])

    async def test_metadata_incomplete_candidate_does_not_poison_shortlist(self):
        incomplete = {
            **_douban_fact(),
            "subject_id": "22",
            "title": "残缺候选",
            "chinese_title": "残缺候选",
            "official_english_title": "",
            "original_title": "",
            "original_language": "",
            "year": "",
            "url": "https://movie.douban.com/subject/22/",
        }

        def binding(fact_id, candidate_id):
            return {
                "candidate_id": candidate_id,
                "anchor_fact_id": fact_id,
                "identity_role": "movie",
                "intended_scope": "movie",
                "fact_bindings": [{
                    "fact_id": fact_id,
                    "role": "movie",
                    "season_number": None,
                    "episode_number": None,
                }],
                "ai_confidence": 0.8,
                "ai_reason": "Provider fact matches one possible work.",
            }

        plan = await build_confirmable_search_plan(
            "候选",
            "unified-metadata-isolation",
            {
                "douban": lambda _query: {
                    "source": "douban",
                    "status": "ok",
                    "facts": [_douban_fact(), incomplete],
                },
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            candidate_editor=lambda _context: {
                "status": "resolved",
                "candidates": [
                    binding("douban:11", "complete"),
                    binding("douban:22", "incomplete"),
                ],
            },
        )

        self.assertEqual(len(plan["candidates"]), 2)
        self.assertTrue(plan["candidates"][0]["metadata_ready"])
        self.assertFalse(plan["candidates"][1]["metadata_ready"])
        self.assertEqual(
            plan["candidates"][1]["metadata_error"]["code"],
            "metadata_incomplete",
        )
        self.assertEqual(
            plan["candidates"][1]["candidate_version"],
            "v0",
        )

    async def test_locked_link_anchor_is_passed_to_ai_and_cannot_change(self):
        contexts = []

        def editor(context):
            contexts.append(context)
            payload = _binding()
            payload["candidates"][0]["anchor_fact_id"] = "douban:11"
            return payload

        plan = await build_confirmable_search_plan(
            "The Grand Budapest Hotel",
            "unified-link",
            {
                "wikipedia": lambda _query: {
                    "status": "ok",
                    "facts": [_wikipedia_fact()],
                },
                "douban": lambda _query: {
                    "status": "ok",
                    "facts": [_douban_fact()],
                },
                "tvdb": lambda _query: {
                    "status": "ok",
                    "facts": [_tvdb_fact()],
                },
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            locked_identity=("douban_subject", "11"),
            candidate_editor=editor,
        )

        self.assertEqual(contexts[0]["locked_anchor_fact_id"], "douban:11")
        self.assertEqual(
            plan["candidates"][0]["anchor_fact_id"],
            "douban:11",
        )
        self.assertEqual(len(plan["candidates"]), 1)

    async def test_ai_no_match_and_ai_fault_are_distinct(self):
        providers = {
            "douban": lambda _query: {
                "status": "ok",
                "facts": [_douban_fact()],
            },
        }
        with self.assertRaises(SearchPlanningError) as no_match:
            await build_confirmable_search_plan(
                "not it",
                "unified-no-match",
                providers,
                lambda _contract: set(),
                TemporarySpecialAllocator(),
                candidate_editor=lambda _context: {
                    "status": "no_match",
                    "candidates": [],
                },
            )
        self.assertEqual(no_match.exception.code, "no_match")

        with self.assertRaises(SearchPlanningError) as failed:
            await build_confirmable_search_plan(
                "not it",
                "unified-ai-failed",
                providers,
                lambda _contract: set(),
                TemporarySpecialAllocator(),
                candidate_editor=lambda _context: None,
            )
        self.assertEqual(failed.exception.code, "ai_candidate_failure")

    async def test_invalid_shared_fact_binding_is_repaired_once_with_diagnostics(self):
        from telepiplex_search.context import runtime_context

        contexts = []
        logger = Mock()

        def candidate(fact_id, candidate_id):
            return {
                "candidate_id": candidate_id,
                "anchor_fact_id": fact_id,
                "identity_role": "movie",
                "intended_scope": "movie",
                "fact_bindings": [{
                    "fact_id": fact_id,
                    "role": "movie",
                    "season_number": None,
                    "episode_number": None,
                }],
                "ai_confidence": 0.9,
                "ai_reason": "The Provider fact supports this candidate.",
            }

        def editor(context):
            contexts.append(context)
            if len(contexts) == 1:
                return {
                    "status": "resolved",
                    "candidates": [
                        candidate("douban:11", "duplicate-a"),
                        candidate("douban:11", "duplicate-b"),
                    ],
                }
            return {
                "status": "resolved",
                "candidates": [
                    candidate("douban:11", "grand-budapest"),
                ],
            }

        with patch.object(runtime_context, "logger", logger):
            plan = await build_confirmable_search_plan(
                "布达佩斯大饭店",
                "unified-binding-repair",
                {
                    "douban": lambda _query: {
                        "source": "douban",
                        "status": "ok",
                        "facts": [_douban_fact()],
                    },
                },
                lambda _contract: set(),
                TemporarySpecialAllocator(),
                candidate_editor=editor,
            )

        self.assertEqual(
            [context["stage"] for context in contexts],
            ["discovery", "binding_repair"],
        )
        self.assertEqual(
            contexts[1]["binding_error"],
            "fact_bound_multiple_times",
        )
        self.assertEqual(
            {
                item["candidate_id"]
                for item in contexts[1]["invalid_candidates"]
            },
            {"duplicate-a", "duplicate-b"},
        )
        self.assertEqual(
            plan["candidates"][0]["candidate_id"],
            "grand-budapest",
        )
        info_logs = " ".join(
            call.args[0] for call in logger.info.call_args_list
        )
        warning_logs = " ".join(
            call.args[0] for call in logger.warning.call_args_list
        )
        self.assertIn("search_binding status=received", info_logs)
        self.assertIn("candidate_id=duplicate-a", info_logs)
        self.assertIn('"fact_id": "douban:11"', info_logs)
        self.assertIn("search_binding status=ok", info_logs)
        self.assertIn("search_binding status=invalid", warning_logs)
        self.assertIn("error=fact_bound_multiple_times", warning_logs)
        self.assertIn("search_binding status=repairing", warning_logs)

    @patch(
        "telepiplex_search.planner.infer_search_hypotheses_with_ai",
        return_value=None,
    )
    async def test_zero_fact_ai_fault_is_not_reported_as_no_match(
        self, _infer
    ):
        providers = {
            provider: lambda _query, provider=provider: {
                "source": provider,
                "status": "not_found",
                "facts": [],
            }
            for provider in ("wikipedia", "douban", "tvdb")
        }

        with self.assertRaises(SearchPlanningError) as failed:
            await build_confirmable_search_plan(
                "不存在的作品",
                "unified-zero-ai-failed",
                providers,
                lambda _contract: set(),
                TemporarySpecialAllocator(),
                candidate_editor=lambda _context: None,
            )

        self.assertEqual(
            failed.exception.code,
            "ai_candidate_failure",
        )

    @patch(
        "telepiplex_search.planner.infer_search_hypotheses_with_ai",
        return_value=None,
    )
    async def test_all_source_failures_are_reported_before_ai_recovery(
        self, infer
    ):
        providers = {
            "wikipedia": lambda _query: {
                "source": "wikipedia",
                "status": "server_down",
                "facts": [],
            },
            "douban": lambda _query: {
                "source": "douban",
                "status": "rate_limited",
                "facts": [],
            },
            "tvdb": lambda _query: {
                "source": "tvdb",
                "status": "authentication_failed",
                "facts": [],
            },
        }

        with self.assertRaises(SearchPlanningError) as failed:
            await build_confirmable_search_plan(
                "作品",
                "unified-all-down",
                providers,
                lambda _contract: set(),
                TemporarySpecialAllocator(),
                candidate_editor=lambda _context: None,
            )

        self.assertEqual(failed.exception.code, "source_failure")
        self.assertEqual(
            failed.exception.reason_codes,
            (
                "wikipedia:server_down",
                "douban:rate_limited",
                "tvdb:authentication_failed",
            ),
        )
        infer.assert_not_called()

    def test_evidence_merge_does_not_hide_an_earlier_hard_failure(self):
        merged = _merge_evidence_passes(
            [{
                "source": "wikipedia",
                "status": "rate_limited",
                "facts": [],
                "error": "HTTP 429",
            }],
            [{
                "source": "wikipedia",
                "status": "not_found",
                "facts": [],
                "error": "",
            }],
        )

        self.assertEqual(merged[0]["status"], "rate_limited")
        self.assertEqual(merged[0]["error"], "HTTP 429")

    async def test_all_rate_limited_sources_use_specific_error_code(self):
        providers = {
            provider: lambda _query, provider=provider: {
                "source": provider,
                "status": "rate_limited",
                "facts": [],
            }
            for provider in ("wikipedia", "douban", "tvdb")
        }

        with self.assertRaises(SearchPlanningError) as failed:
            await build_confirmable_search_plan(
                "作品",
                "unified-all-rate-limited",
                providers,
                lambda _contract: set(),
                TemporarySpecialAllocator(),
                candidate_editor=lambda _context: None,
            )

        self.assertEqual(
            failed.exception.code,
            "source_rate_limited",
        )

    @patch(
        "telepiplex_search.planner.infer_search_hypotheses_with_ai",
    )
    async def test_all_disabled_sources_are_not_reported_as_no_match(
        self,
        infer,
    ):
        providers = {
            provider: lambda _query, provider=provider: {
                "source": provider,
                "status": "disabled",
                "facts": [],
            }
            for provider in ("wikipedia", "douban", "tvdb")
        }

        with self.assertRaises(SearchPlanningError) as failed:
            await build_confirmable_search_plan(
                "作品",
                "unified-all-disabled",
                providers,
                lambda _contract: set(),
                TemporarySpecialAllocator(),
                candidate_editor=lambda _context: None,
            )

        self.assertEqual(failed.exception.code, "source_failure")
        self.assertEqual(
            failed.exception.reason_codes,
            (
                "wikipedia:disabled",
                "douban:disabled",
                "tvdb:disabled",
            ),
        )
        infer.assert_not_called()

    async def test_hard_failed_and_disabled_sources_are_not_retried_immediately(
        self,
    ):
        calls = {
            "wikipedia": 0,
            "douban": 0,
            "tvdb": 0,
            "editor": 0,
        }

        def provider(name, status, facts):
            def lookup(_hypotheses):
                calls[name] += 1
                return {
                    "source": name,
                    "status": status,
                    "facts": facts,
                }
            return lookup

        def editor(_context):
            calls["editor"] += 1
            return {
                "status": "resolved",
                "candidates": [{
                    "candidate_id": "douban-only",
                    "anchor_fact_id": "douban:11",
                    "identity_role": "movie",
                    "intended_scope": "movie",
                    "fact_bindings": [{
                        "fact_id": "douban:11",
                        "role": "movie",
                        "season_number": None,
                        "episode_number": None,
                    }],
                    "ai_confidence": 0.9,
                    "ai_reason": "Only Douban returned usable facts.",
                }],
            }

        plan = await build_confirmable_search_plan(
            "布达佩斯大饭店",
            "unified-no-hard-failure-retry",
            {
                "wikipedia": provider(
                    "wikipedia",
                    "rate_limited",
                    [],
                ),
                "douban": provider(
                    "douban",
                    "ok",
                    [_douban_fact()],
                ),
                "tvdb": provider("tvdb", "disabled", []),
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            candidate_editor=editor,
        )

        self.assertEqual(calls, {
            "wikipedia": 1,
            "douban": 1,
            "tvdb": 1,
            "editor": 1,
        })
        self.assertEqual(
            plan["candidates"][0]["candidate_version"],
            "v0",
        )
        self.assertIn(
            "wikipedia:rate_limited",
            plan["candidates"][0]["unresolved_sources"],
        )

    def test_named_regressions_preserve_raw_title_for_every_provider(self):
        for raw_title in ("蜂蜜与四叶草", "ODDTAXI", "冰果", "1917"):
            with self.subTest(raw_title=raw_title):
                hypotheses = build_rule_hypotheses(raw_title)
                self.assertEqual(
                    hypotheses["intent"]["title"],
                    raw_title,
                )
                self.assertEqual(
                    hypotheses["source_queries"],
                    {
                        "wikipedia": [raw_title],
                        "douban": [raw_title],
                        "tvdb": [raw_title],
                    },
                )

    def test_numeric_only_title_remains_a_source_query(self):
        hypotheses = build_rule_hypotheses("1917")

        self.assertEqual(hypotheses["intent"]["title"], "1917")
        self.assertEqual(hypotheses["source_queries"]["douban"], ["1917"])

    def test_quoted_numeric_title_does_not_set_a_year_hint(self):
        hypotheses = build_rule_hypotheses('"1917"')

        self.assertEqual(hypotheses["intent"]["title"], "1917")
        self.assertEqual(hypotheses["intent"]["year"], "")
        self.assertEqual(
            hypotheses["source_queries"]["wikipedia"],
            ["1917"],
        )

    def test_ai_discovery_context_is_bounded_and_omits_inventory(self):
        wikipedia = [{
            "wikibase_item": f"Q{index}",
            "title": f"Wiki {index}",
            "year": "2024",
            "media_type": "movie",
            "url": f"https://en.wikipedia.org/wiki/Wiki_{index}",
        } for index in range(25)]
        douban = [{
            "subject_id": str(1000 + index),
            "title": f"Douban {index}",
            "official_english_title": f"Douban {index}",
            "year": "2024",
            "media_type": "movie",
            "url": f"https://movie.douban.com/subject/{1000 + index}/",
        } for index in range(25)]
        series = [{
            "tvdb_series_id": str(2000 + index),
            "name": f"Series {index}",
            "year": "2024",
        } for index in range(25)]
        episodes = {
            str(2000 + index): [{
                "tvdb_episode_id": f"{index}-{episode}",
                "season_number": 1,
                "episode_number": episode,
            } for episode in range(1, 101)]
            for index in range(25)
        }
        graph = build_search_graph([
            {
                "source": "wikipedia",
                "status": "ok",
                "facts": wikipedia,
            },
            {
                "source": "douban",
                "status": "ok",
                "facts": douban,
            },
            {
                "source": "tvdb",
                "status": "ok",
                "facts": [{
                    "movies": [],
                    "series": series,
                    "episodes_by_series": episodes,
                }],
            },
        ])

        context = _anchored_editor_context(
            "ambiguous",
            graph,
            intent={},
            locked_anchor_fact_id="",
            stage="discovery",
        )

        counts = {
            provider: sum(
                fact["provider"] == provider
                for fact in context["facts"]
            )
            for provider in ("wikipedia", "douban", "tvdb")
        }
        self.assertEqual(counts, {
            "wikipedia": 20,
            "douban": 20,
            "tvdb": 20,
        })
        self.assertTrue(all(
            fact["tvdb_inventory"] == []
            for fact in context["facts"]
        ))


if __name__ == "__main__":
    unittest.main()
