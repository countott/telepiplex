"""Bounded external subtitle retrieval from public sites and documented APIs.

Provider metadata is evidence for ranking, never proof of subtitle language or
quality. Downloaded bodies must pass archive.py and quality.py before use.
"""
from __future__ import annotations

import html
import http.cookiejar
import http.cookies
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from contextlib import contextmanager
from contextvars import ContextVar
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any, Mapping

from .models import MediaQuery, SubtitleCandidate


USER_AGENT = "telepiplex-caption/1.0"
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
PAGE_MAX_BYTES = 2 * 1024 * 1024
_REQUEST_DEADLINE: ContextVar[float | None] = ContextVar("caption_provider_deadline", default=None)


@contextmanager
def provider_request_scope(timeout_seconds: float):
    """Share a cooperative deadline across one provider's network operation.

    DNS resolution and an already active socket read cannot be force-cancelled
    by urllib; the engine keeps their worker's capacity reserved until it exits.
    """
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    previous = _REQUEST_DEADLINE.get()
    token = _REQUEST_DEADLINE.set(min(deadline, previous) if previous is not None else deadline)
    try:
        yield
    finally:
        _REQUEST_DEADLINE.reset(token)


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    status: str
    candidates: tuple[SubtitleCandidate, ...] = ()
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "status": self.status,
                "candidates": [item.to_dict() for item in self.candidates],
                "message": self.message}


class ProviderError(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class HttpResponse:
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)
    url: str = ""


def _check_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except (ValueError, TypeError):
        raise ProviderError("unsafe_url", "字幕来源返回了不受支持的下载地址") from None
    host = (parsed.hostname or "").lower().rstrip(".")
    if (parsed.scheme not in {"http", "https"} or not host or parsed.username
            or parsed.password or port not in {None, 80, 443}):
        raise ProviderError("unsafe_url", "字幕来源返回了不受支持的下载地址")
    if not any(host == item or host.endswith("." + item) for item in allowed_hosts):
        raise ProviderError("unsafe_url", "字幕下载地址不属于此来源的已知服务域名")
    try:
        addresses = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ProviderError("unavailable", "字幕来源域名暂时无法解析") from exc
    if any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ProviderError("unsafe_url", "字幕来源地址解析到非公网网络")


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts: tuple[str, ...], *, deadline: float | None = None):
        super().__init__()
        self.hosts = hosts
        self.deadline = deadline

    def http_error_302(self, req, fp, code, msg, headers):
        # urllib otherwise drains redirects with an unbounded fp.read(). There
        # is no connection reuse to preserve here; discard and close the body.
        # Delegate all Location validation and loop detection to the stdlib.
        class BodyDiscarder:
            def read(self, *args):
                return b""
            def __getattr__(self, name):
                return getattr(fp, name)
        try:
            return super().http_error_302(req, BodyDiscarder(), code, msg, headers)
        except urllib.error.HTTPError:
            raise  # The transport reads and closes its bounded error prefix.
        except BaseException:
            fp.close()
            raise

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self.deadline is not None:
            _remaining_request_time(self.deadline)
        _check_url(newurl, self.hosts)
        if self.deadline is not None:
            req.timeout = _remaining_request_time(self.deadline)
        # API credentials never travel to a different origin or an HTTP URL.
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and ((old.scheme, old.hostname, old.port or 443) !=
                           (new.scheme, new.hostname, new.port or 443) or new.scheme != "https"):
            # Default-deny custom headers on another origin. Besides the known
            # API-key spellings, providers can use arbitrary credential names.
            safe = {"user-agent", "accept", "accept-language", "accept-encoding", "range"}
            for name in list(redirected.headers) + list(redirected.unredirected_hdrs):
                if name.lower() not in safe:
                    redirected.remove_header(name)
        return redirected


class _OriginCookies(urllib.request.HTTPCookieProcessor):
    """Persist ordinary website sessions without sending them to a file CDN."""

    def __init__(self, cookiejar, origin: str):
        super().__init__(cookiejar)
        self.origin = urllib.parse.urlsplit(origin).netloc.lower()

    def _same_origin(self, request):
        parts = urllib.parse.urlsplit(request.full_url)
        return parts.scheme == "https" and parts.netloc.lower() == self.origin

    def http_request(self, request):
        return super().http_request(request) if self._same_origin(request) else request

    https_request = http_request

    def http_response(self, request, response):
        return super().http_response(request, response) if self._same_origin(request) else response

    https_response = http_response


