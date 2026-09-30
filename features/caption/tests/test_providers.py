from __future__ import annotations

import io
import http.cookiejar
import json
import socket
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from telepiplex_caption.models import MediaQuery, SubtitleCandidate
from telepiplex_caption.providers import (
    AssrtProvider, HttpResponse, HttpTransport, OpenSubtitlesProvider, ProviderError,
    ShooterProvider, SubHDProvider, ZimukuProvider, _CheckedRedirect, _check_url,
    build_providers,
    _OriginCookies, provider_request_scope,
)


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(body, **headers):
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode()
    if isinstance(body, str):
        body = body.encode()
    return HttpResponse(body, headers)


ASSRT_PAGE = '''<html><div class="subitem">
<a class="introtitle" title="示例电影/Example.2020.1080p.BluRay" href="/xml/sub/123/123456.xml">示例电影</a>
<span>格式： SSA</span><span>语言：英 简 双语</span><span>下载次数：321次</span>
<a href="#" onclick="location.href='/download/123456/Example.2020.BluRay.zip';return false;">下载</a>
</div></html>'''


def test_assrt_public_search_and_download_without_key():
    transport = FakeTransport(response(ASSRT_PAGE), response(b"PK\x03\x04archive"))
    provider = AssrtProvider(transport=transport)
    result = provider.search(MediaQuery("示例电影", original_title="Example", year=2020))
    assert result.status == "ok"
    candidate, = result.candidates
    assert candidate.year == 2020
    assert candidate.language == "chi"
    assert candidate.subtitle_format == "ass"
    assert candidate.metadata["bilingual"] is True
    assert candidate.downloads == 321
    content, name = provider.download(candidate)
    assert content.startswith(b"PK")
    assert name == "Example.2020.BluRay.zip"
    assert "searchword=Example" in transport.calls[0][0]
    assert "Authorization" not in transport.calls[0][1].get("headers", {})


def test_assrt_api_resolves_fresh_signed_url_at_download():
    transport = FakeTransport(
        response({"status": 0, "sub": {"subs": [{"id": 123456, "native_name": "Example",
                  "videoname": "Example.2020.BluRay", "subtype": "Subrip(srt)",
                  "lang": {"desc": "简 双语", "langlist": {"langdou": True}}}]}}),
        response({"status": 0, "sub": {"subs": [{"url": "https://file0.assrt.net/download/fresh.zip?signature=new",
                  "filename": "Example.zip"}]}}),
        response(b"PK\x03\x04archive"),
    )
    provider = AssrtProvider({"token": "personal-secret"}, transport=transport)
    candidate, = provider.search(MediaQuery("Example")).candidates
    assert not candidate.download_url
    assert "personal-secret" not in transport.calls[0][0]
    assert transport.calls[0][1]["headers"]["Authorization"] == "Bearer personal-secret"
    assert provider.download(candidate)[1] == "Example.zip"
    assert "/sub/detail?id=123456" in transport.calls[1][0]
    assert "headers" not in transport.calls[2][1]


@pytest.mark.parametrize("code,status", [(20001, "auth_required"), (30900, "rate_limited"), (30002, "unavailable")])
def test_assrt_api_failure_is_not_no_results(code, status):
    provider = AssrtProvider({"token": "secret"}, transport=FakeTransport(response({"status": code})))
    result = provider.search(MediaQuery("Example"))
    assert result.status == status
    assert "secret" not in result.message


def test_assrt_page_change_is_visible():
    provider = AssrtProvider(transport=FakeTransport(response("<html>Maintenance</html>")))
    assert provider.search(MediaQuery("Example")).status == "invalid_response"


def test_html_challenge_is_not_a_subtitle_download():
    provider = AssrtProvider(transport=FakeTransport(response("<!doctype html><html>Just a moment...</html>")))
    with pytest.raises(ProviderError, match="浏览器验证") as error:
        provider.download(SubtitleCandidate("assrt", "1", "Example", download_url="https://assrt.net/download/one.zip"))
    assert error.value.status == "user_action_required"


