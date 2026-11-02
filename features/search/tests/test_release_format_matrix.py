"""Independent, user-visible format combinations rather than parser-table echoes."""

from itertools import product
from urllib.parse import quote

import pytest

from telepiplex_search.release_identity import stable_release_id
from telepiplex_search.release_report import format_release_report, release_keyboard


# These expected pairs are deliberately owned by the display tests; do not
# derive them from the production alias registry or parse_release_specs().
RESOLUTIONS = (
    ("4K", "2160p"),
    ("1920x1080", "1080p"),
    ("720p", "720p"),
    ("480p", "480p"),
)
VIDEO_CODECS = (
    ("x265", "HEVC"),
    ("H.264", "AVC"),
    ("AV1", "AV1"),
    ("VP9", "VP9"),
)
AUDIO_TRACKS = (
    ("AAC2.0", "AAC", "2ch立体"),
    ("DD+5.1", "DD+", "6ch环绕"),
    ("DTS-HD.MA.7.1", "DTS-HD MA", "8ch环绕"),
    ("TrueHD.Atmos.7.1", "TrueHD", "8ch沉浸"),
    ("FLAC.Stereo", "FLAC", "2ch立体"),
    ("Opus.6CH", "Opus", "6ch环绕"),
)
SEPARATORS = (" ", ".", "_", "-", "/", "][")
CASINGS = ("original", "lower", "upper")


def specification_row(item):
    report = format_release_report(
        "Feature", None, [item], {"total_indexers": 1, "final": True},
    )
    return report.splitlines()[3]


def release_title(tokens, separator, casing="original"):
    suffix = separator.join(tokens)
    if separator == "][":
        suffix = f"[{suffix}]"
    title = f"Feature.2026.{suffix}"
    return title.lower() if casing == "lower" else title.upper() if casing == "upper" else title


@pytest.mark.parametrize(
    "resolution,video,audio,separator,casing",
    product(RESOLUTIONS, VIDEO_CODECS, AUDIO_TRACKS, SEPARATORS, CASINGS),
)
def test_report_covers_resolution_video_audio_separator_and_case_product(
    resolution, video, audio, separator, casing,
):
    title = release_title(
        (resolution[0], "WEB-DL", video[0], "10bit", audio[0]),
        separator,
        casing,
    )
    expected = " · ".join((
        "① " + resolution[1], "WEB-DL", video[1], "10bit", audio[1], audio[2],
    ))
    assert specification_row({"title": title}) == expected


@pytest.mark.parametrize("alias,expected", (
    ("Dolby Vision", "DV"),
    ("DoVi", "DV"),
    ("HDR10+", "HDR10+"),
    ("HDR10Plus", "HDR10+"),
    ("HDR10", "HDR10"),
    ("HLG", "HLG"),
    ("SDR", "SDR"),
))
@pytest.mark.parametrize("separator", SEPARATORS)
def test_each_dynamic_range_family_reaches_the_report(alias, expected, separator):
    title = release_title(("2160p", "WEB-DL", alias, "HEVC", "DDP5.1"), separator)
    assert specification_row({"title": title}) == (
        f"① 2160p · WEB-DL · {expected} · HEVC · DD+ · 6ch环绕"
    )


@pytest.mark.parametrize("suffix,expected", (
    ("480p.DVDRip.MPEG-2.MP2.2CH", "480p · DVD · MPEG-2 · MP2 · 2ch立体"),
    ("576p.BDRip.XviD.MP3.2CH", "576p · BluRay · Xvid · MP3 · 2ch立体"),
    ("1080i.HDTV.VC-1.AC3.5.1", "1080i · HDTV · VC-1 · DD · 6ch环绕"),
    ("2160p.REMUX.H.265.DTS-HD.HRA.7.1", "2160p · REMUX · HEVC · DTS-HD HRA · 8ch环绕"),
    ("1080p.WEBRip.H264.HE-AAC.2CH", "1080p · WEBRip · AVC · HE-AAC · 2ch立体"),
    ("1080p.WEB-DL.VP8.Vorbis.2CH", "1080p · WEB-DL · VP8 · Vorbis · 2ch立体"),
    ("1080p.BluRay.AVC.ALAC.6CH", "1080p · BluRay · AVC · ALAC · 6ch环绕"),
    ("1080p.BluRay.AVC.LPCM.5.1", "1080p · BluRay · AVC · PCM · 6ch环绕"),
))
def test_legacy_and_profiled_formats_remain_visible(suffix, expected):
    assert specification_row({"title": "Feature.2026." + suffix}) == "① " + expected


