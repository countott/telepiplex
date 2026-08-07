import ast
import asyncio
from copy import deepcopy
import html
import re
import tomllib
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import yaml


ROOT = Path(__file__).resolve().parents[1]


def search_plan():
    return {
        "plan_id": "plan-1",
        "prowlarr_queries": ["中文标题 2024", "English Title 2024"],
        "media_metadata": {
            "schema_version": 1,
            "metadata_id": "plan-1",
            "confirmed": False,
            "identity": {
                "chinese_title": "中文标题",
                "english_title": "English Title",
                "year": "2024",
                "content_kind": "movie",
                "external_ids": {},
            },
            "retrieval": {
                "media_type": "movie",
                "scope": "work",
                "query": "English Title 2024",
            },
            "relation": {"target_series": None, "source": "evidence"},
            "placement": {
                "category_kind": "live_action_movie",
                "library_type": "movie",
                "mapping_kind": "standalone",
                "season_number": None,
                "episode_number": None,
            },
            "evidence": {},
            "warnings": [],
            "items": [],
        },
    }


def clarification_plan(plan_id="clarify-1"):
    return {
        "plan_id": plan_id,
        "raw_query": "康斯坦汀",
        "status": "needs_clarification",
        "clarification": {
            "reason": "可能指电影或剧集。",
            "options": [{
                "label": "电影《康斯坦丁》",
                "query": "康斯坦丁（电影）",
                "media_type": "movie",
                "year": "",
            }, {
                "label": "剧集《康斯坦丁》",
                "query": "康斯坦丁（电视剧）",
                "media_type": "series",
                "year": "",
            }],
        },
        "candidates": [],
    }


def ranked_search_plan():
    result = search_plan()
    candidates = []
    for index, (title, year, poster) in enumerate((
        ("English Title", "2024", "https://image.example/top.jpg"),
        ("English Alternate", "2023", "https://image.example/second.jpg"),
    )):
        contract = deepcopy(result["media_metadata"])
        contract["identity"].update({
            "chinese_title": f"中文标题{index + 1}",
            "english_title": title,
            "year": year,
            "poster_url": poster,
            "poster_source": "tvdb",
        })
        contract["retrieval"]["query"] = f"{title} {year}"
        candidates.append({
            "candidate_key": f"tvdb:movie:{index + 1}",
            "score": {"total": 92 - index * 10},
            "recommended": index == 0,
            "selectable": True,
            "media_metadata": contract,
            "prowlarr_queries": [f"{title} {year}"],
            "poster_url": poster,
            "reasons": [],
            "entity_snapshot": {
                "entity_key": f"tvdb:movie:{index + 1}",
                "content_kind": "movie",
                "year": year,
                "chinese_title": f"中文标题{index + 1}",
                "original_title": title,
                "original_language": "en",
                "official_english_title": title,
                "romanized_original_title": "",
                "canonical_search_title": title,
                "search_title_policy": "official_english",
                "canonical_latin_title": title,
                "poster_url": poster,
                "poster_source": "tvdb",
                "external_ids": {"tvdb": str(index + 1)},
                "scoring_version": "media-entity-v1",
            },
            "relation_snapshot": {
                "relation_type": "standalone",
                "mapping_kind": "standalone",
            },
        })
    result["candidates"] = candidates
    return result


def related_ranked_search_plan():
    result = ranked_search_plan()
    candidate = result["candidates"][0]
    contract = candidate["media_metadata"]
    contract["identity"]["content_kind"] = "extension_movie"
    contract["relation"] = {
        "type": "extension_movie",
        "target_series": {
            "chinese_title": "中文剧集",
            "english_title": "English Series",
            "year": "2020",
            "external_ids": {"tvdb": "100"},
        },
        "source": "verified_relation_scorecard",
    }
    contract["placement"].update({
        "library_type": "series",
        "category_kind": "live_action_series",
        "season_number": 0,
        "episode_number": None,
        "mapping_kind": "temporary_related_special",
        "mapping_source": "local_allocator_after_verified_relation",
    })
    source_url = "https://movie.douban.com/subject/1/"
    contract["source_entry"] = {
        "title": "中文标题1",
        "url": source_url,
        "provider": "douban",
        "verification": "verified",
    }
    contract["evidence"] = {
        "provider_statuses": {"douban": "ok"},
        "provider_support": {"douban": {
            "has_facts": True,
            "source_urls": [source_url],
            "stable_ids": ["1"],
        }},
        "verified_tvdb_special_candidates": [],
        "tvdb_official_special_candidates": [],
        "decision": {"mode": "fixed_scorecard"},
    }
    candidate["entity_snapshot"]["content_kind"] = "extension_movie"
    candidate["relation_snapshot"] = {
        "relation_type": "extension_movie",
        "target_entity_key": "tvdb:series:100",
        "target_chinese_title": "中文剧集",
        "target_canonical_latin_title": "English Series",
        "target_year": "2020",
        "target_external_ids": {"tvdb": "100"},
        "mapping_kind": "temporary_related_special",
        "season_number": 0,
        "episode_number": None,
        "tvdb_episode_id": "",
    }
    result["media_metadata"] = contract
    return result


def series_ranked_search_plan():
    result = ranked_search_plan()
    candidate = result["candidates"][0]
    contract = candidate["media_metadata"]
    contract["identity"].update({
        "chinese_title": "黑暗荣耀",
        "english_title": "The Glory",
        "year": "2022",
        "content_kind": "series",
    })
    contract["retrieval"] = {
        "media_type": "series",
        "scope": "work",
        "query": "The Glory 2022",
    }
    contract["placement"].update({
        "library_type": "series",
        "category_kind": "live_action_series",
    })
    contract["items"] = [{
        "item_id": f"e{number}",
        "content_role": "main_episode",
        "season_number": 1,
        "episode_number": number,
        "aired": "2022-12-30",
    } for number in range(1, 9)]
    contract["evidence"] = {
        "decision": {
            "mode": "deterministic_bounded",
            "scope": "movie_or_series",
            "season_number": None,
            "episode_number": None,
        }
    }
    candidate["prowlarr_queries"] = ["The Glory 2022"]
    result["media_metadata"] = contract
    return result


class FakeHost:
    def __init__(self):
        self.calls = []
        self.reports = []
        self.milestones = []

    async def call_capability(self, capability, method, payload, **kwargs):
        self.calls.append((capability, method, payload, kwargs))
        return {"accepted": True, "job_id": "download-1"}

    async def report_operation(self, operation):
        self.reports.append(operation)
        return {
            "accepted": True,
            "state": operation["state"],
            "revision": operation["revision"],
        }

    async def publish_operation_milestone(
        self,
        operation_id,
        milestone_id,
        text,
        *,
        photo_url="",
        deadline=10,
    ):
        self.milestones.append({
            "operation_id": operation_id,
            "milestone_id": milestone_id,
            "text": text,
            "photo_url": photo_url,
            "deadline": deadline,
        })
        return {"accepted": True, "duplicate": False}


class FakeRuntime:
    def __init__(self):
        self.tasks = {}

    def spawn(self, awaitable, *, task_id):
        self.tasks[task_id] = awaitable
        return None

    async def run(self, prefix):
        task_id = next(key for key in self.tasks if key.startswith(prefix))
        await self.tasks.pop(task_id)


class SearchFeatureTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from telepiplex_search.service import SearchFeature

        self.host = FakeHost()
        self.search_queries = []

        async def planner(raw_query, plan_id):
            result = search_plan()
            result["plan_id"] = plan_id
            result["media_metadata"]["metadata_id"] = plan_id
            return result

        def search(query, media_type):
            self.search_queries.append((query, media_type))
            return [{
                "title": "English.Title.2024.1080p.WEB-DL",
                "magnet_url": "magnet:?xt=urn:btih:" + "a" * 40,
                "seeders": 10,
                "size": 100,
                "indexer": "test",
            }]

        async def selected_candidate_passthrough(candidate, _raw_query):
            return candidate

        self.feature = SearchFeature(
            config={
                "category_folder": [{
                    "kind": "live_action_movie",
                    "name": "电影",
                    "path": "/Movies",
                    "plex_library_id": "",
                }, {
                    "kind": "live_action_series",
                    "name": "剧集",
                    "path": "/Series",
                    "plex_library_id": "",
                }],
                "search": {"prowlarr": {"result_limit": 8}},
            },
            host=self.host,
            plan_builder=planner,
            release_search=search,
            release_rank=lambda items, limit: items[:limit],
            release_resolver=lambda item: item["magnet_url"],
            selected_candidate_supplementer=selected_candidate_passthrough,
        )
        self.runtime = FakeRuntime()
        self.feature.bind_runtime(self.runtime)

    async def asyncTearDown(self):
        for awaitable in self.runtime.tasks.values():
            awaitable.close()

    async def _prepare_search(self):
        command = await self.feature.command({
            "command": "search",
            "args": ["English", "Title"],
            "user_id": 1,
            "chat_id": 10,
        })
        self.assertEqual(command["operation"]["state"], "running")
        await self.runtime.run("search-plan-")
        plan_report = self.host.reports[-1]
        callback_data = plan_report["details"]["keyboard"][0][0]["callback_data"]
        return callback_data.rsplit(":", 1)[-1]

    async def test_background_operation_rejection_is_logged_once_without_second_report(
        self,
    ):
        from telepiplex_search.context import runtime_context

        self.host.report_operation = AsyncMock(return_value={
            "accepted": False,
            "error_code": "operation_owner_conflict",
        })
        logger = Mock()
        original = runtime_context.logger
        runtime_context.logger = logger
        try:
            command = await self.feature.command({
                "command": "s",
                "args": ["后室"],
                "user_id": 1,
                "chat_id": 10,
                "update_id": 99,
            })
            await self.runtime.run("search-plan-")
        finally:
            runtime_context.logger = original

        self.host.report_operation.assert_awaited_once()
        operation_id = command["operation"]["operation_id"]
        messages = [
            call.args[0]
            for call in logger.warning.call_args_list
        ]
        self.assertTrue(any(
            "event=search.operation_report_failed" in message
            and f"operation_id={operation_id}" in message
            and "update_id=99" in message
            for message in messages
        ))
        self.assertTrue(any(
            "event=search.background_task_failed" in message
            and "error_code=operation_rejected" in message
            for message in messages
        ))

    async def test_confirmed_plan_searches_prowlarr_in_english_only(self):
        command = await self.feature.command({
            "command": "s",
            "args": ["中文输入"],
            "user_id": 1,
            "chat_id": 10,
        })
        self.assertEqual(command["operation"]["stage"], "planning")
        await self.runtime.run("search-plan-")
        callback_data = self.host.reports[-1]["details"]["keyboard"][0][0]["callback_data"]
        plan_id = callback_data.rsplit(":", 1)[-1]
        self.assertEqual(self.search_queries, [])

        confirmed = await self.feature.callback({
            "namespace": "search",
            "payload": f"confirm:{plan_id}",
            "user_id": 1,
            "chat_id": 10,
        })

        self.assertEqual(confirmed["operation"]["stage"], "prowlarr_search")
        await self.runtime.run("search-releases-")

        self.assertEqual(self.search_queries, [("English Title", "movie")])
        self.assertIn(
            "✅ 中文标题 (English Title)",
            self.host.reports[-1]["status_text"],
        )
        self.assertEqual(self.host.reports[-1]["state"], "awaiting_input")

    async def test_prowlarr_failure_keeps_plan_and_offers_retry_exit(self):
        from telepiplex_search.adapters.prowlarr import ProwlarrRequestError

        def fail_search(_query, _media_type):
            raise ProwlarrRequestError(
                "Prowlarr 查询超时（已等待 200 秒）。",
                kind="timeout",
                retryable=True,
            )

        self.feature.release_search = fail_search
        plan_id = await self._prepare_search()
        self.assertIn("keyboard", self.host.reports[-1]["details"])

        started = await self.feature.callback({
            "namespace": "search",
            "payload": f"confirm:{plan_id}",
            "user_id": 1,
            "chat_id": 10,
        })

        self.assertNotIn("keyboard", started["operation"]["details"])
        await self.runtime.run("search-releases-")

        failed = self.host.reports[-1]
        self.assertEqual(failed["state"], "awaiting_input")
        self.assertEqual(failed["stage"], "prowlarr_recovery")
        keyboard = failed["details"]["keyboard"]
        self.assertEqual(keyboard[0][0]["text"], "重试 Prowlarr")
        self.assertEqual(
            keyboard[0][0]["callback_data"],
            f"search:confirm:{plan_id}",
        )
        self.assertEqual(
            [button["text"] for row in keyboard for button in row],
            ["重试 Prowlarr", "退出"],
        )
        self.assertEqual(
            len({
                button["callback_data"]
                for row in keyboard
                for button in row
            }),
            2,
        )
        self.assertIn("已等待 200 秒", failed["status_text"])
        self.assertIn(plan_id, self.feature.plans)

    async def test_wrong_scope_never_enters_release_rank(self):
        from telepiplex_search.series_scope import apply_series_scope

        contract = series_ranked_search_plan()["candidates"][0][
            "media_metadata"
        ]
        contract = apply_series_scope(
            contract,
            "season",
            season_number=1,
        )
        plan_id = "scope-gate"
        stored = {
            "plan": {
                "plan_id": plan_id,
                "media_metadata": contract,
                "prowlarr_queries": ["The Glory S01"],
            },
        }
        self.feature.plans[plan_id] = stored
        self.feature.release_search = lambda *_: [
            {
                "title": "The.Glory.S01E01",
                "magnet_url": "magnet:?xt=urn:btih:" + "a" * 40,
            },
            {
                "title": "The.Glory.S01",
                "magnet_url": "magnet:?xt=urn:btih:" + "b" * 40,
            },
            {
                "title": "The.Glory.S01-S02",
                "magnet_url": "magnet:?xt=urn:btih:" + "c" * 40,
            },
        ]
        ranked_inputs = []
        self.feature.release_rank = (
            lambda items, limit: ranked_inputs.extend(items) or list(items)
        )
        self.feature.indexer_summary = lambda _items: {}

        await self.feature._confirm_and_search(plan_id, stored)

        self.assertEqual(
            [item["title"] for item in ranked_inputs],
            ["The.Glory.S01"],
        )

    async def test_no_exact_scope_reports_counts_without_fallback_buttons(self):
        from telepiplex_search.series_scope import apply_series_scope

        contract = series_ranked_search_plan()["candidates"][0][
            "media_metadata"
        ]
        contract = apply_series_scope(
            contract,
            "season",
            season_number=1,
        )
        plan_id = "scope-zero"
        stored = {
            "plan": {
                "plan_id": plan_id,
                "media_metadata": contract,
                "prowlarr_queries": ["The Glory S01"],
            },
        }
        self.feature.plans[plan_id] = stored
        self.feature.release_search = lambda *_: [{
            "title": "The.Glory.S01E01",
            "magnet_url": "magnet:?xt=urn:btih:" + "a" * 40,
        }]
        self.feature.indexer_summary = lambda _items: {}

        result = await self.feature._confirm_and_search(plan_id, stored)
        action = result["actions"][0]

        self.assertIn("没有同身份、同范围的可用片源", action["text"])
        self.assertNotIn("keyboard", action.get("data") or {})
        self.assertNotIn(plan_id, self.feature.plans)

    async def test_twelve_results_render_four_rows_of_three(self):
        from telepiplex_search.series_scope import apply_series_scope

        contract = series_ranked_search_plan()["candidates"][0][
            "media_metadata"
        ]
        contract = apply_series_scope(
            contract,
            "season",
            season_number=1,
        )
        plan_id = "scope-twelve"
        stored = {
            "plan": {
                "plan_id": plan_id,
                "media_metadata": contract,
                "prowlarr_queries": ["The Glory S01"],
            },
        }
        self.feature.plans[plan_id] = stored
        self.feature.config["search"]["prowlarr"]["result_limit"] = 100
        self.feature.release_search = lambda *_: [{
            "title": f"The.Glory.S01.1080p.Group{index}",
            "magnet_url": (
                "magnet:?xt=urn:btih:"
                f"{index + 1:040x}"
            ),
        } for index in range(20)]
        self.feature.indexer_summary = lambda _items: {}

        result = await self.feature._confirm_and_search(plan_id, stored)
        keyboard = result["actions"][0]["data"]["keyboard"]

        self.assertEqual(
            [len(row) for row in keyboard[:-1]],
            [3, 3, 3, 3],
        )
        self.assertEqual(len(stored["results"]), 12)

    async def test_indexers_stream_independently_and_old_button_keeps_release(self):
        import time

        from telepiplex_search.adapters.prowlarr import ProwlarrRequestError

        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        stored["selected_path"] = "/Movies"
        self.feature.indexer_loader = lambda: [
            {"id": 1, "name": "Fast"},
            {"id": 2, "name": "Slow"},
            {"id": 3, "name": "Broken"},
        ]

        def search_indexer(_query, _media_type, indexer_id):
            if indexer_id == 1:
                return [{
                    "title": "English.Title.2024.1080p.WEB-DL.Fast",
                    "magnet_url": "magnet:?xt=urn:btih:" + "a" * 40,
                    "seeders": 1,
                    "size": 5 * 1024 ** 3,
                    "indexer": "Fast",
                }]
            if indexer_id == 2:
                time.sleep(0.15)
                return [{
                    "title": "English.Title.2024.2160p.REMUX.Slow",
                    "magnet_url": "magnet:?xt=urn:btih:" + "b" * 40,
                    "seeders": 100,
                    "size": 35 * 1024 ** 3,
                    "indexer": "Slow",
                }]
            raise ProwlarrRequestError(
                "FlareSolverr challenge failed",
                kind="server_error",
                http_status=500,
                retryable=True,
            )

        self.feature.indexer_search = search_indexer
        self.feature.release_rank = lambda items, limit: sorted(
            items,
            key=lambda item: int(item.get("seeders") or 0),
            reverse=True,
        )[:limit]

        task = asyncio.create_task(
            self.feature._confirm_and_search(plan_id, stored)
        )
        await asyncio.sleep(0.05)

        partial = [
            report for report in self.host.reports
            if report["stage"] == "prowlarr_search"
            and report["details"].get("allow_running_callbacks") is True
            and report["details"].get("keyboard")
        ]
        self.assertTrue(partial)
        self.assertIn("🔍 中文标题 (English Title)", partial[-1]["status_text"])
        self.assertNotIn("Fast", partial[-1]["status_text"])
        first_callback = partial[-1]["details"]["keyboard"][0][0][
            "callback_data"
        ]
        first_release_id = first_callback.rsplit(":", 1)[-1]

        result = await task

        self.assertEqual(
            [item["indexer"] for item in stored["results"]],
            ["Slow", "Fast"],
        )
        self.assertEqual(
            stored["indexer_summary"]["down_indexers"],
            [{
                "source": "Broken",
                "message": "FlareSolverr challenge failed",
                "kind": "server_error",
                "http_status": 500,
                "retryable": True,
            }],
        )
        self.assertIn(
            first_release_id,
            stored["release_by_id"],
        )
        self.assertIn("✅ 中文标题 (English Title)", result["actions"][0]["text"])
        self.assertNotIn("Slow", result["actions"][0]["text"])

        await self.feature._submit_release(
            plan_id,
            stored,
            first_release_id,
            stored["operation_id"],
        )
        _capability, _method, payload, kwargs = self.host.calls[-1]
        self.assertEqual(
            payload["release"]["title"],
            "English.Title.2024.1080p.WEB-DL.Fast",
        )
        self.assertEqual(
            kwargs["idempotency_key"],
            f"{plan_id}:release:{first_release_id}",
        )

    async def test_selecting_partial_release_freezes_and_cancels_search(self):
        from telepiplex_search.release_identity import stable_release_id

        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        item = {
            "title": "English.Title.2024.1080p.WEB-DL",
            "magnet_url": "magnet:?xt=urn:btih:" + "c" * 40,
            "indexer": "Fast",
        }
        release_id = stable_release_id(item)
        stored["release_by_id"] = {release_id: item}
        search_task = asyncio.create_task(asyncio.Event().wait())
        operation = self.feature.operations[stored["operation_id"]]
        operation["task"] = search_task

        result = self.feature._start_submission_task(
            plan_id,
            stored,
            release_id,
        )
        await asyncio.sleep(0)

        self.assertTrue(stored["selection_frozen"])
        self.assertEqual(stored["selected_release_id"], release_id)
        self.assertTrue(search_task.cancelled())
        self.assertEqual(result["operation"]["stage"], "resolving_release")

    async def test_exiting_partial_results_cancels_background_search(self):
        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        search_task = asyncio.create_task(asyncio.Event().wait())
        operation = self.feature.operations[stored["operation_id"]]
        operation["task"] = search_task

        result = await self.feature.callback({
            "namespace": "search",
            "payload": f"cancel:{plan_id}",
            "user_id": 1,
            "chat_id": 10,
        })
        await asyncio.sleep(0)

        self.assertTrue(search_task.cancelled())
        self.assertEqual(result["operation"]["state"], "cancelled")
        self.assertNotIn(plan_id, self.feature.plans)

    async def test_planning_failure_uses_safe_specific_reason(self):
        from telepiplex_search.planner import SearchPlanningError

        async def blocked(_raw_query, _plan_id):
            raise SearchPlanningError(
                "ai_unavailable_after_gate_failure",
                ["ambiguous_candidates"],
            )

        self.feature.plan_builder = blocked
        result = await self.feature.command({
            "command": "search",
            "args": ["同名条目"],
            "user_id": 1,
            "chat_id": 10,
        })

        self.assertEqual(result["operation"]["state"], "running")
        await self.runtime.run("search-plan-")
        self.assertIn("多个候选", self.host.reports[-1]["status_text"])
        self.assertIn("AI 当前不可用", self.host.reports[-1]["status_text"])
        self.assertEqual(self.host.reports[-1]["state"], "failed")
        self.assertEqual(self.feature.plans, {})
        self.assertEqual(self.search_queries, [])

    async def test_candidate_binding_failure_logs_correlation_without_raw_query(self):
        from telepiplex_search.context import runtime_context
        from telepiplex_search.planner import SearchPlanningError

        async def blocked(_raw_query, _plan_id):
            raise SearchPlanningError(
                "candidate_binding_failed",
                ("fact_bound_multiple_times", "unknown_fact_id"),
            )

        logger = Mock()
        self.feature.plan_builder = blocked
        with patch.object(runtime_context, "logger", logger):
            await self.feature.command({
                "command": "search",
                "args": ["ODDTAXI"],
                "user_id": 1,
                "chat_id": 10,
                "update_id": 88,
            })
            await self.runtime.run("search-plan-")

        warning = " ".join(
            call.args[0]
            for call in logger.warning.call_args_list
        )
        self.assertIn("event=search.planning_failed", warning)
        self.assertRegex(warning, r"search_session_id=[a-f0-9]{10}")
        self.assertRegex(warning, r"operation_id=[a-f0-9]{32}")
        self.assertIn("update_id=88", warning)
        self.assertIn("error_code=candidate_binding_failed", warning)
        self.assertIn("fact_bound_multiple_times", warning)
        self.assertIn("unknown_fact_id", warning)
        self.assertIn("query_chars=7", warning)
        self.assertNotIn("ODDTAXI", warning)

    async def test_recoverable_planning_errors_have_retry_and_single_exit_ui(self):
        from telepiplex_search.planner import SearchPlanningError

        for code in (
            "source_failure",
            "source_rate_limited",
            "source_fact_conflict",
            "ai_candidate_failure",
            "candidate_binding_failed",
            "fixed_link_read_failed",
            "metadata_conflict",
            "metadata_incomplete",
        ):
            with self.subTest(code=code):
                async def blocked(_raw_query, _plan_id, code=code):
                    raise SearchPlanningError(
                        code,
                        ("wikipedia:server_down",),
                    )

                self.feature.plan_builder = blocked
                result = await self.feature.command({
                    "command": "search",
                    "args": ["同名条目"],
                    "user_id": 1,
                    "chat_id": 10,
                })
                await self.runtime.run("search-plan-")
                report = self.host.reports[-1]
                keyboard = report["details"]["keyboard"]
                self.assertEqual(report["state"], "awaiting_input")
                self.assertNotIn("取消或退出", report["status_text"])
                plan_id = keyboard[0][0]["callback_data"].rsplit(":", 1)[-1]
                self.assertEqual(keyboard, [[{
                    "text": "重试",
                    "callback_data": f"search:retry:{plan_id}",
                }], [{
                    "text": "退出",
                    "callback_data": f"search:cancel:{plan_id}",
                }]])
                for stored_plan_id in list(self.feature.plans):
                    self.feature._release_plan(stored_plan_id)

    async def test_source_fact_conflict_ui_is_human_readable_and_keeps_retry(self):
        from telepiplex_search.planner import SearchPlanningError

        async def blocked(_raw_query, _plan_id):
            raise SearchPlanningError(
                "source_fact_conflict",
                ("wikipedia:Q-conflict", "field:year"),
            )

        self.feature.plan_builder = blocked
        await self.feature.command({
            "command": "search",
            "args": ["冲突作品"],
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")

        report = self.host.reports[-1]
        self.assertEqual(report["state"], "awaiting_input")
        self.assertIn("来源事实存在冲突", report["status_text"])
        self.assertNotIn("source_fact_conflict", report["status_text"])
        self.assertNotIn("wikipedia:Q-conflict", report["status_text"])
        self.assertNotIn("field:year", report["status_text"])
        self.assertEqual(
            [
                button["text"]
                for row in report["details"]["keyboard"]
                for button in row
            ],
            ["重试", "退出"],
        )

    @patch("telepiplex_search.service.lookup_wikipedia_evidence")
    def test_wikipedia_provider_caps_each_targeted_query_batch(
        self, lookup
    ):
        lookup.return_value = {
            "source": "wikipedia",
            "status": "not_found",
            "facts": [],
            "source_urls": [],
            "error": "",
        }
        self.feature.config["metadata"] = {
            "wikipedia": {
                "enable": True,
                "languages": ["zh", "en"],
                "max_queries": 2,
            },
        }

        self.feature._wikipedia_provider({
            "source_queries": {
                "wikipedia": ["one", "two", "three"],
            },
        })

        self.assertEqual(
            lookup.call_args.args[0],
            ["one", "two"],
        )

    async def test_config_wizard_refuses_to_replace_active_search_session(self):
        owner = (10, 1)
        self.feature.awaiting_queries.add(owner)

        result = await self.feature.command({
            "command": "search_config",
            "args": [],
            "user_id": owner[1],
            "chat_id": owner[0],
        })

        self.assertIn("先完成或退出", result["actions"][0]["text"])
        self.assertIn(owner, self.feature.awaiting_queries)
        self.assertFalse(self.feature.config_wizard.has_session({
            "chat_id": owner[0], "user_id": owner[1],
        }))

    async def test_series_query_keeps_confirmed_episode_scope(self):
        plan = search_plan()
        contract = plan["media_metadata"]
        contract["identity"]["content_kind"] = "series"
        contract["placement"].update({
            "library_type": "series",
            "category_kind": "live_action_series",
            "mapping_kind": "tvdb_official",
            "season_number": 9,
            "episode_number": 7,
        })
        contract["items"] = [{"season_number": 9, "episode_number": 7}]
        contract["retrieval"] = {
            "media_type": "series",
            "scope": "episode",
            "query": "English Title S09E07",
        }
        plan["prowlarr_queries"] = ["中文标题 第九季第七集", "English Title S09E07"]

        self.assertEqual(
            self.feature._english_prowlarr_query(plan, contract),
            "English Title S09E07",
        )

    async def test_series_query_never_reuses_mixed_chinese_ai_query(self):
        plan = search_plan()
        contract = plan["media_metadata"]
        contract["placement"].update({
            "library_type": "series", "category_kind": "live_action_series",
            "season_number": 1, "episode_number": 2,
        })
        contract["items"] = [{"season_number": 1, "episode_number": 2}]
        contract["retrieval"] = {
            "media_type": "series",
            "scope": "episode",
            "query": "English Title S01E02",
        }
        plan["prowlarr_queries"] = ["中文 English Title S01E02"]

        self.assertEqual(
            self.feature._english_prowlarr_query(plan, contract),
            "English Title S01E02",
        )

    async def test_rule_series_queries_preserve_requested_scope(self):
        cases = {
            "whole_series": "English Title",
            "season": "English Title S02",
            "episode": "English Title S02E05",
        }
        for scope, expected in cases.items():
            with self.subTest(scope=scope):
                plan = search_plan()
                contract = plan["media_metadata"]
                contract["identity"]["content_kind"] = "series"
                contract["placement"].update({
                    "library_type": "series",
                    "category_kind": "live_action_series",
                })
                contract["evidence"] = {"decision": {"scope": scope}}
                contract["items"] = [{
                    "season_number": 2,
                    "episode_number": 5,
                }]
                contract["retrieval"] = {
                    "media_type": "series",
                    "scope": scope,
                    "query": expected,
                }

                self.assertEqual(
                    self.feature._english_prowlarr_query(plan, contract),
                    expected,
                )

    async def test_one_season_whole_series_uses_three_bounded_queries(self):
        plan = search_plan()
        contract = plan["media_metadata"]
        contract["identity"].update({
            "content_kind": "series",
            "english_title": "Someday or One Day",
        })
        contract["placement"].update({
            "library_type": "series",
            "category_kind": "live_action_series",
        })
        contract["evidence"] = {
            "decision": {"mode": "ai", "scope": "movie_or_series"}
        }
        contract["items"] = [{"season_number": 1, "episode_number": 1}]
        contract["retrieval"] = {
            "media_type": "series",
            "scope": "whole_series",
            "query": "The Glory 2022",
        }
        plan["prowlarr_queries"] = ["The Glory 2022"]

        self.assertEqual(
            self.feature._english_prowlarr_queries(plan, contract),
            [
                "Someday or One Day S01",
                "Someday or One Day Season 01",
                "Someday or One Day Complete",
            ],
        )
        self.assertEqual(
            self.feature._english_prowlarr_query(plan, contract),
            "Someday or One Day S01",
        )

    async def test_season_search_uses_only_s_and_season_variants(self):
        plan = search_plan()
        contract = plan["media_metadata"]
        contract["identity"].update({
            "content_kind": "series",
            "english_title": "English Title",
        })
        contract["placement"].update({
            "library_type": "series",
            "category_kind": "live_action_series",
            "season_number": 2,
        })
        contract["evidence"] = {
            "decision": {"scope": "season", "season_number": 2}
        }
        contract["items"] = [
            {"season_number": 2, "episode_number": 1},
        ]
        contract["retrieval"] = {
            "media_type": "series",
            "scope": "season",
            "query": "English Title S02",
        }

        self.assertEqual(
            self.feature._english_prowlarr_queries(plan, contract),
            [
                "English Title S02",
                "English Title Season 02",
            ],
        )

    async def test_episode_search_does_not_expand_query(self):
        plan = search_plan()
        contract = plan["media_metadata"]
        contract["identity"].update({
            "content_kind": "series",
            "english_title": "English Title",
        })
        contract["placement"].update({
            "library_type": "series",
            "category_kind": "live_action_series",
            "season_number": 2,
            "episode_number": 5,
        })
        contract["evidence"] = {"decision": {
            "scope": "episode",
            "season_number": 2,
            "episode_number": 5,
        }}
        contract["items"] = [
            {"season_number": 2, "episode_number": 5},
        ]
        contract["retrieval"] = {
            "media_type": "series",
            "scope": "episode",
            "query": "English Title S02E05",
        }

        self.assertEqual(
            self.feature._english_prowlarr_queries(plan, contract),
            ["English Title S02E05"],
        )

    async def test_multi_season_whole_series_uses_range_and_complete_queries(self):
        plan = search_plan()
        contract = plan["media_metadata"]
        contract["identity"].update({
            "content_kind": "series",
            "english_title": "English Title",
        })
        contract["placement"].update({
            "library_type": "series",
            "category_kind": "live_action_series",
        })
        contract["evidence"] = {
            "decision": {"scope": "whole_series"}
        }
        contract["items"] = [
            {"season_number": season, "episode_number": 1}
            for season in (1, 2, 3)
        ]
        contract["retrieval"] = {
            "media_type": "series",
            "scope": "whole_series",
            "query": "English Title",
        }

        self.assertEqual(
            self.feature._english_prowlarr_queries(plan, contract),
            [
                "English Title S01-S03",
                "English Title Complete",
            ],
        )

    async def test_whole_series_searches_every_indexer_query_pair(self):
        import threading
        import time

        from telepiplex_search.adapters.prowlarr import ProwlarrRequestError
        from telepiplex_search.series_scope import apply_series_scope

        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        contract = apply_series_scope(
            series_ranked_search_plan()["candidates"][0]["media_metadata"],
            "whole_series",
        )
        stored["plan"] = {
            "plan_id": plan_id,
            "media_metadata": contract,
            "prowlarr_queries": ["The Glory"],
        }
        stored["selected_path"] = "/Series"
        self.feature.indexer_loader = lambda: [
            {"id": 1, "name": "Partial"},
            {"id": 2, "name": "Broken"},
        ]
        calls = []
        lock = threading.Lock()
        active = 0
        max_active = 0

        def search_indexer(query, media_type, indexer_id):
            nonlocal active, max_active
            with lock:
                calls.append((indexer_id, query, media_type))
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.03)
                if indexer_id == 2 or query.endswith(" S01"):
                    raise ProwlarrRequestError(
                        f"{query} failed",
                        kind="server_error",
                        http_status=500,
                        retryable=True,
                    )
                return [{
                    "title": "The.Glory.S01.1080p.WEB-DL",
                    "magnet_url": "magnet:?xt=urn:btih:" + "d" * 40,
                    "indexer": "Partial",
                }]
            finally:
                with lock:
                    active -= 1

        self.feature.indexer_search = search_indexer

        result = await self.feature._confirm_and_search(plan_id, stored)

        expected_queries = {
            "The Glory S01",
            "The Glory Season 01",
            "The Glory Complete",
        }
        self.assertEqual(
            set(calls),
            {
                (indexer_id, query, "series")
                for indexer_id in (1, 2)
                for query in expected_queries
            },
        )
        self.assertGreaterEqual(max_active, 2)
        self.assertEqual(len(stored["results"]), 1)
        self.assertEqual(
            stored["indexer_summary"]["down_indexers"],
            [{
                "source": "Broken",
                "message": (
                    "all query variants failed: "
                    "The Glory S01 failed; "
                    "The Glory Season 01 failed; "
                    "The Glory Complete failed"
                ),
                "kind": "server_error",
                "http_status": 500,
                "retryable": True,
            }],
        )
        self.assertNotIn("Partial", str(
            stored["indexer_summary"]["down_indexers"]
        ))
        self.assertIn("✅ 黑暗荣耀 (The Glory)", result["actions"][0]["text"])

    async def test_aggregate_whole_series_keeps_successful_variants(self):
        from telepiplex_search.adapters.prowlarr import ProwlarrRequestError
        from telepiplex_search.series_scope import apply_series_scope

        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        contract = apply_series_scope(
            series_ranked_search_plan()["candidates"][0]["media_metadata"],
            "whole_series",
        )
        stored["plan"] = {
            "plan_id": plan_id,
            "media_metadata": contract,
            "prowlarr_queries": ["The Glory"],
        }
        self.feature.indexer_loader = lambda: []
        calls = []

        def search(query, media_type):
            calls.append((query, media_type))
            if query.endswith(" S01"):
                raise ProwlarrRequestError(
                    "S01 failed",
                    kind="server_error",
                    http_status=500,
                    retryable=True,
                )
            return [{
                "title": "The.Glory.S01.1080p.WEB-DL",
                "magnet_url": "magnet:?xt=urn:btih:" + "e" * 40,
                "indexer": "Aggregate",
            }]

        self.feature.release_search = search
        self.feature.indexer_summary = lambda _items: {}

        result = await self.feature._confirm_and_search(plan_id, stored)

        self.assertEqual(calls, [
            ("The Glory S01", "series"),
            ("The Glory Season 01", "series"),
            ("The Glory Complete", "series"),
        ])
        self.assertEqual(len(stored["results"]), 1)
        self.assertIn("✅ 黑暗荣耀 (The Glory)", result["actions"][0]["text"])

    async def test_selected_release_calls_download_provider_with_canonical_contract(self):
        plan_id = await self._prepare_search()
        await self.feature.callback({
            "namespace": "search", "payload": f"confirm:{plan_id}",
            "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("search-releases-")
        release_callback = self.host.reports[-1]["details"]["keyboard"][0][0][
            "callback_data"
        ]
        release_id = release_callback.rsplit(":", 1)[-1]
        result = await self.feature.callback({
            "namespace": "search",
            "payload": release_callback.removeprefix("search:"),
            "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(result["operation"]["stage"], "resolving_release")
        await self.runtime.run("search-submit-")

        capability, method, payload, kwargs = self.host.calls[0]
        self.assertEqual((capability, method), ("download.provider", "submit"))
        self.assertEqual(payload["selected_path"], "/Movies")
        self.assertEqual(payload["media_metadata"]["schema_version"], 1)
        self.assertTrue(payload["media_metadata"]["confirmed"])
        self.assertEqual(
            payload["media_metadata"]["identity"]["chinese_title"],
            "中文标题",
        )
        self.assertEqual(
            payload["naming_metadata"],
            {
                "source": "confirmed",
                "media_type": "movie",
                "chinese_title": "中文标题",
                "english_title": "English Title",
                "year": "2024",
            },
        )
        self.assertEqual(
            payload["release"],
            {
                "title": "English.Title.2024.1080p.WEB-DL",
                "indexer": "test",
                "size": 100,
            },
        )
        self.assertEqual(payload["operation_id"], self.host.reports[-1]["operation_id"])
        self.assertEqual(payload["operation_revision"], self.host.reports[-1]["revision"])
        self.assertEqual(self.host.reports[-1]["state"], "handed_off")
        self.assertEqual(
            kwargs["idempotency_key"],
            f"{plan_id}:release:{release_id}",
        )

    async def test_unresolvable_release_is_removed_and_other_results_remain(self):
        from telepiplex_search.release_identity import stable_release_id

        self.feature.release_search = lambda *_: [{
            "title": "English.Title.2024.2160p.WEB-DL-First",
            "download_url": "https://indexer.example/first.torrent",
            "seeders": 20,
            "size": 20 * 1024 ** 3,
            "indexer": "first",
        }, {
            "title": "English.Title.2024.1080p.WEB-DL-Second",
            "magnet_url": "magnet:?xt=urn:btih:" + "b" * 40,
            "seeders": 10,
            "size": 10 * 1024 ** 3,
            "indexer": "second",
        }]
        plan_id = await self._prepare_search()
        await self.feature.callback({
            "namespace": "search",
            "payload": f"confirm:{plan_id}",
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-releases-")
        stored = self.feature.plans[plan_id]
        failed_id = stable_release_id(stored["results"][0])
        remaining_id = stable_release_id(stored["results"][1])

        def resolve_except_first(item):
            if stable_release_id(item) == failed_id:
                raise RuntimeError("indexer rejected torrent download")
            return item["magnet_url"]

        self.feature.release_resolver = resolve_except_first
        await self.feature.callback({
            "namespace": "search",
            "payload": f"release:{plan_id}:{failed_id}",
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-submit-")

        current = self.feature.operations[stored["operation_id"]]
        self.assertNotIn(failed_id, stored["release_by_id"])
        self.assertEqual(
            [stable_release_id(item) for item in stored["results"]],
            [remaining_id],
        )
        self.assertFalse(stored["selection_frozen"])
        self.assertNotIn("selected_release_id", stored)
        self.assertEqual(current["state"], "awaiting_input")
        self.assertEqual(current["stage"], "release_selection")
        self.assertIn("已从结果中移除", current["status_text"])
        buttons = [
            button
            for row in current["details"]["keyboard"]
            for button in row
        ]
        self.assertEqual(
            [button["text"] for button in buttons],
            ["①", "退出"],
        )
        self.assertEqual(
            buttons[0]["callback_data"],
            f"search:release:{plan_id}:{remaining_id}",
        )
        self.assertNotIn(
            failed_id,
            [button["callback_data"] for button in buttons],
        )

        await self.feature.callback({
            "namespace": "search",
            "payload": f"release:{plan_id}:{remaining_id}",
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-submit-")

        self.assertEqual(len(self.host.calls), 1)
        self.assertEqual(
            self.host.calls[0][2]["release"]["title"],
            "English.Title.2024.1080p.WEB-DL-Second",
        )
        self.assertEqual(
            self.feature.operations[stored["operation_id"]]["state"],
            "handed_off",
        )

    async def test_last_unresolvable_release_leaves_exit_only(self):
        from telepiplex_search.release_identity import stable_release_id

        plan_id = await self._prepare_search()
        await self.feature.callback({
            "namespace": "search",
            "payload": f"confirm:{plan_id}",
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-releases-")
        stored = self.feature.plans[plan_id]
        release_id = stable_release_id(stored["results"][0])
        self.feature.release_resolver = lambda _item: ""

        await self.feature.callback({
            "namespace": "search",
            "payload": f"release:{plan_id}:{release_id}",
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-submit-")

        current = self.feature.operations[stored["operation_id"]]
        self.assertNotIn(release_id, stored["release_by_id"])
        self.assertEqual(stored["results"], [])
        self.assertFalse(stored["selection_frozen"])
        self.assertNotIn("selected_release_id", stored)
        self.assertEqual(current["state"], "awaiting_input")
        self.assertEqual(current["stage"], "release_selection")
        self.assertIn(
            "当前搜索结果均无法取得下载内容",
            current["status_text"],
        )
        self.assertEqual(
            current["details"]["keyboard"],
            [[{
                "text": "退出",
                "callback_data": f"search:cancel:{plan_id}",
            }]],
        )

    async def test_rejected_handoff_never_calls_download_provider(self):
        original_report = self.host.report_operation

        async def reject_handoff(operation):
            if operation["state"] == "handed_off":
                return {
                    "accepted": False,
                    "state": "cancelled",
                    "revision": operation["revision"] + 1,
                }
            return await original_report(operation)

        self.host.report_operation = reject_handoff
        plan_id = await self._prepare_search()
        await self.feature.callback({
            "namespace": "search", "payload": f"confirm:{plan_id}",
            "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("search-releases-")
        release_callback = self.host.reports[-1]["details"]["keyboard"][0][0][
            "callback_data"
        ]
        submission = await self.feature.callback({
            "namespace": "search",
            "payload": release_callback.removeprefix("search:"),
            "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("search-submit-")

        self.assertEqual(self.host.calls, [])
        self.assertEqual(
            self.feature.operations[
                submission["operation"]["operation_id"]
            ]["state"],
            "failed",
        )

    async def test_missing_download_feature_ends_with_actionable_terminal_status(self):
        original_report = self.host.report_operation

        async def reject_missing_target(operation):
            if operation["state"] == "handed_off":
                return {
                    "accepted": False,
                    "state": "running",
                    "revision": operation["revision"] - 1,
                    "error_code": "handoff_target_unavailable",
                    "target_plugin_id": "download",
                }
            return await original_report(operation)

        self.host.report_operation = reject_missing_target
        plan_id = await self._prepare_search()
        await self.feature.callback({
            "namespace": "search", "payload": f"confirm:{plan_id}",
            "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("search-releases-")
        release_callback = self.host.reports[-1]["details"]["keyboard"][0][0][
            "callback_data"
        ]
        submission = await self.feature.callback({
            "namespace": "search",
            "payload": release_callback.removeprefix("search:"),
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-submit-")

        current = self.feature.operations[
            submission["operation"]["operation_id"]
        ]
        self.assertEqual(current["state"], "failed")
        self.assertEqual(
            current["status_text"],
            "115 下载未安装，无法提交片源。",
        )
        self.assertEqual(current["control"], "")
        self.assertEqual(self.host.calls, [])

    async def test_lost_handoff_response_reuses_exact_revision(self):
        plan_id = await self._prepare_search()
        await self.feature.callback({
            "namespace": "search", "payload": f"confirm:{plan_id}",
            "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("search-releases-")
        stored = self.feature.plans[plan_id]
        operation_id = stored["operation_id"]
        release_id = next(iter(stored["release_by_id"]))
        original_report = self.host.report_operation
        handoff_revisions = []
        lost = False

        async def accept_then_lose(operation):
            nonlocal lost
            response = await original_report(operation)
            if operation["state"] == "handed_off":
                handoff_revisions.append(operation["revision"])
                if not lost:
                    lost = True
                    raise RuntimeError("handoff response lost")
            return response

        self.host.report_operation = accept_then_lose

        with self.assertRaises(RuntimeError):
            await self.feature._submit_release(
                plan_id, stored, release_id, operation_id
            )
        result = await self.feature._submit_release(
            plan_id, stored, release_id, operation_id
        )

        self.assertEqual(handoff_revisions, [
            handoff_revisions[0], handoff_revisions[0]
        ])
        self.assertEqual(len(self.host.calls), 1)
        self.assertIn("已提交", result["actions"][0]["text"])

    async def test_empty_query_has_explicit_exit_and_awaiting_operation(self):
        result = await self.feature.command({
            "command": "search", "args": [], "user_id": 1, "chat_id": 10,
        })

        self.assertEqual(result["operation"]["state"], "awaiting_input")
        keyboard = result["actions"][0]["data"]["keyboard"]
        self.assertEqual(keyboard[-1][0]["text"], "退出")

    async def test_s_command_rejects_link_argument_without_starting_plan(self):
        result = await self.feature.command({
            "command": "s",
            "args": ["https://movie.douban.com/subject/1/"],
            "user_id": 1,
            "chat_id": 10,
        })

        self.assertIn("/s 只接受片名", result["actions"][0]["text"])
        self.assertEqual(self.runtime.tasks, {})

    async def test_direct_shared_link_starts_search_without_command_session(self):
        received = []

        async def planner(raw_query, plan_id):
            received.append(raw_query)
            result = search_plan()
            result["plan_id"] = plan_id
            result["media_metadata"]["metadata_id"] = plan_id
            return result

        self.feature.plan_builder = planner
        result = await self.feature.message({
            "text": (
                "分享《繁花》 "
                "https://m.douban.com/movie/subject/36490422/"
            ),
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")

        self.assertEqual(result["operation"]["stage"], "planning")
        self.assertEqual(
            received,
            [
                "分享《繁花》 "
                "https://m.douban.com/movie/subject/36490422/"
            ],
        )

    async def test_all_wrong_rejects_without_replanning(self):
        calls = []

        async def planner(raw_query, plan_id):
            calls.append(raw_query)
            result = ranked_search_plan()
            result.update({
                "plan_id": plan_id,
                "raw_query": raw_query,
                "links_frozen": True,
            })
            result["candidates"] = result["candidates"][:1]
            return result

        self.feature.plan_builder = planner
        await self.feature.command({
            "command": "s",
            "args": ["唯一候选"],
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")
        plan_id = next(iter(self.feature.plans))

        result = await self.feature.callback({
            "payload": f"reject:{plan_id}",
            "user_id": 1,
            "chat_id": 10,
        })

        self.assertEqual(calls, ["唯一候选"])
        self.assertNotIn(plan_id, self.feature.plans)
        self.assertIn("没有继续查询其他来源", result["actions"][0]["text"])

    async def test_running_planner_can_be_cancelled_and_releases_plan(self):
        entered = asyncio.Event()

        async def blocked(_raw_query, _plan_id):
            entered.set()
            await asyncio.Event().wait()

        class TaskRuntime:
            def spawn(self, awaitable, *, task_id):
                return asyncio.create_task(awaitable, name=task_id)

        self.feature.plan_builder = blocked
        self.feature.bind_runtime(TaskRuntime())
        result = await self.feature.command({
            "command": "search", "args": ["等待"], "user_id": 1, "chat_id": 10,
        })
        await entered.wait()
        operation_id = result["operation"]["operation_id"]
        task = self.feature.operations[operation_id]["task"]

        cancelling = await self.feature.operation_control({
            "operation_id": operation_id,
            "revision": result["operation"]["revision"],
            "action": "cancel",
        })
        await task

        self.assertEqual(cancelling["operation"]["state"], "cancelling")
        self.assertEqual(self.feature.operations[operation_id]["state"], "cancelled")
        self.assertEqual(self.feature.plans, {})
        self.assertEqual(self.host.calls, [])

    async def test_operation_snapshot_only_returns_active_operations(self):
        result = await self.feature.command({
            "command": "search", "args": [], "user_id": 1, "chat_id": 10,
        })
        operation_id = result["operation"]["operation_id"]

        active = await self.feature.operation_snapshot({})
        self.assertEqual([item["operation_id"] for item in active["operations"]], [operation_id])
        await self.feature.operation_control({
            "operation_id": operation_id, "action": "exit",
        })
        self.assertEqual((await self.feature.operation_snapshot({}))["operations"], [])

    async def test_source_can_cancel_during_provisional_handoff(self):
        result = await self.feature.command({
            "command": "search", "args": [], "user_id": 1, "chat_id": 10,
        })
        operation_id = result["operation"]["operation_id"]
        self.feature._advance_operation(
            operation_id,
            state="handed_off",
            stage="submitting_download",
            status_text="正在交给 115。",
            control="cancel",
            next_plugin_id="download",
        )

        cancelled = await self.feature.operation_control({
            "operation_id": operation_id, "action": "cancel",
        })

        self.assertEqual(cancelled["operation"]["state"], "cancelled")

    async def test_ranked_plan_renders_top_candidate_poster(self):
        async def planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            return result

        self.feature.plan_builder = planner
        await self.feature.command({
            "command": "s", "args": ["候选"], "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("search-plan-")

        report = self.host.reports[-1]
        self.assertEqual(report["details"]["photo_url"], "https://image.example/top.jpg")
        self.assertIn("92/100", report["status_text"])

    async def test_unified_candidates_render_clean_numbered_photo_grid(self):
        async def planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            result["raw_query"] = "候选"
            result["links_frozen"] = True
            for index, candidate in enumerate(result["candidates"], 1):
                candidate.update({
                    "candidate_id": candidate["candidate_key"],
                    "identity_role": "movie",
                    "intended_scope": "movie",
                    "ai_confidence": 0.9 - index / 100,
                    "ai_reason": f"候选 {index} 与来源事实匹配。",
                    "source_links": [{
                        "provider": "douban",
                        "url": (
                            f"https://movie.douban.com/subject/{index}/"
                        ),
                    }],
                    "poster_assets": [],
                    "links_frozen": True,
                })
            return result

        self.feature.plan_builder = planner
        await self.feature.command({
            "command": "s",
            "args": ["候选"],
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")

        report = self.host.reports[-1]
        self.assertEqual(report["stage"], "candidate_selection")
        self.assertEqual(len(report["details"]["poster_items"]), 2)
        self.assertEqual(
            [
                row[0]["callback_data"]
                for row in report["details"]["keyboard"][:2]
            ],
            [
                f"search:select:{next(iter(self.feature.plans))}:0",
                f"search:select:{next(iter(self.feature.plans))}:1",
            ],
        )
        self.assertIn("<b>中文标题1 2024</b>", report["status_text"])
        self.assertNotIn("English Title", report["status_text"])
        self.assertIn("类型：电影", report["status_text"])
        self.assertIn("来源：豆瓣", report["status_text"])
        for internal in (
            "匹配参考",
            "评分",
            "理由",
            "来源待补充",
            "https://",
        ):
            self.assertNotIn(internal, report["status_text"])

    def test_candidate_grid_uses_chinese_original_year_in_body_and_button(self):
        plan = ranked_search_plan()
        first, second = plan["candidates"]
        first["media_metadata"]["identity"].update({
            "chinese_title": "想见你",
            "original_title": "想見你",
            "english_title": "Someday or One Day",
            "official_english_title": "Someday or One Day",
            "year": "2019",
        })
        second["media_metadata"]["identity"].update({
            "chinese_title": "让子弹飞",
            "original_title": "让子弹飞",
            "english_title": "Let the Bullets Fly",
            "official_english_title": "Let the Bullets Fly",
            "year": "2010",
        })

        action = self.feature._candidate_grid_action({
            "candidates": plan["candidates"],
            "plan": {"plan_id": "douban-labels"},
        })

        visible = html.unescape(re.sub(r"<[^>]+>", "", action["text"]))
        self.assertIn("1. 想见你 (想見你) 2019", visible)
        self.assertIn("2. 让子弹飞 2010", visible)
        self.assertNotIn("Someday or One Day", visible)
        self.assertNotIn("Let the Bullets Fly", visible)
        self.assertEqual(
            [
                row[0]["text"]
                for row in action["data"]["keyboard"][:2]
            ],
            [
                "1. 想见你 (想見你) 2019",
                "2. 让子弹飞 2010",
            ],
        )
        self.assertEqual(
            action["data"]["poster_items"][0]["title"],
            "想见你",
        )

    def test_candidate_grid_omits_missing_optional_original_title(self):
        plan = ranked_search_plan()
        candidate = plan["candidates"][0]
        candidate["media_metadata"]["identity"].update({
            "chinese_title": "让子弹飞",
            "original_title": "",
            "year": "2010",
        })

        action = self.feature._candidate_grid_action({
            "candidates": [candidate],
            "plan": {"plan_id": "missing-original"},
        })

        visible = html.unescape(re.sub(r"<[^>]+>", "", action["text"]))
        self.assertIn("1. 让子弹飞 2010", visible)
        self.assertNotIn("让子弹飞 (", visible)
        self.assertEqual(
            action["data"]["keyboard"][0][0]["text"],
            "1. 让子弹飞 2010",
        )

    def test_candidate_grid_omits_matching_original_before_title_clipping(self):
        plan = ranked_search_plan()
        candidate = plan["candidates"][0]
        long_title = "一部名称非常长但原标题仍然完全相同的中文电影" * 2
        candidate["media_metadata"]["identity"].update({
            "chinese_title": long_title,
            "original_title": long_title,
            "year": "2026",
        })

        action = self.feature._candidate_grid_action({
            "candidates": [candidate],
            "plan": {"plan_id": "matching-long-original"},
        })

        visible = html.unescape(re.sub(r"<[^>]+>", "", action["text"]))
        title_line = visible.splitlines()[1]
        self.assertNotIn("(", title_line)
        self.assertTrue(title_line.endswith(" 2026"))
        button = action["data"]["keyboard"][0][0]["text"]
        self.assertNotIn("(", button)
        self.assertTrue(button.endswith(" 2026"))

    def test_candidate_grid_is_bounded_to_five_and_hides_internal_fields(self):
        candidates = []
        for index in range(6):
            contract = deepcopy(search_plan()["media_metadata"])
            contract["identity"].update({
                "chinese_title": f"候选标题 {index + 1}",
                "year": str(2000 + index),
            })
            candidates.append({
                "candidate_id": f"candidate-{index + 1}",
                "candidate_key": f"candidate-{index + 1}",
                "identity_role": "movie",
                "candidate_version": "v0",
                "media_metadata": contract,
                "ai_confidence": 0.91,
                "ai_reason": (
                    "候选与用户意图相符，但仍有来源暂时不可用，"
                    "保留现有证据供用户选择。"
                ),
                "source_links": [{
                    "provider": provider,
                    "url": (
                        f"https://example.com/{provider}/{index}"
                    ),
                } for provider in ("wikipedia", "douban", "tvdb")],
                "unresolved_sources": ["wikipedia:server_down"],
                "poster_url": "https://image.example/poster.jpg",
            })

        action = self.feature._candidate_grid_action({
            "candidates": candidates,
            "plan": {"plan_id": "caption-limit"},
        })

        self.assertEqual(action["kind"], "send_photo_grid")
        visible = html.unescape(
            re.sub(r"<[^>]+>", "", action["text"])
        )
        self.assertLessEqual(len(visible), 1024)
        self.assertEqual(len(action["data"]["poster_items"]), 5)
        self.assertEqual(action["text"].count("来源：豆瓣"), 5)
        self.assertNotIn("<a ", action["text"])
        self.assertNotIn("维基百科暂时不可用", action["text"])
        self.assertNotIn("wikipedia:server_down", action["text"])
        self.assertNotIn("匹配参考", action["text"])
        self.assertEqual(
            action["data"]["keyboard"][-1][0]["text"],
            "都不是",
        )

    def test_single_candidate_grid_returns_poster_and_source_overview(self):
        plan = ranked_search_plan()
        candidate = plan["candidates"][0]
        candidate["media_metadata"]["identity"]["summary"] = (
            "一名年轻电影制作人进入诡异的后室，并试图找到出口。"
        )
        candidate["media_metadata"]["identity"]["countries"] = ["美国"]

        action = self.feature._candidate_grid_action({
            "candidates": [candidate],
            "plan": {"plan_id": "backrooms"},
        })

        self.assertEqual(action["kind"], "send_photo_grid")
        self.assertEqual(
            action["data"]["poster_items"][0]["poster_url"],
            "https://image.example/top.jpg",
        )
        self.assertIn(
            "总览：一名年轻电影制作人进入诡异的后室，并试图找到出口。",
            action["text"],
        )
        self.assertIn("国家/地区：美国", action["text"])

    def test_candidate_detail_uses_human_media_and_relation_labels(self):
        plan = series_ranked_search_plan()
        candidate = plan["candidates"][0]
        candidate.update({
            "candidate_id": "series-candidate",
            "identity_role": "series_root",
            "candidate_version": "v0",
        })
        candidate["media_metadata"]["identity"].update({
            "chinese_title": "想见你",
            "original_title": "想見你",
            "english_title": "Someday or One Day",
            "official_english_title": "Someday or One Day",
            "year": "2019",
        })

        action = self.feature._candidate_action(
            {
                "candidates": (candidate,),
                "plan": {"plan_id": "human-detail"},
            },
            0,
            edit=False,
        )

        self.assertIn("类型：剧集", action["text"])
        self.assertIn("关系：独立作品", action["text"])
        self.assertEqual(
            action["text"].splitlines()[1],
            "想见你 (想見你) 2019",
        )
        self.assertNotIn("Someday or One Day", action["text"])
        self.assertNotIn("standalone", action["text"])
        self.assertNotIn("series_root", action["text"])
        self.assertEqual(
            action["data"]["keyboard"][0][0]["text"],
            "选择并验证",
        )
        self.assertEqual(set(action["data"]), {"keyboard", "photo_url"})
        self.assertNotIn("candidate_key", action["data"])

    def test_frozen_candidate_action_only_emits_host_render_fields(self):
        plan = ranked_search_plan()
        candidate = plan["candidates"][0]
        candidate.update({
            "links_frozen": True,
            "source_links": [{
                "provider": "douban",
                "url": "https://movie.douban.com/subject/36235977/",
            }],
        })
        candidate["media_metadata"]["identity"]["countries"] = ["美国"]
        candidate["media_metadata"]["identity"].update({
            "chinese_title": "想见你",
            "original_title": "想見你",
            "english_title": "Someday or One Day",
            "official_english_title": "Someday or One Day",
            "year": "2019",
        })

        action = self.feature._candidate_action(
            {
                "candidates": (candidate,),
                "plan": {
                    "plan_id": "frozen-contract",
                    "links_frozen": True,
                },
            },
            0,
            edit=False,
        )

        self.assertEqual(set(action["data"]), {"keyboard", "photo_url"})
        self.assertEqual(
            action["text"].splitlines()[0],
            "想见你 (想見你) 2019",
        )
        self.assertNotIn("Someday or One Day", action["text"])
        self.assertIn("国家/地区：美国", action["text"])
        self.assertNotIn("candidate_key", action["data"])

    async def test_unified_single_candidate_requires_user_confirmation(self):
        async def planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            result["raw_query"] = "唯一候选"
            result["links_frozen"] = True
            result["candidates"] = result["candidates"][:1]
            return result

        self.feature.plan_builder = planner
        await self.feature.command({
            "command": "s",
            "args": ["唯一候选"],
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")

        report = self.host.reports[-1]
        self.assertEqual(report["stage"], "candidate_selection")
        self.assertEqual(report["state"], "awaiting_input")
        self.assertEqual(
            [
                button["text"]
                for row in report["details"]["keyboard"]
                for button in row
            ],
            ["就是它", "都不是"],
        )

    @patch("telepiplex_search.service.hydrate_frozen_candidate")
    async def test_tvdb_unavailable_series_continues_as_whole_series(
        self,
        hydrate,
    ):
        plan_id = "degraded-whole-series"
        plan = series_ranked_search_plan()
        plan.update({
            "plan_id": plan_id,
            "raw_query": "黑暗荣耀 第一季",
            "links_frozen": True,
            "auto_confirm": False,
        })
        candidate = plan["candidates"][0]
        candidate["links_frozen"] = True
        contract = candidate["media_metadata"]
        contract["items"] = []
        contract["retrieval"] = {
            "media_type": "series",
            "scope": "whole_series",
            "query": "The Glory",
        }
        contract["warnings"] = [
            "warning:tvdb_inventory_unavailable",
        ]
        hydrate.return_value = deepcopy(candidate)
        operation = self.feature._new_operation(
            {"chat_id": 10, "user_id": 1},
            state="awaiting_input",
            stage="candidate_selection",
            status_text="等待选择。",
            control="exit",
            kind="search",
        )
        stored = {
            "plan": plan,
            "candidates": (candidate,),
            "selected_path": "",
            "operation_id": operation["operation_id"],
        }

        result = await self.feature._select_candidate(
            plan_id,
            stored,
            "0",
        )

        self.assertEqual(result["operation"]["stage"], "prowlarr_search")
        self.assertEqual(
            stored["plan"]["media_metadata"]["retrieval"]["scope"],
            "whole_series",
        )
        self.assertEqual(
            stored["plan"]["media_metadata"]["items"],
            [],
        )

    async def test_unique_hard_match_announces_identity_before_auto_confirmation(self):
        async def planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result.update({
                "plan_id": plan_id,
                "raw_query": "中文标题1 2024",
                "links_frozen": True,
                "auto_confirm": True,
                "selection_mode": "hard_match",
            })
            result["candidates"] = result["candidates"][:1]
            return result

        self.feature.plan_builder = planner
        await self.feature.command({
            "command": "s",
            "args": ["中文标题1", "2024"],
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")
        await self.runtime.run("search-releases-")

        self.assertEqual(len(self.host.milestones), 1)
        self.assertIn(
            "🎬 中文标题1 (English Title)",
            self.host.milestones[0]["text"],
        )
        self.assertNotIn("已识别为", self.host.reports[-1]["status_text"])

    async def test_multi_candidate_exact_read_retry_keeps_selected_index(self):
        from telepiplex_search.direct_link import DirectLinkError

        plan_id = "exact-retry"
        plan = ranked_search_plan()
        plan.update({
            "plan_id": plan_id,
            "raw_query": "候选",
            "entry_kind": "link",
            "links_frozen": True,
        })
        for index, candidate in enumerate(plan["candidates"], 1):
            candidate.update({
                "candidate_id": candidate["candidate_key"],
                "identity_role": "movie",
                "intended_scope": "movie",
                "links_frozen": True,
                "source_links": [{
                    "provider": "douban",
                    "fact_id": f"douban:{index}",
                    "url": (
                        "https://movie.douban.com/subject/"
                        f"{index}/"
                    ),
                    "external_ids": {
                        "douban_subject": str(index),
                    },
                    "role": "movie",
                    "season_number": None,
                    "episode_number": None,
                    "verification": "fact_verified",
                }],
            })

        def fail_exact_read(_link):
            raise DirectLinkError(
                "fixed_link_read_failed",
                ("douban:timeout",),
            )

        self.feature.exact_link_resolver = fail_exact_read
        result = await self.feature._select_candidate(
            plan_id,
            {
                "plan": plan,
                "candidates": tuple(plan["candidates"]),
                "selected_path": "",
                "operation_id": "",
            },
            "1",
        )

        keyboard = result["actions"][0]["data"]["keyboard"]
        retry = next(
            button
            for row in keyboard
            for button in row
            if button["text"] == "重试精确读取"
        )
        self.assertEqual(
            retry["callback_data"],
            f"search:select:{plan_id}:1",
        )

    @patch("telepiplex_search.service.hydrate_frozen_candidate")
    async def test_selected_candidate_is_supplemented_before_exact_read(
        self,
        hydrate,
    ):
        from telepiplex_search.candidate_hydration import (
            CandidateHydrationError,
        )

        events = []

        async def supplement(candidate, raw_query):
            events.append(("supplement", raw_query))
            enriched = deepcopy(candidate)
            enriched["selected_supplemented"] = True
            return enriched

        def exact_read(candidate, **_kwargs):
            events.append(
                ("hydrate", candidate.get("selected_supplemented"))
            )
            raise CandidateHydrationError(
                "metadata_incomplete",
                ("canonical_latin_title",),
            )

        self.feature.selected_candidate_supplementer = supplement
        hydrate.side_effect = exact_read
        plan_id = "selected-supplement"
        plan = ranked_search_plan()
        plan.update({
            "plan_id": plan_id,
            "raw_query": "冰果",
            "entry_kind": "text",
            "links_frozen": True,
        })
        for candidate in plan["candidates"]:
            candidate.update({
                "links_frozen": True,
                "identity_role": "series_root",
            })

        await self.feature._select_candidate(
            plan_id,
            {
                "plan": plan,
                "candidates": tuple(plan["candidates"]),
                "selected_path": "",
                "operation_id": "",
            },
            "0",
        )

        self.assertEqual(
            events,
            [("supplement", "冰果"), ("hydrate", True)],
        )

    @patch("telepiplex_search.service.hydrate_frozen_candidate")
    async def test_deterministic_hydration_error_removes_same_selection_retry(
        self,
        hydrate,
    ):
        from telepiplex_search.candidate_hydration import (
            CandidateHydrationError,
        )

        hydrate.side_effect = CandidateHydrationError(
            "metadata_incomplete",
            ("canonical_latin_title",),
        )
        plan_id = "metadata-incomplete"
        plan = ranked_search_plan()
        plan.update({
            "plan_id": plan_id,
            "raw_query": "冰果",
            "entry_kind": "text",
            "links_frozen": True,
        })
        for candidate in plan["candidates"]:
            candidate.update({
                "links_frozen": True,
                "identity_role": "series_root",
            })
        stored = {
            "plan": plan,
            "candidates": tuple(plan["candidates"]),
            "selected_path": "",
            "operation_id": "",
        }

        result = await self.feature._select_candidate(
            plan_id,
            stored,
            "0",
        )

        action = result["actions"][0]
        callbacks = {
            button["callback_data"]
            for row in action["data"]["keyboard"]
            for button in row
        }
        self.assertNotIn(f"search:select:{plan_id}:0", callbacks)
        self.assertIn("规范拉丁标题", action["text"])
        self.assertNotIn("canonical_latin_title", action["text"])
        self.assertNotIn("可重试", action["text"])

        hydrate.side_effect = CandidateHydrationError(
            "source_fact_conflict",
            ("wikipedia:Q-conflict", "field:year"),
        )
        conflict_plan_id = "source-fact-conflict"
        conflict_plan = deepcopy(plan)
        conflict_plan["plan_id"] = conflict_plan_id
        conflict_stored = {
            "plan": conflict_plan,
            "candidates": tuple(conflict_plan["candidates"]),
            "selected_path": "",
            "operation_id": "",
        }

        conflict = await self.feature._select_candidate(
            conflict_plan_id,
            conflict_stored,
            "0",
        )

        conflict_action = conflict["actions"][0]
        self.assertIn("来源事实存在冲突", conflict_action["text"])
        self.assertNotIn("source_fact_conflict", conflict_action["text"])
        self.assertNotIn("wikipedia:Q-conflict", conflict_action["text"])
        self.assertNotIn("field:year", conflict_action["text"])
        self.assertNotIn("可重试", conflict_action["text"])

    async def test_clarification_plan_renders_options(self):
        async def planner(_raw_query, plan_id):
            return clarification_plan(plan_id)

        self.feature.plan_builder = planner
        command = await self.feature.command({
            "command": "s",
            "args": ["康斯坦汀"],
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")

        report = self.host.reports[-1]
        self.assertEqual(report["stage"], "clarification")
        self.assertEqual(report["operation_id"], command["operation"]["operation_id"])
        self.assertIn("可能指电影或剧集", report["status_text"])
        self.assertEqual(
            [
                row[0]["text"]
                for row in report["details"]["keyboard"][:-1]
            ],
            ["电影《康斯坦丁》", "剧集《康斯坦丁》"],
        )
        self.assertEqual(set(report["details"]), {"keyboard"})
        self.assertNotIn("clarification", report["details"])

    async def test_clarification_choice_replans_in_same_operation(self):
        queries = []

        async def planner(raw_query, plan_id):
            queries.append(raw_query)
            if len(queries) == 1:
                return clarification_plan(plan_id)
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            return result

        self.feature.plan_builder = planner
        command = await self.feature.command({
            "command": "s",
            "args": ["康斯坦汀"],
            "user_id": 1,
            "chat_id": 10,
        })
        operation_id = command["operation"]["operation_id"]
        await self.runtime.run("search-plan-")
        callback_data = (
            self.host.reports[-1]["details"]["keyboard"][0][0]
            ["callback_data"]
        )
        old_plan_id = callback_data.split(":")[2]

        restarted = await self.feature.callback({
            "payload": callback_data.removeprefix("search:"),
            "user_id": 1,
            "chat_id": 10,
        })

        self.assertEqual(restarted["operation"]["operation_id"], operation_id)
        self.assertEqual(restarted["operation"]["stage"], "planning")
        self.assertEqual(restarted["actions"][0]["kind"], "edit_message")
        self.assertNotIn(old_plan_id, self.feature.plans)
        await self.runtime.run("search-plan-")
        self.assertEqual(queries, ["康斯坦汀", "康斯坦丁（电影）"])
        self.assertEqual(self.host.reports[-1]["operation_id"], operation_id)
        self.assertEqual(self.host.reports[-1]["stage"], "plan_confirmation")

    async def test_verified_clarification_choice_preserves_locked_identity(self):
        calls = []

        async def planner(raw_query, plan_id, *, locked_identity=None):
            calls.append((raw_query, locked_identity))
            if len(calls) == 1:
                result = clarification_plan(plan_id)
                result["clarification"]["options"][0].update({
                    "label": "电影《想見你》(2022)",
                    "query": "Someday or One Day: The Movie 2022（电影）",
                    "year": "2022",
                    "locked_identity": {
                        "key": "tvdb",
                        "value": "342532",
                    },
                })
                return result
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            return result

        self.feature.plan_builder = planner
        await self.feature.command({
            "command": "s",
            "args": ["想见你"],
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")
        callback_data = (
            self.host.reports[-1]["details"]["keyboard"][0][0]
            ["callback_data"]
        )

        await self.feature.callback({
            "payload": callback_data.removeprefix("search:"),
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")

        self.assertEqual(calls, [
            ("想见你", None),
            (
                "Someday or One Day: The Movie 2022（电影）",
                ("tvdb", "342532"),
            ),
        ])

    async def test_retry_after_locked_clarification_failure_keeps_identity(self):
        from telepiplex_search.planner import SearchPlanningError

        calls = []

        async def planner(raw_query, plan_id, *, locked_identity=None):
            calls.append((raw_query, locked_identity))
            if len(calls) == 1:
                result = clarification_plan(plan_id)
                result["clarification"]["options"][0].update({
                    "label": "电影《想見你》(2022)",
                    "query": "Someday or One Day: The Movie 2022（电影）",
                    "locked_identity": {
                        "key": "tvdb",
                        "value": "342532",
                    },
                })
                return result
            if len(calls) == 2:
                raise SearchPlanningError(
                    "source_failure",
                    ("tvdb:server_down",),
                )
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            return result

        self.feature.plan_builder = planner
        await self.feature.command({
            "command": "s",
            "args": ["想见你"],
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")
        clarify_callback = (
            self.host.reports[-1]["details"]["keyboard"][0][0]
            ["callback_data"]
        )

        await self.feature.callback({
            "payload": clarify_callback.removeprefix("search:"),
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")
        retry_callback = (
            self.host.reports[-1]["details"]["keyboard"][0][0]
            ["callback_data"]
        )

        await self.feature.callback({
            "payload": retry_callback.removeprefix("search:"),
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")

        self.assertEqual(calls, [
            ("想见你", None),
            (
                "Someday or One Day: The Movie 2022（电影）",
                ("tvdb", "342532"),
            ),
            (
                "Someday or One Day: The Movie 2022（电影）",
                ("tvdb", "342532"),
            ),
        ])

    async def test_browse_and_select_keep_only_request_scoped_state(self):
        async def planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            return result

        self.feature.plan_builder = planner
        await self.feature.command({
            "command": "s", "args": ["候选"], "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("search-plan-")
        next_callback = self.host.reports[-1]["details"]["keyboard"][0][1]["callback_data"]
        plan_id = next_callback.split(":")[2]

        browsed = await self.feature.callback({
            "payload": f"browse:{plan_id}:1", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(browsed["actions"][0]["kind"], "edit_photo")

        selected = await self.feature.callback({
            "payload": f"select:{plan_id}:1", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(selected["operation"]["stage"], "prowlarr_search")
        self.assertEqual(
            self.feature.plans[plan_id]["selected_candidate_key"],
            "tvdb:movie:2",
        )

    async def test_cancel_discards_ranked_candidates_without_persistence(self):
        async def planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            return result

        self.feature.plan_builder = planner
        await self.feature.command({
            "command": "s", "args": ["候选"], "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("search-plan-")
        callback = self.host.reports[-1]["details"]["keyboard"][-1][0]["callback_data"]
        plan_id = callback.rsplit(":", 1)[-1]

        await self.feature.callback({
            "payload": f"cancel:{plan_id}", "user_id": 1, "chat_id": 10,
        })

        self.assertNotIn(plan_id, self.feature.plans)

    async def test_related_selection_prompts_then_allocates_task_local_special(self):
        async def planner(_raw_query, plan_id):
            result = related_ranked_search_plan()
            result["plan_id"] = plan_id
            return result

        self.feature.plan_builder = planner
        await self.feature.command({
            "command": "s", "args": ["关联电影"], "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("search-plan-")
        callback = self.host.reports[-1]["details"]["keyboard"][0][0]["callback_data"]
        plan_id = callback.split(":")[2]

        placement = await self.feature.callback({
            "payload": f"select:{plan_id}:0", "user_id": 1, "chat_id": 10,
        })
        self.assertIn(
            "Specials",
            placement["actions"][0]["data"]["keyboard"][0][0]["text"],
        )

        started = await self.feature.callback({
            "payload": f"placement:{plan_id}:special",
            "user_id": 1,
            "chat_id": 10,
        })

        stored = self.feature.plans[plan_id]
        self.assertEqual(started["operation"]["stage"], "prowlarr_search")
        self.assertEqual(stored["selected_path"], "/Series")
        self.assertEqual(
            stored["plan"]["media_metadata"]["placement"]["episode_number"],
            100,
        )
        self.assertEqual(
            stored["plan"]["media_metadata"]["retrieval"]["query"],
            "English Title 2024",
        )

    @patch("telepiplex_search.service.infer_relation_hypotheses_with_ai")
    async def test_relation_ai_runs_only_after_selected_movie(self, relation_ai):
        async def planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            result["candidates"] = result["candidates"][:1]
            result["raw_query"] = "中文标题 电影版"
            result["relation_pool"] = [{
                "candidate_key": "tvdb:movie:1",
                "fact_ids": ["douban:movie"],
                "facts": [{
                    "fact_id": "douban:movie",
                    "complex_signals": ["provider_relation_signal"],
                }],
                "media_type": "movie",
                "identity": {},
            }, {
                "candidate_key": "tvdb:series:100",
                "fact_ids": ["tvdb:series"],
                "facts": [{
                    "fact_id": "tvdb:series",
                    "complex_signals": [],
                }],
                "media_type": "series",
                "identity": {
                    "chinese_title": "中文剧集",
                    "english_title": "English Series",
                    "year": "2020",
                    "external_ids": {"tvdb": "100"},
                },
            }]
            return result

        relation_ai.return_value = {"hypotheses": [{
            "candidate_key": "tvdb:movie:1",
            "target_candidate_key": "tvdb:series:100",
            "relation_type": "extension_movie",
            "fact_ids": ["douban:movie", "tvdb:series"],
        }]}
        self.feature.plan_builder = planner
        await self.feature.command({
            "command": "s",
            "args": ["中文标题", "电影版"],
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")
        self.assertFalse(relation_ai.called)
        callback = self.host.reports[-1]["details"]["keyboard"][0][0]["callback_data"]
        plan_id = callback.split(":")[2]

        selected = await self.feature.callback({
            "payload": f"select:{plan_id}:0",
            "user_id": 1,
            "chat_id": 10,
        })

        relation_ai.assert_called_once()
        self.assertIn(
            "Specials",
            selected["actions"][0]["data"]["keyboard"][0][0]["text"],
        )
        self.assertEqual(
            self.feature.plans[plan_id]["plan"]["media_metadata"]["retrieval"]["query"],
            "English Title 2024",
        )

    async def test_bare_series_requires_scope_before_prowlarr(self):
        async def planner(_raw_query, plan_id):
            result = series_ranked_search_plan()
            result["plan_id"] = plan_id
            return result

        self.feature.plan_builder = planner
        await self.feature.command({
            "command": "s",
            "args": ["黑暗荣耀"],
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")
        callback = self.host.reports[-1]["details"]["keyboard"][0][0]["callback_data"]
        plan_id = callback.split(":")[2]

        scope = await self.feature.callback({
            "payload": f"select:{plan_id}:0",
            "user_id": 1,
            "chat_id": 10,
        })

        self.assertEqual(self.search_queries, [])
        labels = [
            row[0]["text"]
            for row in scope["actions"][0]["data"]["keyboard"]
        ]
        self.assertIn("全剧（推荐）", labels)
        self.assertIn("指定集", labels)
        self.assertNotIn("指定季", labels)

        started = await self.feature.callback({
            "payload": f"scope:{plan_id}:whole_series",
            "user_id": 1,
            "chat_id": 10,
        })
        self.assertEqual(started["operation"]["stage"], "prowlarr_search")
        await self.runtime.run("search-releases-")

        self.assertEqual(self.search_queries, [
            ("The Glory S01", "series"),
            ("The Glory Season 01", "series"),
            ("The Glory Complete", "series"),
        ])

    async def test_metadata_capability_resolves_once_without_registry(self):
        planner_queries = []

        async def live_planner(raw_query, plan_id):
            planner_queries.append(raw_query)
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            result["candidates"] = result["candidates"][:1]
            result["candidates"][0]["media_metadata"]["metadata_id"] = plan_id
            return result

        self.feature.plan_builder = live_planner
        with self.assertLogs(
            "telepiplex.search",
            level="INFO",
        ) as captured:
            resolved = await self.feature.metadata_capability({
                "method": "resolve_metadata",
                "payload": {
                    "query": "English Title 2024",
                    "probe": {
                        "content_shape": "movie",
                        "observed_seasons": [],
                        "observed_episodes": [],
                        "video_count": 1,
                    },
                },
                "context": {"idempotency_key": "rename-job-1"},
            })

        self.assertTrue(resolved["media_metadata"]["confirmed"])
        self.assertEqual(planner_queries, ["English Title 2024"])
        self.assertTrue(any(
            "metadata_probe content_shape=movie" in line
            for line in captured.output
        ))
        self.assertEqual(
            resolved["media_metadata"]["identity"]["english_title"],
            "English Title",
        )
        self.assertEqual(resolved["naming_metadata"]["source"], "search-live")
        self.assertEqual(self.host.calls, [])

        async def ambiguous_planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            return result

        self.feature.plan_builder = ambiguous_planner
        ambiguous = await self.feature.metadata_capability({
            "method": "resolve_metadata",
            "payload": {"query": "English Title"},
        })
        self.assertEqual(ambiguous["status"], "confirmation_required")
        self.assertEqual(len(ambiguous["candidates"]), 2)
        self.assertTrue(all(
            item["ref"] for item in ambiguous["candidates"]
        ))

    async def test_metadata_probe_filters_media_type_before_ambiguity(self):
        async def mixed_planner(_raw_query, plan_id):
            movie_plan = ranked_search_plan()
            series_plan = series_ranked_search_plan()
            movie = deepcopy(movie_plan["candidates"][0])
            series = deepcopy(series_plan["candidates"][0])
            for candidate in (movie, series):
                candidate["media_metadata"]["metadata_id"] = plan_id
            movie["media_metadata"]["identity"]["chinese_title"] = "同名电影"
            series["media_metadata"]["identity"]["chinese_title"] = "同名剧集"
            return {
                "plan_id": plan_id,
                "candidates": [movie, series],
                "source_queries": {"douban": ["同名作品"]},
            }

        self.feature.plan_builder = mixed_planner
        resolved = await self.feature.metadata_capability({
            "method": "resolve_metadata",
            "payload": {
                "query": "同名作品",
                "probe": {
                    "content_shape": "season_pack",
                    "observed_seasons": [1],
                    "observed_episodes": [
                        {"season_number": 1, "episode_number": 1},
                        {"season_number": 1, "episode_number": 2},
                    ],
                    "video_count": 2,
                },
            },
        })

        contract = resolved["media_metadata"]
        self.assertEqual(contract["identity"]["chinese_title"], "同名剧集")
        self.assertEqual(contract["identity"]["content_kind"], "series")
        self.assertEqual(
            contract["evidence"]["decision"]["scope"],
            "season",
        )

    async def test_metadata_probe_does_not_choose_between_same_type_works(self):
        async def series_planner(_raw_query, plan_id):
            first = deepcopy(series_ranked_search_plan()["candidates"][0])
            second = deepcopy(first)
            first["candidate_key"] = "douban:series:1"
            second["candidate_key"] = "douban:series:2"
            first["media_metadata"]["metadata_id"] = plan_id
            second["media_metadata"]["metadata_id"] = plan_id
            first["media_metadata"]["identity"]["chinese_title"] = "剧集甲"
            second["media_metadata"]["identity"]["chinese_title"] = "剧集乙"
            return {
                "plan_id": plan_id,
                "candidates": [first, second],
            }

        self.feature.plan_builder = series_planner

        ambiguous = await self.feature.metadata_capability({
            "method": "resolve_metadata",
            "payload": {
                "query": "同名剧集",
                "probe": {
                    "year_hint": "2022",
                    "content_shape": "season_pack",
                    "observed_seasons": [1],
                    "observed_episodes": [],
                    "video_count": 8,
                },
            },
        })

        self.assertEqual(ambiguous["status"], "confirmation_required")
        self.assertEqual(
            [item["title"] for item in ambiguous["candidates"]],
            ["剧集甲", "剧集乙"],
        )
        confirmed = await self.feature.metadata_capability({
            "method": "confirm_metadata",
            "payload": {
                "query": "同名剧集",
                "probe": ambiguous["probe"],
                "candidate_ref": ambiguous["candidates"][1]["ref"],
            },
        })
        self.assertEqual(confirmed["status"], "resolved")
        self.assertEqual(
            confirmed["media_metadata"]["identity"]["chinese_title"],
            "剧集乙",
        )

    async def test_metadata_probe_conflict_fails_without_rewriting_identity(self):
        async def movie_planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            result["candidates"] = result["candidates"][:1]
            result["candidates"][0]["media_metadata"]["metadata_id"] = plan_id
            return result

        self.feature.plan_builder = movie_planner

        unresolved = await self.feature.metadata_capability({
            "method": "resolve_metadata",
            "payload": {
                "query": "English Title 2024",
                "probe": {
                    "year_hint": "2024",
                    "content_shape": "season_pack",
                    "observed_seasons": [1],
                    "observed_episodes": [],
                    "video_count": 8,
                },
            },
        })

        self.assertEqual(unresolved, {
            "status": "unresolved",
            "reason_code": "media_type_mismatch",
        })

    async def test_metadata_capability_exact_reads_a_frozen_candidate(self):
        async def live_planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            result["raw_query"] = "布达佩斯大饭店"
            result["entry_kind"] = "text"
            result["links_frozen"] = True
            candidate = result["candidates"][0]
            candidate.update({
                "links_frozen": True,
                "source_links": [{
                    "provider": "douban",
                    "fact_id": "douban:11",
                    "url": "https://movie.douban.com/subject/11/",
                    "external_ids": {"douban_subject": "11"},
                    "role": "movie",
                    "season_number": None,
                    "episode_number": None,
                    "verification": "fact_verified",
                }],
            })
            candidate["media_metadata"]["identity"][
                "english_title"
            ] = "Discovery Summary"
            result["candidates"] = [candidate]
            return result

        self.feature.plan_builder = live_planner
        supplement_calls = []

        async def supplement(candidate, raw_query):
            supplement_calls.append(raw_query)
            enriched = deepcopy(candidate)
            enriched["selected_supplemented"] = True
            return enriched

        self.feature.selected_candidate_supplementer = supplement

        def exact_hydration(candidate, **_kwargs):
            self.assertTrue(candidate["selected_supplemented"])
            hydrated = deepcopy(candidate)
            hydrated["media_metadata"]["identity"][
                "english_title"
            ] = "Hydrated Exact Title"
            hydrated["metadata_hydrated"] = True
            return hydrated

        with patch(
            "telepiplex_search.service.hydrate_frozen_candidate",
            side_effect=exact_hydration,
        ) as hydrate:
            resolved = await self.feature.metadata_capability({
                "method": "resolve_metadata",
                "payload": {"query": "布达佩斯大饭店"},
            })

        hydrate.assert_called_once()
        self.assertEqual(supplement_calls, ["布达佩斯大饭店"])
        self.assertEqual(
            resolved["media_metadata"]["identity"]["english_title"],
            "Hydrated Exact Title",
        )

    async def test_metadata_capability_derives_series_scope_from_probe(self):
        async def live_planner(_raw_query, plan_id):
            result = series_ranked_search_plan()
            result["plan_id"] = plan_id
            candidate = result["candidates"][0]
            candidate["media_metadata"]["metadata_id"] = plan_id
            candidate["media_metadata"]["items"].extend({
                "item_id": f"s2e{number}",
                "content_role": "main_episode",
                "season_number": 2,
                "episode_number": number,
                "aired": "2023-03-10",
            } for number in range(1, 9))
            result["candidates"] = [candidate]
            return result

        self.feature.plan_builder = live_planner
        cases = (
            (
                {
                    "content_shape": "single_episode",
                    "observed_seasons": [2],
                    "observed_episodes": [
                        {"season_number": 2, "episode_number": 3},
                    ],
                    "video_count": 1,
                },
                ("episode", 2, 3),
            ),
            (
                {
                    "content_shape": "single_season_episode_pack",
                    "observed_seasons": [2],
                    "observed_episodes": [
                        {"season_number": 2, "episode_number": 1},
                        {"season_number": 2, "episode_number": 2},
                    ],
                    "video_count": 2,
                },
                ("season", 2, None),
            ),
            (
                {
                    "content_shape": "multi_season_episode_pack",
                    "observed_seasons": [1, 2],
                    "observed_episodes": [
                        {"season_number": 1, "episode_number": 1},
                        {"season_number": 2, "episode_number": 1},
                    ],
                    "video_count": 2,
                },
                ("whole_series", None, None),
            ),
        )

        for probe, expected in cases:
            with self.subTest(shape=probe["content_shape"]):
                resolved = await self.feature.metadata_capability({
                    "method": "resolve_metadata",
                    "payload": {"query": "黑暗荣耀", "probe": probe},
                })
                decision = resolved["media_metadata"]["evidence"]["decision"]
                self.assertEqual(
                    (
                        decision["scope"],
                        decision["season_number"],
                        decision["episode_number"],
                    ),
                    expected,
                )

    async def test_metadata_capability_maps_unscoped_episode_probe_only_with_unique_inventory(self):
        async def live_planner(_raw_query, plan_id):
            result = series_ranked_search_plan()
            result["plan_id"] = plan_id
            candidate = result["candidates"][0]
            candidate["media_metadata"]["metadata_id"] = plan_id
            result["candidates"] = [candidate]
            return result

        self.feature.plan_builder = live_planner
        pack = await self.feature.metadata_capability({
            "method": "resolve_metadata",
            "payload": {
                "query": "黑暗荣耀",
                "probe": {
                    "content_shape": "episode_pack_unscoped",
                    "observed_seasons": [],
                    "observed_episodes": [
                        {"season_number": None, "episode_number": 1},
                        {"season_number": None, "episode_number": 2},
                    ],
                    "video_count": 2,
                },
            },
        })
        pack_decision = pack["media_metadata"]["evidence"]["decision"]
        self.assertEqual(pack_decision["scope"], "season")
        self.assertEqual(pack_decision["season_number"], 1)

        episode = await self.feature.metadata_capability({
            "method": "resolve_metadata",
            "payload": {
                "query": "黑暗荣耀",
                "probe": {
                    "content_shape": "single_episode_unscoped",
                    "observed_seasons": [],
                    "observed_episodes": [{
                        "season_number": None,
                        "episode_number": 3,
                    }],
                    "video_count": 1,
                },
            },
        })
        episode_decision = episode["media_metadata"]["evidence"]["decision"]
        self.assertEqual(episode_decision["scope"], "episode")
        self.assertEqual(episode_decision["season_number"], 1)
        self.assertEqual(episode_decision["episode_number"], 3)

    async def test_metadata_capability_keeps_ambiguous_unscoped_episode_probe_unresolved(self):
        async def live_planner(_raw_query, plan_id):
            result = series_ranked_search_plan()
            result["plan_id"] = plan_id
            candidate = result["candidates"][0]
            candidate["media_metadata"]["metadata_id"] = plan_id
            candidate["media_metadata"]["items"].extend({
                "item_id": f"s2e{number}",
                "content_role": "main_episode",
                "season_number": 2,
                "episode_number": number,
                "aired": "2023-03-10",
            } for number in range(1, 9))
            result["candidates"] = [candidate]
            return result

        self.feature.plan_builder = live_planner

        unresolved = await self.feature.metadata_capability({
            "method": "resolve_metadata",
            "payload": {
                "query": "黑暗荣耀",
                "probe": {
                    "content_shape": "episode_pack_unscoped",
                    "observed_seasons": [],
                    "observed_episodes": [
                        {"season_number": None, "episode_number": 1},
                        {"season_number": None, "episode_number": 2},
                    ],
                    "video_count": 2,
                },
            },
        })

        self.assertEqual(unresolved["status"], "unresolved")
        self.assertEqual(unresolved["reason_code"], "scope_unresolved")


class FeatureSourceContractTest(unittest.TestCase):
    def test_release_identity_requires_host_photo_action_contract(self):
        manifest = yaml.safe_load(
            (ROOT / "manifest.yaml").read_text(encoding="utf-8")
        )
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["version"], "1.7.0")
        self.assertEqual(manifest["host_api"], ">=1.4,<2.0")
        self.assertEqual(project["project"]["version"], "1.7.0")

    def test_default_config_enables_free_and_configured_sources(self):
        config = yaml.safe_load((ROOT / "config.default.yaml").read_text())

        self.assertTrue(config["metadata"]["wikipedia"]["enable"])
        self.assertTrue(config["metadata"]["douban"]["enable"])
        self.assertTrue(config["metadata"]["tvdb"]["enable"])
        self.assertTrue(config["ai"]["enable"])
        self.assertNotIn("source_orchestration", config["ai"])
        self.assertEqual(config["ai"]["thinking_mode"], "enabled")

    def test_prowlarr_is_not_disabled_by_legacy_hidden_search_flag(self):
        from telepiplex_search.adapters.prowlarr import _get_prowlarr_config
        from telepiplex_search.context import runtime_context

        runtime_context.configure({
            "search": {
                "enable": False,
                "prowlarr": {
                    "base_url": "http://prowlarr:9696",
                    "api_key": "configured",
                },
            },
        })

        _config, base_url, api_key = _get_prowlarr_config()
        self.assertEqual(base_url, "http://prowlarr:9696")
        self.assertEqual(api_key, "configured")

    def test_readme_build_example_uses_current_version(self):
        source = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("/tmp/search-1.7.0.tpx", source)
        self.assertIn("豆瓣", source)
        self.assertIn("都不是", source)
        self.assertIn("统一 AI", source)
        self.assertIn("Wikipedia", source)
        self.assertIn("TVDB", source)
        self.assertIn("rename", source)
        self.assertNotIn("dist/search-1.7.0.tpx", source)

    def test_source_has_no_host_telegram_or_init_imports(self):
        forbidden = []
        for path in (ROOT / "src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = (
                    [item.name for item in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module]
                    if isinstance(node, ast.ImportFrom) and node.module
                    else []
                )
                forbidden.extend(
                    name for name in names
                    if name.split(".", 1)[0] in {"app", "init", "telegram"}
                )
        self.assertEqual(forbidden, [])


if __name__ == "__main__":
    unittest.main()
