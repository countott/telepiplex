import unittest
from unittest.mock import patch

from telepiplex_search.planner import (
    PlanningBudget,
    SearchPlanningError,
    _ordered_expansion_candidates,
    build_confirmable_search_plan,
)
from telepiplex_search.entity_graph import build_search_graph
from telepiplex_search.evidence_verifier import (
    VerifiedAiDecision,
    VerifiedEquivalenceEdge,
)
from telepiplex_search.search_plan import TemporarySpecialAllocator
from telepiplex_search.search_plan import confirm_media_metadata
from telepiplex_search.series_scope import apply_series_scope
from telepiplex_search.source_orchestrator import OrchestrationOutcome


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


def _provider_with_titles(provider, titles, *, media_type="series", episodes=None):
    def provide(_hypotheses):
        if provider == "tvdb":
            facts = []
            for number, title in enumerate(titles):
                entity_id = f"tvdb-{number}"
                facts.append({
                    "series": [{
                        "tvdb_series_id": entity_id,
                        "name": title,
                        "english_title": title,
                        "official_english_title": title,
                        "year": "2024",
                        "media_type": media_type,
                    }],
                    "episodes_by_series": {
                        entity_id: list(episodes or []),
                    },
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
                for number, title in enumerate(titles)
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


def _orchestrated_series_outcome(series_items):
    douban_facts = []
    wikipedia_facts = []
    tvdb_facts = []
    for index, item in enumerate(series_items):
        title = item["title"]
        year = item["year"]
        series_id = item["tvdb_id"]
        douban_facts.append({
            "subject_id": f"douban-{index}",
            "title": title,
            "english_title": title,
            "official_english_title": title,
            "year": year,
            "media_type": "series",
        })
        wikipedia_facts.append({
            "wikibase_item": f"Q-{index}",
            "title": title,
            "english_title": title,
            "official_english_title": title,
            "year": year,
            "media_type": "series",
        })
        tvdb_facts.append({
            "series": [{
                "tvdb_series_id": series_id,
                "name": title,
                "english_title": title,
                "official_english_title": title,
                "year": year,
                "media_type": "series",
            }],
            "episodes_by_series": {
                series_id: list(item.get("episodes") or []),
            },
        })
    sources = (
        {
            "source": "douban",
            "status": "ok",
            "facts": douban_facts,
            "source_urls": [],
            "query_summaries": [item["title"] for item in series_items],
        },
        {
            "source": "wikipedia",
            "status": "ok",
            "facts": wikipedia_facts,
            "source_urls": [],
            "query_summaries": [item["title"] for item in series_items],
        },
        {
            "source": "tvdb",
            "status": "ok",
            "facts": tvdb_facts,
            "source_urls": [],
            "query_summaries": [item["title"] for item in series_items],
        },
    )
    intent = {
        "title_hints": [item["title"] for item in series_items],
        "media_type_hint": "series",
        "year_hint": "",
        "scope": "episode",
        "season_number": None,
        "episode_number": None,
    }
    decision = VerifiedAiDecision(
        "resolved",
        intent,
        (),
        (),
        "confirm",
    )
    return OrchestrationOutcome(
        "resolved",
        intent,
        sources,
        decision,
        1,
        "",
    )


class RankedPlannerTest(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_funnel_logs_qualification_reasons(self):
        def douban(_hypotheses):
            return {
                "source": "douban",
                "status": "ok",
                "facts": [
                    _fact("douban", 1, title="Media A"),
                    _fact("douban", 2, title="Media B"),
                    _fact(
                        "douban",
                        3,
                        title="Media C",
                        media_type="series",
                    ),
                ],
            }

        def wikipedia(_hypotheses):
            return {
                "source": "wikipedia",
                "status": "ok",
                "facts": [
                    _fact("wikipedia", 1, title="Media A"),
                    _fact(
                        "wikipedia",
                        3,
                        title="Media C",
                        media_type="series",
                    ),
                ],
            }

        with self.assertLogs("telepiplex.search", level="INFO") as captured:
            plan = await build_confirmable_search_plan(
                "Media",
                "p-funnel",
                {
                    "douban": douban,
                    "wikipedia": wikipedia,
                },
                lambda _contract: set(),
                TemporarySpecialAllocator(),
            )

        self.assertEqual(len(plan["candidates"]), 1)
        funnel = next(
            message
            for message in captured.output
            if "stage=candidate_funnel" in message
        )
        self.assertIn("raw=3", funnel)
        self.assertIn("title_matched=3", funnel)
        self.assertIn("qualified=1", funnel)
        self.assertIn("rejected_single_source=1", funnel)
        self.assertIn("rejected_missing_tvdb=1", funnel)
        self.assertIn("rejected_missing_scope=0", funnel)
        self.assertIn("rejected_media_type=0", funnel)
        self.assertIn("rejected_year=0", funnel)
        self.assertIn("rejected_title_policy=0", funnel)

    def test_controlled_expansion_orders_prefixes_by_query_relevance(self):
        graph = build_search_graph([{
            "source": "douban",
            "status": "ok",
            "facts": [
                _fact("douban", 0, title="Target Query Extremely Long"),
                _fact("douban", 1, title="Target Query Medium"),
                _fact("douban", 2, title="Target Query Short"),
                _fact("douban", 3, title="Target Query A"),
            ],
        }])

        ordered = _ordered_expansion_candidates(
            list(graph.candidates),
            {
                "title": "Target Query",
                "year": "",
                "media_type": "movie",
            },
        )

        self.assertEqual(ordered[0].titles[0], "Target Query A")
        self.assertEqual(ordered[-1].titles[0], "Target Query Extremely Long")

    async def test_episode_query_prefers_exact_base_title_over_prefix_noise(self):
        titles = [
            "Rick and Morty",
            "Rick and Morty: The Anime",
            "Rick and Morty: Alien Worlds",
            "Rick and Morty: Behind the Scenes",
            "Rick and Morty: Companion",
            "Rick and Morty: Origins",
            "Rick and Morty: Shorts",
            "Rick and Morty: Special",
        ]
        episodes = [{
            "tvdb_episode_id": "episode-9-8",
            "name": "Nomortland",
            "season_number": 9,
            "episode_number": 8,
            "aired": "2026-07-20",
        }]

        plan = await build_confirmable_search_plan(
            "Rick and Morty S09E08",
            "p-exact-episode",
            {
                provider: _provider_with_titles(
                    provider,
                    titles,
                    episodes=episodes,
                )
                for provider in ("douban", "wikipedia", "tvdb")
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(len(plan["candidates"]), 1)
        candidate = plan["candidates"][0]
        self.assertEqual(
            candidate["media_metadata"]["retrieval"]["query"],
            "Rick and Morty S09E08",
        )
        self.assertEqual(
            candidate["media_metadata"]["evidence"]["decision"]["scope"],
            "episode",
        )
        self.assertEqual(
            candidate["media_metadata"]["items"][0]["episode_number"],
            8,
        )

    async def test_explicit_episode_query_requires_tvdb_inventory_match(self):
        episodes = [{
            "tvdb_episode_id": "episode-9-7",
            "name": "Previous Episode",
            "season_number": 9,
            "episode_number": 7,
            "aired": "2026-07-13",
        }]

        with self.assertRaises(SearchPlanningError) as raised:
            await build_confirmable_search_plan(
                "Rick and Morty S09E08",
                "p-explicit-episode-missing",
                {
                    provider: _provider_with_titles(
                        provider,
                        ["Rick and Morty"],
                        episodes=episodes,
                    )
                    for provider in ("douban", "wikipedia", "tvdb")
                },
                lambda _contract: set(),
                TemporarySpecialAllocator(),
            )

        self.assertEqual(raised.exception.code, "tvdb_scope_not_verified")

    async def test_standalone_episode_title_resolves_verified_parent_inventory(self):
        outcome = _orchestrated_series_outcome([{
            "title": "Rick and Morty",
            "year": "2013",
            "tvdb_id": "275274",
            "episodes": [{
                "tvdb_episode_id": "1001",
                "name": "Rickmurai Jack",
                "season_number": 5,
                "episode_number": 10,
                "aired": "2021-09-05",
            }],
        }])

        async def orchestrate(_raw_query, _gateway):
            return outcome

        plan = await build_confirmable_search_plan(
            "Rickmurai Jack",
            "p-episode-title",
            {},
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            source_gateway=object(),
            source_orchestrator=orchestrate,
        )

        self.assertEqual(len(plan["candidates"]), 1)
        contract = plan["candidates"][0]["media_metadata"]
        self.assertEqual(
            contract["retrieval"]["query"],
            "Rick and Morty S05E10",
        )
        self.assertEqual(contract["items"][0]["item_id"], "1001")

    async def test_standalone_episode_title_requires_inventory_match(self):
        outcome = _orchestrated_series_outcome([{
            "title": "Rick and Morty",
            "year": "2013",
            "tvdb_id": "275274",
            "episodes": [{
                "tvdb_episode_id": "1001",
                "name": "Rickmurai Jack",
                "season_number": 5,
                "episode_number": 10,
                "aired": "2021-09-05",
            }],
        }])

        async def orchestrate(_raw_query, _gateway):
            return outcome

        with self.assertRaises(SearchPlanningError) as raised:
            await build_confirmable_search_plan(
                "Unknown Episode",
                "p-episode-title-missing",
                {},
                lambda _contract: set(),
                TemporarySpecialAllocator(),
                source_gateway=object(),
                source_orchestrator=orchestrate,
            )

        self.assertEqual(raised.exception.code, "tvdb_scope_not_verified")

    async def test_standalone_episode_title_rejects_multiple_parent_matches(self):
        outcome = _orchestrated_series_outcome([
            {
                "title": "Series A",
                "year": "2020",
                "tvdb_id": "series-a",
                "episodes": [{
                    "tvdb_episode_id": "a-1",
                    "name": "Pilot",
                    "season_number": 1,
                    "episode_number": 1,
                    "aired": "2020-01-01",
                }],
            },
            {
                "title": "Series B",
                "year": "2021",
                "tvdb_id": "series-b",
                "episodes": [{
                    "tvdb_episode_id": "b-1",
                    "name": "Pilot",
                    "season_number": 1,
                    "episode_number": 1,
                    "aired": "2021-01-01",
                }],
            },
        ])

        async def orchestrate(_raw_query, _gateway):
            return outcome

        with self.assertRaises(SearchPlanningError) as raised:
            await build_confirmable_search_plan(
                "Pilot",
                "p-episode-title-ambiguous",
                {},
                lambda _contract: set(),
                TemporarySpecialAllocator(),
                source_gateway=object(),
                source_orchestrator=orchestrate,
            )

        self.assertEqual(raised.exception.code, "ambiguous_candidates")

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
        "telepiplex_search.planner.infer_candidate_scorecard_with_ai",
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

    async def test_eight_qualified_candidates_remain_selectable(self):
        plan = await build_confirmable_search_plan(
            "Movie",
            "p-eight",
            {
                "douban": _provider("douban", 8),
                "wikipedia": _provider("wikipedia", 8),
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(len(plan["candidates"]), 8)
        self.assertTrue(all(
            candidate["selectable"]
            for candidate in plan["candidates"]
        ))

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
        "telepiplex_search.planner.infer_candidate_scorecard_with_ai",
        return_value=None,
    )
    @patch("telepiplex_search.planner.infer_search_hypotheses_with_ai")
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

    @patch("telepiplex_search.planner.infer_candidate_scorecard_with_ai")
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
