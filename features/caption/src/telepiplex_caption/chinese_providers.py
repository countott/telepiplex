"""Chinese catalogs using ordinary public pages and optional user sessions."""
from __future__ import annotations

import re
import urllib.parse

from .models import MediaQuery, SubtitleCandidate
from .providers import (
    CookieWebsiteProvider, PAGE_MAX_BYTES, ProviderError, _format, _identity,
    _json, _language, _links, _plain, _query_text,
)


class LwlTvProvider(CookieWebsiteProvider):
    name = "lwltv"
    base_url = "https://www.lwltv.com"
    hosts = ("lwltv.com",)

    def _search(self, query: MediaQuery) -> list[SubtitleCandidate]:
        page = self._page(self.base_url + "/search/" + urllib.parse.quote(_query_text(query), safe=""))
        results = []
        seen = set()
        for block in re.split(r'<div\b[^>]*class=["\'][^"\']*\bcard mb-3 shadow-sm\b[^"\']*["\'][^>]*>', page)[1:]:
            links = [row for row in _links(block) if re.fullmatch(r"/subtitles/\d+", row.get("href", ""))]
            if not links:
                continue
            link = links[0]
            identifier = link["href"].rsplit("/", 1)[-1]
            if identifier in seen:
                continue
            seen.add(identifier)
            title = _plain(link["text"])
            release = _plain(links[1]["text"]) if len(links) > 1 else title
            text = _plain(block)
            results.append(SubtitleCandidate(
                self.name, identifier, title, detail_url=self.base_url + link["href"],
                release_name=release, language=_language(text), subtitle_format=_format(text),
                metadata={"bilingual": "双语" in text, "filename": release},
                **_identity(release, work_titles=(query.title, query.original_title, *query.aliases))))
        if not results and not re.search(r"(?:没有找到|无搜索结果|共\s*[⁨⁩]*0[⁨⁩]*\s*条)", _plain(page)):
            raise ProviderError("invalid_response", "LWLTV 搜索页面结构已改变或暂不可用")
        return results

    def download(self, candidate: SubtitleCandidate) -> tuple[bytes, str]:
        if candidate.provider != self.name or not re.fullmatch(r"\d+", candidate.candidate_id):
            raise ProviderError("invalid_candidate", "LWLTV 字幕编号无效")
        page = self._page(candidate.detail_url)
        url = self._published_file(page, candidate.detail_url)
        if url:
            return self._download_url(url, str(candidate.metadata.get("filename") or ""))
        # Current UI requires an interactive Turnstile token per download.
        # A user cookie cannot substitute for that browser verification.
        if "turnstile" in page.lower() or "/challenges" in page:
            raise ProviderError("user_action_required", "LWLTV 此字幕需要在来源网页完成浏览器验证")
        raise ProviderError("invalid_response", "LWLTV 未提供可识别的公开下载链接")


class YySubProvider(CookieWebsiteProvider):
    """yysub.cc, without claiming official subtitle-group identity."""
    name = "yysub"
    base_url = "https://yysub.cc"
    hosts = ("yysub.cc",)

    def _search(self, query: MediaQuery) -> list[SubtitleCandidate]:
        page = self._page(self.base_url + "/search?" + urllib.parse.urlencode(
            {"keyword": _query_text(query), "type": "subtitle"}))
        results = []
        seen = set()
        for block in re.findall(r"<li\b[^>]*>(.*?)</li>", page, re.S | re.I):
            links = [row for row in _links(block) if re.fullmatch(r"/subtitle/\d+", row.get("href", ""))]
            if not links or "search-item" not in block:
                continue
            link = links[0]
            identifier = link["href"].rsplit("/", 1)[-1]
            if identifier in seen:
                continue
            seen.add(identifier)
            title_match = re.search(r'<strong\b[^>]*class=["\']list_title["\'][^>]*>(.*?)</strong>', block, re.S)
            title = _plain(title_match[1] if title_match else link["text"])
            title = re.sub(r"^\[[^\]]*字幕\]", "", title).strip()
            release_match = re.search(r"版本：\s*<span\b[^>]*>(.*?)</span>", block, re.S)
            release = _plain(release_match[1]) if release_match else ""
            text = _plain(block)
            results.append(SubtitleCandidate(
                self.name, identifier, title, detail_url=self.base_url + link["href"],
                release_name=release or title, language=_language(text), subtitle_format=_format(text),
                metadata={"bilingual": "中英" in text or "双语" in text},
                **_identity(release or title, work_titles=(query.title, query.original_title, *query.aliases))))
        if not results and not re.search(r"(?:没有找到|未找到|字幕\(0\)|字幕（0）)", _plain(page)):
            raise ProviderError("invalid_response", "YYSub 搜索页面结构已改变或暂不可用")
        return results

    def download(self, candidate: SubtitleCandidate) -> tuple[bytes, str]:
        if candidate.provider != self.name or not re.fullmatch(r"\d+", candidate.candidate_id):
            raise ProviderError("invalid_candidate", "YYSub 字幕编号无效")
        # This check is mandatory in the public download button's flow.
        url = self.base_url + "/subtitle/file/" + candidate.candidate_id
        payload = _json(self._request(url + "?action=check", max_bytes=PAGE_MAX_BYTES,
                                     headers={"Referer": candidate.detail_url}))
        status = payload.get("status")
        if status in {1001, "1001"}:
            raise ProviderError("auth_required", "YYSub 下载需要有效的网站登录 Cookie")
        if status in {5001, "5001"}:
            raise ProviderError("user_action_required", "YYSub 下载需要在来源网站完成极验验证")
        if status not in {1, "1"}:
            raise ProviderError("unavailable", "YYSub 未批准本次字幕下载，请检查网站会话或来源状态")
        return self._download_url(url, candidate.release_name + ".zip")