def test_content_disposition_filename_is_sanitized():
    provider = AssrtProvider(transport=FakeTransport(response(b"subtitle", **{"Content-Disposition": 'attachment; filename="../../Example.chi.srt"'})))
    _, filename = provider.download(SubtitleCandidate("assrt", "1", "Example", download_url="https://assrt.net/download/one"))
    assert filename == "Example.chi.srt"


def test_legacy_utf8_filename_header_is_recovered():
    filename = '千与千寻.简体.srt'
    broken = filename.encode('utf-8').decode('latin-1')
    provider = AssrtProvider(transport=FakeTransport(response(b'subtitle', **{'Content-Disposition': f'attachment; filename="{broken}"'})))
    assert provider.download(SubtitleCandidate('assrt', '1', '千与千寻', download_url='https://assrt.net/download/one'))[1] == filename


def test_chinese_catalog_prefers_localized_title_for_japanese_original():
    transport = FakeTransport(response(ASSRT_PAGE))
    AssrtProvider(transport=transport).search(MediaQuery('千与千寻', original_title='千と千尋の神隠し', original_language='ja'))
    assert 'searchword=%E5%8D%83%E4%B8%8E%E5%8D%83%E5%AF%BB' in transport.calls[0][0]


def test_shooter_query_only_is_not_applicable():
    transport = FakeTransport()
    result = ShooterProvider(transport=transport).search(MediaQuery("Example"))
    assert result.status == "not_applicable"
    assert not transport.calls


@pytest.mark.parametrize("body", [b"\xff", b"-1", b"[]"])
def test_shooter_legacy_no_match(body):
    provider = ShooterProvider(transport=FakeTransport(response(body)))
    assert provider.search(MediaQuery("Example", shooter_hash=";".join(["a" * 32] * 4))).status == "no_results"


def test_shooter_exact_hash_evidence_and_delay_are_preserved():
    transport = FakeTransport(response([{"Desc": "", "Delay": 150,
                                         "Files": [{"Ext": "srt", "Link": "https://www.shooter.cn/file.srt"}]}]))
    provider = ShooterProvider(transport=transport)
    result = provider.search(MediaQuery("Example", shooter_hash=";".join(["a" * 32] * 4)))
    assert result.candidates[0].metadata == {"hash_match": True, "filename": "subtitle.srt", "delay_ms": 150}
    assert b"filehash=" in transport.calls[0][1]["data"]


SUBHD_PAGE = '''<html><div class="bg-white shadow-sm rounded-3 mb-4">
<a href='/a/ab12CD'>示例电影</a>
<a href='/a/ab12CD'>Example.2020.1080p.BluRay</a>
<span>简体 双语 ASS</span><div>发布人 translator</div></div></html>'''


def test_subhd_public_candidates_preserve_version():
    provider = SubHDProvider(transport=FakeTransport(response(SUBHD_PAGE)))
    candidate, = provider.search(MediaQuery("Example")).candidates
    assert candidate.title == "示例电影"
    assert candidate.release_name == "Example.2020.1080p.BluRay"
    assert candidate.language == "chi"
    assert candidate.subtitle_format == "ass"
    assert candidate.year == 2020
    assert candidate.detail_url == "https://subhd.tv/a/ab12CD"


def test_subhd_never_fabricates_download_preparation():
    transport = FakeTransport(response('<html><button data-sid="ab12CD">下载字幕文件</button></html>'))
    provider = SubHDProvider(transport=transport)
    with pytest.raises(ProviderError) as error:
        provider.download(SubtitleCandidate("subhd", "ab12CD", "Example", detail_url="https://subhd.tv/a/ab12CD"))
    assert error.value.status == "user_action_required"
    assert len(transport.calls) == 1


def test_subhd_can_follow_an_explicit_public_download_link():
    transport = FakeTransport(response('<a href="https://subhd.me/files/Example.zip">下载</a>'), response(b"PK\x03\x04x"))
    provider = SubHDProvider(transport=transport)
    body, filename = provider.download(SubtitleCandidate("subhd", "ab12CD", "Example", detail_url="https://subhd.tv/a/ab12CD"))
    assert filename == "Example.zip"
    assert body.startswith(b"PK")


