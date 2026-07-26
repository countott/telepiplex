"""Source-backed canonical title policy for media entities."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .entity_graph import CandidateEntity, normalize_title


_CJK = re.compile(r"[\u3400-\u9fff]")
_ANIMATION_SIGNALS = ("animation", "animated", "anime", "动画", "動畫")
_HIRAGANA = re.compile(r"[\u3041-\u3096]")
_KATAKANA = re.compile(r"[\u30a1-\u30fa]")
_KANJI = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_KANA_BREAK = re.compile(r"[\s・･,，、。.!！?？:：;；/／〜～\-]+")
_KANA = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "wo", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ゔ": "vu",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
}
_KANA_DIGRAPHS = {
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo",
    "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho",
    "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
    "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
    "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
    "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
    "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "じゃ": "ja", "じゅ": "ju", "じょ": "jo",
    "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
    "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
    "てぃ": "ti", "でぃ": "di", "とぅ": "tu", "どぅ": "du",
    "ふぁ": "fa", "ふぃ": "fi", "ふぇ": "fe", "ふぉ": "fo",
    "うぃ": "wi", "うぇ": "we", "うぉ": "wo",
    "ゔぁ": "va", "ゔぃ": "vi", "ゔぇ": "ve", "ゔぉ": "vo",
}


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


def _is_japanese_animation(candidate: CandidateEntity) -> bool:
    return bool(
        any(fact.media_type in {"movie", "series"} for fact in candidate.facts)
        and any(
            signal in _text(genre).casefold()
            for fact in candidate.facts
            for genre in fact.genres
            for signal in _ANIMATION_SIGNALS
        )
    )


def _hiragana(value: str) -> str:
    result = []
    for character in value:
        codepoint = ord(character)
        if 0x30A1 <= codepoint <= 0x30F6:
            result.append(chr(codepoint - 0x60))
        else:
            result.append(character)
    return "".join(result)


def _geminate_prefix(romaji: str) -> str:
    if romaji.startswith("ch"):
        return "t"
    if romaji.startswith(("sh", "ts")):
        return romaji[0]
    return romaji[:1] if romaji[:1] not in "aeiou" else ""


def _romanize_kana_segment(value: str) -> str:
    source = _hiragana(value)
    result = []
    index = 0
    geminate = False
    while index < len(source):
        character = source[index]
        if character == "っ":
            geminate = True
            index += 1
            continue
        if character == "ー":
            vowels = re.findall(r"[aeiou]", "".join(result))
            if not vowels:
                return ""
            result.append(vowels[-1])
            index += 1
            continue
        pair = source[index:index + 2]
        romaji = _KANA_DIGRAPHS.get(pair)
        if romaji:
            index += 2
        else:
            romaji = _KANA.get(character)
            index += 1
        if not romaji:
            return ""
        if character == "ん" and index < len(source):
            following = _KANA_DIGRAPHS.get(
                source[index:index + 2],
                _KANA.get(source[index], ""),
            )
            if following.startswith(("b", "m", "p")):
                romaji = "m"
            elif following.startswith(("a", "e", "i", "o", "u", "y")):
                romaji = "n'"
        if geminate:
            romaji = _geminate_prefix(romaji) + romaji
            geminate = False
        result.append(romaji)
    if geminate:
        return ""
    return "".join(result)


def _kana_segments(value: str) -> list[str]:
    result = []
    current = []
    current_script = ""
    for character in value:
        if _KANA_BREAK.fullmatch(character):
            if current:
                result.append("".join(current))
                current = []
                current_script = ""
            continue
        script = (
            "hiragana"
            if _HIRAGANA.fullmatch(character)
            else "katakana"
            if _KATAKANA.fullmatch(character) or character == "ー"
            else "latin"
            if character.isascii() and character.isalnum()
            else ""
        )
        if not script:
            return []
        if current and script != current_script and character != "ー":
            result.append("".join(current))
            current = []
        current.append(character)
        current_script = script
    if current:
        result.append("".join(current))
    return result


def _romanize_japanese_title(value: str) -> str:
    value = _text(value)
    if not value or _KANJI.search(value):
        return ""
    words = []
    for segment in _kana_segments(value):
        if segment.isascii():
            word = segment
        else:
            word = _romanize_kana_segment(segment)
        if not word:
            return ""
        if len(segment) == 1 and _HIRAGANA.fullmatch(segment):
            word = {"は": "wa", "へ": "e", "を": "o"}.get(segment, word)
        words.append(word[:1].upper() + word[1:])
    for index in range(1, len(words)):
        if words[index].casefold() in {
            "de", "e", "ga", "ka", "ni", "no", "o", "to", "wa", "ya",
        }:
            words[index] = words[index].casefold()
    return " ".join(words)


def _chinese_values(candidate: CandidateEntity, provider: str) -> list[str]:
    result = []
    for fact in candidate.facts:
        if fact.provider != provider:
            continue
        for value in (fact.chinese_title, *fact.titles):
            value = _text(value)
            if value and _CJK.search(value) and value not in result:
                result.append(value)
    return result


def _chinese_title(
    candidate: CandidateEntity,
    preferred_chinese_title: str = "",
) -> str:
    original_titles = {
        _text(fact.original_title) for fact in candidate.facts if fact.original_title
    }
    preferred = _text(preferred_chinese_title)
    if (
        preferred
        and _CJK.search(preferred)
        and preferred not in original_titles
        and normalize_title(preferred) in candidate.normalized_titles
    ):
        return preferred
    for title in _chinese_values(candidate, "douban"):
        if title not in original_titles:
            return title
    for title in _chinese_values(candidate, "douban"):
        return title
    return ""


def resolve_title_policy(
    candidate: CandidateEntity,
    *,
    preferred_chinese_title: str = "",
) -> CanonicalTitles:
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
    if (
        original_language == "ja"
        and not romanized_original_title
        and original_title
        and _is_japanese_animation(candidate)
    ):
        romanized_original_title = _romanize_japanese_title(original_title)

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
        chinese_title=_chinese_title(candidate, preferred_chinese_title),
        original_title=original_title,
        original_language=original_language,
        official_english_title=official_english_title,
        romanized_original_title=romanized_original_title,
        canonical_search_title=canonical,
        canonical_latin_title=canonical,
        search_title_policy=policy,
    )
