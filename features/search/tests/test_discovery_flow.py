import unittest

from telepiplex_search.discovery_flow import (
    SearchContext,
    SearchDecisionError,
    build_douban_first_search_plan,
    decide_with_technical_retry,
    validate_search_decision,
)
from telepiplex_search.planner import SearchPlanningError


def douban_fact(
    subject_id,
    title,
    *,
    year="2023",
    media_type="series",
    english_title="",
):
    return {
        "subject_id": str(subject_id),
        "title": title,
        "chinese_title": title,
        "english_title": english_title,
        "year": year,
        "media_type": media_type,
        "url": f"https://movie.douban.com/subject/{subject_id}/",
    }


def context(*, attempt=1, query="繁花", candidates=None):
    return SearchContext(
        search_session_id="session-1",
        original_input="繁花",
        title="繁花",
        year="",
        media_type="",
        scope="work",
        season_number=None,
        episode_number=None,
        attempt=attempt,
        query=query,
        candidates=tuple(candidates or (
            douban_fact("1", "繁花"),
            douban_fact("2", "繁花似锦"),
        )),
        history=(),
        retry_available=attempt == 1,
    )


class SearchDecisionContractTest(unittest.TestCase):
    def test_valid_shortlist_keeps_ai_order(self):
        decision = validate_search_decision({
            "action": "show_candidates",
            "candidate_ids": ["2", "1"],
            "rewrite_query": "",
        }, context())

        self.assertEqual(decision.candidate_ids, ("2", "1"))

    def test_rejects_unknown_duplicate_oversized_or_single_multi_pool_ids(self):
        payloads = (
            ["9", "1"],
            ["1", "1"],
            ["1", "2", "3", "4", "5", "6"],
            ["1"],
        )
        for candidate_ids in payloads:
            with self.subTest(candidate_ids=candidate_ids):
                with self.assertRaises(SearchDecisionError):
                    validate_search_decision({
                        "action": "show_candidates",
                        "candidate_ids": candidate_ids,
                        "rewrite_query": "",
                    }, context(candidates=tuple(
                        douban_fact(str(index), f"标题{index}")
                        for index in range(1, 7)
                    )))

    def test_rejects_second_attempt_or_unchanged_rewrite(self):
        for current in (
            context(attempt=2),
            context(query="繁花"),
        ):
            payload = {
                "action": "retry",
                "candidate_ids": [],
                "rewrite_query": (
                    "繁花"
                    if current.attempt == 1
                    else "Blossoms Shanghai"
                ),
            }
            with self.subTest(attempt=current.attempt):
                with self.assertRaises(SearchDecisionError):
                    validate_search_decision(payload, current)

    def test_rejects_extra_fields(self):
        with self.assertRaises(SearchDecisionError):
            validate_search_decision({
                "action": "show_candidates",
                "candidate_ids": ["1", "2"],
                "rewrite_query": "",
                "reason": "extra",
            }, context())

    def test_technical_retry_receives_identical_context(self):
        calls = []

        def decide(payload):
            calls.append(payload)
            if len(calls) == 1:
                return {"invalid": True}
            return {
                "action": "show_candidates",
                "candidate_ids": ["1", "2"],
                "rewrite_query": "",
            }

        decision = decide_with_technical_retry(
            context(),
            decide,
            logger=None,
        )

        self.assertEqual(decision.action, "show_candidates")
        self.assertEqual(calls[0], calls[1])


