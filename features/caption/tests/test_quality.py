from dataclasses import replace

import pytest

from telepiplex_caption.models import MediaQuery, SubtitleCandidate, SubtitleDocument
from telepiplex_caption.quality import inspect_subtitle, rank_subtitle, subtitle_priority


def _dialogue(index: int, traditional: bool = False) -> str:
    # Deliberately synthetic parser fixture, never a downloadable subtitle.
    text = "这是我们的测试字幕，他们已经来到这里，说话时间会随着变化。"
    if traditional:
        text = "這是我們的測試字幕，他們已經來到這裡，說話時間會隨著變化。"
    return f"{text}{index}"


def make_srt(*, bilingual=False, traditional=False, count=25, encoding="utf-8", interval=3):
    blocks = []
    for index in range(count):
        start, end = index * interval, index * interval + 2
        line = _dialogue(index, traditional)
        if bilingual:
            line += f"\nThis is a dialogue fixture number {index}."
        blocks.append(f"{index + 1}\n{start // 3600:02}:{start // 60 % 60:02}:{start % 60:02},000 --> {end // 3600:02}:{end // 60 % 60:02}:{end % 60:02},000\n{line}\n")
    return SubtitleDocument("Example.2020.srt", "srt", "\n".join(blocks).encode(encoding))


def make_ass(*, bilingual=False, traditional=False, split_layers=False):
    lines = ["[Script Info]", "ScriptType: v4.00+", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for index in range(25):
        start, end = index * 3, index * 3 + 2
        prefix = f"Dialogue: 0,0:{start // 60:02}:{start % 60:02}.00,0:{end // 60:02}:{end % 60:02}.00,Default,,0,0,0,,"
        text = _dialogue(index, traditional)
        if bilingual and not split_layers:
            text += rf"\NThis is a dialogue fixture number {index}."
        lines.append(prefix + text)
        if bilingual and split_layers:
            lines.append(prefix + f"This is a dialogue fixture number {index}.")
    return SubtitleDocument("Example.2020.ass", "ass", "\n".join(lines).encode())


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "gb18030"])
def test_actual_simplified_content_and_encoding(encoding):
    report = inspect_subtitle(make_srt(encoding=encoding), MediaQuery("Example", original_language="zh"))
    assert report.accepted, report.reasons
    assert report.language == "chi"
    assert not report.bilingual
    assert report.cue_count == 25
    assert not report.timing_verified
    assert "video_timing_unverified" in report.warnings


def test_traditional_big5_content_overrides_misleading_filename():
    doc = replace(make_srt(traditional=True, encoding="big5"), filename="简中双语.srt")
    report = inspect_subtitle(doc)
    assert report.accepted, report.reasons
    assert report.language == "cht" and not report.bilingual


@pytest.mark.parametrize("doc", [make_srt(bilingual=True), make_ass(bilingual=True), make_ass(bilingual=True, split_layers=True)])
def test_bilingual_requires_actual_synchronized_dialogue(doc):
    report = inspect_subtitle(doc)
    assert report.accepted, report.reasons
    assert report.bilingual and report.bilingual_ratio == 1


def test_english_exact_priority_and_non_english_monolingual():
    query = MediaQuery("Example", year=2020, original_language="en")
    candidate = SubtitleCandidate("local", "1", "Example", year=2020)
    docs = [make_ass(bilingual=True), make_srt(bilingual=True), make_srt(), make_ass(traditional=True)]
    ranks = [rank_subtitle(query, candidate, doc, inspect_subtitle(doc, query)) for doc in docs]
    assert all(rank is not None for rank in ranks)
    assert ranks == sorted(ranks, reverse=True)
    assert rank_subtitle(query, candidate, make_ass(), inspect_subtitle(make_ass(), query)) is None
    for lang in ["zh", "yue", "ja", "ko", "fr"]:
        non_english = replace(query, original_language=lang)
        assert subtitle_priority(non_english, inspect_subtitle(make_ass(), non_english)) == 200
        assert subtitle_priority(non_english, inspect_subtitle(make_ass(bilingual=True), non_english)) == 0
        assert subtitle_priority(non_english, inspect_subtitle(make_srt(traditional=True), non_english)) == 100


def test_unknown_original_language_cannot_select():
    query = MediaQuery("Example")
    assert subtitle_priority(query, inspect_subtitle(make_srt(), query)) == 0


