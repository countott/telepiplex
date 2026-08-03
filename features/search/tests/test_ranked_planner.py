import unittest
from unittest.mock import patch

from telepiplex_search.planner import (
    PlanningBudget,
    SearchPlanningError,
    _ordered_expansion_candidates,
    _source_media_type_clarification_plan,
    build_confirmable_search_plan,
)
from telepiplex_search.entity_graph import (
    CandidateEntity,
    EvidenceFact,
    build_search_graph,
)
from telepiplex_search.search_plan import TemporarySpecialAllocator
from telepiplex_search.search_plan import confirm_media_metadata
from telepiplex_search.series_scope import apply_series_scope


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


def _dual_media_provider(
    provider,
    *,
    title,
    movie_year,
    series_year,
    movie_english,
    series_english,
):
    def provide(hypotheses):
        queries = (
            (hypotheses.get("source_queries") or {}).get(provider)
            or []
        )
        if not any(title in str(query) for query in queries):
            return {
                "source": provider,
                "status": "not_found",
                "facts": [],
            }
        if provider == "tvdb":
            return {
                "source": provider,
                "status": "ok",
                "facts": [{
                    "movies": [{
                        "tvdb_movie_id": "855",
                        "name": title,
                        "english_title": movie_english,
                        "official_english_title": movie_english,
                        "year": movie_year,
                    }],
                    "series": [{
                        "tvdb_series_id": "273690",
                        "name": title,
                        "english_title": series_english,
                        "official_english_title": series_english,
                        "year": series_year,
                    }],
                }],
            }
        key = "subject_id" if provider == "douban" else "wikibase_item"
        return {
            "source": provider,
            "status": "ok",
            "facts": [{
                key: f"{provider}-movie-{movie_year}",
                "title": title,
                "chinese_title": title,
                "english_title": movie_english,
                "official_english_title": movie_english,
                "year": movie_year,
                "media_type": "movie",
            }, {
                key: f"{provider}-series-{series_year}",
                "title": title,
                "chinese_title": title,
                "english_title": series_english,
                "official_english_title": series_english,
                "year": series_year,
                "media_type": "series",
            }],
        }

    return provide


def _someday_provider(provider):
    def provide(_hypotheses):
        if provider == "tvdb":
            return {
                "source": provider,
                "status": "ok",
                "facts": [{
                    "movies": [{
                        "tvdb_movie_id": "342532",
                        "name": "想見你",
                        "english_title": "Someday or One Day: The Movie",
                        "official_english_title": (
                            "Someday or One Day: The Movie"
                        ),
                        "year": "2022",
                    }],
                    "series": [{
                        "tvdb_series_id": "someday-2019",
                        "name": "想见你",
                        "english_title": "Someday or One Day",
                        "official_english_title": "Someday or One Day",
                        "year": "2019",
                    }],
                    "episodes_by_series": {
                        "someday-2019": [{
                            "tvdb_episode_id": "episode-1",
                            "name": "Episode 1",
                            "season_number": 1,
                            "episode_number": 1,
                        }],
                    },
                }],
            }
        key = "subject_id" if provider == "douban" else "wikibase_item"
        return {
            "source": provider,
            "status": "ok",
            "facts": [{
                key: f"{provider}-movie-2022",
                "title": "想見你",
                "chinese_title": "想見你",
                "english_title": "Someday or One Day: The Movie",
                "official_english_title": "Someday or One Day: The Movie",
                "year": "2022",
                "media_type": "movie",
            }, {
                key: f"{provider}-series-2019",
                "title": "想见你",
                "chinese_title": "想见你",
                "english_title": "Someday or One Day",
                "official_english_title": "Someday or One Day",
                "year": "2019",
                "media_type": "series",
                "episodes": [{
                    "tvdb_episode_id": "episode-1",
                    "name": "Episode 1",
                    "season_number": 1,
                    "episode_number": 1,
                }],
            }],
        }

    return provide


def _parsed_ai_hypotheses(title):
    return {
        "status": "ok",
        "hypotheses": [{
            "title": title,
            "year": "",
            "content_identity": "unknown",
            "scope": "movie_or_series",
            "season_number": None,
            "episode_number": None,
            "possible_related_series": [],
            "explicit_facts": [],
            "inferred_facts": ["ai_intent_hint"],
        }],
        "source_queries": {
            provider: [title]
            for provider in ("wikipedia", "douban", "tvdb")
        },
        "warnings": ["ai_intent_hint_requires_source_verification"],
        "intent_hint": {
            "title_hints": [title],
            "media_type_hint": "unknown",
        },
        "clarification_reason": "",
    }


