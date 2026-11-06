"""Finite, inspectable vocabulary for facts explicitly present in release names.

This is a title parser, not a media probe. Container names do not imply codecs,
codec/profile names do not imply HDR or channels, and absent facts stay absent.
Codec families follow the Matroska codec registry; release-source aliases follow
the naming conventions used by release indexers rather than a quality ranking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from types import MappingProxyType
import re
import unicodedata


_SEP = r"[ .-]*"
_LEFT = r"(?<![a-z0-9])"
_RIGHT = r"(?![a-z0-9])"
_AUDIO_RIGHT = r"(?=$|[^a-z0-9]|\d)"
_AAC_PROFILE = rf"(?:{_SEP}v[12]|{_SEP}[12](?![.]\d))?"


@dataclass(frozen=True)
class FormatRule:
    """One canonical family, with representative aliases and compiled matcher."""

    dimension: str
    label: str
    pattern: str
    examples: tuple[str, ...]
    expression: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "expression", re.compile(self.pattern, re.I))


def _rule(dimension, label, pattern, *examples, right=_RIGHT):
    bounded = _LEFT + "(?:" + pattern + ")" + right
    # Chinese technical phrases may directly touch Latin labels on either
    # side: e.g. 2160p蓝光杜比视界HDR10. Latin aliases still need boundaries.
    han = re.findall(r"[\u3400-\u9fff]+", pattern)
    expression = "(?:" + bounded + ")|" + "|".join(map(re.escape, han)) if han else bounded
    return FormatRule(dimension, label, expression, examples)


# Order is semantic: explicit scan mode before pixel dimensions; compound
# audio codecs before their cores; specific HDR before generic HDR. Aliases are
# not an assertion that every arbitrary concatenation has a defined meaning.
FORMAT_RULES = (
    _rule("resolution", "1080p", rf"(?:4k|2160p){_SEP}(?:to|2){_SEP}1080p", "4Kto1080p", "2160p-to-1080p"),
    *(_rule("resolution", f"{height}{scan}", rf"{height}{scan}", f"{height}{scan}")
      for height, scan in (
          (4320, "p"), (2160, "p"), (1440, "p"), (1080, "p"),
          (1080, "i"), (720, "p"), (576, "p"), (576, "i"),
          (540, "p"), (480, "p"), (480, "i"), (360, "p"), (240, "p"),
      )),
    _rule("resolution", "4320p", r"8k|(?:7680|8192)\s*[x×]\s*4320", "8K", "7680x4320", "8192×4320"),
    _rule("resolution", "2160p", r"4k|uhd|(?:3840|4096)\s*[x×]\s*2160", "4K", "UHD", "3840x2160", "4096×2160"),
    _rule("resolution", "1440p", r"2560\s*[x×]\s*1440", "2560x1440"),
    _rule("resolution", "1080p", rf"1920\s*[x×]\s*1080|fhd|full{_SEP}hd", "1920x1080", "FHD", "FullHD"),
    _rule("resolution", "720p", r"1280\s*[x×]\s*720", "1280x720"),
    _rule("resolution", "576p", r"720\s*[x×]\s*576", "720x576"),
    _rule("resolution", "480p", r"(?:640|720)\s*[x×]\s*480", "640x480", "720x480"),
    _rule("resolution", "360p", r"640\s*[x×]\s*360", "640x360"),
    _rule("resolution", "240p", r"(?:320|426)\s*[x×]\s*240", "320x240", "426x240"),
    _rule("source", "REMUX", rf"(?:bd|uhd|web){_SEP}remux|remux", "REMUX", "BDRemux", "UHDRemux", "WEBRemux"),
    _rule("source", "WEBMux", rf"web{_SEP}(?:dl{_SEP})?mux", "WEBMux", "WEB-DLmux"),
    _rule("source", "WEB-DL", rf"web{_SEP}dl", "WEB-DL", "WEB.DL", "WEBDL"),
    _rule("source", "WEBRip", rf"web{_SEP}rip", "WEBRip", "WEB-Rip"),
    _rule("source", "BluRay", rf"blu{_SEP}ray|(?:uhd{_SEP})?b[dr]{_SEP}rip|bd{_SEP}(?:iso|mux)|uhd2bd|bd(?:25|50|66|100)|bd{_SEP}light|bd|蓝光|藍光", "BluRay", "Blu-Ray", "BDRip", "UHDBDRip", "BRRip", "BDISO", "BDMux", "BD50", "BDLight", "BD", "蓝光", "藍光"),
    _rule("source", "HD-DVD", rf"hd{_SEP}dvd(?:{_SEP}rip)?", "HD-DVD", "HDDVDRip"),
    _rule("source", "HDTV", rf"(?:hd|uhd){_SEP}tv(?:{_SEP}rip)?", "HDTV", "HDTVRip", "UHDTV"),
    _rule("source", "PDTV", r"pdtv", "PDTV"),
    _rule("source", "SDTV", r"sdtv", "SDTV"),
    _rule("source", "TVRip", rf"tv{_SEP}rip", "TVRip"),
    _rule("source", "DSR", rf"dsr(?:{_SEP}rip)?|dsrip", "DSR", "DSRip"),
    _rule("source", "DVDSCR", rf"dvd{_SEP}scr(?:eener)?|screener", "DVDSCR", "DVDScreener", "Screener"),
    _rule("source", "DVD", rf"dvd(?:{_SEP}rip)?|dvd[59]", "DVD", "DVDRip", "DVD9"),
    _rule("source", "VHS", rf"vhs(?:{_SEP}rip)?", "VHS", "VHSRip"),
    _rule("source", "VCD", rf"(?:s)?vcd(?:{_SEP}rip)?", "VCD", "SVCDRip"),
    _rule("source", "CAM", rf"(?:hd)?cam(?:{_SEP}rip)?", "CAM", "HDCAM", "CAMRip"),
    _rule("source", "TELESYNC", rf"hd{_SEP}ts|telesync", "HDTS", "HD-TS", "TELESYNC"),
    _rule("source", "TELECINE", rf"hd{_SEP}tc|telecine", "HDTC", "HD-TC", "TELECINE"),
    _rule("source", "WORKPRINT", rf"work{_SEP}print", "WORKPRINT", "Work-Print"),
    _rule("dynamic_range", "DV", rf"dv|dovi|dolby{_SEP}vision|杜比视界|杜比視界", "DV", "DoVi", "Dolby Vision", "杜比视界"),
    _rule("dynamic_range", "HDR10+", rf"hdr{_SEP}10(?:\+|plus|p)|hdr{_SEP}10{_SEP}plus", "HDR10+", "HDR10Plus", "HDR10P", "HDR 10 Plus", "HDR10+DV", right=rf"(?:{_RIGHT}|(?<=\+)(?=(?:dv|dovi){_RIGHT}))"),
    _rule("dynamic_range", "HDR10", rf"hdr{_SEP}10", "HDR10", "HDR-10"),
    _rule("dynamic_range", "HDR", r"hdr", "HDR"),
    _rule("dynamic_range", "HLG", rf"hlg|hybrid{_SEP}log{_SEP}gamma", "HLG", "Hybrid Log Gamma"),
    _rule("dynamic_range", "SDR", r"sdr", "SDR"),
    _rule("video_codec", "HEVC", rf"hevc|h{_SEP}265|x265", "HEVC", "H.265", "H265", "x265"),
    _rule("video_codec", "AVC", rf"avc|h{_SEP}264|x264", "AVC", "H.264", "H264", "x264"),
    _rule("video_codec", "AV1", r"av1|av01", "AV1", "AV01"),
    _rule("video_codec", "VVC", rf"vvc|h{_SEP}266|x266", "VVC", "H.266", "x266"),
    _rule("video_codec", "VP9", rf"vp{_SEP}9", "VP9", "VP-9"),
    _rule("video_codec", "VP8", rf"vp{_SEP}8", "VP8", "VP-8"),
    _rule("video_codec", "VC-1", rf"vc{_SEP}1|wmv3|wvc1", "VC-1", "VC1", "WMV3", "WVC1"),
    _rule("video_codec", "MPEG-2", rf"mpeg{_SEP}2|h{_SEP}262", "MPEG2", "MPEG-2", "H.262"),
    _rule("video_codec", "MPEG-1", rf"mpeg{_SEP}1", "MPEG1", "MPEG-1"),
    _rule("video_codec", "Xvid", r"xvid", "Xvid"),
    _rule("video_codec", "DivX", r"divx", "DivX"),
    _rule("video_codec", "MPEG-4", rf"mpeg{_SEP}4(?:{_SEP}asp)?|mp4v", "MPEG4", "MPEG-4 ASP", "MP4V"),
    _rule("video_codec", "AVS3", rf"avs{_SEP}3", "AVS3"),
    _rule("video_codec", "AVS2", rf"avs{_SEP}2", "AVS2"),
    _rule("video_codec", "AVS", rf"avs{_SEP}1|cavs", "AVS1", "CAVS"),
    _rule("video_codec", "FFV1", r"ffv1", "FFV1"),
    _rule("video_codec", "ProRes", rf"pro{_SEP}res(?:{_SEP}(?:422|4444)(?:{_SEP}(?:hq|lt|xq))?)?", "ProRes", "ProRes 422 HQ", "ProRes4444"),
    _rule("video_codec", "Theora", r"theora", "Theora"),
    _rule("video_codec", "RealVideo", rf"real{_SEP}video|rv(?:10|20|30|40)", "RealVideo", "RV40"),
    _rule("video_codec", "WMV", r"wmv[12]?", "WMV", "WMV2"),
    _rule("video_codec", "H.263", rf"h{_SEP}263", "H.263", "H263"),
    _rule("video_codec", "MJPEG", rf"mjpeg|motion{_SEP}jpeg", "MJPEG", "Motion JPEG"),
    *(_rule("bit_depth", f"{bits}bit", rf"{bits}{_SEP}bits?|(?:yuv|yuva|gbr)[a-z0-9]*p{bits}(?:le|be)?", f"{bits}bit", f"{bits}-bit", f"yuv420p{bits}le")
      for bits in (8, 10, 12, 16)),
    _rule("bit_depth", "10bit", r"hi10p|high10p", "Hi10P", "High10P"),
    _rule("bit_depth", "10bit", r"p010(?:le|be)?", "P010", "P010LE"),
    _rule("bit_depth", "12bit", r"p012(?:le|be)?", "P012", "P012LE"),
    _rule("audio_codecs", "TrueHD", rf"true{_SEP}hd|dolby{_SEP}true{_SEP}hd", "TrueHD", "True-HD", "Dolby TrueHD", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "DTS-HD MA", rf"dts{_SEP}(?:hd{_SEP})?(?:ma|master{_SEP}audio)", "DTS-HD MA", "DTS-HDMA", "DTSMA", "DTS Master Audio", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "DTS-HD HRA", rf"dts{_SEP}hd{_SEP}(?:hra|high{_SEP}resolution(?:{_SEP}audio)?)", "DTS-HD HRA", "DTSHDHRA", "DTS-HD High Resolution Audio", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "DTS-HD", rf"dts{_SEP}hd", "DTS-HD", "DTSHD", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "DTS:X Pro", rf"dts[ .:-]*x{_SEP}pro", "DTS:X Pro", "DTS-X-Pro", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "DTS:X", rf"dts[ .:-]*x(?!26[45])", "DTS:X", "DTS-X", "DTSX", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "DTS-ES", rf"dts{_SEP}es(?:{_SEP}(?:matrix|discrete))?", "DTS-ES", "DTSES", "DTS ES Discrete", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "DTS96/24", rf"dts{_SEP}96[ /.-]*24", "DTS96/24", "DTS 96-24", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "DTS", r"dts", "DTS", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "DD+", rf"e{_SEP}ac{_SEP}3|ddp|dd\+|dolby{_SEP}digital{_SEP}plus", "EAC3", "E-AC-3", "DDP", "DD+", "Dolby Digital Plus", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "DD", rf"ac{_SEP}3|dd|dolby{_SEP}digital", "AC3", "AC-3", "DD", "Dolby Digital", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "AC-4", rf"ac{_SEP}4", "AC4", "AC-4", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "MLP", r"mlp", "MLP", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "FLAC", r"flac", "FLAC", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "PCM", rf"lpcm|pcm(?:{_SEP}[suf](?:8|16|24|32)(?:le|be)?)?", "LPCM", "PCM", "PCM S24LE", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "HE-AAC", rf"he{_SEP}aac{_AAC_PROFILE}|aac{_SEP}(?:he{_AAC_PROFILE}|sbr|plus)|e{_SEP}aac\+|aac\+\+?", "HE-AAC", "HE-AACv2", "AAC-HE", "AAC-SBR", "AAC+", "AAC++", "AACplus", "eAAC+", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "AAC", rf"(?:mpeg{_SEP}4{_SEP})?aac(?:{_SEP}lc)?|lc{_SEP}aac", "AAC", "AAC-LC", "LC-AAC", "MPEG4 AAC", "MPEG-4 AAC", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "ALAC", rf"alac|apple{_SEP}lossless", "ALAC", "Apple Lossless", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "Opus", r"opus", "Opus", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "Vorbis", r"vorbis", "Vorbis", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "MP3", rf"mp3|mpeg{_SEP}(?:1{_SEP})?(?:layer{_SEP}(?:3|iii)|audio{_SEP}layer{_SEP}(?:3|iii))", "MP3", "MPEG Layer III", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "MP2", rf"mp2|mpeg{_SEP}(?:1{_SEP})?layer{_SEP}(?:2|ii)", "MP2", "MPEG Layer II", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "MP1", rf"mp1|mpeg{_SEP}(?:1{_SEP})?layer{_SEP}(?:1|i)", "MP1", "MPEG Layer I", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "WavPack", r"wavpack", "WavPack", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "TTA", rf"tta|true{_SEP}audio", "TTA", "True Audio", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "APE", r"ape|monkey'?s{0,1}[ .-]*audio", "APE", "Monkey's Audio", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "WMA", rf"wma(?:{_SEP}(?:pro|lossless))?", "WMA", "WMA Pro", "WMA Lossless", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "DSD", r"dsd(?:64|128|256|512)?|dst", "DSD", "DSD128", "DST", right=_AUDIO_RIGHT),
    _rule("audio_codecs", "RealAudio", rf"real{_SEP}audio|raac|ralf", "RealAudio", "RAAC", "RALF", right=_AUDIO_RIGHT),
    _rule("immersive", "Atmos", rf"(?:dolby{_SEP})?atmos|杜比全景声|杜比全景聲", "Atmos", "Dolby Atmos", "杜比全景声"),
    _rule("immersive", "DTS:X", rf"dts[ .:-]*x(?:{_SEP}pro)?", "DTS:X", "DTSX", "DTS:X Pro"),
    _rule("immersive", "Auro-3D", rf"auro{_SEP}3d", "Auro3D", "Auro-3D"),
    _rule("channels", "2ch", r"stereo|立体声|立體聲", "Stereo", "立体声", "立體聲"),
    _rule("channels", "1ch", r"mono|单声道|單聲道", "Mono", "单声道", "單聲道"),
    _rule("channels", "4ch", r"quad(?:raphonic)?", "Quad", "Quadraphonic"),
    _rule("channels", "2ch", r"binaural", "Binaural"),
    _rule("container", "MKV", r"mkv|matroska", "MKV", "Matroska"),
    _rule("container", "MP4", r"mp4", "MP4"),
    _rule("container", "M4V", r"m4v", "M4V"),
    _rule("container", "AVI", r"avi", "AVI"),
    _rule("container", "MOV", r"mov|quicktime", "MOV", "QuickTime"),
    _rule("container", "WebM", r"webm", "WebM"),
    _rule("container", "M2TS", r"m2ts|mts", "M2TS", "MTS"),
    _rule("container", "MPEG-TS", r"mpeg[ .-]*ts", "MPEG-TS"),
    _rule("container", "MPEG-PS", r"mpeg[ .-]*ps|vob", "MPEG-PS", "VOB"),
    _rule("container", "FLV", r"flv", "FLV"),
    _rule("container", "ASF", r"asf", "ASF"),
    _rule("container", "WMV", r"wmv", "WMV"),
    _rule("container", "RMVB", r"rmvb|rm", "RMVB", "RM"),
    _rule("container", "OGM", r"ogm|ogv", "OGM", "OGV"),
    _rule("container", "ISO", r"iso", "ISO"),
    *(_rule("frame_rate", label + "fps", re.escape(value) + rf"{_SEP}fps", value + "fps")
      for value, label in (
          ("23.976", "23.976"), ("23.98", "23.98"), ("24", "24"), ("25", "25"),
          ("29.97", "29.97"), ("30", "30"), ("48", "48"), ("50", "50"),
          ("59.94", "59.94"), ("60", "60"), ("100", "100"), ("119.88", "119.88"),
          ("120", "120"), ("144", "144"), ("240", "240"),
      )),
    *(_rule("frame_rate", label + "fps", value + rf"\s*/\s*1001{_SEP}fps", value + "/1001fps")
      for value, label in (("24000", "23.976"), ("30000", "29.97"), ("60000", "59.94"), ("120000", "119.88"))),
)

FORMAT_TOPOLOGY = MappingProxyType({
    dimension: tuple(rule for rule in FORMAT_RULES if rule.dimension == dimension)
    for dimension in dict.fromkeys(rule.dimension for rule in FORMAT_RULES)
})

# Edges describe family refinement, not additional observed tracks. A single
# DTS-HD MA label does not manufacture a second DTS track or any channel count.
AUDIO_CODEC_PARENTS = MappingProxyType({
    "HE-AAC": "AAC",
    "DTS-HD MA": "DTS-HD",
    "DTS-HD HRA": "DTS-HD",
    "DTS-HD": "DTS",
    "DTS-ES": "DTS",
    "DTS96/24": "DTS",
    "DTS:X Pro": "DTS:X",
    "DTS:X": "DTS",
})


@dataclass(frozen=True)
class ReleaseSpecs:
    resolution: str = ""
    source: str = ""
    dynamic_range: tuple[str, ...] = ()
    video_codec: str = ""
    bit_depth: str = ""
    audio_codecs: tuple[str, ...] = ()
    channels: int | None = None
    immersive: bool = False
    container: str = ""
    frame_rate: str = ""


def normalize_release_text(value: object) -> str:
    value = unicodedata.normalize("NFKC", str(value or ""))
    return value.translate(str.maketrans({"_": ".", "–": "-", "—": "-", "−": "-", "·": "."}))


def _matches(dimension, text):
    """Consume compound matches without hiding independent tracks elsewhere."""
    matches = []
    consumed = []
    for rule in FORMAT_TOPOLOGY[dimension]:
        for match in rule.expression.finditer(text):
            if any(match.start() < end and start < match.end() for start, end in consumed):
                continue
            consumed.append(match.span())
            matches.append((rule.label, match.start(), match.end()))
    return matches


def _first(dimension, text):
    return next((label for label, _, _ in _matches(dimension, text)), "")


_LAYOUT = re.compile(r"(?<!\d)(\d{1,2})[.](\d)(?:[.](\d))?(?!\d)", re.I)
_COUNT = re.compile(r"(?<!\d)(?<!\d\.)(\d{1,2})[ .-]*(?:ch(?:annels?)?|声道|聲道)(?![a-z0-9])", re.I)
_MEASUREMENT = re.compile(r"[ .-]*(?:fps|hz|khz|mhz|[kmgt]?[bi]?b(?:ps|/s)?|[kmgt]?bit(?:s|/s|ps)?|mbps|gbps|版本)(?![a-z])", re.I)
_NUMBER_WITH_UNIT = re.compile(r"(?<!\d)\d{1,5}(?:[.]\d{1,3})?" + _MEASUREMENT.pattern, re.I)
_COMPACT_LAYOUTS = {"10": 1, "20": 2, "21": 3, "30": 3, "31": 4, "40": 4,
                    "41": 5, "50": 5, "51": 6, "60": 6, "61": 7, "70": 7, "71": 8}


def _channels(text, matches):
    audio_spans = [(start, end) for _, start, end in matches["audio_codecs"]]
    immersive_spans = [(start, end) for _, start, end in matches["immersive"]]
    technical_spans = [span for dimension in ("resolution", "source", "video_codec", "bit_depth", "audio_codecs", "immersive")
                       for _, *span in matches[dimension]]
    technical_start = min((start for start, _ in technical_spans), default=None)
    counts = []
    # AC3.5.1 must be scanned as a codec followed by 5.1, not the invalid
    # overlapping layout 3.5.1. Preserve offsets for adjacency checks.
    layout_text = list(text)
    for start, end in audio_spans:
        layout_text[start:end] = " " * (end - start)
    layout_text = "".join(layout_text)

    def adjacent(start, end):
        return any(not text[codec_end:start].strip(" .-:/+") for _, codec_end in audio_spans if codec_end <= start) or any(
            not text[end:codec_start].strip(" .-:/+") for codec_start, _ in (*audio_spans, *immersive_spans) if codec_start >= end)

    for layout in _LAYOUT.finditer(layout_text):
        parts = [int(part) for part in layout.groups() if part is not None]
        if layout.group(3) is not None and _NUMBER_WITH_UNIT.match(text, layout.start(3)):
            # In 7.1.3.0Mbps, 3.0 is bitrate, not three height speakers.
            # Keep the valid 7.1 prefix instead of swallowing its LFE digit.
            parts = parts[:2]
        if not (1 <= parts[0] <= 22 and parts[1] <= 2 and all(part <= 8 for part in parts[2:])):
            continue
        if _MEASUREMENT.match(text, layout.end()):
            continue
        preceding = text[layout.start() - 1:layout.start()]
        if preceding and preceding.isascii() and preceding.isalpha() and not adjacent(*layout.span()):
            continue
        # Versions (v2.0), title numbers before the technical suffix, years,
        # sample rates and frame rates are not speaker layouts.
        if re.search(
            r"(?:\bv|\bversion|\brevision|\brev|\bl|\blevel|\bp|\bprofile|\bdv|\bdovi)[ .-]*$",
            text[:layout.start()], re.I,
        ):
            continue
        if (technical_start is not None and layout.start() >= technical_start
                or adjacent(*layout.span())
                or text[layout.end():].startswith(("声道", "聲道"))
                or text.strip() == layout.group()):
            counts.append(sum(parts))
    for explicit in _COUNT.finditer(layout_text):
        preceding = text[explicit.start() - 1:explicit.start()]
        if preceding and preceding.isascii() and preceding.isalpha() and not adjacent(*explicit.span()):
            continue
        count = int(explicit.group(1))
        if 1 <= count <= 32:
            counts.append(count)
    for _, end in audio_spans:
        compact = re.match(r"[ .-]*(10|20|21|30|31|40|41|50|51|60|61|70|71)(?![a-z0-9])", text[end:], re.I)
        if compact:
            counts.append(_COMPACT_LAYOUTS[compact.group(1)])
    for label, start, end in matches["channels"]:
        if (text.strip() == text[start:end] or technical_start is not None and start >= technical_start
                or adjacent(start, end)):
            counts.append(int(label.removesuffix("ch")))
    return max(counts) if counts else None


_FRAME_RATE = re.compile(_LEFT + r"(\d{1,3}(?:[.]\d{1,3})?)[ .-]*(?:fps|frames?[ .-]*(?:per[ .-]*second|/[ .-]*s))" + _RIGHT, re.I)
_FRAME_RATIONAL = re.compile(_LEFT + r"(24000|30000|60000|120000)\s*/\s*1001[ .-]*fps" + _RIGHT, re.I)
_RATIONAL_LABELS = {"24000": "23.976fps", "30000": "29.97fps", "60000": "59.94fps", "120000": "119.88fps"}
_ATTACHED_SCAN_RATE = re.compile(
    _LEFT + r"(?P<scan>(?:4320|2160|1440|1080|720|576|540|480|360|240)[pi])"
    r"(?P<rate>23[.]976|23[.]98|24|25|29[.]97|30|48|50|59[.]94|60|100|119[.]88|120|144|240)(?:fps)?" + _RIGHT,
    re.I,
)


def _frame_rate(text):
    rational = _FRAME_RATIONAL.search(text)
    if rational:
        return _RATIONAL_LABELS[rational.group(1)]
    match = _FRAME_RATE.search(text)
    if match and 1 <= float(match.group(1)) <= 240:
        return match.group(1).rstrip("0").rstrip(".") + "fps" if "." in match.group(1) else match.group(1) + "fps"
    return ""


@lru_cache(maxsize=2048)
def _parse_normalized(text: str) -> ReleaseSpecs:
    # Attached scan/rate is a defined release spelling (1080p60, 1080i50),
    # unlike arbitrary neighboring numbers. Give the rate its explicit unit
    # before channel scanning so its decimal cannot become a speaker layout.
    text = _ATTACHED_SCAN_RATE.sub(lambda match: match["scan"] + "." + match["rate"] + "fps", text)
    matches = {dimension: _matches(dimension, text) for dimension in FORMAT_TOPOLOGY}
    identity = re.search(r"(?<!\d)(?:18|19|20|21)\d{2}(?!\d)|(?<![a-z0-9])s\d{1,2}(?:e\d{1,4})?(?![a-z0-9])", text, re.I)
    anchor = identity.start() if identity else min((start for _, start, _ in matches["resolution"]), default=None)
    ambiguous_words = {
        "audio_codecs": {"APE", "Opus", "FLAC", "WMA", "TTA", "RealAudio"},
        "source": {"CAM", "DVD", "VHS", "VCD", "TVRip", "DVDSCR"},
        "video_codec": {"Theora", "ProRes", "WMV"},
        "immersive": {"Atmos"},
    }
    if anchor is not None:
        for dimension, labels in ambiguous_words.items():
            matches[dimension] = [
                (label, start, end) for label, start, end in matches[dimension]
                if label not in labels or start >= anchor or (
                    text[start - 1:start] in {"[", "(", "【"}
                    and text[end:end + 1] in {"]", ")", "】"}
                )
            ]
    video_text = list(text)
    for _, start, end in matches["audio_codecs"]:
        video_text[start:end] = " " * (end - start)
    # MPEG-1 Layer II is an audio codec compound, not separate MPEG-1 video.
    video_text = "".join(video_text)
    masked_video = _matches("video_codec", video_text)
    allowed_video = {(start, end) for _, start, end in matches["video_codec"]}
    matches["video_codec"] = [value for value in masked_video if value[1:] in allowed_video]
    if re.search(r"[.]wmv$", text, re.I):
        matches["video_codec"] = [value for value in matches["video_codec"] if value[0] != "WMV"]
    depth_matches = []
    for label, start, end in matches["bit_depth"]:
        last_audio = max((stop for _, begin, stop in matches["audio_codecs"] if begin < start), default=-1)
        last_video = max((begin for _, begin, stop in matches["video_codec"] if stop <= start), default=-1)
        explicit_pixel = re.search(r"(?:yuv|yuva|gbr|p0(?:10|12))", text[start:end], re.I)
        if last_audio < 0 or last_video >= last_audio or explicit_pixel:
            depth_matches.append((label, start, end))
    matches["bit_depth"] = depth_matches
    dynamic = list(dict.fromkeys(label for label, _, _ in matches["dynamic_range"]))
    # HDR10+ refines HDR10/HDR; SDR is incompatible with an explicitly named
    # HDR transfer format. DV does not by itself assert an HDR10 base layer.
    if "HDR10+" in dynamic:
        dynamic = [label for label in dynamic if label not in {"HDR10", "HDR"}]
    elif "HDR10" in dynamic:
        dynamic = [label for label in dynamic if label != "HDR"]
    if any(label != "SDR" for label in dynamic):
        dynamic = [label for label in dynamic if label != "SDR"]
    container = _first("container", text)
    if not container and re.search(r"[.]ts$", text, re.I):
        container = "MPEG-TS"  # Bare TS elsewhere could mean TELESYNC.
    return ReleaseSpecs(
        resolution=_first("resolution", text),
        source=next((label for label, _, _ in matches["source"]), ""),
        dynamic_range=tuple(dynamic),
        video_codec=next((label for label, _, _ in matches["video_codec"]), ""),
        bit_depth=next((label for label, _, _ in matches["bit_depth"]), ""),
        audio_codecs=tuple(dict.fromkeys(label for label, _, _ in matches["audio_codecs"])),
        channels=_channels(text, matches),
        immersive=bool(matches["immersive"]) or (
            any(label == "DD+" for label, _, _ in matches["audio_codecs"])
            and re.search(_LEFT + r"joc" + _RIGHT, text, re.I) is not None
        ),
        container=container,
        frame_rate=_frame_rate(text),
    )


def release_source_profile(title: str) -> tuple[str, bool]:
    """Separate source family from remux packaging using explicit evidence."""
    text = normalize_release_text(title)
    source = parse_release_specs(text).source
    remux = source == "REMUX"
    if source in {"WEB-DL", "WEBRip", "WEBMux"}:
        return "WEB", False
    if source not in {"BluRay", "REMUX"}:
        return "", False
    if remux:
        if re.search(_LEFT + rf"web{_SEP}remux" + _RIGHT, text, re.I):
            return "WEB", True
        for rule in FORMAT_RULES:
            if rule.dimension == "source" and rule.label in {"WEB-DL", "WEBRip", "WEBMux"}:
                if rule.expression.search(text):
                    return "WEB", True
    if re.search(_LEFT + rf"(?:uhd{_SEP})?b[dr]{_SEP}rip" + _RIGHT, text, re.I):
        return "BDRip", remux
    if re.search(_LEFT + rf"bd{_SEP}remux" + _RIGHT, text, re.I):
        return "BluRay", remux
    for rule in FORMAT_RULES:
        if rule.dimension == "source" and rule.label == "BluRay" and rule.expression.search(text):
            return "BluRay", remux
    return "", remux


def parse_release_specs(value: object) -> ReleaseSpecs:
    """Return only supported, explicit title facts; never read the network."""
    return _parse_normalized(normalize_release_text(value))
