# -*- coding: utf-8 -*-

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse


YEAR_PATTERN = re.compile(r"\b(19\d{2}|20\d{2})\b")
SITE_SUFFIX_PATTERNS = [
    re.compile(r"\s*[\-|]\s*IMDb\s*$", re.IGNORECASE),
    re.compile(r"\s*[\-|]\s*TheTVDB(?:\.com)?\s*$", re.IGNORECASE),
    re.compile(r"\s*[\-|]\s*TVDB(?:\.com)?\s*$", re.IGNORECASE),
    re.compile(r"\s*\(豆瓣\)\s*$", re.IGNORECASE),
]
NOISE_LINE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"^imdb$",
        r"^imdbpro$",
        r"^豆瓣$",
        r"^豆瓣电影$",
        r"^thetvdb(?:\.com)?$",
        r"^tvdb(?:\.com)?$",
        r"^open in app$",
        r"^user reviews?$",
        r"^all topics$",
        r"^photos?$",
        r"^cast$",
        r"^episodes?$",
        r"^where to watch$",
        r"^想看\s*看过$",
        r"^看过$",
        r"^想看$",
        r"^\d+(?:\.\d+)?$",
        r"^\d+h\s*\d+m$",
    ]
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


def _is_noise_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in NOISE_LINE_PATTERNS)


def _looks_like_title(line: str) -> bool:
    if not line or _is_noise_line(line):
        return False
    if len(line) > 100:
        return False
    return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", line))


def extract_search_query_from_ocr_text(text: str) -> str:
    lines = [_collapse_spaces(line) for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]

    for index, line in enumerate(lines):
        title = _clean_media_title(line)
        if not _looks_like_title(title):
            continue

        year = ""
        for nearby_line in lines[index:index + 3]:
            year_match = YEAR_PATTERN.search(nearby_line)
            if year_match:
                year = year_match.group(1)
                break

        if year and year not in title:
            title = f"{title} {year}"
        return title

    return ""
