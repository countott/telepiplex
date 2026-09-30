from __future__ import annotations

import io
import json
import zipfile
from urllib.parse import parse_qs, urlsplit

import pytest

from telepiplex_caption.archive import extract_subtitles
from telepiplex_caption.extra_providers import (
    Addic7edProvider, R3SubProvider, SubDLProvider, SubSourceProvider, Subf2mProvider, XunleiProvider,
)
from telepiplex_caption.models import MediaQuery, SubtitleCandidate
from telepiplex_caption.providers import HttpResponse, ProviderError
from telepiplex_caption.quality import inspect_subtitle


class Transport:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, HttpResponse):
            return value
        if isinstance(value, (dict, list)):
            value = json.dumps(value).encode()
        return HttpResponse(value.encode() if isinstance(value, str) else value)


def subtitle_bytes(encoding="utf-8"):
    return "\n\n".join(f"{i}\n00:{i:02d}:00,000 --> 00:{i:02d}:03,000\n这是第{i}个不同的故事，我们继续寻找新的方向。" for i in range(1, 26)).encode(encoding)


def archive_bytes(encoding="utf-8"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("Example.2020.chs.srt", subtitle_bytes(encoding))
    return stream.getvalue()


def assert_usable(data, filename):
    documents = extract_subtitles(filename, data)
    assert len(documents) == 1
    report = inspect_subtitle(documents[0], MediaQuery("Example", original_language="zh"))
    assert report.accepted, report.reasons
    assert report.language == "chi"
    assert report.cue_count == 25
    return report


CLASSES = (XunleiProvider, SubDLProvider, SubSourceProvider, Subf2mProvider, R3SubProvider, Addic7edProvider)


@pytest.mark.parametrize("cls", CLASSES)
def test_disabled_never_calls_network(cls):
    transport = Transport()
    assert cls({"enabled": False}, transport=transport).search(MediaQuery("Example")).status == "disabled"
    assert transport.calls == []


@pytest.mark.parametrize("cls", (SubDLProvider, SubSourceProvider))
def test_free_key_required_is_explicit_and_not_no_results(cls):
    transport = Transport()
    result = cls(transport=transport).search(MediaQuery("Example"))
    assert result.status == "auth_required"
    assert transport.calls == []


def thunder_item(**overrides):
    return {"cid": "abcdef", "url": "https://subtitle.v.geilijiasu.com/AB/CD/example.srt", "ext": "srt",
            "name": "Example.2020.WEB.chs.srt", "simple_name": "中文[SRT]Example(2020)", "languages": ["简体"], "mt": 5, **overrides}


def test_xunlei_uses_release_and_real_identity_and_rejects_unsafe_wrong_ai():
    transport = Transport({"code": 0, "data": [thunder_item(), thunder_item(cid="wrong", simple_name="Other(2019)", name="Other.2019.srt"),
        thunder_item(cid="evil", url="https://subtitle.v.geilijiasu.com.evil.test/file"),
        thunder_item(cid="mt", machine_translated=True), thunder_item(cid="none", name="", simple_name="")]}, subtitle_bytes())
    provider = XunleiProvider(transport=transport)
    query = MediaQuery("Example", year=2020, video_path="/movies/Example.2020.WEB.mkv")
    candidate, = provider.search(query).candidates
    assert candidate.title == "Example(2020)" and candidate.year == 2020
    assert candidate.language == "chi"
    assert parse_qs(urlsplit(transport.calls[0][0]).query)["name"] == ["Example.2020.WEB.mkv"]
    assert_usable(*provider.download(candidate))


def test_xunlei_episode_not_invented_from_request():
    provider = XunleiProvider(transport=Transport({"code": 0, "data": [thunder_item(name="Example.srt", simple_name="Example")]}))
    candidate, = provider.search(MediaQuery("Example", media_type="series", season=2, episode=5)).candidates
    assert candidate.season is None and candidate.episode is None


def subdl_payload(**item_overrides):
    return {"status": True, "results": [{"name": "Example", "sd_id": 100, "imdb_id": "tt1234567", "tmdb_id": 42, "year": 2020, "type": "movie"}],
        "subtitles": [{"n_id": "abc123", "name": "example.zip", "language": "ZH", "releases": ["Example.2020.WEB"],
                       "subtitlePage": "/s/info/abc123/example", **item_overrides}]}


def test_subdl_v2_exact_ids_and_binary_download_keep_key_out_of_urls():
    transport = Transport(subdl_payload(), archive_bytes())
    provider = SubDLProvider({"api_key": "secret-value"}, transport=transport)
    candidate, = provider.search(MediaQuery("Localized", imdb_id="tt1234567", tmdb_id="42", year=2020)).candidates
    assert candidate.title == "Example"
    assert candidate.metadata["imdb_id"] == "tt1234567"
    assert "/api/v2/subtitles/search?" in transport.calls[0][0]
    assert parse_qs(urlsplit(transport.calls[0][0]).query)["imdb_id"] == ["tt1234567"]
    assert_usable(*provider.download(candidate))
    assert "/api/v2/subtitles/abc123/download?format=zip" in transport.calls[1][0]
    assert all("secret-value" not in url for url, _ in transport.calls)
    assert all(kw["headers"]["Authorization"] == "Bearer secret-value" for _, kw in transport.calls)


@pytest.mark.parametrize("flag", ("ai_translated", "is_ai_translation", "machine_translated"))
def test_subdl_never_uses_machine_translation_candidates(flag):
    payload = subdl_payload(**{flag: True})
    payload["translation"] = {"missing_languages": ["ZH"], "sources": [{"n_id": "machine-job"}]}
    transport = Transport(payload)
    result = SubDLProvider({"api_key": "key"}, transport=transport).search(MediaQuery("Example"))
    assert result.status == "no_results"
    assert len(transport.calls) == 1


def test_subdl_multiple_titles_without_association_cannot_impersonate_requested_title():
    payload = subdl_payload(releases=[], release_name="")
    payload["results"].append({"name": "Other", "year": 2019, "sd_id": 200})
    assert SubDLProvider({"api_key": "key"}, transport=Transport(payload)).search(MediaQuery("Example")).status == "no_results"


def test_subdl_pack_never_invents_requested_episode_and_public_cdn_has_no_key():
    payload = subdl_payload(n_id="", url="/subtitle/123-456.zip", full_season=True, season=2, episode=3,
                            releases=["Example.S02.Complete"])
    payload["results"][0]["type"] = "tv"
    transport = Transport(payload, archive_bytes())
    provider = SubDLProvider({"api_key": "secret"}, transport=transport)
    candidate, = provider.search(MediaQuery("Example", media_type="series", season=2, episode=7)).candidates
    assert candidate.episode is None
    assert candidate.metadata["season_pack"]
    provider.download(candidate)
    assert "headers" not in transport.calls[1][1]
    assert transport.calls[1][0] == "https://dl.subdl.com/subtitle/123-456.zip"


def test_subdl_mismatched_imdb_and_malformed_results_are_rejected():
    result = SubDLProvider({"api_key": "key"}, transport=Transport(subdl_payload())).search(MediaQuery("Example", imdb_id="tt9999999"))
    assert result.status == "no_results"
    result = SubDLProvider({"api_key": "key"}, transport=Transport({"results": []})).search(MediaQuery("Example"))
    assert result.status == "invalid_response"


def test_subsource_search_contract_binds_returned_title_and_episode():
    transport = Transport({"success": True, "data": [{"movieId": 11, "title": "Other", "releaseYear": 2020},
        {"movieId": 12, "title": "Example", "releaseYear": 2020, "type": "tv", "imdbId": "tt1234567"}]},
        {"success": True, "data": [{"subtitleId": 400, "releaseInfo": ["Example.S02E03.WEB"], "language": "chinese_bg_code", "link": "/subtitle/example/chinese_bg_code/400"},
            {"subtitleId": 401, "releaseInfo": "Example.S02E04.WEB", "language": "chinese_bg_code"}]}, archive_bytes())
    provider = SubSourceProvider({"api_key": "free-secret"}, transport=transport)
    candidate, = provider.search(MediaQuery("Example", year=2020, media_type="series", season=2, episode=3)).candidates
    assert candidate.title == "Example" and candidate.episode == 3
    params = parse_qs(urlsplit(transport.calls[1][0]).query)
    assert params["movieId"] == ["12"] and params["episodeNumber"] == ["3"]
    assert_usable(*provider.download(candidate))
    assert all("free-secret" not in url for url, _ in transport.calls)
    assert all(kw["headers"]["X-API-Key"] == "free-secret" for _, kw in transport.calls)
    assert transport.calls[-1][0].endswith("/subtitles/400/download")


@pytest.mark.parametrize("cls", (SubDLProvider, SubSourceProvider))
def test_api_errors_are_visible_without_echoing_credentials_or_server_body(cls):
    result = cls({"api_key": "secret"}, transport=Transport({"error": {"code": "unauthorized", "message": "secret"}})).search(MediaQuery("Example"))
    assert result.status == "auth_required" and "secret" not in result.message


SUBF_SEARCH = '<ul><li><div class="title"><a href="/subtitles/example">Example (2020)</a></div></li></ul>'
SUBF_LIST = '''<a href="/subtitles/example/big_5_code">Big 5</a><a href="https://www.imdb.com/title/tt1234567/">IMDb</a><ul><li class="item">
<ul class="scrolllist"><li>Example.2020.chs&amp;eng</li></ul><div class="comment-col">简英</div>
<a class="download icon-download" href="/subtitles/example/chinese-bg-code/123"></a></li></ul>'''


def test_subf2m_fetches_fresh_download_and_accepts_utf16_archive():
    transport = Transport(SUBF_SEARCH, SUBF_LIST, "<ul></ul>",
        '<a id="downloadButton" href="/subtitles/example/chinese-bg-code/123/download">Download</a>', archive_bytes("utf-16"))
    provider = Subf2mProvider(transport=transport)
    candidate, = provider.search(MediaQuery("Example", year=2020)).candidates
    assert candidate.release_name == "Example.2020.chs&eng" and candidate.year == 2020
    assert candidate.download_url == ""
    report = assert_usable(*provider.download(candidate))
    assert report.encoding.lower().startswith("utf-16")
    assert transport.calls[-2][0].endswith("/123")
    assert transport.calls[-1][0].endswith("/123/download")


def test_subf2m_series_season_is_derived_from_catalogue_not_query():
    search = SUBF_SEARCH.replace("Example (2020)", "Example - Second Season (2020)")
    listing = SUBF_LIST.replace("Example.2020.chs", "Example.S02E03.chs")
    candidate, = Subf2mProvider(transport=Transport(search, listing, "<ul></ul>")).search(
        MediaQuery("Example", media_type="series", season=2, episode=3)).candidates
    assert candidate.season == 2 and candidate.episode == 3
    result = Subf2mProvider(transport=Transport(search)).search(MediaQuery("Example", media_type="series", season=3))
    assert result.status == "no_results"


R3_SEARCH = '<a class="movie__title" href="show.php?id=Abc123">示例 / Example</a>'
R3_DETAIL = '''<a class="movie__title" href="movie.php?id=tt1234567">示例 / Example (2020)</a>
<a href="https://www.imdb.com/title/tt1234567/">IMDb</a>
<a data-sid="123" data-un="p" data-fname="Example.2020.cmn-Hans.srt">预览</a>
<a data-sid="123" data-un="p" data-fname="Example.2020.en.srt">预览</a>'''


def test_r3_public_preview_form_export_preserves_single_newlines_and_quality():
    raw = subtitle_bytes().decode().replace("-->", "--&gt;").replace("\n", "<br />\r\n")
    transport = Transport(R3_SEARCH, R3_DETAIL, raw)
    provider = R3SubProvider(transport=transport)
    candidate, = provider.search(MediaQuery("Example", year=2020, imdb_id="tt1234567")).candidates
    assert candidate.language == "chi" and candidate.metadata["download_method"] == "public_preview_export"
    body, name = provider.download(candidate)
    assert body == subtitle_bytes()
    assert_usable(body, name)
    assert parse_qs(transport.calls[-1][1]["data"].decode()) == {
        "dasid": ["123"], "dafname": ["Example.2020.cmn-Hans.srt"], "un": ["p"]}
    assert not any("download.php" in url for url, _ in transport.calls)


def test_r3_cannot_use_unrelated_title_returned_by_search():
    detail = R3_DETAIL.replace("Example", "Unrelated").replace("tt1234567", "tt8888888")
    result = R3SubProvider(transport=Transport(R3_SEARCH, detail)).search(MediaQuery("Example", imdb_id="tt1234567"))
    assert result.status == "no_results"


def test_r3_gated_or_invalid_preview_never_returns_fake_subtitle():
    candidate = SubtitleCandidate("r3sub", "123", "Example", metadata={"sid": "123", "preview_mode": "p", "filename": "Example.srt"})
    with pytest.raises(ProviderError, match="公开 SRT"):
        R3SubProvider(transport=Transport("Please login")).download(candidate)


ADDIC_SEARCH = '<button onmouseup="loadShow(42,2,\'\');">2</button>'
ADDIC_ROW = '''<tr class="completed"><td>2</td><td>3</td><td><a href="/serie/Example/2/3/Title">Title</a></td>
<td>Chinese (Simplified)</td><td>WEB.GROUP</td><td>Completed</td><td><a href="/updated/41/123/0">Download</a></td></tr>'''


def test_addic7ed_completed_chinese_episode_and_cookie_referer_download():
    transport = Transport(ADDIC_SEARCH, "<table>" + ADDIC_ROW + ADDIC_ROW.replace("Chinese (Simplified)", "English") +
                          ADDIC_ROW.replace("Completed", "80%") + "</table>", subtitle_bytes())
    provider = Addic7edProvider({"cookie": "session=test", "user_agent": "TestAgent"}, transport=transport)
    candidate, = provider.search(MediaQuery("Example", media_type="series", season=2, episode=3)).candidates
    assert candidate.title == "Example" and candidate.year is None and candidate.language == "chi"
    assert candidate.season == 2 and candidate.episode == 3
    assert_usable(*provider.download(candidate))
    assert transport.calls[-1][1]["headers"]["Referer"] == "https://www.addic7ed.com/season/42/2"
    assert all(kw["headers"]["Cookie"] == "session=test" for _, kw in transport.calls)


def test_addic7ed_other_show_or_episode_is_not_relabelled():
    row = ADDIC_ROW.replace("/Example/", "/Other/")
    result = Addic7edProvider(transport=Transport(ADDIC_SEARCH, row)).search(MediaQuery("Example", media_type="series", season=2, episode=3))
    assert result.status == "no_results"
    result = Addic7edProvider(transport=Transport(ADDIC_SEARCH, ADDIC_ROW)).search(MediaQuery("Example", media_type="series", season=2, episode=4))
    assert result.status == "no_results"


@pytest.mark.parametrize("cls,query", [(Subf2mProvider, MediaQuery("Example")), (R3SubProvider, MediaQuery("Example")),
    (Addic7edProvider, MediaQuery("Example", media_type="series", season=1))])
def test_public_website_challenge_is_actionable(cls, query):
    assert cls(transport=Transport("<html>Just a moment... cf-chl-</html>")).search(query).status == "user_action_required"


@pytest.mark.parametrize("cls", CLASSES)
def test_download_rejects_candidate_owned_by_other_provider(cls):
    with pytest.raises(ProviderError) as error:
        cls(transport=Transport()).download(SubtitleCandidate("other", "1", "Example"))
    assert error.value.status == "invalid_candidate"


def test_xunlei_bilingual_after_twenty_rows_gets_inspection_slot_before_truncation():
    items = [thunder_item(cid=f"mono-{i}") for i in range(25)] + [
        thunder_item(cid="bilingual-ass", ext="ass", name="Example.2020.chs&eng.ass", languages=["简体&英语"]),
        thunder_item(cid="bilingual-srt", name="Example.2020.chs&eng.srt", languages=["简体&英语"])]
    result = XunleiProvider(transport=Transport({"code": 0, "data": items}), max_candidates=2).search(
        MediaQuery("Example", year=2020, original_language="en"))
    assert [c.candidate_id for c in result.candidates] == ["bilingual-ass", "bilingual-srt"]
    assert all(c.metadata["bilingual"] for c in result.candidates)


def test_addic7ed_simplified_receives_slot_before_traditional_or_cantonese():
    rows = ADDIC_ROW.replace("Chinese (Simplified)", "Cantonese") + ADDIC_ROW.replace("Chinese (Simplified)", "Chinese (Traditional)") + ADDIC_ROW
    candidate, = Addic7edProvider(transport=Transport(ADDIC_SEARCH, rows), max_candidates=1).search(
        MediaQuery("Example", media_type="series", season=2, episode=3, original_language="en")).candidates
    assert candidate.language == "chi"


@pytest.mark.parametrize("cls,url,valid", [
    (R3SubProvider, "https://www.r3sub.com/show.php?id=rVshnE23414", True),
    (R3SubProvider, "https://www.r3sub.com/show.php?id=Abc123&id=Other", False),
    (R3SubProvider, "https://www.r3sub.com/show.php?id=Abc123&url=http://127.0.0.1", False),
    (R3SubProvider, "https://r3sub.com.evil.test/show.php?id=Abc123", False),
    (R3SubProvider, "https://www.r3sub.com/download.php?id=Abc123", False),
    (Subf2mProvider, "https://subf2m.co/subtitles/example/chinese-bg-code/123", True),
    (Subf2mProvider, "https://isubcdn.com/subtitles/example/chinese-bg-code/123", False),
    (Subf2mProvider, "https://subf2m.co/subtitles/example/chinese-bg-code/123/download", False),
])
def test_explicit_detail_routes_are_narrow_and_cannot_relay_arbitrary_urls(cls, url, valid):
    assert cls().supports_detail_url(url) is valid


def test_r3_explicit_detail_provides_only_actual_page_identity():
    candidate, = R3SubProvider(transport=Transport(R3_DETAIL)).lookup_detail("https://www.r3sub.com/show.php?id=Abc123").candidates
    assert candidate.title == "示例 / Example (2020)" and candidate.year == 2020
    assert candidate.metadata["imdb_id"] == "tt1234567"


def test_subf2m_explicit_detail_can_download_when_search_is_unavailable():
    detail = '''<h1><span itemprop="name">Example</span></h1><li class="release"><strong>Release info:</strong>
    <div>Example.2020.chs&amp;eng</div></li><a id="downloadButton" href="/subtitles/example/chinese-bg-code/123/download">Download</a>'''
    transport = Transport(detail, detail, archive_bytes("utf-16"))
    provider = Subf2mProvider(transport=transport)
    candidate, = provider.lookup_detail("https://subf2m.co/subtitles/example/chinese-bg-code/123").candidates
    assert candidate.title == "Example" and candidate.year == 2020 and candidate.metadata["bilingual"]
    assert_usable(*provider.download(candidate))
    assert not any("searchbytitle" in url for url, _ in transport.calls)


def test_addic7ed_login_on_season_page_is_not_empty_search():
    result = Addic7edProvider(transport=Transport(ADDIC_SEARCH, "<html>Please login to continue</html>")).search(
        MediaQuery("Example", media_type="series", season=2))
    assert result.status == "auth_required"


def test_subdl_explicit_foreign_catalogue_id_cannot_inherit_sole_result_identity():
    payload = subdl_payload(sd_id=200, releases=["Other.2020.WEB"])
    result = SubDLProvider({"api_key": "key"}, transport=Transport(payload)).search(MediaQuery("Example", imdb_id="tt1234567"))
    assert result.status == "no_results"


def test_subdl_season_query_uses_documented_full_season_parameter():
    transport = Transport({"results": [], "subtitles": []})
    SubDLProvider({"api_key": "key"}, transport=transport).search(MediaQuery("Example", media_type="series", season=2))
    assert parse_qs(urlsplit(transport.calls[0][0]).query)["full_season"] == ["1"]


def test_subf2m_does_not_guess_missing_traditional_category():
    listing = SUBF_LIST.replace('<a href="/subtitles/example/big_5_code">Big 5</a>', '')
    transport = Transport(SUBF_SEARCH, listing)
    assert Subf2mProvider(transport=transport).search(MediaQuery("Example")).status == "ok"
    assert len(transport.calls) == 2


@pytest.mark.parametrize("filename", ("../secret.srt", "/secret.srt", "folder/../../secret.srt", "null\x00.srt"))
def test_r3_preview_cannot_send_path_traversal_to_source_server(filename):
    candidate = SubtitleCandidate("r3sub", "123", "Example", metadata={"sid": "123", "preview_mode": "p", "filename": filename})
    transport = Transport()
    with pytest.raises(ProviderError) as exc:
        R3SubProvider(transport=transport).download(candidate)
    assert exc.value.status == "invalid_candidate" and not transport.calls


@pytest.mark.parametrize("field,value", [
    ("releases", ["Example.2020.machine-translated"]),
    ("productionType", "machine_translation"),
    ("translation_type", "AI"),
])
def test_api_machine_labels_in_release_or_production_fields_are_excluded(field, value):
    provider = SubDLProvider({"api_key": "key"}, transport=Transport(subdl_payload(**{field: value})))
    assert provider.search(MediaQuery("Example")).status == "no_results"


def test_movie_title_ai_is_not_itself_a_machine_translation_marker():
    payload = subdl_payload(releases=["A.I.Artificial.Intelligence.2001.BluRay"])
    payload["results"][0].update(name="A.I. Artificial Intelligence", year=2001)
    assert SubDLProvider({"api_key": "key"}, transport=Transport(payload)).search(
        MediaQuery("A.I. Artificial Intelligence", year=2001)).status == "ok"


@pytest.mark.parametrize("cls,query,empty", [
    (Subf2mProvider, MediaQuery("Example"), "No results found"),
    (R3SubProvider, MediaQuery("Example"), "查無資料"),
    (Addic7edProvider, MediaQuery("Example", media_type="series", season=1), "0 results found"),
])
def test_explicit_no_results_is_distinct_from_maintenance_or_changed_markup(cls, query, empty):
    assert cls(transport=Transport(f"<html>{empty}</html>")).search(query).status == "no_results"
    assert cls(transport=Transport("<html>Maintenance</html>")).search(query).status == "unavailable"
    assert cls(transport=Transport("<html>Unknown layout</html>")).search(query).status == "invalid_response"


def reply_query():
    return MediaQuery("请回答1988", original_title="Reply 1988", aliases=("응답하라 1988",),
                      media_type="series", year=2015, season=1)


@pytest.mark.parametrize("release_year,expected_year", [(None, None), (2015, 2015), (2023, 2023)])
@pytest.mark.parametrize("cls", (XunleiProvider, SubDLProvider, SubSourceProvider))
def test_numeric_work_title_never_becomes_release_year_in_api_candidates(cls, release_year, expected_year):
    release = "Reply.1988" + (f".{release_year}" if release_year else "") + ".S01.chs.srt"
    if cls is XunleiProvider:
        responses = [{"code": 0, "data": [thunder_item(name=release, simple_name="中文[SRT]Reply 1988")]}]
        provider = cls(transport=Transport(*responses))
    elif cls is SubDLProvider:
        payload = subdl_payload(releases=[release])
        payload["results"][0].update(name="Reply 1988", year=None, type="tv")
        provider = cls({"api_key": "key"}, transport=Transport(payload))
    else:
        provider = cls({"api_key": "key"}, transport=Transport(
            {"data": [{"movieId": 12, "title": "Reply 1988", "type": "tv"}]},
            {"data": [{"subtitleId": 400, "releaseInfo": release, "language": "chinese_bg_code"}]}))
    result = provider.search(reply_query())
    if release_year == 2023:
        assert result.status == "no_results"  # A genuine conflicting year still rejects.
    else:
        candidate, = result.candidates
        assert candidate.year == expected_year


@pytest.mark.parametrize("api_year,status", [(2015, "ok"), (2023, "no_results")])
def test_subdl_explicit_api_year_keeps_its_authority_for_numeric_titles(api_year, status):
    payload = subdl_payload(releases=["Reply.1988.S01.chs.srt"])
    payload["results"][0].update(name="Reply 1988", year=api_year, type="tv")
    result = SubDLProvider({"api_key": "key"}, transport=Transport(payload)).search(reply_query())
    assert result.status == status
    if result.candidates:
        assert result.candidates[0].year == api_year


@pytest.mark.parametrize("year", [None, 2015, 2023])
def test_subf2m_search_numeric_title_preserves_actual_release_year(year):
    title = "Reply 1988" + (f" ({year})" if year else "")
    search = SUBF_SEARCH.replace("Example (2020)", title)
    listing = SUBF_LIST.replace("Example.2020.chs", "Reply.1988.S01.chs")
    result = Subf2mProvider(transport=Transport(search, listing, "<ul></ul>")).search(reply_query())
    if year == 2023:
        assert result.status == "no_results"
    else:
        candidate, = result.candidates
        assert candidate.year == year


@pytest.mark.parametrize("cls", (Subf2mProvider, R3SubProvider))
@pytest.mark.parametrize("header_year,file_year,expected", [
    (None, None, None), (2015, None, 2015), (2023, None, 2023), (None, 2015, 2015), (None, 2023, 2023),
])
def test_numeric_title_in_explicit_details_uses_page_evidence_only(cls, header_year, file_year, expected):
    title = "Reply 1988" + (f" ({header_year})" if header_year else "")
    release = "Reply.1988" + (f".{file_year}" if file_year else "") + ".S01.chs.srt"
    if cls is Subf2mProvider:
        page = f'<h1><span itemprop="name">{title}</span></h1><li class="release"><div>{release}</div></li>'
        url = "https://subf2m.co/subtitles/reply-1988/chinese-bg-code/123"
    else:
        page = f'<a class="movie__title">{title}</a><a data-sid="123" data-un="p" data-fname="{release}">预览</a>'
        url = "https://www.r3sub.com/show.php?id=Abc123"
    candidate, = cls(transport=Transport(page)).lookup_detail(url).candidates
    assert candidate.year == expected


def test_addic7ed_numeric_work_title_keeps_unknown_release_year():
    row = ADDIC_ROW.replace("/Example/2/3/", "/Reply_1988/2/3/")
    query = MediaQuery("请回答1988", original_title="Reply 1988", year=2015, media_type="series", season=2, episode=3)
    candidate, = Addic7edProvider(transport=Transport(ADDIC_SEARCH, row)).search(query).candidates
    assert candidate.title == "Reply 1988" and candidate.year is None
