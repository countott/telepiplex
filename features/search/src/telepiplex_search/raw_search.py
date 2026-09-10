"""Direct queries within Search, sharing its operation and download lifecycle."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import math
import time
import uuid

from telepiplex_plugin_sdk import FeatureError

from .adapters.prowlarr import (
    ProwlarrConfigError,
    ProwlarrRequestError,
    search_prowlarr,
)
from .context import runtime_context
from .raw_results import (
    PAGE_SIZE,
    SORT_LABELS,
    clipped_text,
    command_query,
    raw_release_id,
    result_view,
    selectable,
)
from .search_logging import bind_search_log_context, log_search_event


class RawSearch:
    def __init__(self, feature):
        self.feature = feature
        self.awaiting_queries = set()

    def command(self, request):
        feature = self.feature
        owner = feature._owner_key(request)
        existing = feature._operation_for_owner(owner)
        if (
            existing
            and existing.get("state") not in {
                "completed", "failed", "cancelled", "handed_off", "interrupted",
            }
            and owner not in self.awaiting_queries
        ):
            return {"actions": [{
                "kind": "send_message", "text": "请先完成或退出当前搜索。",
            }]}
        feature.config_wizard.clear(request)
        return self.start(
            command_query(request), request,
            reuse_owner=owner in self.awaiting_queries,
        )

    def start(self, query, request, *, reuse_owner=False):
        feature = self.feature
        if feature.runtime is None:
            raise FeatureError("not_ready", "search runtime is not ready")
        owner = feature._owner_key(request)
        operation = feature._operation_for_owner(owner) if reuse_owner else None
        state = "running" if query else "awaiting_input"
        stage = "prowlarr_search" if query else "raw_query_input"
        text = "正在直接搜索片源…" if query else "请输入原始查询词，或使用 /pr <query>。"
        changes = dict(
            state=state, stage=stage, status_text=text,
            control="cancel" if query else "exit",
        )
        view = (
            feature._advance_operation(operation["operation_id"], **changes, details={})
            if operation
            else feature._new_operation(request, **changes, kind="raw_search")
        )
        operation_id = view["operation_id"]
        if not query:
            self.awaiting_queries.add(owner)
            return {
                "actions": [{
                    "kind": "send_message", "text": text,
                    "data": {"keyboard": [[{
                        "text": "退出", "callback_data": "search:exit",
                    }]]},
                }],
                "session": {"state": "open"},
                "operation": view,
            }
        self.awaiting_queries.discard(owner)
        plan_id = uuid.uuid4().hex[:10]
        stored = {
            "kind": "raw", "owner": owner, "operation_id": operation_id,
            "plan": {"plan_id": plan_id, "raw_query": query},
            "results": [], "release_by_id": {}, "raw_sort": "age",
            "raw_descending": False, "raw_page": 0,
        }
        feature.plans[plan_id] = stored
        operation = feature.operations[operation_id]
        operation["plan_id"] = plan_id
        bind_search_log_context(
            plan_id, chat_id=owner[0], user_id=owner[1], operation_id=operation_id,
        )
        log_search_event(
            runtime_context.logger, "search.raw_started", search_session_id=plan_id,
        )
        task_id = f"search-raw-{operation_id}"
        worker = self.run(plan_id, stored)
        try:
            task = feature.runtime.spawn(worker, task_id=task_id)
        except Exception:
            worker.close()
            feature._release_plan(plan_id)
            feature._advance_operation(
                operation_id, state="failed", stage="prowlarr_search",
                status_text="无法启动原文搜索，请重试 /pr。", control="", details={},
            )
            raise
        operation.update(task=task, task_id=task_id)
        return {
            "actions": [{"kind": "send_message", "text": text}],
            "session": {"state": "close"}, "operation": view,
        }

    async def run(self, plan_id, stored):
        feature = self.feature
        operation_id = stored["operation_id"]
        if plan_id not in feature.plans:
            return
        stored["raw_worker_started"] = True
        pending = None
        try:
            pending = asyncio.create_task(asyncio.to_thread(
                search_prowlarr, stored["plan"]["raw_query"], None,
            ))
            started = time.monotonic()
            while not pending.done():
                done, _ = await asyncio.wait({pending}, timeout=5)
                if not done:
                    await feature._report_operation(
                        operation_id, state="running", stage="prowlarr_search",
                        status_text=f"正在直接搜索片源… 已等待 {int(time.monotonic() - started)} 秒",
                        control="cancel", details={},
                    )
            results = await pending
            if plan_id not in feature.plans:
                return
            stored["release_by_id"] = {raw_release_id(item): item for item in results}
            stored["results"] = list(stored["release_by_id"].values())
            if stored["results"]:
                action = result_view(plan_id, stored)
                await feature._report_operation(
                    operation_id, state="awaiting_input", stage="release_selection",
                    status_text=action["text"], control="exit", details=action["data"],
                )
            else:
                await feature._report_operation(
                    operation_id, state="completed", stage="prowlarr_search",
                    status_text="没有找到结果，请调整原始查询词后重新发送 /pr。",
                    control="", details={},
                )
                feature._release_plan(plan_id)
            feature._log_completed_once(
                plan_id, stored, terminal_status="success" if results else "no_match",
                release_result_count=len(results),
            )
        except asyncio.CancelledError:
            if stored.get("selection_frozen"):
                return
            feature._release_plan(plan_id)
            if feature.operations[operation_id]["state"] not in {"cancelled", "handed_off"}:
                await feature._report_operation(
                    operation_id, state="cancelled", stage="prowlarr_search",
                    status_text="已取消原文搜索。", control="", details={},
                )
        except Exception as exc:
            feature._release_plan(plan_id)
            log_search_event(
                runtime_context.logger, "search.raw_failed", search_session_id=plan_id,
                level="warning", error_type=type(exc).__name__,
            )
            operation = feature.operations[operation_id]
            if not operation.get("_host_report_rejected"):
                reason = "请检查搜索服务后重试 /pr。"
                if isinstance(exc, ProwlarrConfigError):
                    reason = "请先通过 /search_config 配置搜索服务。"
                elif isinstance(exc, ProwlarrRequestError) and exc.kind == "timeout":
                    reason = "查询超时，请稍后重试 /pr。"
                await feature._report_operation(
                    operation_id, state="failed", stage="prowlarr_search",
                    status_text=f"原文搜索失败。{reason}", control="", details={},
                )
        finally:
            if pending is not None and not pending.done():
                pending.cancel()

    def present(self, plan_id, stored, *, action=None, stage="release_selection"):
        action = action or result_view(plan_id, stored)
        view = self.feature._advance_operation(
            stored["operation_id"], state="awaiting_input", stage=stage,
            status_text=action["text"], control="exit", details=action["data"],
        )
        return {"actions": [], "operation": view}

    def callback(self, action, plan_id, stored, arguments):
        feature = self.feature
        operation = feature.operations[stored["operation_id"]]
        if stored.get("selection_frozen"):
            return {"actions": [], "operation": feature._operation_view(operation)}
        if operation["state"] != "awaiting_input":
            raise FeatureError("invalid_state", "raw search choice is no longer active")
        stage = operation["stage"]
        value = arguments[0] if len(arguments) == 1 else None
        if action == "raw_sort" and stage == "release_selection" and value in SORT_LABELS:
            stored["raw_descending"] = (
                not stored["raw_descending"] if value == stored["raw_sort"] else False
            )
            stored.update(raw_sort=value, raw_page=0)
            return self.present(plan_id, stored)
        if action == "raw_page" and stage == "release_selection":
            page = self._index(
                value, max(1, math.ceil(len(stored["results"]) / PAGE_SIZE)),
            )
            stored["raw_page"] = page
            return self.present(plan_id, stored)
        if action == "release" and stage == "release_selection":
            item = stored["release_by_id"].get(value)
            if not item or not selectable(item):
                raise FeatureError("invalid_release", "selected release is unavailable")
            directories = [
                deepcopy(directory)
                for directory in feature.config.get("category_folder") or []
                if isinstance(directory, dict)
                and str(directory.get("path") or "").startswith("/")
                and str(directory["path"]).strip("/")
            ]
            if not directories:
                return self.present(plan_id, stored, action=result_view(
                    plan_id, stored, prefix="尚未配置保存目录，请先配置分类目录。",
                ))
            stored.update(raw_selected_release=value, raw_directories=directories)
            keyboard = [[{
                "text": str(directory.get("name") or directory["path"]),
                "callback_data": f"search:raw_path:{plan_id}:{index}",
            }] for index, directory in enumerate(directories)]
            keyboard.append([
                {"text": "返回结果", "callback_data": f"search:raw_back:{plan_id}"},
                {"text": "退出", "callback_data": f"search:cancel:{plan_id}"},
            ])
            return self.present(plan_id, stored, stage="raw_destination", action={
                "text": f"已选片源：{clipped_text(item['title'])}\n\n选择保存目录：",
                "data": {"keyboard": keyboard},
            })
        if action == "raw_back" and stage == "raw_destination" and not arguments:
            stored.pop("raw_selected_release", None)
            return self.present(plan_id, stored)
        if action == "raw_path" and stage == "raw_destination":
            directories = stored["raw_directories"]
            index = self._index(value, len(directories))
            stored["selected_path"] = directories[index]["path"]
            return feature._start_submission_task(
                plan_id, stored, stored["raw_selected_release"],
            )
        raise FeatureError("invalid_callback", "raw search callback is invalid")

    @staticmethod
    def _index(value, length):
        try:
            index = int(value)
        except (TypeError, ValueError):
            raise FeatureError("invalid_callback", "raw search index is invalid") from None
        if not 0 <= index < length:
            raise FeatureError("invalid_callback", "raw search index is out of range")
        return index

    def remove_release(self, plan_id, stored, release_id):
        stored["release_by_id"].pop(release_id, None)
        stored["results"] = list(stored["release_by_id"].values())
        stored["selection_frozen"] = False
        stored.pop("raw_selected_release", None)
        stored.pop("selected_release_id", None)
        return {
            "actions": [result_view(
                plan_id, stored, prefix="所选片源无法取得下载链接，已移除，请改选其他片源。",
            )],
            "release_resolution_recovered": True,
        }
