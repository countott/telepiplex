# -*- coding: utf-8 -*-

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse


SITE_SUFFIX_PATTERNS = [
    re.compile(r"\s*[\-|]\s*IMDb\s*$", re.IGNORECASE),
    re.compile(r"\s*[\-|]\s*TheTVDB(?:\.com)?\s*$", re.IGNORECASE),
    re.compile(r"\s*[\-|]\s*TVDB(?:\.com)?\s*$", re.IGNORECASE),
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


def parse_douban_api_title(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    title = _clean_media_title(data.get("title") or "")
    if not title:
        return ""

    return title


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
