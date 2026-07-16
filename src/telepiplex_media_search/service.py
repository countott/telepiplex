from __future__ import annotations

import asyncio
import re
import time
import uuid
from copy import deepcopy

from telepiplex_plugin_sdk import FeatureError
from telepiplex_plugin_sdk.media_metadata import resolve_category_route

from .adapters.douban import lookup_douban_evidence
from .adapters.prowlarr import resolve_prowlarr_download_url, search_prowlarr
from .adapters.tvdb import (
    TvdbConfigError,
    TvdbRequestError,
    get_tvdb_series_episodes,
    search_tvdb_movies,
    search_tvdb_series,
)
from .adapters.wikipedia import lookup_wikipedia_evidence
from .config_wizard import MediaSearchConfigWizard
from .planner import SearchPlanningError, build_confirmable_search_plan
from .release_score import rank_releases
from .search_plan import TemporarySpecialAllocator, confirm_media_metadata


_LATIN = re.compile(r"[A-Za-z]")


def _ambiguous_core_report_error(exc: Exception) -> bool:
    return not isinstance(exc, FeatureError) or exc.code in {
        "core_unavailable", "deadline_exceeded", "invalid_response",
    }

_PLANNING_ERROR_MESSAGES = {
    "ambiguous_candidates": "存在多个候选，请补充年份或电影/剧集类型。",
    "evidence_conflict": "不同来源的年份或媒体类型冲突，请补充更明确的信息。",
    "insufficient_independent_support": "独立证据来源不足，无法安全生成计划。",
    "missing_bilingual_identity": "缺少可验证的中英文媒体身份。",
    "missing_year": "缺少可交叉验证的发行年份。",
    "tvdb_identity_required": "剧集缺少唯一 TVDB 身份，无法安全生成计划。",
    "tvdb_scope_not_verified": "TVDB 无法验证请求的季或集。",
    "complex_identity_requires_ai": "该条目包含复杂媒体关系，需要 AI 判断。",
    "ai_unavailable_after_gate_failure": "规则证据不足，且 AI 当前不可用；请补充年份、媒体类型或稍后重试。",
    "ai_invalid_after_gate_failure": "规则证据不足，且 AI 未能生成有效计划；请补充信息后重试。",
}


