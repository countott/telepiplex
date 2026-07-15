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

    async def rollback(self, core, *, deadline: float = 120) -> dict:
        restored = []
        remaining = []
        for inverse in reversed(self.inverses):
            current = await self._storage(
                core, "get_file_info", [inverse.target_path], deadline
            )
            current_id = self._file_id(current)
            if current_id != inverse.file_id:
                remaining.append(inverse.target_path)
                break
            original_name = PurePosixPath(inverse.source_path).name
            renamed = await self._storage(
                core,
                "rename",
                [inverse.target_path, original_name],
                deadline,
            )
            if renamed is not True:
                remaining.append(inverse.target_path)
                break
            verified = await self._storage(
                core, "get_file_info", [inverse.source_path], deadline
            )
            if self._file_id(verified) != inverse.file_id:
                remaining.append(inverse.source_path)
                break
            restored.append(inverse.source_path)
        restored_set = set(restored)
        for inverse in self.inverses:
            if inverse.source_path not in restored_set:
                remaining.append(inverse.target_path)
        remaining = list(dict.fromkeys(remaining))
        return {
            "state": "rolled_back" if not remaining else "partially_rolled_back",
            "restored": restored,
            "remaining": remaining,
        }

    @staticmethod
    async def _storage(core, method: str, args: list, deadline: float):
        result = await core.call_capability(
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
