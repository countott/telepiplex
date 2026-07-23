"""Request-scoped media facts and exact candidate clustering."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


_COMPLEX_PATTERN = re.compile(
    r"(?i)\b(?:ova|special|spin[ -]?off|prequel|sequel)\b|"
    r"前传|前傳|续集|續集|特别篇|特別篇|番外|衍生|电影版|電影版|剧场版|劇場版"
)


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def normalize_title(value) -> str:
    value = unicodedata.normalize("NFKC", _text(value)).casefold()
    value = re.sub(r"(?<!\d)(?:19\d{2}|20\d{2})(?!\d)", " ", value)
    value = re.sub(
        r"[\(（]\s*(?:电影|電影|film|movie|电视剧|電視劇|series)\s*[\)）]$",
        "",
        value,
    )
    return "".join(character for character in value if character.isalnum())


def _unique_text(values) -> tuple[str, ...]:
    result = []
    for value in values:
        value = _text(value)
        if value and value not in result:
            result.append(value)
    return tuple(result)


def _mapping(values: dict | None) -> Mapping[str, str]:
    normalized = {
        _text(key): _text(value)
        for key, value in (values or {}).items()
        if _text(key) and _text(value)
    }
    return MappingProxyType(normalized)


@dataclass(frozen=True)
class EvidenceFact:
    fact_id: str
    provider: str
    titles: tuple[str, ...]
    year: str
    media_type: str
    external_ids: Mapping[str, str]
    source_url: str = ""
    poster_url: str = ""
    original_title: str = ""
    original_language: str = ""
    official_english_title: str = ""
    romanized_original_title: str = ""
    genres: tuple[str, ...] = ()
    episodes: tuple[dict, ...] = ()
    complex_signals: tuple[str, ...] = ()

    @property
    def normalized_titles(self) -> frozenset[str]:
        return frozenset(
            normalized
            for title in self.titles
            if (normalized := normalize_title(title))
        )


@dataclass(frozen=True)
class CandidateEntity:
    candidate_key: str
    facts: tuple[EvidenceFact, ...]

    @property
    def providers(self) -> frozenset[str]:
        return frozenset(fact.provider for fact in self.facts)

    @property
    def titles(self) -> tuple[str, ...]:
        return _unique_text(
            title for fact in self.facts for title in fact.titles
        )

    @property
    def normalized_titles(self) -> frozenset[str]:
        return frozenset(
            title
            for fact in self.facts
            for title in fact.normalized_titles
        )

    @property
    def years(self) -> frozenset[str]:
        return frozenset(fact.year for fact in self.facts if fact.year)

    @property
    def media_types(self) -> frozenset[str]:
        return frozenset(
            fact.media_type for fact in self.facts if fact.media_type
        )

    @property
    def external_ids(self) -> Mapping[str, str]:
        merged = {}
        for fact in self.facts:
            merged.update(fact.external_ids)
        return MappingProxyType(merged)

    @property
    def poster_url(self) -> str:
        for provider in ("tvdb", "douban", "wikipedia"):
            for fact in self.facts:
                if fact.provider == provider and fact.poster_url:
                    return fact.poster_url
        return ""

    @property
    def complex_signals(self) -> frozenset[str]:
        return frozenset(
            signal
            for fact in self.facts
            for signal in fact.complex_signals
        )


@dataclass(frozen=True)
class SearchGraph:
    candidates: tuple[CandidateEntity, ...]


def _fact_id(provider: str, raw: dict, index: int, media_type: str = "") -> str:
    identifiers = raw.get("external_ids") if isinstance(raw.get("external_ids"), dict) else {}
    value = (
        raw.get(f"tvdb_{media_type}_id")
        or raw.get("tvdb_id")
        or raw.get("subject_id")
        or raw.get("wikibase_item")
        or next((item for item in identifiers.values() if _text(item)), "")
        or index
    )
    return f"{provider}:{_text(value)}"


def _fact(
    provider: str,
    raw: dict,
    index: int,
    *,
    media_type: str = "",
    episodes: list[dict] | None = None,
) -> EvidenceFact:
    resolved_type = _text(media_type or raw.get("media_type")).casefold()
    if resolved_type == "movies":
        resolved_type = "movie"
    external_ids = dict(raw.get("external_ids") or {})
    if provider == "douban" and _text(raw.get("subject_id")):
        external_ids["douban_subject"] = _text(raw.get("subject_id"))
    if provider == "wikipedia" and _text(raw.get("wikibase_item")):
        external_ids["wikipedia"] = _text(raw.get("wikibase_item"))
    if provider == "tvdb":
        tvdb_id = _text(
            raw.get(f"tvdb_{resolved_type}_id")
            or raw.get("tvdb_id")
            or raw.get("id")
        )
        if tvdb_id:
            external_ids["tvdb"] = tvdb_id
    titles = _unique_text((
        raw.get("title"),
        raw.get("name"),
        raw.get("chinese_title"),
        raw.get("english_title"),
        raw.get("original_title"),
        raw.get("official_english_title"),
        raw.get("romanized_original_title"),
        *(raw.get("aliases") or []),
    ))
    complex_text = " ".join((
        *titles,
        _text(raw.get("extract")),
        _text(raw.get("overview")),
    ))
    signals = list(raw.get("complex_signals") or [])
    if _COMPLEX_PATTERN.search(complex_text):
        signals.append("provider_relation_signal")
    return EvidenceFact(
        fact_id=_fact_id(provider, raw, index, resolved_type),
        provider=provider,
        titles=titles,
        year=_text(raw.get("year"))[:4],
        media_type=resolved_type,
        external_ids=_mapping(external_ids),
        source_url=_text(raw.get("url")),
        poster_url=_text(raw.get("cover_url") or raw.get("poster_url")),
        original_title=_text(raw.get("original_title")),
        original_language=_text(raw.get("original_language")).casefold(),
        official_english_title=_text(
            raw.get("official_english_title") or raw.get("english_title")
        ),
        romanized_original_title=_text(raw.get("romanized_original_title")),
        genres=_unique_text(raw.get("genres") or []),
        episodes=tuple(dict(item) for item in (episodes or []) if isinstance(item, dict)),
        complex_signals=_unique_text(signals),
    )


def _facts_from_source(source: dict) -> list[EvidenceFact]:
    if not isinstance(source, dict) or source.get("status") != "ok":
        return []
    provider = _text(source.get("source")).casefold()
    result = []
    for index, raw in enumerate(source.get("facts") or []):
        if not isinstance(raw, dict):
            continue
        if provider != "tvdb":
            result.append(_fact(provider, raw, index))
            continue
        episodes_by_series = raw.get("episodes_by_series") or {}
        for media_type, key in (("movie", "movies"), ("series", "series")):
            for nested_index, entry in enumerate(raw.get(key) or []):
                if not isinstance(entry, dict):
                    continue
                entity_id = _text(
                    entry.get(f"tvdb_{media_type}_id")
                    or entry.get("tvdb_id")
                    or entry.get("id")
                )
                result.append(_fact(
                    provider,
                    entry,
                    index * 1000 + nested_index,
                    media_type=media_type,
                    episodes=(
                        episodes_by_series.get(entity_id) or []
                        if media_type == "series"
                        else []
                    ),
                ))
    return result


def _stable_id_match(left: EvidenceFact, right: EvidenceFact) -> bool:
    return any(
        key in right.external_ids
        and value
        and value == right.external_ids[key]
        for key, value in left.external_ids.items()
    )


def _title_year_type_match(left: EvidenceFact, right: EvidenceFact) -> bool:
    return bool(
        left.normalized_titles.intersection(right.normalized_titles)
        and left.year
        and left.year == right.year
        and left.media_type
        and left.media_type == right.media_type
    )


def _matches_candidate(candidate: list[EvidenceFact], fact: EvidenceFact) -> bool:
    return any(
        _stable_id_match(existing, fact)
        or _title_year_type_match(existing, fact)
        for existing in candidate
    )


def _candidate_key(facts: list[EvidenceFact]) -> str:
    for provider, key in (
        ("tvdb", "tvdb"),
        ("douban", "douban_subject"),
        ("wikipedia", "wikipedia"),
    ):
        for fact in facts:
            if fact.provider == provider and _text(fact.external_ids.get(key)):
                media_type = fact.media_type or "media"
                return f"{provider}:{media_type}:{fact.external_ids[key]}"
    first = facts[0]
    title = min(first.normalized_titles, key=len, default="unknown")
    return f"title:{title}:{first.year}:{first.media_type or 'media'}"


def build_search_graph(sources: list[dict]) -> SearchGraph:
    clusters: list[list[EvidenceFact]] = []
    for source in sources or []:
        for fact in _facts_from_source(source):
            matches = [cluster for cluster in clusters if _matches_candidate(cluster, fact)]
            if not matches:
                clusters.append([fact])
                continue
            primary = matches[0]
            primary.append(fact)
            for extra in matches[1:]:
                primary.extend(extra)
                clusters.remove(extra)
    candidates = [
        CandidateEntity(_candidate_key(cluster), tuple(cluster))
        for cluster in clusters
        if cluster
    ]
    candidates.sort(key=lambda item: item.candidate_key)
    return SearchGraph(tuple(candidates))


def merge_verified_equivalence_edges(
    graph: SearchGraph,
    edges,
) -> SearchGraph:
    """Merge candidate components connected by already verified fact edges."""

    candidates = list((graph or SearchGraph(())).candidates)
    if not candidates or not edges:
        return SearchGraph(tuple(candidates))
    parent = list(range(len(candidates)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    by_fact = {
        fact.fact_id: index
        for index, candidate in enumerate(candidates)
        for fact in candidate.facts
    }
    for edge in edges:
        if isinstance(edge, dict):
            left_id = _text(edge.get("left_fact_id"))
            right_id = _text(edge.get("right_fact_id"))
        else:
            left_id = _text(getattr(edge, "left_fact_id", ""))
            right_id = _text(getattr(edge, "right_fact_id", ""))
        left = by_fact.get(left_id)
        right = by_fact.get(right_id)
        if left is not None and right is not None:
            union(left, right)

    components: dict[int, list[EvidenceFact]] = {}
    for index, candidate in enumerate(candidates):
        facts = components.setdefault(find(index), [])
        known = {fact.fact_id for fact in facts}
        facts.extend(
            fact for fact in candidate.facts if fact.fact_id not in known
        )
    merged = [
        CandidateEntity(_candidate_key(facts), tuple(facts))
        for facts in components.values()
        if facts
    ]
    merged.sort(key=lambda item: item.candidate_key)
    return SearchGraph(tuple(merged))