def test_zimuku_search_failure_is_visible_instead_of_blanket_disabling():
    transport = FakeTransport(ProviderError("unavailable", "字幕来源返回 HTTP 404"))
    result = ZimukuProvider(transport=transport).search(MediaQuery("Example"))
    assert result.status == "unavailable"
    assert "/search?q=Example" in transport.calls[0][0]


def test_opensubtitles_missing_key_does_not_suppress_other_sources():
    providers = build_providers()
    names = [provider.name for provider in providers]
    assert len(names) == len(set(names))
    assert set(names) >= {"assrt", "shooter", "subhd", "zimuku", "opensubtitles", "xunlei", "r3sub", "subdl", "subsource", "subf2m", "addic7ed", "lwltv", "yysub"}
    assert set(names) >= {"nekomoe", "mingy", "kitauji", "haruhana", "local_archive"}
    assert next(p for p in providers if p.name == "opensubtitles").search(MediaQuery("Example")).status == "auth_required"


def test_opensubtitles_uses_actual_file_id_and_chinese_bilingual_language():
    transport = FakeTransport(response({"data": [{"id": "123", "attributes": {
        "language": "ze", "release": "Example.2020.BluRay", "download_count": 200,
        "feature_details": {"title": "Example", "year": 2020, "imdb_id": 12345},
        "files": [{"file_id": 9876, "file_name": "Example.chs.ass"}]}}]}),
        response({"link": "https://www.opensubtitles.com/file/one.ass", "file_name": "Example.chs.ass"}),
        response(b"[Script Info]"))
    provider = OpenSubtitlesProvider({"api_key": "secret"}, transport=transport)
    candidate, = provider.search(MediaQuery("Example", imdb_id="tt12345")).candidates
    assert "languages=zh-cn%2Czh-tw%2Cze%2Czh-ca" in transport.calls[0][0]
    assert "imdb_id=12345" in transport.calls[0][0]
    assert candidate.candidate_id == "123:9876"
    assert candidate.metadata["bilingual"] is True
    assert candidate.metadata["imdb_id"] == "tt12345"
    assert provider.download(candidate)[1] == "Example.chs.ass"
    assert json.loads(transport.calls[1][1]["data"]) == {"file_id": 9876}


def test_opensubtitles_episode_uses_series_identity_and_parent_title():
    transport = FakeTransport(response({"data": [{"id": "1", "attributes": {
        "language": "zh-cn", "release": "Example.S01E02", "feature_details": {
            "feature_type": "Episode", "title": "Chapter Two", "parent_title": "Example",
            "imdb_id": 999, "parent_imdb_id": 123, "tmdb_id": 888, "parent_tmdb_id": 456,
            "season_number": 1, "episode_number": 2}, "files": [{"file_id": 10}]}}]}))
    candidate, = OpenSubtitlesProvider({"api_key": "x"}, transport=transport).search(
        MediaQuery("Example", media_type="series", season=1, episode=2)).candidates
    assert candidate.title == 'Example'
    assert candidate.media_type == 'series'
    assert candidate.metadata['imdb_id'] == 'tt123'
    assert candidate.metadata['tmdb_id'] == '456'


def test_opensubtitles_rejects_other_languages_and_machine_translations():
    transport = FakeTransport(response({"data": [
        {"id": "1", "attributes": {"language": "en", "files": [{"file_id": 1}]}},
        {"id": "2", "attributes": {"language": "zh-cn", "ai_translated": True, "files": [{"file_id": 2}]}}
    ]}))
    assert OpenSubtitlesProvider({"api_key": "x"}, transport=transport).search(MediaQuery("Example")).status == "no_results"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1/x", "https://assrt.net.evil.example/x",
                                "https://user:password@assrt.net/x", "https://assrt.net:9000/x",
                                "https://assrt.net:not-a-port/file", "https://[invalid/file"])
def test_transport_rejects_unsafe_or_unrelated_urls(url):
    with pytest.raises(ProviderError) as error:
        _check_url(url, ("assrt.net",))
    assert error.value.status == "unsafe_url"


