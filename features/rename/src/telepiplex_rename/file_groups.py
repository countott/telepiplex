"""Derive work groups from file evidence rather than source directories."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib

from .file_facts import ParsedFileEvidence


@dataclass(frozen=True)
class ProvisionalWorkGroup:
    group_id: str
    title_key: str
    year_hints: tuple[int, ...]
    source_ids: tuple[str, ...]
    query_candidates: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class VerifiedWorkGroup:
    external_identity: str
    group_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    metadata: dict


def _group_id(
    title_key: str,
    year_hints: tuple[int, ...],
    source_ids: tuple[str, ...],
    status: str,
) -> str:
    value = "\0".join((
        "file-first-v1",
        title_key,
        ",".join(str(year) for year in year_hints),
        ",".join(source_ids),
        status,
    ))
    return "group:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _query_candidates(items: list[ParsedFileEvidence]) -> tuple[str, ...]:
    candidates = {
        candidate.strip()
        for item in items
        for candidate in item.title_candidates
        if candidate.strip()
    }
    return tuple(sorted(candidates, key=lambda value: (value.casefold(), value)))


def _make_group(
    title_key: str,
    items: list[ParsedFileEvidence],
    *,
    status: str,
) -> ProvisionalWorkGroup:
    source_ids = tuple(sorted(item.source_id for item in items))
    year_hints = tuple(sorted({
        item.year_hint for item in items if item.year_hint is not None
    }))
    return ProvisionalWorkGroup(
        group_id=_group_id(title_key, year_hints, source_ids, status),
        title_key=title_key,
        year_hints=year_hints,
        source_ids=source_ids,
        query_candidates=_query_candidates(items),
        status=status,
    )


def build_provisional_groups(
    evidence: list[ParsedFileEvidence],
) -> list[ProvisionalWorkGroup]:
    """Group compatible filename identities without using parent folders."""

    by_title: dict[str, list[ParsedFileEvidence]] = defaultdict(list)
    unresolved = []
    for item in evidence or []:
        if not item.title_key:
            unresolved.append(item)
        else:
            by_title[item.title_key].append(item)

    groups = [
        _make_group("", [item], status="unresolved_title")
        for item in sorted(unresolved, key=lambda value: value.source_id)
    ]
    for title_key in sorted(by_title):
        items = by_title[title_key]
        explicit_years = sorted({
            item.year_hint for item in items if item.year_hint is not None
        })
        if len(explicit_years) <= 1:
            groups.append(_make_group(title_key, items, status="ready"))
            continue

        for year in explicit_years:
            year_items = [item for item in items if item.year_hint == year]
            groups.append(_make_group(title_key, year_items, status="ready"))
        for item in sorted(
            (item for item in items if item.year_hint is None),
            key=lambda value: value.source_id,
        ):
            groups.append(_make_group(
                title_key,
                [item],
                status="ambiguous_year",
            ))
    return groups


def _external_identity(metadata: dict) -> str:
    source = str(
        metadata.get("provider") or metadata.get("source") or "metadata"
    ).strip().casefold()
    external_id = str(
        metadata.get("external_id")
        or metadata.get("tvdb_id")
        or metadata.get("tmdb_id")
        or metadata.get("imdb_id")
        or ""
    ).strip()
    return f"{source}:{external_id}" if external_id else ""


def build_verified_groups(
    groups: list[ProvisionalWorkGroup],
    confirmed_by_group: dict[str, dict],
) -> list[VerifiedWorkGroup]:
    """Merge aliases only after metadata confirms one external identity."""

    verified: dict[str, list[tuple[ProvisionalWorkGroup, dict]]] = defaultdict(list)
    for group in groups or []:
        if group.status != "ready":
            continue
        metadata = confirmed_by_group.get(group.group_id)
        if not isinstance(metadata, dict):
            continue
        identity = _external_identity(metadata)
        if identity:
            verified[identity].append((group, metadata))

    result = []
    for identity in sorted(verified):
        members = sorted(verified[identity], key=lambda item: item[0].group_id)
        result.append(VerifiedWorkGroup(
            external_identity=identity,
            group_ids=tuple(item[0].group_id for item in members),
            source_ids=tuple(sorted({
                source_id
                for group, _metadata in members
                for source_id in group.source_ids
            })),
            metadata=dict(members[0][1]),
        ))
    return result
