import unittest
from unittest.mock import patch

from telepiplex_search.planner import (
    _provider_status_and_support,
    build_confirmable_search_plan,
    collect_evidence,
)
from telepiplex_search.search_plan import TemporarySpecialAllocator


class SearchPlannerServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_all_providers_run_and_soft_fail_independently(self):
        calls = []

        def ok(_hypotheses):
            calls.append("wikipedia")
            return {"status": "not_found", "facts": []}

        def down(_hypotheses):
            calls.append("douban")
            raise OSError("offline")

        evidence = await collect_evidence(
            {"source_queries": {}},
            {"wikipedia": ok, "douban": down},
        )

        self.assertEqual(set(calls), {"wikipedia", "douban"})
        self.assertEqual(
            {item["source"]: item["status"] for item in evidence},
            {"wikipedia": "not_found", "douban": "server_down"},
        )

    @patch(
        "telepiplex_search.planner.infer_candidate_scorecard_with_ai",
        return_value=None,
    )
    @patch("telepiplex_search.planner.infer_search_hypotheses_with_ai")
    async def test_clear_query_survives_unavailable_scorecard(
        self,
        infer,
        scorecard,
    ):
        def provider(provider_name):
            def provide(_hypotheses):
                key = "subject_id" if provider_name == "douban" else "wikibase_item"
                return {
                    "status": "ok",
                    "facts": [{
                        key: "1",
                        "title": "The Grand Budapest Hotel",
                        "chinese_title": "布达佩斯大饭店",
                        "official_english_title": "The Grand Budapest Hotel",
                        "original_title": "The Grand Budapest Hotel",
                        "original_language": "en",
                        "year": "2014",
                        "media_type": "movie",
                    }],
                }
            return provide

        plan = await build_confirmable_search_plan(
            "布达佩斯大饭店",
            "p1",
            {
                "douban": provider("douban"),
                "wikipedia": provider("wikipedia"),
            },
            lambda _contract: set(),
            TemporarySpecialAllocator(),
        )

        candidate = plan["candidates"][0]
        infer.assert_not_called()
        scorecard.assert_called_once()
        self.assertEqual(candidate["score"]["ai_total"], 0)
        self.assertTrue(candidate["selectable"])
        self.assertEqual(
            candidate["media_metadata"]["evidence"]["decision"]["mode"],
            "deterministic_bounded",
        )

    def test_provider_support_collects_only_provider_specific_stable_ids(self):
        statuses, support = _provider_status_and_support([{
            "source": "douban",
            "status": "ok",
            "facts": [{
                "subject_id": "1295644",
                "tvdb_series_id": "must-not-cross-provider",
                "url": "https://movie.douban.com/subject/1295644/",
            }],
        }])

        self.assertEqual(statuses, {"douban": "ok"})
        self.assertEqual(support["douban"]["stable_ids"], ["1295644"])

    def test_provider_support_normalizes_actual_source_urls(self):
        _, support = _provider_status_and_support([{
            "source": "wikipedia",
            "status": "ok",
            "facts": [{
                "wikibase_item": "Q1",
                "url": "HTTPS://ZH.WIKIPEDIA.ORG/wiki/Test/#fragment",
            }],
        }])

        self.assertEqual(
            support["wikipedia"]["source_urls"],
            ["https://zh.wikipedia.org/wiki/Test"],
        )


if __name__ == "__main__":
    unittest.main()