def test_transport_rejects_private_dns_result(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("10.0.0.1", 443))])
    with pytest.raises(ProviderError) as error:
        _check_url("https://assrt.net/file.zip", ("assrt.net",))
    assert error.value.status == "unsafe_url"


def test_redirect_does_not_leak_api_credentials(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("1.1.1.1", 443))])
    request = urllib.request.Request("https://api.assrt.net/download", headers={"Authorization": "Bearer secret", "Api-Key": "secret"})
    redirected = _CheckedRedirect(("assrt.net",)).redirect_request(
        request, None, 302, "Found", {}, "http://file0.assrt.net/file.zip")
    assert redirected.get_header("Authorization") is None
    assert redirected.get_header("Api-key") is None


def test_download_body_limit_is_enforced_even_without_content_length(monkeypatch):
    class Stream(io.BytesIO):
        headers = {}
        def geturl(self):
            return "https://assrt.net/file.zip"
    class Opener:
        def open(self, request, timeout):
            return Stream(b"x" * 1025)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("1.1.1.1", 443))])
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(ProviderError) as error:
        HttpTransport(max_bytes=1024).request("https://assrt.net/file.zip", allowed_hosts=("assrt.net",))
    assert error.value.status == "too_large"


def test_http_400_assrt_auth_error_is_explicit_and_secret_free(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 400, 'Bad request', {}, io.BytesIO(b'{"status":20001,"error":"secret"}'))
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(2, 1, 6, '', ('1.1.1.1', 443))])
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: Opener())
    with pytest.raises(ProviderError) as error:
        HttpTransport().request('https://api.assrt.net/v1/sub/search', allowed_hosts=('assrt.net',))
    assert error.value.status == 'auth_required'
    assert 'secret' not in error.value.message


def test_provider_failure_status_and_disabled_configuration():
    provider = AssrtProvider(transport=FakeTransport(ProviderError("rate_limited", "请求过多")))
    assert provider.search(MediaQuery("Example")).status == "rate_limited"
    provider = AssrtProvider({"enabled": False}, transport=FakeTransport())
    assert provider.search(MediaQuery("Example")).status == "disabled"


def test_non_english_title_uses_confirmed_chinese_alias():
    transport = FakeTransport(response(ASSRT_PAGE))
    AssrtProvider(transport=transport).search(MediaQuery('Your Name', original_title='君の名は。',
        original_language='ja', aliases=('你的名字', 'Your Name')))
    assert 'searchword=%E4%BD%A0%E7%9A%84%E5%90%8D%E5%AD%97' in transport.calls[0][0]


def test_subhd_normal_prepare_session_and_download_flow():
    transport = FakeTransport(
        response('<button class="subtitle-prepare-download" data-sid="abc">下载</button>'),
        response({'success': True, 'url': '/down/abc'}),
        response('<script>fetch("/api/sub/down")</script>'),
        response({'success': True, 'pass': True, 'url': 'https://dl.subhd.me/2026/test.ass'}),
        response(b'[Script Info]'),
    )
    provider = SubHDProvider({'cookie': 'user_session=secret'}, transport=transport)
    candidate = SubtitleCandidate('subhd', 'abc', 'Example', detail_url='https://subhd.tv/a/abc')
    assert provider.download(candidate) == (b'[Script Info]', 'test.ass')
    assert json.loads(transport.calls[1][1]['data']) == {'sid': 'abc'}
    assert '/api/sub/down' in transport.calls[3][0]
    assert all(call[1]['cookie_jar'] is provider.cookie_jar for call in transport.calls[:4])
    assert all(call[1]['headers']['Cookie'] == 'user_session=secret' for call in transport.calls[:4])
    assert 'Cookie' not in transport.calls[-1][1]['headers']
    assert 'cookie_jar' not in transport.calls[-1][1]


