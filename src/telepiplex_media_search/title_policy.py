"""Source-backed canonical title policy for media entities."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .entity_graph import CandidateEntity


_CJK = re.compile(r"[\u3400-\u9fff]")


def _text(value) -> str:
    return " ".join(str(value or "").split())


class TitlePolicyError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CanonicalTitles:
    chinese_title: str
    original_title: str
    original_language: str
    official_english_title: str
    romanized_original_title: str
    canonical_search_title: str
    canonical_latin_title: str
    search_title_policy: str

    def identity_fields(self) -> dict:
        return {
            "chinese_title": self.chinese_title,
            "english_title": self.canonical_latin_title,
            "original_title": self.original_title,
            "original_language": self.original_language,
            "official_english_title": self.official_english_title,
            "romanized_original_title": self.romanized_original_title,
            "canonical_search_title": self.canonical_search_title,
            "search_title_policy": self.search_title_policy,
        }


def _preferred_fact_values(candidate: CandidateEntity, field: str) -> list[str]:
    result = []
    for provider in ("tvdb", "douban", "wikipedia"):
        for fact in candidate.facts:
            value = _text(getattr(fact, field, ""))
            if fact.provider == provider and value and value not in result:
                result.append(value)
    return result


def _chinese_title(candidate: CandidateEntity) -> str:
    original_titles = {
        _text(fact.original_title) for fact in candidate.facts if fact.original_title
    }
    for title in candidate.titles:
        title = _text(title)
        if title and _CJK.search(title) and title not in original_titles:
            return title
    for title in candidate.titles:
        title = _text(title)
        if title and _CJK.search(title):
            return title
    return ""


def resolve_title_policy(candidate: CandidateEntity) -> CanonicalTitles:
    language_values = _preferred_fact_values(candidate, "original_language")
    original_language = "ja" if "ja" in language_values else next(
        iter(language_values), ""
    )
    original_title = next(
        iter(_preferred_fact_values(candidate, "original_title")), ""
    )
    official_english_title = next(
        iter(_preferred_fact_values(candidate, "official_english_title")), ""
    )
    romanized_original_title = next(
        iter(_preferred_fact_values(candidate, "romanized_original_title")), ""
    )

    if original_language == "ja":
        if not romanized_original_title:
            raise TitlePolicyError("canonical_title_unavailable")
        canonical = romanized_original_title
        policy = "romanized_original"
    else:
        if not official_english_title:
            raise TitlePolicyError("canonical_title_unavailable")
        canonical = official_english_title
        policy = "official_english"

    return CanonicalTitles(
        chinese_title=_chinese_title(candidate),
        original_title=original_title,
        original_language=original_language,
        official_english_title=official_english_title,
        romanized_original_title=romanized_original_title,
        canonical_search_title=canonical,
        canonical_latin_title=canonical,
        search_title_policy=policy,
    )
