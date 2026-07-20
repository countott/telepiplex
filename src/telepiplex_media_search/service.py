from __future__ import annotations

import asyncio
import re
import time
import uuid
from copy import deepcopy

from telepiplex_plugin_sdk import FeatureError
from telepiplex_plugin_sdk.media_metadata import resolve_category_route

from .ai import infer_relation_hypotheses_with_ai
from .adapters.douban import (
    lookup_douban_evidence,
    lookup_douban_subject,
)
from .adapters.prowlarr import (
    get_prowlarr_indexer_summary,
    resolve_prowlarr_download_url,
    search_prowlarr,
)
from .adapters.tvdb import (
    TvdbAuthenticationError,
    TvdbConfigError,
    TvdbRequestError,
    get_tvdb_movie,
    get_tvdb_series,
    get_tvdb_series_episodes,
    search_tvdb_movies,
    search_tvdb_series,
)
from .adapters.wikipedia import lookup_wikipedia_evidence
from .config_wizard import MediaSearchConfigWizard
from .context import runtime_context
from .direct_link import DirectLinkError, resolve_direct_link
from .input_contract import classify_search_input
from .planner import SearchPlanningError, build_confirmable_search_plan
from .prowlarr_query import build_prowlarr_query
from .release_gate import gate_releases
from .release_report import format_release_report, release_keyboard
from .release_score import rank_releases
from .search_plan import (
    TemporarySpecialAllocator,
    confirm_media_metadata,
    finalize_search_plan,
)
from .series_scope import (
    SeriesScopeError,
    apply_series_scope,
    series_inventory,
    series_scope_options,
)
from .source_tools import SourceToolGateway


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
    "ambiguous_numeric_role": "片名末尾数字无法证明是正式标题的一部分，请补充年份、完整片名或条目链接。",
    "too_many_candidates": "合格候选超过 7 个，请补充年份、完整片名、电影/剧集类型，或提供豆瓣/TVDB 链接。",
    "unsupported_metadata_link": "链接不是可识别的豆瓣/TVDB作品、季或单集地址。",
    "unsupported_scope_syntax": "不支持范围、1x02 或英文数字单词写法；请使用作品名、S01、S01E01 或数字季/集。",
    "unsupported_special_scope": "暂不支持 Special、Season 0、OVA、OAD 或附加内容下载。",
    "direct_link_not_found": "无法读取该豆瓣/TVDB条目，请检查链接是否有效。",
    "direct_link_invalid": "链接条目缺少可验证的标题或稳定ID。",
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
        indexer_summary=None,
    ):
        self.config = config
        self.core = core
        self.allocator = TemporarySpecialAllocator()
        self.plan_builder = plan_builder or self._build_plan
        self.release_search = release_search or self._search_releases
        self.release_rank = release_rank or rank_releases
        self.release_resolver = release_resolver or resolve_prowlarr_download_url
        self.indexer_summary = (
            indexer_summary or get_prowlarr_indexer_summary
        )
        self.plans = {}
        self.awaiting_queries = set()
        self.awaiting_scope_inputs = {}
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
        probe = (
            payload.get("probe")
            if isinstance(payload.get("probe"), dict)
            else {}
        )
        if probe and runtime_context.logger:
            runtime_context.logger.info(
                "metadata_probe "
                f"content_shape={str(probe.get('content_shape') or 'unknown')} "
                f"seasons={len(probe.get('observed_seasons') or [])} "
                f"episodes={len(probe.get('observed_episodes') or [])} "
                f"videos={str(probe.get('video_count') or 0)}"
            )
        plan_id = f"cap-{uuid.uuid4().hex[:10]}"
        try:
            plan = await self.plan_builder(raw_query, plan_id)
        except SearchPlanningError as exc:
            raise FeatureError(
                "metadata_unresolved",
                f"metadata resolution failed: {exc.code}",
            ) from exc
        candidates = [
            item
            for item in plan.get("candidates") or []
            if item.get("selectable") is not False
        ]
        if len(candidates) != 1:
            raise FeatureError(
                "metadata_unresolved",
                "noninteractive metadata resolution requires exactly one candidate",
            )
        selected = candidates[0]
        selected_plan = {
            "plan_id": plan_id,
            "media_metadata": deepcopy(selected.get("media_metadata") or {}),
            "prowlarr_queries": list(selected.get("prowlarr_queries") or []),
        }
        contract = selected_plan["media_metadata"]
        placement = contract.get("placement") or {}
        if placement.get("mapping_kind") == "temporary_related_special":
            prefix = (
                "animated"
                if str(placement.get("category_kind") or "").startswith("animated_")
                else "live_action"
            )
            placement.update({
                "library_type": "movie",
                "category_kind": f"{prefix}_movie",
                "season_number": None,
                "episode_number": None,
                "mapping_kind": "standalone",
                "mapping_source": "noninteractive_standalone",
                "tvdb_episode_id": "",
            })
            contract["items"] = []
        elif placement.get("library_type") == "series":
            decision = ((contract.get("evidence") or {}).get("decision") or {})
            scope = str(decision.get("scope") or "movie_or_series")
            if scope == "episode":
                contract = apply_series_scope(
                    contract,
                    "episode",
                    season_number=decision.get("season_number"),
                    episode_number=decision.get("episode_number"),
                )
            elif scope == "season":
                contract = apply_series_scope(
                    contract,
                    "season",
                    season_number=decision.get("season_number"),
                )
            elif scope == "whole_series":
                contract = apply_series_scope(contract, "whole_series")
            else:
                raise FeatureError(
                    "metadata_unresolved",
                    "series metadata resolution requires an explicit scope",
                )
            selected_plan["media_metadata"] = contract
        try:
            contract = confirm_media_metadata(selected_plan)
        except ValueError as exc:
            raise FeatureError(
                "metadata_unresolved",
                "resolved metadata did not pass the canonical contract",
            ) from exc
        identity = contract["identity"]
        return {
            "media_metadata": contract,
            "naming_metadata": {
                "source": "media-search-live",
                "media_type": (
                    (contract.get("retrieval") or {}).get("media_type")
                    or contract["placement"]["library_type"]
                ),
                "chinese_title": identity.get("chinese_title") or "",
                "english_title": identity.get("english_title") or "",
                "year": identity.get("year") or "",
            },
            "source_queries": deepcopy(plan.get("source_queries") or {}),
            "evidence": deepcopy(contract.get("evidence") or {}),
        }

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
        if key in self.awaiting_scope_inputs:
            return self._handle_scope_input(request, key)
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
        if action == "browse" and len(parts) == 3:
            return self._browse_candidate(plan_id, stored, parts[2])
        if action == "select" and len(parts) == 3:
            return await self._select_candidate(plan_id, stored, parts[2])
        if action == "scope" and len(parts) == 3:
            return self._scope_callback(plan_id, stored, parts[2], request)
        if action == "placement" and len(parts) == 3:
            return self._placement_callback(plan_id, stored, parts[2])
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
        candidates = plan.get("candidates") if isinstance(plan.get("candidates"), list) else []
        if not candidates:
            candidates = [{
                "candidate_key": f"legacy:{plan_id}",
                "score": {"total": 100},
                "recommended": True,
                "selectable": True,
                "media_metadata": plan.get("media_metadata") or {},
                "prowlarr_queries": list(plan.get("prowlarr_queries") or []),
                "poster_url": (plan.get("media_metadata") or {}).get("identity", {}).get("poster_url") or "",
                "reasons": [],
            }]
        selectable = [item for item in candidates if item.get("selectable") is not False]
        if not selectable:
            self.allocator.release(plan_id)
            return self._closed("❌ 候选最高评分低于 65，受控检索后仍不足以安全确认。")
        route = resolve_category_route(
            self.config,
            (selectable[0].get("media_metadata", {}).get("placement") or {}).get("category_kind"),
        )
        if not route:
            self.allocator.release(plan_id)
            return self._closed("❌ 媒体分类没有对应保存目录。")
        self.plans[plan_id] = {
            "owner": self._owner_key(request),
            "created_at": time.time(),
            "plan": plan,
            "candidates": tuple(deepcopy(candidates)),
            "selected_path": route["path"],
            "results": [],
            "operation_id": operation_id,
        }
        action = self._candidate_action(self.plans[plan_id], 0, edit=False)
        return {
            "actions": [action],
            "session": {"state": "close"},
        }

    def _candidate_action(self, stored: dict, index: int, *, edit: bool) -> dict:
        candidates = stored["candidates"]
        candidate = candidates[index]
        contract = candidate["media_metadata"]
        identity = contract.get("identity") or {}
        placement = contract.get("placement") or {}
        score = candidate.get("score") or {}
        title = identity.get("chinese_title") or identity.get("english_title") or "未知"
        relation = (contract.get("relation") or {}).get("type") or "standalone"
        recommended = " · 推荐" if candidate.get("recommended") else ""
        text = (
            f"候选 {index + 1}/{len(candidates)}{recommended}\n"
            f"{title} ({identity.get('year') or '年份未知'})\n"
            f"类型：{placement.get('library_type') or '未知'} · 关系：{relation}\n"
            f"评分：{score.get('total', 0)}/100"
        )
        navigation = []
        if len(candidates) > 1:
            navigation = [{
                "text": "上一项",
                "callback_data": f"media-search:browse:{stored['plan']['plan_id']}:{(index - 1) % len(candidates)}",
            }, {
                "text": "下一项",
                "callback_data": f"media-search:browse:{stored['plan']['plan_id']}:{(index + 1) % len(candidates)}",
            }]
        keyboard = [navigation] if navigation else []
        if candidate.get("selectable") is not False:
            callback_data = (
                f"media-search:confirm:{stored['plan']['plan_id']}"
                if str(candidate.get("candidate_key") or "").startswith("legacy:")
                else f"media-search:select:{stored['plan']['plan_id']}:{index}"
            )
            keyboard.append([{
                "text": "选择并搜索片源",
                "callback_data": callback_data,
            }])
        keyboard.append([{
            "text": "退出",
            "callback_data": f"media-search:cancel:{stored['plan']['plan_id']}",
        }])
        poster = str(candidate.get("poster_url") or "")
        data = {"keyboard": keyboard, "candidate_key": candidate.get("candidate_key") or ""}
        if poster.startswith("https://"):
            data["photo_url"] = poster
            kind = "edit_photo" if edit else "send_photo"
        else:
            kind = "edit_message" if edit else "send_message"
        return {"kind": kind, "text": text, "data": data}

    def _browse_candidate(self, plan_id: str, stored: dict, raw_index: str) -> dict:
        try:
            index = int(raw_index)
            stored["candidates"][index]
        except (ValueError, IndexError):
            raise FeatureError("invalid_candidate", "selected candidate is invalid") from None
        action = self._candidate_action(stored, index, edit=True)
        operation = self._advance_operation(
            stored["operation_id"],
            state="awaiting_input",
            stage="candidate_selection",
            status_text=action["text"],
            control="exit",
            details=deepcopy(action["data"]),
        )
        return {"actions": [action], "operation": operation}

    async def _select_candidate(
        self, plan_id: str, stored: dict, raw_index: str
    ) -> dict:
        try:
            index = int(raw_index)
            candidate = deepcopy(stored["candidates"][index])
        except (ValueError, IndexError):
            raise FeatureError("invalid_candidate", "selected candidate is invalid") from None
        if candidate.get("selectable") is False:
            return {"actions": [self._candidate_action(stored, index, edit=True)]}
        selected_plan = {
            "plan_id": plan_id,
            "media_metadata": candidate["media_metadata"],
            "prowlarr_queries": list(candidate.get("prowlarr_queries") or []),
            "source_queries": deepcopy(stored["plan"].get("source_queries") or {}),
        }
        try:
            await self._apply_selected_relation(candidate, selected_plan, stored)
            contract = selected_plan["media_metadata"]
            placement = contract.get("placement") or {}
            if placement.get("mapping_kind") == "temporary_related_special":
                stored["plan"] = selected_plan
                stored["selected_candidate_key"] = candidate.get("candidate_key") or ""
                return self._related_placement_action(plan_id, stored)
            if placement.get("library_type") == "series":
                if not contract.get("items"):
                    raise SeriesScopeError("tvdb_scope_not_verified")
                decision = ((contract.get("evidence") or {}).get("decision") or {})
                scope = str(decision.get("scope") or "movie_or_series")
                if scope == "episode":
                    selected_plan["media_metadata"] = apply_series_scope(
                        contract,
                        "episode",
                        season_number=decision.get("season_number"),
                        episode_number=decision.get("episode_number"),
                    )
                elif scope == "whole_series":
                    selected_plan["media_metadata"] = apply_series_scope(
                        contract, "whole_series"
                    )
                else:
                    stored["plan"] = selected_plan
                    stored["selected_candidate_key"] = (
                        candidate.get("candidate_key") or ""
                    )
                    return self._series_scope_action(plan_id, stored)
        except (ValueError, SeriesScopeError):
            action = self._candidate_action(stored, index, edit=True)
            action["text"] += "\n❌ TVDB 无法验证该剧集的季集范围，请重试或提供 TVDB 链接。"
            return {"actions": [action]}
        stored["plan"] = selected_plan
        stored["selected_candidate_key"] = candidate.get("candidate_key") or ""
        return self._start_selected_release(plan_id, stored)

    async def _apply_selected_relation(
        self,
        candidate: dict,
        selected_plan: dict,
        stored: dict,
    ) -> None:
        contract = selected_plan["media_metadata"]
        placement = contract.get("placement") or {}
        identity = contract.get("identity") or {}
        if (
            placement.get("mapping_kind") != "standalone"
            or identity.get("content_kind") != "movie"
        ):
            return
        pool = stored["plan"].get("relation_pool") or []
        selected_key = str(candidate.get("candidate_key") or "")
        selected = next(
            (
                item
                for item in pool
                if str(item.get("candidate_key") or "") == selected_key
            ),
            None,
        )
        targets = [
            item
            for item in pool
            if item.get("media_type") == "series"
            and str(
                ((item.get("identity") or {}).get("external_ids") or {}).get(
                    "tvdb"
                )
                or ""
            )
            and str(item.get("candidate_key") or "") != selected_key
        ]
        if not selected or not targets:
            return
        selected_signal_facts = {
            str(fact.get("fact_id") or "")
            for fact in selected.get("facts") or []
            if fact.get("complex_signals")
        }
        if not selected_signal_facts:
            return
        try:
            configured_timeout = float(
                ((self.config.get("ai") or {}).get("relation_timeout") or 5)
            )
        except (TypeError, ValueError):
            configured_timeout = 5
        try:
            async with asyncio.timeout(max(1, min(configured_timeout, 15))):
                payload = await asyncio.to_thread(
                    infer_relation_hypotheses_with_ai,
                    {
                        "raw_query": stored["plan"].get("raw_query") or "",
                        "selected_candidate_key": selected_key,
                        "candidates": [
                            {
                                key: value
                                for key, value in item.items()
                                if key in {
                                    "candidate_key", "fact_ids", "facts"
                                }
                            }
                            for item in (selected, *targets)
                        ],
                    },
                )
        except Exception:
            return
        hypotheses = (
            payload.get("hypotheses")
            if isinstance(payload, dict)
            else []
        )
        allowed_relations = {
            "prequel", "sequel", "spin_off", "special", "extension_movie",
        }
        target_by_key = {
            str(item.get("candidate_key") or ""): item for item in targets
        }
        known_fact_ids = {
            str(fact.get("fact_id") or "")
            for item in (selected, *targets)
            for fact in item.get("facts") or []
        }
        verified = None
        for hypothesis in hypotheses or []:
            if not isinstance(hypothesis, dict):
                continue
            fact_ids = hypothesis.get("fact_ids")
            target = target_by_key.get(
                str(hypothesis.get("target_candidate_key") or "")
            )
            if (
                str(hypothesis.get("candidate_key") or "") != selected_key
                or target is None
                or hypothesis.get("relation_type") not in allowed_relations
                or not isinstance(fact_ids, list)
                or not fact_ids
                or not set(fact_ids).issubset(known_fact_ids)
                or not set(fact_ids).intersection(selected_signal_facts)
            ):
                continue
            verified = (hypothesis, target)
            break
        if verified is None:
            return
        hypothesis, target = verified
        target_identity = deepcopy(target.get("identity") or {})
        relation_type = str(hypothesis["relation_type"])
        contract["identity"]["content_kind"] = {
            "prequel": "prequel_movie",
            "sequel": "sequel_movie",
            "spin_off": "spin_off",
            "special": "special",
            "extension_movie": "extension_movie",
        }[relation_type]
        contract["relation"] = {
            "type": relation_type,
            "target_series": target_identity,
            "source": "selected_candidate_source_verified_ai_hint",
        }
        prefix = (
            "animated"
            if str(placement.get("category_kind") or "").startswith("animated_")
            else "live_action"
        )
        placement.update({
            "library_type": "series",
            "category_kind": f"{prefix}_series",
            "season_number": 0,
            "episode_number": None,
            "mapping_kind": "temporary_related_special",
            "mapping_source": "selected_candidate_relation",
            "tvdb_episode_id": "",
        })
        (contract.get("evidence") or {}).setdefault("decision", {})[
            "relation_fact_ids"
        ] = list(dict.fromkeys(hypothesis.get("fact_ids") or []))

    def _series_scope_action(self, plan_id: str, stored: dict) -> dict:
        contract = stored["plan"]["media_metadata"]
        options = series_scope_options(contract)
        inventory = series_inventory(contract)
        decision = ((contract.get("evidence") or {}).get("decision") or {})
        scope = str(decision.get("scope") or "movie_or_series")
        labels = {
            "whole_series": "全剧（推荐）",
            "season": "指定季",
            "episode": "指定集",
            "season_all": "搜索整季（推荐）",
            "season_episode": "指定单集",
        }
        keyboard = [[{
            "text": labels[choice],
            "callback_data": f"media-search:scope:{plan_id}:{choice}",
        }] for choice in options]
        keyboard.append([{
            "text": "退出",
            "callback_data": f"media-search:cancel:{plan_id}",
        }])
        if scope == "season":
            season = decision.get("season_number")
            aired = inventory.aired_by_season.get(int(season or 0), ())
            total = inventory.all_by_season.get(int(season or 0), ())
            text = (
                f"已确认第 {season} 季：共 {len(total)} 集，"
                f"当前已播 {len(aired)} 集。请选择下载范围。"
            )
        else:
            text = (
                f"已确认剧集，共 {len(inventory.seasons)} 季。"
                "请选择本次下载范围。"
            )
        action = {
            "kind": "edit_message",
            "text": text,
            "data": {"keyboard": keyboard},
        }
        operation = self._advance_operation(
            stored["operation_id"],
            state="awaiting_input",
            stage="series_scope",
            status_text=text,
            control="exit",
            details=deepcopy(action["data"]),
        )
        return {"actions": [action], "operation": operation}

    def _related_placement_action(self, plan_id: str, stored: dict) -> dict:
        contract = stored["plan"]["media_metadata"]
        relation = contract.get("relation") or {}
        target = relation.get("target_series") or {}
        target_title = (
            target.get("chinese_title")
            or target.get("english_title")
            or "目标剧集"
        )
        text = (
            f"已验证该电影与《{target_title}》存在"
            f"{relation.get('type') or '关联'}关系。请选择本次整理方式；"
            "无论如何，Prowlarr 都按电影标题和年份检索。"
        )
        data = {"keyboard": [
            [{
                "text": f"归入《{target_title}》Specials（推荐）",
                "callback_data": f"media-search:placement:{plan_id}:special",
            }],
            [{
                "text": "按独立电影整理",
                "callback_data": f"media-search:placement:{plan_id}:standalone",
            }],
            [{
                "text": "退出",
                "callback_data": f"media-search:cancel:{plan_id}",
            }],
        ]}
        action = {"kind": "edit_message", "text": text, "data": data}
        operation = self._advance_operation(
            stored["operation_id"],
            state="awaiting_input",
            stage="related_movie_placement",
            status_text=text,
            control="exit",
            details=deepcopy(data),
        )
        return {"actions": [action], "operation": operation}

    def _placement_callback(
        self,
        plan_id: str,
        stored: dict,
        choice: str,
    ) -> dict:
        plan = deepcopy(stored["plan"])
        contract = plan["media_metadata"]
        placement = contract.get("placement") or {}
        if choice == "standalone":
            prefix = (
                "animated"
                if str(placement.get("category_kind") or "").startswith("animated_")
                else "live_action"
            )
            placement.update({
                "library_type": "movie",
                "category_kind": f"{prefix}_movie",
                "season_number": None,
                "episode_number": None,
                "mapping_kind": "standalone",
                "mapping_source": "user_selected_standalone",
                "tvdb_episode_id": "",
            })
            contract["items"] = []
        elif choice == "special":
            official = (
                (contract.get("evidence") or {}).get(
                    "tvdb_official_special_candidates"
                )
                or []
            )
            if len(official) == 1:
                selected = official[0]
                placement.update({
                    "season_number": 0,
                    "episode_number": int(selected.get("episode_number") or 0),
                    "mapping_kind": "tvdb_official",
                    "mapping_source": "tvdb_official",
                    "tvdb_episode_id": str(selected.get("episode_id") or ""),
                })
                contract["items"] = [{
                    "item_id": str(selected.get("episode_id") or ""),
                    "content_role": "special",
                    "season_number": 0,
                    "episode_number": int(selected.get("episode_number") or 0),
                }]
            else:
                try:
                    plan = finalize_search_plan(
                        plan,
                        self.allocator,
                        set(
                            (contract.get("evidence") or {}).get(
                                "occupied_special_numbers"
                            )
                            or []
                        ),
                    )
                except ValueError:
                    return self._closed("❌ 无法为本次任务分配临时 Special 编号。")
        else:
            raise FeatureError(
                "invalid_callback", "related movie placement is invalid"
            )
        stored["plan"] = plan
        return self._start_selected_release(plan_id, stored)

    def _scope_callback(
        self,
        plan_id: str,
        stored: dict,
        choice: str,
        request: dict,
    ) -> dict:
        contract = stored["plan"]["media_metadata"]
        decision = ((contract.get("evidence") or {}).get("decision") or {})
        if choice == "whole_series":
            stored["plan"]["media_metadata"] = apply_series_scope(
                contract, "whole_series"
            )
            return self._start_selected_release(plan_id, stored)
        if choice == "season_all":
            stored["plan"]["media_metadata"] = apply_series_scope(
                contract,
                "season",
                season_number=decision.get("season_number"),
            )
            return self._start_selected_release(plan_id, stored)
        if choice == "season":
            return self._scope_input_action(
                plan_id, stored, request, phase="season"
            )
        if choice == "season_episode":
            return self._scope_input_action(
                plan_id,
                stored,
                request,
                phase="episode",
                season_number=decision.get("season_number"),
            )
        if choice == "episode":
            seasons = series_inventory(contract).seasons
            if len(seasons) == 1:
                return self._scope_input_action(
                    plan_id,
                    stored,
                    request,
                    phase="episode",
                    season_number=seasons[0],
                )
            return self._scope_input_action(
                plan_id, stored, request, phase="episode_season"
            )
        raise FeatureError("invalid_callback", "series scope choice is invalid")

    def _scope_input_action(
        self,
        plan_id: str,
        stored: dict,
        request: dict,
        *,
        phase: str,
        season_number=None,
    ) -> dict:
        owner = self._owner_key(request)
        self.awaiting_scope_inputs[owner] = {
            "plan_id": plan_id,
            "phase": phase,
            "season_number": season_number,
        }
        text = {
            "season": "请输入季号，例如：2",
            "episode_season": "请先输入季号，例如：2",
            "episode": f"请输入第 {season_number} 季的集号，例如：3",
        }[phase]
        action = {
            "kind": "edit_message",
            "text": text,
            "data": {"keyboard": [[{
                "text": "退出",
                "callback_data": f"media-search:cancel:{plan_id}",
            }]]},
        }
        operation = self._advance_operation(
            stored["operation_id"],
            state="awaiting_input",
            stage="series_scope_number",
            status_text=text,
            control="exit",
            details=deepcopy(action["data"]),
        )
        return {
            "actions": [action],
            "session": {"state": "open"},
            "operation": operation,
        }

    def _handle_scope_input(self, request: dict, owner) -> dict:
        pending = self.awaiting_scope_inputs.get(owner) or {}
        plan_id = str(pending.get("plan_id") or "")
        stored = self.plans.get(plan_id)
        if not stored:
            self.awaiting_scope_inputs.pop(owner, None)
            return self._closed("⚠️ 搜索任务已过期，请重新搜索。")
        raw = " ".join(str(request.get("text") or "").split())
        if not raw.isdigit() or int(raw) < 1:
            return {
                "actions": [{"kind": "send_message", "text": "请输入大于 0 的数字。"}],
                "session": {"state": "open"},
            }
        number = int(raw)
        contract = stored["plan"]["media_metadata"]
        inventory = series_inventory(contract)
        phase = pending.get("phase")
        if phase in {"season", "episode_season"}:
            if number not in inventory.seasons:
                return {
                    "actions": [{"kind": "send_message", "text": "该季不存在，请重新输入。"}],
                    "session": {"state": "open"},
                }
            if phase == "episode_season":
                return self._scope_input_action(
                    plan_id,
                    stored,
                    request,
                    phase="episode",
                    season_number=number,
                )
            self.awaiting_scope_inputs.pop(owner, None)
            stored["plan"]["media_metadata"] = apply_series_scope(
                contract, "season", season_number=number
            )
            return self._start_selected_release(plan_id, stored)
        if phase == "episode":
            try:
                scoped = apply_series_scope(
                    contract,
                    "episode",
                    season_number=pending.get("season_number"),
                    episode_number=number,
                )
            except SeriesScopeError as exc:
                message = (
                    "该集尚未播出，请输入已播集号。"
                    if str(exc) == "episode_not_aired"
                    else "该集不存在，请重新输入。"
                )
                return {
                    "actions": [{"kind": "send_message", "text": message}],
                    "session": {"state": "open"},
                }
            self.awaiting_scope_inputs.pop(owner, None)
            stored["plan"]["media_metadata"] = scoped
            return self._start_selected_release(plan_id, stored)
        raise FeatureError("invalid_state", "series scope input state is invalid")

    def _start_selected_release(self, plan_id: str, stored: dict) -> dict:
        contract = stored["plan"]["media_metadata"]
        route = resolve_category_route(
            self.config,
            (contract.get("placement") or {}).get("category_kind"),
        )
        if not route:
            self._release_plan(plan_id)
            return self._closed("❌ 媒体分类没有对应保存目录。")
        stored["selected_path"] = route["path"]
        return self._start_release_search_task(plan_id, stored)

    async def _confirm_and_search(self, plan_id: str, stored: dict) -> dict:
        plan = stored["plan"]
        contract = confirm_media_metadata(plan)
        query = self._english_prowlarr_query(plan, contract)
        media_type = str(
            (contract.get("retrieval") or {}).get("media_type")
            or (contract.get("placement") or {}).get("library_type")
            or ""
        )
        try:
            raw_items = await asyncio.to_thread(
                self.release_search,
                query,
                media_type,
            )
        except Exception as exc:
            self._release_plan(plan_id)
            return self._closed(f"❌ Prowlarr 搜索失败：{type(exc).__name__}")
        try:
            indexer_summary = await asyncio.to_thread(
                self.indexer_summary,
                raw_items,
            )
        except Exception as exc:
            indexer_summary = {
                "enabled_indexers": [],
                "result_sources": {},
                "down_indexers": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        gate = gate_releases(raw_items, contract)
        try:
            configured_limit = int(
                (((self.config.get("search") or {}).get("prowlarr") or {})
                 .get("result_limit") or 12)
            )
        except (TypeError, ValueError):
            configured_limit = 12
        limit = min(12, max(1, configured_limit))
        results = self.release_rank(list(gate.eligible), limit)
        text = format_release_report(
            query,
            gate,
            results,
            indexer_summary,
        )
        if not results:
            self._release_plan(plan_id)
            return self._closed(text)
        stored["confirmed_contract"] = contract
        stored["results"] = results
        stored["gate_report"] = gate
        stored["indexer_summary"] = indexer_summary
        keyboard = release_keyboard(plan_id, len(results))
        return {
            "actions": [{
                "kind": "edit_message",
                "text": text,
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
        parsed = classify_search_input(raw_query)
        if parsed.kind in {"invalid_link", "unsupported_text"}:
            raise SearchPlanningError(parsed.reason)
        locked_identity = None
        planning_query = raw_query
        source_gateway = self._source_tool_gateway()
        if parsed.kind == "link":
            try:
                direct = await asyncio.to_thread(resolve_direct_link, parsed.link)
            except DirectLinkError as exc:
                raise SearchPlanningError(str(exc)) from exc
            planning_query = direct.query
            locked_identity = direct.stable_identity
            providers[direct.provider] = lambda _hypotheses: direct.evidence
            source_gateway = None
        return await build_confirmable_search_plan(
            planning_query,
            plan_id,
            providers,
            lambda contract: set((contract.get("evidence") or {}).get("occupied_special_numbers") or []),
            self.allocator,
            locked_identity=locked_identity,
            source_gateway=source_gateway,
        )

    def _wikipedia_provider(self, hypotheses: dict):
        config = (((self.config.get("metadata") or {}).get("wikipedia") or {}))
        if not config.get("enable", True):
            return {"source": "wikipedia", "status": "disabled", "facts": [], "source_urls": [], "error": ""}
        source_queries = hypotheses.get("source_queries") or {}
        zh_queries = source_queries.get("wikipedia_zh")
        en_queries = source_queries.get("wikipedia_en")
        timeout = float(config.get("timeout") or 10)
        if isinstance(zh_queries, list) or isinstance(en_queries, list):
            results = []
            configured = tuple(config.get("languages") or ["zh", "en"])
            if "zh" in configured and zh_queries:
                results.append(lookup_wikipedia_evidence(
                    zh_queries,
                    languages=("zh",),
                    timeout=timeout,
                ))
            if "en" in configured and en_queries:
                results.append(lookup_wikipedia_evidence(
                    en_queries,
                    languages=("en",),
                    timeout=timeout,
                ))
            if results:
                return self._merge_source_results("wikipedia", results)
        queries = source_queries.get("wikipedia") or []
        return lookup_wikipedia_evidence(
            queries,
            languages=tuple(config.get("languages") or ["zh", "en"]),
            timeout=timeout,
        )

    def _douban_provider(self, hypotheses: dict):
        queries = ((hypotheses.get("source_queries") or {}).get("douban") or [])
        config = ((self.config.get("metadata") or {}).get("douban") or {})
        if not config.get("enable", True):
            return {
                "source": "douban",
                "status": "disabled",
                "facts": [],
                "source_urls": [],
                "error": "",
            }
        return lookup_douban_evidence(
            queries,
            timeout=float(config.get("timeout") or 10),
            cache_ttl=float(config.get("cache_ttl") or 900),
            max_concurrency=int(config.get("max_concurrency") or 2),
            circuit_breaker_failures=int(
                config.get("circuit_breaker_failures") or 3
            ),
            circuit_breaker_seconds=float(
                config.get("circuit_breaker_seconds") or 300
            ),
        )

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
            return {
                "source": "tvdb",
                "status": exc.code,
                "facts": [],
                "source_urls": [],
                "error": str(exc),
            }
        except TvdbAuthenticationError as exc:
            return {
                "source": "tvdb",
                "status": "authentication_failed",
                "facts": [],
                "source_urls": [],
                "error": str(exc),
            }
        except TvdbRequestError as exc:
            return {
                "source": "tvdb",
                "status": exc.code,
                "facts": [],
                "source_urls": [],
                "error": str(exc),
            }
        except OSError as exc:
            return {"source": "tvdb", "status": "server_down", "facts": [], "source_urls": [], "error": str(exc)}
        return {"source": "tvdb", "status": "ok" if facts else "not_found", "facts": facts, "source_urls": [], "error": ""}

    @staticmethod
    def _merge_source_results(source: str, results: list[dict]) -> dict:
        facts = []
        urls = []
        errors = []
        statuses = []
        for result in results:
            if not isinstance(result, dict):
                continue
            statuses.append(str(result.get("status") or "server_down"))
            facts.extend(
                item
                for item in (result.get("facts") or [])
                if isinstance(item, dict)
            )
            for url in result.get("source_urls") or []:
                if url and url not in urls:
                    urls.append(url)
            if result.get("error"):
                errors.append(str(result["error"]))
        if facts:
            status = "ok"
        elif "not_found" in statuses:
            status = "not_found"
        else:
            status = next(iter(statuses), "server_down")
        return {
            "source": source,
            "status": status,
            "facts": facts,
            "source_urls": urls,
            "error": "; ".join(errors),
        }

    def _targeted_wikipedia(self, arguments: dict) -> dict:
        queries = list(arguments.get("queries") or [])
        return self._wikipedia_provider({
            "source_queries": {
                "wikipedia_zh": queries,
                "wikipedia_en": queries,
            },
        })

    def _targeted_douban_subject(self, arguments: dict) -> dict:
        config = ((self.config.get("metadata") or {}).get("douban") or {})
        facts = []
        for subject_id in arguments.get("subject_ids") or []:
            fact = lookup_douban_subject(
                subject_id,
                timeout=float(config.get("timeout") or 10),
                cache_ttl=float(config.get("cache_ttl") or 900),
                max_concurrency=int(config.get("max_concurrency") or 2),
            )
            if fact:
                facts.append(fact)
        return {
            "source": "douban",
            "status": "ok" if facts else "not_found",
            "facts": facts,
            "source_urls": [
                item.get("url") for item in facts if item.get("url")
            ],
            "error": "",
        }

    def _targeted_tvdb_entity(self, arguments: dict) -> dict:
        facts = []
        try:
            for query in arguments.get("queries") or []:
                title = query.get("title") or ""
                year = query.get("year") or ""
                media_type = query.get("media_type") or "unknown"
                movies = (
                    search_tvdb_movies(title, year)
                    if media_type in {"movie", "unknown"}
                    else []
                )
                series = (
                    search_tvdb_series(title, year)
                    if media_type in {"series", "unknown"}
                    else []
                )
                facts.append({
                    "movies": movies[:5],
                    "series": series[:5],
                    "episodes_by_series": {},
                })
            for entity in arguments.get("entity_ids") or []:
                media_type = entity.get("media_type")
                entity_id = entity.get("tvdb_id")
                item = (
                    get_tvdb_series(entity_id)
                    if media_type == "series"
                    else get_tvdb_movie(entity_id)
                )
                if item:
                    facts.append({
                        "movies": [item] if media_type == "movie" else [],
                        "series": [item] if media_type == "series" else [],
                        "episodes_by_series": (
                            {str(entity_id): item.get("episodes") or []}
                            if media_type == "series"
                            else {}
                        ),
                    })
        except TvdbConfigError as exc:
            return {
                "source": "tvdb",
                "status": exc.code,
                "facts": [],
                "source_urls": [],
                "error": str(exc),
            }
        except TvdbRequestError as exc:
            return {
                "source": "tvdb",
                "status": exc.code,
                "facts": [],
                "source_urls": [],
                "error": str(exc),
            }
        return {
            "source": "tvdb",
            "status": "ok" if facts else "not_found",
            "facts": facts,
            "source_urls": [],
            "error": "",
        }

    def _targeted_tvdb_episodes(self, arguments: dict) -> dict:
        facts = []
        try:
            for series_id in arguments.get("series_ids") or []:
                episodes = get_tvdb_series_episodes(series_id)
                facts.append({
                    "movies": [],
                    "series": [{
                        "tvdb_series_id": str(series_id),
                        "media_type": "series",
                    }],
                    "episodes_by_series": {
                        str(series_id): episodes,
                    },
                })
        except TvdbConfigError as exc:
            return {
                "source": "tvdb",
                "status": exc.code,
                "facts": [],
                "source_urls": [],
                "error": str(exc),
            }
        except TvdbRequestError as exc:
            return {
                "source": "tvdb",
                "status": exc.code,
                "facts": [],
                "source_urls": [],
                "error": str(exc),
            }
        return {
            "source": "tvdb",
            "status": "ok" if facts else "not_found",
            "facts": facts,
            "source_urls": [],
            "error": "",
        }

    def _source_tool_gateway(self) -> SourceToolGateway:
        return SourceToolGateway(
            {
                "wikipedia": self._wikipedia_provider,
                "douban": self._douban_provider,
                "tvdb": self._tvdb_provider,
            },
            targeted_handlers={
                "lookup_wikipedia_entity": self._targeted_wikipedia,
                "lookup_douban_subject": self._targeted_douban_subject,
                "lookup_tvdb_entity": self._targeted_tvdb_entity,
                "lookup_tvdb_episodes": self._targeted_tvdb_episodes,
            },
            config=self.config,
            logger=runtime_context.logger,
        )

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
        del plan
        retrieval = contract.get("retrieval") or {}
        identity = contract.get("identity") or {}
        english = " ".join(str(identity.get("english_title") or "").split())
        if not english or not _LATIN.search(english):
            raise FeatureError(
                "english_title_missing",
                "Prowlarr search requires a canonical Latin title",
            )
        media_type = str(retrieval.get("media_type") or "")
        scope = str(retrieval.get("scope") or "work")
        if media_type == "movie":
            scope = "movie"
        decision = ((contract.get("evidence") or {}).get("decision") or {})
        placement = contract.get("placement") or {}
        season = decision.get("season_number")
        episode = decision.get("episode_number")
        if season is None:
            season = placement.get("season_number")
        if episode is None:
            episode = placement.get("episode_number")
        if scope in {"season", "episode"} and season is None:
            items = contract.get("items") or []
            first = next(
                (item for item in items if isinstance(item, dict)),
                {},
            )
            season = first.get("season_number")
            if episode is None:
                episode = first.get("episode_number")
        try:
            return build_prowlarr_query(
                english,
                scope,
                season_number=season,
                episode_number=episode,
            )
        except (TypeError, ValueError) as exc:
            raise FeatureError(
                "bounded_scope_incomplete",
                "Prowlarr search scope is incomplete",
            ) from exc

    def _release_plan(self, plan_id: str):
        self.plans.pop(plan_id, None)
        for owner, pending in tuple(self.awaiting_scope_inputs.items()):
            if str(pending.get("plan_id") or "") == plan_id:
                self.awaiting_scope_inputs.pop(owner, None)
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
    def _owner_key(request):
        return int(request.get("chat_id") or 0), int(request.get("user_id") or 0)

    @staticmethod
    def _closed(text: str):
        return {"actions": [{"kind": "send_message", "text": text}], "session": {"state": "close"}}