@pytest.mark.parametrize("payload", [b"<html>captcha required</html>", b"1\n00:00:02,000 --> 00:00:01,000\nbad\n", b"not a subtitle"])
def test_rejects_html_and_broken_timestamps(payload):
    assert not inspect_subtitle(SubtitleDocument("bilingual.srt", "srt", payload)).accepted


def test_duration_rejects_wrong_or_partial_timeline():
    doc = make_srt()
    assert "timeline_exceeds_video" in inspect_subtitle(doc, MediaQuery("Example", duration_seconds=20)).reasons
    assert "likely_partial_subtitle" in inspect_subtitle(doc, MediaQuery("Example", duration_seconds=3600)).reasons
    report = inspect_subtitle(doc, MediaQuery("Example", duration_seconds=80))
    assert report.accepted
    assert not report.timing_verified and "dialogue_sync_unverified" in report.warnings


def test_english_only_never_masquerades_as_chinese():
    doc = make_srt()
    text = doc.content.decode()
    for index in range(25):
        text = text.replace(_dialogue(index), f"This is a dialogue fixture number {index}.")
    report = inspect_subtitle(replace(doc, content=text.encode()))
    assert not report.accepted and "insufficient_chinese_dialogue" in report.reasons


def test_mixed_script_rejected_instead_of_labelled_from_filename():
    doc = make_srt()
    text = doc.content.decode()
    for index in range(13):
        text = text.replace(_dialogue(index) + "\n", _dialogue(index, True) + "\n")
    assert "mixed_chinese_scripts" in inspect_subtitle(replace(doc, content=text.encode())).reasons


def test_single_english_credit_does_not_claim_bilingual():
    doc = make_srt()
    text = doc.content.decode().replace(_dialogue(0), _dialogue(0) + "\nTranslated by Fictional Fixture Team")
    report = inspect_subtitle(replace(doc, content=text.encode()))
    assert report.accepted and not report.bilingual


def test_names_and_ass_styles_are_not_english_dialogue():
    doc = make_ass()
    text = doc.content.decode().replace("Format:", "Format:")
    text = text.replace("[Events]", "[V4+ Styles]\nFormat: Name, Fontname\nStyle: English Chinese Main,Noto Sans\n[Events]")
    report = inspect_subtitle(replace(doc, content=text.encode()))
    assert report.accepted and not report.bilingual