class DoubanFirstPlannerTest(unittest.IsolatedAsyncioTestCase):
    async def test_unique_exact_candidate_auto_confirms_without_ai(self):
        ai_calls = []

        def provider(payload):
            self.assertEqual(
                payload["source_queries"]["douban"],
                ["繁花"],
            )
            return {
                "source": "douban",
                "status": "ok",
                "facts": [douban_fact("1", "繁花")],
                "source_urls": [],
                "error": "",
            }

        plan = await build_douban_first_search_plan(
            "繁花",
            "plan-1",
            provider,
            ai_decider=lambda payload: ai_calls.append(payload),
        )

        self.assertTrue(plan["auto_confirm"])
        self.assertEqual(ai_calls, [])
        self.assertEqual(plan["candidates"][0]["candidate_id"], "douban:1")

    async def test_multi_result_uses_ai_order_and_only_douban(self):
        calls = []

        def provider(payload):
            calls.append(payload)
            return {
                "source": "douban",
                "status": "ok",
                "facts": [
                    douban_fact("1", "繁花似锦"),
                    douban_fact("2", "繁花之城"),
                    douban_fact("3", "繁花年代"),
                ],
                "source_urls": [],
                "error": "",
            }

        plan = await build_douban_first_search_plan(
            "繁花",
            "plan-2",
            provider,
            ai_decider=lambda _context: {
                "action": "show_candidates",
                "candidate_ids": ["3", "1"],
                "rewrite_query": "",
            },
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            [item["candidate_id"] for item in plan["candidates"]],
            ["douban:3", "douban:1"],
        )
        self.assertFalse(plan["auto_confirm"])

    async def test_zero_results_use_one_business_rewrite(self):
        queries = []

        def provider(payload):
            query = payload["source_queries"]["douban"][0]
            queries.append(query)
            facts = (
                [douban_fact("7", "The Glory", year="2022")]
                if query == "The Glory 2022"
                else []
            )
            return {
                "source": "douban",
                "status": "ok" if facts else "not_found",
                "facts": facts,
                "source_urls": [],
                "error": "",
            }

        decisions = iter((
            {
                "action": "retry",
                "candidate_ids": [],
                "rewrite_query": "The Glory 2022",
            },
            {
                "action": "show_candidates",
                "candidate_ids": ["7"],
                "rewrite_query": "",
            },
        ))
        plan = await build_douban_first_search_plan(
            "黑暗荣耀",
            "plan-3",
            provider,
            ai_decider=lambda _context: next(decisions),
        )

        self.assertEqual(queries, ["黑暗荣耀", "The Glory 2022"])
        self.assertFalse(plan["auto_confirm"])

    async def test_second_no_match_is_terminal(self):
        def provider(_payload):
            return {
                "source": "douban",
                "status": "not_found",
                "facts": [],
                "source_urls": [],
                "error": "",
            }

        decisions = iter((
            {
                "action": "retry",
                "candidate_ids": [],
                "rewrite_query": "Different",
            },
            {
                "action": "no_match",
                "candidate_ids": [],
                "rewrite_query": "",
            },
        ))
        with self.assertRaisesRegex(SearchPlanningError, "no_match"):
            await build_douban_first_search_plan(
                "Unknown",
                "plan-4",
                provider,
                ai_decider=lambda _context: next(decisions),
            )

    async def test_provider_failure_never_calls_ai(self):
        ai_calls = []

        with self.assertRaisesRegex(SearchPlanningError, "source_failure"):
            await build_douban_first_search_plan(
                "繁花",
                "plan-5",
                lambda _payload: {
                    "source": "douban",
                    "status": "rate_limited",
                    "facts": [],
                    "source_urls": [],
                    "error": "429",
                },
                ai_decider=lambda payload: ai_calls.append(payload),
            )

        self.assertEqual(ai_calls, [])

    async def test_ai_double_failure_falls_back_to_first_five_candidates(self):
        facts = [
            douban_fact(str(index), f"候选{index}")
            for index in range(1, 7)
        ]

        plan = await build_douban_first_search_plan(
            "候选",
            "plan-6",
            lambda _payload: {
                "source": "douban",
                "status": "ok",
                "facts": facts,
                "source_urls": [],
                "error": "",
            },
            ai_decider=lambda _context: None,
        )

        self.assertEqual(
            [item["candidate_id"] for item in plan["candidates"]],
            [f"douban:{index}" for index in range(1, 6)],
        )
        self.assertFalse(plan["auto_confirm"])

    async def test_ai_double_failure_without_facts_is_terminal(self):
        with self.assertRaisesRegex(
            SearchPlanningError,
            "ai_candidate_failure",
        ):
            await build_douban_first_search_plan(
                "Unknown",
                "plan-7",
                lambda _payload: {
                    "source": "douban",
                    "status": "not_found",
                    "facts": [],
                    "source_urls": [],
                    "error": "",
                },
                ai_decider=lambda _context: None,
            )


if __name__ == "__main__":
    unittest.main()