def test_subhd_denied_download_never_requests_file():
    transport = FakeTransport(response('<button class="subtitle-prepare-download">下载</button>'),
        response({'success': True, 'url': '/down/abc'}),
        response('<script>fetch("/api/sub/down")</script>'),
        response({'success': True, 'pass': False, 'url': 'https://dl.subhd.me/denied.ass'}))
    with pytest.raises(ProviderError) as error:
        SubHDProvider(transport=transport).download(SubtitleCandidate('subhd', 'abc', 'Example', detail_url='https://subhd.tv/a/abc'))
    assert error.value.status == 'user_action_required'
    assert len(transport.calls) == 4


def test_zimuku_catalog_and_published_signed_download():
    transport = FakeTransport(response('<div><a href="/subs/11.html">Example</a></div>'),
        response('<table><tr><td><a href="/detail/22.html" title="Example.2020.BluRay.rar">Example</a> ASS/SSA</td><td><img alt="简体中文字幕"><img alt="双语字幕"></td></tr></table>'),
        response('<a id="down1" href="/dld/22.html">下载</a>'),
        response('<a href="/download/opaque-signature/svr/d0">电信高速下载</a>'),
        response(b'Rar!archive', **{'Content-Disposition': 'attachment; filename="Example.rar"'}))
    provider = ZimukuProvider(transport=transport)
    result = provider.search(MediaQuery('Example', year=2020))
    candidate, = result.candidates
    assert (candidate.year, candidate.language, candidate.subtitle_format) == (2020, 'chi', 'ass')
    assert candidate.metadata['bilingual'] is True
    assert provider.download(candidate) == (b'Rar!archive', 'Example.rar')
    assert transport.calls[-1][0].endswith('/download/opaque-signature/svr/d0')


@pytest.mark.parametrize('target', ['https://dl.subhd.me/file', 'http://subhd.tv/file'])
def test_redirect_strips_all_custom_credentials_and_cookie(target, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('1.1.1.1', 443))])
    request = urllib.request.Request('https://subhd.tv/file', headers={
        'X-API-Key': 'secret', 'Authorization': 'Bearer secret', 'Cookie': 'a=secret',
        'X-Custom-Token': 'secret', 'User-Agent': 'caption', 'Accept': '*/*'})
    redirected = _CheckedRedirect(('subhd.tv', 'subhd.me')).redirect_request(request, None, 302, 'Found', {}, target)
    assert set(k.lower() for k in redirected.headers) == {'user-agent', 'accept'}


def test_cookie_jar_does_not_reintroduce_credentials_on_cross_origin_redirect():
    jar = http.cookiejar.CookieJar()
    jar.set_cookie(http.cookiejar.Cookie(0, 'session', 'secret', None, False, '.subhd.tv', True, True,
        '/', True, True, None, True, None, None, {}))
    handler = _OriginCookies(jar, 'https://subhd.tv/prepare')
    same = handler.http_request(urllib.request.Request('https://subhd.tv/down/abc'))
    assert same.get_header('Cookie') == 'session=secret'
    cross = handler.http_request(urllib.request.Request('https://cdn.subhd.tv/file'))
    assert cross.get_header('Cookie') is None


def test_provider_total_deadline_stops_next_network_call(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: pytest.fail('expired call must not resolve DNS'))
    with provider_request_scope(0):
        with pytest.raises(ProviderError) as error:
            HttpTransport().request('https://subhd.tv/file', allowed_hosts=('subhd.tv',))
    assert error.value.status == 'timeout'


def test_website_cookie_header_injection_is_rejected():
    provider = SubHDProvider({'cookie': 'secret\r\nX-Injection: secret'}, transport=FakeTransport())
    assert provider.search(MediaQuery('Example')).status == 'invalid_configuration'


def test_http_403_challenge_has_explicit_user_action_state(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, 'Forbidden', {}, io.BytesIO(b'<html>Just a moment...</html>'))
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('1.1.1.1', 443))])
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: Opener())
    with pytest.raises(ProviderError) as error:
        HttpTransport().request('https://subhd.tv/down/1', allowed_hosts=('subhd.tv',))
    assert error.value.status == 'user_action_required'


