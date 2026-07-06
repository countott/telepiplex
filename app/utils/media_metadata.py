# -*- coding: utf-8 -*-


def _clean_text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _clean_mapping(mapping: dict | None) -> dict:
    if not isinstance(mapping, dict):
        return {}
    return {str(key): _clean_text(value) for key, value in mapping.items() if _clean_text(value)}


def _clean_evidence(evidence) -> list[dict]:
    if not isinstance(evidence, list):
        return []
    return [item for item in evidence if isinstance(item, dict)]


def _query_from_title_year(title: str, year: str = "") -> str:
    title = _clean_text(title)
    year = _clean_text(year)
    if title and year and year not in title:
        return f"{title} {year}"
    return title


def build_search_metadata(
    source: str,
    media_type: str = "",
    chinese_title: str = "",
    english_title: str = "",
    year: str = "",
    query: str = "",
    original_url: str = "",
    collection_chinese_title: str = "",
    collection_english_title: str = "",
    external_ids: dict | None = None,
    evidence: list[dict] | None = None,
) -> dict:
    metadata = {
        "source": _clean_text(source),
        "media_type": _clean_text(media_type),
        "chinese_title": _clean_text(chinese_title),
        "english_title": _clean_text(english_title),
        "year": _clean_text(year),
        "query": _clean_text(query) or _query_from_title_year(english_title or chinese_title, year),
        "original_url": _clean_text(original_url),
        "external_ids": _clean_mapping(external_ids),
        "evidence": _clean_evidence(evidence),
    }
    collection_chinese_title = _clean_text(collection_chinese_title)
    collection_english_title = _clean_text(collection_english_title)
    if collection_chinese_title:
        metadata["collection_chinese_title"] = collection_chinese_title
    if collection_english_title:
        metadata["collection_english_title"] = collection_english_title
    return metadata


def build_external_metadata(
    source: str,
    title: str,
    year: str = "",
    external_id: str = "",
    original_url: str = "",
    media_type: str = "series",
) -> dict:
    source = _clean_text(source)
    title = _clean_text(title)
    year = _clean_text(year)
    external_ids = {source: external_id} if source and _clean_text(external_id) else {}
    return build_search_metadata(
        source=source,
        media_type=media_type,
        english_title=title,
        year=year,
        query=_query_from_title_year(title, year),
        original_url=original_url,
        external_ids=external_ids,
        evidence=[
            {
                "source": source,
                "field": "title_year",
                "title": title,
                "year": year,
            }
        ],
    )
