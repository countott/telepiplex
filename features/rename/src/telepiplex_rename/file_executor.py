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


@dataclass(frozen=True)
class DirectoryCleanupOutcome:
    path: str
    state: str
    reason_code: str


@dataclass(frozen=True)
class DirectoryCleanupSummary:
    outcomes: tuple[DirectoryCleanupOutcome, ...]
    candidate_directories: int
    deleted_directories: int
    retained_directories: int
    failed_directories: int
    complete: bool

    def to_dict(self) -> dict:
        return {
            "candidate_directories": self.candidate_directories,
            "deleted_directories": self.deleted_directories,
            "retained_directories": self.retained_directories,
            "failed_directories": self.failed_directories,
            "complete": self.complete,
            "deleted_paths": [
                item.path
                for item in self.outcomes
                if item.state == "deleted"
            ],
            "failures": [{
                "path": item.path,
                "state": item.state,
                "reason_code": item.reason_code,
            } for item in self.outcomes if item.state in {
                "lookup_failed",
                "delete_failed",
            }],
        }


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


def _sha1(value) -> str:
    if not isinstance(value, dict):
        return ""
    return str(
        value.get("sha1") or value.get("sha") or value.get("file_sha1") or ""
    ).strip().lower()


def _size(value) -> int:
    if not isinstance(value, dict):
        return 0
    try:
        return int(
            value.get("size") or value.get("fs") or value.get("size_byte") or 0
        )
    except (TypeError, ValueError):
        return 0


def _matches_fingerprint(expected, observed, *, source_id="") -> bool:
    if not isinstance(observed, dict):
        return False
    if source_id and _provider_id(observed) == str(source_id):
        return True
    expected_sha1 = _sha1(expected)
    observed_sha1 = _sha1(observed)
    if expected_sha1 and observed_sha1:
        return expected_sha1 == observed_sha1
    expected_size = _size(expected)
    observed_size = _size(observed)
    return bool(expected_size and observed_size and expected_size == observed_size)


def _get_info(storage, path: str):
    method = getattr(storage, "get_file_info", None)
    if not callable(method) or not path:
        return None
    return method(path)


def prefetch_file_info(storage, paths) -> dict[str, object]:
    normalized = []
    seen = set()
    for path in paths or ():
        value = normalize_storage_path(path)
        if value and value not in seen:
            seen.add(value)
            normalized.append(value)
    if not normalized:
        return {}
    method = getattr(storage, "get_file_info_batch", None)
    if callable(method):
        values = method(normalized)
        if isinstance(values, dict):
            return {
                path: values.get(path)
                for path in normalized
            }
    return {
        path: _get_info(storage, path)
        for path in normalized
    }


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