@pytest.mark.parametrize("title,expected", (
    ("剧集_1080P_WEB_DL_H_264_DDP5_1", "① 1080p · WEB-DL · AVC · DD+ · 6ch环绕"),
    ("Feature.2160p.Blu-Ray.H-265.DTS_HD_MA_7_1", "① 2160p · BluRay · HEVC · DTS-HD MA · 8ch环绕"),
    ("Feature.1080p.WEBRip.h264.HE_AAC.2_0", "① 1080p · WEBRip · AVC · HE-AAC · 2ch立体"),
    ("Feature.1080p.WEB-DL.AVC.DD51", "① 1080p · WEB-DL · AVC · DD · 6ch环绕"),
    ("Feature.2160p.REMUX.HEVC.TrueHD.7.1.4.Atmos", "① 2160p · REMUX · HEVC · TrueHD · 12ch沉浸"),
    ("Feature.2160p.REMUX.HEVC.DTS-X.7.1.4", "① 2160p · REMUX · HEVC · DTS:X · 12ch沉浸"),
))
def test_compound_aliases_and_height_channels_survive_internal_separators(title, expected):
    assert specification_row({"title": title}) == expected


@pytest.mark.parametrize("title", (
    "Feature.2026.1080p.H.264.AAC2.0",
    "Feature_2026_1080p_H.264_AAC2.0",
))
def test_fullwidth_forms_do_not_drop_known_capabilities(title):
    fullwidth = "".join(chr(ord(value) + 0xFEE0) if "!" <= value <= "~" else value for value in title)
    assert specification_row({"title": fullwidth}) == "① 1080p · AVC · AAC · 2ch立体"


@pytest.mark.parametrize("title,expected", (
    ("Feature 2.0 (2026)", "① ?ch"),
    ("Feature.2026.1080p.x264.AAC.v2.0", "① 1080p · AVC · AAC · ?ch"),
    ("Atmosphere.2026.1080p.AAC", "① 1080p · AAC · ?ch"),
    ("Feature.2026.1080p.AAC.44.1kHz", "① 1080p · AAC · ?ch"),
    ("Feature.2026.1080p.AAC.1.0Mbps", "① 1080p · AAC · ?ch"),
    ("Feature.2026.1080p.DTSx264", "① 1080p · ?ch"),
    ("Feature.2026.1080p.StereoType", "① 1080p · ?ch"),
    ("Feature.2026.1080p.10bit", "① 1080p · 10bit · ?ch"),
    ("Feature.2026.1080p.Dual.Audio", "① 1080p · ?ch"),
))
def test_absent_capabilities_stay_unknown_and_decimals_are_not_channels(title, expected):
    assert specification_row({"title": title}) == expected


def test_container_and_frame_rate_do_not_imply_codec_or_audio_channels():
    assert specification_row({"title": "Feature.2026.1080p.23.976fps.mkv"}) == (
        "① 1080p · MKV · 23.976fps · ?ch"
    )


@pytest.mark.parametrize("evidence_key", ("sort_title", "sortTitle", "magnet_url", "magnetUrl", "download_url", "downloadUrl"))
def test_same_release_alternate_titles_fill_missing_specs_through_all_public_aliases(evidence_key):
    complete = "Feature.2026.2160p.WEB-DL.HEVC.10bit.DDP5.1"
    value = (
        "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=" + quote(complete)
        if "url" in evidence_key.lower() else complete
    )
    assert specification_row({"title": "Feature.2026.2160p", evidence_key: value}) == (
        "① 2160p · WEB-DL · HEVC · 10bit · DD+ · 6ch环绕"
    )


@pytest.mark.parametrize("fallback", (
    "Feature.2026.2160p.WEB-DL.HEVC.DDP5.1.Atmos",
    "Feature.2026.1080p.BluRay.AVC.DDP5.1.Atmos",
    "Feature.2026.1080p.WEB-DL.HEVC.DDP5.1.Atmos",
    "Feature.2026.1080p.WEB-DL.AVC.DTS5.1.Atmos",
    "Feature.2026.1080p.WEB-DL.AVC.SDR.AAC2.0",
))
def test_conflicting_fallback_cannot_upgrade_known_primary_specs(fallback):
    assert specification_row({
        "title": "Feature.2026.1080p.WEB-DL.AVC.HDR10.AAC",
        "sort_title": fallback,
    }) == "① 1080p · WEB-DL · HDR10 · AVC · AAC · ?ch"


