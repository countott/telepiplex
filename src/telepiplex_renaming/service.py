from __future__ import annotations

import asyncio

from telepiplex_plugin_sdk.media_metadata import (
    MEDIA_METADATA_KEY,
    attach_media_metadata,
    extract_confirmed_media_metadata,
)

from .models import DownloadCompletedEvent, PostDownloadResult
from .processor import process_generic_media, process_tvdb_episode


_STORAGE_METHODS = {
    "get_file_info", "get_file_info_by_id", "get_file_list",
    "create_directory", "create_dir_recursive", "rename", "copy_file",
    "delete_single_file", "move_file", "is_directory", "get_files_from_dir",
}


class StorageProxy:
    def __init__(self, core, loop, *, timeout=120):
        self.core = core
        self.loop = loop
        self.timeout = float(timeout)

    def __getattr__(self, method):
        if method not in _STORAGE_METHODS:
            raise AttributeError(method)

        def call(*args, **kwargs):
            future = asyncio.run_coroutine_threadsafe(
                self.core.call_capability(
                    "storage.provider",
                    method,
                    {"args": list(args), "kwargs": kwargs},
                    deadline=self.timeout,
                ),
                self.loop,
            )
            return future.result(timeout=self.timeout + 1).get("value")

        return call


class RenamingFeature:
    def __init__(self, *, config: dict, core):
        self.config = config
        self.core = core

    async def download_completed(self, request: dict) -> dict:
        payload = request.get("payload") or {}
        job_id = str(payload.get("job_id") or request.get("event_id") or "")
        user_id = int(payload.get("user_id") or 0)
        metadata = {}
        if isinstance(payload.get("media_metadata"), dict):
            try:
                metadata = attach_media_metadata({}, payload["media_metadata"])
            except ValueError:
                metadata = {MEDIA_METADATA_KEY: payload["media_metadata"]}
        loop = asyncio.get_running_loop()
        storage = StorageProxy(
            self.core,
            loop,
            timeout=float(self.config.get("storage_timeout") or 120),
        )
        event = DownloadCompletedEvent(
            link=str(payload.get("link") or ""),
            selected_path=str(payload.get("selected_path") or ""),
            user_id=user_id,
            final_path=str(payload.get("final_path") or ""),
            resource_name=str(payload.get("resource_name") or ""),
            naming_metadata=(
                payload.get("naming_metadata")
                if isinstance(payload.get("naming_metadata"), dict)
                else None
            ),
            metadata=metadata,
            provider=str(payload.get("provider") or "open115"),
            storage=storage,
        )
        result = await asyncio.to_thread(self._process, event)
        organized = bool(
            result.handled
            and result.final_path
            and str(result.message or "").startswith("✅")
        )
        if organized:
            contract = extract_confirmed_media_metadata(result.metadata or event.metadata)
            await self.core.publish_event(
                "media.organized",
                {
                    "job_id": job_id,
                    "user_id": user_id,
                    "provider": event.provider,
                    "source_path": payload.get("final_path"),
                    "final_path": result.final_path,
                    "media_metadata": contract,
                },
                idempotency_key=f"{job_id}:organized",
            )
        if user_id and result.message:
            await self.core.notify_user(
                user_id,
                result.message,
                idempotency_key=f"{job_id}:renaming-notice",
            )
        return {
            "accepted": True,
            "organized": organized,
            "final_path": result.final_path or event.final_path,
        }

    def _process(self, event: DownloadCompletedEvent) -> PostDownloadResult:
        result = process_tvdb_episode(event)
        if result.handled or result.should_stop:
            return result
        result = process_generic_media(event)
        if result.handled or result.should_stop:
            return result
        return self._fallback_unorganized(event)

    def _fallback_unorganized(self, event: DownloadCompletedEvent) -> PostDownloadResult:
        root = str(self.config.get("unorganized_path") or "").rstrip("/")
        if not root:
            return PostDownloadResult(
                True,
                final_path=event.final_path,
                message="⚠️ 无法确定整理规则，文件保持原位。",
                should_stop=True,
                metadata=event.metadata,
            )
        leaf = str(event.final_path).rstrip("/").rsplit("/", 1)[-1]
        if not event.storage.create_dir_recursive(root):
            raise RuntimeError(f"cannot create unorganized path: {root}")
        if event.storage.move_file(event.final_path, root) is not True:
            raise RuntimeError(f"cannot move release to unorganized path: {event.final_path}")
        target = f"{root}/{leaf}"
        return PostDownloadResult(
            True,
            final_path=target,
            message=f"⚠️ 无法确定整理规则，已移入未整理。\n保存目录：{target}",
            should_stop=True,
            metadata=event.metadata,
        )
