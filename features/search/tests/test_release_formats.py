"""Declared vocabulary coverage plus independent real-world and negative facts."""

from dataclasses import FrozenInstanceError

import pytest

from telepiplex_search.release_formats import (
    FORMAT_RULES,
    FORMAT_TOPOLOGY,
    ReleaseSpecs,
    parse_release_specs,
)


ALIASES = [(rule, example) for rule in FORMAT_RULES for example in rule.examples]


@pytest.mark.parametrize("rule,alias", ALIASES, ids=[f"{rule.dimension}-{alias}" for rule, alias in ALIASES])
@pytest.mark.parametrize("wrapper", ["{}", "Movie.2026.[{}]-GROUP", "剧集【{}】资源"])
def test_every_declared_alias_can_be_recognized_in_release_boundaries(rule, alias, wrapper):
    specs = parse_release_specs(wrapper.format(alias))
    value = getattr(specs, rule.dimension)
    if rule.dimension == "immersive":
        assert value is True
    elif rule.dimension == "channels":
        # Semantic channel words need release context rather than a title word.
        specs = parse_release_specs("Movie.2026.1080p.AAC." + alias)
        assert specs.channels == int(rule.label.removesuffix("ch"))
    elif isinstance(value, tuple):
        assert rule.label in value
    else:
        assert value == rule.label


@pytest.mark.parametrize("separator", [" ", ".", "_", "-", "–", "—"])
@pytest.mark.parametrize("video,audio", [
    ("H.264", "Dolby Digital Plus"),
    ("H.265", "DTS-HD MA"),
    ("VP-9", "HE-AAC"),
    ("MPEG-2", "DTS:X Pro"),
])
def test_compound_alias_internal_separators_and_case_are_release_independent(separator, video, audio):
    def variant(value):
        for old in (".", "-", " "):
            value = value.replace(old, separator)
        return value.swapcase()

    title = separator.join(("Movie", "2026", "1080p", variant(video), variant(audio), "5.1"))
    specs = parse_release_specs(title)
    expected_video = {"H.264": "AVC", "H.265": "HEVC", "VP-9": "VP9", "MPEG-2": "MPEG-2"}[video]
    expected_audio = {"Dolby Digital Plus": "DD+", "DTS-HD MA": "DTS-HD MA", "HE-AAC": "HE-AAC", "DTS:X Pro": "DTS:X Pro"}[audio]
    assert specs.video_codec == expected_video
    assert specs.audio_codecs == (expected_audio,)
    assert specs.channels == 6


@pytest.mark.parametrize("title,expected", [
    ("The.Wire.S01.2160p.x265.10bit.DTS-HD.MA.5.1[TheUpscaler]", dict(resolution="2160p", video_codec="HEVC", bit_depth="10bit", audio_codecs=("DTS-HD MA",), channels=6)),
    ("Documentary.2026.4320p.AV1.12bit.HDR10+.Opus.7.1.mkv", dict(resolution="4320p", video_codec="AV1", bit_depth="12bit", dynamic_range=("HDR10+",), audio_codecs=("Opus",), channels=8, container="MKV")),
    ("Concert.2026.1080i.1920x1080.HDTV.H.264.PCM.2.0.59.94fps.ts", dict(resolution="1080i", source="HDTV", video_codec="AVC", audio_codecs=("PCM",), channels=2, frame_rate="59.94fps", container="MPEG-TS")),
    ("Movie.2026.2160p.BluRay.REMUX.DoVi.HDR10.HEVC.TrueHD7.1.Atmos.m2ts", dict(source="REMUX", dynamic_range=("DV", "HDR10"), audio_codecs=("TrueHD",), channels=8, immersive=True, container="M2TS")),
    ("剧集_1080Ｐ_ＷＥＢ－ＤＬ_Ｈ．２６５_ＤＤＰ５．１", dict(resolution="1080p", source="WEB-DL", video_codec="HEVC", audio_codecs=("DD+",), channels=6)),
    ("Movie.2026.1080p.AAC2.0.DTS-HD.MA.5.1", dict(audio_codecs=("DTS-HD MA", "AAC"), channels=6)),
    ("Movie.2026.1080p.AAC.10.0.2", dict(audio_codecs=("AAC",), channels=12)),
    ("Movie.2026.576i.MPEG2.MP2.2.0", dict(resolution="576i", video_codec="MPEG-2", audio_codecs=("MP2",), channels=2)),
    ("Feature.2026.2160p.DDP.7.1.JOC", dict(audio_codecs=("DD+",), channels=8, immersive=True)),
    ("Feature.2026.1080p.yuv420p10le.FLAC.Stereo", dict(bit_depth="10bit", audio_codecs=("FLAC",), channels=2)),
])
def test_independent_full_release_examples(title, expected):
    specs = parse_release_specs(title)
    for key, value in expected.items():
        assert getattr(specs, key) == value, (key, specs)