def _remaining_request_time(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProviderError("timeout", "字幕来源检索或下载已超时")
    return remaining


def _read_bounded_response(response, limit: int, deadline: float, *, truncate=False) -> bytes:
    """Avoid read(n), which can keep waiting while a server drips more bytes.

    HTTPResponse.read1 performs at most one buffered/raw read per call. There
    is no stable public urllib socket timeout setter: an active read can finish
    after the deadline, with the socket's existing idle timeout still applied.
    Streams without read1 use one byte per read to avoid waiting for a chunk.
    """
    read_one = getattr(response, "read1", None)
    chunks, size = [], 0
    while size < limit + (0 if truncate else 1):
        _remaining_request_time(deadline)
        count = min(16 * 1024, limit + (0 if truncate else 1) - size)
        chunk = read_one(count) if callable(read_one) else response.read(1)
        _remaining_request_time(deadline)
        if not chunk:
            break
        size += len(chunk)
        if size > limit and not truncate:
            raise ProviderError("too_large", "字幕响应超过大小限制")
        chunks.append(chunk)
    return b"".join(chunks)


class HttpTransport:
    """No credentials in URLs/errors, no unbounded bodies or arbitrary redirects."""

    def __init__(self, timeout: float = 15, max_bytes: int = DEFAULT_MAX_BYTES):
        self.timeout = min(max(float(timeout), 1), 60)
        self.max_bytes = max(1024, min(int(max_bytes), 100 * 1024 * 1024))

    def request(self, url: str, *, allowed_hosts: tuple[str, ...],
                headers: Mapping[str, str] | None = None, data: bytes | None = None,
                max_bytes: int | None = None,
                cookie_jar: http.cookiejar.CookieJar | None = None) -> HttpResponse:
        deadline = time.monotonic() + self.timeout
        if (operation_deadline := _REQUEST_DEADLINE.get()) is not None:
            deadline = min(deadline, operation_deadline)
        _remaining_request_time(deadline)
        _check_url(url, allowed_hosts)
        limit = min(max_bytes or self.max_bytes, self.max_bytes)
        request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})}
        handlers = [_CheckedRedirect(allowed_hosts, deadline=deadline)]
        if cookie_jar is not None:
            handlers.append(_OriginCookies(cookie_jar, url))
        opener = urllib.request.build_opener(*handlers)
        request = urllib.request.Request(url, headers=request_headers, data=data)
        try:
            with opener.open(request, timeout=_remaining_request_time(deadline)) as response:
                if int(response.headers.get("Content-Length", "0") or 0) > limit:
                    raise ProviderError("too_large", "字幕响应超过大小限制")
                body = _read_bounded_response(response, limit, deadline)
                return HttpResponse(body, dict(response.headers), response.geturl())
        except urllib.error.HTTPError as exc:
            status = {401: "auth_required", 403: "access_denied", 429: "rate_limited"}.get(
                exc.code, "unavailable")
            # ASSRT reports invalid credentials and quota inside JSON even
            # when the HTTP status is 400/500. Never echo the remote body.
            try:
                error_body = _read_bounded_response(exc, 8192, deadline, truncate=True)
                _html(HttpResponse(error_body))
                details = json.loads(error_body)
                if isinstance(details, dict) and details.get("status") in {1, 20001}:
                    status = "auth_required"
                elif isinstance(details, dict) and details.get("status") == 30900:
                    status = "rate_limited"
            except ProviderError as error:
                raise error from None
            except TimeoutError:
                raise ProviderError("timeout", "字幕来源检索或下载已超时") from None
            except (ValueError, OSError):
                pass
            finally:
                exc.close()
            raise ProviderError(status, f"字幕来源返回 HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            # A URL-based API can carry a key or signed query in urllib's
            # exception object. Do not expose it through traceback chaining.
            raise ProviderError("unavailable", "字幕来源请求失败或超时") from None


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._current = {key: value or "" for key, value in attrs}
            self._current["text"] = ""

    def handle_data(self, data):
        if self._current is not None:
            self._current["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._current is not None:
            self.links.append(self._current)
            self._current = None


def _links(value: str) -> list[dict[str, str]]:
    parser = _Links()
    parser.feed(value)
    return parser.links


def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", value))).strip()


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value not in {None, ""} else None
    except (ValueError, TypeError):
        return None


def _format(value: str) -> str:
    if re.search(r"\b(?:ass|ssa)\b", value, re.I):
        return "ass"
    if re.search(r"\b(?:srt|subrip)\b", value, re.I):
        return "srt"
    return ""


def _language(value: str) -> str:
    if re.search(r"简|簡|\b(?:chs|chi|zh-cn|zh-hans)\b", value, re.I):
        return "chi"
    if re.search(r"繁|\b(?:cht|zh-tw|zh-hant)\b", value, re.I):
        return "cht"
    return ""


def _identity(title: str, *, work_titles: tuple[str, ...] = ()) -> dict[str, Any]:
    # A four-digit title is not a publication date (Reply 1988, 1917, 2049).
    # In ambiguous releases leave year unverified; never copy the query year.
    title_years = {value for name in work_titles
                   for value in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", name)}
    year = next((value for value in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", title)
                 if value not in title_years), None)
    episode = re.search(r"\bS(\d{1,2})[ ._-]*E(\d{1,3})\b", title, re.I)
    season = re.search(r"\bS(\d{1,2})\b", title, re.I)
    return {"year": int(year) if year else None,
            "season": int(episode[1]) if episode else int(season[1]) if season else None,
            "episode": int(episode[2]) if episode else None}


def _query_text(query: MediaQuery) -> str:
    # Chinese catalogs usually index localized titles for Japanese/Korean and
    # other non-English films; their original scripts often have no entries.
    prefer_local = query.original_language and query.original_language.casefold() not in {"en", "eng", "english"}
    localized = next((str(value) for value in (query.title, *query.aliases)
                      if re.search(r"[\u3400-\u9fff]", str(value))), query.title)
    title = (localized if prefer_local else query.original_title or query.title).strip()
    if query.season is not None:
        title += f" S{query.season:02d}"
        if query.episode is not None:
            title += f"E{query.episode:02d}"
    return title


def _filename(response: HttpResponse, fallback: str) -> str:
    disposition = next((v for k, v in response.headers.items()
                        if k.lower() == "content-disposition"), "")
    encoded = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", disposition, re.I)
    normal = re.search(r'filename="([^"]+)"|filename=([^;]+)', disposition, re.I)
    value = (urllib.parse.unquote(encoded[1].strip(' "')) if encoded else
             (normal[1] or normal[2]).strip(' "') if normal else fallback)
    # Some Chinese mirrors put raw UTF-8 bytes into the legacy HTTP filename
    # field, which urllib exposes as ISO-8859-1. Recover only a valid roundtrip.
    if normal and not encoded:
        try:
            value = value.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    # Only a logical filename is returned; no provider path can reach the disk.
    return PurePosixPath(value.replace("\\", "/")).name or "subtitle.zip"


def _html(response: HttpResponse) -> str:
    value = response.body.decode("utf-8", errors="replace")
    if any(marker in value.lower() for marker in (
            "cf-chl-", "just a moment...", "g-recaptcha", "h-captcha")):
        raise ProviderError("user_action_required", "字幕来源要求浏览器验证，请在来源网站完成验证")
    return value


def _json(response: HttpResponse) -> Any:
    try:
        return json.loads(response.body)
    except (ValueError, UnicodeDecodeError) as exc:
        _html(response)  # Identify challenge pages instead of claiming zero matches.
        raise ProviderError("invalid_response", "字幕来源返回了无法识别的数据") from exc


class SubtitleProvider:
    name = ""
    hosts: tuple[str, ...] = ()
    detail_pattern = ""

    def __init__(self, config: Mapping[str, Any] | None = None, *,
                 transport: HttpTransport | None = None, max_candidates: int = 15):
        self.config = dict(config or {})
        self.transport = transport or HttpTransport()
        self.max_candidates = max(1, min(int(max_candidates), 50))

    def search(self, query: MediaQuery) -> ProviderResult:
        if self.config.get("enabled", True) is False:
            return ProviderResult(self.name, "disabled", message="此字幕来源已停用")
        try:
            candidates = tuple(self._search(query))[:self.max_candidates]
            return ProviderResult(self.name, "ok" if candidates else "no_results", candidates)
        except ProviderError as exc:
            return ProviderResult(self.name, exc.status, message=exc.message)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            return ProviderResult(self.name, "invalid_response", message="字幕来源数据格式已改变")

    def _search(self, query: MediaQuery) -> list[SubtitleCandidate]:
        raise NotImplementedError

    def supports_detail_url(self, url: str) -> bool:
        try:
            parts = urllib.parse.urlsplit(url)
            host = (parts.hostname or "").lower()
            return bool(self.detail_pattern and parts.scheme == "https" and
                        not parts.username and not parts.password and not parts.query and not parts.fragment and
                        parts.port in {None, 443} and
                        any(host in {name, "www." + name} for name in self.hosts) and
                        re.fullmatch(self.detail_pattern, parts.path))
        except (TypeError, ValueError):
            return False

    def lookup_detail(self, url: str) -> ProviderResult:
        if self.config.get("enabled", True) is False:
            return ProviderResult(self.name, "disabled", message="此字幕来源已停用")
        if not self.supports_detail_url(url):
            return ProviderResult(self.name, "invalid_query", message="此来源不支持该字幕详情链接")
        try:
            candidates = tuple(self._lookup_detail(url))[:self.max_candidates]
            if not candidates:
                raise ProviderError("invalid_response", "字幕详情页未包含可识别的字幕资料")
            return ProviderResult(self.name, "ok", candidates)
        except ProviderError as exc:
            return ProviderResult(self.name, exc.status, message=exc.message)
        except (ValueError, TypeError, KeyError, AttributeError):
            return ProviderResult(self.name, "invalid_response", message="字幕来源详情页格式已改变")

    def _lookup_detail(self, url: str) -> list[SubtitleCandidate]:
        raise ProviderError("not_applicable", "此来源暂不支持详情链接")

    def _request(self, url: str, **kwargs) -> HttpResponse:
        return self.transport.request(url, allowed_hosts=self.hosts, **kwargs)

    def _page(self, url: str) -> str:
        return _html(self._request(url, max_bytes=PAGE_MAX_BYTES))

    def download(self, candidate: SubtitleCandidate) -> tuple[bytes, str]:
        if candidate.provider != self.name:
            raise ProviderError("invalid_candidate", "字幕候选与来源不一致")
        if not candidate.download_url:
            raise ProviderError("user_action_required", "此字幕需在来源详情页完成下载验证")
        return self._download_url(candidate.download_url, candidate.metadata.get("filename", ""))

    def _download_url(self, url: str, filename: str = "") -> tuple[bytes, str]:
        response = self._request(url)
        start = response.body[:512].lstrip().lower()
        if start.startswith((b"<!doctype html", b"<html", b"<?xml")):
            _html(response)
            raise ProviderError("user_action_required", "来源返回网页，请在字幕详情页检查下载条件")
        fallback = filename or urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
        return response.body, _filename(response, fallback)


class CookieWebsiteProvider(SubtitleProvider):
    """Use an optional user session only on the selected website origin."""
    base_url = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cookie_jar = http.cookiejar.CookieJar()
        self._seeded_cookie = ""

    def _request(self, url: str, **kwargs) -> HttpResponse:
        headers = dict(kwargs.pop("headers", {}) or {})
        parts = urllib.parse.urlsplit(url)
        origin = urllib.parse.urlsplit(self.base_url)
        same_origin = (parts.scheme, parts.netloc.lower()) == ("https", origin.netloc.lower())
        if same_origin:
            for key, name in (("cookie", "Cookie"), ("user_agent", "User-Agent")):
                value = str(self.config.get(key) or "")
                if "\r" in value or "\n" in value or len(value) > 16384:
                    raise ProviderError("invalid_configuration", "字幕网站会话配置格式无效")
                if value and key == "cookie":
                    # Seed a browser-provided session once. Server Set-Cookie
                    # updates must survive prepare/download and may rotate it.
                    if value != self._seeded_cookie:
                        parsed = http.cookies.SimpleCookie()
                        try:
                            parsed.load(value)
                        except http.cookies.CookieError:
                            raise ProviderError("invalid_configuration", "字幕网站 Cookie 格式无效") from None
                        if not parsed:
                            raise ProviderError("invalid_configuration", "字幕网站 Cookie 格式无效")
                        for key_name, morsel in parsed.items():
                            self.cookie_jar.set_cookie(http.cookiejar.Cookie(
                                0, key_name, morsel.value, None, False, origin.hostname,
                                False, False, "/", True, True, None, True, None, None, {}))
                        self._seeded_cookie = value
                    # Expose the effective header to transports, while the real
                    # cookie processor keeps response/session state current.
                    request = urllib.request.Request(url)
                    self.cookie_jar.add_cookie_header(request)
                    if request.get_header("Cookie"):
                        headers["Cookie"] = request.get_header("Cookie")
                elif value:
                    headers[name] = value
            kwargs["cookie_jar"] = self.cookie_jar
        return super()._request(url, headers=headers, **kwargs)

    def _published_file(self, page: str, page_url: str) -> str:
        for link in _links(page):
            url = urllib.parse.urljoin(page_url, link.get("href", ""))
            if re.search(r"\.(?:zip|rar|7z|ass|ssa|srt)(?:\?|$)", url, re.I):
                return url
        return ""


class AssrtProvider(SubtitleProvider):
    """Official token API when configured; ordinary public pages otherwise."""
    name = "assrt"
    hosts = ("assrt.net", "makedie.me")

    def _api(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self._request("https://api.assrt.net/v1/" + endpoint + "?" +
                                 urllib.parse.urlencode(params), max_bytes=PAGE_MAX_BYTES,
                                 headers={"Authorization": "Bearer " + str(self.config["token"]),
                                          "Accept": "application/json"})
        result = _json(response)
        status = _integer(result.get("status"))
        if status != 0:
            code = ("auth_required" if status in {1, 20001} else
                    "rate_limited" if status == 30900 else "unavailable")
            raise ProviderError(code, "ASSRT API 无法完成请求，请检查授权或服务配额")
        return result

    def _search(self, query: MediaQuery) -> list[SubtitleCandidate]:
        terms = _query_text(query)
        if query.year and query.media_type in {"movie", "film"}:
            terms += " " + str(query.year)
        if self.config.get("token"):
            payload = self._api("sub/search", {"q": terms,
                                             "cnt": min(self.max_candidates, 15), "filelist": 1})
            results = []
            for row in payload.get("sub", {}).get("subs", []):
                title = str(row.get("native_name") or row.get("videoname") or "")
                release = str(row.get("videoname") or title)
                language = row.get("lang") or {}
                desc = str(language.get("desc", ""))
                results.append(SubtitleCandidate(
                    self.name, str(row["id"]), title,
                    detail_url=f"https://assrt.net/xml/sub/{int(row['id']) // 1000}/{row['id']}.xml",
                    release_name=release, language=_language(desc),
                    subtitle_format=_format(str(row.get("subtype", ""))),
                    metadata={"api": True, "bilingual": bool((language.get("langlist") or {}).get("langdou")),
                              "source": str(row.get("release_site") or ""),
                              "machine_translated": bool(row.get("vote_machine_translate")),
                              "attribution": "字幕服务由 assrt.net 提供"},
                    **_identity(release, work_titles=(query.title, query.original_title, *query.aliases))))
            return results
        # ASSRT's default sort can fill the first page with newly uploaded,
        # weakly related words. Its public relevance sort plus known film year
        # exposes exact older titles within our deliberately bounded budget.
        page = self._page("https://assrt.net/sub/?" + urllib.parse.urlencode({"searchword": terms, "sort": "relevance"}))
        results = []
        for block in re.split(r'class=["\']subitem["\']', page)[1:]:
            links = _links(block)
            detail = next((link for link in links if "introtitle" in link.get("class", "")), None)
            if not detail:
                continue
            identifier = re.search(r"/xml/sub/\d+/(\d+)\.xml", detail.get("href", ""))
            if not identifier:
                continue
            title = html.unescape(detail.get("title") or detail["text"]).strip()
            download = re.search(r"location\.href\s*=\s*['\"]([^'\"]+/download/[^'\"]+|/download/[^'\"]+)['\"]", block)
            text = _plain(block.split("</div>\n\t</div>", 1)[0])
            version = re.search(r"版本：\s*<b>(.*?)</b>", block, re.S)
            release = _plain(version[1]) if version else title
            count = re.search(r"下载次数：\s*(\d+)", text)
            results.append(SubtitleCandidate(
                self.name, identifier[1], title,
                download_url=urllib.parse.urljoin("https://assrt.net", html.unescape(download[1])) if download else "",
                detail_url=urllib.parse.urljoin("https://assrt.net", detail["href"]),
                release_name=release, language=_language(text), subtitle_format=_format(text),
                downloads=int(count[1]) if count else 0,
                metadata={"bilingual": "双语" in text, "attribution": "字幕服务由 assrt.net 提供"},
                **_identity(release, work_titles=(query.title, query.original_title, *query.aliases))))
        if not results and "subitem" not in page and not any(x in page for x in ("未找到", "没有找到", "没有搜索到")):
            raise ProviderError("invalid_response", "ASSRT 页面结构已改变或搜索暂时不可用")
        return results

    def download(self, candidate: SubtitleCandidate) -> tuple[bytes, str]:
        if candidate.metadata.get("api"):
            if candidate.provider != self.name or not self.config.get("token"):
                raise ProviderError("auth_required", "ASSRT API 下载需要配置个人 Token")
            payload = self._api("sub/detail", {"id": candidate.candidate_id})
            rows = payload.get("sub", {}).get("subs", [])
            if not rows or not rows[0].get("url"):
                raise ProviderError("unavailable", "ASSRT 未返回字幕下载地址")
            return self._download_url(rows[0]["url"], str(rows[0].get("filename") or ""))
        return super().download(candidate)


class ShooterProvider(SubtitleProvider):
    name = "shooter"
    hosts = ("shooter.cn",)

    def _search(self, query: MediaQuery) -> list[SubtitleCandidate]:
        if not query.shooter_hash:
            raise ProviderError("not_applicable", "射手哈希检索需要本地视频文件")
        if not re.fullmatch(r"[a-fA-F0-9]{32}(?:;[a-fA-F0-9]{32}){3}", query.shooter_hash):
            raise ProviderError("invalid_query", "射手视频哈希格式无效")
        payload = urllib.parse.urlencode({"filehash": query.shooter_hash,
                                         "pathinfo": query.release_name or query.title,
                                         "format": "json", "lang": "Chn"}).encode()
        response = self._request("https://www.shooter.cn/api/subapi.php", data=payload,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"},
                                 max_bytes=PAGE_MAX_BYTES)
        # The legacy endpoint explicitly returns byte FF when there is no match.
        if response.body.strip() in {b"\xff", b"-1", b"[]"}:
            return []
        rows = _json(response)
        if not isinstance(rows, list):
            raise ProviderError("invalid_response", "射手哈希服务返回了无法识别的数据")
        results = []
        for index, row in enumerate(rows):
            for file_index, item in enumerate(row.get("Files", [])):
                extension = str(item.get("Ext", "")).lower().lstrip(".")
                if extension not in {"srt", "ass", "ssa"} or not item.get("Link"):
                    continue
                results.append(SubtitleCandidate(
                    self.name, f"{index}-{file_index}", str(row.get("Desc") or query.title),
                    download_url=str(item["Link"]), release_name=query.release_name,
                    subtitle_format="ass" if extension == "ssa" else extension,
                    metadata={"hash_match": True, "filename": "subtitle." + extension,
                              "delay_ms": _integer(row.get("Delay")) or 0}))
        return results


class SubHDProvider(CookieWebsiteProvider):
    name = "subhd"
    base_url = "https://subhd.tv"
    detail_pattern = r"/a/[A-Za-z0-9]+"
    hosts = ("subhd.tv", "subhd.me", "subhdtw.com", "subhd.one", "subhd.top", "subhd.cc", "subhd.com")

    def _lookup_detail(self, url: str) -> list[SubtitleCandidate]:
        path = urllib.parse.urlsplit(url).path
        detail_url = self.base_url + path
        page = self._page(detail_url)
        title_match = re.search(r"<h1\b[^>]*>(.*?)</h1>", page, re.S | re.I)
        release_match = re.search(r'<div\b[^>]*class=["\'][^"\']*\bsubtitle-edition\b[^"\']*["\'][^>]*>(.*?)</div>', page, re.S)
        if not title_match or not release_match:
            raise ProviderError("invalid_response", "SubHD 详情缺少片名或片源版本")
        title, release = _plain(title_match[1]), _plain(release_match[1])
        tags = re.search(r'<div\b[^>]*class=["\'][^"\']*\bsubtitle-metadata-tags\b[^"\']*["\'][^>]*>(.*?)</div>', page, re.S)
        text = _plain(tags[1]) if tags else ""
        header_year = re.search(r"\(((?:19|20)\d{2})\)\s*$", title)
        work_title = title[:header_year.start()].strip() if header_year else title
        identity = _identity(release, work_titles=(work_title,))
        if header_year:
            identity["year"] = int(header_year[1])
        imdb = re.search(r'https://(?:www\.)?imdb\.com/title/(tt\d+)', page)
        return [SubtitleCandidate(self.name, path.rsplit("/", 1)[-1], title,
            detail_url=detail_url, release_name=release, language=_language(text), subtitle_format=_format(text),
            metadata={"bilingual": "双语" in text, "imdb_id": imdb[1] if imdb else ""}, **identity)]

    def _search(self, query: MediaQuery) -> list[SubtitleCandidate]:
        from .matching import match_candidate
        terms = _query_text(query)
        results = self._search_page(terms, query)
        # TV catalogs often index whole-season packs or E01 rather than S01E01.
        # One broader title lookup improves recall; all identity/episode and
        # content checks still apply to every returned file.
        if (query.season is not None or query.episode is not None) and not any(
                match_candidate(query, candidate).accepted for candidate in results):
            title_terms = _query_text(replace(query, season=None, episode=None))
            if title_terms != terms:
                results = self._search_page(title_terms, query) + results
        return list({candidate.candidate_id: candidate for candidate in results}.values())

    def _search_page(self, terms: str, query: MediaQuery) -> list[SubtitleCandidate]:
        page = self._page(self.base_url + "/search/" + urllib.parse.quote(terms, safe=""))
        results = []
        seen = set()
        for block in re.split(r'<div class="bg-white shadow-sm rounded-3 mb-4">', page)[1:]:
            links = [link for link in _links(block) if re.fullmatch(r"/a/[A-Za-z0-9]+", link.get("href", ""))]
            if not links:
                continue
            detail = links[0]
            identifier = detail["href"].rsplit("/", 1)[-1]
            if identifier in seen:
                continue
            seen.add(identifier)
            title = _plain(detail["text"])
            release = _plain(links[1]["text"]) if len(links) > 1 else title
            text = _plain(block.split("发布人", 1)[0])
            results.append(SubtitleCandidate(
                self.name, identifier, title, detail_url="https://subhd.tv" + detail["href"],
                release_name=release, language=_language(text), subtitle_format=_format(text),
                metadata={"bilingual": "双语" in text},
                **_identity(release, work_titles=(query.title, query.original_title, *query.aliases))))
        if not results and not any(x in page for x in ("没有找到", "没有搜索", "暂无", "没有字幕")) and not re.search(r"共\s*0\s*条", _plain(page)):
            raise ProviderError("invalid_response", "SubHD 页面结构已改变或搜索暂时不可用")
        return results

    def download(self, candidate: SubtitleCandidate) -> tuple[bytes, str]:
        if candidate.provider != self.name:
            raise ProviderError("invalid_candidate", "字幕候选与来源不一致")
        if candidate.download_url:
            return super().download(candidate)
        page = self._page(candidate.detail_url)
        direct = self._published_file(page, candidate.detail_url)
        if direct:
            return self._download_url(direct)
        if not re.search(r'class=["\'][^"\']*subtitle-prepare-download', page):
            raise ProviderError("user_action_required", "SubHD 下载页面已改变或需要网站验证")
        if not re.fullmatch(r"[A-Za-z0-9]+", candidate.candidate_id):
            raise ProviderError("invalid_candidate", "SubHD 字幕编号无效")
        # The site's published JS explicitly performs these three operations.
        # prepare-download sets a server session; never fabricate that cookie.
        headers = {"Content-Type": "application/json", "Referer": candidate.detail_url}
        body = json.dumps({"sid": candidate.candidate_id}).encode()
        prepared = _json(self._request(self.base_url + "/api/sub/prepare-download",
                                     data=body, headers=headers, max_bytes=PAGE_MAX_BYTES))
        target = prepared.get("url")
        if prepared.get("success") is not True or not isinstance(target, str) or not re.fullmatch(r"/down/[A-Za-z0-9]+", target):
            raise ProviderError("user_action_required", "SubHD 未完成下载准备，请检查网站会话或验证")
        landing = self._page(self.base_url + target)
        if "/api/sub/down" not in landing:
            raise ProviderError("invalid_response", "SubHD 下载页面结构已改变")
        payload = _json(self._request(self.base_url + "/api/sub/down", data=body,
                                    headers=headers, max_bytes=PAGE_MAX_BYTES))
        if payload.get("success") is not True or payload.get("pass") is not True:
            raise ProviderError("user_action_required", "SubHD 需要在网站完成下载验证或恢复登录")
        url = payload.get("url")
        if not isinstance(url, str) or not url:
            raise ProviderError("invalid_response", "SubHD 未返回字幕文件地址")
        return self._download_url(url)


class ZimukuProvider(CookieWebsiteProvider):
    name = "zimuku"
    base_url = "https://zimuku.org"
    detail_pattern = r"/detail/\d+\.html"
    hosts = ("zimuku.org", "srtku.com", "zmk.pw")

    def _lookup_detail(self, url: str) -> list[SubtitleCandidate]:
        path = urllib.parse.urlsplit(url).path
        detail_url = self.base_url + path
        page = self._page(detail_url)
        title_match = re.search(r"<h1\b[^>]*>(.*?)</h1>", page, re.S | re.I)
        info_match = re.search(r'<ul\b[^>]*class=["\'][^"\']*\bsubinfo\b[^"\']*["\'][^>]*>(.*?)</ul>', page, re.S)
        if not title_match or not info_match:
            raise ProviderError("invalid_response", "字幕库详情缺少片名或字幕资料")
        title = _plain(title_match[1])
        text = _plain(info_match[1]) + " " + " ".join(re.findall(r'alt=["\']([^"\']+)["\']', info_match[1]))
        work = re.search(r"<h2\b[^>]*>(.*?)</h2>", page, re.S | re.I)
        work_name = _plain(work[1]) if work else ""
        header_year = re.search(r"\(((?:19|20)\d{2})\)\s*$", work_name)
        work_titles = (work_name[:header_year.start()].strip(),) if header_year else (work_name,)
        identity = _identity(title, work_titles=work_titles)
        if header_year:
            identity["year"] = int(header_year[1])
        count = re.search(r"下载次数：\s*(\d+)", text)
        return [SubtitleCandidate(self.name, path.rsplit("/", 1)[-1].split(".")[0], title,
            detail_url=detail_url, release_name=title, language=_language(text), subtitle_format=_format(text),
            downloads=int(count[1]) if count else 0, metadata={"bilingual": "双语" in text}, **identity)]

    def _search(self, query: MediaQuery) -> list[SubtitleCandidate]:
        page = self._page(self.base_url + "/search?" + urllib.parse.urlencode({"q": _query_text(query)}))
        # Search publishes work catalog links; catalog rows contain the actual
        # subtitle versions. Ignore recommendations outside the result section.
        catalogs = []
        for link in _links(page):
            path = link.get("href", "")
            if re.fullmatch(r"/subs/\d+\.html", path) and path not in catalogs:
                catalogs.append(path)
        known_titles = (query.title, query.original_title, *query.aliases)
        results = self._catalog(page, work_titles=known_titles)
        for path in catalogs[:3]:
            results.extend(self._catalog(self._page(self.base_url + path), work_titles=known_titles))
            if len(results) >= self.max_candidates:
                break
        if not results and not any(word in page for word in ("没有找到", "未找到", "无搜索结果", "没有搜索到")):
            raise ProviderError("invalid_response", "字幕库搜索页面结构已改变或检索暂不可用")
        return list({row.candidate_id: row for row in results}.values())

    def _catalog(self, page: str, *, work_titles: tuple[str, ...] = ()) -> list[SubtitleCandidate]:
        results = []
        for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, re.S | re.I):
            links = [link for link in _links(row) if re.fullmatch(r"/detail/\d+\.html", link.get("href", ""))]
            if not links:
                continue
            link = links[0]
            title = _plain(link.get("title") or link["text"])
            text = _plain(row) + " " + " ".join(re.findall(r'alt=["\']([^"\']+)["\']', row))
            results.append(SubtitleCandidate(
                self.name, link["href"].split("/")[-1].split(".")[0], title,
                detail_url=self.base_url + link["href"], release_name=title,
                language=_language(text), subtitle_format=_format(text),
                metadata={"bilingual": "双语" in text}, **_identity(title, work_titles=work_titles)))
        return results

    def download(self, candidate: SubtitleCandidate) -> tuple[bytes, str]:
        if candidate.provider != self.name or not re.fullmatch(r"\d+", candidate.candidate_id):
            raise ProviderError("invalid_candidate", "字幕库字幕编号无效")
        detail = self._page(candidate.detail_url)
        expected = f"/dld/{candidate.candidate_id}.html"
        link = next((row["href"] for row in _links(detail) if row.get("href") == expected), "")
        if not link:
            raise ProviderError("user_action_required", "字幕库未提供下载入口，请检查来源网页或网站会话")
        page = self._page(self.base_url + link)
        for row in _links(page):
            path = row.get("href", "")
            if path.startswith("/download/"):
                # Signed URL copied intact from the ordinary download page.
                return self._download_url(self.base_url + path, candidate.title)
        raise ProviderError("user_action_required", "字幕库下载需要网站验证或暂不可用")


class OpenSubtitlesProvider(SubtitleProvider):
    name = "opensubtitles"
    hosts = ("opensubtitles.com", "opensubtitles.org", "osdb.link")

    def _headers(self) -> dict[str, str]:
        if not self.config.get("api_key"):
            raise ProviderError("auth_required", "OpenSubtitles 中文分类检索需要个人 API Key")
        return {"Api-Key": str(self.config["api_key"]), "Accept": "application/json",
                "User-Agent": str(self.config.get("user_agent") or USER_AGENT)}

    def _api(self, endpoint: str, *, params: dict[str, Any] | None = None,
             payload: dict[str, Any] | None = None, bearer: str = "") -> Any:
        headers = self._headers()
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        url = "https://api.opensubtitles.com/api/v1/" + endpoint
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return _json(self._request(url, headers=headers, data=data, max_bytes=PAGE_MAX_BYTES))

    def _search(self, query: MediaQuery) -> list[SubtitleCandidate]:
        params: dict[str, Any] = {"languages": "zh-cn,zh-tw,ze,zh-ca", "order_by": "download_count",
                                  "order_direction": "desc", "ai_translated": "exclude", "machine_translated": "exclude"}
        if query.imdb_id:
            params["imdb_id"] = query.imdb_id.removeprefix("tt")
        elif query.tmdb_id:
            params["tmdb_id"] = query.tmdb_id
        else:
            params["query"] = query.original_title or query.title
        if query.year:
            params["year"] = query.year
        if query.season is not None:
            params["season_number"] = query.season
        if query.episode is not None:
            params["episode_number"] = query.episode
        payload = self._api("subtitles", params=params)
        results = []
        for row in payload.get("data", []):
            attrs = row.get("attributes") or {}
            lang = str(attrs.get("language", ""))
            if lang not in {"zh-cn", "zh-tw", "ze", "zh-ca"}:
                continue
            if attrs.get("ai_translated") or attrs.get("machine_translated"):
                continue
            feature = attrs.get("feature_details") or {}
            kind = str(feature.get("feature_type") or "").casefold()
            media_type = "series" if kind in {"episode", "tvshow", "tv", "series"} else "movie" if kind in {"movie", "film"} else ""
            imdb_id = str((feature.get("parent_imdb_id") if media_type == "series" else None) or feature.get("imdb_id") or "")
            if imdb_id and not imdb_id.startswith("tt"):
                imdb_id = "tt" + imdb_id
            tmdb_id = str((feature.get("parent_tmdb_id") if media_type == "series" else None) or feature.get("tmdb_id") or "")
            for item in attrs.get("files") or []:
                if not item.get("file_id"):
                    continue
                filename = str(item.get("file_name") or "subtitle.srt")
                results.append(SubtitleCandidate(
                    self.name, str(row["id"]) + ":" + str(item["file_id"]),
                    str(feature.get("parent_title") or feature.get("title") or feature.get("movie_name") or attrs.get("release") or filename),
                    detail_url=str(attrs.get("url") or ""), release_name=str(attrs.get("release") or filename),
                    media_type=media_type,
                    year=_integer(feature.get("year")), season=_integer(feature.get("season_number")),
                    episode=_integer(feature.get("episode_number")),
                    subtitle_format=_format(filename), language=_language(lang),
                    downloads=_integer(attrs.get("download_count")) or 0,
                    metadata={"file_id": item["file_id"], "filename": filename,
                              "bilingual": lang == "ze", "imdb_id": imdb_id,
                              "tmdb_id": tmdb_id,
                              "hearing_impaired": bool(attrs.get("hearing_impaired")),
                              "fps": attrs.get("fps")}))
        return results

    def download(self, candidate: SubtitleCandidate) -> tuple[bytes, str]:
        if candidate.provider != self.name or not candidate.metadata.get("file_id"):
            raise ProviderError("invalid_candidate", "OpenSubtitles 文件编号缺失")
        bearer = str(self.config.get("token") or "")
        if not bearer and self.config.get("username") and self.config.get("password"):
            result = self._api("login", payload={"username": self.config["username"], "password": self.config["password"]})
            bearer = str(result.get("token") or "")
            if not bearer:
                raise ProviderError("auth_required", "OpenSubtitles 登录未成功")
        payload = self._api("download", payload={"file_id": candidate.metadata["file_id"]}, bearer=bearer)
        if not payload.get("link"):
            raise ProviderError("rate_limited", "OpenSubtitles 未返回下载地址，请检查账户下载配额")
        return self._download_url(str(payload["link"]), str(payload.get("file_name") or candidate.metadata.get("filename") or ""))


def build_providers(config: Mapping[str, Any] | None = None) -> list[SubtitleProvider]:
    # Provider modules import shared helpers here; keep registration lazy.
    from .chinese_providers import LwlTvProvider, YySubProvider
    from .extra_providers import (Addic7edProvider, R3SubProvider, SubDLProvider,
                                  SubSourceProvider, Subf2mProvider, XunleiProvider)
    from .catalog_providers import (HaruhanaProvider, KitaujiProvider, LocalArchiveProvider,
                                    MingYProvider, NekomoeProvider)
    config = dict(config or {})
    settings = config.get("providers") or {}
    transport = HttpTransport(config.get("request_timeout_seconds", 15),
                              config.get("max_download_bytes", DEFAULT_MAX_BYTES))
    return [provider(settings.get(provider.name, {}), transport=transport,
                     max_candidates=config.get("max_candidates_per_provider", 15))
            for provider in (AssrtProvider, ShooterProvider, SubHDProvider, ZimukuProvider,
                             OpenSubtitlesProvider, XunleiProvider, R3SubProvider,
                             SubDLProvider, SubSourceProvider, Subf2mProvider,
                             Addic7edProvider, LwlTvProvider, YySubProvider,
                             NekomoeProvider, MingYProvider, KitaujiProvider,
                             HaruhanaProvider, LocalArchiveProvider)]
