import pytest

from telepiplex_caption.interaction import metadata_choice_view


def test_candidates_follow_rename_layout_and_caption_callbacks():
    text, details = metadata_choice_view([
        {"title": "想见你", "original_title": "Someday or One Day", "year": 2019,
         "countries": ["中国台湾"], "media_type": "series", "poster_url": "https://img.example/series.jpg"},
        {"title": "想见你", "original_title": "Someday or One Day", "year": 2022,
         "countries": ["中国大陆", "中国台湾"], "media_type": "movie"},
    ], "0123456789abcdef")
    assert "1. 想见你 (Someday or One Day)\n   2019｜中国台湾｜剧集" in text
    assert "2. 想见你 (Someday or One Day)\n   2022｜中国大陆、中国台湾｜电影" in text
    assert "点击作品按钮或回复编号" in text
    assert details["keyboard"] == [
        [{"text": "1. 想见你", "callback_data": "caption:choose:0123456789ab:0"}],
        [{"text": "2. 想见你", "callback_data": "caption:choose:0123456789ab:1"}],
    ]
    assert details["poster_items"] == [
        {"number": 1, "title": "想见你", "poster_url": "https://img.example/series.jpg"},
        {"number": 2, "title": "想见你", "poster_url": ""},
    ]


@pytest.mark.parametrize("label", ["电影", "剧集", "动画电影", "动画剧集"])
def test_confirmed_media_type_labels_are_preserved(label):
    text, _ = metadata_choice_view([{"title": "示例", "media_type_label": label}], "op")
    assert f"｜{label}" in text


def test_scan_candidates_show_current_file_and_do_not_duplicate_original_title():
    text, _ = metadata_choice_view([
        {"title": "Example", "original_title": "example", "countries": "英国", "media_type": "movie"},
    ], "op", video_path="/真人电影/Example.2020.mkv")
    assert "当前文件：/真人电影/Example.2020.mkv" in text
    assert "1. Example\n" in text
    assert "(example)" not in text
    assert "｜英国｜电影" in text


def test_sparse_or_malformed_candidates_keep_numbering_and_unknown_fields():
    text, details = metadata_choice_view([
        None, {}, {"title": ["invalid"], "year": False, "countries": {"invalid": "country"}, "media_type": []},
    ], "op")
    for index in range(1, 4):
        assert f"{index}. 未知作品\n   年份未知｜地区未知｜类型未知" in text
    assert [row[0]["callback_data"] for row in details["keyboard"]] == [
        "caption:choose:op:0", "caption:choose:op:1", "caption:choose:op:2",
    ]
    assert "poster_items" not in details


@pytest.mark.parametrize("url", [None, "http://img.example/a.jpg", "file:///tmp/a.jpg", "https:///missing-host", "https://[invalid", "https://user:secret@img.example/a.jpg"])
def test_only_usable_https_posters_enable_poster_details(url):
    _, details = metadata_choice_view([{"title": "示例", "poster_url": url}], "op")
    assert "poster_items" not in details


def test_long_metadata_is_bounded_and_only_first_five_candidates_are_rendered():
    candidate = {"title": "中" * 1000, "original_title": "A" * 1000, "year": "2" * 1000,
                 "countries": ["国" * 1000] * 100, "media_type_label": "动画电影"}
    text, details = metadata_choice_view([candidate] * 8, "a" * 128, video_path="/" + "片" * 1000)
    assert len(text) < 3000
    assert len(details["keyboard"]) == 5
    assert "6. " not in text
    assert "…" in text
    assert all(len(row[0]["text"]) <= 27 for row in details["keyboard"])
    assert all(len(row[0]["callback_data"].encode("utf-8")) <= 64 for row in details["keyboard"])
