from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


class OperationCancelled(BaseException):
    """Cooperative stop that must cross processor-level Exception handlers."""


@dataclass(frozen=True)
class RenameInverse:
    source_path: str
    target_path: str
    file_id: str


class RenameOperationJournal:
    def __init__(self):
        self.inverses: list[RenameInverse] = []
        self.irreversible_reason = ""

    @property
    def can_rollback(self) -> bool:
        return bool(self.inverses) and not self.irreversible_reason

    def mark_irreversible(self, reason: str):
        if not self.irreversible_reason:
            self.irreversible_reason = str(reason or "unknown_mutation")

    def record_rename(
        self,
        *,
        source_path: str,
        target_path: str,
        source_id: str,
        target_id: str,
    ) -> bool:
        source_id = str(source_id or "")
        target_id = str(target_id or "")
        if (
            not source_id
            or source_id != target_id
            or not source_path
            or not target_path
        ):
            self.mark_irreversible("rename_identity_unverified")
            return False
        self.inverses.append(RenameInverse(
            source_path=str(source_path),
            target_path=str(target_path),
            file_id=source_id,
        ))
        return True

    async def rollback(self, host, *, deadline: float = 120) -> dict:
        restored = []
        remaining = []
        error = ""
        for inverse in reversed(self.inverses):
            try:
                current = await self._storage(
                    host, "get_file_info", [inverse.target_path], deadline
                )
                original = await self._storage(
                    host, "get_file_info", [inverse.source_path], deadline
                )
            except Exception as exc:
                remaining.append(inverse.target_path)
                error = type(exc).__name__
                break
            current_id = self._file_id(current)
            if current_id != inverse.file_id or original is not None:
                remaining.append(inverse.target_path)
                break
            original_name = PurePosixPath(inverse.source_path).name
            try:
                renamed = await self._storage(
                    host,
                    "rename",
                    [inverse.target_path, original_name],
                    deadline,
                )
            except Exception as exc:
                remaining.append(inverse.target_path)
                error = type(exc).__name__
                break
            if renamed is not True:
                remaining.append(inverse.target_path)
                break
            try:
                verified = await self._storage(
                    host, "get_file_info", [inverse.source_path], deadline
                )
            except Exception as exc:
                remaining.append(inverse.source_path)
                error = type(exc).__name__
                break
            if self._file_id(verified) != inverse.file_id:
                remaining.append(inverse.source_path)
                break
            restored.append(inverse.source_path)
        restored_set = set(restored)
        for inverse in self.inverses:
            if inverse.source_path not in restored_set:
                remaining.append(inverse.target_path)
        remaining = list(dict.fromkeys(remaining))
        outcome = {
            "state": "rolled_back" if not remaining else "partially_rolled_back",
            "restored": restored,
            "remaining": remaining,
        }
        if error:
            outcome["error"] = error
        return outcome

    @staticmethod
    async def _storage(host, method: str, args: list, deadline: float):
        result = await host.call_capability(
            "storage.provider",
            method,
            {"args": args, "kwargs": {}},
            deadline=deadline,
        )
        return result.get("value")

    @staticmethod
    def _file_id(value) -> str:
        if not isinstance(value, dict):
            return ""
        return str(value.get("file_id") or value.get("fid") or "")
