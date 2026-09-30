"""Free subtitle catalogues; keys unlock search, never machine translation.

Website adapters follow only public links/forms. API contracts:
https://subdl.com/developers and https://subsource.net/api-docs.
All downloaded bytes still pass Caption's archive, identity and quality gates.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlencode, unquote, urljoin, urlsplit

from .matching import match_candidate
from .models import QualityReport, SubtitleCandidate
from .quality import subtitle_priority
from .providers import (
    PAGE_MAX_BYTES, CookieWebsiteProvider, HttpResponse, ProviderError, SubtitleProvider, _filename,
    _format, _html, _identity, _integer, _json, _language, _links, _plain, _query_text,
)


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def _rows(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _release(value: Any) -> str:
    return " / ".join(_text(item) for item in value if _text(item)) if isinstance(value, list) else _text(value)


def _detail_work_title(title: str) -> tuple[str, int | None]:
    """Separate a site's explicit trailing release year from its work title."""
    year = re.search(r"[（(]((?:19|20)\d{2})[)）]\s*$", title)
    return (title[:year.start()].strip(), int(year[1])) if year else (title, None)


def _machine(item: dict[str, Any]) -> bool:
    for key in ("ai_translated", "is_ai_translation", "machine_translated", "isMachineTranslated"):
        if item.get(key) is True or str(item.get(key, "")).casefold() in {"1", "true", "yes"}:
            return True
    for key in ("productionType", "production_type", "translationType", "translation_type"):
        if re.fullmatch(r"(?:ai|machine|automatic)(?:[-_ ](?:translated|translation))?", _text(item.get(key)), re.I):
            return True
    labels = " ".join(_release(item.get(k)) for k in (
        "title", "name", "release_name", "releases", "releaseInfo", "simple_name", "comment",
        "productionType", "production_type", "translationType", "translation_type"))
    return bool(re.search(r"machine[-_ ]translated|AI[-_ ](?:translated|translation)|机器翻译|機器翻譯|机翻|機翻", labels, re.I))


def _empty_search(page: str) -> bool:
    return bool(re.search(r"no (?:results|subtitles|matches)|(?:zero|0) results|nothing found|查無資料|查无资料|未找到|没有找到|沒有找到", _plain(page), re.I))


def _unknown_search_page(page: str) -> None:
    if _empty_search(page):
        return
    if re.search(r"maintenance|service unavailable|維護中|维护中", _plain(page), re.I):
        raise ProviderError("unavailable", "字幕来源正在维护或暂时不可用")
    raise ProviderError("invalid_response", "字幕检索页面格式已改变，不能确认是否存在匹配结果")


def _lang(value: str) -> str:
    if re.search(r"traditional|big.?5|(?:zh|cmn|yue)[_-]hant|\bcht\b", value, re.I):
        return "cht"
    if re.search(r"simplified|(?:zh|cmn)[_-]hans|\bchs\b", value, re.I):
        return "chi"
    return _language(value)


def _chinese(value: str) -> bool:
    return bool(_lang(value) or re.search(r"chinese|cantonese|mandarin|中文|汉语|漢語|粵|粤|\b(?:zh|zho|chi|cht)(?:_bg)?\b", value, re.I))


def _bilingual_hint(value: str) -> bool:
    return bool(re.search(r"中英|简英|簡英|繁英|双语|雙語|bilingual|chs[._ &/-]*eng|chi[._ &/-]*eng", value, re.I)
                or (_chinese(value) and re.search(r"英语|英語|english|\beng\b", value, re.I)))


def _ordered(query, candidates):
    # Catalogue labels allocate inspection slots only. Actual acceptance and
    # priority always come from the downloaded text's QualityReport.
    def key(candidate):
        hint = QualityReport(True, language=candidate.language,
            bilingual=bool(candidate.metadata.get("bilingual")), format=candidate.subtitle_format)
        return subtitle_priority(query, hint), match_candidate(query, candidate).score
    return sorted(candidates, key=key, reverse=True)


def _safe_url(base: str, value: Any, hosts: tuple[str, ...]) -> str:
    """Cheap candidate validation; HttpTransport additionally verifies DNS/IP."""
    raw = _text(value)
    if not raw:
        return ""
    target = urljoin(base, raw)
    try:
        parts = urlsplit(target)
        host = (parts.hostname or "").lower()
        if (parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443)
                or not any(host == allowed or host.endswith("." + allowed) for allowed in hosts)):
            return ""
    except ValueError:
        return ""
    return target


