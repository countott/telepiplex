"""Execute file resolutions without reintroducing directory-batch failure."""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections import defaultdict
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Mapping

from .file_plan import FileResolution, normalize_storage_path


def _normalized_text_scalar(value, *, field: str, lower=False) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TypeError(f"{field} must be a string or integer scalar")
    normalized = str(value).strip()
    return normalized.lower() if lower else normalized


def _normalized_size_scalar(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TypeError("size must be a string or integer scalar")
    try:
        normalized = int(str(value).strip())
    except ValueError as exc:
        raise TypeError("size must be an integer scalar") from exc
    if normalized < 0:
        raise ValueError("size must not be negative")
    return normalized


@dataclass(frozen=True, slots=True)
class PreflightFileInfo:
    provider_id: str
    sha1: str
    size: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_id",
            _normalized_text_scalar(self.provider_id, field="provider_id"),
        )
        object.__setattr__(
            self,
            "sha1",
            _normalized_text_scalar(self.sha1, field="sha1", lower=True),
        )
        object.__setattr__(self, "size", _normalized_size_scalar(self.size))


@dataclass(frozen=True, slots=True)
class FileTransactionSnapshot:
    """Immutable pre-mutation facts for one synchronous file transaction."""

    file_info: Mapping[str, PreflightFileInfo | None]
    source_parent_ids: Mapping[str, str]

    def __post_init__(self) -> None:
        normalized_info = {}
        for path, value in dict(self.file_info).items():
            normalized = normalize_storage_path(path)
            if not normalized:
                raise ValueError("snapshot file path must be non-empty")
            if value is not None and not isinstance(value, PreflightFileInfo):
                raise TypeError("snapshot file facts must be projected values")
            if normalized in normalized_info and normalized_info[normalized] != value:
                raise ValueError(f"conflicting snapshot facts for {normalized}")
            normalized_info[normalized] = value
        normalized_parents = {}
        for path, provider_id in dict(self.source_parent_ids).items():
            normalized = normalize_storage_path(path)
            if not normalized:
                raise ValueError("snapshot parent path must be non-empty")
            value = _normalized_text_scalar(
                provider_id,
                field="source_parent_id",
            )
            if (
                normalized in normalized_parents
                and normalized_parents[normalized] != value
            ):
                raise ValueError(f"conflicting snapshot parent IDs for {normalized}")
            normalized_parents[normalized] = value
        for path, provider_id in normalized_parents.items():
            if path not in normalized_info:
                raise ValueError(
                    f"source parent fact missing from snapshot: {path}"
                )
            parent_fact = normalized_info[path]
            projected_id = (
                "" if parent_fact is None else parent_fact.provider_id
            )
            if projected_id != provider_id:
                raise ValueError(
                    f"source parent ID mismatch for {path}"
                )
        object.__setattr__(
            self, "file_info", MappingProxyType(dict(normalized_info))
        )
        object.__setattr__(
            self,
            "source_parent_ids",
            MappingProxyType(dict(normalized_parents)),
        )

    @classmethod
    def from_provider_facts(
        cls,
        file_info: Mapping[str, object],
        source_parent_ids: Mapping[str, str],
    ) -> FileTransactionSnapshot:
        projected = {}
        for path, value in dict(file_info).items():
            normalized = normalize_storage_path(path)
            projected_value = (
                None
                if value is None
                else PreflightFileInfo(
                    provider_id=_provider_id(value),
                    sha1=_sha1(value),
                    size=_size(value),
                )
            )
            if (
                normalized in projected
                and projected[normalized] != projected_value
            ):
                raise ValueError(f"conflicting snapshot facts for {normalized}")
            projected[normalized] = projected_value
        return cls(projected, source_parent_ids)

    def require_file_info(self, path: str) -> PreflightFileInfo | None:
        return self.file_info[normalize_storage_path(path)]

    def require_source_parent_id(self, path: str) -> str:
        return self.source_parent_ids[normalize_storage_path(path)]


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
    if isinstance(value, PreflightFileInfo):
        return value.provider_id
    if not isinstance(value, dict):
        return ""
    for key in ("source_id", "file_id", "fid", "id"):
        if key not in value:
            continue
        candidate = value[key]
        if candidate is None:
            continue
        if isinstance(candidate, str) and not candidate.strip():
            continue
        return _normalized_text_scalar(candidate, field="provider_id")
    return ""