@pytest.mark.parametrize("title", [
    "Movie.3.0.2026",
    "Movie.5.1.2026",
    "Movie.2026.1080p.v2.0.AAC",
    "Movie.2026.1080p.AAC.v2.0",
    "Movie.2026.1080p.AAC.version.2.0",
    "Movie.2026.1080p.AAC.23.976fps",
    "Movie.2026.1080p.AAC.29.97.fps",
    "Movie.2026.1080p.AAC.44.1kHz",
    "Movie.2026.1080p.AAC.3.0GB",
    "Movie.2026.1080p.AAC.7.1Mbps",
    "Movie.2026.1080p.AAC.1.0Mbps",
    "Movie.2026.1080p.AAC.2.0kbps",
    "Movie.2026.1080p.H.264",
    "Movie.2026.1080p.HEVC.Main10",
    "Movie.2026.1080p.H264.L4.1.AAC",
    "Movie.2026.1080p.HEVC.Main.10.L5.1.AAC",
    "Movie.2026.1080p.DV.Profile.8.1.HEVC.AAC",
    "Movie.2026.1080p.DoVi.8.1.AAC",
])
def test_title_year_version_measurement_and_codec_numbers_are_not_channels(title):
    assert parse_release_specs(title).channels is None


@pytest.mark.parametrize("title", [
    "Atmosphere.2026.1080p.AAC", "Feature.1080p.DTSx264", "Feature.1080p.AC4",
    "Feature.1080p.DTS-HD.MA.7.1", "Feature.1080p.JOC", "Feature.1080p.Dual.Audio",
])
def test_immersion_is_not_inferred_from_substrings_or_codec_capability(title):
    assert parse_release_specs(title).immersive is False


def test_compound_codecs_suppress_cores_but_keep_independent_tracks():
    specs = parse_release_specs("Movie.1080p.DTS-HD.MA.7.1.DTS:X.Pro.AAC-LC.2.0.HE-AACv2")
    assert specs.audio_codecs == ("DTS-HD MA", "DTS:X Pro", "HE-AAC", "AAC")
    assert specs.channels == 8
    assert specs.immersive


@pytest.mark.parametrize("marker", ["10bit", "Main10", "BT2020", "HEVC", "UltraHD", "mkv", "mp4"])
def test_depth_profile_color_codec_and_container_do_not_imply_hdr(marker):
    assert parse_release_specs("Movie.2026.1080p." + marker).dynamic_range == ()


def test_unknown_explicit_facts_and_truncated_titles_stay_unknown():
    assert parse_release_specs(None) == ReleaseSpecs()
    assert parse_release_specs("Movie.2026.S01E01") == ReleaseSpecs()
    short = parse_release_specs("The Wire (2002) S01-S05 (1080p BluRay x265 HEVC 10bit")
    assert short.channels is None and short.audio_codecs == ()
    assert short.video_codec == "HEVC" and short.bit_depth == "10bit"
    assert parse_release_specs("Movie.1080p.BluRay.H.26").video_codec == ""


def test_container_does_not_manufacture_source_or_codec():
    assert parse_release_specs("Movie.2026.mp4").video_codec == ""
    assert parse_release_specs("Movie.2026.mkv").source == ""
    assert parse_release_specs("Movie.2026.1080p.HDTV.ts").source == "HDTV"
    assert parse_release_specs("TS.2026.S01E01").source == ""
    assert parse_release_specs("Movie.2026.1920x800").resolution == ""
    assert parse_release_specs("Movie.wmv").video_codec == ""
    assert parse_release_specs("Movie.wmv").container == "WMV"
    assert parse_release_specs("Movie.1080p.MPEG-1.Layer.II").video_codec == ""


