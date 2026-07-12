from __future__ import annotations

import asyncio
from pathlib import Path

from .adapters.fanart import FanartAdapter
from .adapters.plex import PlexAdapter
from .adapters.tmdb import TmdbAdapter
from .ai import PlexAIOrchestrator
from .jobs import PlexJobRepository
from .management import PlexManagementService


class PlexFeature:
    def __init__(
        self,
        *,
        config: dict,
        core,
        state_path: Path,
        repository=None,
        service_factory=None,
    ):
        self.config = config
        self.core = core
        self.state_path = Path(state_path)
        self.state_path.mkdir(parents=True, exist_ok=True)
        self.jobs = repository or PlexJobRepository(self.state_path / "plex_jobs.db")
        self.service_factory = service_factory or self._build_service
        self.service = None
        self.service_error = ""
        self.ai = None
        self.ai_error = ""
        self.mcp_handle = None
        self.runtime = None
        self.loop = None
        self._service_lock = asyncio.Lock()
        self.pending_writes = {}
        self.interrupted_job_ids = self.jobs.mark_incomplete_interrupted()

    def bind_runtime(self, runtime):
        self.runtime = runtime
        self.loop = asyncio.get_running_loop()
        if self.interrupted_job_ids:
            runtime.spawn(self._resume_interrupted(), task_id="plex-resume")

    async def media_organized(self, request: dict) -> dict:
        service = await self._ensure_service()
        payload = request.get("payload") or {}
        job = await asyncio.to_thread(service.enqueue_organized_event, payload)
        if not job:
            if payload.get("user_id"):
                await self.core.notify_user(
                    int(payload["user_id"]),
                    "⚠️ Plex 管理拒绝了不完整的 canonical metadata；请人工检查。",
                )
            return {"accepted": True, "state": "rejected"}
        if job["state"] == "completed":
            return {"accepted": True, "job_id": job["id"], "state": "completed", "duplicate": True}
        claimed = await asyncio.to_thread(self.jobs.claim, job["id"])
        if not claimed:
            current = self.jobs.get(job["id"])
            return {
                "accepted": True,
                "job_id": job["id"],
                "state": (current or job)["state"],
                "duplicate": True,
            }
        try:
            self.runtime.spawn(self._run_job(job["id"]), task_id=f"plex-job-{job['id']}")
        except Exception:
            self.jobs.update(job["id"], state="interrupted", error="failed to start Plex job task")
            raise
        return {"accepted": True, "job_id": job["id"], "state": "running"}

    async def command(self, request: dict) -> dict:
        try:
            service = await self._ensure_service()
        except Exception:
            return self._message(f"⚠️ Plex Feature 暂不可用：{self.service_error or 'configuration error'}")
        text = " ".join(str(item) for item in request.get("args") or []).strip()
        if not text:
            jobs = await asyncio.to_thread(service.list_jobs, 5)
            if not jobs:
                return self._message("当前没有 Plex 管理任务。")
            lines = ["最近 Plex 任务："]
            lines.extend(
                f"#{job['id']} {job['state']} {job['payload'].get('resource_name') or ''}"
                for job in jobs
            )
            if self.ai_error:
                lines.append(f"AI 已隔离：{self.ai_error}")
            return self._message("\n".join(lines))
        if self.ai is None:
            return self._message(f"Plex AI 未启用或已隔离：{self.ai_error or 'missing configuration'}")
        try:
            result = await asyncio.to_thread(self.ai.run, text)
        except Exception as exc:
            return self._message(f"Plex AI 请求失败：{type(exc).__name__}")
        action = {"kind": "send_message", "text": result.get("message") or "Plex AI 未返回内容。"}
        confirmation = result.get("confirmation") or {}
        token = str(confirmation.get("confirmation_token") or "")
        if token:
            self.pending_writes[token] = {
                "action": confirmation.get("action") or "",
                "payload": confirmation.get("payload") or {},
            }
            action["data"] = {"keyboard": [[{
                "text": "确认执行",
                "callback_data": f"plex:write:{token}",
            }]]}
        return {"actions": [action]}

    async def callback(self, request: dict) -> dict:
        service = await self._ensure_service()
        payload = str(request.get("payload") or "")
        if payload.startswith("write:"):
            token = payload.split(":", 1)[1]
            pending = self.pending_writes.pop(token, None)
            if not pending:
                return self._message("⚠️ Plex 确认已失效。")
            try:
                result = await asyncio.to_thread(
                    service.apply_operation,
                    pending["action"], pending["payload"], token,
                )
            except ValueError as exc:
                return self._message(str(exc))
            return self._message(f"✅ Plex 操作已执行：{result['action']}")
        if payload.startswith("match:"):
            _, job_id, raw_index = payload.split(":", 2)
            job = service.get_job(int(job_id))
            waiting = next((
                value for value in (job or {}).get("step_results", {}).values()
                if isinstance(value, dict) and value.get("status") == "waiting"
            ), None)
            candidates = (waiting or {}).get("candidates") or []
            try:
                candidate = candidates[int(raw_index)]
            except (IndexError, ValueError):
                return self._message("⚠️ Plex 候选已失效。")
            selection = (
                candidate.get("rating_key")
                if waiting.get("kind") == "location"
                else candidate.get("guid")
            )
            result = await asyncio.to_thread(service.confirm_match, int(job_id), selection)
            return self._message(PlexManagementService.format_job_summary(result))
        return self._message("⚠️ Plex callback 无效。")

    async def management_capability(self, request: dict) -> dict:
        """Expose stable read-only job inspection to other Features."""
        service = await self._ensure_service()
        method = str(request.get("method") or "")
        params = request.get("params") or {}
        if method == "get_job":
            return {
                "job": await asyncio.to_thread(
                    service.get_job, int(params.get("job_id") or 0)
                )
            }
        if method == "list_jobs":
            limit = min(max(int(params.get("limit") or 20), 1), 100)
            return {"jobs": await asyncio.to_thread(service.list_jobs, limit)}
        raise ValueError(f"unsupported plex.management method: {method}")

    async def _ensure_service(self):
        if self.service is not None:
            return self.service
        async with self._service_lock:
            if self.service is not None:
                return self.service
            try:
                self.service = await asyncio.to_thread(self.service_factory)
                self.service_error = ""
            except Exception as exc:
                self.service_error = PlexManagementService._safe_error(exc)
                raise
            return self.service

    def _build_service(self):
        plex_config = self.config.get("plex") or {}
        base_url = str(plex_config.get("base_url") or "").strip()
        token = str(plex_config.get("token") or "").strip()
        if not base_url or not token:
            raise ValueError("plex.base_url and plex.token are required")
        tmdb_config = self.config.get("tmdb") or {}
        fanart_config = self.config.get("fanart") or {}
        service = PlexManagementService(
            self.jobs,
            PlexAdapter(base_url, token, plex_config.get("timeout", 15)),
            tmdb=(TmdbAdapter(tmdb_config["api_key"], tmdb_config.get("timeout", 15)) if tmdb_config.get("api_key") else None),
            fanart=(FanartAdapter(fanart_config["api_key"], fanart_config.get("timeout", 15)) if fanart_config.get("api_key") else None),
            notifier=self._notify_sync,
            category_folders=self.config.get("category_folder") or [],
            scan_poll_interval=plex_config.get("scan_poll_interval", 5),
            scan_timeout=plex_config.get("scan_timeout", 300),
        )
        ai_config = self.config.get("ai") or {}
        if ai_config.get("enabled"):
            if all(str(ai_config.get(key) or "").strip() for key in ("api_url", "api_key", "model")):
                try:
                    from .mcp_server import PlexToolDispatcher
                    self.ai = PlexAIOrchestrator(
                        ai_config,
                        PlexToolDispatcher(service),
                        max_tool_rounds=ai_config.get("max_tool_rounds", 3),
                    )
                except Exception as exc:
                    self.ai_error = PlexManagementService._safe_error(exc)
            else:
                self.ai_error = "AI credentials are incomplete"
        mcp_config = self.config.get("mcp") or {}
        if mcp_config.get("enabled"):
            try:
                from .mcp_server import start_plex_mcp_server
                self.mcp_handle = start_plex_mcp_server(service, mcp_config)
            except Exception as exc:
                self.service_error = f"MCP isolated: {PlexManagementService._safe_error(exc)}"
        return service

    async def _run_job(self, job_id: int):
        try:
            service = await self._ensure_service()
            await asyncio.to_thread(service.run_job, job_id)
        finally:
            job = self.jobs.get(job_id)
            if job and job["state"] in {
                "running", "scanning", "locating", "matching",
                "localizing", "artwork", "streams",
            }:
                self.jobs.update(
                    job_id,
                    state="interrupted",
                    error="interrupted before completion",
                )

    async def _resume_interrupted(self):
        try:
            await self._ensure_service()
        except Exception:
            return
        for job_id in self.interrupted_job_ids:
            if await asyncio.to_thread(self.jobs.claim, job_id):
                self.runtime.spawn(self._run_job(job_id), task_id=f"plex-job-{job_id}")
        self.interrupted_job_ids = []

    def _notify_sync(self, user_id, message, confirmation=None):
        if not user_id or self.loop is None:
            return False
        if confirmation:
            message += f"\n请使用 /plex 查看任务 #{confirmation.get('job_id')} 并人工确认。"
        future = asyncio.run_coroutine_threadsafe(
            self.core.notify_user(int(user_id), str(message)),
            self.loop,
        )
        return bool(future.result(timeout=30).get("accepted"))

    @staticmethod
    def _message(text):
        return {"actions": [{"kind": "send_message", "text": str(text)}]}