class RankedPlannerTest(unittest.IsolatedAsyncioTestCase):
    def test_source_clarification_filters_prefix_noise_and_keeps_both_types(
        self,
    ):
        def candidate(
            key,
            media_type,
            year,
            chinese,
            english,
        ):
            return CandidateEntity(key, (EvidenceFact(
                fact_id=f"{key}:fact",
                provider="tvdb",
                titles=(chinese, english),
                year=year,
                media_type=media_type,
                external_ids={"tvdb": key},
                official_english_title=english,
                chinese_title=chinese,
            ),))

        candidates = [
            candidate(
                "movie-related",
                "movie",
                "2022",
                "想見你",
                "Someday or One Day: The Movie",
            ),
            candidate(
                "movie-related-duplicate",
                "movie",
                "2022",
                "想見你",
                "Someday or One Day: The Movie",
            ),
            *[
                candidate(
                    f"movie-noise-{index}",
                    "movie",
                    str(2010 + index),
                    f"想见你以后{index}",
                    f"Unrelated Movie {index}",
                )
                for index in range(5)
            ],
            candidate(
                "series-related",
                "series",
                "2019",
                "想见你",
                "Someday or One Day",
            ),
            candidate(
                "series-noise",
                "series",
                "2024",
                "想见你父母",
                "Meet the Parents",
            ),
        ]

        plan = _source_media_type_clarification_plan(
            plan_id="prefix-noise",
            raw_query="想见你",
            intent={"title": "想见你"},
            candidates=candidates,
        )

        self.assertEqual(
            plan["clarification"]["options"],
            [{
                "label": "电影《想见你》(2022)",
                "query": "Someday or One Day: The Movie 2022（电影）",
                "media_type": "movie",
                "year": "2022",
                "locked_identity": {
                    "key": "tvdb",
                    "value": "movie-related",
                },
            }, {
                "label": "剧集《想见你》(2019)",
                "query": "Someday or One Day 2019（电视剧）",
                "media_type": "series",
                "year": "2019",
                "locked_identity": {
                    "key": "tvdb",
                    "value": "series-related",
                },
            }],
        )

    @patch(
        "telepiplex_search.planner.infer_candidate_scorecard_with_ai",
        return_value=None,
    )
    @patch("telepiplex_search.planner.infer_search_hypotheses_with_ai")
    async def test_ai_parsed_typo_with_movie_and_series_evidence_asks_user(
        self,
        infer_hypotheses,
        _scorecard,
    ):
        infer_hypotheses.return_value = _parsed_ai_hypotheses("康斯坦丁")
        providers = {
            name: _dual_media_provider(
                name,
                title="康斯坦丁",
                movie_year="2005",
                series_year="2014",
                movie_english="Constantine",
                series_english="Constantine",
            )
            for name in ("wikipedia", "douban", "tvdb")
        }

        plan = await build_confirmable_search_plan(
            "康斯坦汀",
            "p-constantine-typo-evidence-clarify",
            providers,
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(plan.get("status"), "needs_clarification")
        self.assertEqual(
            [option["media_type"] for option in plan["clarification"]["options"]],
            ["movie", "series"],
        )

    @patch(
        "telepiplex_search.planner.infer_candidate_scorecard_with_ai",
        return_value=None,
    )
    @patch("telepiplex_search.planner.infer_search_hypotheses_with_ai")
    async def test_post_gate_typo_recovery_with_type_conflict_asks_user(
        self,
        infer_hypotheses,
        _scorecard,
    ):
        infer_hypotheses.return_value = _parsed_ai_hypotheses("康斯坦丁")

        def provider(name):
            corrected_provider = _dual_media_provider(
                name,
                title="康斯坦丁",
                movie_year="2005",
                series_year="2014",
                movie_english="Constantine",
                series_english="Constantine",
            )

            def provide(hypotheses):
                corrected = corrected_provider(hypotheses)
                if corrected["status"] == "ok":
                    return corrected
                if name == "wikipedia":
                    return {
                        "source": name,
                        "status": "ok",
                        "facts": [{
                            "wikibase_item": "Q-typo-only",
                            "title": "康斯坦汀",
                            "chinese_title": "康斯坦汀",
                            "year": "2005",
                            "media_type": "movie",
                        }],
                    }
                return {
                    "source": name,
                    "status": "not_found",
                    "facts": [],
                }

            return provide

        plan = await build_confirmable_search_plan(
            "康斯坦汀",
            "p-post-gate-type-conflict",
            {
                name: provider(name)
                for name in ("wikipedia", "douban", "tvdb")
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(plan.get("status"), "needs_clarification")
        self.assertEqual(
            plan["clarification"]["reason"],
            "来源证据同时匹配电影和剧集，请选择后继续验证。",
        )

    @patch(
        "telepiplex_search.planner.infer_candidate_scorecard_with_ai",
        return_value=None,
    )
    async def test_exact_constantine_movie_and_series_evidence_asks_user(
        self,
        _scorecard,
    ):
        providers = {
            name: _dual_media_provider(
                name,
                title="康斯坦丁",
                movie_year="2005",
                series_year="2014",
                movie_english="Constantine",
                series_english="Constantine",
            )
            for name in ("wikipedia", "douban", "tvdb")
        }

        plan = await build_confirmable_search_plan(
            "康斯坦丁",
            "p-constantine-exact-evidence-clarify",
            providers,
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(plan.get("status"), "needs_clarification")

    @patch(
        "telepiplex_search.planner.infer_candidate_scorecard_with_ai",
        return_value=None,
    )
    async def test_same_title_movie_and_series_evidence_asks_user(
        self,
        _scorecard,
    ):
        providers = {
            name: _dual_media_provider(
                name,
                title="想见你",
                movie_year="2022",
                series_year="2019",
                movie_english="Someday or One Day",
                series_english="Someday or One Day",
            )
            for name in ("wikipedia", "douban", "tvdb")
        }

        plan = await build_confirmable_search_plan(
            "想见你",
            "p-someday-evidence-clarify",
            providers,
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(plan.get("status"), "needs_clarification")

    @patch(
        "telepiplex_search.planner.infer_candidate_scorecard_with_ai",
        return_value=None,
    )
    async def test_simplified_and_traditional_related_works_get_verified_options(
        self,
        _scorecard,
    ):
        plan = await build_confirmable_search_plan(
            "想见你",
            "p-someday-verified-options",
            {
                name: _someday_provider(name)
                for name in ("wikipedia", "douban", "tvdb")
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(plan.get("status"), "needs_clarification")
        self.assertEqual(
            plan["clarification"]["options"],
            [{
                "label": "电影《想见你》(2022)",
                "query": "Someday or One Day: The Movie 2022（电影）",
                "media_type": "movie",
                "year": "2022",
                "locked_identity": {
                    "key": "tvdb",
                    "value": "342532",
                },
            }, {
                "label": "剧集《想见你》(2019)",
                "query": "Someday or One Day 2019（电视剧）",
                "media_type": "series",
                "year": "2019",
                "locked_identity": {
                    "key": "tvdb",
                    "value": "someday-2019",
                },
            }],
        )

    @patch(
        "telepiplex_search.planner.infer_candidate_scorecard_with_ai",
        return_value=None,
    )
    async def test_traditional_user_title_is_not_converted_for_display(
        self,
        _scorecard,
    ):
        plan = await build_confirmable_search_plan(
            "想見你",
            "p-someday-traditional-display",
            {
                name: _someday_provider(name)
                for name in ("wikipedia", "douban", "tvdb")
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(
            [
                option["label"]
                for option in plan["clarification"]["options"]
            ],
            [
                "电影《想見你》(2022)",
                "剧集《想見你》(2019)",
            ],
        )

    @patch(
        "telepiplex_search.planner.infer_candidate_scorecard_with_ai",
        return_value=None,
    )
    async def test_verified_someday_identity_lock_resolves_selected_work(
        self,
        _scorecard,
    ):
        cases = (
            (
                "Someday or One Day: The Movie 2022（电影）",
                ("tvdb", "342532"),
                "movie",
                "2022",
            ),
            (
                "Someday or One Day 2019（电视剧）",
                ("tvdb", "someday-2019"),
                "series",
                "2019",
            ),
        )
        for index, (query, identity, media_type, year) in enumerate(cases):
            with self.subTest(media_type=media_type):
                plan = await build_confirmable_search_plan(
                    query,
                    f"p-someday-locked-{index}",
                    {
                        name: _someday_provider(name)
                        for name in ("wikipedia", "douban", "tvdb")
                    },
                    lambda _contract: set(),
                    TemporarySpecialAllocator(),
                    locked_identity=identity,
                )

                self.assertNotIn("clarification", plan)
                self.assertEqual(len(plan["candidates"]), 1)
                contract = plan["candidates"][0]["media_metadata"]
                self.assertEqual(
                    contract["retrieval"]["media_type"],
                    media_type,
                )
                self.assertEqual(contract["identity"]["year"], year)

    @patch(
        "telepiplex_search.planner.infer_candidate_scorecard_with_ai",
        return_value=None,
    )
    async def test_explicit_movie_bypasses_source_media_type_clarification(
        self,
        _scorecard,
    ):
        providers = {
            name: _dual_media_provider(
                name,
                title="康斯坦丁",
                movie_year="2005",
                series_year="2014",
                movie_english="Constantine",
                series_english="Constantine",
            )
            for name in ("wikipedia", "douban", "tvdb")
        }

        plan = await build_confirmable_search_plan(
            "康斯坦丁（电影）",
            "p-constantine-explicit-movie",
            providers,
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertNotIn("clarification", plan)
        self.assertEqual(len(plan["candidates"]), 1)
        self.assertEqual(
            plan["candidates"][0]["media_metadata"]["retrieval"]["media_type"],
            "movie",
        )

    @patch(
        "telepiplex_search.planner.infer_candidate_scorecard_with_ai",
        return_value=None,
    )
    @patch("telepiplex_search.planner.infer_search_hypotheses_with_ai")
    async def test_explicit_year_resolves_ai_media_type_clarification(
        self,
        infer_hypotheses,
        _scorecard,
    ):
        ai_result = _parsed_ai_hypotheses("康斯坦丁")
        ai_result["status"] = "needs_clarification"
        ai_result["clarification_reason"] = "可能指电影或剧集。"
        infer_hypotheses.return_value = ai_result
        providers = {
            name: _dual_media_provider(
                name,
                title="康斯坦丁",
                movie_year="2005",
                series_year="2014",
                movie_english="Constantine",
                series_english="Constantine",
            )
            for name in ("wikipedia", "douban", "tvdb")
        }

        plan = await build_confirmable_search_plan(
            "康斯坦汀 2005",
            "p-constantine-explicit-year",
            providers,
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertNotIn("clarification", plan)
        self.assertEqual(len(plan["candidates"]), 1)
        self.assertEqual(
            plan["candidates"][0]["media_metadata"]["identity"]["year"],
            "2005",
        )

    @patch(
        "telepiplex_search.planner.infer_candidate_scorecard_with_ai",
        return_value=None,
    )
    async def test_single_verified_movie_does_not_ask_for_media_type(
        self,
        _scorecard,
    ):
        providers = {
            name: _provider(name, 1, title="布达佩斯大饭店")
            for name in ("wikipedia", "douban", "tvdb")
        }

        plan = await build_confirmable_search_plan(
            "布达佩斯大饭店",
            "p-grand-budapest-single",
            providers,
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertNotIn("clarification", plan)
        self.assertEqual(len(plan["candidates"]), 1)

    @patch("telepiplex_search.planner.infer_candidate_scorecard_with_ai")
    @patch(
        "telepiplex_search.planner.infer_search_hypotheses_with_ai",
        return_value=None,
    )
    async def test_empty_ranked_candidates_skip_ai_scorecard(
        self,
        _infer_hypotheses,
        scorecard,
    ):
        with self.assertRaises(SearchPlanningError) as raised:
            await build_confirmable_search_plan(
                "只有单一来源的剧集",
                "p-empty-scorecard",
                {
                    "douban": _provider(
                        "douban",
                        1,
                        title="只有单一来源的剧集",
                        media_type="series",
                    ),
                },
                lambda _contract: set(),
                TemporarySpecialAllocator(),
            )

        self.assertEqual(raised.exception.code, "insufficient_independent_support")
        scorecard.assert_not_called()

    @patch("telepiplex_search.planner.infer_candidate_scorecard_with_ai")
    @patch(
        "telepiplex_search.planner.infer_search_hypotheses_with_ai",
        return_value=None,
    )
    async def test_explicit_series_rejects_movie_before_ai_scorecard(
        self,
        _infer_hypotheses,
        scorecard,
    ):
        with self.assertRaises(SearchPlanningError) as raised:
            await build_confirmable_search_plan(
                "想见你 2024（电视剧）",
                "p-explicit-series-movie-only",
                {
                    name: _provider(
                        name,
                        1,
                        title="想见你",
                        media_type="movie",
                    )
                    for name in ("wikipedia", "douban", "tvdb")
                },
                lambda _contract: set(),
                TemporarySpecialAllocator(),
            )

        self.assertEqual(
            raised.exception.code,
            "insufficient_independent_support",
        )
        scorecard.assert_not_called()

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

    @patch("telepiplex_search.planner.infer_search_hypotheses_with_ai")
    async def test_ai_typo_recovery_runs_after_lexical_candidates_fail_gate(
        self,
        infer_hypotheses,
    ):
        infer_hypotheses.return_value = {
            "status": "ok",
            "hypotheses": [{
                "title": "康斯坦丁",
                "year": "",
                "content_identity": "movie",
                "scope": "movie_or_series",
                "season_number": None,
                "episode_number": None,
                "possible_related_series": [],
                "explicit_facts": [],
                "inferred_facts": ["ai_intent_hint"],
            }],
            "source_queries": {
                "wikipedia": ["康斯坦丁"],
                "douban": ["康斯坦丁"],
                "tvdb": ["康斯坦丁"],
            },
            "warnings": ["ai_intent_hint_requires_source_verification"],
            "intent_hint": {
                "title_hints": ["康斯坦丁"],
                "media_type_hint": "movie",
            },
        }

        def provider(name):
            def provide(hypotheses):
                queries = (
                    (hypotheses.get("source_queries") or {}).get(name)
                    or []
                )
                corrected = "康斯坦丁" in queries
                if not corrected and name != "wikipedia":
                    return {
                        "source": name,
                        "status": "not_found",
                        "facts": [],
                    }
                title = "康斯坦丁" if corrected else "康斯坦汀"
                key = (
                    "subject_id"
                    if name == "douban"
                    else "wikibase_item"
                )
                return {
                    "source": name,
                    "status": "ok",
                    "facts": [{
                        key: f"{name}-constantine",
                        "title": title,
                        "chinese_title": title,
                        "english_title": "Constantine",
                        "official_english_title": "Constantine",
                        "year": "2005",
                        "media_type": "movie",
                    }],
                }

            return provide

        plan = await build_confirmable_search_plan(
            "康斯坦汀",
            "p-post-gate-typo",
            {
                "wikipedia": provider("wikipedia"),
                "douban": provider("douban"),
                "tvdb": provider("tvdb"),
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        infer_hypotheses.assert_called_once()
        self.assertEqual(len(plan["candidates"]), 1)
        self.assertEqual(
            plan["candidates"][0]["media_metadata"]["identity"]["chinese_title"],
            "康斯坦丁",
        )
        self.assertEqual(
            plan["candidates"][0]["media_metadata"]["identity"]["english_title"],
            "Constantine",
        )

    @patch("telepiplex_search.planner.infer_search_hypotheses_with_ai")
    async def test_ai_clarification_returns_movie_and_series_options(
        self,
        infer_hypotheses,
    ):
        infer_hypotheses.return_value = {
            "status": "needs_clarification",
            "hypotheses": [{
                "title": title,
                "year": "",
                "content_identity": "unknown",
                "scope": "movie_or_series",
                "season_number": None,
                "episode_number": None,
                "possible_related_series": [],
                "explicit_facts": [],
                "inferred_facts": ["ai_intent_hint"],
            } for title in ("康斯坦汀", "康斯坦丁")],
            "source_queries": {
                provider: ["康斯坦汀", "康斯坦丁"]
                for provider in ("wikipedia", "douban", "tvdb")
            },
            "warnings": ["ai_intent_hint_requires_source_verification"],
            "intent_hint": {
                "title_hints": ["康斯坦汀", "康斯坦丁"],
                "media_type_hint": "unknown",
            },
            "clarification_reason": "可能指电影或剧集。",
        }

        plan = await build_confirmable_search_plan(
            "康斯坦汀",
            "p-ai-clarify",
            {
                "wikipedia": _provider(
                    "wikipedia", 1, title="康斯坦汀"
                ),
                "douban": lambda _hypotheses: {
                    "source": "douban",
                    "status": "not_found",
                    "facts": [],
                },
                "tvdb": lambda _hypotheses: {
                    "source": "tvdb",
                    "status": "not_found",
                    "facts": [],
                },
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(plan["status"], "needs_clarification")
        self.assertEqual(
            [option["query"] for option in plan["clarification"]["options"]],
            ["康斯坦汀（电影）", "康斯坦汀（电视剧）"],
        )
        self.assertEqual(
            plan["clarification"]["reason"],
            "可能指电影或剧集。",
        )

    @patch("telepiplex_search.planner.infer_search_hypotheses_with_ai")
    async def test_explicit_movie_constraint_consumes_ai_title_hints(
        self,
        infer_hypotheses,
    ):
        infer_hypotheses.return_value = {
            "status": "needs_clarification",
            "hypotheses": [{
                "title": "康斯坦丁",
                "year": "",
                "content_identity": "movie",
                "scope": "movie_or_series",
                "season_number": None,
                "episode_number": None,
                "possible_related_series": [],
                "explicit_facts": [],
                "inferred_facts": ["ai_intent_hint"],
            }],
            "source_queries": {
                provider: ["康斯坦丁"]
                for provider in ("wikipedia", "douban", "tvdb")
            },
            "warnings": ["ai_intent_hint_requires_source_verification"],
            "intent_hint": {
                "title_hints": ["康斯坦丁"],
                "media_type_hint": "unknown",
            },
            "clarification_reason": "可能指电影或剧集。",
        }

        def provider(name):
            def provide(hypotheses):
                queries = (
                    (hypotheses.get("source_queries") or {}).get(name)
                    or []
                )
                if "康斯坦丁" not in queries:
                    return {
                        "source": name,
                        "status": "not_found",
                        "facts": [],
                    }
                if name == "tvdb":
                    return {
                        "source": name,
                        "status": "ok",
                        "facts": [{
                            "movies": [{
                                "tvdb_movie_id": "855",
                                "name": "康斯坦丁",
                                "english_title": "Constantine",
                                "year": "2005",
                            }],
                            "series": [],
                        }],
                    }
                key = (
                    "subject_id"
                    if name == "douban"
                    else "wikibase_item"
                )
                return {
                    "source": name,
                    "status": "ok",
                    "facts": [{
                        key: f"{name}-constantine",
                        "title": "康斯坦丁",
                        "chinese_title": "康斯坦丁",
                        "english_title": "Constantine",
                        "official_english_title": "Constantine",
                        "year": "2005",
                        "media_type": "movie",
                    }],
                }

            return provide

        plan = await build_confirmable_search_plan(
            "康斯坦汀（电影）",
            "p-ai-clarified-movie",
            {
                provider_name: provider(provider_name)
                for provider_name in ("wikipedia", "douban", "tvdb")
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertNotIn("clarification", plan)
        self.assertEqual(len(plan["candidates"]), 1)
        self.assertEqual(
            plan["candidates"][0]["media_metadata"]["retrieval"]["media_type"],
            "movie",
        )

    async def test_direct_anchor_keeps_locked_identity(self):
        plan = await build_confirmable_search_plan(
            "Movie 1 2024",
            "p-direct-locked",
            {"douban": _provider("douban", 1, title="Movie 1")},
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            locked_identity=("douban_subject", "douban-0"),
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

    async def test_legacy_budget_argument_no_longer_stops_planning(self):
        plan = await build_confirmable_search_plan(
            "Movie",
            "p-budget",
            {
                "douban": _provider("douban", 1),
                "wikipedia": _provider("wikipedia", 1),
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            budget=PlanningBudget(total=0),
        )

        self.assertEqual(len(plan["candidates"]), 1)


if __name__ == "__main__":
    unittest.main()