def _binary(response: HttpResponse, fallback: str) -> tuple[bytes, str]:
    start = response.body[:1024].lstrip().lower()
    if start.startswith((b"<!doctype", b"<html", b"<?xml", b"{", b"[")) and not start.startswith(b"[script info]"):
        _html(response)
        raise ProviderError("user_action_required", "字幕下载返回了网页或错误数据，请检查来源站访问条件")
    return response.body, _filename(response, fallback)


def _validate_candidate(candidate: SubtitleCandidate, name: str) -> None:
    if candidate.provider != name:
        raise ProviderError("invalid_candidate", "字幕候选与来源不一致")


class _Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs = tag, dict(attrs)
        self.children: list[_Node | str] = []

    def nodes(self, tag: str = ""):
        stack = list(reversed(self.children))
        while stack:
            child = stack.pop()
            if isinstance(child, _Node):
                if not tag or child.tag == tag:
                    yield child
                stack.extend(reversed(child.children))

    def text(self):
        values, stack = [], list(reversed(self.children))
        while stack:
            child = stack.pop()
            if isinstance(child, str):
                values.append(child)
            else:
                stack.extend(reversed(child.children))
        return re.sub(r"\s+", " ", " ".join(values)).strip()

    def has_class(self, name: str):
        return name in (self.attrs.get("class") or "").split()