def _execute_one(
    storage,
    resolution: FileResolution,
    journal=None,
    initial_info=None,
):
    source = normalize_storage_path(resolution.source_path)
    target = normalize_storage_path(resolution.target_path)
    if resolution.action == "keep_original" or resolution.status != "resolved":
        _transition(journal, resolution, "kept", source)
        return _outcome(resolution, "kept", source)

    initial_info = initial_info or {}

    def initial(path):
        if path in initial_info:
            return initial_info[path]
        return _get_info(storage, path)

    target_info = initial(target)
    if target and _provider_id(target_info) == resolution.source_id:
        _transition(journal, resolution, "verified", target, replay=True)
        return _outcome(resolution, "no_op", target, "target_identity_verified")

    if resolution.action == "recover_duplicate_copy":
        expected = dict(resolution.source_fingerprint or {})
        if not _matches_fingerprint(expected, target_info):
            _transition(
                journal,
                resolution,
                "failed",
                target,
                reason="target_identity_unverifiable",
            )
            return _outcome(
                resolution,
                "failed",
                target,
                "target_identity_unverifiable",
            )
        source_info = initial(source)
        if source_info is None:
            _transition(journal, resolution, "verified", target, replay=True)
            return _outcome(
                resolution,
                "no_op",
                target,
                "recovered_source_already_absent",
            )
        if not _matches_fingerprint(
            expected,
            source_info,
            source_id=resolution.source_id,
        ):
            _transition(
                journal,
                resolution,
                "failed",
                source,
                reason="source_identity_changed",
            )
            return _outcome(
                resolution,
                "failed",
                source,
                "source_identity_changed",
            )
        try:
            deleted = storage.delete_single_file(source) is True
        except Exception:
            deleted = False
        if not deleted or _get_info(storage, source) is not None:
            _transition(
                journal,
                resolution,
                "failed",
                source,
                reason="copied_source_retained",
            )
            return _outcome(
                resolution,
                "failed",
                source,
                "copied_source_retained",
            )
        verified_target = _get_info(storage, target)
        if not _matches_fingerprint(expected, verified_target):
            _transition(
                journal,
                resolution,
                "failed",
                target,
                reason="target_identity_changed",
            )
            return _outcome(
                resolution,
                "failed",
                target,
                "target_identity_changed",
            )
        _transition(
            journal,
            resolution,
            "verified",
            target,
            recovered_copy=True,
        )
        return _outcome(
            resolution,
            "organized",
            target,
            "recovered_interrupted_copy",
        )

    if resolution.action == "no_op" or source == target:
        source_info = initial(source)
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
        source_info = initial(current)
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
        if target_info is not None:
            _transition(
                journal,
                resolution,
                "failed",
                target,
                reason="target_conflict",
            )
            return _outcome(resolution, "failed", target, "target_conflict")

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
        if not _matches_fingerprint(
            source_info,
            verified,
            source_id=resolution.source_id,
        ):
            _transition(
                journal,
                resolution,
                "failed",
                target,
                reason="target_identity_unverifiable",
            )
            return _outcome(
                resolution,
                "failed",
                target,
                "target_identity_unverifiable",
            )
        if _get_info(storage, current) is not None:
            _transition(
                journal,
                resolution,
                "failed",
                current,
                reason="source_still_present_after_move",
            )
            return _outcome(
                resolution,
                "failed",
                current,
                "source_still_present_after_move",
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
    resolutions = list(resolutions or [])
    initial_info = prefetch_file_info(
        storage,
        [
            path
            for resolution in resolutions
            if resolution.action != "keep_original"
            and resolution.status == "resolved"
            for path in (resolution.source_path, resolution.target_path)
            if path
        ],
    )
    outcomes = tuple(
        _execute_one(
            storage,
            resolution,
            journal,
            initial_info=initial_info,
        )
        for resolution in resolutions
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


def _fresh_directory_state(storage, path: str) -> str:
    try:
        info = storage.get_file_info(path)
        if info is None:
            return "absent"
        directory_id = _provider_id(info)
        if not info or not directory_id:
            return "lookup_failed"
        response = storage.get_file_list({
            "cid": directory_id,
            "limit": 1,
            "show_dir": 1,
        })
    except Exception:
        return "lookup_failed"
    items = _list_items(response)
    if items is None:
        return "lookup_failed"
    if items:
        return "nonempty"
    if isinstance(response, dict):
        if response.get("has_more") is True:
            return "nonempty"
        data = response.get("data")
        if isinstance(data, dict) and data.get("has_more") is True:
            return "nonempty"
    return "empty"


def _fresh_directory_is_empty(storage, path: str) -> bool:
    return _fresh_directory_state(storage, path) == "empty"


def cleanup_source_directories(
    storage,
    resolutions: list[FileResolution],
    *,
    selected_root: str,
    include_selected_root: bool,
    protected_roots: tuple[str, ...] = (),
) -> DirectoryCleanupSummary:
    """Freshly verify and clean source ancestors after file verification."""

    root_value = normalize_storage_path(selected_root)
    root = PurePosixPath(root_value)
    protected = {
        normalize_storage_path(path)
        for path in protected_roots
        if normalize_storage_path(path)
    }
    candidates = set()
    for resolution in resolutions or []:
        path = PurePosixPath(
            normalize_storage_path(resolution.source_path)
        ).parent
        while path != root and root in path.parents:
            candidates.add(str(path))
            path = path.parent
        if include_selected_root and path == root and root_value not in {"", "/"}:
            candidates.add(root_value)

    outcomes = []
    for path in sorted(
        candidates,
        key=lambda value: (-len(PurePosixPath(value).parts), value),
    ):
        if path in protected:
            outcomes.append(DirectoryCleanupOutcome(
                path=path,
                state="protected",
                reason_code="protected_root",
            ))
            continue
        state = _fresh_directory_state(storage, path)
        if state == "nonempty":
            outcomes.append(DirectoryCleanupOutcome(
                path=path,
                state="retained_nonempty",
                reason_code="directory_not_empty",
            ))
            continue
        if state == "absent":
            outcomes.append(DirectoryCleanupOutcome(
                path=path,
                state="already_absent",
                reason_code="source_directory_already_absent",
            ))
            continue
        if state != "empty":
            outcomes.append(DirectoryCleanupOutcome(
                path=path,
                state="lookup_failed",
                reason_code="fresh_listing_failed",
            ))
            continue
        try:
            deleted = storage.delete_single_file(path) is True
        except Exception:
            deleted = False
        post_delete_state = (
            _fresh_directory_state(storage, path)
            if deleted
            else "delete_failed"
        )
        verified_deleted = deleted and post_delete_state == "absent"
        outcomes.append(DirectoryCleanupOutcome(
            path=path,
            state="deleted" if verified_deleted else "delete_failed",
            reason_code=(
                "empty_directory_deleted"
                if verified_deleted
                else (
                    "post_delete_lookup_failed"
                    if post_delete_state == "lookup_failed"
                    else "directory_still_present"
                    if deleted
                    else "provider_delete_failed"
                )
            ),
        ))

    frozen = tuple(outcomes)
    failed = sum(
        item.state in {"lookup_failed", "delete_failed"}
        for item in frozen
    )
    retained_nonempty = sum(
        item.state == "retained_nonempty"
        for item in frozen
    )
    return DirectoryCleanupSummary(
        outcomes=frozen,
        candidate_directories=len(frozen),
        deleted_directories=sum(
            item.state == "deleted" for item in frozen
        ),
        retained_directories=sum(
            item.state in {"retained_nonempty", "protected"}
            for item in frozen
        ),
        failed_directories=failed,
        complete=failed == 0 and retained_nonempty == 0,
    )


def cleanup_empty_source_directories(
    storage,
    resolutions: list[FileResolution],
    *,
    selected_root: str,
) -> list[str]:
    """Delete only source ancestors proven empty by a fresh provider listing."""

    summary = cleanup_source_directories(
        storage,
        resolutions,
        selected_root=selected_root,
        include_selected_root=False,
    )
    return [
        item.path
        for item in summary.outcomes
        if item.state == "deleted"
    ]