@pytest.mark.parametrize('url', [
    'http://zimuku.org/detail/22.html', 'https://user:secret@zimuku.org/detail/22.html',
    'https://zimuku.org/detail/22.html?secret=x', 'https://zimuku.org/detail/22.html#fragment',
    'https://zimuku.org.evil.test/detail/22.html', 'https://zimuku.org:444/detail/22.html',
    'https://s.zimuku.org/download/22', 'https://zimuku.org/subs/22.html',
    'https://zimuku.org/detail/../22.html',
])
def test_detail_lookup_only_accepts_exact_https_public_detail_urls(url):
    transport = FakeTransport()
    provider = ZimukuProvider(transport=transport)
    assert not provider.supports_detail_url(url)
    assert provider.lookup_detail(url).status == 'invalid_query'
    assert not transport.calls


def test_zimuku_detail_lookup_reads_actual_page_identity_and_version():
    page = '''<h1>Friends.S01.720p.BluRay.x264-PSYCHD.rar</h1>
    <h2>老友记 第一季 / Friends (1994)</h2><ul class="subinfo clearfix">
    <li>字幕格式：ASS/SSA</li><li><img alt="简体中文字幕"><img alt="双语字幕"></li>
    <li>下载次数：123</li></ul>'''
    transport = FakeTransport(response(page))
    provider = ZimukuProvider(transport=transport)
    candidate, = provider.lookup_detail('https://srtku.com/detail/2265.html').candidates
    assert (candidate.year, candidate.season, candidate.language, candidate.downloads) == (1994, 1, 'chi', 123)
    assert candidate.title == 'Friends.S01.720p.BluRay.x264-PSYCHD.rar'
    assert candidate.detail_url == 'https://zimuku.org/detail/2265.html'


def test_subhd_detail_lookup_does_not_invent_query_identity():
    page = '''<h1><a>无敌浩克 The Incredible Hulk (2008)</a></h1>
    <a href="https://www.imdb.com/title/tt0800080">IMDb</a>
    <div class="f16 subtitle-edition">Hulk.2008.1080p.BluRay</div>
    <div class="subtitle-metadata-tags">简体 双语 ASS</div>'''
    provider = SubHDProvider(transport=FakeTransport(response(page)))
    result = provider.lookup_detail('https://subhd.me/a/XncJNb')
    candidate, = result.candidates
    assert candidate.year == 2008
    assert candidate.metadata['imdb_id'] == 'tt0800080'
    assert candidate.subtitle_format == 'ass'
    assert 'Hulk' in candidate.title


def test_disabled_detail_source_never_uses_network():
    transport = FakeTransport()
    result = SubHDProvider({'enabled': False}, transport=transport).lookup_detail('https://subhd.tv/a/abc')
    assert result.status == 'disabled'
    assert not transport.calls


def test_cookie_seed_preserves_server_rotation_for_following_request():
    transport = FakeTransport(response('first'), response('second'))
    provider = SubHDProvider({'cookie': 'session=old'}, transport=transport)
    provider._request('https://subhd.tv/first')
    for cookie in provider.cookie_jar:
        if cookie.name == 'session':
            cookie.value = 'rotated'
    provider._request('https://subhd.tv/second')
    assert transport.calls[1][1]['headers']['Cookie'] == 'session=rotated'


def test_transport_exception_does_not_chain_secret_query_or_headers(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            raise urllib.error.URLError('upstream failed: api_key=personal-secret')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('1.1.1.1', 443))])
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: Opener())
    with pytest.raises(ProviderError) as caught:
        HttpTransport().request('https://assrt.net/search', allowed_hosts=('assrt.net',))
    assert caught.value.__suppress_context__ is True
    assert 'personal-secret' not in str(caught.value)


def test_factory_catalog_configuration_is_isolated_and_local_has_no_implicit_roots():
    providers = build_providers({'providers': {'mingy': {'enabled': False}, 'local_archive': {}}})
    assert len(providers) == 18
    assert next(p for p in providers if p.name == 'mingy').search(MediaQuery('Example')).status == 'disabled'
    local = next(p for p in providers if p.name == 'local_archive')
    assert local.search(MediaQuery('Example')).status == 'not_configured'
    assert local.config.get('roots', []) == []


