import unittest
from unittest.mock import patch

from telepiplex_media_search.planner import (
    PlanningBudget,
    SearchPlanningError,
    build_confirmable_search_plan,
)
from telepiplex_media_search.search_plan import TemporarySpecialAllocator


def sources_for_glory(_hypotheses):
    return {
        "source": "douban",
        "status": "ok",
        "facts": [{
            "subject_id": "35314632",
            "title": "The Glory",
            "chinese_title": "黑暗荣耀",
            "english_title": "The Glory",
            "official_english_title": "The Glory",
            "original_title": "더 글로리",
            "original_language": "ko",
            "year": "2022",
            "media_type": "series",
            "url": "https://movie.douban.com/subject/35314632/",
        }, {
            "subject_id": "noise",
            "title": "Terminator: Dark Fate",
            "english_title": "Terminator: Dark Fate",
            "official_english_title": "Terminator: Dark Fate",
            "year": "2019",
            "media_type": "movie",
            "url": "https://movie.douban.com/subject/noise/",
        }],
        "source_urls": [],
    }


def wikipedia_glory(_hypotheses):
    return {
        "source": "wikipedia",
        "status": "ok",
        "facts": [{
            "wikibase_item": "Q114639581",
            "title": "黑暗荣耀",
            "english_title": "The Glory",
            "official_english_title": "The Glory",
            "year": "2022",
            "media_type": "series",
            "url": "https://zh.wikipedia.org/wiki/黑暗荣耀",
        }],
        "source_urls": [],
    }


def ai_score(context):
    return {"scorecards": [{
        "candidate_key": item["candidate_key"],
        "title_equivalence": {"score": 20, "fact_ids": [item["fact_ids"][0]]},
        "relation_consistency": {"score": 10, "fact_ids": [item["fact_ids"][0]]},
        "intent_relevance": {"score": 10, "fact_ids": [item["fact_ids"][0]]},
    } for item in context["candidates"]]}


class RankedPlannerTest(unittest.IsolatedAsyncioTestCase):
    @patch("telepiplex_media_search.planner.score_candidates_with_ai", side_effect=ai_score)
    async def test_wrong_year_keeps_title_match_not_same_year_noise(self, _score):
        plan = await build_confirmable_search_plan(
            "黑暗荣耀 2019",
            "p1",
            {"douban": sources_for_glory, "wikipedia": wikipedia_glory},
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(
            plan["candidates"][0]["media_metadata"]["identity"]["english_title"],
            "The Glory",
        )
        self.assertNotIn(
            "Terminator",
            [item["media_metadata"]["identity"]["english_title"] for item in plan["candidates"]],
        )
        self.assertEqual(plan["candidates"][0]["score"]["release_consistency"], 0)

    @patch("telepiplex_media_search.planner.score_candidates_with_ai", side_effect=ai_score)
    @patch("telepiplex_media_search.planner.infer_relation_hypotheses_with_ai")
    async def test_relation_scout_runs_before_scoring_for_complex_signals(
        self, relation, score
    ):
        calls = []
        relation.side_effect = lambda _context: calls.append("relation_scout") or {"hypotheses": []}
        score.side_effect = lambda context: calls.append("scorecard") or ai_score(context)

        await build_confirmable_search_plan(
            "黑暗荣耀 特别篇",
            "p2",
            {"douban": sources_for_glory, "wikipedia": wikipedia_glory},
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertLess(calls.index("relation_scout"), calls.index("scorecard"))

    async def test_exhausted_total_budget_fails_structurally(self):
        with self.assertRaisesRegex(SearchPlanningError, "planning_timed_out"):
            await build_confirmable_search_plan(
                "黑暗荣耀",
                "p3",
                {"douban": sources_for_glory},
                lambda _contract: set(),
                TemporarySpecialAllocator(),
                budget=PlanningBudget(total=0),
            )

    @patch("telepiplex_media_search.planner.score_candidates_with_ai", side_effect=ai_score)
    async def test_ranked_candidate_limit_is_five(self, _score):
        def many(_hypotheses):
            facts = []
            for number in range(8):
                facts.append({
                    "subject_id": str(number),
                    "title": f"Movie {number}",
                    "english_title": f"Movie {number}",
                    "official_english_title": f"Movie {number}",
                    "year": "2024",
                    "media_type": "movie",
                })
            return {"source": "douban", "status": "ok", "facts": facts}

        plan = await build_confirmable_search_plan(
            "unknown",
            "p4",
            {"douban": many},
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        self.assertEqual(len(plan["candidates"]), 5)


if __name__ == "__main__":
    unittest.main()
