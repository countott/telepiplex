"""Request-scoped media facts and exact candidate clustering."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field, replace
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


def normalize_language(value) -> str:
    value = _text(value).casefold().replace("_", "-")
    primary = value.split("-", 1)[0]
    return {
        "eng": "en",
        "jpn": "ja",
        "kor": "ko",
        "zho": "zh",
        "chi": "zh",
        "cmn": "zh",
        "und": "",
        "zxx": "",
    }.get(primary, primary)


def _optional_integer(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _unique_records(values) -> tuple[dict, ...]:
    records = {}
    for value in values or ():
        if not isinstance(value, dict):
            continue
        record = {
            _text(key): item
            for key, item in value.items()
            if _text(key) and item not in (None, "", [], {})
        }
        if not record:
            continue
        key = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        records[key] = record
    return tuple(records[key] for key in sorted(records))


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
    summary: str = ""
    original_title: str = ""
    original_language: str = ""
    official_english_title: str = ""
    romanized_original_title: str = ""
    chinese_title: str = ""
    poster_language: str = ""
    genres: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    episodes: tuple[dict, ...] = ()
    original_release_date: str = ""
    runtime_minutes: int | None = None
    status: str = ""
    release_format: str = ""
    relations: tuple[dict, ...] = ()
    studios: tuple[str, ...] = ()
    networks: tuple[str, ...] = ()
    cast: tuple[dict, ...] = ()
    crew: tuple[dict, ...] = ()
    certifications: tuple[str, ...] = ()
    backdrop_urls: tuple[str, ...] = ()
    season_count: int | None = None
    episode_count: int | None = None
    episode_inventory: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )
    douban_title_raw: str = ""
    source_season_number: int | None = None
    complex_signals: tuple[str, ...] = ()
    stable_fact_id: str = ""

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
        languages = sorted({
            fact.original_language
            for fact in self.facts
            if fact.original_language
        })
        original_language = languages[0] if len(languages) == 1 else ""
        if original_language:
            matching = sorted({
                fact.poster_url
                for fact in self.facts
                if fact.poster_url
                and fact.poster_language == original_language
            })
            if matching:
                return matching[0]
        untagged = sorted({
            fact.poster_url
            for fact in self.facts
            if fact.poster_url and not fact.poster_language
        })
        if untagged:
            return untagged[0]
        posters = sorted({
            fact.poster_url for fact in self.facts if fact.poster_url
        })
        return posters[0] if posters else ""

    @property
    def summary(self) -> str:
        summaries = {fact.summary for fact in self.facts if fact.summary}
        return max(summaries, key=lambda value: (len(value), value)) if summaries else ""

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
    fact_merges: tuple["FactMergeDiagnostic", ...] = ()


@dataclass(frozen=True)
class FactMergeDiagnostic:
    provider: str
    fact_id: str
    occurrences: int
    conflicting_fields: tuple[str, ...] = ()


class EvidenceFactConflict(ValueError):
    def __init__(
        self,
        fact_id: str,
        provider: str,
        conflicting_fields,
    ):
        self.fact_id = _text(fact_id)
        self.provider = _text(provider).casefold()
        self.conflicting_fields = tuple(sorted({
            _text(field) for field in conflicting_fields if _text(field)
        }))
        super().__init__(
            f"{self.fact_id}:"
            f"{','.join(self.conflicting_fields) or 'identity'}"
        )


def _provider_stable_id(
    provider: str,
    raw: dict,
    media_type: str = "",
) -> str:
    identifiers = (
        raw.get("external_ids")
        if isinstance(raw.get("external_ids"), dict)
        else {}
    )
    if provider == "tvdb":
        return _text(
            raw.get(f"tvdb_{media_type}_id")
            or raw.get("tvdb_id")
            or raw.get("id")
            or identifiers.get("tvdb")
        )
    if provider == "douban":
        return _text(
            raw.get("subject_id")
            or identifiers.get("douban_subject")
            or identifiers.get("douban")
        )
    if provider == "wikipedia":
        return _text(
            raw.get("wikibase_item")
            or identifiers.get("wikipedia")
            or identifiers.get("wikibase_item")
        )
    if provider == "wikidata":
        return _text(
            raw.get("wikibase_item")
            or identifiers.get("wikidata")
        )
    if provider == "tmdb":
        return _text(raw.get("tmdb_id") or raw.get("id") or identifiers.get("tmdb"))
    if provider == "anilist":
        return _text(raw.get("anilist_id") or raw.get("id") or identifiers.get("anilist"))
    return ""


def _request_fact_id(provider: str, raw: dict, media_type: str) -> str:
    serialized = json.dumps(
        {
            "provider": provider,
            "media_type": media_type,
            "raw": raw,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return f"request:{digest}"


def _fact_id(provider: str, raw: dict, index: int, media_type: str = "") -> str:
    del index
    normalized_provider = _text(provider).casefold()
    normalized_type = _text(media_type).casefold()
    value = (
        _provider_stable_id(normalized_provider, raw, normalized_type)
        or _request_fact_id(normalized_provider, raw, normalized_type)
    )
    if normalized_provider == "tvdb" and normalized_type:
        return f"{normalized_provider}:{normalized_type}:{value}"
    return f"{normalized_provider}:{value}"


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
    provider_stable_id = _provider_stable_id(
        provider,
        raw,
        resolved_type,
    )
    if provider == "douban" and provider_stable_id:
        external_ids["douban_subject"] = provider_stable_id
    if provider == "wikipedia" and provider_stable_id:
        external_ids["wikipedia"] = provider_stable_id
    if provider == "wikidata" and provider_stable_id:
        external_ids["wikidata"] = provider_stable_id
    if provider == "tvdb":
        if provider_stable_id:
            external_ids["tvdb"] = provider_stable_id
    if provider in {"tmdb", "anilist"} and provider_stable_id:
        external_ids[provider] = provider_stable_id
    source_url = _text(raw.get("url"))
    if (
        provider == "tvdb"
        and not source_url
        and _text(external_ids.get("tvdb"))
        and resolved_type in {"movie", "series"}
    ):
        source_url = (
            "https://thetvdb.com/"
            f"{'movies' if resolved_type == 'movie' else 'series'}/"
            f"{external_ids['tvdb']}"
        )
    if provider == "tmdb" and not source_url and provider_stable_id:
        source_url = (
            "https://www.themoviedb.org/"
            f"{'movie' if resolved_type == 'movie' else 'tv'}/{provider_stable_id}"
        )
    if provider == "anilist" and not source_url and provider_stable_id:
        source_url = f"https://anilist.co/anime/{provider_stable_id}"
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
    fact_id = _fact_id(provider, raw, index, resolved_type)
    return EvidenceFact(
        fact_id=fact_id,
        provider=provider,
        titles=titles,
        year=_text(raw.get("year"))[:4],
        media_type=resolved_type,
        external_ids=_mapping(external_ids),
        source_url=source_url,
        poster_url=_text(raw.get("cover_url") or raw.get("poster_url")),
        summary=_text(
            raw.get("overview")
            or raw.get("summary")
            or raw.get("extract")
            or raw.get("intro")
            or raw.get("description")
        ),
        original_title=_text(raw.get("original_title")),
        original_language=normalize_language(raw.get("original_language")),
        official_english_title=_text(
            raw.get("official_english_title") or raw.get("english_title")
        ),
        romanized_original_title=_text(raw.get("romanized_original_title")),
        chinese_title=_text(raw.get("chinese_title")),
        poster_language=normalize_language(raw.get("poster_language")),
        genres=_unique_text(raw.get("genres") or []),
        countries=_unique_text(raw.get("countries") or []),
        episodes=tuple(dict(item) for item in (episodes or []) if isinstance(item, dict)),
        original_release_date=_text(raw.get("original_release_date") or raw.get("release_date")),
        runtime_minutes=_optional_integer(raw.get("runtime_minutes")),
        status=_text(raw.get("status")),
        release_format=_text(raw.get("release_format")),
        relations=_unique_records(raw.get("relations") or []),
        studios=_unique_text(raw.get("studios") or []),
        networks=_unique_text(raw.get("networks") or []),
        cast=_unique_records(raw.get("cast") or []),
        crew=_unique_records(raw.get("crew") or []),
        certifications=_unique_text(raw.get("certifications") or []),
        backdrop_urls=_unique_text(raw.get("backdrop_urls") or []),
        season_count=_optional_integer(raw.get("season_count")),
        episode_count=_optional_integer(raw.get("episode_count")),
        episode_inventory=MappingProxyType(dict(
            raw.get("wikipedia_episode_inventory")
            or raw.get("episode_inventory")
            or {}
        )),
        douban_title_raw=_text(raw.get("douban_title_raw")),
        source_season_number=_optional_integer(raw.get("season_number")),
        complex_signals=_unique_text(signals),
        stable_fact_id=fact_id,
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
            result.append(_fact(
                provider,
                raw,
                index,
                episodes=(
                    raw.get("episodes")
                    if isinstance(raw.get("episodes"), list)
                    else None
                ),
            ))
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
    if (
        left.media_type
        and right.media_type
        and left.media_type != right.media_type
    ):
        return False
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
    candidate_types = {
        existing.media_type
        for existing in candidate
        if existing.media_type
    }
    if (
        fact.media_type
        and candidate_types
        and fact.media_type not in candidate_types
    ):
        return False
    return any(
        _stable_id_match(existing, fact)
        or _title_year_type_match(existing, fact)
        for existing in candidate
    )


def _sorted_unique_text(values) -> tuple[str, ...]:
    unique = {_text(value) for value in values if _text(value)}
    return tuple(sorted(unique, key=lambda value: (value.casefold(), value)))


def _preferred_text(values, *, shortest: bool = False) -> str:
    unique = _sorted_unique_text(values)
    if not unique:
        return ""
    if shortest:
        return min(unique, key=lambda value: (len(value), value.casefold(), value))
    return max(unique, key=lambda value: (len(value), value.casefold(), value))


def _preferred_integer(values) -> int | None:
    normalized = sorted({
        parsed
        for value in values
        if (parsed := _optional_integer(value)) is not None
    })
    return normalized[-1] if normalized else None


def _single_identity_value(
    facts: list[EvidenceFact],
    field: str,
) -> str:
    values = _sorted_unique_text(getattr(fact, field) for fact in facts)
    if len(values) > 1:
        raise EvidenceFactConflict(
            facts[0].fact_id,
            facts[0].provider,
            (field,),
        )
    return values[0] if values else ""


def _merged_external_ids(
    facts: list[EvidenceFact],
) -> Mapping[str, str]:
    values_by_key = {}
    for fact in facts:
        for key, value in fact.external_ids.items():
            key = _text(key)
            value = _text(value)
            if key and value:
                values_by_key.setdefault(key, set()).add(value)
    conflicts = [
        f"external_ids.{key}"
        for key, values in values_by_key.items()
        if len(values) > 1
    ]
    if conflicts:
        raise EvidenceFactConflict(
            facts[0].fact_id,
            facts[0].provider,
            conflicts,
        )
    return _mapping({
        key: next(iter(values))
        for key, values in sorted(values_by_key.items())
    })


def _episode_ids(item: dict) -> tuple[str, ...]:
    return _sorted_unique_text((
        item.get("tvdb_episode_id"),
        item.get("id"),
        item.get("episode_id"),
    ))


def _episode_coordinate(value) -> str:
    if value is None or value == "":
        return ""
    text = _text(value)
    try:
        return str(int(text))
    except (TypeError, ValueError):
        return text


def _episode_number_key(item: dict) -> tuple[str, str] | None:
    season = _episode_coordinate(item.get("season_number"))
    episode = _episode_coordinate(item.get("episode_number"))
    return (season, episode) if season and episode else None


def _episode_identity(item: dict) -> tuple:
    episode_ids = _episode_ids(item)
    if episode_ids:
        return ("id", episode_ids[0])
    if number_key := _episode_number_key(item):
        return ("number", *number_key)
    return (
        "payload",
        json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
    )


def _episode_value_present(value) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _merge_episode_group(
    items: list[dict],
    *,
    fact: EvidenceFact,
) -> dict:
    episode_ids = {
        episode_id
        for item in items
        for episode_id in _episode_ids(item)
    }
    identity_label = min(episode_ids) if episode_ids else "numbered"
    conflicts = []
    if len(episode_ids) > 1:
        conflicts.append(f"episodes.{identity_label}.episode_id")
    for key in ("season_number", "episode_number"):
        values = {
            _episode_coordinate(item.get(key))
            for item in items
            if _episode_coordinate(item.get(key))
        }
        if len(values) > 1:
            conflicts.append(f"episodes.{identity_label}.{key}")
    if conflicts:
        raise EvidenceFactConflict(
            fact.fact_id,
            fact.provider,
            conflicts,
        )

    ranked = sorted(
        (dict(item) for item in items),
        key=lambda item: (
            -sum(
                _episode_value_present(value)
                for value in item.values()
            ),
            json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
        ),
    )
    merged = dict(ranked[0])
    for item in ranked[1:]:
        for key in sorted(item):
            if (
                _episode_value_present(item[key])
                and not _episode_value_present(merged.get(key))
            ):
                merged[key] = item[key]
    return merged


def _merged_episodes(facts: list[EvidenceFact]) -> tuple[dict, ...]:
    id_groups: dict[str, list[dict]] = {}
    idless = []
    for fact in facts:
        for episode in fact.episodes:
            item = dict(episode)
            episode_ids = _episode_ids(item)
            if episode_ids:
                id_groups.setdefault(episode_ids[0], []).append(item)
            else:
                idless.append(item)

    ids_by_number: dict[tuple[str, str], set[str]] = {}
    for episode_id, items in id_groups.items():
        for item in items:
            if number_key := _episode_number_key(item):
                ids_by_number.setdefault(number_key, set()).add(episode_id)

    other_groups: dict[tuple, list[dict]] = {}
    for item in idless:
        matching_ids = ids_by_number.get(_episode_number_key(item), set())
        if len(matching_ids) == 1:
            id_groups[next(iter(matching_ids))].append(item)
        else:
            other_groups.setdefault(_episode_identity(item), []).append(item)

    groups = {
        ("id", episode_id): items
        for episode_id, items in id_groups.items()
    }
    groups.update(other_groups)
    return tuple(
        _merge_episode_group(groups[key], fact=facts[0])
        for key in sorted(groups, key=lambda value: tuple(map(str, value)))
    )


def _poster_fields(facts: list[EvidenceFact], original_language: str) -> tuple[str, str]:
    posters = sorted({
        (fact.poster_url, fact.poster_language)
        for fact in facts
        if fact.poster_url
    })
    if not posters:
        return "", ""
    selected = next(
        (
            item for item in posters
            if original_language and item[1] == original_language
        ),
        None,
    )
    selected = selected or next(
        (item for item in posters if not item[1]),
        posters[0],
    )
    return selected


def _merge_fact_group(facts: list[EvidenceFact]) -> EvidenceFact:
    facts = sorted(
        facts,
        key=lambda fact: json.dumps(
            {
                "fact_id": fact.fact_id,
                "titles": fact.titles,
                "year": fact.year,
                "media_type": fact.media_type,
                "external_ids": dict(fact.external_ids),
                "source_url": fact.source_url,
                "poster_url": fact.poster_url,
                "summary": fact.summary,
                "original_title": fact.original_title,
                "original_language": fact.original_language,
                "official_english_title": fact.official_english_title,
                "romanized_original_title": fact.romanized_original_title,
                "chinese_title": fact.chinese_title,
                "poster_language": fact.poster_language,
                "genres": fact.genres,
                "countries": fact.countries,
                "episodes": fact.episodes,
                "original_release_date": fact.original_release_date,
                "runtime_minutes": fact.runtime_minutes,
                "status": fact.status,
                "release_format": fact.release_format,
                "relations": fact.relations,
                "studios": fact.studios,
                "networks": fact.networks,
                "cast": fact.cast,
                "crew": fact.crew,
                "certifications": fact.certifications,
                "backdrop_urls": fact.backdrop_urls,
                "season_count": fact.season_count,
                "episode_count": fact.episode_count,
                "episode_inventory": dict(fact.episode_inventory),
                "douban_title_raw": fact.douban_title_raw,
                "source_season_number": fact.source_season_number,
                "complex_signals": fact.complex_signals,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
    )
    identity_conflicts = []
    for field in ("year", "media_type"):
        values = _sorted_unique_text(
            getattr(fact, field) for fact in facts
        )
        if len(values) > 1:
            identity_conflicts.append(field)
    external_values = {}
    for fact in facts:
        for key, value in fact.external_ids.items():
            if _text(key) and _text(value):
                external_values.setdefault(_text(key), set()).add(
                    _text(value)
                )
    identity_conflicts.extend(
        f"external_ids.{key}"
        for key, values in external_values.items()
        if len(values) > 1
    )
    if identity_conflicts:
        raise EvidenceFactConflict(
            facts[0].fact_id,
            facts[0].provider,
            identity_conflicts,
        )

    year = _single_identity_value(facts, "year")
    media_type = _single_identity_value(facts, "media_type")
    original_language = _preferred_text(
        fact.original_language for fact in facts
    )
    poster_url, poster_language = _poster_fields(facts, original_language)
    return EvidenceFact(
        fact_id=facts[0].fact_id,
        provider=facts[0].provider,
        titles=_sorted_unique_text(
            title for fact in facts for title in fact.titles
        ),
        year=year,
        media_type=media_type,
        external_ids=_merged_external_ids(facts),
        source_url=_preferred_text(
            (fact.source_url for fact in facts),
            shortest=True,
        ),
        poster_url=poster_url,
        summary=_preferred_text(
            fact.summary for fact in facts
        ),
        original_title=_preferred_text(
            fact.original_title for fact in facts
        ),
        original_language=original_language,
        official_english_title=_preferred_text(
            fact.official_english_title for fact in facts
        ),
        romanized_original_title=_preferred_text(
            fact.romanized_original_title for fact in facts
        ),
        chinese_title=_preferred_text(
            fact.chinese_title for fact in facts
        ),
        poster_language=poster_language,
        genres=_sorted_unique_text(
            genre for fact in facts for genre in fact.genres
        ),
        countries=_sorted_unique_text(
            country for fact in facts for country in fact.countries
        ),
        episodes=_merged_episodes(facts),
        original_release_date=_preferred_text(
            fact.original_release_date for fact in facts
        ),
        runtime_minutes=_preferred_integer(
            fact.runtime_minutes for fact in facts
        ),
        status=_preferred_text(fact.status for fact in facts),
        release_format=_preferred_text(
            fact.release_format for fact in facts
        ),
        relations=_unique_records(
            value for fact in facts for value in fact.relations
        ),
        studios=_sorted_unique_text(
            value for fact in facts for value in fact.studios
        ),
        networks=_sorted_unique_text(
            value for fact in facts for value in fact.networks
        ),
        cast=_unique_records(
            value for fact in facts for value in fact.cast
        ),
        crew=_unique_records(
            value for fact in facts for value in fact.crew
        ),
        certifications=_sorted_unique_text(
            value for fact in facts for value in fact.certifications
        ),
        backdrop_urls=_sorted_unique_text(
            value for fact in facts for value in fact.backdrop_urls
        ),
        season_count=_preferred_integer(
            fact.season_count for fact in facts
        ),
        episode_count=_preferred_integer(
            fact.episode_count for fact in facts
        ),
        episode_inventory=MappingProxyType(dict(max(
            (fact.episode_inventory for fact in facts),
            key=lambda value: (
                {
                    "complete": 6,
                    "partial": 5,
                    "parse_error": 4,
                    "conflict": 3,
                    "absent": 2,
                    "unavailable": 1,
                }.get(_text(value.get("status")), 0),
                len(json.dumps(
                    dict(value),
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )),
            ),
            default={},
        ))),
        douban_title_raw=_preferred_text(
            fact.douban_title_raw for fact in facts
        ),
        source_season_number=_preferred_integer(
            fact.source_season_number for fact in facts
        ),
        complex_signals=_sorted_unique_text(
            signal for fact in facts for signal in fact.complex_signals
        ),
        stable_fact_id=(
            facts[0].stable_fact_id
            or facts[0].fact_id
        ),
    )


def _converged_facts(sources: list[dict]) -> tuple[list[EvidenceFact], tuple[FactMergeDiagnostic, ...]]:
    groups = {}
    for source in sources or []:
        for fact in _facts_from_source(source):
            groups.setdefault(fact.fact_id, []).append(fact)
    facts = []
    diagnostics = []
    for fact_id in sorted(groups):
        group = groups[fact_id]
        facts.append(_merge_fact_group(group))
        if len(group) > 1:
            diagnostics.append(FactMergeDiagnostic(
                provider=group[0].provider,
                fact_id=fact_id,
                occurrences=len(group),
            ))
    return facts, tuple(diagnostics)


def _occurrence_fact(fact: EvidenceFact) -> EvidenceFact:
    stable_fact_id = fact.stable_fact_id or fact.fact_id
    serialized = json.dumps(
        {
            "provider": fact.provider,
            "titles": fact.titles,
            "year": fact.year,
            "media_type": fact.media_type,
            "external_ids": dict(fact.external_ids),
            "source_url": fact.source_url,
            "poster_url": fact.poster_url,
            "summary": fact.summary,
            "original_title": fact.original_title,
            "original_language": fact.original_language,
            "official_english_title": fact.official_english_title,
            "romanized_original_title": fact.romanized_original_title,
            "chinese_title": fact.chinese_title,
            "poster_language": fact.poster_language,
            "genres": fact.genres,
            "countries": fact.countries,
            "episodes": fact.episodes,
            "original_release_date": fact.original_release_date,
            "runtime_minutes": fact.runtime_minutes,
            "status": fact.status,
            "release_format": fact.release_format,
            "relations": fact.relations,
            "studios": fact.studios,
            "networks": fact.networks,
            "cast": fact.cast,
            "crew": fact.crew,
            "certifications": fact.certifications,
            "backdrop_urls": fact.backdrop_urls,
            "season_count": fact.season_count,
            "episode_count": fact.episode_count,
            "complex_signals": fact.complex_signals,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]
    return replace(
        fact,
        fact_id=f"{stable_fact_id}@occurrence:{digest}",
        stable_fact_id=stable_fact_id,
    )


def _discovery_facts(
    sources: list[dict],
) -> tuple[list[EvidenceFact], tuple[FactMergeDiagnostic, ...]]:
    groups = {}
    for source in sources or []:
        for fact in _facts_from_source(source):
            groups.setdefault(fact.fact_id, []).append(fact)
    facts = []
    diagnostics = []
    for fact_id in sorted(groups):
        group = groups[fact_id]
        conflicting_fields = ()
        try:
            facts.append(_merge_fact_group(group))
        except EvidenceFactConflict as exc:
            conflicting_fields = exc.conflicting_fields
            occurrences = {
                occurrence.fact_id: occurrence
                for occurrence in (
                    _occurrence_fact(fact) for fact in group
                )
            }
            facts.extend(
                occurrences[key] for key in sorted(occurrences)
            )
        if len(group) > 1:
            diagnostics.append(FactMergeDiagnostic(
                provider=group[0].provider,
                fact_id=fact_id,
                occurrences=len(group),
                conflicting_fields=conflicting_fields,
            ))
    return facts, tuple(diagnostics)


def _candidate_key(facts: list[EvidenceFact]) -> str:
    for provider, key in (
        ("tvdb", "tvdb"),
        ("douban", "douban_subject"),
        ("wikipedia", "wikipedia"),
        ("tmdb", "tmdb"),
        ("anilist", "anilist"),
    ):
        for fact in facts:
            if fact.provider == provider and _text(fact.external_ids.get(key)):
                media_type = fact.media_type or "media"
                return f"{provider}:{media_type}:{fact.external_ids[key]}"
    first = facts[0]
    title = min(first.normalized_titles, key=len, default="unknown")
    return f"title:{title}:{first.year}:{first.media_type or 'media'}"


def _safe_cluster_matches(
    clusters: list[list[EvidenceFact]],
    fact: EvidenceFact,
) -> list[list[EvidenceFact]]:
    matches = [
        cluster for cluster in clusters if _matches_candidate(cluster, fact)
    ]
    matched_types = {
        existing.media_type
        for cluster in matches
        for existing in cluster
        if existing.media_type
    }
    if len(matched_types) <= 1:
        return matches
    same_year = [
        cluster
        for cluster in matches
        if fact.year
        and fact.year in {
            existing.year for existing in cluster if existing.year
        }
    ]
    return same_year if len(same_year) == 1 else []


def _cluster_facts(
    facts: list[EvidenceFact],
    diagnostics: tuple[FactMergeDiagnostic, ...],
) -> SearchGraph:
    clusters: list[list[EvidenceFact]] = []
    for fact in facts:
        matches = _safe_cluster_matches(clusters, fact)
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
    return SearchGraph(tuple(candidates), diagnostics)


def build_discovery_graph(sources: list[dict]) -> SearchGraph:
    facts, diagnostics = _discovery_facts(sources)
    return _cluster_facts(facts, diagnostics)


def build_search_graph(sources: list[dict]) -> SearchGraph:
    facts, diagnostics = _converged_facts(sources)
    return _cluster_facts(facts, diagnostics)


def merge_verified_equivalence_edges(
    graph: SearchGraph,
    edges,
) -> SearchGraph:
    """Merge candidate components connected by already verified fact edges."""

    candidates = list((graph or SearchGraph(())).candidates)
    if not candidates or not edges:
        return SearchGraph(tuple(candidates), graph.fact_merges)
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
    return SearchGraph(tuple(merged), graph.fact_merges)
