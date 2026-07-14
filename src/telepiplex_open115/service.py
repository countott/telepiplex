from __future__ import annotations

import asyncio
import hashlib
import re
import uuid

from telepiplex_plugin_sdk import FeatureError

from .context import logger


_MAGNET = re.compile(r"^magnet:\?xt=urn:btih:(?:[A-Fa-f0-9]{40}|[A-Za-z2-7]{32})(?:&.*)?$")
SESSION_TTL_SECONDS = 30 * 60
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
    "get_file_tree",
}


def _link_fingerprint(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _single_secret(value: str) -> str:
    value = str(value or "").strip().strip("`").strip('"').strip("'")
    if (
        not value
        or "\n" in value
        or "\r" in value
        or value.lower().startswith("your_")
    ):
        raise ValueError("invalid secret")
    return value


class Open115Feature:
    def __init__(self, *, config: dict, core, client, jobs=None, config_store=None):
        self.config = config
        self.core = core
        self.client = client
        self.runtime = None
        self.sessions = {}
        self.session_expiry_handles = {}
        self.active_job_ids = set()
        self.jobs = jobs
        self.config_store = config_store

    def bind_runtime(self, runtime):
        self.runtime = runtime
        if self.jobs:
            for job in self.jobs.resumable():
                runtime.spawn(self._publish_downloaded(job), task_id=job["job_id"])

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
            self._clear_auth_session(key)
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
            self._clear_auth_session(self._session_key(request))
            return {"actions": [{"kind": "send_message", "text": "已取消。"}], "session": {"state": "close"}}
        if command in {"config", "auth"}:
            return self._start_auth_session(request)
        raise FeatureError("not_found", f"unknown open115 command: {command}")

    async def callback(self, request: dict) -> dict:
        payload = str(request.get("payload") or "")
        key = self._session_key(request)
        if payload == "auth:direct":
            session = self.sessions.get(key)
            if not session or session.get("stage") != "choose_mode":
                return self._message_with_session("⚠️ 会话已失效。", "close")
            self.sessions[key] = {"stage": "access_token"}
            return self._message_with_session(
                "请发送 Access token。\n\n发送 /q 可取消。",
                "open",
                kind="edit_message",
            )
        if payload == "auth:scan":
            session = self.sessions.get(key)
            if not session or session.get("stage") != "choose_mode":
                return self._message_with_session("⚠️ 会话已失效。", "close")
            self._clear_auth_session(key)
            return await self._start_scan_auth(request)
        session = self.sessions.get(key)
        if not session or not payload.startswith("path:"):
            return {"actions": [{"kind": "send_message", "text": "⚠️ 会话已失效。"}], "session": {"state": "close"}}
        try:
            directory = (self.config.get("save_directories") or [])[int(payload.split(":", 1)[1])]
        except (IndexError, TypeError, ValueError):
            return {"actions": [{"kind": "send_message", "text": "⚠️ 保存目录不可用。"}], "session": {"state": "close"}}
        self.sessions.pop(key, None)
        result = self._start_download({
            "link": session["link"],
            "selected_path": directory["path"],
            "user_id": request.get("user_id"),
        }, {
            "idempotency_key": (
                f"telegram:{request.get('update_id') or uuid.uuid4().hex}"
            ),
        })
        return {
            "actions": [{
                "kind": "edit_message",
                "text": f"✅ 已加入 115 下载队列：{result['job_id']}",
            }],
            "session": {"state": "close"},
        }

    async def message(self, request: dict) -> dict:
        key = self._session_key(request)
        session = self.sessions.get(key)
        if not session:
            return self._message_with_session("⚠️ 会话已失效。", "close")

        stage = session.get("stage")
        if stage == "access_token":
            try:
                access_token = _single_secret(request.get("text"))
            except ValueError:
                return self._message_with_session(
                    "⚠️ Access token 无效，请重新发送单行 Token。",
                    "open",
                )
            self.sessions[key] = {
                "stage": "refresh_token",
                "access_token": access_token,
            }
            self._schedule_sensitive_expiry(key)
            return self._message_with_session(
                "已收到 Access token。\n请发送 Refresh token。\n\n发送 /q 可取消。",
                "open",
            )

        if stage == "refresh_token":
            try:
                refresh_token = _single_secret(request.get("text"))
            except ValueError:
                return self._message_with_session(
                    "⚠️ Refresh token 无效，请重新发送单行 Token。",
                    "open",
                )
            access_token = session["access_token"]
            try:
                if self.config_store:
                    updated = self.config_store.write_tokens(
                        access_token,
                        refresh_token,
                        auth_mode="direct",
                    )
                else:
                    updated = dict(self.config)
                    updated.update({
                        "auth_mode": "direct",
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                    })
                self.client.set_tokens(access_token, refresh_token)
            except Exception:
                logger.error("open115_direct_auth_write_failed")
                return self._message_with_session(
                    "⚠️ 115 Token 写入失败，请重新发送 Refresh token 或使用 /q 取消。",
                    "open",
                )
            self.config.update(updated)
            self._clear_auth_session(key)
            logger.info("open115_direct_auth_updated auth_mode=direct")
            return self._message_with_session(
                "✅ 115 Token 已写入并立即生效。",
                "close",
            )

        return self._message_with_session("⚠️ 会话已失效。", "close")

    def _start_auth_session(self, request: dict) -> dict:
        key = self._session_key(request)
        self._clear_auth_session(key)
        self.sessions[key] = {"stage": "choose_mode"}
        return {
            "actions": [{
                "kind": "send_message",
                "text": "请选择 115 授权方式：",
                "data": {"keyboard": [[
                    {
                        "text": "Access / Refresh Token",
                        "callback_data": "open115:auth:direct",
                    },
                    {
                        "text": "115 扫码授权",
                        "callback_data": "open115:auth:scan",
                    },
                ]]},
            }],
            "session": {"state": "open"},
        }

    def _schedule_sensitive_expiry(self, key):
        handle = self.session_expiry_handles.pop(key, None)
        if handle is not None:
            handle.cancel()
        expected = self.sessions.get(key)
        handle = asyncio.get_running_loop().call_later(
            SESSION_TTL_SECONDS,
            self._expire_sensitive_session,
            key,
            expected,
        )
        self.session_expiry_handles[key] = handle

    def _expire_sensitive_session(self, key, expected):
        if self.sessions.get(key) is expected:
            self.sessions.pop(key, None)
        self.session_expiry_handles.pop(key, None)

    def _clear_auth_session(self, key):
        handle = self.session_expiry_handles.pop(key, None)
        if handle is not None:
            handle.cancel()
        self.sessions.pop(key, None)

    async def _start_scan_auth(self, request):
        config = self.config_store.read() if self.config_store else dict(self.config)
        app_id = str(config.get("app_id") or "").strip()
        if not app_id:
            return self._message_with_session(
                "⚠️ 扫码授权需要先在私有配置中填写 app_id。",
                "close",
            )
        if self.runtime is None:
            raise FeatureError("not_ready", "open115 runtime is not ready")
        try:
            authorization = await asyncio.to_thread(
                self.client.create_device_authorization,
                app_id,
            )
            qr_text = self._render_qr(authorization["qrcode"])
        except Exception as exc:
            logger.error(
                "open115_scan_auth_start_failed "
                f"error={type(exc).__name__}"
            )
            return self._message_with_session(
                f"⚠️ 115 扫码授权启动失败：{type(exc).__name__}",
                "close",
            )
        logger.info(
            "open115_scan_auth_started "
            f"user_id={int(request.get('user_id') or 0)} "
            f"auth_uid={authorization['uid']}"
        )
        task_id = f"open115-auth-{authorization['uid']}"
        self.runtime.spawn(
            self._complete_scan_auth(
                authorization,
                int(request.get("user_id") or 0),
            ),
            task_id=task_id,
        )
        return {
            "actions": [{
                "kind": "edit_message",
                "text": f"请使用 115 App 扫码并确认：\n<pre>{qr_text}</pre>",
                "parse_mode": "HTML",
            }],
            "session": {"state": "close"},
        }

    async def _complete_scan_auth(self, authorization, user_id):
        try:
            tokens = await asyncio.to_thread(
                self.client.complete_device_authorization,
                authorization,
                timeout=float(self.config.get("auth_poll_timeout") or 300),
                poll_interval=float(self.config.get("auth_poll_interval") or 2),
            )
            if self.config_store:
                updated = self.config_store.write_tokens(
                    tokens["access_token"],
                    tokens["refresh_token"],
                    auth_mode="scan",
                )
                self.config.update(updated)
            else:
                self.config.update({
                    "auth_mode": "scan",
                    "access_token": tokens["access_token"],
                    "refresh_token": tokens["refresh_token"],
                })
            self.client.set_tokens(tokens["access_token"], tokens["refresh_token"])
            logger.info(
                "open115_scan_auth_completed "
                f"user_id={user_id} auth_uid={authorization['uid']}"
            )
            message = "✅ 115 扫码授权成功，Token 已写回 Feature 私有配置。"
        except Exception as exc:
            logger.error(
                "open115_scan_auth_failed "
                f"user_id={user_id} auth_uid={authorization.get('uid') or ''} "
                f"error={type(exc).__name__}"
            )
            message = f"⚠️ 115 扫码授权失败：{type(exc).__name__}"
        if user_id:
            await self.core.notify_user(user_id, message)

    @staticmethod
    def _render_qr(value: str) -> str:
        import qrcode

        matrix = qrcode.QRCode(border=1, box_size=1)
        matrix.add_data(str(value))
        matrix.make(fit=True)
        rows = matrix.get_matrix()
        if len(rows) % 2:
            rows.append([False] * len(rows[0]))
        chars = {
            (False, False): " ",
            (True, False): "▀",
            (False, True): "▄",
            (True, True): "█",
        }
        return "\n".join(
            "".join(chars[(top, bottom)] for top, bottom in zip(rows[index], rows[index + 1]))
            for index in range(0, len(rows), 2)
        )

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
            logger.info(
                "open115_download_duplicate "
                f"job_id={job_id} selected_path={selected_path}"
            )
            return {"accepted": True, "job_id": job_id, "duplicate": True}
        if self.jobs:
            job = self.jobs.create_or_get(job_id, payload | {"link": link, "selected_path": selected_path})
            if job["state"] in {"completed", "failed", "downloaded", "running", "interrupted"}:
                logger.info(
                    "open115_download_duplicate "
                    f"job_id={job_id} selected_path={selected_path} "
                    f"state={job['state']}"
                )
                return {"accepted": True, "job_id": job_id, "duplicate": True, "state": job["state"]}
        self.active_job_ids.add(job_id)
        logger.info(
            "open115_download_started "
            f"job_id={job_id} "
            f"selected_path={selected_path} "
            f"user_id={int(payload.get('user_id') or 0)} "
            f"link_sha1={_link_fingerprint(link)}"
        )
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
            final_path = f"{selected_path.rstrip('/')}/{resource_name}"
            file_tree = await asyncio.to_thread(
                self.client.get_file_tree,
                final_path,
            )
            event_payload = {
                "job_id": job_id,
                "provider": "open115",
                "link": link,
                "selected_path": selected_path,
                "user_id": user_id,
                "resource_name": resource_name,
                "download_root": final_path,
                "final_path": final_path,
                "file_tree": file_tree,
                "media_metadata": payload.get("media_metadata"),
                "naming_metadata": payload.get("naming_metadata"),
                "release": payload.get("release"),
            }
            if self.jobs:
                self.jobs.update(job_id, "downloaded", result=event_payload)
            logger.info(
                "open115_download_completed "
                f"job_id={job_id} "
                f"final_path={final_path} "
                f"resource_name={resource_name}"
            )
            await self._publish_downloaded({"job_id": job_id, "state": "downloaded", "result": event_payload})
        except Exception as exc:
            if self.jobs and (self.jobs.get(job_id) or {}).get("state") == "downloaded":
                return
            if self.jobs:
                self.jobs.update(job_id, "failed", error=type(exc).__name__)
            logger.error(
                "open115_download_failed "
                f"job_id={job_id} "
                f"selected_path={selected_path} "
                f"error={type(exc).__name__} "
                f"link_sha1={_link_fingerprint(link)}"
            )
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
        logger.info(
            "open115_download_published "
            f"job_id={job_id} final_path={payload.get('final_path') or ''}"
        )
        if self.jobs:
            self.jobs.update(job_id, "completed", result=payload)
        user_id = int(payload.get("user_id") or 0)
        if user_id:
            try:
                await self.core.notify_user(user_id, f"✅ 115 下载完成，已交给整理管线。\n保存目录：{payload.get('final_path')}", idempotency_key=f"{job_id}:download-notice")
            except Exception:
                pass

    @staticmethod
    def _session_key(request):
        return int(request.get("chat_id") or 0), int(request.get("user_id") or 0)

    @staticmethod
    def _message(text):
        return {"actions": [{"kind": "send_message", "text": text}]}

    @staticmethod
    def _message_with_session(text, state, *, kind="send_message"):
        return {
            "actions": [{"kind": kind, "text": text}],
            "session": {"state": state},
        }
