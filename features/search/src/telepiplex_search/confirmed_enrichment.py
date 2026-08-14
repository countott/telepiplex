"""Deterministic source queries after a user-confirmed work identity."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .entity_graph import normalize_title


@dataclass(frozen=True)
class ConfirmedIdentity:
    provider: str
    stable_id: str
    chinese_title: str
    english_title: str
    original_title: str
    year: str
    media_type: str
    requested_scope: str
    original_language: str
    genres: tuple[str, ...]
    external_ids: dict[str, str]
    countries: tuple[str, ...] = ()
    cast_names: tuple[str, ...] = ()
    season_number: int | None = None


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _titles(value: dict) -> set[str]:
    return {
        normalized
        for title in (
            value.get("title"),
            value.get("name"),
            value.get("chinese_title"),
            value.get("english_title"),
            value.get("official_english_title"),
            value.get("original_title"),
            *(value.get("aliases") or ()),
        )
        if (normalized := normalize_title(title))
    }


def _identity_titles(identity: ConfirmedIdentity) -> set[str]:
    return {
        normalized
        for title in (
            identity.chinese_title,
            identity.english_title,
            identity.original_title,
        )
        if (normalized := normalize_title(title))
    }


def build_wikipedia_queries(
    identity: ConfirmedIdentity,
) -> dict[str, list[str]]:
    if identity.media_type not in {"movie", "series"}:
        return {"wikipedia_zh": [], "wikipedia_en": []}
    zh_type = "电影" if identity.media_type == "movie" else "电视剧"
    en_type = "film" if identity.media_type == "movie" else "TV series"
    zh = _text(" ".join(filter(None, (
        identity.chinese_title,
        identity.year,
        zh_type,
    ))))
    english_base = identity.english_title or (
        identity.original_title
        if re.search(r"[A-Za-z]", identity.original_title)
        else ""
    )
    en = _text(" ".join(filter(None, (
        english_base,
        identity.year,
        en_type,
    ))))
    return {
        "wikipedia_zh": [zh] if zh else [],
        "wikipedia_en": [en] if en else [],
    }


def build_tmdb_query(identity: ConfirmedIdentity) -> dict | None:
    if identity.media_type not in {"movie", "series"}:
        return None
    title = _text(
        identity.english_title
        or (
            identity.original_title
            if re.search(r"[A-Za-z]", identity.original_title)
            else ""
        )
        or identity.chinese_title
    )
    if not title:
        return None
    return {
        "title": title,
        "year": identity.year,
        "media_type": identity.media_type,
    }


def is_confirmed_japanese_animation(identity: ConfirmedIdentity) -> bool:
    return bool(
        identity.original_language == "ja"
        and any(
            signal in _text(genre).casefold()
            for genre in identity.genres
            for signal in ("animation", "animated", "anime", "动画", "動畫")
        )
    )


def build_anilist_query(identity: ConfirmedIdentity) -> dict | None:
    if not is_confirmed_japanese_animation(identity):
        return None
    title = _text(identity.english_title or identity.original_title)
    if not title:
        return None
    return {"title": title, "year": identity.year}


def _same_identity(
    raw: dict,
    identity: ConfirmedIdentity,
    *,
    media_type: str,
    require_media_type: bool = False,
) -> bool:
    raw_type = _text(raw.get("media_type")).casefold()
    if require_media_type and not raw_type:
        return False
    if raw_type and raw_type != media_type:
        return False
    raw_year = _text(raw.get("year"))[:4]
    if identity.year and raw_year and raw_year != identity.year:
        return False
    return bool(_titles(raw).intersection(_identity_titles(identity)))


def select_unique_wikipedia_fact(
    result: dict,
    identity: ConfirmedIdentity,
) -> dict | None:
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    matches = [
        fact
        for fact in result.get("facts") or ()
        if isinstance(fact, dict)
        and _same_identity(
            fact,
            identity,
            media_type=identity.media_type,
            require_media_type=True,
        )
        and _text(
            fact.get("wikibase_item")
            or (
                fact.get("external_ids")
                if isinstance(fact.get("external_ids"), dict)
                else {}
            ).get("wikipedia")
        )
    ]
    stable_ids = {
        _text(
            fact.get("wikibase_item")
            or (fact.get("external_ids") or {}).get("wikipedia")
        )
        for fact in matches
    }
    return dict(matches[0]) if len(stable_ids) == 1 else None


def _select_unique_flat_fact(
    result: dict,
    identity: ConfirmedIdentity,
    *,
    provider: str,
    id_key: str,
) -> dict | None:
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    matches = [
        fact
        for fact in result.get("facts") or ()
        if isinstance(fact, dict)
        and _same_identity(
            fact,
            identity,
            media_type=identity.media_type,
            require_media_type=True,
        )
        and _text(
            fact.get(id_key)
            or fact.get("id")
            or (
                fact.get("external_ids")
                if isinstance(fact.get("external_ids"), dict)
                else {}
            ).get(provider)
        )
    ]
    stable_ids = {
        _text(
            fact.get(id_key)
            or fact.get("id")
            or (fact.get("external_ids") or {}).get(provider)
        )
        for fact in matches
    }
    return dict(matches[0]) if len(stable_ids) == 1 else None


def select_unique_tmdb_fact(
    result: dict,
    identity: ConfirmedIdentity,
) -> dict | None:
    return _select_unique_flat_fact(
        result,
        identity,
        provider="tmdb",
        id_key="tmdb_id",
    )


def select_unique_anilist_fact(
    result: dict,
    identity: ConfirmedIdentity,
) -> dict | None:
    return _select_unique_flat_fact(
        result,
        identity,
        provider="anilist",
        id_key="anilist_id",
    )


def select_unique_douban_fact(
    result: dict,
    identity: ConfirmedIdentity,
) -> dict | None:
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    matches = []
    for fact in result.get("facts") or ():
        if not isinstance(fact, dict):
            continue
        match_mode = douban_identity_match(fact, identity)
        if not match_mode:
            continue
        selected = dict(fact)
        selected["douban_match_mode"] = match_mode
        matches.append(selected)
    stable_ids = {
        _text(
            fact.get("subject_id")
            or (fact.get("external_ids") or {}).get("douban_subject")
        )
        for fact in matches
    }
    stable_ids.discard("")
    if len(stable_ids) == 1:
        return dict(matches[0])
    if matches and all(
        fact.get("douban_match_mode") == "imdb_exact"
        for fact in matches
    ):
        root_titles = {
            normalize_title(fact.get("chinese_title"))
            for fact in matches
            if normalize_title(fact.get("chinese_title"))
        }
        imdb_ids = {
            _external_id(fact, "imdb") for fact in matches
            if _external_id(fact, "imdb")
        }
        season_numbers = []
        for fact in matches:
            try:
                season = int(fact.get("season_number"))
            except (TypeError, ValueError):
                season = 0
            if season > 0:
                season_numbers.append(season)
        split_seasons_are_unique = bool(
            len(season_numbers) == len(matches)
            and len(set(season_numbers)) == len(season_numbers)
        )
        if (
            len(root_titles) == 1
            and len(imdb_ids) == 1
            and split_seasons_are_unique
        ):
            def season_key(fact):
                try:
                    season = int(fact.get("season_number"))
                except (TypeError, ValueError):
                    season = 0
                return (
                    season,
                    _text(fact.get("year"))[:4],
                    _text(fact.get("subject_id")),
                )

            selected = dict(min(matches, key=season_key))
            selected["douban_subject_ids"] = sorted(stable_ids)
            return selected
    return None


def _external_id(value: dict, key: str) -> str:
    external_ids = (
        value.get("external_ids")
        if isinstance(value.get("external_ids"), dict)
        else {}
    )
    return _text(external_ids.get(key)).casefold()


def _latin_identity_titles(value: dict) -> set[str]:
    return {
        normalized
        for title in (
            value.get("english_title"),
            value.get("official_english_title"),
            value.get("original_title"),
            *(value.get("aliases") or ()),
        )
        if re.search(r"[A-Za-z]", _text(title))
        and (normalized := normalize_title(title))
    }


def _identity_latin_titles(identity: ConfirmedIdentity) -> set[str]:
    return {
        normalized
        for title in (identity.english_title, identity.original_title)
        if re.search(r"[A-Za-z]", _text(title))
        and (normalized := normalize_title(title))
    }


def _person_names(value) -> set[str]:
    items = value if isinstance(value, (list, tuple)) else ()
    result = set()
    for item in items:
        raw = (
            item.get("name") or item.get("title")
            if isinstance(item, dict)
            else item
        )
        if normalized := normalize_title(raw):
            result.add(normalized)
    return result


def douban_identity_match(
    fact: dict,
    identity: ConfirmedIdentity,
) -> str:
    raw_type = _text(fact.get("media_type")).casefold()
    if not raw_type or raw_type != identity.media_type:
        return ""

    expected_imdb = _text(identity.external_ids.get("imdb")).casefold()
    fact_imdb = _external_id(fact, "imdb")
    if expected_imdb and fact_imdb:
        return "imdb_exact" if expected_imdb == fact_imdb else ""

    fact_year = _text(fact.get("year"))[:4]
    if identity.year and fact_year and identity.year != fact_year:
        return ""
    if not _latin_identity_titles(fact).intersection(
        _identity_latin_titles(identity)
    ):
        return ""

    strong_fields = 0
    if identity.year and fact_year and identity.year == fact_year:
        strong_fields += 1
    fact_language = _text(fact.get("original_language")).casefold()
    if (
        identity.original_language
        and fact_language
        and identity.original_language == fact_language
    ):
        strong_fields += 1
    fact_countries = {
        normalize_title(value) or _text(value).casefold()
        for value in fact.get("countries") or ()
        if _text(value)
    }
    identity_countries = {
        normalize_title(value) or _text(value).casefold()
        for value in identity.countries
        if _text(value)
    }
    if fact_countries.intersection(identity_countries):
        strong_fields += 1
    fact_people = _person_names(
        list(fact.get("cast") or ())
        + list(fact.get("crew") or ())
        + list(fact.get("directors") or ())
        + list(fact.get("actors") or ())
    )
    identity_people = {
        normalized
        for value in identity.cast_names
        if (normalized := normalize_title(value))
    }
    if fact_people.intersection(identity_people):
        strong_fields += 1
    try:
        fact_season = int(fact.get("season_number"))
    except (TypeError, ValueError):
        fact_season = None
    if (
        identity.season_number is not None
        and fact_season == identity.season_number
    ):
        strong_fields += 1
    return "strong_fields" if strong_fields >= 2 else ""


def build_tvdb_query(
    identity: ConfirmedIdentity,
    wikipedia_fact: dict | None,
) -> dict | None:
    if identity.media_type != "series":
        return None
    wikipedia_fact = (
        wikipedia_fact if isinstance(wikipedia_fact, dict) else {}
    )
    title = _text(
        wikipedia_fact.get("official_english_title")
        or wikipedia_fact.get("english_title")
        or identity.english_title
        or identity.original_title
    )
    if not title or not re.search(r"[A-Za-z]", title):
        return None
    return {
        "title": title,
        "year": _text(wikipedia_fact.get("year") or identity.year)[:4],
        "media_type": "series",
    }


def select_unique_tvdb_series(
    result: dict,
    identity: ConfirmedIdentity,
) -> dict | None:
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    series = []
    for fact in result.get("facts") or ():
        if not isinstance(fact, dict):
            continue
        series.extend(
            item
            for item in fact.get("series") or ()
            if isinstance(item, dict)
        )
    matches = [
        item
        for item in series
        if _same_identity(item, identity, media_type="series")
        and _text(
            item.get("tvdb_series_id")
            or item.get("tvdb_id")
            or item.get("id")
        )
    ]
    ids = {
        _text(
            item.get("tvdb_series_id")
            or item.get("tvdb_id")
            or item.get("id")
        )
        for item in matches
    }
    return dict(matches[0]) if len(ids) == 1 else None