def test_audio_bit_depth_is_not_reported_as_video_depth():
    assert parse_release_specs("Movie.1080p.AVC.LPCM.16bit.48kHz").bit_depth == ""
    assert parse_release_specs("Movie.1080p.AVC.10bit.LPCM.16bit").bit_depth == "10bit"
    assert parse_release_specs("Movie.1080p.FLAC.x264.10bit").bit_depth == "10bit"


def test_numeric_bitrate_after_layout_is_not_an_extra_height_channel():
    specs = parse_release_specs("Movie.1080p.TrueHD.7.1.3.0Mbps")
    assert specs.channels == 8
    assert parse_release_specs("Movie.1080p.DD5.1.640kbps").channels == 6
    assert parse_release_specs("Movie.1080p.PCM2.0.48kHz").channels == 2


def test_audio_descriptor_compounds_and_title_words_are_not_phantom_streams():
    assert parse_release_specs("Movie.1080p.MPEG-4.AAC").video_codec == ""
    assert parse_release_specs("Ape.2012.1080p.BluRay.x264.AAC2.0").audio_codecs == ("AAC",)
    assert parse_release_specs("The.Cook.1989.1080p.BluRay.H264.AC3.5.1").audio_codecs == ("DD",)
    assert parse_release_specs("Cam.2018.1080p.WEB-DL.AAC2.0").source == "WEB-DL"
    assert parse_release_specs("Opus.S01E01.1080p.WEB-DL.AAC2.0").audio_codecs == ("AAC",)
    assert parse_release_specs("[APE].Movie.2026.1080p").audio_codecs == ("APE",)


def test_mux_is_not_automatically_claimed_as_remux():
    assert parse_release_specs("Feature.1080p.WEBMux.AAC2.0").source == "WEBMux"
    assert parse_release_specs("Feature.1080p.WEB-DLmux.AAC2.0").source == "WEBMux"
    assert parse_release_specs("Feature.1080p.BDMux.AAC2.0").source == "BluRay"


@pytest.mark.parametrize("marker,resolution,rate", [
    ("1080p60", "1080p", "60fps"),
    ("1080i50", "1080i", "50fps"),
    ("2160p23.976", "2160p", "23.976fps"),
])
def test_attached_explicit_scan_and_rate_are_not_channels(marker, resolution, rate):
    specs = parse_release_specs("Movie.2026." + marker + ".H264.AAC")
    assert specs.resolution == resolution
    assert specs.frame_rate == rate
    assert specs.channels is None


def test_combined_hdr10_plus_dv_does_not_lose_plus():
    assert parse_release_specs("Movie.2160p.HDR10+DV.HEVC").dynamic_range == ("DV", "HDR10+")
    assert parse_release_specs("Movie.2160p.HDR10+DoVi.HEVC").dynamic_range == ("DV", "HDR10+")


def test_explicit_dynamic_range_refinements_and_not_assumed_base_layer():
    assert parse_release_specs("Movie.DV").dynamic_range == ("DV",)
    assert parse_release_specs("Movie.DV.HDR.HDR10.HDR10+").dynamic_range == ("DV", "HDR10+")
    assert parse_release_specs("Movie.HLG.SDR").dynamic_range == ("HLG",)
    assert parse_release_specs("Movie.Main10").bit_depth == ""


@pytest.mark.parametrize("layout,count", [
    ("1.0", 1), ("2.0", 2), ("2.1", 3), ("3.0", 3), ("3.1", 4),
    ("4.0", 4), ("4.1", 5), ("5.0", 5), ("5.1", 6), ("6.0", 6),
    ("6.1", 7), ("7.0", 7), ("7.1", 8), ("3.1.2", 6), ("5.1.2", 8),
    ("5.1.4", 10), ("7.1.2", 10), ("7.1.4", 12), ("9.1.6", 16), ("24ch", 24),
])
def test_explicit_channel_layout_families(layout, count):
    assert parse_release_specs("Movie.2026.1080p.PCM." + layout).channels == count


def test_catalog_and_cached_results_cannot_be_mutated():
    specs = parse_release_specs("Movie.1080p.AAC2.0")
    with pytest.raises(FrozenInstanceError):
        specs.channels = 8
    with pytest.raises(TypeError):
        FORMAT_TOPOLOGY["video_codec"] = ()
    assert parse_release_specs("Movie.1080p.AAC2.0").channels == 2
    assert set(FORMAT_TOPOLOGY) == {"resolution", "source", "dynamic_range", "video_codec", "bit_depth", "audio_codecs", "immersive", "channels", "container", "frame_rate"}
