from __future__ import annotations

from dataclasses import dataclass

from telepiplex_plugin_sdk.log_sanitizer import sanitize_log_text

from .client import Open115Error
from .cleanup import DownloadCleanupError
from .transport_capacity import TreeCapacityError
from telepiplex_plugin_sdk.storage_snapshot import SnapshotError


_AUTH_CODES = {"401", "40140125", "40140126"}
_AUTH_MARKERS = (
    "access_token",
    "refresh_token",
    "token refresh",
    "authorization",
    "auth",
    "登录",
    "授权",
)


@dataclass(frozen=True)
class DownloadFailure:
    code: str
    summary: str
    detail: str
    remedy: str
    stage: str
    provider_code: str = ""
    provider_operation: str = ""

    def user_text(self) -> str:
        provider_suffix = (
            f"（115：{self.provider_code}）" if self.provider_code else ""
        )
        return "\n".join((
            f"❌ {self.summary}",
            f"原因：{self.detail}",
            f"处理：{self.remedy}",
            f"错误码：{self.code}{provider_suffix}",
        ))

    def details(self) -> dict:
        result = {
            "error_code": self.code,
            "error_message": self.detail,
            "remedy": self.remedy,
            "stopped_at": self.stage,
        }
        if self.provider_code:
            result["provider_code"] = self.provider_code
        if self.provider_operation:
            result["provider_operation"] = self.provider_operation
        return result


def classify_download_failure(exc: Exception, *, stage: str) -> DownloadFailure:
    safe_stage = str(stage or "download")
    detail = sanitize_log_text(str(exc), max_chars=500).strip()
    if not detail:
        detail = type(exc).__name__

    provider_code = ""
    provider_operation = ""
    if isinstance(exc, Open115Error):
        provider_code = sanitize_log_text(exc.code, max_chars=80).strip()
        provider_operation = sanitize_log_text(
            exc.operation, max_chars=120
        ).strip()

    if isinstance(exc, (TreeCapacityError, SnapshotError)) or provider_code == "file_tree_incomplete":
        return DownloadFailure(
            code="download_tree_capacity_exceeded" if isinstance(exc, TreeCapacityError) else "download_tree_incomplete",
            summary="下载文件完整性或容量检查未通过，未交给 rename。",
            detail=detail,
            remedy="请检查下载目录或拆分资源后重试；已执行的清理记录见任务详情。",
            stage=safe_stage, provider_code=provider_code,
            provider_operation=provider_operation,
        )

    normalized = f"{provider_operation} {detail}".lower()
    if isinstance(exc, DownloadCleanupError):
        return DownloadFailure(
            code="download_cleanup_failed",
            summary="下载文件清理未完成，未交给 rename。",
            detail=detail,
            remedy=(
                "请检查片源内容，或调整 download 配置中的最小视频体积后重试。"
            ),
            stage=safe_stage,
        )

    if provider_code in _AUTH_CODES or any(
        marker in normalized for marker in _AUTH_MARKERS
    ):
        return DownloadFailure(
            code="open115_auth_failed",
            summary="115 授权已失效，离线任务未提交。",
            detail=detail,
            remedy="请发送 /auth 重新授权 115，然后重试。",
            stage=safe_stage,
            provider_code=provider_code,
            provider_operation=provider_operation,
        )

    if isinstance(exc, Open115Error) and (
        provider_operation == "create_directory"
        or "directory" in normalized
        or "folder" in normalized
        or "目录" in normalized
    ):
        return DownloadFailure(
            code="open115_directory_failed",
            summary="115 保存目录不可用，离线任务未提交。",
            detail=detail,
            remedy=(
                "请打开 /config，检查 download 保存目录及该目录的 "
                "115 写入权限，然后重试。"
            ),
            stage=safe_stage,
            provider_code=provider_code,
            provider_operation=provider_operation,
        )

    if isinstance(exc, Open115Error) and provider_operation == "add_offline_task":
        return DownloadFailure(
            code="open115_submit_rejected",
            summary="115 拒绝了离线任务。",
            detail=detail,
            remedy=(
                "请检查资源是否重复、受限或无效；必要时更换候选后重试。"
            ),
            stage=safe_stage,
            provider_code=provider_code,
            provider_operation=provider_operation,
        )

    if isinstance(exc, Open115Error):
        return DownloadFailure(
            code="open115_request_failed",
            summary="115 Open API 请求失败。",
            detail=detail,
            remedy="请检查网络与 115 Open API 可用性，稍后重试。",
            stage=safe_stage,
            provider_code=provider_code,
            provider_operation=provider_operation,
        )

    return DownloadFailure(
        code="download_failed",
        summary="115 下载任务失败。",
        detail=detail,
        remedy="请根据上述原因处理后重试；若仍失败，请查看 Download 日志。",
        stage=safe_stage,
    )