def _sha1(value) -> str:
    if isinstance(value, PreflightFileInfo):
        return value.sha1
    if not isinstance(value, dict):
        return ""
    for key in ("sha1", "sha", "file_sha1"):
        if key not in value:
            continue
        candidate = value[key]
        if candidate is None:
            continue
        if isinstance(candidate, str) and not candidate.strip():
            continue
        return _normalized_text_scalar(candidate, field="sha1", lower=True)
    return ""


def _size(value) -> int:
    if isinstance(value, PreflightFileInfo):
        return value.size
    if not isinstance(value, dict):
        return 0
    # 115 may expose ``size`` as a human-readable display value (for example,
    # ``11.57GB``); prefer its exact byte-count fields for fingerprinting.
    for key in ("size_byte", "fs", "size"):
        if key not in value:
            continue
        candidate = value[key]
        if candidate is None or candidate == "":
            continue
        return _normalized_size_scalar(candidate)
    return 0


def _matches_fingerprint(expected, observed, *, source_id="") -> bool:
    if not isinstance(observed, (dict, PreflightFileInfo)):
        return False
    observed_id = _provider_id(observed)
    if source_id and observed_id:
        return observed_id == str(source_id)
    expected_sha1 = _sha1(expected)
    observed_sha1 = _sha1(observed)
    expected_size = _size(expected)
    observed_size = _size(observed)
    return bool(
        expected_sha1
        and observed_sha1
        and expected_sha1 == observed_sha1
        and expected_size
        and observed_size
        and expected_size == observed_size
    )


def _matches_resolution_identity(
    resolution: FileResolution,
    observed,
) -> bool:
    expected_id = str(resolution.source_id or "").strip()
    if not expected_id:
        return False
    return _matches_fingerprint(
        resolution.source_fingerprint,
        observed,
        source_id=expected_id,
    )


def _matches_legacy_copy_target(
    expected,
    observed,
    *,
    source_id: str,
) -> bool:
    """Accept a preserved object ID or a complete copy-content proof."""

    if not isinstance(observed, (dict, PreflightFileInfo)):
        return False
    observed_id = _provider_id(observed)
    if observed_id and observed_id == str(source_id):
        return True
    return _matches_fingerprint(expected, observed)


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
        try:
            values = method(normalized)
        except (AttributeError, NotImplementedError):
            values = None
        else:
            if not isinstance(values, dict):
                raise TypeError("storage file info batch must return a mapping")
            missing = [path for path in normalized if path not in values]
            if missing:
                raise ValueError(
                    "storage file info batch missing requested keys: "
                    + ", ".join(missing)
                )
            return {path: values[path] for path in normalized}
    direct = getattr(storage, "get_file_info", None)
    if not callable(direct):
        raise AttributeError("storage get_file_info capability is unavailable")
    return {path: direct(path) for path in normalized}


def build_file_transaction_snapshot(
    storage,
    *,
    file_paths,
    source_parent_paths,
) -> FileTransactionSnapshot:
    normalized_parents = tuple(dict.fromkeys(
        normalize_storage_path(path)
        for path in source_parent_paths or ()
        if normalize_storage_path(path)
    ))
    requested = tuple(dict.fromkeys(
        normalize_storage_path(path)
        for path in (*tuple(file_paths or ()), *normalized_parents)
        if normalize_storage_path(path)
    ))
    directory_snapshot = _build_directory_preflight_snapshot(
        storage,
        requested=requested,
        file_paths=file_paths,
        source_parent_paths=normalized_parents,
    )
    if directory_snapshot is not None:
        return directory_snapshot
    provider_facts = prefetch_file_info(storage, requested)
    parent_ids = {
        path: _provider_id(provider_facts[path])
        for path in normalized_parents
    }
    return FileTransactionSnapshot.from_provider_facts(
        provider_facts,
        parent_ids,
    )