def _anime_ass(*, japanese_dialogue: bool):
    """Authored signs/lyrics fixture: Chinese vector text must not dilute JP."""
    lines = ["[Script Info]", "ScriptType: v4.00+", "[Events]",
             "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for index in range(100):
        start, end = index * 12, index * 12 + 3
        timing = f"0:{start // 60:02}:{start % 60:02}.00,0:{end // 60:02}:{end % 60:02}.00"
        prefix = f"Dialogue: 0,{timing},Chinese,,0,0,0,,"
        lines.append(prefix + _dialogue(index))
        # Unique heavy Chinese sign layers lower the old character proportion.
        lines.append(prefix.replace(",Chinese,", ",Sign,") + ("这是合成测试画面文字" * 8) + str(index))
        if japanese_dialogue or index < 5 or index >= 95:
            lines.append(prefix.replace(",Chinese,", ",Japanese,") + f"これはテストの字幕です {index}")
    return SubtitleDocument("fixture.ass", "ass", "\n".join(lines).encode())


def test_japanese_layers_cannot_hide_behind_heavy_chinese_effects():
    query = MediaQuery("Example", original_language="ja")
    report = inspect_subtitle(_anime_ass(japanese_dialogue=True), query)
    assert not report.accepted
    assert "non_chinese_or_multilingual_dialogue" in report.reasons
    assert subtitle_priority(query, report) == 0


def test_chinese_dialogue_with_brief_japanese_opening_and_ending_is_preserved():
    report = inspect_subtitle(_anime_ass(japanese_dialogue=False), MediaQuery("Example", original_language="ja"))
    assert report.accepted, report.reasons
    assert report.language == "chi" and not report.bilingual


def test_unmerged_ass_import_rejected_but_compiled_karaoke_comments_allowed():
    doc = make_ass()
    prefix = "\nComment: 0,0:00:00.00,0:00:00.00,Default,,0,0,0,"
    pending = replace(doc, content=doc.content + (prefix + "import,OP.ass").encode())
    assert "unresolved_ass_import" in inspect_subtitle(pending).reasons
    compiled = replace(doc, content=doc.content + (prefix + "template syl,{\\k$kdur}$text").encode())
    assert inspect_subtitle(compiled).accepted


@pytest.mark.parametrize("filename", ["Songs/OP.SC.ass", "ED_jpn.ass", "ep01/Screen.ass", "staff.ass", "insert01.ass", "Screen/Example.S01E01.ass"])
def test_short_named_ass_production_fragments_are_not_complete_dialogue(filename):
    report = inspect_subtitle(replace(make_ass(), filename=filename), MediaQuery("Example", original_language="ja"))
    assert "subtitle_source_fragment" in report.reasons


def test_actual_work_named_screen_is_not_rejected_as_source_fragment():
    report = inspect_subtitle(replace(make_ass(), filename="Screen.ass"), MediaQuery("Screen", original_language="ja"))
    assert report.accepted, report.reasons


@pytest.mark.parametrize("filename,media_type", [("Example.2020.BluRay-002.ass", "movie"), ("Example.S01E01-001_track3_chi.ass", "series")])
def test_numbered_split_is_rejected_without_video_or_duration(filename, media_type):
    report = inspect_subtitle(replace(make_ass(bilingual=True), filename=filename), MediaQuery("Example", media_type=media_type, original_language="en"))
    assert "likely_split_subtitle" in report.reasons


def test_metadata_runtime_checks_completeness_without_claiming_video_sync():
    query = MediaQuery("Example", original_language="zh", expected_duration_seconds=3600)
    partial = inspect_subtitle(make_srt(interval=40), query)
    assert "likely_partial_subtitle" in partial.reasons
    # The dialogue can end before credits; an approximate runtime is not an
    # exact video measurement or a reason to require subtitle coverage to 100%.
    full = inspect_subtitle(make_srt(interval=120), query)
    assert full.accepted, full.reasons
    assert not full.timing_verified
    assert "metadata_runtime_reference" in full.warnings
    assert "video_timing_unverified" in full.warnings


def _nonrendering_fx_doc(*, style='OPJP 3-3', effect='fx', text='字', start='0:00:04.00', end='0:00:03.00', count=1, dialogue_count=100):
    original = make_ass().content.decode()
    header = original[:original.index('Dialogue:')]
    lines = []
    for index in range(dialogue_count):
        start_at = index * 3
        lines.append(f'Dialogue: 0,0:{start_at // 60:02}:{start_at % 60:02}.00,0:{(start_at+2) // 60:02}:{(start_at+2) % 60:02}.00,Default,,0,0,0,,{_dialogue(index)}')
    lines.extend(f'Dialogue: 0,{start},{end},{style},,0,0,0,{effect},{text}' for _ in range(count))
    return SubtitleDocument('Example.ass', 'ass', (header + '\n'.join(lines)).encode())


@pytest.mark.parametrize('end', ['0:00:03.00', '0:00:04.00'])
def test_sparse_nonrendering_op_fx_is_preserved_but_not_counted_as_dialogue(end):
    document = _nonrendering_fx_doc(end=end)
    report = inspect_subtitle(document, MediaQuery('Example', original_language='zh'))
    assert report.accepted, report.reasons
    assert report.cue_count == 100
    assert 'ignored_nonrendering_effects' in report.warnings
    assert report.normalized_text == document.content.decode()


@pytest.mark.parametrize('options', [
    {'style': 'Default'}, {'style': 'OPERA'}, {'effect': ''}, {'effect': 'kara'},
    {'text': '普通对白'}, {'text': ''}, {'start': '0:99:04.00'},
    {'count': 3, 'dialogue_count': 100}, {'count': 65, 'dialogue_count': 4000},
])
def test_negative_or_zero_events_outside_narrow_fx_compatibility_still_rejected(options):
    report = inspect_subtitle(_nonrendering_fx_doc(**options), MediaQuery('Example', original_language='zh'))
    assert 'malformed_cues' in report.reasons


def test_nonrendering_fx_compatibility_does_not_hide_other_invalid_dialogue():
    document = _nonrendering_fx_doc()
    damaged = document.content + b'\nDialogue: 0,0:00:04.00,0:00:03.00,Default,,0,0,0,,broken ordinary dialogue'
    report = inspect_subtitle(replace(document, content=damaged), MediaQuery('Example', original_language='zh'))
    assert not report.accepted and 'malformed_cues' in report.reasons
    assert 'ignored_nonrendering_effects' in report.warnings