class _Tree(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]
        self.count = 0
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 50000:
            raise ProviderError("invalid_response", "来源页面节点数量异常")
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            if len(self.stack) >= 128:
                raise ProviderError("invalid_response", "来源页面嵌套层数异常")
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.count += 1
        if self.count > 50000:
            raise ProviderError("invalid_response", "来源页面节点数量异常")
        self.stack[-1].children.append(_Node(tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


class XunleiProvider(SubtitleProvider):
    """Xunlei's public subtitle catalogue, including actual release filenames."""
    name = "xunlei"
    hosts = ("api-shoulei-ssl.xunlei.com", "subtitle.v.geilijiasu.com")

    def _search(self, query):
        term = PurePosixPath(query.release_name or query.video_path).name or _query_text(query)
        payload = _json(self._request("https://api-shoulei-ssl.xunlei.com/oracle/subtitle?" +
                                     urlencode({"name": term}), max_bytes=PAGE_MAX_BYTES))
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise ProviderError("invalid_response", "迅雷字幕检索未返回有效结果")
        data = payload.get("data")
        if isinstance(data, dict):
            data = data.get("subtitles", data.get("list"))
        if not isinstance(data, list):
            raise ProviderError("invalid_response", "迅雷字幕结果格式已改变")
        result, seen = [], set()
        for item in _rows(data):
            if _machine(item):
                continue
            release = _text(item.get("name"))
            title = re.sub(r"^(?:中文|英文|双语|雙語)\[[^]]+\]", "", _text(item.get("simple_name"))) or release
            language = " ".join(_text(v) for v in item.get("languages", []) if isinstance(v, str))
            if language and not _chinese(language):
                continue
            url = _safe_url("", item.get("url"), ("subtitle.v.geilijiasu.com",))
            ident = _text(item.get("cid") or item.get("gcid"))
            if not url or not ident or ident in seen or not (title or release):
                continue
            facts = _identity(release + " " + title, work_titles=(query.title, query.original_title, *query.aliases))
            candidate = SubtitleCandidate(self.name, ident, title, url, release_name=release,
                language=_lang(language), subtitle_format=_format(_text(item.get("ext"))),
                metadata={"filename": release, "duration_ms": item.get("duration"),
                          "bilingual": _bilingual_hint(language + " " + release),
                          "source_page": "https://api-shoulei-ssl.xunlei.com/oracle/subtitle"}, **facts)
            if match_candidate(query, candidate).accepted:
                seen.add(ident)
                result.append(candidate)
        return _ordered(query, result)


class _KeyProvider(SubtitleProvider):
    api_base = ""
    key_header = "X-API-Key"

    def _headers(self):
        key = _text(self.config.get("api_key"))
        if not key:
            raise ProviderError("auth_required", "此来源需要免费个人 API Key，请在配置中补充")
        return {self.key_header: ("Bearer " if self.key_header == "Authorization" else "") + key,
                "Accept": "application/json"}

    def _api(self, path: str, params: dict[str, Any] | None = None):
        response = self._request(self.api_base + path + ("?" + urlencode(params) if params else ""),
                                 headers=self._headers(), max_bytes=PAGE_MAX_BYTES)
        payload = _json(response)
        if not isinstance(payload, dict):
            raise ProviderError("invalid_response", "字幕 API 返回结构已改变")
        if payload.get("error") or payload.get("success") is False or payload.get("status") is False:
            error = payload.get("error")
            code = _text(error.get("code")) if isinstance(error, dict) else _text(error)
            status = "auth_required" if re.search(r"auth|api.?key", code, re.I) else "rate_limited" if re.search(r"limit|quota", code, re.I) else "unavailable"
            raise ProviderError(status, "字幕 API 暂时无法提供检索，请检查授权与额度")
        return payload


class SubDLProvider(_KeyProvider):
    name = "subdl"
    hosts = ("subdl.com",)
    api_base = "https://api.subdl.com/api/v2/"
    key_header = "Authorization"

    def _search(self, query):
        params: dict[str, Any] = {"type": "tv" if query.media_type in {"tv", "series"} else "movie",
                                  "languages": "ZH,ZH_BG", "subs_per_page": min(30, self.max_candidates)}
        if query.imdb_id:
            params["imdb_id"] = query.imdb_id
        elif query.tmdb_id:
            params["tmdb_id"] = query.tmdb_id
        else:
            params["film_name"] = query.original_title or query.title
        for key in ("season", "episode"):
            if getattr(query, key) is not None:
                params[key] = getattr(query, key)
        if query.season is not None and query.episode is None:
            params["full_season"] = 1
        payload = self._api("subtitles/search", params)
        if not isinstance(payload.get("subtitles"), list):
            raise ProviderError("invalid_response", "SubDL 字幕列表格式已改变")
        titles = _rows(payload.get("results"))
        result = []
        for item in _rows(payload["subtitles"]):
            if _machine(item):
                continue
            language = _text(item.get("language") or item.get("lang"))
            if language and not _chinese(language):
                continue
            associated = [row for row in titles if item.get("sd_id") is not None and str(row.get("sd_id")) == str(item["sd_id"])]
            title_info = associated[0] if len(associated) == 1 else titles[0] if len(titles) == 1 and item.get("sd_id") is None else {}
            release = _release(item.get("releases") or item.get("release_name"))
            title = _text(title_info.get("name") or title_info.get("original_name")) or release
            ident = _text(item.get("n_id") or item.get("nId"))
            download = _safe_url("https://dl.subdl.com", item.get("url"), self.hosts)
            if not ident and not download:
                continue
            facts = _identity(release, work_titles=(query.title, query.original_title, *query.aliases))
            season = _integer(item.get("season"))
            episode = _integer(item.get("episode"))
            first, last = _integer(item.get("episode_from")), _integer(item.get("episode_end"))
            pack = bool(item.get("full_season")) or (first is not None and last is not None and first != last)
            if pack:
                # A pack does not certify the requested episode; archive filenames must.
                episode = None
            elif first is not None and first == last and first > 0:
                episode = first
            detail = _safe_url("https://subdl.com", item.get("subtitlePage"), self.hosts)
            candidate = SubtitleCandidate(self.name, ident or _text(item.get("name")) or download, title,
                download, detail, release, year=_integer(title_info.get("year")) or facts["year"],
                media_type=_text(title_info.get("type")), season=season if season is not None else facts["season"],
                episode=episode if episode is not None else (None if pack else facts["episode"]),
                language="cht" if language.upper() == "ZH_BG" else "chi" if language.upper() == "ZH" else _lang(language),
                subtitle_format=_format(_text(item.get("format")) + " " + release),
                metadata={"n_id": ident, "filename": _text(item.get("name")) or "subtitle.zip",
                    "imdb_id": _text(title_info.get("imdb_id")), "tmdb_id": _text(title_info.get("tmdb_id")),
                    "source_page": detail, "fps": item.get("fps") or item.get("framerate"), "season_pack": pack,
                    "bilingual": _bilingual_hint(language + " " + release)})
            if title and match_candidate(query, candidate).accepted:
                result.append(candidate)
        return _ordered(query, result)

    def download(self, candidate):
        _validate_candidate(candidate, self.name)
        ident = _text(candidate.metadata.get("n_id"))
        if ident:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", ident):
                raise ProviderError("invalid_candidate", "字幕编号无效")
            response = self._request(self.api_base + "subtitles/" + ident + "/download?format=zip",
                                     headers=self._headers())
            return _binary(response, "subtitle.zip")
        # Older records expose public CDN URLs; no API credential accompanies them.
        return super().download(candidate)


class SubSourceProvider(_KeyProvider):
    name = "subsource"
    hosts = ("subsource.net",)
    api_base = "https://api.subsource.net/api/v1/"

    def _search(self, query):
        params = {"searchType": "imdb", "imdb": query.imdb_id} if query.imdb_id else {
            "searchType": "text", "q": query.original_title or query.title}
        payload = self._api("movies/search", params)
        if not isinstance(payload.get("data"), list):
            raise ProviderError("invalid_response", "SubSource 作品列表格式已改变")
        result = []
        matched_movies = 0
        for movie in _rows(payload["data"]):
            title = _text(movie.get("title"))
            info = {"imdb_id": _text(movie.get("imdbId") or movie.get("imdb")),
                    "tmdb_id": _text(movie.get("tmdbId"))}
            year = _integer(movie.get("releaseYear"))
            kind = _text(movie.get("type"))
            movie_id = _integer(movie.get("movieId"))
            if not title or movie_id is None or not match_candidate(query, SubtitleCandidate(
                    self.name, str(movie_id), title, year=year, media_type=kind, metadata=info)).accepted:
                continue
            matched_movies += 1
            if matched_movies > 3:
                break
            params = {"movieId": movie_id, "language": "chinese_bg_code", "limit": min(100, self.max_candidates)}
            if query.season is not None:
                params["seasonNumber"] = query.season
            if query.episode is not None:
                params["episodeNumber"] = query.episode
            items = self._api("subtitles", params).get("data")
            if not isinstance(items, list):
                raise ProviderError("invalid_response", "SubSource 字幕列表格式已改变")
            for item in _rows(items):
                if _machine(item):
                    continue
                ident = _integer(item.get("subtitleId"))
                release = _release(item.get("releaseInfo"))
                language = _text(item.get("language"))
                if ident is None or (language and not _chinese(language)):
                    continue
                facts = _identity(release, work_titles=(query.title, query.original_title, *query.aliases))
                detail = _safe_url("https://subsource.net", item.get("link"), self.hosts)
                candidate = SubtitleCandidate(self.name, str(ident), title, detail_url=detail,
                    release_name=release, year=year or facts["year"], media_type=kind,
                    season=_integer(item.get("seasonNumber")) if item.get("seasonNumber") is not None else facts["season"],
                    episode=_integer(item.get("episodeNumber")) if item.get("episodeNumber") is not None else facts["episode"],
                    language=_lang(_text(item.get("comment")) + " " + language),
                    subtitle_format=_format(_text(item.get("format")) + " " + release),
                    metadata={**info, "source_page": detail, "filename": "subtitle.zip",
                              "bilingual": _bilingual_hint(release + " " + _text(item.get("comment")))})
                if match_candidate(query, candidate).accepted:
                    result.append(candidate)
            if len(result) >= self.max_candidates:
                break
        return _ordered(query, result)

    def download(self, candidate):
        _validate_candidate(candidate, self.name)
        if not re.fullmatch(r"\d{1,20}", candidate.candidate_id):
            raise ProviderError("invalid_candidate", "字幕编号无效")
        response = self._request(self.api_base + "subtitles/" + candidate.candidate_id + "/download",
                                 headers=self._headers())
        return _binary(response, "subtitle.zip")


class Subf2mProvider(SubtitleProvider):
    name = "subf2m"
    hosts = ("subf2m.co", "isubcdn.com")
    base = "https://subf2m.co"

    def _search(self, query):
        page = self._page(self.base + "/subtitles/searchbytitle?" + urlencode({"query": query.original_title or query.title, "l": ""}))
        tree, selections, observed_titles = _Tree(page).root, [], 0
        for node in tree.nodes("div"):
            if not node.has_class("title"):
                continue
            for link in node.nodes("a"):
                path, title = link.attrs.get("href", ""), link.text()
                if not re.fullmatch(r"/subtitles/[^/?#]+", path):
                    continue
                observed_titles += 1
                facts = _identity(title, work_titles=(query.title, query.original_title, *query.aliases))
                season_match = re.search(r"\s*[-(]\s*(?:the\s+)?(\w+)\s+(?:season|series)\)?", title, re.I)
                if season_match:
                    words = ("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
                             "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth").split()
                    label = season_match[1].lower()
                    facts["season"] = words.index(label) + 1 if label in words else _integer(label)
                    title = title[:season_match.start()].strip()
                seed = SubtitleCandidate(self.name, path, title, **facts)
                if match_candidate(query, seed).accepted and path not in [entry[0] for entry in selections]:
                    selections.append((path, title, facts))
        if not observed_titles:
            _unknown_search_page(page)
        result = []
        for path, title, facts in selections[:3]:
            has_traditional = False
            for language_path in ("chinese-bg-code", "big_5_code"):
                if language_path == "big_5_code" and not has_traditional:
                    continue
                listing = self._page(self.base + path + "/" + language_path)
                has_traditional = any(urlsplit(urljoin(self.base, link.get("href", ""))).path == path + "/big_5_code" for link in _links(listing))
                imdb = re.search(r"imdb\.com/title/(tt\d+)", listing)
                for node in _Tree(listing).root.nodes("li"):
                    if not node.has_class("item"):
                        continue
                    links = [link for link in node.nodes("a") if link.has_class("download")]
                    if not links:
                        continue
                    detail = _safe_url(self.base, links[0].attrs.get("href"), ("subf2m.co",))
                    if not detail or not re.search(r"/\d+$", urlsplit(detail).path):
                        continue
                    releases = [item.text() for ul in node.nodes("ul") if ul.has_class("scrolllist") for item in ul.nodes("li")]
                    release = " / ".join(releases)
                    if _machine({"title": node.text()}):
                        continue
                    release_facts = _identity(release, work_titles=(query.title, query.original_title, *query.aliases))
                    candidate = SubtitleCandidate(self.name, detail.rsplit("/", 1)[-1], title, detail_url=detail,
                        release_name=release, year=facts["year"] or release_facts["year"],
                        season=release_facts["season"] or facts["season"], episode=release_facts["episode"],
                        language="cht" if language_path == "big_5_code" else _lang(node.text()),
                        subtitle_format=_format(release),
                        metadata={"source_page": detail, "imdb_id": imdb[1] if imdb else "",
                                  "bilingual": _bilingual_hint(node.text())})
                    if match_candidate(query, candidate).accepted:
                        result.append(candidate)
                if len(result) >= self.max_candidates:
                    return _ordered(query, result)
        return _ordered(query, result)

    detail_pattern = r"/subtitles/[^/?#]+/(?:chinese-bg-code|big_5_code)/\d+"

    def supports_detail_url(self, url):
        return super().supports_detail_url(url) and urlsplit(url).hostname in {"subf2m.co", "www.subf2m.co"}

    def _lookup_detail(self, url):
        body = self._page(url)
        tree = _Tree(body).root
        title = next((node.text() for heading in tree.nodes("h1") for node in heading.nodes()
                      if node.attrs.get("itemprop") == "name"), "")
        releases = [child.text() for li in tree.nodes("li") if li.has_class("release")
                    for child in li.nodes("div")]
        release = " / ".join(releases)
        if not title or not release or _machine({"title": tree.text()}):
            return []
        work_title, header_year = _detail_work_title(title)
        facts = _identity(release, work_titles=(work_title,))
        if header_year is not None:
            facts["year"] = header_year
        imdb = re.search(r"imdb\.com/title/(tt\d+)", body)
        return [SubtitleCandidate(self.name, urlsplit(url).path.rsplit("/", 1)[-1], title,
            detail_url=url, release_name=release, language="cht" if "/big_5_code/" in url else _lang(release),
            subtitle_format=_format(release), metadata={"source_page": url,
                "imdb_id": imdb[1] if imdb else "", "bilingual": _bilingual_hint(release)}, **facts)]

    def download(self, candidate):
        _validate_candidate(candidate, self.name)
        detail = _safe_url(self.base, candidate.detail_url, ("subf2m.co",))
        if not detail:
            raise ProviderError("invalid_candidate", "字幕详情地址无效")
        for link in _links(self._page(detail)):
            if link.get("id") == "downloadButton":
                url = _safe_url(self.base, link.get("href"), self.hosts)
                if url:
                    return self._download_url(url, "subtitle.zip")
        raise ProviderError("user_action_required", "字幕详情未提供公开下载链接")


class R3SubProvider(SubtitleProvider):
    """Exports the website's public, unshifted subtitle preview, not gated ZIPs."""
    name = "r3sub"
    hosts = ("r3sub.com",)
    base = "https://www.r3sub.com"

    def _search(self, query):
        page = self._page(self.base + "/search.php?" + urlencode({"s": query.imdb_id or query.original_title or query.title, "type": "movie"}))
        details = []
        for link in _links(page):
            if "movie__title" not in link.get("class", "").split():
                continue
            url = _safe_url(self.base, link.get("href"), self.hosts)
            if url and urlsplit(url).path == "/show.php" and url not in details:
                details.append(url)
        if not details:
            _unknown_search_page(page)
        result = []
        for detail in details[:5]:
            result.extend(candidate for candidate in self._lookup_detail(detail) if match_candidate(query, candidate).accepted)
            if len(result) >= self.max_candidates:
                break
        return _ordered(query, result)

    def supports_detail_url(self, url):
        try:
            parts = urlsplit(url)
            params = parse_qs(parts.query, keep_blank_values=True)
            return bool(parts.scheme == "https" and parts.hostname in {"r3sub.com", "www.r3sub.com"}
                and not parts.username and not parts.password and parts.port in {None, 443}
                and parts.path == "/show.php" and not parts.fragment and set(params) == {"id"}
                and len(params["id"]) == 1 and re.fullmatch(r"[A-Za-z0-9]{3,32}", params["id"][0]))
        except (TypeError, ValueError):
            return False

    def _lookup_detail(self, detail):
        body = self._page(detail)
        title = next((link["text"].strip() for link in _links(body) if "movie__title" in link.get("class", "").split()), "")
        if not title:
            return []
        imdb = re.search(r"imdb\.com/title/(tt\d+)", body)
        info = {"imdb_id": imdb[1] if imdb else "", "source_page": detail}
        work_title, header_year = _detail_work_title(title)
        facts, result = _identity(title, work_titles=(work_title,)), []
        if header_year is not None:
            facts["year"] = header_year
        for link in _links(body):
            filename = link.get("data-fname", "")
            sid, mode = link.get("data-sid", ""), link.get("data-un", "")
            if not filename or not re.fullmatch(r"(?:fix_)?\d+", sid) or mode not in {"p", "r"}:
                continue
            if not filename.lower().endswith((".srt", ".ass", ".ssa")) or _machine({"name": filename}):
                continue
            # Unlabelled members still go to the real body-language classifier.
            if re.search(r"[._]en[._]", filename, re.I) and not _chinese(filename):
                continue
            file_facts = _identity(filename, work_titles=(work_title,))
            result.append(SubtitleCandidate(self.name, sid + ":" + filename, title, detail_url=detail,
                release_name=filename, year=facts["year"] or file_facts["year"],
                season=file_facts["season"], episode=file_facts["episode"], language=_lang(filename),
                subtitle_format="srt", metadata={**info, "filename": filename, "sid": sid, "preview_mode": mode,
                    "download_method": "public_preview_export"}))
        return result

    def download(self, candidate):
        _validate_candidate(candidate, self.name)
        sid, mode = _text(candidate.metadata.get("sid")), _text(candidate.metadata.get("preview_mode"))
        filename = _text(candidate.metadata.get("filename"))
        parts = PurePosixPath(filename.replace("\\", "/"))
        if (not re.fullmatch(r"(?:fix_)?\d+", sid) or mode not in {"p", "r"} or not filename
                or len(filename) > 1024 or "\x00" in filename or parts.is_absolute() or ".." in parts.parts):
            raise ProviderError("invalid_candidate", "字幕预览参数无效")
        response = self._request(self.base + "/sub_ajax.php", data=urlencode({"dasid": sid, "dafname": filename, "un": mode}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": candidate.detail_url})
        preview = _html(response)
        normalized = html.unescape(re.sub(r"<br\s*/?>\r?\n?", "\n", preview, flags=re.I))
        if not re.search(r"\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}", normalized):
            raise ProviderError("user_action_required", "来源未提供可导出的公开 SRT 预览")
        # R3's visible export creates SRT even when the original member was ASS.
        return normalized.encode("utf-8"), PurePosixPath(filename.replace("\\", "/")).stem + ".srt"


class Addic7edProvider(CookieWebsiteProvider):
    name = "addic7ed"
    hosts = ("addic7ed.com",)
    base = "https://www.addic7ed.com"

    base_url = base

    def _search(self, query):
        if query.media_type not in {"tv", "series"} or query.season is None:
            return []
        page = self._page(self.base + "/search.php?" + urlencode({"search": query.original_title or query.title, "Submit": "Search"}))
        show_ids = list(dict.fromkeys(re.findall(r"loadShow\((\d+),", page)))
        if not show_ids:
            # Search results link to show pages. Read only title-matched entries.
            for link in _links(page):
                match = re.fullmatch(r"/show/(\d+)", link.get("href", ""))
                if match and match_candidate(query, SubtitleCandidate(self.name, match[1], link["text"])).accepted:
                    show_ids.append(match[1])
        result = []
        for show_id in show_ids[:3]:
            detail = self.base + "/season/" + show_id + "/" + str(query.season)
            season_page = self._page(detail)
            if re.search(r"must (?:be logged in|log in)|please (?:log in|login)|login required", _plain(season_page), re.I):
                raise ProviderError("auth_required", "Addic7ed 要求登录，请配置来源站授权 Cookie")
            for row in _Tree(season_page).root.nodes("tr"):
                cells = [child for child in row.children if isinstance(child, _Node) and child.tag == "td"]
                if len(cells) < 6 or cells[5].text().casefold() != "completed" or not _chinese(cells[3].text()):
                    continue
                links = list(row.nodes("a"))
                source = next((re.fullmatch(r"/serie/([^/]+)/(\d+)/(\d+)(?:/.*)?", link.attrs.get("href", "")) for link in links if link.attrs.get("href", "").startswith("/serie/")), None)
                if source is None:
                    continue
                title, season, episode = unquote(source[1]).replace("_", " "), int(source[2]), int(source[3])
                if _integer(cells[0].text()) != season or _integer(cells[1].text()) != episode:
                    continue
                download = next((_safe_url(self.base, link.attrs.get("href"), self.hosts) for link in links
                    if re.fullmatch(r"/(?:updated|original)/\d+/\d+/\d+", link.attrs.get("href", ""))), "")
                if not download or _machine({"title": row.text()}):
                    continue
                release = f"{title}.S{season:02d}E{episode:02d}." + cells[4].text()
                candidate = SubtitleCandidate(self.name, urlsplit(download).path, title, download, detail,
                    release_name=release, media_type="series", season=season, episode=episode,
                    language=_lang(cells[3].text()), subtitle_format="srt",
                    metadata={"filename": release + ".srt", "source_page": detail})
                if match_candidate(query, candidate).accepted:
                    result.append(candidate)
            if len(result) >= self.max_candidates:
                break
        if not show_ids and re.search(r"must (?:be logged in|log in)|please (?:log in|login)|login required", _plain(page), re.I):
            raise ProviderError("auth_required", "Addic7ed 要求登录，请配置来源站授权 Cookie")
        if not show_ids and not any(re.fullmatch(r"/show/\d+", link.get("href", "")) for link in _links(page)):
            _unknown_search_page(page)
        return _ordered(query, result)

    def download(self, candidate):
        _validate_candidate(candidate, self.name)
        url = _safe_url(self.base, candidate.download_url, self.hosts)
        if not url:
            raise ProviderError("invalid_candidate", "字幕下载地址无效")
        response = self._request(url, headers={"Referer": candidate.detail_url})
        return _binary(response, candidate.metadata.get("filename", "subtitle.srt"))
