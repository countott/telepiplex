import unittest
from unittest.mock import patch

from telepiplex_media_search.planner import (
    PlanningBudget,
    SearchPlanningError,
    build_confirmable_search_plan,
)
from telepiplex_media_search.evidence_verifier import (
    VerifiedAiDecision,
    VerifiedEquivalenceEdge,
)
from telepiplex_media_search.search_plan import TemporarySpecialAllocator
from telepiplex_media_search.search_plan import confirm_media_metadata
from telepiplex_media_search.series_scope import apply_series_scope
from telepiplex_media_search.source_orchestrator import OrchestrationOutcome


def _fact(provider, number, *, title=None, media_type="movie", episodes=None):
    title = title or f"Movie {number}"
    key = "subject_id" if provider == "douban" else "wikibase_item"
    return {
        key: f"{provider}-{number}",
        "title": title,
        "english_title": title,
        "official_english_title": title,
        "chinese_title": title,
        "year": "2024",
        "media_type": media_type,
        "episodes": episodes or [],
    }


def _provider(provider, count, *, title=None, media_type="movie", episodes=None):
    def provide(_hypotheses):
        if provider == "tvdb":
            key = "series" if media_type == "series" else "movies"
            facts = []
            for number in range(count):
                entity_id = f"tvdb-{number}"
                item = {
                    f"tvdb_{media_type}_id": entity_id,
                    "name": title or f"Movie {number}",
                    "english_title": title or f"Movie {number}",
                    "official_english_title": title or f"Movie {number}",
                    "year": "2024",
                    "media_type": media_type,
                }
                facts.append({
                    key: [item],
                    "episodes_by_series": {
                        entity_id: episodes or []
                    } if media_type == "series" else {},
                })
            return {"source": provider, "status": "ok", "facts": facts}
        return {
            "source": provider,
            "status": "ok",
            "facts": [
                _fact(
                    provider,
                    number,
                    title=title,
                    media_type=media_type,
                    episodes=episodes,
                )
                for number in range(count)
            ],
        }

    return provide


def _orchestrated_movie_outcome(
    *,
    chinese_title,
    english_title,
    year,
    split_languages=False,
):
    douban = {
        "subject_id": f"d-{year}-{len(chinese_title)}",
        "title": chinese_title,
        "chinese_title": chinese_title,
        "year": year,
        "media_type": "movie",
    }
    if not split_languages:
        douban.update({
            "english_title": english_title,
            "official_english_title": english_title,
        })
    wikipedia = {
        "wikibase_item": f"Q-{year}-{len(english_title)}",
        "title": english_title,
        "english_title": english_title,
        "official_english_title": english_title,
        "year": year,
        "media_type": "movie",
    }
    sources = (
        {
            "source": "douban",
            "status": "ok",
            "facts": [douban],
            "source_urls": [],
            "query_summaries": [chinese_title],
        },
        {
            "source": "wikipedia",
            "status": "ok",
            "facts": [wikipedia],
            "source_urls": [],
            "query_summaries": [english_title],
        },
        {
            "source": "tvdb",
            "status": "not_found",
            "facts": [],
            "source_urls": [],
            "query_summaries": [english_title],
        },
    )
    edges = ()
    if split_languages:
        edges = (VerifiedEquivalenceEdge(
            f"douban:d-{year}-{len(chinese_title)}",
            f"wikipedia:Q-{year}-{len(english_title)}",
            "same_entity",
            "verified cross-language title pair",
        ),)
    intent = {
        "title_hints": [chinese_title, english_title],
        "media_type_hint": "movie",
        "year_hint": year,
        "scope": "work",
        "season_number": None,
        "episode_number": None,
    }
    decision = VerifiedAiDecision(
        "resolved",
        intent,
        edges,
        (),
        "confirm",
    )
    return OrchestrationOutcome(
        "resolved",
        intent,
        sources,
        decision,
        0,
        "",
    )


