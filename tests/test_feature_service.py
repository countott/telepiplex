import ast
import asyncio
from copy import deepcopy
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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


class FakeCore:
    def __init__(self):
        self.calls = []
        self.reports = []

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


class FakeRuntime:
    def __init__(self):
        self.tasks = {}

    def spawn(self, awaitable, *, task_id):
        self.tasks[task_id] = awaitable
        return None

    async def run(self, prefix):
        task_id = next(key for key in self.tasks if key.startswith(prefix))
        await self.tasks.pop(task_id)


class MediaSearchFeatureTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from telepiplex_media_search.service import MediaSearchFeature

        self.core = FakeCore()
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

        self.feature = MediaSearchFeature(
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
            core=self.core,
            plan_builder=planner,
            release_search=search,
            release_rank=lambda items, limit: items[:limit],
            release_resolver=lambda item: item["magnet_url"],
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
        await self.runtime.run("media-search-plan-")
        plan_report = self.core.reports[-1]
        callback_data = plan_report["details"]["keyboard"][0][0]["callback_data"]
        return callback_data.rsplit(":", 1)[-1]

    async def test_confirmed_plan_searches_prowlarr_in_english_only(self):
        command = await self.feature.command({
            "command": "s",
            "args": ["中文输入"],
            "user_id": 1,
            "chat_id": 10,
        })
        self.assertEqual(command["operation"]["stage"], "planning")
        await self.runtime.run("media-search-plan-")
        callback_data = self.core.reports[-1]["details"]["keyboard"][0][0]["callback_data"]
        plan_id = callback_data.rsplit(":", 1)[-1]
        self.assertEqual(self.search_queries, [])

        confirmed = await self.feature.callback({
            "namespace": "media-search",
            "payload": f"confirm:{plan_id}",
            "user_id": 1,
            "chat_id": 10,
        })

        self.assertEqual(confirmed["operation"]["stage"], "prowlarr_search")
        await self.runtime.run("media-search-releases-")

        self.assertEqual(self.search_queries, [("English Title", "movie")])
        self.assertIn("找到 1 个", self.core.reports[-1]["status_text"])
        self.assertEqual(self.core.reports[-1]["state"], "awaiting_input")

    async def test_wrong_scope_never_enters_release_rank(self):
        from telepiplex_media_search.series_scope import apply_series_scope

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
        from telepiplex_media_search.series_scope import apply_series_scope

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

        self.assertIn("未自动展示其他范围", action["text"])
        self.assertNotIn("keyboard", action.get("data") or {})
        self.assertNotIn(plan_id, self.feature.plans)

    async def test_twelve_results_render_four_rows_of_three(self):
        from telepiplex_media_search.series_scope import apply_series_scope

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

    async def test_planning_failure_uses_safe_specific_reason(self):
        from telepiplex_media_search.planner import SearchPlanningError

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
        await self.runtime.run("media-search-plan-")
        self.assertIn("多个候选", self.core.reports[-1]["status_text"])
        self.assertIn("AI 当前不可用", self.core.reports[-1]["status_text"])
        self.assertEqual(self.core.reports[-1]["state"], "failed")
        self.assertEqual(self.feature.plans, {})
        self.assertEqual(self.search_queries, [])

    async def test_config_wizard_refuses_to_replace_active_search_session(self):
        owner = (10, 1)
        self.feature.awaiting_queries.add(owner)

        result = await self.feature.command({
            "command": "media_search_config",
            "args": [],
            "user_id": owner[1],
            "chat_id": owner[0],
        })

        self.assertIn("先完成或取消", result["actions"][0]["text"])
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

    async def test_whole_series_query_uses_locked_canonical_identity(self):
        plan = search_plan()
        contract = plan["media_metadata"]
        contract["identity"]["content_kind"] = "series"
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
            self.feature._english_prowlarr_query(plan, contract),
            "English Title",
        )

    async def test_selected_release_calls_download_provider_with_canonical_contract(self):
        plan_id = await self._prepare_search()
        await self.feature.callback({
            "namespace": "media-search", "payload": f"confirm:{plan_id}",
            "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("media-search-releases-")
        result = await self.feature.callback({
            "namespace": "media-search", "payload": f"release:{plan_id}:0",
            "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(result["operation"]["stage"], "resolving_release")
        await self.runtime.run("media-search-submit-")

        capability, method, payload, kwargs = self.core.calls[0]
        self.assertEqual((capability, method), ("download.provider", "submit"))
        self.assertEqual(payload["selected_path"], "/Movies")
        self.assertTrue(payload["media_metadata"]["confirmed"])
        self.assertEqual(payload["media_metadata"]["identity"]["chinese_title"], "中文标题")
        self.assertEqual(payload["operation_id"], self.core.reports[-1]["operation_id"])
        self.assertEqual(payload["operation_revision"], self.core.reports[-1]["revision"])
        self.assertEqual(self.core.reports[-1]["state"], "handed_off")
        self.assertTrue(kwargs["idempotency_key"].startswith(plan_id))

    async def test_rejected_handoff_never_calls_download_provider(self):
        original_report = self.core.report_operation

        async def reject_handoff(operation):
            if operation["state"] == "handed_off":
                return {
                    "accepted": False,
                    "state": "cancelled",
                    "revision": operation["revision"] + 1,
                }
            return await original_report(operation)

        self.core.report_operation = reject_handoff
        plan_id = await self._prepare_search()
        await self.feature.callback({
            "namespace": "media-search", "payload": f"confirm:{plan_id}",
            "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("media-search-releases-")
        submission = await self.feature.callback({
            "namespace": "media-search", "payload": f"release:{plan_id}:0",
            "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("media-search-submit-")

        self.assertEqual(self.core.calls, [])
        self.assertEqual(
            self.feature.operations[
                submission["operation"]["operation_id"]
            ]["state"],
            "failed",
        )

    async def test_lost_handoff_response_reuses_exact_revision(self):
        plan_id = await self._prepare_search()
        await self.feature.callback({
            "namespace": "media-search", "payload": f"confirm:{plan_id}",
            "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("media-search-releases-")
        stored = self.feature.plans[plan_id]
        operation_id = stored["operation_id"]
        original_report = self.core.report_operation
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

        self.core.report_operation = accept_then_lose

        with self.assertRaises(RuntimeError):
            await self.feature._submit_release(
                plan_id, stored, "0", operation_id
            )
        result = await self.feature._submit_release(
            plan_id, stored, "0", operation_id
        )

        self.assertEqual(handoff_revisions, [
            handoff_revisions[0], handoff_revisions[0]
        ])
        self.assertEqual(len(self.core.calls), 1)
        self.assertIn("已提交", result["actions"][0]["text"])

    async def test_empty_query_has_explicit_exit_and_awaiting_operation(self):
        result = await self.feature.command({
            "command": "search", "args": [], "user_id": 1, "chat_id": 10,
        })

        self.assertEqual(result["operation"]["state"], "awaiting_input")
        keyboard = result["actions"][0]["data"]["keyboard"]
        self.assertEqual(keyboard[-1][0]["text"], "退出")

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
        self.assertEqual(self.core.calls, [])

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
            next_plugin_id="open115",
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
        await self.runtime.run("media-search-plan-")

        report = self.core.reports[-1]
        self.assertEqual(report["details"]["photo_url"], "https://image.example/top.jpg")
        self.assertIn("92/100", report["status_text"])

    async def test_browse_and_select_keep_only_request_scoped_state(self):
        async def planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            return result

        self.feature.plan_builder = planner
        await self.feature.command({
            "command": "s", "args": ["候选"], "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("media-search-plan-")
        next_callback = self.core.reports[-1]["details"]["keyboard"][0][1]["callback_data"]
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
        await self.runtime.run("media-search-plan-")
        callback = self.core.reports[-1]["details"]["keyboard"][-1][0]["callback_data"]
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
        await self.runtime.run("media-search-plan-")
        callback = self.core.reports[-1]["details"]["keyboard"][0][0]["callback_data"]
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

    @patch("telepiplex_media_search.service.infer_relation_hypotheses_with_ai")
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
        await self.runtime.run("media-search-plan-")
        self.assertFalse(relation_ai.called)
        callback = self.core.reports[-1]["details"]["keyboard"][0][0]["callback_data"]
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
        await self.runtime.run("media-search-plan-")
        callback = self.core.reports[-1]["details"]["keyboard"][0][0]["callback_data"]
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
        await self.runtime.run("media-search-releases-")

        self.assertEqual(self.search_queries, [("The Glory", "series")])

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
            "telepiplex.media-search",
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
        self.assertEqual(resolved["naming_metadata"]["source"], "media-search-live")
        self.assertEqual(self.core.calls, [])

        from telepiplex_plugin_sdk import FeatureError
        async def ambiguous_planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            return result

        self.feature.plan_builder = ambiguous_planner
        with self.assertRaises(FeatureError) as raised:
            await self.feature.metadata_capability({
                "method": "resolve_metadata",
                "payload": {"query": "English Title"},
            })
        self.assertEqual(raised.exception.code, "metadata_unresolved")


class FeatureSourceContractTest(unittest.TestCase):
    def test_release_identity_requires_core_photo_action_contract(self):
        manifest = yaml.safe_load(
            (ROOT / "manifest.yaml").read_text(encoding="utf-8")
        )
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "1.4.0")
        self.assertEqual(manifest["core_api"], ">=1.2,<2.0")
        self.assertIn('version = "1.4.0"', project)

    def test_default_config_enables_free_and_configured_sources(self):
        config = yaml.safe_load((ROOT / "config.default.yaml").read_text())

        self.assertTrue(config["metadata"]["wikipedia"]["enable"])
        self.assertTrue(config["metadata"]["tvdb"]["enable"])
        self.assertTrue(config["ai"]["enable"])

    def test_prowlarr_is_not_disabled_by_legacy_hidden_search_flag(self):
        from telepiplex_media_search.adapters.prowlarr import _get_prowlarr_config
        from telepiplex_media_search.context import runtime_context

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
        self.assertIn("dist/media-search-1.4.0.tpx", source)
        self.assertNotIn("dist/media-search-1.2.0.tpx", source)
        self.assertNotIn("dist/media-search-1.1.0.tpx", source)

    def test_source_has_no_core_telegram_or_init_imports(self):
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