@pytest.mark.parametrize("primary,fallback,expected", (
    (
        "Feature.2160p.DV.HEVC",
        "Feature.2160p.DV.HDR10+.HEVC.DDP5.1",
        "① 2160p · DV · HDR10+ · HEVC · DD+ · 6ch环绕",
    ),
    (
        "Feature.2160p.HDR.HEVC",
        "Feature.2160p.HDR10+.HEVC.DDP5.1",
        "① 2160p · HDR10+ · HEVC · DD+ · 6ch环绕",
    ),
    (
        "Feature.2160p.HDR10.HEVC",
        "Feature.2160p.HDR10+.HEVC.DDP5.1",
        "① 2160p · HDR10+ · HEVC · DD+ · 6ch环绕",
    ),
    (
        "Feature.2160p.DV.HDR10.HEVC",
        "Feature.2160p.DV.HDR10+.HEVC.DDP5.1",
        "① 2160p · DV · HDR10+ · HEVC · DD+ · 6ch环绕",
    ),
    (
        "Feature.2160p.SDR.HEVC",
        "Feature.2160p.DV.HDR10.HEVC.DDP5.1",
        "① 2160p · SDR · HEVC · ?ch",
    ),
    (
        "Feature.2160p.HLG.HEVC",
        "Feature.2160p.HDR10.HEVC.DDP5.1",
        "① 2160p · HLG · HEVC · ?ch",
    ),
    (
        "Feature.2160p.HDR10.HEVC",
        "Feature.2160p.HLG.HEVC.DDP5.1",
        "① 2160p · HDR10 · HEVC · ?ch",
    ),
))
def test_hdr_refinements_and_compatible_layers_fill_without_accepting_conflicts(primary, fallback, expected):
    assert specification_row({"title": primary, "sort_title": fallback}) == expected


@pytest.mark.parametrize("first,second,expected", (
    (
        "Feature.2160p.WEB-DL.HEVC.AAC2.0",
        "Feature.2160p.BluRay.HDR10.HEVC.TrueHD7.1.Atmos",
        "① 2160p · WEB-DL · HEVC · AAC · 2ch立体",
    ),
    (
        "Feature.2160p.HEVC.AAC2.0",
        "Feature.2160p.HDR10.HEVC.DTS7.1.Atmos",
        "① 2160p · HEVC · AAC · 2ch立体",
    ),
    (
        "Feature.2160p.SDR.HEVC",
        "Feature.2160p.DV.HDR10.HEVC.DDP5.1",
        "① 2160p · SDR · HEVC · ?ch",
    ),
))
def test_later_magnet_facts_must_agree_with_already_accepted_sort_title(first, second, expected):
    assert specification_row({
        "title": "Feature.2160p.HEVC",
        "sort_title": first,
        "magnet_url": "magnet:?xt=urn:btih:" + "b" * 40 + "&dn=" + quote(second),
    }) == expected


def test_twelve_dense_format_results_keep_all_rows_and_callback_targets():
    ranked = [{
        "title": (
            f"Feature{index}.2026.2160p.REMUX.DV.HDR10+.HEVC.12bit."
            "TrueHD.7.1.Atmos.DTS-HD.MA.AAC2.0.FLAC.23.976fps.mkv"
        ),
        "size": (30 + index) * 1024 ** 3,
        "seeders": 10,
        "magnet_url": "magnet:?xt=urn:btih:" + f"{index + 1:040x}",
    } for index in range(12)]
    report = format_release_report("Feature", None, ranked, {"total_indexers": 1, "final": True})
    lines = report.splitlines()
    assert len(report) <= 4096
    assert len(lines) == 3 + 12 * 2
    for index in range(12):
        row = lines[3 + index * 2]
        assert row.startswith("①②③④⑤⑥⑦⑧⑨⑩⑪⑫"[index] + " ")
        assert len(row) <= 180
        assert "2160p · REMUX · DV · HDR10+ · HEVC · 12bit" in row
        assert "8ch沉浸" in row
        assert lines[4 + index * 2] == f"   {30 + index} GB｜活种"
    buttons = [button for row in release_keyboard("formats", ranked)[:-1] for button in row]
    assert [button["callback_data"] for button in buttons] == [
        "search:release:formats:" + stable_release_id(item) for item in ranked
    ]
    assert all(len(button["callback_data"].encode()) <= 64 for button in buttons)
