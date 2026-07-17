import unittest
from unittest.mock import patch

from telepiplex_media_search.planner import (
    PlanningBudget,
    SearchPlanningError,
    build_confirmable_search_plan,
)
from telepiplex_media_search.search_plan import TemporarySpecialAllocator
from telepiplex_media_search.search_plan import confirm_media_metadata
from telepiplex_media_search.series_scope import apply_series_scope


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


class RankedPlannerTest(unittest.IsolatedAsyncioTestCase):
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

    @patch("telepiplex_media_search.planner.infer_search_hypotheses_with_ai")
    async def test_clear_query_does_not_require_ai_score_or_intent(self, infer):
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
        self.assertNotIn("ai_total", plan["candidates"][0]["score"])
        self.assertEqual(
            plan["candidates"][0]["media_metadata"]["evidence"]["decision"]["mode"],
            "deterministic_bounded",
        )

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