def test_title_year_is_not_invented_as_release_year():
    from telepiplex_caption.providers import _identity
    assert _identity('Reply.1988.S01.1080p', work_titles=('请回答1988', 'Reply 1988'))['year'] is None
    assert _identity('Reply.1988.2015.S01', work_titles=('Reply 1988',))['year'] == 2015
    assert _identity('Blade.Runner.2049.2017.1080p', work_titles=('Blade Runner 2049',))['year'] == 2017
    assert _identity('2001.A.Space.Odyssey.1968', work_titles=('2001: A Space Odyssey',))['year'] == 1968
    assert _identity('Reply.1988.2023.S01', work_titles=('Reply 1988',))['year'] == 2023


def test_subhd_episode_query_falls_back_once_to_title_for_season_packs():
    page = '''<div class="bg-white shadow-sm rounded-3 mb-4">
    <a href="/a/pack1">请回答1988</a><a href="/a/pack1">Reply.1988.S01.KOREAN.1080p.NF.WEBRip</a>
    <span>简体 ASS</span><div>发布人 translator</div></div>'''
    transport = FakeTransport(response('没有找到'), response(page))
    query = MediaQuery('请回答1988', original_title='Reply 1988', year=2015, original_language='ko', media_type='series', season=1, episode=1)
    candidate, = SubHDProvider(transport=transport).search(query).candidates
    assert candidate.year is None
    assert candidate.season == 1
    assert candidate.metadata['bilingual'] is False
    assert 'S01E01' in transport.calls[0][0]
    assert 'S01E01' not in transport.calls[1][0]
    assert len(transport.calls) == 2


def test_subhd_detail_title_year_uses_actual_parenthesized_publication_year():
    page = '''<h1>请回答1988 응답하라 1988 (2015)</h1>
    <div class="subtitle-edition">Reply.1988.S01.1080p.NF.WEBRip</div>
    <div class="subtitle-metadata-tags">简体 ASS</div>'''
    candidate, = SubHDProvider(transport=FakeTransport(response(page))).lookup_detail('https://subhd.tv/a/pack1').candidates
    assert candidate.year == 2015


def test_subhd_irrelevant_episode_results_trigger_title_fallback():
    wrong = '''<div class="bg-white shadow-sm rounded-3 mb-4">
    <a href="/a/anniversary">请回答1988 十周年MT (2025)</a><a href="/a/anniversary">Reply.1988.10th.Anniversary.S01E01.2025</a>
    <span>简体 ASS</span><div>发布人 translator</div></div>'''
    original = '''<div class="bg-white shadow-sm rounded-3 mb-4">
    <a href="/a/original">请回答1988</a><a href="/a/original">Reply.1988.2015.S01.NF.WEBRip</a>
    <span>简体 ASS</span><div>发布人 translator</div></div>'''
    transport = FakeTransport(response(wrong), response(original))
    query = MediaQuery('请回答1988', original_title='Reply 1988', year=2015,
                       original_language='ko', media_type='series', season=1, episode=1)
    result = SubHDProvider(transport=transport).search(query)
    assert result.status == 'ok'
    assert [(item.candidate_id, item.year) for item in result.candidates] == [('original', 2015), ('anniversary', 2025)]
    assert len(transport.calls) == 2


def test_subhd_operational_failure_does_not_trigger_more_search_requests():
    transport = FakeTransport(ProviderError('unavailable', '字幕来源返回 HTTP 526'))
    result = SubHDProvider(transport=transport).search(MediaQuery('请回答1988', season=1, episode=1))
    assert result.status == 'unavailable'
    assert '526' in result.message
    assert len(transport.calls) == 1