class RankedPlannerTest(unittest.IsolatedAsyncioTestCase):
    async def test_ai_first_typo_result_is_not_filtered_by_raw_query_prefix(self):
        calls = []
        outcome = _orchestrated_movie_outcome(
            chinese_title="蝙蝠侠：侠影之谜",
            english_title="Batman Begins",
            year="2005",
        )

        async def orchestrate(raw_query, _gateway):
            calls.append(raw_query)
            return outcome

        def forbidden_provider(_hypotheses):
            raise AssertionError("AI-first success must not re-run rule providers")

        plan = await build_confirmable_search_plan(
            "蝙蝠侠：谍影之谜",
            "p-ai-first-typo",
            {
                "douban": forbidden_provider,
                "wikipedia": forbidden_provider,
                "tvdb": forbidden_provider,
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            source_gateway=object(),
            source_orchestrator=orchestrate,
        )

        self.assertEqual(calls, ["蝙蝠侠：谍影之谜"])
        self.assertEqual(len(plan["candidates"]), 1)
        contract = plan["candidates"][0]["media_metadata"]
        self.assertEqual(contract["identity"]["english_title"], "Batman Begins")
        self.assertEqual(
            contract["evidence"]["decision"]["mode"],
            "ai_tool_orchestrated",
        )

    async def test_ai_verified_cross_language_edge_forms_candidate(self):
        outcome = _orchestrated_movie_outcome(
            chinese_title="布达佩斯大饭店",
            english_title="The Grand Budapest Hotel",
            year="2014",
            split_languages=True,
        )

        async def orchestrate(_raw_query, _gateway):
            return outcome

        plan = await build_confirmable_search_plan(
            "布达佩斯大饭店",
            "p-ai-edge",
            {},
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            source_gateway=object(),
            source_orchestrator=orchestrate,
        )

        self.assertEqual(len(plan["candidates"]), 1)
        self.assertEqual(
            set(
                plan["candidates"][0]["media_metadata"]["evidence"]
                ["provider_statuses"]
            ),
            {"douban", "wikipedia", "tvdb"},
        )

    async def test_log_regression_titles_reach_confirmation_candidates(self):
        cases = (
            ("蝙蝠侠：谍影之谜", "蝙蝠侠：侠影之谜", "Batman Begins", "2005"),
            ("蝙蝠侠：黑暗骑士", "蝙蝠侠：黑暗骑士", "The Dark Knight", "2008"),
            ("蝙蝠侠黑暗骑士", "蝙蝠侠：黑暗骑士", "The Dark Knight", "2008"),
            ("蜂蜜与四叶草", "蜂蜜与四叶草", "Honey and Clover", "2006"),
            ("布达佩斯大饭店", "布达佩斯大饭店", "The Grand Budapest Hotel", "2014"),
        )
        for index, (raw, chinese, english, year) in enumerate(cases):
            with self.subTest(raw=raw):
                outcome = _orchestrated_movie_outcome(
                    chinese_title=chinese,
                    english_title=english,
                    year=year,
                )

                async def orchestrate(_raw_query, _gateway, value=outcome):
                    return value

                plan = await build_confirmable_search_plan(
                    raw,
                    f"p-log-{index}",
                    {},
                    lambda _contract: set(),
                    TemporarySpecialAllocator(),
                    source_gateway=object(),
                    source_orchestrator=orchestrate,
                )

                self.assertGreaterEqual(len(plan["candidates"]), 1)

    async def test_ai_fallback_runs_existing_deterministic_chain(self):
        async def orchestrate(_raw_query, _gateway):
            return OrchestrationOutcome(
                "fallback",
                {},
                (),
                None,
                0,
                "ai_unavailable",
            )

        plan = await build_confirmable_search_plan(
            "Movie 1",
            "p-ai-fallback",
            {
                "douban": _provider("douban", 1, title="Movie 1"),
                "wikipedia": _provider("wikipedia", 1, title="Movie 1"),
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            source_gateway=object(),
            source_orchestrator=orchestrate,
        )

        self.assertEqual(len(plan["candidates"]), 1)
        self.assertEqual(
            plan["candidates"][0]["media_metadata"]["evidence"]["decision"]["mode"],
            "deterministic_bounded",
        )

    async def test_direct_anchor_never_calls_source_orchestrator(self):
        async def forbidden(_raw_query, _gateway):
            raise AssertionError("direct links must bypass AI orchestration")

        plan = await build_confirmable_search_plan(
            "Movie 1 2024",
            "p-direct-no-ai",
            {"douban": _provider("douban", 1, title="Movie 1")},
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            locked_identity=("douban_subject", "douban-0"),
            source_gateway=object(),
            source_orchestrator=forbidden,
        )

        self.assertEqual(len(plan["candidates"]), 1)

    @patch(
        "telepiplex_media_search.planner.infer_candidate_scorecard_with_ai",
        return_value=None,
    )
    async def test_candidate_contract_carries_only_verified_title_aliases(
        self,
        _scorecard,
    ):
        def provider(name):
            def provide(_hypotheses):
                key = (
                    "subject_id"
                    if name == "douban"
                    else "wikibase_item"
                )
                return {
                    "source": name,
                    "status": "ok",
                    "facts": [{
                        key: f"{name}-1",
                        "title": "黑暗荣耀",
                        "english_title": "The Glory",
                        "official_english_title": "The Glory",
                        "aliases": ["The Glory", "더 글로리"],
                        "year": "2022",
                        "media_type": "series",
                        "external_ids": {"tvdb": "411469"},
                    }],
                }

            return provide

        plan = await build_confirmable_search_plan(
            "黑暗荣耀",
            "p-aliases",
            {
                "douban": provider("douban"),
                "wikipedia": provider("wikipedia"),
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            locked_identity=("tvdb", "411469"),
        )

        aliases = plan["candidates"][0]["media_metadata"]["identity"]["aliases"]
        self.assertIn("The Glory", aliases)
        self.assertIn("더 글로리", aliases)

    async def test_seven_qualified_candidates_are_all_returned(self):
        plan = await build_confirmable_search_plan(
            "Movie",
            "p-seven",
            {
                "douban": _provider("douban", 7),
                "wikipedia": _provider("wikipedia", 7),
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(len(plan["candidates"]), 7)
        self.assertTrue(all(item["selectable"] for item in plan["candidates"]))

    async def test_title_family_within_gate_keeps_all_volume_candidates(self):
        def volume_provider(provider):
            def provide(_hypotheses):
                key = (
                    "subject_id"
                    if provider == "douban"
                    else "wikibase_item"
                )
                return {
                    "source": provider,
                    "status": "ok",
                    "facts": [{
                        key: f"{provider}-1",
                        "title": "Kill Bill Vol 1",
                        "english_title": "Kill Bill Vol 1",
                        "chinese_title": "杀死比尔",
                        "year": "2003",
                        "media_type": "movie",
                    }, {
                        key: f"{provider}-2",
                        "title": "Kill Bill Vol 2",
                        "english_title": "Kill Bill Vol 2",
                        "chinese_title": "杀死比尔2",
                        "year": "2004",
                        "media_type": "movie",
                    }],
                }
            return provide

        plan = await build_confirmable_search_plan(
            "杀死比尔",
            "p-volumes",
            {
                "douban": volume_provider("douban"),
                "wikipedia": volume_provider("wikipedia"),
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(len(plan["candidates"]), 2)

    async def test_eight_qualified_candidates_are_rejected_without_truncation(self):
        with self.assertRaises(SearchPlanningError) as raised:
            await build_confirmable_search_plan(
                "Movie",
                "p-eight",
                {
                    "douban": _provider("douban", 8),
                    "wikipedia": _provider("wikipedia", 8),
                },
                lambda _contract: set(),
                TemporarySpecialAllocator(),
            )

        self.assertEqual(raised.exception.code, "too_many_candidates")

    async def test_direct_link_anchor_is_selectable_with_one_authoritative_source(self):
        plan = await build_confirmable_search_plan(
            "Movie 1 2024",
            "p-direct",
            {"douban": _provider("douban", 1, title="Movie 1")},
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            locked_identity=("douban_subject", "douban-0"),
        )

        self.assertEqual(len(plan["candidates"]), 1)
        self.assertTrue(plan["candidates"][0]["selectable"])

    @patch(
        "telepiplex_media_search.planner.infer_candidate_scorecard_with_ai",
        return_value=None,
    )
    @patch("telepiplex_media_search.planner.infer_search_hypotheses_with_ai")
    async def test_clear_query_does_not_require_ai_availability(
        self,
        infer,
        scorecard,
    ):
        plan = await build_confirmable_search_plan(
            "Movie 1",
            "p-clear",
            {
                "douban": _provider("douban", 1, title="Movie 1"),
                "wikipedia": _provider("wikipedia", 1, title="Movie 1"),
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        infer.assert_not_called()
        scorecard.assert_called_once()
        self.assertEqual(plan["candidates"][0]["score"]["ai_total"], 0)
        self.assertEqual(
            plan["candidates"][0]["media_metadata"]["evidence"]["decision"]["mode"],
            "deterministic_bounded",
        )

    @patch("telepiplex_media_search.planner.infer_candidate_scorecard_with_ai")
    async def test_ai_can_reorder_but_not_remove_candidates(self, scorecard):
        def score(context):
            keys = [
                item["candidate_key"]
                for item in context["candidates"]
            ]
            return {"scores": [{
                "candidate_key": key,
                "title_equivalence": 20 if index else 4,
                "intent_relevance": 10 if index else 2,
                "relation_consistency": 10 if index else 1,
                "fact_ids": [
                    fact["fact_id"]
                    for fact in context["candidates"][index]["facts"]
                ],
            } for index, key in enumerate(keys)]}

        scorecard.side_effect = score
        plan = await build_confirmable_search_plan(
            "Movie",
            "p-ai-order",
            {
                "douban": _provider("douban", 2),
                "wikipedia": _provider("wikipedia", 2),
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(len(plan["candidates"]), 2)
        self.assertGreater(
            plan["candidates"][0]["score"]["ai_total"],
            plan["candidates"][1]["score"]["ai_total"],
        )
        self.assertTrue(all(
            item["selectable"] for item in plan["candidates"]
        ))

    async def test_bare_number_requires_official_title_match(self):
        with self.assertRaises(SearchPlanningError) as raised:
            await build_confirmable_search_plan(
                "蝙蝠侠1",
                "p-batman",
                {
                    "douban": _provider("douban", 1, title="蝙蝠侠"),
                    "wikipedia": _provider("wikipedia", 1, title="蝙蝠侠"),
                },
                lambda _contract: set(),
                TemporarySpecialAllocator(),
            )
        self.assertEqual(raised.exception.code, "ambiguous_numeric_role")

        plan = await build_confirmable_search_plan(
            "变形金刚3",
            "p-transformers",
            {
                "douban": _provider("douban", 1, title="变形金刚3"),
                "wikipedia": _provider("wikipedia", 1, title="变形金刚3"),
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )
        self.assertEqual(len(plan["candidates"]), 1)

    async def test_bare_series_never_falls_back_to_first_episode(self):
        episodes = [{
            "tvdb_episode_id": "e1",
            "season_number": 1,
            "episode_number": 1,
            "aired": "2022-12-30",
        }]
        plan = await build_confirmable_search_plan(
            "The Glory",
            "p-series",
            {
                "douban": _provider(
                    "douban", 1, title="The Glory", media_type="series"
                ),
                "tvdb": _provider(
                    "tvdb",
                    1,
                    title="The Glory",
                    media_type="series",
                    episodes=episodes,
                ),
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )
        candidate = plan["candidates"][0]

        self.assertEqual(
            candidate["media_metadata"]["evidence"]["decision"]["scope"],
            "movie_or_series",
        )
        self.assertEqual(
            candidate["media_metadata"]["retrieval"]["query"],
            "The Glory",
        )
        self.assertNotIn("S01E01", candidate["prowlarr_queries"][0])
        scoped = apply_series_scope(
            candidate["media_metadata"], "whole_series"
        )
        confirmed = confirm_media_metadata({
            "media_metadata": scoped,
        })
        self.assertTrue(confirmed["confirmed"])

    async def test_exhausted_total_budget_fails_structurally(self):
        with self.assertRaisesRegex(SearchPlanningError, "planning_timed_out"):
            await build_confirmable_search_plan(
                "Movie",
                "p-budget",
                {"douban": _provider("douban", 1)},
                lambda _contract: set(),
                TemporarySpecialAllocator(),
                budget=PlanningBudget(total=0),
            )


if __name__ == "__main__":
    unittest.main()
