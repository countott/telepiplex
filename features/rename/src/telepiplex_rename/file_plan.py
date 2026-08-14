"""Pure per-file resolution planning and target preflight."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

from .file_facts import FileFact, ParsedFileEvidence


@dataclass(frozen=True)
class FileResolution:
    source_id: str
    source_path: str
    status: str
    work_identity: dict
    item_identity: dict
    target_path: str
    action: str
    reason_codes: tuple[str, ...]

    @property
    def target_dir(self) -> str:
        return str(PurePosixPath(self.target_path).parent) if self.target_path else ""

    @property
    def target_name(self) -> str:
        return PurePosixPath(self.target_path).name if self.target_path else ""


def normalize_storage_path(value: str) -> str:
    value = str(value or "").strip()
    return str(PurePosixPath(value)) if value else ""


def _item_identity(evidence: ParsedFileEvidence) -> dict:
    return {
        "season_number": evidence.season_number,
        "episode_number": evidence.episode_number,
        "absolute_episode": evidence.absolute_episode,
        "content_role": evidence.content_role,
        "subtitle_language": evidence.subtitle_language,
        "subtitle_variant": evidence.subtitle_variant,
    }


def _resolved_action(source_path: str, target_path: str) -> str:
    source = PurePosixPath(normalize_storage_path(source_path))
    target = PurePosixPath(normalize_storage_path(target_path))
    if source == target:
        return "no_op"
    if source.parent == target.parent:
        return "rename_only"
    if source.name == target.name:
        return "move_only"
    return "rename_and_move"


def plan_file_resolutions(
    facts: list[FileFact],
    evidence_by_source: dict[str, ParsedFileEvidence],
    target_by_source: dict[str, str],
    work_identity_by_source: dict[str, dict],
    *,
    existing_targets: dict[str, dict] | None = None,
) -> list[FileResolution]:
    """Return one immutable decision for every fact in the snapshot."""

    normalized_targets = {
        source_id: normalize_storage_path(target)
        for source_id, target in (target_by_source or {}).items()
        if normalize_storage_path(target)
    }
    target_counts = Counter(normalized_targets.values())
    existing = {
        normalize_storage_path(path): info
        for path, info in (existing_targets or {}).items()
        if normalize_storage_path(path) and isinstance(info, dict)
    }
    resolutions = []
    for fact in facts or []:
        evidence = evidence_by_source.get(fact.source_id)
        if evidence is None:
            evidence = ParsedFileEvidence(
                source_id=fact.source_id,
                title_candidates=(),
                title_key="",
                year_hint=None,
                season_number=None,
                episode_number=None,
                absolute_episode=None,
                content_role="unknown",
                subtitle_language="unknown",
                subtitle_variant="unknown",
                confidence="low",
                evidence=(),
                directory_hints=(),
            )
        work_identity = work_identity_by_source.get(fact.source_id)
        target_path = normalized_targets.get(fact.source_id, "")
        if fact.media_kind not in {"video", "subtitle"}:
            resolutions.append(FileResolution(
                source_id=fact.source_id,
                source_path=normalize_storage_path(fact.absolute_path),
                status="unsupported",
                work_identity={},
                item_identity=_item_identity(evidence),
                target_path="",
                action="keep_original",
                reason_codes=("non_media",),
            ))
            continue

        missing_reasons = []
        if not isinstance(work_identity, dict) or not work_identity:
            missing_reasons.append("work_identity_unresolved")
        if not target_path:
            missing_reasons.append("target_unresolved")
        if missing_reasons:
            resolutions.append(FileResolution(
                source_id=fact.source_id,
                source_path=normalize_storage_path(fact.absolute_path),
                status="ambiguous",
                work_identity=dict(work_identity or {}),
                item_identity=_item_identity(evidence),
                target_path="",
                action="keep_original",
                reason_codes=tuple(missing_reasons),
            ))
            continue

        if target_counts[target_path] > 1:
            resolutions.append(FileResolution(
                source_id=fact.source_id,
                source_path=normalize_storage_path(fact.absolute_path),
                status="ambiguous",
                work_identity=dict(work_identity),
                item_identity=_item_identity(evidence),
                target_path=target_path,
                action="keep_original",
                reason_codes=("planned_target_collision",),
            ))
            continue

        target_info = existing.get(target_path)
        if target_info:
            target_id = str(
                target_info.get("source_id")
                or target_info.get("file_id")
                or target_info.get("fid")
                or ""
            ).strip()
            if target_id and target_id == fact.source_id:
                resolutions.append(FileResolution(
                    source_id=fact.source_id,
                    source_path=normalize_storage_path(fact.absolute_path),
                    status="resolved",
                    work_identity=dict(work_identity),
                    item_identity=_item_identity(evidence),
                    target_path=target_path,
                    action="no_op",
                    reason_codes=("target_same_provider_identity",),
                ))
                continue
            reasons = ["target_conflict"]
            target_sha1 = str(
                target_info.get("sha1") or target_info.get("sha") or ""
            ).strip().lower()
            if fact.sha1 and target_sha1 and fact.sha1 == target_sha1:
                reasons.append("duplicate_hash_distinct_identity")
            resolutions.append(FileResolution(
                source_id=fact.source_id,
                source_path=normalize_storage_path(fact.absolute_path),
                status="ambiguous",
                work_identity=dict(work_identity),
                item_identity=_item_identity(evidence),
                target_path=target_path,
                action="keep_original",
                reason_codes=tuple(reasons),
            ))
            continue

        action = _resolved_action(fact.absolute_path, target_path)
        reasons = ("source_equals_target",) if action == "no_op" else ()
        resolutions.append(FileResolution(
            source_id=fact.source_id,
            source_path=normalize_storage_path(fact.absolute_path),
            status="resolved",
            work_identity=dict(work_identity),
            item_identity=_item_identity(evidence),
            target_path=target_path,
            action=action,
            reason_codes=reasons,
        ))
    return resolutions