def _build_directory_preflight_snapshot(
    storage,
    *,
    requested: tuple[str, ...],
    file_paths,
    source_parent_paths: tuple[str, ...],
) -> FileTransactionSnapshot | None:
    """Build all pre-mutation facts from complete listings, or none of them."""

    normalized_files = tuple(dict.fromkeys(
        normalize_storage_path(path)
        for path in file_paths or ()
        if normalize_storage_path(path)
    ))
    if not requested or not normalized_files:
        return None
    directory_paths = tuple(dict.fromkeys(
        normalize_storage_path(str(PurePosixPath(path).parent))
        for path in normalized_files
    ))
    parent_lookup_paths = tuple(dict.fromkeys(
        (*directory_paths, *source_parent_paths)
    ))
    exact_budget = len(requested)
    optimistic_directory_budget = (
        len(parent_lookup_paths) + len(directory_paths)
    )
    if optimistic_directory_budget >= exact_budget:
        return None
    if not callable(getattr(storage, "get_file_info_batch", None)):
        return None
    try:
        parent_facts = prefetch_file_info(storage, parent_lookup_paths)
    except Exception:
        return None

    requested_by_parent = defaultdict(dict)
    for path in normalized_files:
        parent = normalize_storage_path(str(PurePosixPath(path).parent))
        requested_by_parent[parent][PurePosixPath(path).name] = path

    projected = {}
    for parent_path in directory_paths:
        parent_fact = parent_facts.get(parent_path)
        if parent_fact is None:
            if parent_path in source_parent_paths:
                return None
            for path in requested_by_parent[parent_path].values():
                projected[path] = None
            continue
        directory_id = _directory_id(parent_fact)
        if not directory_id:
            return None
        items = _complete_directory_items(storage, directory_id)
        if items is None:
            return None
        requested_children = requested_by_parent[parent_path]
        matched = {}
        for item in items:
            name = _item_name(item)
            path = requested_children.get(name)
            if not path:
                continue
            try:
                fact = _trusted_listing_file_info(item)
            except (TypeError, ValueError):
                return None
            if not fact:
                return None
            existing = matched.get(path)
            if existing is not None and existing != fact:
                return None
            matched[path] = fact
        for path in requested_children.values():
            projected[path] = matched.get(path)

    provider_facts = {
        path: projected[path] if path in projected else parent_facts[path]
        for path in requested
    }
    parent_ids = {
        path: _provider_id(parent_facts[path])
        for path in source_parent_paths
    }
    try:
        return FileTransactionSnapshot.from_provider_facts(
            provider_facts,
            parent_ids,
        )
    except (TypeError, ValueError):
        return None


