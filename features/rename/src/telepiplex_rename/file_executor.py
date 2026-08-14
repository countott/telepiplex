"""Execute file resolutions without reintroducing directory-batch failure."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from .file_plan import FileResolution, normalize_storage_path


@dataclass(frozen=True)
class FileExecutionOutcome:
    source_id: str
    state: str
    source_path: str
    target_path: str
    observed_path: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class FileExecutionSummary:
    outcomes: tuple[FileExecutionOutcome, ...]
    organized_files: int
    canonical_no_ops: int
    kept_files: int
    failed_files: int


def _provider_id(value) -> str:
    if not isinstance(value, dict):
        return ""
    return str(
        value.get("source_id")
        or value.get("file_id")
        or value.get("fid")
        or value.get("id")
        or ""
    ).strip()


def _get_info(storage, path: str):
    method = getattr(storage, "get_file_info", None)
    if not callable(method) or not path:
        return None
    return method(path)


def _transition(
    journal,
    resolution: FileResolution,
    stage: str,
    observed_path: str,
    **details,
) -> None:
    method = getattr(journal, "record_file_transition", None)
    if callable(method):
        method(
            source_id=resolution.source_id,
            target_path=resolution.target_path,
            stage=stage,
            observed_path=observed_path,
            details=details,
        )


def _outcome(
    resolution: FileResolution,
    state: str,
    observed_path: str,
    *reason_codes: str,
) -> FileExecutionOutcome:
    return FileExecutionOutcome(
        source_id=resolution.source_id,
        state=state,
        source_path=resolution.source_path,
        target_path=resolution.target_path,
        observed_path=normalize_storage_path(observed_path),
        reason_codes=tuple(reason_codes) or resolution.reason_codes,
    )


def _execute_one(storage, resolution: FileResolution, journal=None):
    source = normalize_storage_path(resolution.source_path)
    target = normalize_storage_path(resolution.target_path)
    if resolution.action == "keep_original" or resolution.status != "resolved":
        _transition(journal, resolution, "kept", source)
        return _outcome(resolution, "kept", source)

    target_info = _get_info(storage, target)
    if target and _provider_id(target_info) == resolution.source_id:
        _transition(journal, resolution, "verified", target, replay=True)
        return _outcome(resolution, "no_op", target, "target_identity_verified")

    if resolution.action == "no_op" or source == target:
        source_info = _get_info(storage, source)
        if source_info is not None and (
            not _provider_id(source_info)
            or _provider_id(source_info) == resolution.source_id
        ):
            _transition(journal, resolution, "verified", source, no_op=True)
            return _outcome(resolution, "no_op", source)
        _transition(journal, resolution, "failed", source, reason="source_missing")
        return _outcome(resolution, "failed", source, "source_missing")

    current = source
    try:
        source_info = _get_info(storage, current)
        if source_info is None:
            _transition(
                journal, resolution, "failed", current, reason="source_missing"
            )
            return _outcome(resolution, "failed", current, "source_missing")
        source_id = _provider_id(source_info)
        if source_id and source_id != resolution.source_id:
            _transition(
                journal,
                resolution,
                "failed",
                current,
                reason="source_identity_changed",
            )
            return _outcome(
                resolution, "failed", current, "source_identity_changed"
            )

        target_path = PurePosixPath(target)
        current_path = PurePosixPath(current)
        if current_path.name != target_path.name:
            _transition(journal, resolution, "before_rename", current)
            renamed = storage.rename(current, target_path.name)
            if renamed is not True:
                _transition(
                    journal, resolution, "failed", current, reason="rename_failed"
                )
                return _outcome(resolution, "failed", current, "rename_failed")
            current = str(current_path.parent / target_path.name)
            _transition(journal, resolution, "after_rename", current)

        if normalize_storage_path(current) == target:
            verified = _get_info(storage, target)
            if verified is None:
                _transition(
                    journal,
                    resolution,
                    "failed",
                    current,
                    reason="target_missing_after_rename",
                )
                return _outcome(
                    resolution,
                    "failed",
                    current,
                    "target_missing_after_rename",
                )
            if _provider_id(verified) not in {"", resolution.source_id}:
                _transition(
                    journal,
                    resolution,
                    "failed",
                    current,
                    reason="target_identity_changed",
                )
                return _outcome(
                    resolution, "failed", current, "target_identity_changed"
                )
            _transition(journal, resolution, "verified", target)
            return _outcome(resolution, "organized", target)

        create_directory = getattr(storage, "create_dir_recursive", None)
        if callable(create_directory):
            prepared = create_directory(str(target_path.parent))
            if not prepared:
                _transition(
                    journal,
                    resolution,
                    "failed",
                    current,
                    reason="target_directory_failed",
                )
                return _outcome(
                    resolution,
                    "failed",
                    current,
                    "target_directory_failed",
                )
        _transition(journal, resolution, "before_move", current)
        detailed = getattr(storage, "move_file_detailed", None)
        if callable(detailed):
            result = detailed(current, str(target_path.parent))
        else:
            moved = storage.move_file(current, str(target_path.parent))
            result = {
                "state": "moved" if moved is True else "move_failed",
                "copied": moved is True,
                "source_deleted": moved is True,
                "target_path": target,
            }
        state = str((result or {}).get("state") or "")
        if state == "no_op":
            _transition(journal, resolution, "verified", target, provider_no_op=True)
            return _outcome(resolution, "no_op", target)
        if not (result or {}).get("copied"):
            _transition(
                journal, resolution, "failed", current, reason="move_failed"
            )
            return _outcome(resolution, "failed", current, "move_failed")
        if not (result or {}).get("source_deleted"):
            _transition(
                journal,
                resolution,
                "failed",
                current,
                reason="copied_source_retained",
            )
            return _outcome(
                resolution, "failed", current, "copied_source_retained"
            )
        verified = _get_info(storage, target)
        if verified is None:
            _transition(
                journal,
                resolution,
                "failed",
                target,
                reason="target_missing_after_move",
            )
            return _outcome(
                resolution, "failed", target, "target_missing_after_move"
            )
        _transition(journal, resolution, "verified", target)
        return _outcome(resolution, "organized", target)
    except Exception as exc:
        _transition(
            journal,
            resolution,
            "failed",
            current,
            reason=type(exc).__name__,
        )
        return _outcome(resolution, "failed", current, type(exc).__name__)


def execute_file_resolutions(
    storage,
    resolutions: list[FileResolution],
    *,
    selected_root: str,
    journal=None,
) -> FileExecutionSummary:
    del selected_root  # Cleanup is an explicit, separately verified phase.
    outcomes = tuple(
        _execute_one(storage, resolution, journal)
        for resolution in resolutions or []
    )
    return FileExecutionSummary(
        outcomes=outcomes,
        organized_files=sum(item.state == "organized" for item in outcomes),
        canonical_no_ops=sum(item.state == "no_op" for item in outcomes),
        kept_files=sum(item.state == "kept" for item in outcomes),
        failed_files=sum(item.state == "failed" for item in outcomes),
    )


def _list_items(value):
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("list"), list):
        return value["list"]
    data = value.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("list"), list):
        return data["list"]
    return None


def _fresh_directory_is_empty(storage, path: str) -> bool:
    try:
        info = storage.get_file_info(path)
        directory_id = _provider_id(info)
        if not info or not directory_id:
            return False
        response = storage.get_file_list({
            "cid": directory_id,
            "limit": 1,
            "show_dir": 1,
        })
    except Exception:
        return False
    items = _list_items(response)
    if items is None or items:
        return False
    if isinstance(response, dict):
        if response.get("has_more") is True:
            return False
        data = response.get("data")
        if isinstance(data, dict) and data.get("has_more") is True:
            return False
    return True


def cleanup_empty_source_directories(
    storage,
    resolutions: list[FileResolution],
    *,
    selected_root: str,
) -> list[str]:
    """Delete only source ancestors proven empty by a fresh provider listing."""

    root = PurePosixPath(normalize_storage_path(selected_root))
    candidates = set()
    for resolution in resolutions or []:
        path = PurePosixPath(normalize_storage_path(resolution.source_path)).parent
        while path != root and root in path.parents:
            candidates.add(str(path))
            path = path.parent
    deleted = []
    for path in sorted(
        candidates,
        key=lambda value: (-len(PurePosixPath(value).parts), value),
    ):
        if not _fresh_directory_is_empty(storage, path):
            continue
        try:
            if storage.delete_single_file(path) is True:
                deleted.append(path)
        except Exception:
            continue
    return deleted
