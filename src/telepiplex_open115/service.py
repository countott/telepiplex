from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from pathlib import PurePosixPath

from telepiplex_plugin_sdk import FeatureError


_MAGNET = re.compile(r"^magnet:\?xt=urn:btih:(?:[A-Fa-f0-9]{40}|[A-Za-z2-7]{32})(?:&.*)?$")
_INVALID_NAME = re.compile(r'[\\/*?"<>|]+')
_STORAGE_METHODS = {
    "get_file_info",
    "get_file_info_by_id",
    "get_file_list",
    "create_directory",
    "create_dir_recursive",
    "rename",
    "copy_file",
    "delete_single_file",
    "move_file",
    "move_file_detailed",
    "is_directory",
    "get_files_from_dir",
}


class Open115Feature:
    def __init__(self, *, config: dict, core, client, jobs=None):
        self.config = config
        self.core = core
        self.client = client
        self.runtime = None
        self.sessions = {}
        self.active_job_ids = set()
        self.jobs = jobs

    def bind_runtime(self, runtime):
        self.runtime = runtime
        if self.jobs:
            for job in self.jobs.resumable():
                runtime.spawn(
                    self._publish_downloaded(job) if job["state"] == "downloaded"
                    else self._download_job(job["job_id"], job["payload"]),
                    task_id=job["job_id"],
                )

    async def download_capability(self, request: dict) -> dict:
        if request.get("method") != "submit":
            raise FeatureError("method_not_allowed", "download provider method is not allowed")
        return self._start_download(request.get("payload") or {}, request.get("context") or {})

    async def storage_capability(self, request: dict) -> dict:
        method = str(request.get("method") or "")
        if method not in _STORAGE_METHODS:
            raise FeatureError("method_not_allowed", f"storage method is not allowed: {method}")
        payload = request.get("payload") or {}
        args = payload.get("args") or []
        kwargs = payload.get("kwargs") or {}
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise FeatureError("invalid_request", "storage args/kwargs are invalid")
        value = await asyncio.to_thread(getattr(self.client, method), *args, **kwargs)
        return {"value": value}

    async def command(self, request: dict) -> dict:
        command = str(request.get("command") or "")
        if command in {"magnet", "m"}:
            link = " ".join(str(item) for item in request.get("args") or []).strip()
            if not _MAGNET.fullmatch(link):
                return self._message("用法：/magnet <magnet链接>")
            directories = self.config.get("save_directories") or []
            if not directories:
                return self._message("⚠️ open115 配置中没有 save_directories。")
            key = self._session_key(request)
            self.sessions[key] = {"link": link, "stage": "path"}
            keyboard = [[{
                "text": f"📁 {item['name']}",
                "callback_data": f"open115:path:{index}",
            }] for index, item in enumerate(directories)]
            return {
                "actions": [{
                    "kind": "send_message",
                    "text": "请选择保存目录：",
                    "data": {"keyboard": keyboard},
                }],
                "session": {"state": "open"},
            }
        if command == "q":
            self.sessions.pop(self._session_key(request), None)
            return {"actions": [{"kind": "send_message", "text": "已取消。"}], "session": {"state": "close"}}
        if command == "config":
            return self._message("115 Feature 配置：/config/plugins/open115/config.yaml")
        if command == "auth":
            configured = bool(self.config.get("access_token") and self.config.get("refresh_token"))
            return self._message("✅ 115 Token 已配置。" if configured else "⚠️ 请先在 open115/config.yaml 配置 Token。")
        raise FeatureError("not_found", f"unknown open115 command: {command}")

    async def callback(self, request: dict) -> dict:
        key = self._session_key(request)
        session = self.sessions.get(key)
        payload = str(request.get("payload") or "")
        if not session or not payload.startswith("path:"):
            return {"actions": [{"kind": "send_message", "text": "⚠️ 会话已失效。"}], "session": {"state": "close"}}
        try:
            directory = (self.config.get("save_directories") or [])[int(payload.split(":", 1)[1])]
        except (IndexError, TypeError, ValueError):
            return {"actions": [{"kind": "send_message", "text": "⚠️ 保存目录不可用。"}], "session": {"state": "close"}}
        session.update({"selected_path": directory["path"], "stage": "name"})
        return {
            "actions": [{"kind": "edit_message", "text": "请输入顶层文件夹名；发送 - 保留 115 原名。"}],
            "session": {"state": "open"},
        }

    async def message(self, request: dict) -> dict:
        key = self._session_key(request)
        session = self.sessions.pop(key, None)
        if not session or session.get("stage") != "name":
            return {"actions": [{"kind": "send_message", "text": "⚠️ 会话已失效。"}], "session": {"state": "close"}}
        name = self._sanitize_name(request.get("text"))
        result = self._start_download({
            "link": session["link"],
            "selected_path": session["selected_path"],
            "user_id": request.get("user_id"),
            "target_folder_name": name,
        }, {"idempotency_key": f"telegram:{request.get('update_id') or uuid.uuid4().hex}"})
        return {
            "actions": [{"kind": "send_message", "text": f"✅ 已加入 115 下载队列：{result['job_id']}"}],
            "session": {"state": "close"},
        }

    def _start_download(self, payload: dict, call_context: dict) -> dict:
        if self.runtime is None:
            raise FeatureError("not_ready", "open115 runtime is not ready")
        link = str(payload.get("link") or "").strip()
        selected_path = "/" + str(payload.get("selected_path") or "").strip("/")
        if not _MAGNET.fullmatch(link) or selected_path == "/":
            raise FeatureError("invalid_download", "valid magnet link and selected_path are required")
        job_id = str(call_context.get("idempotency_key") or "").strip() or hashlib.sha256(
            f"{link}\0{selected_path}".encode("utf-8")
        ).hexdigest()
        if job_id in self.active_job_ids:
            return {"accepted": True, "job_id": job_id, "duplicate": True}
        if self.jobs:
            job = self.jobs.create_or_get(job_id, payload | {"link": link, "selected_path": selected_path})
            if job["state"] in {"completed", "failed", "downloaded", "running"}:
                return {"accepted": True, "job_id": job_id, "duplicate": True, "state": job["state"]}
        self.active_job_ids.add(job_id)
        try:
            if self.jobs:
                self.jobs.update(job_id, "running")
            self.runtime.spawn(self._download_job(job_id, payload | {
                "link": link,
                "selected_path": selected_path,
            }), task_id=job_id)
        except Exception:
            self.active_job_ids.discard(job_id)
            raise
        return {"accepted": True, "job_id": job_id}

    async def _download_job(self, job_id: str, payload: dict):
        link = payload["link"]
        selected_path = payload["selected_path"]
        user_id = int(payload.get("user_id") or 0)
        info_hash = ""
        try:
            await asyncio.to_thread(self.client.add_offline_task, link, selected_path)
            completed = await asyncio.to_thread(
                self.client.wait_for_download,
                link,
                timeout=float(self.config.get("download_timeout") or 1800),
                poll_interval=float(self.config.get("poll_interval") or 10),
            )
            resource_name = str(completed.get("resource_name") or "").strip("/")
            info_hash = str(completed.get("info_hash") or "")
            if not resource_name:
                raise RuntimeError("115 completed task has no resource name")
            final_leaf = resource_name
            final_path = f"{selected_path.rstrip('/')}/{final_leaf}"
            if not await asyncio.to_thread(self.client.is_directory, final_path):
                final_leaf = PurePosixPath(resource_name).stem
                folder = f"{selected_path.rstrip('/')}/{final_leaf}"
                await asyncio.to_thread(self.client.create_dir_recursive, folder)
                if not await asyncio.to_thread(self.client.move_file, final_path, folder):
                    raise RuntimeError("cannot move downloaded file into its top-level folder")
                final_path = folder
            target_name = self._sanitize_name(payload.get("target_folder_name"))
            if target_name and target_name != final_leaf:
                if not await asyncio.to_thread(self.client.rename, final_path, target_name):
                    raise RuntimeError("download completed but top-level rename failed")
                final_leaf = target_name
                final_path = f"{selected_path.rstrip('/')}/{target_name}"
            event_payload = {
                "job_id": job_id,
                "provider": "open115",
                "link": link,
                "selected_path": selected_path,
                "user_id": user_id,
                "resource_name": final_leaf,
                "final_path": final_path,
                "media_metadata": payload.get("media_metadata"),
                "naming_metadata": payload.get("naming_metadata"),
            }
            if self.jobs:
                self.jobs.update(job_id, "downloaded", result=event_payload)
            await self._publish_downloaded({"job_id": job_id, "state": "downloaded", "result": event_payload})
        except Exception as exc:
            if self.jobs and (self.jobs.get(job_id) or {}).get("state") == "downloaded":
                return
            if self.jobs:
                self.jobs.update(job_id, "failed", error=type(exc).__name__)
            failure = {
                "job_id": job_id,
                "provider": "open115",
                "user_id": user_id,
                "link": link,
                "error": type(exc).__name__,
            }
            try:
                await self.core.publish_event(
                    "download.failed", failure, idempotency_key=f"{job_id}:failed"
                )
            except Exception:
                pass
            if user_id:
                try:
                    await self.core.notify_user(user_id, f"❌ 115 下载任务失败：{type(exc).__name__}", idempotency_key=f"{job_id}:failed-notice")
                except Exception:
                    pass
        finally:
            if info_hash:
                try:
                    await asyncio.to_thread(self.client.del_offline_task, info_hash, 0)
                except Exception:
                    pass
            self.active_job_ids.discard(job_id)

    async def _publish_downloaded(self, job):
        payload = job.get("result") or {}
        job_id = str(job["job_id"])
        await self.core.publish_event("download.completed", payload, idempotency_key=f"{job_id}:completed")
        if self.jobs:
            self.jobs.update(job_id, "completed", result=payload)
        user_id = int(payload.get("user_id") or 0)
        if user_id:
            try:
                await self.core.notify_user(user_id, f"✅ 115 下载完成，已交给整理管线。\n保存目录：{payload.get('final_path')}", idempotency_key=f"{job_id}:download-notice")
            except Exception:
                pass

    @staticmethod
    def _sanitize_name(value) -> str:
        value = str(value or "").strip().strip("`").strip('"').strip("'")
        if value == "-":
            return ""
        value = _INVALID_NAME.sub("", value.replace("：", ":"))
        return " ".join(value.split()).strip().strip(".")

    @staticmethod
    def _session_key(request):
        return int(request.get("chat_id") or 0), int(request.get("user_id") or 0)

    @staticmethod
    def _message(text):
        return {"actions": [{"kind": "send_message", "text": text}]}