def _trusted_listing_file_info(value) -> PreflightFileInfo | None:
    provider_id, _name, sha1, size = _listing_item_facts(value)
    has_size = any(
        key in value and value[key] not in (None, "")
        for key in ("size_byte", "fs", "size")
    )
    if not provider_id or not sha1 or not has_size:
        return None
    return PreflightFileInfo(provider_id, sha1, size)


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
    source_parent_id: str | None = None,
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
    target_provider_id = _provider_id(target_info)
    if (
        target
        and target_provider_id
        and target_provider_id == resolution.source_id
    ):
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
        if source_parent_id is not None and not source_parent_id:
            return _outcome(
                resolution,
                "failed",
                source,
                "source_directory_unverifiable",
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
        if source_info is not None and _matches_resolution_identity(
            resolution,
            source_info,
        ):
            _transition(journal, resolution, "verified", source, no_op=True)
            return _outcome(resolution, "no_op", source)
        reason = (
            "source_missing"
            if source_info is None
            else "source_identity_changed"
        )
        _transition(journal, resolution, "failed", source, reason=reason)
        return _outcome(resolution, "failed", source, reason)

    current = source
    try:
        source_info = initial(current)
        if source_info is None:
            _transition(
                journal, resolution, "failed", current, reason="source_missing"
            )
            return _outcome(resolution, "failed", current, "source_missing")
        if not _matches_resolution_identity(resolution, source_info):
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
        if source_parent_id is not None and not source_parent_id:
            return _outcome(
                resolution,
                "failed",
                current,
                "source_directory_unverifiable",
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
            if not _matches_resolution_identity(resolution, verified):
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
        if not _matches_legacy_copy_target(
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
    move_batch_size: int = 32,
    preflight: FileTransactionSnapshot | None = None,
) -> FileExecutionSummary:
    del selected_root  # Cleanup is an explicit, separately verified phase.
    resolutions = list(resolutions or [])
    actionable = [
        resolution
        for resolution in resolutions
        if resolution.action != "keep_original"
        and resolution.status == "resolved"
    ]
    source_parent_paths = [
        str(PurePosixPath(resolution.source_path).parent)
        for resolution in actionable
        if resolution.source_path
    ]
    if preflight is None:
        preflight = build_file_transaction_snapshot(
            storage,
            file_paths=[
                path
                for resolution in actionable
                for path in (resolution.source_path, resolution.target_path)
                if path
            ],
            source_parent_paths=source_parent_paths,
        )
    initial_info = {
        normalize_storage_path(path): preflight.require_file_info(path)
        for resolution in actionable
        for path in (resolution.source_path, resolution.target_path)
        if path
    }
    source_parent_ids = {}
    for path in source_parent_paths:
        preflight.require_file_info(path)
        source_parent_ids[normalize_storage_path(path)] = (
            preflight.require_source_parent_id(path)
        )

    def captured_parent_id(resolution: FileResolution) -> str | None:
        if (
            resolution.action == "keep_original"
            or resolution.status != "resolved"
        ):
            return None
        parent = normalize_storage_path(
            str(PurePosixPath(resolution.source_path).parent)
        )
        return source_parent_ids[parent]
    native_move = getattr(storage, "move_files_by_id", None)
    if callable(native_move):
        outcomes = _execute_with_native_batches(
            storage,
            resolutions,
            initial_info=initial_info,
            preflight=preflight,
            journal=journal,
            move_batch_size=move_batch_size,
        )
    else:
        outcomes = tuple(
            _execute_one(
                storage,
                resolution,
                journal,
                initial_info=initial_info,
                source_parent_id=captured_parent_id(resolution),
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


def _directory_id(value) -> str:
    return _provider_id(value)


def _item_name(value) -> str:
    if not isinstance(value, dict):
        return ""
    return str(
        value.get("fn")
        or value.get("n")
        or value.get("file_name")
        or value.get("name")
        or ""
    ).strip()


def _fresh_directory_items(storage, directory_id: str):
    if not directory_id:
        return None
    collected = []
    offset = 0
    limit = 1000
    for _page in range(100):
        try:
            response = storage.get_file_list({
                "cid": directory_id,
                "offset": offset,
                "limit": limit,
                "show_dir": 1,
            })
        except Exception:
            return None
        items = _list_items(response)
        if items is None:
            return None
        collected.extend(item for item in items if isinstance(item, dict))
        has_more = False
        if isinstance(response, dict):
            has_more = response.get("has_more") is True
            data = response.get("data")
            if isinstance(data, dict):
                has_more = has_more or data.get("has_more") is True
        if not has_more and len(items) < limit:
            return collected
        if not items:
            return collected
        offset += len(items)
    return None


def _complete_directory_items(storage, directory_id: str) -> list[dict] | None:
    """Return a strictly complete listing suitable for immutable preflight."""

    if not directory_id:
        return None
    collected = []
    seen_page_signatures = set()
    seen_item_facts = set()
    seen_ids = {}
    offset = 0
    limit = 1000
    for _page in range(100):
        try:
            response = storage.get_file_list({
                "cid": directory_id,
                "offset": offset,
                "limit": limit,
                "show_dir": 1,
            })
        except Exception:
            return None
        items = _list_items(response)
        if items is None or any(not isinstance(item, dict) for item in items):
            return None
        has_more = _listing_has_more(response)
        if has_more is None:
            return None
        if not items:
            return None if has_more else collected
        try:
            page_facts = tuple(_listing_item_facts(item) for item in items)
        except Exception:
            return None
        page_signature = frozenset(page_facts)
        if len(page_signature) != len(page_facts):
            return None
        if page_signature in seen_page_signatures:
            return None
        seen_page_signatures.add(page_signature)
        added_item = False
        for item, facts in zip(items, page_facts):
            provider_id, _name, _sha1_value, _size_value = facts
            if facts not in seen_item_facts:
                seen_item_facts.add(facts)
                added_item = True
            identity = facts[1:]
            prior_identity = seen_ids.get(provider_id)
            if prior_identity is not None and prior_identity != identity:
                return None
            seen_ids[provider_id] = identity
        if not added_item:
            return None
        collected.extend(items)
        if not has_more and len(items) < limit:
            return collected
        offset += len(items)
    return None


def _listing_item_facts(value) -> tuple[str, str, str, int]:
    """Project a list entry to the stable fields used for pagination trust."""

    if not isinstance(value, dict):
        raise TypeError("directory listing item must be a mapping")
    provider_id = _provider_id(value)
    if not provider_id:
        raise ValueError("directory listing item lacks a provider ID")
    name_value = None
    for key in ("fn", "n", "file_name", "name"):
        candidate = value.get(key)
        if candidate is None or candidate == "":
            continue
        name_value = candidate
        break
    if name_value is None:
        raise ValueError("directory listing item lacks a name")
    return (
        provider_id,
        _normalized_text_scalar(name_value, field="file_name"),
        _sha1(value),
        _size(value),
    )


def _listing_has_more(response) -> bool | None:
    if not isinstance(response, dict):
        return False
    values = []
    if "has_more" in response:
        value = response["has_more"]
        if type(value) is not bool:
            return None
        values.append(value)
    data = response.get("data")
    if isinstance(data, dict) and "has_more" in data:
        value = data["has_more"]
        if type(value) is not bool:
            return None
        values.append(value)
    if len(set(values)) > 1:
        return None
    return values[0] if values else False


def _legacy_move_prepared(storage, prepared: dict, journal=None):
    resolution = prepared["resolution"]
    current = prepared["current_path"]
    replacement = replace(
        resolution,
        source_path=current,
        action="move_only",
    )
    legacy = _execute_one(
        storage,
        replacement,
        journal,
        initial_info={},
        source_parent_id=prepared["source_parent_id"],
    )
    return FileExecutionOutcome(
        source_id=resolution.source_id,
        state=legacy.state,
        source_path=resolution.source_path,
        target_path=resolution.target_path,
        observed_path=legacy.observed_path,
        reason_codes=legacy.reason_codes,
    )


def _prepare_native_move(
    storage,
    resolution: FileResolution,
    *,
    initial_info: dict,
    directory_info: dict,
    source_parent_id: str,
    journal=None,
):
    source = normalize_storage_path(resolution.source_path)
    target = normalize_storage_path(resolution.target_path)
    target_info = initial_info.get(target)
    target_provider_id = _provider_id(target_info)
    if (
        target
        and target_provider_id
        and target_provider_id == resolution.source_id
    ):
        _transition(journal, resolution, "verified", target, replay=True)
        return _outcome(
            resolution, "no_op", target, "target_identity_verified"
        )
    source_info = initial_info.get(source)
    if source_info is None:
        return _outcome(resolution, "failed", source, "source_missing")
    if not _matches_resolution_identity(resolution, source_info):
        return _outcome(
            resolution, "failed", source, "source_identity_changed"
        )
    if target_info is not None:
        return _outcome(resolution, "failed", target, "target_conflict")
    if not source_parent_id:
        return _outcome(
            resolution,
            "failed",
            source,
            "source_directory_unverifiable",
        )

    current = source
    target_path = PurePosixPath(target)
    current_path = PurePosixPath(current)
    if current_path.name != target_path.name:
        _transition(journal, resolution, "before_rename", current)
        bound_journal = (
            journal is not None
            and getattr(storage, "journal", None) is journal
            and isinstance(getattr(journal, "inverses", None), list)
        )
        inverse_count = len(journal.inverses) if bound_journal else 0
        try:
            renamed = storage.rename(current, target_path.name)
        except Exception:
            renamed = False
        if renamed is not True:
            return _outcome(resolution, "failed", current, "rename_failed")
        current = str(current_path.parent / target_path.name)
        _transition(journal, resolution, "after_rename", current)
        if bound_journal:
            inverse = (
                journal.inverses[-1]
                if len(journal.inverses) == inverse_count + 1
                else None
            )
            if (
                inverse is None
                or normalize_storage_path(inverse.target_path)
                != normalize_storage_path(current)
                or str(inverse.file_id) != resolution.source_id
            ):
                return _outcome(
                    resolution,
                    "failed",
                    current,
                    "target_identity_changed",
                )

    if normalize_storage_path(current) == target:
        verified = _get_info(storage, target)
        if verified is None:
            return _outcome(
                resolution, "failed", current, "target_missing_after_rename"
            )
        if not _matches_resolution_identity(resolution, verified):
            return _outcome(
                resolution, "failed", current, "target_identity_changed"
            )
        _transition(journal, resolution, "verified", target)
        return _outcome(resolution, "organized", target)

    target_dir = str(target_path.parent)
    target_dir_info = directory_info.get(target_dir)
    if target_dir_info is None:
        create_directory = getattr(storage, "create_dir_recursive", None)
        try:
            target_dir_info = (
                create_directory(target_dir)
                if callable(create_directory)
                else _get_info(storage, target_dir)
            )
        except Exception:
            target_dir_info = None
        if not isinstance(target_dir_info, dict):
            target_dir_info = _get_info(storage, target_dir)
        directory_info[target_dir] = target_dir_info
    target_dir_id = _directory_id(target_dir_info)
    if not target_dir_id:
        return _outcome(
            resolution, "failed", current, "target_directory_failed"
        )
    return {
        "resolution": resolution,
        "current_path": current,
        "target_path": target,
        "target_dir": target_dir,
        "target_dir_id": target_dir_id,
        "source_parent_id": source_parent_id,
    }


def _reconcile_native_chunk(storage, chunk: list[dict], journal=None):
    listings = {}
    directory_ids = {
        item["target_dir_id"] for item in chunk
    } | {
        item["source_parent_id"] for item in chunk
    }
    for directory_id in directory_ids:
        listings[directory_id] = _fresh_directory_items(
            storage, directory_id
        )
    outcomes = []
    for item in chunk:
        resolution = item["resolution"]
        target_items = listings.get(item["target_dir_id"])
        source_items = listings.get(item["source_parent_id"])
        if target_items is None or source_items is None:
            outcomes.append(_outcome(
                resolution,
                "failed",
                item["current_path"],
                "fresh_listing_failed",
            ))
            continue
        target_identity_items = [
            value for value in target_items
            if _provider_id(value) == resolution.source_id
        ]
        target_ok = any(
            _item_name(value) == PurePosixPath(item["target_path"]).name
            for value in target_identity_items
        )
        source_present = any(
            _provider_id(value) == resolution.source_id
            for value in source_items
        )
        if target_ok and not source_present:
            _transition(journal, resolution, "verified", item["target_path"])
            outcomes.append(_outcome(
                resolution, "organized", item["target_path"]
            ))
            continue
        if target_identity_items and not target_ok:
            reason = "target_name_mismatch_after_move"
        elif not target_identity_items:
            reason = "target_missing_after_move"
        else:
            reason = "source_still_present_after_move"
        outcomes.append(_outcome(
            resolution, "failed", item["current_path"], reason
        ))
    return outcomes


def _gate_native_move_chunk(
    storage,
    chunk: list[dict],
    journal=None,
) -> tuple[list[dict], dict[int, FileExecutionOutcome]]:
    """Freshly prove each native move is still safe to submit.

    ``FileTransactionSnapshot`` is intentionally only pre-mutation evidence.
    This gate uses complete current listings for the source and target
    directories immediately before submitting a native move batch.
    """

    listings = {}
    directory_ids = {
        item["source_parent_id"] for item in chunk
    } | {
        item["target_dir_id"] for item in chunk
    }
    for directory_id in directory_ids:
        items = _complete_directory_items(storage, directory_id)
        if items is None:
            outcomes = {}
            for item in chunk:
                resolution = item["resolution"]
                _transition(
                    journal,
                    resolution,
                    "failed",
                    item["current_path"],
                    reason="fresh_listing_failed",
                )
                outcomes[item["index"]] = _outcome(
                    resolution,
                    "failed",
                    item["current_path"],
                    "fresh_listing_failed",
                )
            return [], outcomes
        listings[directory_id] = items

    accepted = []
    outcomes = {}
    for item in chunk:
        resolution = item["resolution"]
        source_items = listings[item["source_parent_id"]]
        target_items = listings[item["target_dir_id"]]
        source_id = str(resolution.source_id)
        expected_source_name = PurePosixPath(item["current_path"]).name
        target_name = PurePosixPath(item["target_path"]).name
        source_identity_indexes = [
            index for index, value in enumerate(source_items)
            if _provider_id(value) == source_id
        ]
        source_name_indexes = [
            index for index, value in enumerate(source_items)
            if _item_name(value) == expected_source_name
        ]
        target_identity_indexes = [
            index for index, value in enumerate(target_items)
            if _provider_id(value) == source_id
        ]
        target_name_indexes = [
            index for index, value in enumerate(target_items)
            if _item_name(value) == target_name
        ]
        source_is_expected = (
            len(source_identity_indexes) == 1
            and len(source_name_indexes) == 1
            and source_identity_indexes == source_name_indexes
        )
        source_is_absent = not source_identity_indexes and not source_name_indexes
        target_is_clear = not target_identity_indexes and not target_name_indexes
        target_is_exact_replay = (
            len(target_identity_indexes) == 1
            and len(target_name_indexes) == 1
            and target_identity_indexes == target_name_indexes
        )

        if source_is_absent and target_is_exact_replay:
            _transition(
                journal,
                resolution,
                "verified",
                item["target_path"],
                replay=True,
                native_move_gate=True,
            )
            outcomes[item["index"]] = _outcome(
                resolution,
                "organized",
                item["target_path"],
                "target_identity_verified",
            )
            continue

        if not source_is_expected:
            reason = "source_identity_changed_before_move"
        elif not target_is_clear:
            reason = "target_conflict_before_move"
        else:
            accepted.append(item)
            continue
        _transition(
            journal,
            resolution,
            "failed",
            item["current_path"],
            reason=reason,
        )
        outcomes[item["index"]] = _outcome(
            resolution,
            "failed",
            item["current_path"],
            reason,
        )
    return accepted, outcomes


def _transaction_collision_reasons(
    *,
    source_keys: Mapping[int, str],
    target_keys: Mapping[int, object],
) -> dict[int, tuple[str, ...]]:
    """Return stable, fail-closed reasons for transaction-wide aliases."""

    source_indexes = defaultdict(list)
    target_indexes = defaultdict(list)
    for index, source_id in source_keys.items():
        if source_id:
            source_indexes[source_id].append(index)
    for index, target_key in target_keys.items():
        if target_key:
            target_indexes[target_key].append(index)
    duplicate_sources = {
        index
        for indexes in source_indexes.values()
        if len(indexes) > 1
        for index in indexes
    }
    duplicate_targets = {
        index
        for indexes in target_indexes.values()
        if len(indexes) > 1
        for index in indexes
    }
    return {
        index: tuple(
            code for code, indexes in (
                ("planned_target_collision", duplicate_targets),
                ("duplicate_source_id_in_transaction", duplicate_sources),
            )
            if index in indexes
        )
        for index in duplicate_sources | duplicate_targets
    }


def _logical_native_collision_reasons(
    resolutions: list[FileResolution],
) -> dict[int, tuple[str, ...]]:
    source_keys = {}
    target_keys = {}
    for index, resolution in enumerate(resolutions):
        if (
            resolution.action not in {"move_only", "rename_and_move"}
            or resolution.status != "resolved"
        ):
            continue
        source_keys[index] = str(resolution.source_id or "").strip()
        target_keys[index] = normalize_storage_path(resolution.target_path)
    return _transaction_collision_reasons(
        source_keys=source_keys,
        target_keys=target_keys,
    )


def _prepared_native_collision_reasons(
    prepared: list[dict],
) -> dict[int, tuple[str, ...]]:
    source_keys = {}
    target_keys = {}
    for item in prepared:
        index = item["index"]
        resolution = item["resolution"]
        source_keys[index] = str(resolution.source_id or "").strip()
        target_name = PurePosixPath(item["target_path"]).name
        target_directory_id = str(item["target_dir_id"] or "").strip()
        if target_directory_id and target_name:
            target_keys[index] = (target_directory_id, target_name)
    return _transaction_collision_reasons(
        source_keys=source_keys,
        target_keys=target_keys,
    )


def _transaction_collision_outcome(
    resolution: FileResolution,
    *,
    observed_path: str,
    reason_codes: tuple[str, ...],
    journal=None,
) -> FileExecutionOutcome:
    _transition(
        journal,
        resolution,
        "failed",
        observed_path,
        reason=reason_codes[0],
        reason_codes=reason_codes,
    )
    return _outcome(
        resolution,
        "failed",
        observed_path,
        *reason_codes,
    )


def _execute_with_native_batches(
    storage,
    resolutions: list[FileResolution],
    *,
    initial_info: dict,
    preflight: FileTransactionSnapshot,
    journal=None,
    move_batch_size: int,
) -> tuple[FileExecutionOutcome, ...]:
    try:
        batch_size = max(1, min(int(move_batch_size), 100))
    except (TypeError, ValueError):
        batch_size = 32
    indexed_outcomes = {}
    logical_collision_reasons = _logical_native_collision_reasons(resolutions)
    prepared_items = []
    directory_info = {}
    for index, resolution in enumerate(resolutions):
        if index in logical_collision_reasons:
            indexed_outcomes[index] = _transaction_collision_outcome(
                resolution,
                observed_path=resolution.source_path,
                reason_codes=logical_collision_reasons[index],
                journal=journal,
            )
            continue
        if (
            resolution.action not in {"move_only", "rename_and_move"}
            or resolution.status != "resolved"
        ):
            source_parent_id = None
            if (
                resolution.action != "keep_original"
                and resolution.status == "resolved"
            ):
                source_parent_id = preflight.require_source_parent_id(
                    str(PurePosixPath(resolution.source_path).parent)
                )
            indexed_outcomes[index] = _execute_one(
                storage,
                resolution,
                journal,
                initial_info=initial_info,
                source_parent_id=source_parent_id,
            )
            continue
        source_parent = str(PurePosixPath(resolution.source_path).parent)
        source_parent_id = preflight.require_source_parent_id(source_parent)
        prepared = _prepare_native_move(
            storage,
            resolution,
            initial_info=initial_info,
            directory_info=directory_info,
            source_parent_id=source_parent_id,
            journal=journal,
        )
        if isinstance(prepared, FileExecutionOutcome):
            indexed_outcomes[index] = prepared
            continue
        prepared["index"] = index
        prepared_items.append(prepared)

    prepared_by_target = defaultdict(list)
    prepared_collision_reasons = _prepared_native_collision_reasons(
        prepared_items
    )
    for prepared in prepared_items:
        index = prepared["index"]
        if index in prepared_collision_reasons:
            indexed_outcomes[index] = _transaction_collision_outcome(
                prepared["resolution"],
                observed_path=prepared["current_path"],
                reason_codes=prepared_collision_reasons[index],
                journal=journal,
            )
            continue
        prepared_by_target[prepared["target_dir"]].append(prepared)

    native_unavailable = False
    for target_dir in sorted(prepared_by_target):
        group = prepared_by_target[target_dir]
        for offset in range(0, len(group), batch_size):
            chunk = group[offset:offset + batch_size]
            if native_unavailable:
                for item in chunk:
                    indexed_outcomes[item["index"]] = _legacy_move_prepared(
                        storage, item, journal
                    )
                continue
            chunk, gate_outcomes = _gate_native_move_chunk(
                storage,
                chunk,
                journal,
            )
            indexed_outcomes.update(gate_outcomes)
            if not chunk:
                continue
            for item in chunk:
                _transition(
                    journal,
                    item["resolution"],
                    "before_move",
                    item["current_path"],
                    native_batch=True,
                )
            try:
                storage.move_files_by_id(
                    [item["resolution"].source_id for item in chunk],
                    chunk[0]["target_dir_id"],
                )
            except Exception as exc:
                code = str(getattr(exc, "code", "") or "")
                if isinstance(exc, (AttributeError, NotImplementedError)) or code in {
                    "method_not_allowed", "not_found", "unimplemented",
                }:
                    native_unavailable = True
                    for item in chunk:
                        indexed_outcomes[item["index"]] = _legacy_move_prepared(
                            storage, item, journal
                        )
                    continue
            reconciled = _reconcile_native_chunk(storage, chunk, journal)
            for item, outcome in zip(chunk, reconciled):
                indexed_outcomes[item["index"]] = outcome
    return tuple(indexed_outcomes[index] for index in range(len(resolutions)))


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
    expected_retained = set()
    for resolution in resolutions or []:
        path = PurePosixPath(
            normalize_storage_path(resolution.source_path)
        ).parent
        while path != root and root in path.parents:
            candidates.add(str(path))
            if resolution.action == "keep_original":
                expected_retained.add(str(path))
            path = path.parent
        if include_selected_root and path == root and root_value not in {"", "/"}:
            candidates.add(root_value)
            if resolution.action == "keep_original":
                expected_retained.add(root_value)

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
                state=(
                    "retained_unresolved"
                    if path in expected_retained
                    else "retained_nonempty"
                ),
                reason_code=(
                    "unresolved_files_retained"
                    if path in expected_retained
                    else "directory_not_empty"
                ),
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
            item.state in {
                "retained_nonempty",
                "retained_unresolved",
                "protected",
            }
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
