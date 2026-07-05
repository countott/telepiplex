# -*- coding: utf-8 -*-

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse


SITE_SUFFIX_PATTERNS = [
    re.compile(r"\s*[\-|]\s*IMDb\s*$", re.IGNORECASE),
    re.compile(r"\s*[\-|]\s*TheTVDB(?:\.com)?\s*$", re.IGNORECASE),
    re.compile(r"\s*[\-|]\s*TVDB(?:\.com)?\s*$", re.IGNORECASE),
    re.compile(r"\s*[\-|]\s*豆瓣(?:电影)?\s*$", re.IGNORECASE),
    re.compile(r"\s*\(豆瓣\)\s*$", re.IGNORECASE),
]


class _MetadataTitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title_parts = []
        self.og_title = ""

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True
            return

        if tag.lower() != "meta":
            return

        attr_map = {str(key).lower(): value for key, value in attrs}
        prop = str(attr_map.get("property") or attr_map.get("name") or "").lower()
        if prop == "og:title" and attr_map.get("content"):
            self.og_title = str(attr_map["content"])

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self):
        return "".join(self.title_parts)


def _collapse_spaces(text: str) -> str:
    return " ".join(str(text or "").replace("\xa0", " ").split())


def _clean_media_title(title: str) -> str:
    title = html.unescape(_collapse_spaces(title))
    for pattern in SITE_SUFFIX_PATTERNS:
        title = pattern.sub("", title).strip()

    year_match = re.search(r"\((19\d{2}|20\d{2})(?:[–-]\d{0,4})?\)", title)
    if year_match:
        title = f"{title[:year_match.start()].strip()} {year_match.group(1)}{title[year_match.end():]}".strip()

    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"\s+/\s+.*$", "", title).strip()
    return title


def parse_media_page_title(html_text: str) -> str:
    parser = _MetadataTitleParser()
    parser.feed(html_text or "")
    return _clean_media_title(parser.og_title or parser.title)


def _title_with_year(title: str, year: str) -> str:
    title = _clean_media_title(title)
    year = _collapse_spaces(year)
    if not title:
        return ""
    if year and year not in title:
        title = f"{title}({year})"
    return _clean_media_title(title)


def _contains_latin(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]", str(text or "")))


def _latin_title_from_mixed(title: str) -> str:
    title = _clean_media_title(title)
    if not title or not _contains_latin(title):
        return title
    if not re.search(r"[\u4e00-\u9fff]", title):
        return title

    year = ""
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", title)
    if year_match:
        year = year_match.group(1)

    latin_match = re.search(r"[A-Za-z][A-Za-z0-9\s'’`.,:;!?&+\-()]+", title)
    if not latin_match:
        return title

    latin_title = _clean_media_title(latin_match.group(0))
    latin_title = re.sub(r"\b(19\d{2}|20\d{2})\b.*$", r"\1", latin_title).strip()
    return _title_with_year(latin_title, year)


def _as_title_list(value) -> list[str]:
    if isinstance(value, list):
        values = value
    elif isinstance(value, str):
        values = re.split(r"\s*/\s*|\s*[、,]\s*", value)
    else:
        values = []

    titles = []
    for item in values:
        title = _clean_media_title(str(item or ""))
        if title:
            titles.append(title)
    return titles


def _preferred_douban_title(data: dict) -> str:
    if not isinstance(data, dict):
        return ""

    alias_fields = [
        data.get("original_title"),
        data.get("originalTitle"),
        data.get("original_name"),
        data.get("originalName"),
        data.get("foreign_title"),
        data.get("foreignTitle"),
    ]
    alias_fields.extend(_as_title_list(data.get("aka")))
    alias_fields.extend(_as_title_list(data.get("aka_titles")))
    alias_fields.extend(_as_title_list(data.get("aliases")))
    alias_fields.extend(_as_title_list(data.get("alias")))

    title_fields = [data.get("title"), data.get("name")]
    candidates = [_clean_media_title(title) for title in alias_fields + title_fields]
    candidates = [title for title in candidates if title and title not in {"豆瓣", "豆瓣电影"}]

    for title in candidates:
        if _contains_latin(title):
            return _latin_title_from_mixed(title)

    return candidates[0] if candidates else ""


def _useful_douban_title(title: str) -> str:
    title = _clean_media_title(title)
    if title in {"豆瓣", "豆瓣电影"}:
        return ""
    return _latin_title_from_mixed(title)


def extract_douban_subject_id(raw_url: str) -> str:
    parsed = urlparse(str(raw_url or "").strip())
    if "douban.com" not in parsed.netloc.lower():
        return ""

    match = re.search(r"/subject/(\d+)/?", parsed.path)
    return match.group(1) if match else ""


def parse_douban_subject_abstract_title(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""

    data = payload.get("subject") if isinstance(payload.get("subject"), dict) else payload
    return _title_with_year(_preferred_douban_title(data), data.get("release_year") or data.get("year") or "")


def parse_douban_rexxar_title(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""

    return _title_with_year(_preferred_douban_title(payload), payload.get("year") or payload.get("release_year") or "")


def parse_douban_mobile_title(html_text: str) -> str:
    text = html.unescape(str(html_text or ""))
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:p|div|span|li|h\d)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [_collapse_spaces(line) for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)

    year = ""
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    if year_match:
        year = year_match.group(1)

    for label in ["原名", "外文名", "又名"]:
        match = re.search(rf"{label}\s*[:：]\s*([^\n]+)", text)
        if not match:
            continue
        for title in _as_title_list(match.group(1)):
            if _contains_latin(title):
                return _title_with_year(title, year)

    return _useful_douban_title(parse_media_page_title(html_text))


def is_supported_metadata_url(raw_url: str) -> bool:
    host = urlparse(str(raw_url or "").strip()).netloc.lower()
    return any(
        domain in host
        for domain in [
            "douban.com",
            "imdb.com",
            "thetvdb.com",
            "tvdb.com",
        ]
    )