@pytest.mark.parametrize('http_error', [False, True])
@pytest.mark.parametrize('operation_limit', [None, 0.5])
def test_transport_stops_continuous_drip_at_cooperative_deadline(monkeypatch, http_error, operation_limit):
    import telepiplex_caption.providers as providers
    clock = [0.0]
    monkeypatch.setattr(providers, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    class Drip:
        headers = {}
        calls = 0
        closed = False
        def read1(self, count):
            self.calls += 1
            clock[0] += 0.35  # Always below the socket's idle timeout.
            return b'x'
        def read(self, count=-1):
            pytest.fail('bulk read can run forever while bytes keep arriving')
        def close(self):
            self.closed = True
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()
        def geturl(self):
            return 'https://assrt.net/file.zip'
    stream = Drip()
    class Opener:
        def open(self, request, timeout):
            if http_error:
                raise urllib.error.HTTPError(request.full_url, 503, 'Unavailable', {}, stream)
            return stream
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('1.1.1.1', 443))])
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a: Opener())
    with provider_request_scope(operation_limit if operation_limit is not None else 60):
        with pytest.raises(ProviderError) as error:
            HttpTransport(timeout=1).request('https://assrt.net/file.zip', allowed_hosts=('assrt.net',))
    assert error.value.status == 'timeout'
    assert stream.calls == (2 if operation_limit is not None else 3)
    assert stream.closed


def test_transport_rechecks_deadline_after_dns_before_open(monkeypatch):
    import telepiplex_caption.providers as providers
    clock = [0.0]
    monkeypatch.setattr(providers, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    def dns(*args, **kwargs):
        clock[0] = 2
        return [(2, 1, 6, '', ('1.1.1.1', 443))]
    class Opener:
        def open(self, request, timeout):
            pytest.fail('DNS has already exhausted request deadline')
    monkeypatch.setattr(socket, 'getaddrinfo', dns)
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a: Opener())
    with pytest.raises(ProviderError) as error:
        HttpTransport(timeout=1).request('https://assrt.net/file.zip', allowed_hosts=('assrt.net',))
    assert error.value.status == 'timeout'


def test_error_response_reads_only_bounded_prefix_and_closes(monkeypatch):
    class Stream(io.BytesIO):
        requested = []
        def read1(self, count):
            self.requested.append(count)
            return super().read1(count)
    stream = Stream(b'x' * 16384)
    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 503, 'Unavailable', {}, stream)
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('1.1.1.1', 443))])
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a: Opener())
    with pytest.raises(ProviderError) as error:
        HttpTransport().request('https://assrt.net/file.zip', allowed_hosts=('assrt.net',))
    assert error.value.status == 'unavailable'
    assert stream.requested == [8192]
    assert stream.closed


@pytest.mark.parametrize('code', [301, 302, 303, 307, 308])
def test_redirect_discards_body_and_uses_remaining_deadline(monkeypatch, code):
    import telepiplex_caption.providers as providers
    clock = [0.0]
    monkeypatch.setattr(providers, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    def dns(*args, **kwargs):
        clock[0] += 0.3
        return [(2, 1, 6, '', ('1.1.1.1', 443))]
    class Stream(io.BytesIO):
        def read(self, *args):
            pytest.fail('redirect response must not be drained')
    stream = Stream(b'an unbounded redirect body')
    calls = []
    def open_request(request, timeout):
        calls.append((request.full_url, timeout))
        return 'redirected'
    monkeypatch.setattr(socket, 'getaddrinfo', dns)
    handler = _CheckedRedirect(('assrt.net',), deadline=1)
    handler.parent = SimpleNamespace(open=open_request)
    request = urllib.request.Request('https://assrt.net/download')
    request.timeout = 1
    result = getattr(handler, f'http_error_{code}')(request, stream, code, 'Moved', {'location': '/actual.zip'})
    assert result == 'redirected'
    assert calls == [('https://assrt.net/actual.zip', pytest.approx(0.7))]
    assert stream.closed


def test_transport_without_read1_never_waits_for_a_bulk_chunk(monkeypatch):
    import telepiplex_caption.providers as providers
    clock = [0.0]
    monkeypatch.setattr(providers, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    class Stream:
        counts = []
        def read(self, count):
            self.counts.append(count)
            clock[0] += 0.4
            return b'x'
    stream = Stream()
    with pytest.raises(ProviderError) as error:
        providers._read_bounded_response(stream, 1024, 1)
    assert error.value.status == 'timeout'
    assert stream.counts == [1, 1, 1]