class MediaSearchFeature:
    def __init__(
        self,
        *,
        config: dict,
        core,
        plan_builder=None,
        release_search=None,
        release_rank=None,
        release_resolver=None,
        registry=None,
    ):
        self.config = config
        self.core = core
        self.allocator = TemporarySpecialAllocator()
        self.plan_builder = plan_builder or self._build_plan
        self.release_search = release_search or self._search_releases
        self.release_rank = release_rank or rank_releases
        self.release_resolver = release_resolver or resolve_prowlarr_download_url
        self.registry = registry
        self.plans = {}
        self.awaiting_queries = set()
        self.config_wizard = MediaSearchConfigWizard(config)
        self.runtime = None
        self.operations = {}
        self.owner_operations = {}

    def bind_runtime(self, runtime):
        self.runtime = runtime

    async def metadata_capability(self, request: dict) -> dict:
        if str(request.get("method") or "") != "resolve_metadata":
            raise FeatureError(
                "method_not_allowed",
                "media.search method is not allowed",
            )
        payload = request.get("payload") or {}
        raw_query = " ".join(str(payload.get("query") or "").split())
        if not raw_query:
            raise FeatureError("invalid_query", "metadata query is required")
        plan_id = f"resolve-{uuid.uuid4().hex[:16]}"
        try:
            plan = await self.plan_builder(raw_query, plan_id)
            contract = confirm_media_metadata(plan)
            identity = contract.get("identity") or {}
            return {
                "media_metadata": contract,
                "naming_metadata": {
                    "source": "media-search",
                    "media_type": (
                        (contract.get("placement") or {}).get("library_type") or ""
                    ),
                    "chinese_title": identity.get("chinese_title") or "",
                    "english_title": identity.get("english_title") or "",
                    "year": identity.get("year") or "",
                },
                "source_queries": deepcopy(plan.get("source_queries") or {}),
                "evidence": deepcopy(contract.get("evidence") or {}),
            }
        except SearchPlanningError as exc:
            raise FeatureError(
                "metadata_unresolved",
                f"metadata resolution failed: {getattr(exc, 'code', str(exc))}",
            ) from exc
        finally:
            self.allocator.release(plan_id)

    async def command(self, request: dict) -> dict:
        command = str(request.get("command") or "")
        if command == "media_search_config":
            owner = self._owner_key(request)
            if owner in self.awaiting_queries or any(
                item.get("owner") == owner for item in self.plans.values()
            ):
                return self._closed(
                    "⚠️ 请先完成或取消当前搜索，再打开 media-search 配置。"
                )
            result = self.config_wizard.start(request)
            operation = self._new_operation(
                request,
                state="awaiting_input",
                stage="config_section",
                status_text="等待选择 media-search 配置项。",
                control="exit",
                kind="config",
            )
            result["operation"] = operation
            return result
        if command not in {"search", "s"}:
            raise FeatureError("not_found", f"unknown media-search command: {command}")
        self.config_wizard.clear(request)
        raw_query = " ".join(str(item) for item in request.get("args") or []).strip()
        if not raw_query:
            owner = self._owner_key(request)
            self.awaiting_queries.add(owner)
            operation = self._new_operation(
                request,
                state="awaiting_input",
                stage="query_input",
                status_text="等待输入片名或影视条目链接。",
                control="exit",
                kind="search",
            )
            return {
                "actions": [{
                    "kind": "send_message",
                    "text": "请输入片名或影视条目链接。",
                    "data": {"keyboard": [[{
                        "text": "退出",
                        "callback_data": "media-search:exit",
                    }]]},
                }],
                "session": {"state": "open"},
                "operation": operation,
            }
        return self._start_plan_task(raw_query, request)

    async def message(self, request: dict) -> dict:
        if self.config_wizard.has_session(request):
            return self._decorate_config_result(
                request, self.config_wizard.message(request)
            )
        key = self._owner_key(request)
        if key not in self.awaiting_queries:
            return {
                "actions": [{"kind": "send_message", "text": "⚠️ 搜索会话已失效。"}],
                "session": {"state": "close"},
            }
        self.awaiting_queries.discard(key)
        return self._start_plan_task(
            str(request.get("text") or "").strip(), request, reuse_owner=True
        )

    async def callback(self, request: dict) -> dict:
        payload = str(request.get("payload") or "")
        if payload.startswith("config:"):
            return self._decorate_config_result(
                request, self.config_wizard.callback(request)
            )
        if payload == "exit":
            return self._exit_owner_operation(request)
        parts = payload.split(":")
        if len(parts) < 2:
            raise FeatureError("invalid_callback", "media-search callback is invalid")
        action, plan_id = parts[:2]
        stored = self.plans.get(plan_id)
        if not stored or stored["owner"] != self._owner_key(request):
            return self._closed("⚠️ 搜索任务已过期，请重新搜索。")
        if action == "cancel":
            operation_id = stored.get("operation_id")
            self._release_plan(plan_id)
            result = self._closed("已退出本次搜索。")
            if operation_id:
                result["operation"] = self._advance_operation(
                    operation_id,
                    state="cancelled",
                    stage="cancelled",
                    status_text="已退出本次搜索。",
                    control="",
                )
            return result
        if action == "confirm":
            return self._start_release_search_task(plan_id, stored)
        if action == "release" and len(parts) == 3:
            return self._start_submission_task(plan_id, stored, parts[2])
        raise FeatureError("invalid_callback", "media-search callback action is invalid")

    def _start_plan_task(
        self, raw_query: str, request: dict, *, reuse_owner: bool = False
    ) -> dict:
        if self.runtime is None:
            raise FeatureError("not_ready", "media-search runtime is not ready")
        owner = self._owner_key(request)
        operation = self._operation_for_owner(owner) if reuse_owner else None
        if operation is None:
            operation_view = self._new_operation(
                request,
                state="running",
                stage="planning",
                status_text="正在规划媒体证据。",
                control="cancel",
                kind="search",
            )
            operation = self.operations[operation_view["operation_id"]]
        else:
            operation_view = self._advance_operation(
                operation["operation_id"],
                state="running",
                stage="planning",
                status_text="正在规划媒体证据。",
                control="cancel",
            )
        plan_id = uuid.uuid4().hex[:10]
        task_id = f"media-search-plan-{operation['operation_id']}"
        task = self.runtime.spawn(
            self._prepare_plan_task(
                raw_query,
                dict(request),
                plan_id,
                operation["operation_id"],
            ),
            task_id=task_id,
        )
        operation.update({"task": task, "task_id": task_id, "plan_id": plan_id})
        return {
            "actions": [{
                "kind": "send_message",
                "text": "⏳ 正在规划媒体证据...",
            }],
            "session": {"state": "close"},
            "operation": operation_view,
        }

    async def _prepare_plan_task(
        self, raw_query, request, plan_id, operation_id
    ):
        try:
            result = await self._prepare_plan(
                raw_query,
                request,
                plan_id=plan_id,
                operation_id=operation_id,
            )
            action = (result.get("actions") or [{}])[0]
            if plan_id in self.plans:
                await self._report_operation(
                    operation_id,
                    state="awaiting_input",
                    stage="plan_confirmation",
                    status_text=str(action.get("text") or "媒体方案已生成。"),
                    control="exit",
                    details=deepcopy(action.get("data") or {}),
                )
            else:
                await self._report_operation(
                    operation_id,
                    state="failed",
                    stage="planning",
                    status_text=str(action.get("text") or "媒体规划失败。"),
                    control="",
                )
        except asyncio.CancelledError:
            self._release_plan(plan_id)
            await self._report_operation(
                operation_id,
                state="cancelled",
                stage="planning",
                status_text="媒体规划已取消。",
                control="",
            )
        except Exception as exc:
            self._release_plan(plan_id)
            await self._report_operation(
                operation_id,
                state="failed",
                stage="planning",
                status_text=f"媒体规划失败：{type(exc).__name__}",
                control="",
            )

    def _start_release_search_task(self, plan_id: str, stored: dict) -> dict:
        operation_id = stored["operation_id"]
        operation_view = self._advance_operation(
            operation_id,
            state="running",
            stage="prowlarr_search",
            status_text="正在搜索并排序 Prowlarr 片源。",
            control="cancel",
        )
        task_id = f"media-search-releases-{operation_id}"
        task = self.runtime.spawn(
            self._release_search_task(plan_id, stored, operation_id),
            task_id=task_id,
        )
        self.operations[operation_id].update({"task": task, "task_id": task_id})
        return {
            "actions": [{
                "kind": "edit_message",
                "text": "⏳ 正在搜索并排序 Prowlarr 片源...",
            }],
            "operation": operation_view,
        }

    async def _release_search_task(self, plan_id, stored, operation_id):
        try:
            result = await self._confirm_and_search(plan_id, stored)
            action = (result.get("actions") or [{}])[0]
            if plan_id in self.plans and stored.get("results"):
                await self._report_operation(
                    operation_id,
                    state="awaiting_input",
                    stage="release_selection",
                    status_text=str(action.get("text") or "请选择片源。"),
                    control="exit",
                    details=deepcopy(action.get("data") or {}),
                )
            else:
                await self._report_operation(
                    operation_id,
                    state="failed",
                    stage="prowlarr_search",
                    status_text=str(action.get("text") or "Prowlarr 搜索失败。"),
                    control="",
                )
        except asyncio.CancelledError:
            self._release_plan(plan_id)
            await self._report_operation(
                operation_id,
                state="cancelled",
                stage="prowlarr_search",
                status_text="Prowlarr 搜索已取消。",
                control="",
            )
        except Exception as exc:
            self._release_plan(plan_id)
            await self._report_operation(
                operation_id,
                state="failed",
                stage="prowlarr_search",
                status_text=f"Prowlarr 搜索失败：{type(exc).__name__}",
                control="",
            )

    def _start_submission_task(self, plan_id, stored, raw_index):
        operation_id = stored["operation_id"]
        operation_view = self._advance_operation(
            operation_id,
            state="running",
            stage="resolving_release",
            status_text="正在解析片源下载链接。",
            control="cancel",
        )
        task_id = f"media-search-submit-{operation_id}"
        task = self.runtime.spawn(
            self._submission_task(plan_id, stored, raw_index, operation_id),
            task_id=task_id,
        )
        self.operations[operation_id].update({"task": task, "task_id": task_id})
        return {
            "actions": [{
                "kind": "edit_message",
                "text": "⏳ 正在解析片源并提交下载...",
            }],
            "operation": operation_view,
        }

    async def _submission_task(self, plan_id, stored, raw_index, operation_id):
        try:
            result = await self._submit_release(
                plan_id, stored, raw_index, operation_id
            )
            if self.operations[operation_id]["state"] != "handed_off":
                action = (result.get("actions") or [{}])[0]
                await self._report_operation(
                    operation_id,
                    state="failed",
                    stage="resolving_release",
                    status_text=str(action.get("text") or "片源提交失败。"),
                    control="",
                )
        except asyncio.CancelledError:
            self._release_plan(plan_id)
            await self._report_operation(
                operation_id,
                state="cancelled",
                stage="resolving_release",
                status_text="片源提交已取消。",
                control="",
            )
        except Exception as exc:
            self._release_plan(plan_id)
            if self.operations[operation_id]["state"] != "failed":
                await self._report_operation(
                    operation_id,
                    state="failed",
                    stage="resolving_release",
                    status_text=f"片源提交失败：{type(exc).__name__}",
                    control="",
                )

    async def _prepare_plan(
        self,
        raw_query: str,
        request: dict,
        *,
        plan_id: str,
        operation_id: str,
    ) -> dict:
        if not raw_query:
            return self._closed("⚠️ 搜索内容不能为空。")
        try:
            plan = await self.plan_builder(raw_query, plan_id)
        except SearchPlanningError as exc:
            code = getattr(exc, "code", str(exc))
            message = _PLANNING_ERROR_MESSAGES.get(
                code,
                "媒体证据无法形成有效计划，请补充信息后重试。",
            )
            if code.startswith("ai_") and getattr(exc, "reason_codes", ()):
                gate_message = _PLANNING_ERROR_MESSAGES.get(
                    exc.reason_codes[0],
                    "严格规则无法唯一确认该条目。",
                )
                message = f"{gate_message.rstrip('。')}；{message}"
            return self._closed(f"❌ 无法生成媒体元数据：{message}")
        except Exception as exc:
            return self._closed(f"❌ 媒体规划失败：{type(exc).__name__}")
        route = resolve_category_route(
            self.config,
            (plan.get("media_metadata", {}).get("placement") or {}).get("category_kind"),
        )
        if not route:
            self.allocator.release(plan_id)
            return self._closed("❌ 媒体分类没有对应保存目录。")
        self.plans[plan_id] = {
            "owner": self._owner_key(request),
            "created_at": time.time(),
            "plan": plan,
            "selected_path": route["path"],
            "results": [],
            "operation_id": operation_id,
        }
        contract = plan["media_metadata"]
        identity = contract["identity"]
        placement = contract["placement"]
        title = identity.get("chinese_title") or identity.get("english_title") or "未知"
        marker = placement.get("mapping_kind") or "unknown"
        return {
            "actions": [{
                "kind": "send_message",
                "text": f"媒体方案：{title} ({identity.get('year') or '年份未知'})\n映射：{marker}\n确认后搜索片源。",
                "data": {"keyboard": [[
                    {"text": "确认并搜索", "callback_data": f"media-search:confirm:{plan_id}"},
                    {"text": "退出", "callback_data": f"media-search:cancel:{plan_id}"},
                ]]},
            }],
            "session": {"state": "close"},
        }

    async def _confirm_and_search(self, plan_id: str, stored: dict) -> dict:
        plan = stored["plan"]
        contract = confirm_media_metadata(plan)
        query = self._english_prowlarr_query(plan, contract)
        media_type = str((contract.get("placement") or {}).get("library_type") or "")
        try:
            items = await asyncio.to_thread(self.release_search, query, media_type)
            limit = int((((self.config.get("search") or {}).get("prowlarr") or {}).get("result_limit") or 8))
            results = self.release_rank(items, limit)
        except Exception as exc:
            self._release_plan(plan_id)
            return self._closed(f"❌ Prowlarr 搜索失败：{type(exc).__name__}")
        if not results:
            self._release_plan(plan_id)
            return self._closed("⚠️ Prowlarr 未找到可用片源。")
        stored["confirmed_contract"] = contract
        stored["results"] = results
        keyboard = [[{
            "text": self._release_label(item, index),
            "callback_data": f"media-search:release:{plan_id}:{index}",
        }] for index, item in enumerate(results)]
        keyboard.append([{
            "text": "退出",
            "callback_data": f"media-search:cancel:{plan_id}",
        }])
        return {
            "actions": [{
                "kind": "edit_message",
                "text": f"找到 {len(results)} 个片源；Prowlarr 查询：{query}",
                "data": {"keyboard": keyboard},
            }]
        }

    async def _submit_release(
        self,
        plan_id: str,
        stored: dict,
        raw_index: str,
        operation_id: str,
    ) -> dict:
        try:
            index = int(raw_index)
            item = stored["results"][index]
        except (ValueError, IndexError):
            raise FeatureError("invalid_release", "selected release is invalid") from None
        try:
            link = await asyncio.to_thread(self.release_resolver, item)
        except Exception as exc:
            return {"actions": [{"kind": "send_message", "text": f"❌ 无法解析下载链接：{type(exc).__name__}"}]}
        if not str(link).startswith("magnet:?"):
            return {"actions": [{"kind": "send_message", "text": "❌ 片源没有可用 magnet 链接。"}]}
        contract = deepcopy(stored["confirmed_contract"])
        identity = contract["identity"]
        operation = self.operations[operation_id]
        handoff = operation.get("handoff_operation")
        if not isinstance(handoff, dict):
            handoff = self._advance_operation(
                operation_id,
                state="handed_off",
                stage="submitting_download",
                status_text="片源已解析，正在交给 115 下载任务。",
                control="cancel",
                next_plugin_id="open115",
            )
            operation["handoff_operation"] = deepcopy(handoff)
        try:
            response = await self.core.report_operation(handoff)
        except Exception as exc:
            if _ambiguous_core_report_error(exc):
                operation["handoff_pending"] = True
            raise
        if not isinstance(response, dict) or response.get("accepted") is not True:
            operation.update({
                "state": "interrupted",
                "status_text": "Core 已结束协调任务，未提交 115 下载。",
                "control": "",
                "next_plugin_id": "",
            })
            raise FeatureError(
                "operation_rejected",
                "Core rejected media-search handoff ownership",
            )
        operation["handoff_pending"] = False
        try:
            result = await self.core.call_capability(
                "download.provider",
                "submit",
                {
                    "link": link,
                    "selected_path": stored["selected_path"],
                    "chat_id": stored["owner"][0],
                    "user_id": stored["owner"][1],
                    "operation_id": operation_id,
                    "operation_revision": handoff["revision"],
                    "media_metadata": contract,
                    "naming_metadata": {
                        "source": "confirmed",
                        "media_type": contract["placement"]["library_type"],
                        "chinese_title": identity.get("chinese_title") or "",
                        "english_title": identity.get("english_title") or "",
                        "year": identity.get("year") or "",
                    },
                    "release": {
                        "title": item.get("title") or "",
                        "indexer": item.get("indexer") or "",
                        "size": item.get("size") or 0,
                    },
                },
                deadline=30,
                idempotency_key=f"{plan_id}:release:{index}",
            )
        except Exception as exc:
            await self._report_operation(
                operation_id,
                state="failed",
                stage="submitting_download",
                status_text=f"下载任务提交失败：{type(exc).__name__}",
                control="",
            )
            raise
        self._release_plan(plan_id)
        return {
            "actions": [{
                "kind": "edit_message",
                "text": f"✅ 已提交下载任务：{result.get('job_id') or plan_id}",
            }]
        }

    async def _build_plan(self, raw_query: str, plan_id: str):
        providers = {
            "wikipedia": self._wikipedia_provider,
            "douban": self._douban_provider,
            "tvdb": self._tvdb_provider,
        }
        return await build_confirmable_search_plan(
            raw_query,
            plan_id,
            providers,
            lambda contract: set((contract.get("evidence") or {}).get("occupied_special_numbers") or []),
            self.allocator,
        )

    def _wikipedia_provider(self, hypotheses: dict):
        config = (((self.config.get("metadata") or {}).get("wikipedia") or {}))
        if not config.get("enable", True):
            return {"source": "wikipedia", "status": "disabled", "facts": [], "source_urls": [], "error": ""}
        queries = ((hypotheses.get("source_queries") or {}).get("wikipedia") or [])
        return lookup_wikipedia_evidence(
            queries,
            languages=tuple(config.get("languages") or ["zh", "en"]),
            timeout=float(config.get("timeout") or 10),
        )

    @staticmethod
    def _douban_provider(hypotheses: dict):
        queries = ((hypotheses.get("source_queries") or {}).get("douban") or [])
        return lookup_douban_evidence(queries, timeout=10.0)

    def _tvdb_provider(self, hypotheses: dict):
        facts = []
        try:
            for hypothesis in hypotheses.get("hypotheses") or []:
                title = hypothesis.get("title") or ""
                year = hypothesis.get("year") or ""
                movies = search_tvdb_movies(title, year=year)[:5]
                series = search_tvdb_series(title, year=year)[:5]
                episodes = {
                    str(item.get("tvdb_series_id")): get_tvdb_series_episodes(str(item.get("tvdb_series_id")))
                    for item in series if item.get("tvdb_series_id")
                }
                if movies or series or any(episodes.values()):
                    facts.append({
                        "hypothesis": hypothesis,
                        "movies": movies,
                        "series": series,
                        "episodes_by_series": episodes,
                    })
        except TvdbConfigError as exc:
            return {"source": "tvdb", "status": "disabled", "facts": [], "source_urls": [], "error": str(exc)}
        except (TvdbRequestError, OSError) as exc:
            return {"source": "tvdb", "status": "server_down", "facts": [], "source_urls": [], "error": str(exc)}
        return {"source": "tvdb", "status": "ok" if facts else "not_found", "facts": facts, "source_urls": [], "error": ""}

    @staticmethod
    def _search_releases(query: str, media_type: str):
        lookup_types = ("movie",) if media_type == "movie" else ("tv",) if media_type == "series" else ("movie", "tv")
        results = []
        seen = set()
        for lookup_type in lookup_types:
            for item in search_prowlarr(query, lookup_type):
                key = item.get("magnet_url") or item.get("download_url") or item.get("title")
                if key and key not in seen:
                    seen.add(key)
                    results.append(item)
        return results

    @staticmethod
    def _english_prowlarr_query(plan: dict, contract: dict) -> str:
        identity = contract.get("identity") or {}
        english = " ".join(str(identity.get("english_title") or "").split())
        year = " ".join(str(identity.get("year") or "").split())
        placement = contract.get("placement") or {}
        if placement.get("library_type") == "series":
            decision = ((contract.get("evidence") or {}).get("decision") or {})
            scope = str(decision.get("scope") or "")
            if scope == "whole_series" and english and _LATIN.search(english):
                return " ".join(item for item in (english, year) if item)
            if decision.get("mode") == "ai" and scope not in {
                "whole_series", "season", "episode"
            }:
                for query in plan.get("prowlarr_queries") or []:
                    query = " ".join(str(query).split())
                    if _LATIN.search(query) and not re.search(r"[\u3400-\u9fff]", query):
                        return query
            season = placement.get("season_number")
            episode = placement.get("episode_number")
            if season is None:
                first = next(iter(contract.get("items") or []), {})
                season = first.get("season_number")
                episode = first.get("episode_number")
            if english and _LATIN.search(english) and season is not None:
                marker = f"S{int(season):02d}"
                if scope != "season" and episode is not None:
                    width = 2 if int(episode) < 100 else 3
                    marker += f"E{int(episode):0{width}d}"
                return f"{english} {marker}"
        if english and _LATIN.search(english):
            return " ".join(item for item in (english, year) if item)
        for query in plan.get("prowlarr_queries") or []:
            query = " ".join(str(query).split())
            if _LATIN.search(query):
                return query
        raise FeatureError("english_title_missing", "Prowlarr search requires an English title")

    def _release_plan(self, plan_id: str):
        self.plans.pop(plan_id, None)
        self.allocator.release(plan_id)

    async def operation_control(self, request: dict) -> dict:
        operation_id = str(request.get("operation_id") or "")
        operation = self.operations.get(operation_id)
        if operation is None:
            raise FeatureError("not_found", "media-search operation was not found")
        if operation.get("state") in {"completed", "cancelled", "failed"}:
            return {"actions": [], "operation": self._operation_view(operation)}
        try:
            operation["revision"] = max(
                int(operation.get("revision") or 0),
                int(request.get("revision") or 0),
            )
        except (TypeError, ValueError):
            pass
        action = str(request.get("action") or "")
        if action not in {"exit", "cancel"}:
            raise FeatureError("invalid_control", "media-search control is invalid")
        owner = (operation["chat_id"], operation["user_id"])
        self.awaiting_queries.discard(owner)
        self.config_wizard.clear({"chat_id": owner[0], "user_id": owner[1]})
        plan_id = str(operation.get("plan_id") or "")
        if plan_id:
            self._release_plan(plan_id)
        task = operation.get("task")
        if task is not None and hasattr(task, "cancel") and not task.done():
            task.cancel()
        if operation.get("state") == "awaiting_input" or task is None:
            terminal = self._advance_operation(
                operation_id,
                state="cancelled",
                stage=operation.get("stage") or "cancelled",
                status_text="已退出 media-search 任务。",
                control="",
            )
            return {"actions": [], "operation": terminal}
        cancelling = self._advance_operation(
            operation_id,
            state="cancelling",
            stage=operation.get("stage") or "cancelling",
            status_text="取消请求已接受，正在停止当前本地任务。",
            control="cancel",
        )
        return {"actions": [], "operation": cancelling}

    async def operation_snapshot(self, request: dict) -> dict:
        requested = str(request.get("operation_id") or "")
        terminal = {"completed", "cancelled", "failed", "handed_off"}
        return {"operations": [
            self._operation_view(operation)
            for operation_id, operation in self.operations.items()
            if operation.get("state") not in terminal
            and (not requested or operation_id == requested)
        ]}

    def _decorate_config_result(self, request, result):
        owner = self._owner_key(request)
        operation = self._operation_for_owner(owner)
        if operation is None:
            return result
        session = result.get("session") if isinstance(result, dict) else None
        if "config_patch" in result:
            view = self._advance_operation(
                operation["operation_id"],
                state="running",
                stage="config_apply",
                status_text="正在保存并重新加载 media-search 配置。",
                control="cancel",
            )
        elif isinstance(session, dict) and session.get("state") == "open":
            wizard_session = self.config_wizard.sessions.get(owner) or {}
            view = self._advance_operation(
                operation["operation_id"],
                state="awaiting_input",
                stage=f"config_{wizard_session.get('stage') or 'input'}",
                status_text="等待 media-search 配置输入。",
                control="exit",
            )
        else:
            view = self._advance_operation(
                operation["operation_id"],
                state="cancelled",
                stage="config_cancelled",
                status_text="已退出 media-search 配置。",
                control="",
            )
        result["operation"] = view
        return result

    def _exit_owner_operation(self, request):
        owner = self._owner_key(request)
        operation = self._operation_for_owner(owner)
        if operation is None:
            return self._closed("⚠️ 搜索会话已失效。")
        self.awaiting_queries.discard(owner)
        plan_id = str(operation.get("plan_id") or "")
        if plan_id:
            self._release_plan(plan_id)
        view = self._advance_operation(
            operation["operation_id"],
            state="cancelled",
            stage=operation.get("stage") or "cancelled",
            status_text="已退出 media-search 任务。",
            control="",
        )
        result = self._closed("已退出 media-search 任务。")
        result["operation"] = view
        return result

    def _new_operation(
        self, request, *, state, stage, status_text, control, kind
    ):
        operation_id = uuid.uuid4().hex
        owner = self._owner_key(request)
        operation = {
            "operation_id": operation_id,
            "chat_id": owner[0],
            "user_id": owner[1],
            "state": state,
            "stage": stage,
            "status_text": status_text,
            "control": control,
            "revision": 1,
            "details": {},
            "kind": kind,
        }
        self.operations[operation_id] = operation
        self.owner_operations[owner] = operation_id
        return self._operation_view(operation)

    def _operation_for_owner(self, owner):
        operation_id = self.owner_operations.get(owner)
        return self.operations.get(operation_id) if operation_id else None

    def _advance_operation(
        self,
        operation_id,
        *,
        state,
        stage,
        status_text,
        control,
        details=None,
        next_plugin_id="",
    ):
        operation = self.operations[operation_id]
        operation.update({
            "state": state,
            "stage": stage,
            "status_text": status_text,
            "control": control,
            "revision": int(operation.get("revision") or 0) + 1,
            "next_plugin_id": next_plugin_id if state == "handed_off" else "",
        })
        if details is not None:
            operation["details"] = deepcopy(details)
        return self._operation_view(operation)

    async def _report_operation(self, operation_id, **changes):
        view = self._advance_operation(operation_id, **changes)
        if view["chat_id"] and view["user_id"]:
            response = await self.core.report_operation(view)
            if not isinstance(response, dict) or response.get("accepted") is not True:
                operation = self.operations[operation_id]
                operation.update({
                    "state": "interrupted",
                    "status_text": "Core 未接受当前 Feature 的任务所有权。",
                    "control": "",
                    "next_plugin_id": "",
                })
                raise FeatureError(
                    "operation_rejected",
                    "Core rejected media-search operation ownership",
                )
        return view

    @staticmethod
    def _operation_view(operation):
        view = {
            "operation_id": str(operation["operation_id"]),
            "chat_id": int(operation.get("chat_id") or 0),
            "user_id": int(operation.get("user_id") or 0),
            "state": str(operation.get("state") or ""),
            "stage": str(operation.get("stage") or ""),
            "status_text": str(operation.get("status_text") or ""),
            "control": str(operation.get("control") or ""),
            "revision": int(operation.get("revision") or 0),
            "details": deepcopy(operation.get("details") or {}),
        }
        if operation.get("next_plugin_id"):
            view["next_plugin_id"] = str(operation["next_plugin_id"])
        return view

    @staticmethod
    def _release_label(item: dict, index: int) -> str:
        title = " ".join(str(item.get("title") or f"Result {index + 1}").split())
        return title[:48]

    @staticmethod
    def _owner_key(request):
        return int(request.get("chat_id") or 0), int(request.get("user_id") or 0)

    @staticmethod
    def _closed(text: str):
        return {"actions": [{"kind": "send_message", "text": text}], "session": {"state": "close"}}
