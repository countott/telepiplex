import ast
import asyncio
import unittest
from pathlib import Path
from unittest.mock import Mock

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


class FakeCore:
    def __init__(self):
        self.calls = []
        self.reports = []

    async def call_capability(self, capability, method, payload, **kwargs):
        self.calls.append((capability, method, payload, kwargs))
        return {"accepted": True, "job_id": "download-1"}

    async def report_operation(self, operation):
        self.reports.append(operation)


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

        self.assertEqual(self.search_queries, [("English Title 2024", "movie")])
        self.assertIn("找到 1 个", self.core.reports[-1]["status_text"])
        self.assertEqual(self.core.reports[-1]["state"], "awaiting_input")

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
        plan["prowlarr_queries"] = ["中文 English Title S01E02"]

        self.assertEqual(
            self.feature._english_prowlarr_query(plan, contract),
            "English Title S01E02",
        )

    async def test_rule_series_queries_preserve_requested_scope(self):
        cases = {
            "whole_series": "English Title 2024",
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

                self.assertEqual(
                    self.feature._english_prowlarr_query(plan, contract),
                    expected,
                )

    async def test_ai_whole_series_uses_clean_ai_query_when_scope_is_unknown(self):
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
        plan["prowlarr_queries"] = ["The Glory 2022"]

        self.assertEqual(
            self.feature._english_prowlarr_query(plan, contract),
            "The Glory 2022",
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

    async def test_metadata_capability_requeries_sources_without_downloading(self):
        self.feature.allocator = Mock()
        resolved = await self.feature.metadata_capability({
            "method": "resolve_metadata",
            "payload": {"query": "English.Title.2024.1080p.WEB-DL"},
            "context": {"idempotency_key": "rename-job-1"},
        })

        self.assertTrue(resolved["media_metadata"]["confirmed"])
        self.assertEqual(
            resolved["media_metadata"]["identity"]["english_title"],
            "English Title",
        )
        self.assertEqual(resolved["naming_metadata"]["source"], "media-search")
        self.assertEqual(self.core.calls, [])
        released_plan_id = self.feature.allocator.release.call_args.args[0]
        self.assertTrue(released_plan_id.startswith("resolve-"))


class FeatureSourceContractTest(unittest.TestCase):
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
        self.assertIn("dist/media-search-1.1.0.tpx", source)
        self.assertNotIn("dist/media-search-1.0.0.tpx", source)

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
