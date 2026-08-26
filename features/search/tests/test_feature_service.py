import ast
import asyncio
from copy import deepcopy
from datetime import date, timedelta
import html
import re
import threading
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
                "external_ids": {"wikidata": "QTESTMOVIE1"},
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
            "candidate_id": f"tvdb:movie:{index + 1}",
            "anchor_fact_id": f"tvdb:{index + 1}",
            "score": {"total": 92 - index * 10},
            "recommended": index == 0,
            "selectable": True,
            "media_metadata": contract,
            "prowlarr_queries": [f"{title} {year}"],
            "poster_url": poster,
            "reasons": [],
            "source_links": [{
                "provider": "tvdb",
                "fact_id": f"tvdb:{index + 1}",
                "external_ids": {"tvdb": str(index + 1)},
                "verification": "fact_verified",
                "role": "movie",
            }],
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


def frozen_douban_movie_candidate():
    candidate = deepcopy(ranked_search_plan()["candidates"][0])
    candidate.update({
        "candidate_id": "douban_subject:1",
        "candidate_key": "douban_subject:1",
        "anchor_fact_id": "douban:1",
        "identity_role": "movie",
        "intended_scope": "work",
        "links_frozen": True,
        "unresolved_sources": [],
        "source_links": [{
            "provider": "douban",
            "fact_id": "douban:1",
            "url": "https://movie.douban.com/subject/1/",
            "external_ids": {"douban_subject": "1"},
            "role": "movie",
            "season_number": None,
            "episode_number": None,
            "verification": "fact_verified",
            "proposed_season_number": None,
            "proposed_episode_number": None,
        }],
    })
    return candidate


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
        "series_inventory": {
            "season_totals": {1: 8},
        },
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
        self.segments_sealed = []
        self.timeline = []

    async def call_capability(self, capability, method, payload, **kwargs):
        self.timeline.append(("capability", capability, method))
        self.calls.append((capability, method, payload, kwargs))
        return {"accepted": True, "job_id": "download-1"}

    async def report_operation(self, operation):
        self.timeline.append(("report", operation["state"], operation["stage"]))
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
        mode="identity",
        photo_url="",
        deadline=10,
    ):
        self.timeline.append(("milestone", mode, milestone_id))
        self.milestones.append({
            "operation_id": operation_id,
            "milestone_id": milestone_id,
            "mode": mode,
            "text": text,
            "photo_url": photo_url,
            "deadline": deadline,
        })
        return {"accepted": True, "duplicate": False}

    async def seal_operation_stage(
        self,
        operation_id,
        milestone_id,
        text,
        *,
        deadline=10,
    ):
        return await self.publish_operation_milestone(
            operation_id,
            milestone_id,
            text,
            mode="stage",
            deadline=deadline,
        )

    async def seal_operation_segment(
        self,
        operation_id,
        role,
        *,
        deadline=10,
    ):
        self.timeline.append(("segment_sealed", role, operation_id))
        self.segments_sealed.append({
            "operation_id": operation_id,
            "role": role,
            "deadline": deadline,
        })
        return {"accepted": True, "state": "sealed"}


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

        async def candidate_poster_passthrough(_candidate, _provider):
            return ""

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
            candidate_poster_lookup=candidate_poster_passthrough,
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

    async def test_discovery_observation_is_bounded_and_omits_query_text(self):
        from telepiplex_search.context import runtime_context

        logger = Mock()
        original = runtime_context.logger
        runtime_context.logger = logger
        try:
            await self._prepare_search()
        finally:
            runtime_context.logger = original

        observations = [
            call.kwargs["extra"]
            for call in logger.info.call_args_list
            if call.kwargs.get("extra", {}).get("event_name")
            == "search.discovery.completed"
        ]
        self.assertEqual(len(observations), 1)
        fields = observations[0]["diagnostic_fields"]
        self.assertTrue(fields["input"]["search_session_id"])
        self.assertEqual(fields["output"]["candidate_count"], 1)
        self.assertNotIn("English Title", repr(observations[0]))

    async def test_hydration_observation_omits_frozen_source_url(self):
        from telepiplex_search.context import runtime_context

        logger = Mock()
        original = runtime_context.logger
        runtime_context.logger = logger
        candidate = frozen_douban_movie_candidate()
        try:
            with patch.object(
                self.feature,
                "_prefetch_exact_resolver",
                new=AsyncMock(return_value=lambda _link: {}),
            ), patch(
                "telepiplex_search.service.hydrate_frozen_candidate_anchor",
                return_value={"metadata_hydrated": True},
            ), patch(
                "telepiplex_search.service.needs_authoritative_scope_enrichment",
                return_value=False,
            ):
                await self.feature._hydrate_selected_candidate(
                    candidate,
                    metadata_id="plan-1",
                    raw_query="private query",
                    require_anchor=True,
                )
        finally:
            runtime_context.logger = original

        observations = [
            call.kwargs["extra"]
            for call in logger.info.call_args_list
            if call.kwargs.get("extra", {}).get("event_name")
            == "search.hydration.completed"
        ]
        self.assertEqual(len(observations), 1)
        output = observations[0]["diagnostic_fields"]["output"]
        self.assertTrue(output["metadata_hydrated"])
        self.assertNotIn("movie.douban.com", repr(observations[0]))
        self.assertNotIn("private query", repr(observations[0]))

    async def test_first_and_tail_prowlarr_waves_are_observed_without_query(self):
        from telepiplex_search.context import runtime_context

        logger = Mock()
        original = runtime_context.logger
        runtime_context.logger = logger
        try:
            plan_id = await self._prepare_search()
            stored = self.feature.plans[plan_id]
            self.feature.config["search"]["prowlarr"].update({
                "first_wave_indexer_ids": [1],
                "wave_delay": 0.0,
            })
            self.feature.indexer_loader = lambda: [
                {"id": 1, "name": "First"},
                {"id": 2, "name": "Tail"},
            ]
            self.feature.indexer_search = lambda *_args: []
            await self.feature._confirm_and_search(plan_id, stored)
        finally:
            runtime_context.logger = original

        observations = [
            call.kwargs["extra"]
            for call in logger.info.call_args_list
            if call.kwargs.get("extra", {}).get("event_name")
            == "search.prowlarr.wave.completed"
        ]
        self.assertEqual(
            {item["diagnostic_fields"]["output"]["wave"] for item in observations},
            {"first", "tail"},
        )
        self.assertTrue(all(
            "English Title" not in repr(item)
            for item in observations
        ))

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
        self.assertIs(
            command["operation"]["details"]["defer_photo_until_media"],
            True,
        )
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

        self.assertEqual(confirmed.get("actions"), [])
        self.assertNotEqual(
            confirmed["operation"]["stage"],
            "prowlarr_search",
        )
        await self.runtime.run("search-releases-")

        self.assertEqual(self.search_queries, [("English Title", "movie")])
        self.assertIn(
            "中文标题 (English Title)",
            self.host.reports[-1]["status_text"],
        )
        self.assertEqual(self.host.reports[-1]["state"], "awaiting_input")

    async def test_identity_seals_candidate_before_prowlarr_message_is_created(self):
        plan_id = await self._prepare_search()

        started = await self.feature.callback({
            "namespace": "search",
            "payload": f"confirm:{plan_id}",
            "user_id": 1,
            "chat_id": 10,
        })

        self.assertEqual(started.get("actions"), [])
        self.assertNotEqual(started["operation"]["stage"], "prowlarr_search")

        await self.runtime.run("search-releases-")
        identity_stage_index = self.host.timeline.index(
            ("report", "running", "identity_confirmation")
        )
        seal_index = next(
            index for index, item in enumerate(self.host.timeline)
            if item[:2] == ("segment_sealed", "identity")
        )
        prowlarr_index = next(
            index for index, item in enumerate(self.host.timeline)
            if item == ("report", "running", "prowlarr_search")
        )
        self.assertLess(identity_stage_index, seal_index)
        self.assertLess(seal_index, prowlarr_index)

    async def test_search_reports_use_identity_then_search_message_segments(self):
        plan_id = await self._prepare_search()
        await self.feature.callback({
            "namespace": "search",
            "payload": f"confirm:{plan_id}",
            "user_id": 1,
            "chat_id": 10,
        })

        await self.runtime.run("search-releases-")

        identity_reports = [
            report for report in self.host.reports
            if (report.get("segment") or {}).get("role") == "identity"
        ]
        search_reports = [
            report for report in self.host.reports
            if (report.get("segment") or {}).get("role") == "search"
        ]
        self.assertTrue(identity_reports)
        self.assertTrue(search_reports)
        self.assertTrue(all(
            report["segment"]["presentation_kind"] == "photo"
            for report in identity_reports
        ))
        self.assertTrue(all(
            report["segment"]["presentation_kind"] == "text"
            for report in search_reports
        ))
        self.assertEqual(
            self.host.segments_sealed[0]["role"],
            "identity",
        )

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

        self.assertEqual(started.get("actions"), [])
        await self.runtime.run("search-releases-")

        failed = self.host.reports[-1]
        self.assertEqual(failed["state"], "awaiting_input")
        self.assertEqual(failed["stage"], "prowlarr_recovery")
        keyboard = failed["details"]["keyboard"]
        self.assertEqual(keyboard[0][0]["text"], "重试搜索")
        self.assertEqual(
            keyboard[0][0]["callback_data"],
            f"search:confirm:{plan_id}",
        )
        self.assertEqual(
            [button["text"] for row in keyboard for button in row],
            ["重试搜索", "退出"],
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
        self.assertIn("搜索词：English Title", failed["status_text"])
        self.assertNotIn("Prowlarr", failed["status_text"])
        self.assertIn(plan_id, self.feature.plans)

    async def test_related_movie_prompt_hides_search_backend_name(self):
        plan = search_plan()
        plan["media_metadata"]["relation"] = {
            "type": "衍生电影",
            "target_series": {
                "chinese_title": "目标剧集",
                "english_title": "Target Series",
            },
        }
        operation = self.feature._new_operation(
            {"chat_id": 10, "user_id": 1},
            state="running",
            stage="planning",
            status_text="正在准备。",
            control="cancel",
            kind="search",
        )

        action = self.feature._related_placement_action(
            plan["plan_id"],
            {"plan": plan, "operation_id": operation["operation_id"]},
        )

        text = action["actions"][0]["text"]
        self.assertIn("资源搜索都按电影标题和年份检索", text)
        self.assertNotIn("Prowlarr", text)

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
            "operation_id": "op-scope-gate",
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

    async def test_rejected_identity_delivery_stops_before_release_lookup(self):
        from telepiplex_plugin_sdk import FeatureError

        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        stored["selected_path"] = "/Movies"
        self.host.seal_operation_segment = AsyncMock(return_value={
            "accepted": False,
        })
        release_calls = []
        self.feature.release_search = (
            lambda *args: release_calls.append(args) or []
        )

        with self.assertRaises(FeatureError) as raised:
            await self.feature._confirm_and_search(plan_id, stored)

        self.assertEqual(raised.exception.code, "identity_delivery_failed")
        self.assertEqual(release_calls, [])

    async def test_lost_identity_seal_response_retries_same_segment(self):
        from telepiplex_plugin_sdk import FeatureError

        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        stored["selected_path"] = "/Movies"
        original_seal = self.host.seal_operation_segment
        attempts = []

        async def accept_then_lose(
            operation_id,
            role,
            *,
            deadline=10,
        ):
            attempts.append(role)
            response = await original_seal(
                operation_id,
                role,
                deadline=deadline,
            )
            if len(attempts) == 1:
                raise FeatureError(
                    "internal_error",
                    "Host milestone bookkeeping was interrupted",
                )
            return {**response, "accepted": False, "duplicate": True}

        self.host.seal_operation_segment = accept_then_lose

        await self.feature._confirm_and_search(plan_id, stored)

        self.assertEqual(attempts, [attempts[0], attempts[0]])
        self.assertTrue(self.search_queries)
        self.assertTrue(stored["identity_segment_sealed"])

    async def test_rejected_identity_segment_seal_is_not_retried(self):
        from telepiplex_plugin_sdk import FeatureError

        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        stored["selected_path"] = "/Movies"
        attempts = []

        async def reject_owner(*_args, **_kwargs):
            attempts.append("owner_mismatch")
            raise FeatureError(
                "owner_mismatch",
                "operation belongs to another Feature",
            )

        self.host.seal_operation_segment = reject_owner

        with self.assertRaises(FeatureError) as raised:
            await self.feature._confirm_and_search(plan_id, stored)

        self.assertEqual(raised.exception.code, "identity_delivery_failed")
        self.assertEqual(attempts, ["owner_mismatch"])

    async def test_identity_segment_failure_reports_contract_code_not_type(
        self,
    ):
        from telepiplex_plugin_sdk import FeatureError

        plan_id = await self._prepare_search()

        async def fail_internal(*_args, **_kwargs):
            raise FeatureError(
                "internal_error",
                "Host milestone bookkeeping was interrupted",
            )

        self.host.seal_operation_segment = fail_internal
        await self.feature.callback({
            "namespace": "search",
            "payload": f"confirm:{plan_id}",
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-releases-")

        failed = self.host.reports[-1]
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(
            failed["status_text"],
            "资源搜索失败：identity_delivery_failed",
        )
        self.assertNotIn("FeatureError", failed["status_text"])

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
            "operation_id": "op-scope-zero",
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
            "operation_id": "op-scope-twelve",
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
        self.assertIn(
            "正在搜索：中文标题 (English Title)",
            partial[-1]["status_text"],
        )
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
        self.assertIn("中文标题 (English Title)", result["actions"][0]["text"])
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

    async def test_indexer_tail_wave_starts_at_delay_not_before(self):
        import time

        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        self.feature.config["search"]["prowlarr"].update({
            "first_wave_indexer_ids": [1],
            "wave_delay": 0.05,
        })
        self.feature.indexer_loader = lambda: [
            {"id": 1, "name": "First"},
            {"id": 2, "name": "Tail"},
        ]
        first_started = threading.Event()
        tail_started = threading.Event()
        release = threading.Event()
        starts = {}

        def search_indexer(_query, _media_type, indexer_id):
            starts[indexer_id] = time.monotonic()
            (first_started if indexer_id == 1 else tail_started).set()
            release.wait(1)
            return []

        self.feature.indexer_search = search_indexer
        search_task = asyncio.create_task(
            self.feature._confirm_and_search(plan_id, stored)
        )
        try:
            await asyncio.to_thread(first_started.wait, 1)
            await asyncio.sleep(0.015)
            tail_started_early = tail_started.is_set()
            initial_task_count = len(stored.get("indexer_tasks") or ())
            await asyncio.to_thread(tail_started.wait, 0.3)
        finally:
            release.set()
        await search_task

        self.assertFalse(tail_started_early)
        self.assertEqual(
            initial_task_count,
            len(stored["active_prowlarr_queries"]),
        )
        self.assertTrue(tail_started.is_set())
        self.assertGreaterEqual(starts[2] - starts[1], 0.04)

    async def test_slow_incremental_report_does_not_delay_tail_wave_deadline(
        self,
    ):
        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        self.feature.config["search"]["prowlarr"].update({
            "first_wave_indexer_ids": [1],
            "wave_delay": 0.05,
            "timeout": 1.0,
        })
        self.feature.indexer_loader = lambda: [
            {"id": 1, "name": "First"},
            {"id": 2, "name": "Tail"},
        ]
        tail_started = threading.Event()
        report_started = asyncio.Event()
        release_report = asyncio.Event()

        def search_indexer(_query, _media_type, indexer_id):
            if indexer_id == 2:
                tail_started.set()
                return []
            return [{
                "title": "English.Title.2024.1080p.WEB-DL.First",
                "magnet_url": "magnet:?xt=urn:btih:" + "1" * 40,
                "indexer": "First",
            }]

        async def slow_report(operation):
            if (
                operation["stage"] == "prowlarr_search"
                and operation["details"].get("allow_running_callbacks")
            ):
                report_started.set()
                await release_report.wait()
            self.host.reports.append(deepcopy(operation))
            return {
                "accepted": True,
                "state": operation["state"],
                "revision": operation["revision"],
            }

        self.feature.indexer_search = search_indexer
        self.host.report_operation = slow_report
        search_task = asyncio.create_task(
            self.feature._confirm_and_search(plan_id, stored)
        )
        tail_started_before_report_release = False
        try:
            await asyncio.wait_for(report_started.wait(), timeout=0.2)
            tail_started_before_report_release = await asyncio.to_thread(
                tail_started.wait,
                0.2,
            )
        finally:
            release_report.set()
        await asyncio.wait_for(search_task, timeout=2.0)

        self.assertTrue(tail_started_before_report_release)
        self.assertEqual(stored["indexer_tasks"], [])
        self.assertIsNone(stored.get("wave_launcher_task"))
        self.assertIsNone(stored.get("incremental_report_task"))

    async def test_slow_incremental_report_cannot_extend_global_timeout(self):
        import time

        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        self.feature.config["search"]["prowlarr"].update({
            "first_wave_indexer_ids": [1],
            "wave_delay": 0.01,
        })
        self.feature.indexer_loader = lambda: [
            {"id": 1, "name": "First"},
            {"id": 2, "name": "Tail"},
        ]
        release_tail = threading.Event()
        report_started = asyncio.Event()
        report_cancelled = asyncio.Event()
        never_release_report = asyncio.Event()

        def search_indexer(_query, _media_type, indexer_id):
            if indexer_id == 2:
                release_tail.wait(1)
                return []
            return [{
                "title": "English.Title.2024.1080p.WEB-DL.First",
                "magnet_url": "magnet:?xt=urn:btih:" + "2" * 40,
                "indexer": "First",
            }]

        async def slow_report(operation):
            if (
                operation["stage"] == "prowlarr_search"
                and operation["details"].get("allow_running_callbacks")
            ):
                report_started.set()
                try:
                    await never_release_report.wait()
                except asyncio.CancelledError:
                    report_cancelled.set()
                    raise
            return {
                "accepted": True,
                "state": operation["state"],
                "revision": operation["revision"],
            }

        self.feature.indexer_search = search_indexer
        self.host.report_operation = slow_report
        started = time.monotonic()
        try:
            with patch.object(
                self.feature,
                "_global_prowlarr_timeout",
                return_value=0.08,
            ):
                search_task = asyncio.create_task(
                    self.feature._confirm_and_search(plan_id, stored)
                )
                await asyncio.wait_for(report_started.wait(), timeout=0.2)
                result = await asyncio.wait_for(search_task, timeout=0.4)
        finally:
            release_tail.set()

        self.assertLess(time.monotonic() - started, 0.4)
        self.assertTrue(report_cancelled.is_set())
        self.assertTrue(result["actions"])
        self.assertEqual(stored["indexer_tasks"], [])
        self.assertIsNone(stored.get("wave_launcher_task"))
        self.assertIsNone(stored.get("incremental_report_task"))

    async def test_incremental_report_failure_wakes_loop_before_global_timeout(
        self,
    ):
        import time

        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        self.feature.config["search"]["prowlarr"].update({
            "first_wave_indexer_ids": [1],
            "wave_delay": 5.0,
        })
        self.feature.indexer_loader = lambda: [
            {"id": 1, "name": "First"},
            {"id": 2, "name": "Tail"},
        ]
        tail_calls = 0

        def search_indexer(_query, _media_type, indexer_id):
            nonlocal tail_calls
            if indexer_id == 2:
                tail_calls += 1
                return []
            return [{
                "title": "English.Title.2024.1080p.WEB-DL.First",
                "magnet_url": "magnet:?xt=urn:btih:" + "3" * 40,
                "indexer": "First",
            }]

        async def fail_incremental(operation):
            if (
                operation["stage"] == "prowlarr_search"
                and operation["details"].get("allow_running_callbacks")
            ):
                raise RuntimeError("incremental projection failed")
            return {
                "accepted": True,
                "state": operation["state"],
                "revision": operation["revision"],
            }

        self.feature.indexer_search = search_indexer
        self.host.report_operation = fail_incremental
        started = time.monotonic()
        raised = None
        with patch.object(
            self.feature,
            "_global_prowlarr_timeout",
            return_value=0.2,
        ):
            try:
                await self.feature._confirm_and_search(plan_id, stored)
            except RuntimeError as exc:
                raised = exc
        elapsed = time.monotonic() - started

        self.assertIsNotNone(raised)
        self.assertEqual(str(raised), "incremental projection failed")
        self.assertLess(elapsed, 0.1)
        self.assertEqual(tail_calls, 0)
        self.assertTrue(
            self.feature.operations[stored["operation_id"]].get(
                "_host_report_rejected"
            )
        )
        self.assertEqual(stored["indexer_tasks"], [])
        self.assertIsNone(stored.get("wave_launcher_task"))
        self.assertIsNone(stored.get("incremental_report_task"))

    async def test_incremental_report_and_indexer_completion_share_one_wake(
        self,
    ):
        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        self.feature.config["search"]["prowlarr"].update({
            "first_wave_indexer_ids": [1],
            "wave_delay": 0.0,
        })
        self.feature.indexer_loader = lambda: [
            {"id": 1, "name": "First"},
            {"id": 2, "name": "Tail"},
        ]
        tail_started = threading.Event()
        report_started = asyncio.Event()
        release_both = threading.Event()
        indexer_calls = []
        incremental_calls = 0

        def search_indexer(_query, _media_type, indexer_id):
            indexer_calls.append(indexer_id)
            if indexer_id == 2:
                tail_started.set()
                release_both.wait(1)
                return []
            return [{
                "title": "English.Title.2024.1080p.WEB-DL.First",
                "magnet_url": "magnet:?xt=urn:btih:" + "4" * 40,
                "indexer": "First",
            }]

        async def report(operation):
            nonlocal incremental_calls
            if (
                operation["stage"] == "prowlarr_search"
                and operation["details"].get("allow_running_callbacks")
            ):
                incremental_calls += 1
                report_started.set()
                await asyncio.to_thread(release_both.wait, 1)
            return {
                "accepted": True,
                "state": operation["state"],
                "revision": operation["revision"],
            }

        self.feature.indexer_search = search_indexer
        self.host.report_operation = report
        search_task = asyncio.create_task(
            self.feature._confirm_and_search(plan_id, stored)
        )
        try:
            await asyncio.wait_for(report_started.wait(), timeout=0.2)
            await asyncio.to_thread(tail_started.wait, 0.2)
        finally:
            release_both.set()
        result = await asyncio.wait_for(search_task, timeout=2.0)

        self.assertTrue(result["actions"])
        self.assertEqual(indexer_calls.count(1), 1)
        self.assertEqual(indexer_calls.count(2), 1)
        self.assertEqual(incremental_calls, 1)
        self.assertEqual(stored["indexer_tasks"], [])
        self.assertIsNone(stored.get("wave_launcher_task"))
        self.assertIsNone(stored.get("incremental_report_task"))

    async def test_report_only_wake_does_not_reschedule_unchanged_snapshot(
        self,
    ):
        import time

        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        self.feature.config["search"]["prowlarr"].update({
            "first_wave_indexer_ids": [1],
            "wave_delay": 5.0,
        })
        self.feature.indexer_loader = lambda: [
            {"id": 1, "name": "First"},
            {"id": 2, "name": "Tail"},
        ]
        tail_calls = 0
        incremental_calls = 0

        def search_indexer(_query, _media_type, indexer_id):
            nonlocal tail_calls
            if indexer_id == 2:
                tail_calls += 1
                return []
            return [{
                "title": "English.Title.2024.1080p.WEB-DL.First",
                "magnet_url": "magnet:?xt=urn:btih:" + "5" * 40,
                "indexer": "First",
            }]

        async def report(operation):
            nonlocal incremental_calls
            if (
                operation["stage"] == "prowlarr_search"
                and operation["details"].get("allow_running_callbacks")
            ):
                incremental_calls += 1
            return {
                "accepted": True,
                "state": operation["state"],
                "revision": operation["revision"],
            }

        self.feature.indexer_search = search_indexer
        self.host.report_operation = report
        started = time.monotonic()
        with patch.object(
            self.feature,
            "_global_prowlarr_timeout",
            return_value=0.08,
        ):
            result = await self.feature._confirm_and_search(plan_id, stored)
        elapsed = time.monotonic() - started

        self.assertTrue(result["actions"])
        self.assertGreaterEqual(elapsed, 0.06)
        self.assertLess(elapsed, 2.0)
        self.assertEqual(incremental_calls, 1)
        self.assertEqual(tail_calls, 0)
        self.assertEqual(stored["indexer_tasks"], [])
        self.assertIsNone(stored.get("wave_launcher_task"))
        self.assertIsNone(stored.get("incremental_report_task"))

    async def test_timeout_preserves_non_cancel_error_from_projection_cleanup(
        self,
    ):
        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        self.feature.config["search"]["prowlarr"].update({
            "first_wave_indexer_ids": [1],
            "wave_delay": 0.01,
        })
        self.feature.indexer_loader = lambda: [
            {"id": 1, "name": "First"},
            {"id": 2, "name": "Tail"},
        ]
        release_tail = threading.Event()
        projection_started = asyncio.Event()
        never_release_projection = asyncio.Event()

        def search_indexer(_query, _media_type, indexer_id):
            if indexer_id == 2:
                release_tail.wait(1)
                return []
            return [{
                "title": "English.Title.2024.1080p.WEB-DL.First",
                "magnet_url": "magnet:?xt=urn:btih:" + "6" * 40,
                "indexer": "First",
            }]

        async def report(operation):
            if (
                operation["stage"] == "prowlarr_search"
                and operation["details"].get("allow_running_callbacks")
            ):
                projection_started.set()
                try:
                    await never_release_projection.wait()
                except asyncio.CancelledError as exc:
                    raise RuntimeError("projection cleanup failed") from exc
            return {
                "accepted": True,
                "state": operation["state"],
                "revision": operation["revision"],
            }

        self.feature.indexer_search = search_indexer
        self.host.report_operation = report
        raised = None
        try:
            with patch.object(
                self.feature,
                "_global_prowlarr_timeout",
                return_value=0.08,
            ):
                search_task = asyncio.create_task(
                    self.feature._confirm_and_search(plan_id, stored)
                )
                await asyncio.wait_for(
                    projection_started.wait(),
                    timeout=0.2,
                )
                try:
                    await asyncio.wait_for(search_task, timeout=0.4)
                except RuntimeError as exc:
                    raised = exc
        finally:
            release_tail.set()

        self.assertIsNotNone(raised)
        self.assertEqual(str(raised), "projection cleanup failed")
        self.assertEqual(stored["indexer_tasks"], [])
        self.assertIsNone(stored.get("wave_launcher_task"))
        self.assertIsNone(stored.get("incremental_report_task"))

    async def test_gate_empty_first_wave_launches_tail_before_long_delay(self):
        import time

        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        self.feature.config["search"]["prowlarr"].update({
            "first_wave_indexer_ids": [1],
            "wave_delay": 5.0,
        })
        self.feature.indexer_loader = lambda: [
            {"id": 1, "name": "WrongIdentity"},
            {"id": 2, "name": "Tail"},
        ]
        first_started = threading.Event()
        release_first = threading.Event()
        tail_started = threading.Event()

        def search_indexer(_query, _media_type, indexer_id):
            if indexer_id == 1:
                first_started.set()
                release_first.wait(1)
                return [{
                    "title": "Different.Movie.1999.1080p",
                    "magnet_url": "magnet:?xt=urn:btih:" + "f" * 40,
                    "indexer": "WrongIdentity",
                }]
            tail_started.set()
            return []

        self.feature.indexer_search = search_indexer
        search_task = asyncio.create_task(
            self.feature._confirm_and_search(plan_id, stored)
        )
        await asyncio.to_thread(first_started.wait, 1)
        await asyncio.sleep(0.015)
        tail_started_before_first_completed = tail_started.is_set()
        released_at = time.monotonic()
        release_first.set()
        await asyncio.to_thread(tail_started.wait, 0.4)
        await search_task

        self.assertFalse(tail_started_before_first_completed)
        self.assertTrue(tail_started.is_set())
        self.assertLess(time.monotonic() - released_at, 0.5)
        self.assertEqual(stored.get("results"), [])

    async def test_first_wave_incremental_selection_cancels_unstarted_tail(self):
        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        self.feature.config["search"]["prowlarr"].update({
            "first_wave_indexer_ids": [1],
            "wave_delay": 0.3,
        })
        self.feature.indexer_loader = lambda: [
            {"id": 1, "name": "First"},
            {"id": 2, "name": "Tail"},
        ]
        tail_started = threading.Event()

        def search_indexer(_query, _media_type, indexer_id):
            if indexer_id == 2:
                tail_started.set()
                return []
            return [{
                "title": "English.Title.2024.1080p.WEB-DL.First",
                "magnet_url": "magnet:?xt=urn:btih:" + "1" * 40,
                "indexer": "First",
            }]

        self.feature.indexer_search = search_indexer
        search_task = asyncio.create_task(
            self.feature._confirm_and_search(plan_id, stored)
        )
        self.feature.operations[stored["operation_id"]]["task"] = search_task

        partial = []
        for _attempt in range(100):
            partial = [
                report for report in self.host.reports
                if report["stage"] == "prowlarr_search"
                and report["details"].get("allow_running_callbacks") is True
                and report["details"].get("keyboard")
            ]
            if partial:
                break
            await asyncio.sleep(0.005)

        self.assertTrue(partial)
        callback = partial[-1]["details"]["keyboard"][0][0][
            "callback_data"
        ]
        release_id = callback.rsplit(":", 1)[-1]
        self.assertIn(release_id, stored["release_by_id"])
        tail_started_before_selection = tail_started.is_set()

        self.feature._start_submission_task(plan_id, stored, release_id)
        await asyncio.gather(search_task, return_exceptions=True)
        await asyncio.sleep(0.05)

        self.assertFalse(tail_started_before_selection)
        self.assertFalse(tail_started.is_set())
        self.assertTrue(stored["selection_frozen"])
        self.assertEqual(stored["selected_release_id"], release_id)
        self.assertEqual(stored["indexer_tasks"], [])
        self.assertIsNone(stored.get("wave_launcher_task"))
        self.assertIsNone(stored.get("incremental_report_task"))

    async def test_global_timeout_accounts_for_unstarted_tail_once(self):
        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        self.feature.config["search"]["prowlarr"].update({
            "first_wave_indexer_ids": [1],
            "wave_delay": 5.0,
            "timeout": 1.0,
        })
        self.feature.indexer_loader = lambda: [
            {"id": 1, "name": "First"},
            {"id": 2, "name": "Tail"},
        ]
        release_first = threading.Event()
        tail_started = threading.Event()

        def search_indexer(_query, _media_type, indexer_id):
            if indexer_id == 2:
                tail_started.set()
                return []
            release_first.wait(2)
            return []

        self.feature.indexer_search = search_indexer
        try:
            await self.feature._confirm_and_search(plan_id, stored)
        finally:
            release_first.set()

        summary = stored["indexer_summary"]
        self.assertFalse(tail_started.is_set())
        self.assertEqual(summary["completed_indexers"], 2)
        self.assertEqual(summary["total_indexers"], 2)
        self.assertTrue(summary["final"])
        self.assertEqual(
            [item["source"] for item in summary["down_indexers"]],
            ["First", "Tail"],
        )
        self.assertTrue(all(
            item["kind"] == "timeout"
            for item in summary["down_indexers"]
        ))
        self.assertEqual(stored["indexer_tasks"], [])
        self.assertIsNone(stored.get("wave_launcher_task"))
        self.assertIsNone(stored.get("incremental_report_task"))

    async def test_truthy_malformed_indexer_list_uses_aggregate_fallback(self):
        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        self.feature.indexer_loader = lambda: [
            {},
            {"id": 0, "name": "zero"},
            {"id": "invalid", "name": "bad"},
        ]
        self.feature.indexer_search = Mock(
            side_effect=AssertionError("per-indexer search must not run")
        )
        aggregate = Mock(return_value=[{
            "title": "English.Title.2024.1080p.WEB-DL.Aggregate",
            "magnet_url": "magnet:?xt=urn:btih:" + "a" * 40,
            "indexer": "Aggregate",
        }])
        self.feature.release_search = aggregate
        self.feature.indexer_summary = lambda _items: {}

        result = await self.feature._confirm_and_search(plan_id, stored)

        self.assertTrue(stored["results"])
        self.assertTrue(result["actions"][0]["data"]["keyboard"])
        self.assertGreaterEqual(aggregate.call_count, 1)
        self.feature.indexer_search.assert_not_called()

    async def test_no_optional_enrichment_can_mutate_confirmed_v2_during_indexer_search(self):
        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        stored["selected_candidate"] = deepcopy(
            ranked_search_plan()["candidates"][0]
        )
        self.feature.indexer_loader = lambda: [{"id": 1, "name": "Fast"}]
        self.feature.indexer_search = lambda *_args: [{
            "title": "English.Title.2024.1080p.WEB-DL",
            "magnet_url": "magnet:?xt=urn:btih:" + "d" * 40,
            "indexer": "Fast",
        }]
        optional = AsyncMock(side_effect=AssertionError(
            "post-confirmation enrichment must not run"
        ))
        self.feature._supplement_selected_candidate = optional

        result = await self.feature._confirm_and_search(plan_id, stored)

        self.assertTrue(result["actions"])
        self.assertNotIn("deferred_contract", stored)
        self.assertEqual(stored["confirmed_contract"]["schema_version"], 2)
        optional.assert_not_awaited()

    async def test_aggregate_search_does_not_start_post_confirmation_enrichment(self):
        plan_id = await self._prepare_search()
        stored = self.feature.plans[plan_id]
        stored["selected_candidate"] = deepcopy(
            ranked_search_plan()["candidates"][0]
        )
        self.feature.indexer_loader = lambda: []
        def aggregate_search(_query, _media_type):
            return [{
                "title": "English.Title.2024.1080p.WEB-DL",
                "magnet_url": "magnet:?xt=urn:btih:" + "e" * 40,
                "indexer": "Aggregate",
            }]

        self.feature.release_search = aggregate_search
        optional = AsyncMock(side_effect=AssertionError(
            "post-confirmation enrichment must not run"
        ))
        self.feature._supplement_selected_candidate = optional

        result = await self.feature._confirm_and_search(
            plan_id,
            stored,
        )

        self.assertTrue(result["actions"])
        self.assertNotIn("deferred_enrichment_task", stored)
        optional.assert_not_awaited()

    async def test_restarted_deferred_enrichment_discards_previous_result(self):
        plan_id = "deferred-restart"
        release_optional = asyncio.Event()
        completed = asyncio.create_task(asyncio.sleep(0))
        await completed
        contract = deepcopy(search_plan()["media_metadata"])
        stored = {
            "plan": {"plan_id": plan_id, "raw_query": "English Title"},
            "selected_candidate": {
                "media_metadata": deepcopy(contract),
            },
            "confirmed_contract": contract,
            "deferred_contract": {"stale": True},
            "deferred_enrichment_task": completed,
        }
        self.feature.plans[plan_id] = stored

        async def slow_optional(candidate, _raw_query, *, purpose="all"):
            self.assertEqual(purpose, "presentation")
            await release_optional.wait()
            return deepcopy(candidate)

        self.feature._supplement_selected_candidate = slow_optional

        self.feature._start_deferred_presentation_enrichment(plan_id, stored)

        self.assertNotIn("deferred_contract", stored)
        release_optional.set()
        await stored["deferred_enrichment_task"]

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
        from telepiplex_search.errors import SearchPlanningError

        async def blocked(_raw_query, _plan_id):
            raise SearchPlanningError(
                "ambiguous_candidates",
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
        self.assertNotIn("AI", self.host.reports[-1]["status_text"])
        self.assertEqual(self.host.reports[-1]["state"], "failed")
        self.assertEqual(self.feature.plans, {})
        self.assertEqual(self.search_queries, [])

    async def test_candidate_binding_failure_logs_correlation_without_raw_query(self):
        from telepiplex_search.context import runtime_context
        from telepiplex_search.errors import SearchPlanningError

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
        from telepiplex_search.errors import SearchPlanningError

        for code in (
            "source_failure",
            "source_rate_limited",
            "source_fact_conflict",
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
        from telepiplex_search.errors import SearchPlanningError

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
        self.assertIn("黑暗荣耀 (The Glory)", result["actions"][0]["text"])

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
        self.assertIn("黑暗荣耀 (The Glory)", result["actions"][0]["text"])

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
        self.assertEqual(payload["media_metadata"]["schema_version"], 2)
        self.assertTrue(payload["media_metadata"]["confirmed"])
        self.assertEqual(
            payload["media_metadata"]["identity"]["title_zh"],
            "中文标题",
        )
        self.assertNotIn("naming_metadata", payload)
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

    async def test_submit_release_uses_only_completed_deferred_contract(self):
        plan_id = await self._prepare_search()
        await self.feature.callback({
            "namespace": "search", "payload": f"confirm:{plan_id}",
            "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("search-releases-")
        stored = self.feature.plans[plan_id]
        release_id = next(iter(stored["release_by_id"]))
        frozen = deepcopy(stored["confirmed_contract"])
        deferred = deepcopy(frozen)
        deferred["identity"]["title_zh"] = "不应进入下游"
        completed = asyncio.create_task(asyncio.sleep(0))
        await completed
        stored["deferred_contract"] = deferred
        stored["deferred_enrichment_task"] = completed

        selected_title = stored["release_by_id"][release_id]["title"]
        await self.feature._submit_release(
            plan_id,
            stored,
            release_id,
            stored["operation_id"],
        )

        payload = self.host.calls[-1][2]
        self.assertEqual(payload["media_metadata"], frozen)
        self.assertEqual(payload["release"]["title"], selected_title)

    async def test_submit_release_never_waits_for_running_deferred_contract(self):
        plan_id = await self._prepare_search()
        await self.feature.callback({
            "namespace": "search", "payload": f"confirm:{plan_id}",
            "user_id": 1, "chat_id": 10,
        })
        await self.runtime.run("search-releases-")
        stored = self.feature.plans[plan_id]
        release_id = next(iter(stored["release_by_id"]))
        release_deferred = asyncio.Event()
        running = asyncio.create_task(release_deferred.wait())
        partial = deepcopy(stored["confirmed_contract"])
        partial["identity"]["poster_url"] = (
            "https://image.example/partial.jpg"
        )
        stored["deferred_contract"] = partial
        stored["deferred_enrichment_task"] = running

        await asyncio.wait_for(
            self.feature._submit_release(
                plan_id,
                stored,
                release_id,
                stored["operation_id"],
            ),
            timeout=0.1,
        )

        payload = self.host.calls[-1][2]
        self.assertNotEqual(
            payload["media_metadata"]["identity"].get("poster_url"),
            "https://image.example/partial.jpg",
        )
        release_deferred.set()
        await asyncio.gather(running, return_exceptions=True)

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

    async def test_search_stage_seals_before_download_handoff(self):
        plan_id = await self._prepare_search()
        await self.feature.callback({
            "namespace": "search",
            "payload": f"confirm:{plan_id}",
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-releases-")
        stored = self.feature.plans[plan_id]
        release_id = next(iter(stored["release_by_id"]))
        self.host.timeline.clear()
        original_seal = self.host.seal_operation_segment

        async def queue_before_later_delivery_failure(*args, **kwargs):
            response = await original_seal(*args, **kwargs)
            return {
                **response,
                "queued": True,
                "delivery_state": "failed",
            }

        self.host.seal_operation_segment = queue_before_later_delivery_failure

        await self.feature._submit_release(
            plan_id,
            stored,
            release_id,
            stored["operation_id"],
        )

        seal_index = next(
            index for index, item in enumerate(self.host.timeline)
            if item[:2] == ("segment_sealed", "search")
        )
        handoff_index = self.host.timeline.index(
            ("report", "handed_off", "submitting_download")
        )
        capability_index = next(
            index for index, item in enumerate(self.host.timeline)
            if item[:2] == ("capability", "download.provider")
        )
        self.assertLess(seal_index, handoff_index)
        self.assertLess(handoff_index, capability_index)
        self.assertEqual(len(self.host.calls), 1)

    async def test_lost_search_segment_seal_response_retries_same_role(self):
        from telepiplex_plugin_sdk import FeatureError

        plan_id = await self._prepare_search()
        await self.feature.callback({
            "namespace": "search",
            "payload": f"confirm:{plan_id}",
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-releases-")
        stored = self.feature.plans[plan_id]
        release_id = next(iter(stored["release_by_id"]))
        original_seal = self.host.seal_operation_segment
        attempts = []

        async def accept_then_lose(
            operation_id,
            role,
            *,
            deadline=10,
        ):
            attempts.append(role)
            response = await original_seal(
                operation_id,
                role,
                deadline=deadline,
            )
            if len(attempts) == 1:
                raise FeatureError(
                    "internal_error",
                    "Host milestone bookkeeping was interrupted",
                )
            return {**response, "accepted": False, "duplicate": True}

        self.host.seal_operation_segment = accept_then_lose

        result = await self.feature._submit_release(
            plan_id,
            stored,
            release_id,
            stored["operation_id"],
        )

        self.assertEqual(attempts, [attempts[0], attempts[0]])
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

    async def test_missing_candidate_posters_are_supplemented_in_parallel(self):
        plan = ranked_search_plan()
        for candidate in plan["candidates"]:
            candidate["poster_url"] = ""
            candidate["media_metadata"]["identity"]["poster_url"] = ""
        stored = {
            "candidates": tuple(deepcopy(plan["candidates"])),
            "plan": {"plan_id": "poster-parallel"},
        }
        started = set()
        all_started = asyncio.Event()

        async def lookup(candidate, provider):
            key = (candidate["candidate_key"], provider)
            started.add(key)
            if len(started) == 6:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), timeout=0.5)
            if provider == "tmdb":
                return f"https://image.example/{candidate['candidate_key']}.jpg"
            return ""

        self.feature.candidate_poster_lookup = lookup

        await self.feature._supplement_candidate_posters(stored)

        self.assertEqual(len(started), 6)
        self.assertTrue(all(
            candidate["poster_url"].startswith("https://image.example/")
            for candidate in stored["candidates"]
        ))

    async def test_candidate_poster_supplement_timeout_keeps_placeholder(self):
        plan = ranked_search_plan()
        candidate = plan["candidates"][0]
        candidate["poster_url"] = ""
        candidate["media_metadata"]["identity"]["poster_url"] = ""
        stored = {
            "candidates": (deepcopy(candidate),),
            "plan": {"plan_id": "poster-timeout"},
        }

        async def slow_lookup(_candidate, _provider):
            await asyncio.Event().wait()

        self.feature.candidate_poster_lookup = slow_lookup
        self.feature.candidate_poster_timeout = 0.01

        await self.feature._supplement_candidate_posters(stored)

        self.assertEqual(stored["candidates"][0]["poster_url"], "")

    async def test_prepare_plan_bounds_poster_lookup_before_single_candidate_report(self):
        poster_started = asyncio.Event()

        async def planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            for candidate in result["candidates"]:
                candidate["poster_url"] = ""
                candidate["media_metadata"]["identity"]["poster_url"] = ""
            return result

        async def slow_lookup(_candidate, _provider):
            poster_started.set()
            await asyncio.Event().wait()

        self.feature.plan_builder = planner
        self.feature.candidate_poster_lookup = slow_lookup
        self.feature.candidate_poster_timeout = 0.01
        operation = self.feature._new_operation(
            {"chat_id": 10, "user_id": 1},
            state="running",
            stage="planning",
            status_text="正在规划。",
            control="cancel",
            kind="search",
        )
        prepare = asyncio.create_task(self.feature._prepare_plan(
            "候选",
            {"chat_id": 10, "user_id": 1},
            plan_id="poster-critical-path",
            operation_id=operation["operation_id"],
        ))
        await poster_started.wait()

        result = await asyncio.wait_for(asyncio.shield(prepare), timeout=0.2)

        action = result["actions"][0]
        self.assertIn("中文标题1", action["text"])
        self.assertIn("2024", action["text"])
        self.assertIn("电影", action["text"])
        self.assertTrue(action["data"]["keyboard"])
        self.assertNotIn(
            "candidate_poster_task",
            self.feature.plans["poster-critical-path"],
        )

    async def test_candidate_posters_are_in_the_single_initial_host_report(self):
        async def planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result.update({
                "plan_id": plan_id,
                "raw_query": "候选",
                "links_frozen": True,
                "auto_confirm": False,
            })
            for index, candidate in enumerate(result["candidates"], 1):
                candidate.update({
                    "candidate_id": candidate["candidate_key"],
                    "links_frozen": True,
                    "source_links": [{
                        "provider": "douban",
                        "fact_id": f"douban:{index}",
                        "url": (
                            "https://movie.douban.com/subject/"
                            f"{index}/"
                        ),
                    }],
                    "poster_url": "",
                })
                candidate["media_metadata"]["identity"]["poster_url"] = ""
            return result

        async def lookup(_candidate, provider):
            if provider == "tmdb":
                return "https://image.example/reversed-race.jpg"
            return ""

        self.feature.plan_builder = planner
        self.feature.candidate_poster_lookup = lookup
        command = await self.feature.command({
            "command": "s",
            "args": ["候选"],
            "user_id": 1,
            "chat_id": 10,
        })
        operation_id = command["operation"]["operation_id"]
        await self.runtime.run("search-plan-")
        active_plan_ids = tuple(self.feature.plans)

        self.assertEqual(len(self.host.reports), 1)
        self.assertEqual(self.host.reports[0]["stage"], "candidate_selection")
        self.assertTrue(all(
            item["poster_url"] == "https://image.example/reversed-race.jpg"
            for item in self.feature.plans[active_plan_ids[0]]["candidates"]
        ))
        self.assertEqual(len(active_plan_ids), 1)
        self.assertIn(active_plan_ids[0], self.feature.plans)
        self.assertEqual(
            self.feature.operations[operation_id]["state"],
            "awaiting_input",
        )

    async def test_normal_plan_task_reports_candidates_once_after_poster_lookup(self):
        async def planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            for candidate in result["candidates"]:
                candidate["poster_url"] = ""
                candidate["media_metadata"]["identity"]["poster_url"] = ""
            return result

        async def lookup(_candidate, provider):
            return (
                "https://image.example/normal-plan.jpg"
                if provider == "tmdb"
                else ""
            )

        self.feature.plan_builder = planner
        self.feature.candidate_poster_lookup = lookup
        await self.feature.command({
            "command": "s",
            "args": ["候选"],
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")

        self.assertEqual(len(self.host.reports), 1)
        self.assertEqual(
            [report["stage"] for report in self.host.reports],
            ["plan_confirmation"],
        )
        self.assertEqual(
            self.host.reports[-1]["details"]["photo_url"],
            "https://image.example/normal-plan.jpg",
        )

    async def test_candidate_poster_refresh_report_failure_is_ignored(self):
        candidate = ranked_search_plan()["candidates"][0]
        candidate["poster_url"] = ""
        candidate["media_metadata"]["identity"]["poster_url"] = ""
        operation = self.feature._new_operation(
            {"chat_id": 10, "user_id": 1},
            state="awaiting_input",
            stage="candidate_selection",
            status_text="等待选择。",
            control="exit",
            kind="search",
        )
        stored = {
            "plan": {
                "plan_id": "poster-report-failure",
                "links_frozen": False,
            },
            "candidates": (candidate,),
            "operation_id": operation["operation_id"],
        }
        self.feature.plans["poster-report-failure"] = stored
        self.feature.candidate_poster_lookup = AsyncMock(
            return_value="https://image.example/background.jpg"
        )
        self.host.report_operation = AsyncMock(
            side_effect=RuntimeError("projection unavailable")
        )

        self.feature._start_candidate_poster_enrichment(
            "poster-report-failure",
            stored,
        )
        await stored["candidate_poster_task"]

        self.assertEqual(
            stored["candidates"][0]["poster_url"],
            "https://image.example/background.jpg",
        )

    async def test_candidate_poster_lookup_uses_all_three_existing_adapters(self):
        candidate = deepcopy(ranked_search_plan()["candidates"][0])
        candidate["poster_url"] = ""
        candidate["media_metadata"]["identity"]["poster_url"] = ""
        tmdb = AsyncMock(return_value=({
            "cover_url": "https://image.example/tmdb.jpg",
        }, "ok"))
        douban_result = {
            "status": "ok",
            "facts": [{
                "subject_id": "1",
                "title": "English Title",
                "year": "2024",
                "media_type": "movie",
                "cover_url": "https://image.example/douban.jpg",
            }],
        }
        tvdb_result = [{
            "tvdb_id": "1",
            "name": "English Title",
            "year": "2024",
            "media_type": "movie",
            "cover_url": "https://image.example/tvdb.jpg",
        }]

        with patch.object(
            self.feature,
            "_resolve_confirmed_tmdb",
            tmdb,
        ), patch.object(
            self.feature,
            "_douban_provider",
            return_value=douban_result,
        ), patch(
            "telepiplex_search.service.search_tvdb_movies",
            return_value=tvdb_result,
        ):
            urls = [
                await self.feature._lookup_candidate_poster(
                    candidate,
                    provider,
                )
                for provider in ("tmdb", "douban", "tvdb")
            ]

        self.assertEqual(urls, [
            "https://image.example/tmdb.jpg",
            "https://image.example/douban.jpg",
            "https://image.example/tvdb.jpg",
        ])

    def test_candidate_grid_only_uses_animation_label_with_positive_evidence(self):
        plan = series_ranked_search_plan()
        generic = deepcopy(plan["candidates"][0])
        generic["media_metadata"]["placement"]["category_kind"] = (
            "live_action_series"
        )
        generic["media_metadata"]["identity"]["genres"] = []
        animated = deepcopy(generic)
        animated["candidate_key"] = "animated-series"
        animated["media_metadata"]["identity"]["genres"] = ["Animation"]

        action = self.feature._candidate_grid_action({
            "candidates": [generic, animated],
            "plan": {"plan_id": "type-labels"},
        })

        visible = html.unescape(re.sub(r"<[^>]+>", "", action["text"]))
        self.assertEqual(visible.count("类型：剧集"), 1)
        self.assertEqual(visible.count("类型：动画剧集"), 1)

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
        self.assertIn("① 想见你 (想見你) 2019", visible)
        self.assertIn("② 让子弹飞 2010", visible)
        self.assertNotIn("Someday or One Day", visible)
        self.assertNotIn("Let the Bullets Fly", visible)
        self.assertEqual(
            [
                row[0]["text"]
                for row in action["data"]["keyboard"][:2]
            ],
            [
                "① 想见你 (想見你) 2019",
                "② 让子弹飞 2010",
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
        self.assertIn("① 让子弹飞 2010", visible)
        self.assertNotIn("让子弹飞 (", visible)
        self.assertEqual(
            action["data"]["keyboard"][0][0]["text"],
            "① 让子弹飞 2010",
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

    def test_candidate_grid_paginates_all_candidates_and_hides_internal_fields(self):
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
        self.assertEqual(
            action["text"].count("来源：维基百科、豆瓣、TVDB"),
            5,
        )
        self.assertNotIn("<a ", action["text"])
        self.assertNotIn("维基百科暂时不可用", action["text"])
        self.assertNotIn("wikipedia:server_down", action["text"])
        self.assertNotIn("匹配参考", action["text"])
        self.assertEqual(
            action["data"]["keyboard"][-2][0]["text"],
            "下一页",
        )
        self.assertEqual(
            action["data"]["keyboard"][-2][0]["callback_data"],
            "search:candidate_page:caption-limit:1",
        )
        self.assertEqual(
            action["data"]["keyboard"][-1][0]["text"],
            "都不是",
        )

        second_page = self.feature._candidate_grid_action({
            "candidates": candidates,
            "plan": {"plan_id": "caption-limit"},
        }, page=1)
        self.assertEqual(len(second_page["data"]["poster_items"]), 1)
        self.assertIn("⑥ <b>候选标题 6", second_page["text"])
        self.assertEqual(
            second_page["data"]["keyboard"][-2][0]["text"],
            "上一页",
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

    def test_single_candidate_without_remote_poster_uses_grid_placeholder(self):
        plan = ranked_search_plan()
        candidate = plan["candidates"][0]
        candidate["poster_url"] = ""
        candidate["media_metadata"]["identity"]["poster_url"] = ""

        action = self.feature._candidate_grid_action({
            "candidates": [candidate],
            "plan": {"plan_id": "placeholder"},
        })

        self.assertEqual(action["kind"], "send_photo_grid")
        self.assertEqual(
            action["data"]["poster_items"][0]["poster_url"],
            "",
        )

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
            ["① 中文标题1 2024", "都不是"],
        )

    @patch("telepiplex_search.service.hydrate_frozen_candidate")
    @patch("telepiplex_search.service.hydrate_frozen_candidate_anchor")
    async def test_unverified_series_does_not_continue_as_whole_series(
        self,
        hydrate_anchor,
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
        hydrate_anchor.return_value = deepcopy(candidate)
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

        self.assertTrue(result.get("actions"))
        self.assertIn(
            "无法验证该剧集的季集范围",
            result["actions"][0]["text"],
        )
        self.assertEqual(
            stored["plan"]["media_metadata"]["retrieval"]["scope"],
            "whole_series",
        )
        self.assertEqual(
            stored["plan"]["media_metadata"]["items"],
            [],
        )

    @patch("telepiplex_search.service.hydrate_frozen_candidate_anchor")
    async def test_tvdb_unavailable_season_never_degrades_to_whole_series(
        self,
        hydrate,
    ):
        from telepiplex_search.candidate_hydration import (
            CandidateHydrationError,
        )
        from telepiplex_search.series_scope import apply_series_scope

        plan_id = "season-verification-required"
        plan = series_ranked_search_plan()
        plan.update({
            "plan_id": plan_id,
            "raw_query": "副总统 第一季",
            "links_frozen": True,
            "auto_confirm": False,
        })
        candidate = plan["candidates"][0]
        candidate.update({
            "links_frozen": True,
            "identity_role": "season",
            "intended_scope": "season",
            "requested_season_number": 1,
            "source_links": [{
                "provider": "douban",
                "fact_id": "douban:5379824",
                "url": "https://movie.douban.com/subject/5379824/",
                "external_ids": {"douban_subject": "5379824"},
                "role": "season",
                "season_number": None,
                "episode_number": None,
                "verification": "unresolved_scope_link",
                "proposed_season_number": 1,
                "proposed_episode_number": None,
            }, {
                "provider": "tvdb",
                "fact_id": "tvdb:series:75978",
                "url": "https://thetvdb.com/series/veep",
                "external_ids": {"tvdb": "75978"},
                "role": "series_root",
                "season_number": None,
                "episode_number": None,
                "verification": "fact_verified",
                "proposed_season_number": None,
                "proposed_episode_number": None,
            }],
        })
        candidate["media_metadata"] = apply_series_scope(
            candidate["media_metadata"],
            "season",
            season_number=1,
        )
        self.feature.selected_candidate_supplementer = AsyncMock(
            return_value=deepcopy(candidate)
        )
        hydrate.side_effect = CandidateHydrationError(
            "metadata_incomplete",
            ("verified_scope",),
        )
        operation = self.feature._new_operation(
            {"chat_id": 10, "user_id": 1},
            state="awaiting_input",
            stage="candidate_selection",
            status_text="等待选择。",
            control="exit",
            kind="search",
        )

        result = await self.feature._select_candidate(
            plan_id,
            {
                "plan": plan,
                "candidates": (candidate,),
                "selected_path": "",
                "operation_id": operation["operation_id"],
            },
            "0",
        )

        self.assertEqual(hydrate.call_count, 1)
        self.assertIn("已验证季集范围", result["actions"][0]["text"])
        self.assertNotIn("operation", result)

    @patch("telepiplex_search.service.hydrate_frozen_candidate")
    @patch("telepiplex_search.service.hydrate_frozen_candidate_anchor")
    async def test_wikipedia_bounded_season_without_inventory_offers_no_aggregate(
        self,
        hydrate_anchor,
        hydrate,
    ):
        plan_id = "wikipedia-bounded-season"
        plan = series_ranked_search_plan()
        plan.update({
            "plan_id": plan_id,
            "raw_query": "Veep S01",
            "links_frozen": True,
            "auto_confirm": False,
        })
        candidate = plan["candidates"][0]
        candidate["links_frozen"] = True
        contract = candidate["media_metadata"]
        contract["identity"]["season_count"] = 7
        contract["items"] = []
        contract["retrieval"] = {
            "media_type": "series",
            "scope": "season",
            "query": "Veep S01",
            "queries": ["Veep S01", "Veep Season 01"],
        }
        contract["evidence"]["decision"].update({
            "scope": "season",
            "season_number": 1,
            "episode_number": None,
        })
        contract["evidence"]["series_inventory"] = {
            "season_totals": {},
        }
        contract["warnings"] = ["warning:episode_inventory_unavailable"]
        hydrate_anchor.return_value = deepcopy(candidate)
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

        result = await self.feature._select_candidate(plan_id, stored, "0")

        self.assertEqual(result["operation"]["stage"], "series_scope")
        self.assertIn("尚未确认完整集数", result["actions"][0]["text"])
        labels = [
            button["text"]
            for row in result["actions"][0]["data"]["keyboard"]
            for button in row
        ]
        self.assertEqual(labels, ["返回"])

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

        identity_report = next(
            report for report in self.host.reports
            if report["stage"] == "identity_confirmation"
        )
        self.assertIn(
            "中文标题1 (English Title)",
            identity_report["status_text"],
        )
        self.assertEqual(self.host.segments_sealed[0]["role"], "identity")
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

    async def test_selected_candidate_hydrates_anchor_before_authoritative_enrichment(
        self,
    ):
        from telepiplex_search.direct_link import DirectEntity

        events = []

        candidate = deepcopy(series_ranked_search_plan()["candidates"][0])
        candidate.update({
            "candidate_id": "douban_subject:1919245",
            "candidate_key": "douban_subject:1919245",
            "anchor_fact_id": "douban:1919245",
            "identity_role": "season",
            "intended_scope": "season",
            "requested_season_number": 1,
            "links_frozen": True,
            "unresolved_sources": [
                "douban:1919245:unresolved_scope_link",
            ],
            "source_links": [{
                "provider": "douban",
                "fact_id": "douban:1919245",
                "url": "https://movie.douban.com/subject/1919245/",
                "external_ids": {"douban_subject": "1919245"},
                "role": "season",
                "season_number": None,
                "episode_number": None,
                "verification": "unresolved_scope_link",
                "proposed_season_number": 1,
                "proposed_episode_number": None,
            }],
        })

        def exact_resolver(link):
            events.append(("exact", link.provider))
            if link.provider == "douban":
                fact = {
                    "subject_id": "1919245",
                    "title": "冰菓",
                    "chinese_title": "冰果",
                    "official_english_title": "Hyouka",
                    "original_title": "氷菓",
                    "romanized_original_title": "Hyouka",
                    "original_language": "ja",
                    "year": "2012",
                    "media_type": "series",
                    "url": link.url,
                }
                stable = ("douban_subject", "1919245")
            else:
                fact = {
                    "movies": [],
                    "series": [{
                        "tvdb_series_id": "239911",
                        "name": "Hyouka",
                        "chinese_title": "冰果",
                        "official_english_title": "Hyouka",
                        "original_title": "氷菓",
                        "romanized_original_title": "Hyouka",
                        "original_language": "ja",
                        "year": "2012",
                        "url": link.url,
                    }],
                    "episodes_by_series": {
                        "239911": [{
                            "tvdb_episode_id": "s1e1",
                            "season_number": 1,
                            "episode_number": 1,
                        }],
                    },
                }
                stable = ("tvdb", "239911")
            return DirectEntity(
                provider=link.provider,
                evidence={
                    "source": link.provider,
                    "status": "ok",
                    "facts": [fact],
                    "source_urls": [link.url],
                },
                stable_identity=stable,
                title="Hyouka",
                year="2012",
                media_type="series",
                scope="work",
            )

        async def supplement(candidate, raw_query):
            self.assertEqual(raw_query, "冰果 第一季")
            self.assertTrue(candidate.get("anchor_hydrated"))
            self.assertFalse(candidate.get("metadata_hydrated"))
            self.assertEqual(
                candidate["media_metadata"]["identity"]["english_title"],
                "Hyouka",
            )
            events.append(("authoritative", "scope"))
            enriched = deepcopy(candidate)
            enriched["source_links"].append({
                "provider": "tvdb",
                "fact_id": "tvdb:series:239911",
                "url": "https://thetvdb.com/series/hyouka",
                "external_ids": {"tvdb": "239911"},
                "role": "series_root",
                "season_number": None,
                "episode_number": None,
                "verification": "fact_verified",
                "proposed_season_number": None,
                "proposed_episode_number": None,
            })
            return enriched

        self.feature.selected_candidate_supplementer = supplement
        self.feature.exact_link_resolver = exact_resolver

        hydrated = await self.feature._hydrate_selected_candidate(
            candidate,
            metadata_id="selected-supplement",
            raw_query="冰果 第一季",
            require_anchor=True,
        )

        self.assertTrue(hydrated["metadata_hydrated"])
        self.assertEqual(
            hydrated["media_metadata"]["evidence"]["decision"]["season_number"],
            1,
        )
        self.assertEqual(
            events,
            [
                ("exact", "douban"),
                ("authoritative", "scope"),
                ("exact", "tvdb"),
            ],
        )

    async def test_concurrent_hydration_completes_with_saturated_default_executor(
        self,
    ):
        from concurrent.futures import ThreadPoolExecutor

        from telepiplex_search.direct_link import DirectEntity

        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=2))
        candidate = frozen_douban_movie_candidate()
        raw_calls = 0

        def exact_resolver(link):
            nonlocal raw_calls
            raw_calls += 1
            fact = {
                "subject_id": "1",
                "title": "English Title",
                "chinese_title": "中文标题1",
                "official_english_title": "English Title",
                "original_title": "English Title",
                "original_language": "en",
                "year": "2024",
                "media_type": "movie",
                "url": link.url,
            }
            return DirectEntity(
                provider="douban",
                evidence={
                    "source": "douban",
                    "status": "ok",
                    "facts": [fact],
                    "source_urls": [link.url],
                },
                stable_identity=("douban_subject", "1"),
                title="English Title",
                year="2024",
                media_type="movie",
                scope="work",
            )

        self.feature.exact_link_resolver = exact_resolver
        tasks = [
            asyncio.create_task(self.feature._hydrate_selected_candidate(
                deepcopy(candidate),
                metadata_id=f"saturated-{index}",
                raw_query="English Title 2024",
                require_anchor=True,
            ))
            for index in range(2)
        ]
        timed_out = False
        results = []
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=0.5,
            )
        except TimeoutError:
            timed_out = True
        finally:
            for flight in list(self.feature.source_scheduler._flights.values()):
                flight.cancel()
            await asyncio.gather(
                *self.feature.source_scheduler._flights.values(),
                return_exceptions=True,
            )
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        self.assertFalse(timed_out)
        self.assertEqual(raw_calls, 1)
        self.assertEqual(
            {
                result["media_metadata"]["metadata_id"]
                for result in results
            },
            {"saturated-0", "saturated-1"},
        )

    async def test_poster_raw_fetch_is_single_flight_but_selector_reruns(self):
        candidate = deepcopy(ranked_search_plan()["candidates"][0])
        candidate["poster_url"] = ""
        candidate["media_metadata"]["identity"]["poster_url"] = ""
        started = threading.Event()
        release = threading.Event()
        calls = 0
        result = {
            "source": "douban",
            "status": "ok",
            "facts": [{
                "subject_id": "1",
                "title": "English Title",
                "year": "2024",
                "media_type": "movie",
                "cover_url": "https://image.example/douban.jpg",
            }],
        }

        def provider(_hypotheses):
            nonlocal calls
            calls += 1
            started.set()
            release.wait(2)
            return deepcopy(result)

        selector = self.feature._select_unique_douban_poster_fact
        with patch.object(
            self.feature,
            "_douban_provider",
            side_effect=provider,
        ), patch.object(
            self.feature,
            "_select_unique_douban_poster_fact",
            wraps=selector,
        ) as select:
            first = asyncio.create_task(
                self.feature._lookup_candidate_poster(candidate, "douban")
            )
            second = asyncio.create_task(
                self.feature._lookup_candidate_poster(candidate, "douban")
            )
            await asyncio.to_thread(started.wait, 1)
            release.set()
            urls = await asyncio.gather(first, second)

        self.assertEqual(calls, 1)
        self.assertEqual(
            select.call_count,
            3,
            "one cache-admission selection plus one caller selection each",
        )
        self.assertEqual(urls, [
            "https://image.example/douban.jpg",
            "https://image.example/douban.jpg",
        ])

    async def test_poster_unavailable_result_is_not_negative_cached(self):
        candidate = deepcopy(ranked_search_plan()["candidates"][0])
        calls = 0

        def unavailable(_hypotheses):
            nonlocal calls
            calls += 1
            return {
                "source": "douban",
                "status": "unavailable",
                "facts": [],
            }

        with patch.object(
            self.feature,
            "_douban_provider",
            side_effect=unavailable,
        ):
            self.assertEqual(
                await self.feature._lookup_candidate_poster(
                    candidate,
                    "douban",
                ),
                "",
            )
            self.assertEqual(
                await self.feature._lookup_candidate_poster(
                    candidate,
                    "douban",
                ),
                "",
            )

        self.assertEqual(calls, 2)

        empty_cover_calls = 0

        def empty_cover(_hypotheses):
            nonlocal empty_cover_calls
            empty_cover_calls += 1
            return {
                "source": "douban",
                "status": "ok",
                "facts": [{
                    "subject_id": "1",
                    "title": "English Title",
                    "year": "2024",
                    "media_type": "movie",
                    "cover_url": "",
                }],
            }

        with patch.object(
            self.feature,
            "_douban_provider",
            side_effect=empty_cover,
        ):
            for _attempt in range(2):
                self.assertEqual(
                    await self.feature._lookup_candidate_poster(
                        candidate,
                        "douban",
                    ),
                    "",
                )

        self.assertEqual(empty_cover_calls, 2)

    async def test_exact_identity_mismatch_is_refetched_before_strict_hydration(
        self,
    ):
        from telepiplex_search.candidate_hydration import (
            CandidateHydrationError,
        )
        from telepiplex_search.direct_link import DirectEntity

        candidate = frozen_douban_movie_candidate()
        calls = 0

        def resolve(link):
            nonlocal calls
            calls += 1
            stable_id = "wrong" if calls == 1 else "1"
            return DirectEntity(
                provider="douban",
                evidence={
                    "source": "douban",
                    "status": "ok",
                    "facts": [{
                        "subject_id": stable_id,
                        "title": "English Title",
                        "chinese_title": "中文标题1",
                        "official_english_title": "English Title",
                        "original_title": "English Title",
                        "original_language": "en",
                        "year": "2024",
                        "media_type": "movie",
                        "url": link.url,
                    }],
                    "source_urls": [link.url],
                },
                stable_identity=("douban_subject", stable_id),
                title="English Title",
                year="2024",
                media_type="movie",
                scope="work",
            )

        self.feature.exact_link_resolver = resolve
        with self.assertRaises(CandidateHydrationError):
            await self.feature._hydrate_selected_candidate(
                deepcopy(candidate),
                metadata_id="wrong-exact",
                raw_query="English Title 2024",
                require_anchor=True,
            )
        hydrated = await self.feature._hydrate_selected_candidate(
            deepcopy(candidate),
            metadata_id="correct-exact",
            raw_query="English Title 2024",
            require_anchor=True,
        )

        self.assertEqual(calls, 2)
        self.assertEqual(
            hydrated["media_metadata"]["metadata_id"],
            "correct-exact",
        )

    async def test_identity_conflict_query_is_refetched_before_anilist_selector(
        self,
    ):
        from telepiplex_search.confirmed_enrichment import ConfirmedIdentity
        from telepiplex_search.service import SearchFeature

        identity = ConfirmedIdentity(
            provider="douban",
            stable_id="anime",
            chinese_title="蜂蜜与四叶草",
            english_title="Honey and Clover",
            original_title="ハチミツとクローバー",
            year="2005",
            media_type="series",
            requested_scope="work",
            original_language="ja",
            genres=("Animation",),
            external_ids={},
        )
        base = {
            "title": "Hachimitsu to Clover",
            "official_english_title": "Honey and Clover",
            "original_title": "ハチミツとクローバー",
            "year": "2005",
            "media_type": "series",
        }
        conflict = [
            {
                **base,
                "anilist_id": str(anilist_id),
                "external_ids": {"anilist": str(anilist_id)},
            }
            for anilist_id in ("1142", "1143")
        ]
        correct = [{
            **base,
            "anilist_id": "1142",
            "external_ids": {"anilist": "1142"},
        }]
        searches = Mock(side_effect=[conflict, correct])

        with patch(
            "telepiplex_search.service.search_anilist",
            searches,
        ), patch(
            "telepiplex_search.service.get_anilist_media",
            return_value=correct[0],
        ):
            first = await SearchFeature._resolve_confirmed_anilist(
                identity,
                source_scheduler=self.feature.source_scheduler,
            )
            second = await SearchFeature._resolve_confirmed_anilist(
                identity,
                source_scheduler=self.feature.source_scheduler,
            )

        self.assertEqual(first, (None, "not_unique"))
        self.assertEqual(second[1], "ok")
        self.assertEqual(second[0]["anilist_id"], "1142")
        self.assertEqual(searches.call_count, 2)

    async def test_unselected_https_cover_does_not_cache_empty_selected_poster(
        self,
    ):
        candidate = deepcopy(ranked_search_plan()["candidates"][0])
        candidate["media_metadata"]["identity"]["external_ids"] = {
            "douban_subject": "1",
        }
        responses = [{
            "source": "douban",
            "status": "ok",
            "facts": [{
                "subject_id": "1",
                "title": "English Title",
                "year": "2024",
                "media_type": "movie",
                "cover_url": "",
            }, {
                "subject_id": "2",
                "title": "Unrelated",
                "year": "2024",
                "media_type": "movie",
                "cover_url": "https://image.example/unrelated.jpg",
            }],
        }, {
            "source": "douban",
            "status": "ok",
            "facts": [{
                "subject_id": "1",
                "title": "English Title",
                "year": "2024",
                "media_type": "movie",
                "cover_url": "https://image.example/selected.jpg",
            }],
        }]
        provider = Mock(side_effect=responses)

        with patch.object(
            self.feature,
            "_douban_provider",
            provider,
        ):
            first = await self.feature._lookup_candidate_poster(
                candidate,
                "douban",
            )
            second = await self.feature._lookup_candidate_poster(
                candidate,
                "douban",
            )

        self.assertEqual(first, "")
        self.assertEqual(second, "https://image.example/selected.jpg")
        self.assertEqual(provider.call_count, 2)

    async def test_status_ok_malformed_poster_facts_are_refetched(self):
        candidate = deepcopy(ranked_search_plan()["candidates"][0])
        responses = [{
            "source": "douban",
            "status": "ok",
            "facts": [{}],
        }, {
            "source": "douban",
            "status": "ok",
            "facts": [{
                "subject_id": "1",
                "title": "English Title",
                "year": "2024",
                "media_type": "movie",
                "cover_url": "https://image.example/recovered.jpg",
            }],
        }]
        provider = Mock(side_effect=responses)

        with patch.object(
            self.feature,
            "_douban_provider",
            provider,
        ):
            first = await self.feature._lookup_candidate_poster(
                candidate,
                "douban",
            )
            second = await self.feature._lookup_candidate_poster(
                candidate,
                "douban",
            )

        self.assertEqual(first, "")
        self.assertEqual(second, "https://image.example/recovered.jpg")
        self.assertEqual(provider.call_count, 2)

    async def test_poster_same_stable_id_keeps_douban_request_variants_separate(
        self,
    ):
        candidates = []
        for title, year in (("Alpha", "2020"), ("Beta", "2021")):
            candidate = deepcopy(ranked_search_plan()["candidates"][0])
            identity = candidate["media_metadata"]["identity"]
            identity.update({
                "chinese_title": "",
                "english_title": title,
                "year": year,
                "external_ids": {"douban_subject": "123"},
            })
            candidates.append(candidate)
        queries = []

        def provider(hypotheses):
            query = hypotheses["source_queries"]["douban"][0]
            queries.append(query)
            title, year = query.rsplit(" ", 1)
            return {
                "source": "douban",
                "status": "ok",
                "facts": [{
                    "subject_id": "123",
                    "title": title,
                    "year": year,
                    "media_type": "movie",
                    "cover_url": f"https://image.example/{title}.jpg",
                }],
            }

        with patch.object(
            self.feature,
            "_douban_provider",
            side_effect=provider,
        ):
            posters = [
                await self.feature._lookup_candidate_poster(
                    candidate,
                    "douban",
                )
                for candidate in candidates
            ]

        self.assertEqual(queries, ["Alpha 2020", "Beta 2021"])
        self.assertEqual(posters, [
            "https://image.example/Alpha.jpg",
            "https://image.example/Beta.jpg",
        ])

    async def test_poster_same_stable_id_keeps_tvdb_request_variants_separate(
        self,
    ):
        candidates = []
        for title, year in (("Alpha", "2020"), ("Beta", "2021")):
            candidate = deepcopy(ranked_search_plan()["candidates"][0])
            identity = candidate["media_metadata"]["identity"]
            identity.update({
                "chinese_title": "",
                "english_title": title,
                "year": year,
                "external_ids": {"tvdb": "123"},
            })
            candidates.append(candidate)
        queries = []

        def provider(title, year):
            queries.append((title, year))
            return [{
                "tvdb_id": "123",
                "name": title,
                "year": year,
                "cover_url": f"https://image.example/{title}.jpg",
            }]

        with patch(
            "telepiplex_search.service.search_tvdb_movies",
            side_effect=provider,
        ):
            posters = [
                await self.feature._lookup_candidate_poster(
                    candidate,
                    "tvdb",
                )
                for candidate in candidates
            ]

        self.assertEqual(queries, [("Alpha", "2020"), ("Beta", "2021")])
        self.assertEqual(posters, [
            "https://image.example/Alpha.jpg",
            "https://image.example/Beta.jpg",
        ])

    async def test_anchor_failure_never_calls_authoritative_enrichment(self):
        from telepiplex_search.candidate_hydration import (
            CandidateHydrationError,
        )
        from telepiplex_search.direct_link import DirectLinkError

        candidate = deepcopy(series_ranked_search_plan()["candidates"][0])
        candidate.update({
            "candidate_id": "douban_subject:1919245",
            "anchor_fact_id": "douban:1919245",
            "identity_role": "season",
            "intended_scope": "season",
            "links_frozen": True,
            "source_links": [{
                "provider": "douban",
                "fact_id": "douban:1919245",
                "url": "https://movie.douban.com/subject/1919245/",
                "external_ids": {"douban_subject": "1919245"},
                "role": "season",
                "season_number": None,
                "episode_number": None,
                "verification": "unresolved_scope_link",
                "proposed_season_number": 1,
                "proposed_episode_number": None,
            }],
        })
        supplement = AsyncMock()
        self.feature.selected_candidate_supplementer = supplement

        def fail_anchor(_link):
            raise DirectLinkError("direct_link_not_found")

        self.feature.exact_link_resolver = fail_anchor

        with self.assertRaises(CandidateHydrationError) as raised:
            await self.feature._hydrate_selected_candidate(
                candidate,
                metadata_id="anchor-failure",
                raw_query="冰果 第一季",
                require_anchor=True,
            )

        self.assertEqual(raised.exception.code, "fixed_link_read_failed")
        supplement.assert_not_awaited()

    async def test_hydration_failure_keeps_candidate_poster_work_alive(self):
        from telepiplex_search.candidate_hydration import (
            CandidateHydrationError,
        )

        plan_id = "poster-survives-hydration-failure"
        plan = ranked_search_plan()
        plan.update({
            "plan_id": plan_id,
            "raw_query": "冰果",
            "links_frozen": True,
        })
        candidate = plan["candidates"][0]
        candidate.update({
            "links_frozen": True,
            "identity_role": "series_root",
            "intended_scope": "whole_series",
        })
        operation = self.feature._new_operation(
            {"chat_id": 10, "user_id": 1},
            state="awaiting_input",
            stage="candidate_selection",
            status_text="等待选择。",
            control="exit",
            kind="search",
        )
        release_poster = asyncio.Event()
        poster_task = asyncio.create_task(release_poster.wait())
        stored = {
            "plan": plan,
            "candidates": (candidate,),
            "selected_path": "",
            "operation_id": operation["operation_id"],
            "candidate_poster_task": poster_task,
        }

        with patch(
            "telepiplex_search.service.hydrate_frozen_candidate_anchor",
            side_effect=CandidateHydrationError(
                "metadata_incomplete",
                ("canonical_latin_title",),
            ),
        ):
            result = await self.feature._select_candidate(
                plan_id,
                stored,
                "0",
            )
        await asyncio.sleep(0)

        try:
            self.assertIn("严格媒体元数据不完整", result["actions"][0]["text"])
            self.assertFalse(poster_task.done())
            self.assertEqual(poster_task.cancelling(), 0)
        finally:
            if not poster_task.done():
                release_poster.set()
            await asyncio.gather(poster_task, return_exceptions=True)

    async def test_scope_failure_keeps_candidate_poster_work_alive(self):
        plan_id = "poster-survives-scope-failure"
        plan = series_ranked_search_plan()
        plan.update({
            "plan_id": plan_id,
            "raw_query": "黑暗荣耀",
            "links_frozen": False,
        })
        candidate = plan["candidates"][0]
        candidate["media_metadata"]["items"] = []
        operation = self.feature._new_operation(
            {"chat_id": 10, "user_id": 1},
            state="awaiting_input",
            stage="plan_confirmation",
            status_text="等待确认。",
            control="exit",
            kind="search",
        )
        release_poster = asyncio.Event()
        poster_task = asyncio.create_task(release_poster.wait())
        stored = {
            "plan": plan,
            "candidates": (candidate,),
            "selected_path": "/Series",
            "operation_id": operation["operation_id"],
            "candidate_poster_task": poster_task,
        }

        result = await self.feature._select_candidate(
            plan_id,
            stored,
            "0",
        )
        await asyncio.sleep(0)

        try:
            self.assertIn("无法验证该剧集", result["actions"][0]["text"])
            self.assertFalse(poster_task.done())
            self.assertEqual(poster_task.cancelling(), 0)
        finally:
            if not poster_task.done():
                release_poster.set()
            await asyncio.gather(poster_task, return_exceptions=True)

    @patch("telepiplex_search.service.hydrate_frozen_candidate_anchor")
    async def test_authoritative_enrichment_failure_stays_fail_closed(
        self,
        hydrate,
    ):
        plan_id = "authoritative-failure"
        plan = series_ranked_search_plan()
        plan.update({
            "plan_id": plan_id,
            "raw_query": "冰果 第一季",
            "links_frozen": True,
        })
        candidate = plan["candidates"][0]
        candidate.update({
            "links_frozen": True,
            "identity_role": "season",
            "intended_scope": "season",
            "requested_season_number": 1,
        })
        contract = candidate["media_metadata"]
        contract["items"] = []
        contract["retrieval"]["scope"] = "season"
        contract["evidence"]["decision"].update({
            "scope": "season",
            "season_number": 1,
            "episode_number": None,
        })
        contract["evidence"]["series_inventory"] = {"season_totals": {}}
        hydrate.return_value = deepcopy(candidate)

        async def fail_authoritative(_candidate, _raw_query):
            raise RuntimeError("scope provider unavailable")

        self.feature.selected_candidate_supplementer = fail_authoritative
        operation = self.feature._new_operation(
            {"chat_id": 10, "user_id": 1},
            state="awaiting_input",
            stage="candidate_selection",
            status_text="等待选择。",
            control="exit",
            kind="search",
        )

        result = await self.feature._select_candidate(
            plan_id,
            {
                "plan": plan,
                "candidates": (candidate,),
                "selected_path": "",
                "operation_id": operation["operation_id"],
            },
            "0",
        )

        self.assertIn("已验证季集范围", result["actions"][0]["text"])
        self.assertNotIn("operation", result)

    @patch("telepiplex_search.service.hydrate_frozen_candidate_anchor")
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
        from telepiplex_search.errors import SearchPlanningError

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
        self.assertEqual(selected.get("actions"), [])
        self.assertNotEqual(
            selected["operation"]["stage"],
            "prowlarr_search",
        )
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
        self.assertEqual(started.get("actions"), [])
        self.assertNotEqual(
            started["operation"]["stage"],
            "prowlarr_search",
        )
        self.assertEqual(stored["selected_path"], "/Series")
        self.assertEqual(
            stored["plan"]["media_metadata"]["placement"]["episode_number"],
            100,
        )
        self.assertEqual(
            stored["plan"]["media_metadata"]["retrieval"]["query"],
            "English Title 2024",
        )

    async def test_related_movie_is_not_mapped_into_specials(self):
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

        self.feature.plan_builder = planner
        await self.feature.command({
            "command": "s",
            "args": ["中文标题", "电影版"],
            "user_id": 1,
            "chat_id": 10,
        })
        await self.runtime.run("search-plan-")
        callback = self.host.reports[-1]["details"]["keyboard"][0][0]["callback_data"]
        plan_id = callback.split(":")[2]

        selected = await self.feature.callback({
            "payload": f"select:{plan_id}:0",
            "user_id": 1,
            "chat_id": 10,
        })

        self.assertEqual(selected.get("actions"), [])
        self.assertNotEqual(
            selected["operation"]["stage"],
            "prowlarr_search",
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
        self.assertIn("全剧（共 1 季）", labels)
        self.assertNotIn("指定集", labels)
        self.assertNotIn("指定季", labels)

        started = await self.feature.callback({
            "payload": f"scope:{plan_id}:whole_series",
            "user_id": 1,
            "chat_id": 10,
        })
        self.assertEqual(started.get("actions"), [])
        self.assertNotEqual(
            started["operation"]["stage"],
            "prowlarr_search",
        )
        await self.runtime.run("search-releases-")

        self.assertEqual(self.search_queries, [
            ("The Glory S01", "series"),
            ("The Glory Season 01", "series"),
            ("The Glory Complete", "series"),
        ])

    def test_multi_season_menu_lists_each_season_directly(self):
        plan = series_ranked_search_plan()
        contract = plan["media_metadata"]
        contract["items"].extend({
            "item_id": f"s2e{number}",
            "content_role": "main_episode",
            "season_number": 2,
            "episode_number": number,
            "aired": "2023-01-01",
        } for number in range(1, 4))
        contract["evidence"]["series_inventory"]["season_totals"][2] = 3
        operation = self.feature._new_operation(
            {"chat_id": 10, "user_id": 1},
            state="awaiting_input",
            stage="series_scope",
            status_text="等待选择。",
            control="exit",
            kind="search",
        )

        action = self.feature._series_scope_action("seasons", {
            "plan": {"plan_id": "seasons", "media_metadata": contract},
            "operation_id": operation["operation_id"],
        })["actions"][0]
        labels = [row[0]["text"] for row in action["data"]["keyboard"]]

        self.assertEqual(labels[:3], ["全剧", "第一季", "第二季"])
        self.assertEqual(
            action["data"]["keyboard"][1][0]["callback_data"],
            "search:scope:seasons:season:1",
        )

    def test_multi_season_menu_formats_double_digit_chinese_ordinals(self):
        plan = series_ranked_search_plan()
        contract = plan["media_metadata"]
        contract["identity"]["season_count"] = 11
        contract["items"] = []
        contract["evidence"]["series_inventory"] = {
            "season_totals": {},
        }
        operation = self.feature._new_operation(
            {"chat_id": 10, "user_id": 1},
            state="awaiting_input",
            stage="series_scope",
            status_text="等待选择。",
            control="exit",
            kind="search",
        )

        action = self.feature._series_scope_action("season-eleven", {
            "plan": {"plan_id": "season-eleven", "media_metadata": contract},
            "operation_id": operation["operation_id"],
        })["actions"][0]
        labels = [row[0]["text"] for row in action["data"]["keyboard"]]

        self.assertIn("第十一季（已播 0 集）", labels)

    def test_explicit_season_menu_lists_each_regular_episode(self):
        plan = series_ranked_search_plan()
        contract = plan["media_metadata"]
        contract["evidence"]["decision"].update({
            "scope": "season",
            "season_number": 1,
        })
        operation = self.feature._new_operation(
            {"chat_id": 10, "user_id": 1},
            state="awaiting_input",
            stage="series_scope",
            status_text="等待选择。",
            control="exit",
            kind="search",
        )

        action = self.feature._series_scope_action("episodes", {
            "plan": {"plan_id": "episodes", "media_metadata": contract},
            "operation_id": operation["operation_id"],
        })["actions"][0]
        labels = [row[0]["text"] for row in action["data"]["keyboard"]]

        self.assertEqual(labels[0], "第一季 全季")
        self.assertEqual(labels[1:4], ["第一集", "第二集", "第三集"])
        self.assertEqual(
            action["data"]["keyboard"][1][0]["callback_data"],
            "search:scope:episodes:episode:1:1",
        )

    def test_ongoing_series_uses_third_level_aired_episode_menu(self):
        plan = series_ranked_search_plan()
        contract = plan["media_metadata"]
        today = date.today()
        past_air_date = (today - timedelta(days=365)).isoformat()
        future_air_date = (today + timedelta(days=365)).isoformat()
        contract["items"] = [
            *({
                "item_id": f"s1e{number}",
                "content_role": "main_episode",
                "season_number": 1,
                "episode_number": number,
                "aired": past_air_date,
            } for number in range(1, 9)),
            *({
                "item_id": f"s2e{number}",
                "content_role": "main_episode",
                "season_number": 2,
                "episode_number": number,
                "aired": past_air_date if number < 8 else future_air_date,
            } for number in range(1, 9)),
        ]
        contract["evidence"]["series_inventory"] = {
            "source": "wikipedia",
            "status": "complete",
            "season_totals": {1: 8, 2: 8},
            "source_revisions": {"en": 1367933110},
        }
        operation = self.feature._new_operation(
            {"chat_id": 10, "user_id": 1},
            state="awaiting_input",
            stage="series_scope",
            status_text="等待选择。",
            control="exit",
            kind="search",
        )
        stored = {
            "plan": {"plan_id": "ongoing", "media_metadata": contract},
            "operation_id": operation["operation_id"],
        }

        action = self.feature._series_scope_action(
            "ongoing",
            stored,
        )["actions"][0]
        labels = [row[0]["text"] for row in action["data"]["keyboard"]]

        self.assertEqual(
            labels[:2],
            ["第一季（全季）", "第二季（已播 7/8）"],
        )
        self.assertNotIn("全剧", labels)

        submenu = self.feature._scope_callback(
            "ongoing",
            stored,
            "season",
            {"user_id": 1, "chat_id": 10},
            "2",
        )["actions"][0]
        episode_labels = [
            row[0]["text"] for row in submenu["data"]["keyboard"]
        ]
        self.assertEqual(
            episode_labels[:7],
            [f"第{number}集" for number in "一二三四五六七"],
        )
        self.assertNotIn("第八集", episode_labels)

    def test_one_season_ongoing_series_does_not_offer_whole_series(self):
        plan = series_ranked_search_plan()
        contract = plan["media_metadata"]
        contract["items"][-1]["aired"] = "2099-01-01"
        contract["evidence"]["series_inventory"] = {
            "season_totals": {1: 8},
        }
        operation = self.feature._new_operation(
            {"chat_id": 10, "user_id": 1},
            state="awaiting_input",
            stage="series_scope",
            status_text="等待选择。",
            control="exit",
            kind="search",
        )

        action = self.feature._series_scope_action("single-ongoing", {
            "plan": {
                "plan_id": "single-ongoing",
                "media_metadata": contract,
            },
            "operation_id": operation["operation_id"],
        })["actions"][0]
        labels = [row[0]["text"] for row in action["data"]["keyboard"]]

        self.assertNotIn("全剧（共 1 季）", labels)
        self.assertEqual(labels[0], "第一季（已播 7/8）")

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
            resolved["media_metadata"]["identity"]["title_original"],
            "English Title",
        )
        self.assertNotIn("naming_metadata", resolved)
        self.assertNotIn("source_queries", resolved)
        self.assertNotIn("evidence", resolved)
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

    async def test_ambiguous_metadata_capability_does_not_wait_for_posters(self):
        planned = ranked_search_plan()
        for candidate in planned["candidates"]:
            candidate["poster_url"] = ""
            candidate["media_metadata"]["identity"]["poster_url"] = ""
        expected_refs = [
            candidate["candidate_key"]
            for candidate in planned["candidates"]
        ]
        expected_titles = [
            candidate["media_metadata"]["identity"]["chinese_title"]
            for candidate in planned["candidates"]
        ]

        async def ambiguous_planner(_raw_query, plan_id):
            planned["plan_id"] = plan_id
            return planned

        poster_calls = []

        async def never_returning_poster(candidate, provider):
            poster_calls.append((candidate["candidate_key"], provider))
            await asyncio.Event().wait()

        self.feature.plan_builder = ambiguous_planner
        self.feature.candidate_poster_lookup = never_returning_poster

        response = await asyncio.wait_for(
            self.feature.metadata_capability({
                "method": "resolve_metadata",
                "payload": {"query": "同名作品"},
            }),
            timeout=0.1,
        )

        self.assertEqual(response["status"], "confirmation_required")
        self.assertEqual(
            [candidate["ref"] for candidate in response["candidates"]],
            expected_refs,
        )
        self.assertEqual(
            [candidate["title"] for candidate in response["candidates"]],
            expected_titles,
        )
        self.assertEqual(
            [candidate["year"] for candidate in response["candidates"]],
            ["2024", "2023"],
        )
        self.assertEqual(
            [candidate["media_type"] for candidate in response["candidates"]],
            ["movie", "movie"],
        )
        self.assertEqual(
            [candidate["poster_url"] for candidate in response["candidates"]],
            ["", ""],
        )
        self.assertEqual(poster_calls, [])

        state, frozen = self.feature.metadata_resolution_store.load(
            response["resolution_id"]
        )
        self.assertEqual(state, "found")
        frozen_candidates = frozen["plan"]["candidates"]
        self.assertEqual(
            [candidate["candidate_key"] for candidate in frozen_candidates],
            expected_refs,
        )
        self.assertEqual(
            [
                candidate["media_metadata"]["identity"]["chinese_title"]
                for candidate in frozen_candidates
            ],
            expected_titles,
        )

        planned["candidates"][0]["candidate_key"] = "mutated-after-save"
        planned["candidates"][0]["media_metadata"]["identity"][
            "chinese_title"
        ] = "保存后突变"
        _state, reloaded = self.feature.metadata_resolution_store.load(
            response["resolution_id"]
        )
        self.assertEqual(
            reloaded["plan"]["candidates"][0]["candidate_key"],
            expected_refs[0],
        )
        self.assertEqual(
            reloaded["plan"]["candidates"][0]["media_metadata"][
                "identity"
            ]["chinese_title"],
            expected_titles[0],
        )

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
        self.assertEqual(contract["identity"]["title_zh"], "同名剧集")
        self.assertEqual(contract["identity"]["media_type"], "series")
        self.assertEqual(
            contract["scope"]["kind"],
            "season",
        )

    async def test_metadata_probe_does_not_choose_between_same_type_works(self):
        planner_calls = []

        async def series_planner(_raw_query, plan_id):
            planner_calls.append(plan_id)
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
        self.assertTrue(ambiguous["resolution_id"])
        self.assertEqual(
            [item["title"] for item in ambiguous["candidates"]],
            ["剧集甲", "剧集乙"],
        )
        confirmed = await self.feature.metadata_capability({
            "method": "confirm_metadata",
            "payload": {
                "query": "同名剧集",
                "probe": ambiguous["probe"],
                "resolution_id": ambiguous["resolution_id"],
                "candidate_ref": ambiguous["candidates"][1]["ref"],
            },
        })
        self.assertEqual(confirmed["status"], "resolved")
        self.assertEqual(len(planner_calls), 1)
        self.assertEqual(
            confirmed["media_metadata"]["identity"]["title_zh"],
            "剧集乙",
        )
        for _index in range(100):
            replay = await self.feature.metadata_capability({
                "method": "confirm_metadata",
                "payload": {
                    "resolution_id": ambiguous["resolution_id"],
                    "candidate_ref": ambiguous["candidates"][1]["ref"],
                },
            })
            self.assertEqual(replay, confirmed)
        self.assertEqual(len(planner_calls), 1)

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
            self.assertTrue(candidate["metadata_hydrated"])
            return deepcopy(candidate)

        self.feature.selected_candidate_supplementer = supplement

        def exact_hydration(candidate, **_kwargs):
            hydrated = deepcopy(candidate)
            hydrated["media_metadata"]["identity"][
                "english_title"
            ] = "Hydrated Exact Title"
            hydrated["metadata_hydrated"] = True
            return hydrated

        with patch(
            "telepiplex_search.service.hydrate_frozen_candidate_anchor",
            side_effect=exact_hydration,
        ) as hydrate:
            resolved = await self.feature.metadata_capability({
                "method": "resolve_metadata",
                "payload": {"query": "布达佩斯大饭店"},
            })

        hydrate.assert_called_once()
        self.assertEqual(supplement_calls, [])
        self.assertEqual(
            resolved["media_metadata"]["identity"]["title_original"],
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
                decision = resolved["media_metadata"]["scope"]
                self.assertEqual(
                    (
                        decision["kind"],
                        decision["season_number"],
                        decision["episode_number"],
                    ),
                    expected,
                )

    async def test_metadata_capability_returns_honey_canonical_subset_and_unresolved_evidence(self):
        async def live_planner(_raw_query, plan_id):
            result = series_ranked_search_plan()
            result["plan_id"] = plan_id
            candidate = result["candidates"][0]
            contract = candidate["media_metadata"]
            contract["metadata_id"] = plan_id
            contract["identity"]["chinese_title"] = "蜂蜜与四叶草"
            contract["identity"]["english_title"] = "Honey and Clover"
            contract["items"] = [
                {
                    "item_id": f"s{season}e{episode}",
                    "content_role": "main_episode",
                    "season_number": season,
                    "episode_number": episode,
                    "aired": "",
                }
                for season, total in ((1, 24), (2, 12))
                for episode in range(1, total + 1)
            ]
            contract["evidence"]["series_inventory"] = {
                "source": "tvdb",
                "season_totals": {1: 24, 2: 12},
            }
            result["candidates"] = [candidate]
            return result

        self.feature.plan_builder = live_planner
        probe = {
            "content_shape": "multi_season_episode_pack",
            "observed_seasons": [1, 2],
            "observed_episodes": [
                {"season_number": season, "episode_number": episode}
                for season, total in ((1, 26), (2, 12))
                for episode in range(1, total + 1)
            ],
            "video_count": 38,
        }

        resolved = await self.feature.metadata_capability({
            "method": "resolve_metadata",
            "payload": {"query": "蜂蜜与四叶草", "probe": probe},
        })

        contract = resolved["media_metadata"]
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(contract["identity"]["title_zh"], "蜂蜜与四叶草")
        self.assertEqual(contract["scope"]["kind"], "whole_series")
        self.assertNotIn("items", contract)
        self.assertNotIn("evidence", contract)

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
        pack_decision = pack["media_metadata"]["scope"]
        self.assertEqual(pack_decision["kind"], "season")
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
        episode_decision = episode["media_metadata"]["scope"]
        self.assertEqual(episode_decision["kind"], "episode")
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

        self.assertEqual(manifest["version"], "1.12.3")
        self.assertEqual(manifest["host_api"], ">=1.7,<2.0")
        self.assertEqual(project["project"]["version"], "1.12.3")
        self.assertEqual(
            project["project"]["dependencies"][0],
            "telepiplex-plugin-sdk==1.4.0",
        )

    def test_default_config_enables_free_and_configured_sources(self):
        config = yaml.safe_load((ROOT / "config.default.yaml").read_text())

        self.assertTrue(config["metadata"]["wikipedia"]["enable"])
        self.assertTrue(config["metadata"]["douban"]["enable"])
        self.assertTrue(config["metadata"]["tvdb"]["enable"])
        self.assertNotIn("ai", config)

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
        self.assertIn("/tmp/search-1.12.3.tpx", source)
        self.assertIn("豆瓣", source)
        self.assertIn("用户确认", source)
        self.assertIn("不调用 AI", source)
        self.assertIn("Wikipedia", source)
        self.assertIn("TVDB", source)
        self.assertIn("Rename", source)
        self.assertNotIn("dist/search-1.12.3.tpx", source)

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
