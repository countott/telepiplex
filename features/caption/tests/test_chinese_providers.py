from __future__ import annotations

import json
import pytest

from telepiplex_caption.chinese_providers import LwlTvProvider, YySubProvider
from telepiplex_caption.models import MediaQuery, SubtitleCandidate
from telepiplex_caption.providers import HttpResponse, ProviderError


class FakeTransport:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    def request(self, url, **kwargs):
        self.calls.append((url, kwargs))
        body = self.payloads.pop(0)
        if isinstance(body, dict):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode()
        return HttpResponse(body)


LWL_PAGE = '''<div class="card mb-3 shadow-sm"><h6><a href="/subtitles/123">星际穿越</a></h6>
<p><a href="/subtitles/123">Interstellar.2014.1080p.BluRay.简英.ass</a></p>
<span>双语</span><span>简体</span><span>ASS</span></div>'''
YY_PAGE = '''<ul><li><div class="clearfix search-item"><a href="/subtitle/43044">
<strong class="list_title">[电影字幕]星际穿越(Interstellar)</strong>[简体/繁体/英文/中英]</a>
<p>版本：<span class="f4">Interstellar.2014.BluRay</span></p></div></li></ul>'''


def test_lwltv_search_preserves_bilingual_ass_release_evidence():
    result = LwlTvProvider(transport=FakeTransport(LWL_PAGE)).search(MediaQuery('Interstellar'))
    candidate, = result.candidates
    assert candidate.title == '星际穿越'
    assert candidate.year == 2014
    assert candidate.language == 'chi'
    assert candidate.subtitle_format == 'ass'
    assert candidate.metadata['bilingual'] is True


def test_lwltv_does_not_call_legacy_download_to_bypass_turnstile():
    transport = FakeTransport('<button class="download">下载</button><script>turnstile.execute()</script>')
    provider = LwlTvProvider({'cookie': 'authorized=secret'}, transport=transport)
    with pytest.raises(ProviderError) as error:
        provider.download(SubtitleCandidate('lwltv', '123', 'Example', detail_url='https://www.lwltv.com/subtitles/123'))
    assert error.value.status == 'user_action_required'
    assert len(transport.calls) == 1
    assert 'secret' not in error.value.message


def test_lwltv_downloads_explicit_public_file_without_sending_source_cookie_to_cdn():
    transport = FakeTransport('<a href="https://files.lwltv.com/subtitle.ass">下载</a>', b'[Script Info]')
    result = LwlTvProvider({'cookie': 'authorized=secret'}, transport=transport).download(
        SubtitleCandidate('lwltv', '123', 'Example', detail_url='https://www.lwltv.com/subtitles/123'))
    assert result == (b'[Script Info]', 'subtitle.ass')
    assert 'Cookie' not in transport.calls[-1][1]['headers']


def test_yysub_search_excludes_resource_links_and_preserves_release():
    result = YySubProvider(transport=FakeTransport(YY_PAGE)).search(MediaQuery('Interstellar'))
    candidate, = result.candidates
    assert candidate.title == '星际穿越(Interstellar)'
    assert candidate.release_name == 'Interstellar.2014.BluRay'
    assert candidate.year == 2014
    assert candidate.metadata['bilingual'] is True


@pytest.mark.parametrize('code,status', [(1001, 'auth_required'), (5001, 'user_action_required'), (0, 'unavailable')])
def test_yysub_respects_mandatory_download_check(code, status):
    transport = FakeTransport({'status': code})
    with pytest.raises(ProviderError) as error:
        YySubProvider(transport=transport).download(SubtitleCandidate('yysub', '43044', 'Example'))
    assert error.value.status == status
    assert len(transport.calls) == 1
    assert transport.calls[0][0].endswith('?action=check')


def test_yysub_approved_cookie_session_may_download():
    transport = FakeTransport({'status': 1}, b'PK\x03\x04archive')
    provider = YySubProvider({'cookie': 'authorized=secret'}, transport=transport)
    body, name = provider.download(SubtitleCandidate('yysub', '43044', 'Example', release_name='Example.2020'))
    assert body.startswith(b'PK')
    assert name == 'Example.2020.zip'
    assert transport.calls[-1][0] == 'https://yysub.cc/subtitle/file/43044'
    assert transport.calls[-1][1]['headers']['Cookie'] == 'authorized=secret'


@pytest.mark.parametrize('provider,page', [(LwlTvProvider, '<p>共 ⁨0⁩ 条</p>'), (YySubProvider, '<p>字幕(0)</p>')])
def test_explicit_empty_results_are_not_page_change_errors(provider, page):
    assert provider(transport=FakeTransport(page)).search(MediaQuery('NoMatch')).status == 'no_results'


@pytest.mark.parametrize('provider', [LwlTvProvider, YySubProvider])
def test_unknown_page_is_reported(provider):
    assert provider(transport=FakeTransport('<html>Maintenance</html>')).search(MediaQuery('Example')).status == 'invalid_response'
