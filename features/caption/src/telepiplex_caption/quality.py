"""Inspect downloaded dialogue and timestamps before applying user priorities.

This validates subtitle content, not translation accuracy or dialogue alignment.
No filename, language label or provider popularity can pass the content gate.
"""
from __future__ import annotations

import html
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from .matching import match_document, split_subtitle_piece
from .models import MediaQuery, QualityReport, SubtitleCandidate, SubtitleDocument

_CJK = re.compile(r"[\u3400-\u9fff]")
_KANA = re.compile(r"[\u3040-\u30ff\uac00-\ud7af]")
_WORDS = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_SRT_TIME = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})$")


@dataclass(frozen=True)
class _Cue:
    start: float
    end: float
    text: str


def decode_subtitle(content: bytes) -> tuple[str, str]:
    if len(content) > 8 * 1024 * 1024:
        raise ValueError("subtitle_too_large")
    if content.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        encodings = ["utf-32"]
    elif content.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ["utf-16"]
    elif content.startswith(b"\xef\xbb\xbf"):
        encodings = ["utf-8-sig"]
    else:
        encodings = ["utf-8"]
    for encoding in encodings:
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    try:
        from charset_normalizer import from_bytes
        # Restrict to plausible Chinese subtitle encodings. Generic permissive
        # Latin codecs can turn corrupt Chinese into apparently valid text.
        best = from_bytes(content, cp_isolation=["gb18030", "big5", "big5hkscs", "utf_16_le", "utf_16_be"]).best()
        if best is not None and best.encoding and best.chaos <= 0.2:
            return str(best), best.encoding
    except ImportError:
        pass
    raise ValueError("encoding_unrecognized")


def _timestamp(value: str, *, ass: bool = False) -> float:
    match = _SRT_TIME.fullmatch(value.strip())
    if not match:
        raise ValueError("invalid_timestamp")
    hour, minute, second = map(int, match.group(1, 2, 3))
    if minute >= 60 or second >= 60:
        raise ValueError("invalid_timestamp")
    return hour * 3600 + minute * 60 + second + int(match[4]) / (10 ** len(match[4]))


def _plain(text: str) -> str:
    # ASS vector drawings are shapes, not dialogue.
    text = re.sub(r"\{[^}]*\\p[1-9][^}]*\}.*?(?:\{[^}]*\\p0[^}]*\}|$)", "", text)
    text = re.sub(r"\{[^}]*\}", "", text)
    text = text.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ")
    text = re.sub(r"<[^>]*>", "", text)
    return html.unescape(text).strip()


def _parse_srt(text: str) -> tuple[list[_Cue], int]:
    cues, invalid = [], 0
    for block in re.split(r"\n[ \t]*\n", text.strip()):
        lines = block.strip().splitlines()
        if not lines:
            continue
        timing_index = 1 if lines[0].strip().isdigit() else 0
        if timing_index >= len(lines) or "-->" not in lines[timing_index]:
            invalid += 1
            continue
        try:
            start, end = lines[timing_index].split("-->", 1)
            cue = _Cue(_timestamp(start), _timestamp(end.strip().split()[0]), _plain("\n".join(lines[timing_index + 1:])))
            if cue.end <= cue.start or not cue.text:
                invalid += 1
            else:
                cues.append(cue)
        except (ValueError, IndexError):
            invalid += 1
    return cues, invalid


def _parse_ass(text: str, warnings: list[str] | None = None) -> tuple[list[_Cue], int]:
    cues, invalid, nonrendering, dialogue_count = [], 0, 0, 0
    in_events = False
    columns: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_events = line.casefold() == "[events]"
        if not in_events:
            continue
        if line.casefold().startswith("format:"):
            columns = [column.strip().casefold() for column in line.split(":", 1)[1].split(",")]
        elif line.casefold().startswith("dialogue:"):
            dialogue_count += 1
            # SSA's legacy Marked field and ASS's Layer are both accepted via
            # the actual Format declaration, never a guessed comma position.
            if not columns or not {"start", "end", "text"}.issubset(columns) or columns[-1] != "text":
                invalid += 1
                continue
            values = line.split(":", 1)[1].split(",", len(columns) - 1)
            try:
                row = dict(zip(columns, values, strict=True))
                cue = _Cue(_timestamp(row["start"], ass=True), _timestamp(row["end"], ass=True), _plain(row["text"]))
                if cue.end <= cue.start:
                    # libass renders only Start <= now < End. Some compiled
                    # karaoke generators retain a few impossible single-letter
                    # OP/ED fx events. They never render, so exclude these from
                    # statistics without rewriting the original ASS. This is
                    # deliberately not general tolerance for damaged dialogue.
                    # https://github.com/libass/libass/blob/master/libass/ass_render.c
                    if (row.get("effect", "").strip().casefold() == "fx"
                            and re.match(r"(?i)^(?:OP|ED)(?:JP|JPN|CH|CHS|CHT|SC|TC|CN)?(?:$|[ ._-]|\d)", row.get("style", "").strip())
                            and len(cue.text) == 1):
                        nonrendering += 1
                    else:
                        invalid += 1
                elif cue.text:
                    cues.append(cue)
            except (KeyError, ValueError):
                invalid += 1
    if nonrendering:
        if nonrendering <= 64 and nonrendering / max(1, dialogue_count) <= 0.02:
            if warnings is not None:
                warnings.append("ignored_nonrendering_effects")
        else:
            invalid += nonrendering
    return cues, invalid


def _unresolved_ass_import(text: str) -> bool:
    """Merge Scripts imports are source instructions, not rendered subtitles.

    A compiled karaoke file may legitimately retain template/code comments;
    those alone are not evidence that its rendered Dialogue is incomplete.
    """
    columns: list[str] = []
    in_events = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_events = line.casefold() == "[events]"
        if not in_events:
            continue
        if line.casefold().startswith("format:"):
            columns = [value.strip().casefold() for value in line.split(":", 1)[1].split(",")]
        elif line.casefold().startswith("comment:") and columns:
            row = dict(zip(columns, line.split(":", 1)[1].split(",", len(columns) - 1)))
            if re.match(r"(?i)^import(?:\s|$)", row.get("effect", "").strip()):
                return True
    return False


def _foreign_dialogue(cues: list[_Cue]) -> bool:
    # Count unique timed events, not characters. Thousands of Chinese sign or
    # effect layers can otherwise dilute the Japanese half of bilingual ASS.
    foreign = [cue for cue in cues if len(_KANA.findall(cue.text)) >= 3]
    if len(foreign) < 6:
        return False
    ratio = len(foreign) / len(cues)
    first, last = min(cue.start for cue in cues), max(cue.end for cue in cues)
    span = max(1, last - first)
    bins = {min(4, int((cue.start - first) / span * 5)) for cue in foreign}
    # A Chinese-only release may retain Japanese lyrics in OP/ED or signs.
    # Substantial Japanese throughout the programme is a different case.
    return ratio >= 0.5 or (ratio >= 0.2 and len(bins) >= 3)


def _source_fragment(filename: str, cues: list[_Cue], query: MediaQuery | None) -> bool:
    from pathlib import PurePosixPath
    path = PurePosixPath(filename.replace("\\", "/"))
    stem = path.stem
    role = re.sub(r"(?i)(?:[._ -](?:chs|cht|chi|sc|tc|jpn|jpsc|jptc|zh|ja|en|jp|cn|tw))+$", "", stem)
    roles = [role, *path.parts[:-1]]
    source_roles = [value for value in roles if re.fullmatch(r"(?i)(?:nc)?(?:op|ed)[ ._-]*\d*|screen|signs?|typesetting|staff|insert\d*|templates?|effects?|songs", value)]
    if not source_roles:
        return False
    if query and all(any(value.casefold() == title.casefold() for title in (query.title, query.original_title, *(query.aliases or ())) if title)
                     for value in source_roles):
        return False
    first, last = min(cue.start for cue in cues), max(cue.end for cue in cues)
    # Require actual short or sparse content in addition to an explicit source
    # role. A film simply named "Screen" is not an ASS production fragment.
    intervals = sorted((cue.start, cue.end) for cue in cues)
    covered, end = 0.0, first
    for start, stop in intervals:
        covered += max(0, stop - max(start, end))
        end = max(end, stop)
    return last - first <= 600 or covered / max(1, last - first) < 0.25


@lru_cache(maxsize=1)
def _script_converters():
    from opencc import OpenCC
    return OpenCC("t2s"), OpenCC("s2t")


def _chinese_script(text: str) -> tuple[str, list[str]]:
    try:
        to_simple, to_traditional = _script_converters()
    except ImportError:
        return "", ["script_detector_unavailable"]
    chars = "".join(_CJK.findall(text))
    simplified = to_simple.convert(chars)
    traditional = to_traditional.convert(chars)
    traditional_count = sum(a != b for a, b in zip(chars, simplified)) + abs(len(chars) - len(simplified))
    simplified_count = sum(a != b for a, b in zip(chars, traditional)) + abs(len(chars) - len(traditional))
    total = traditional_count + simplified_count
    if total < 3:
        return "", ["chinese_script_unverified"]
    if min(traditional_count, simplified_count) >= 5 and min(traditional_count, simplified_count) / total > 0.2:
        return "", ["mixed_chinese_scripts"]
    return ("cht" if traditional_count > simplified_count else "chi"), []


def inspect_subtitle(document: SubtitleDocument, query: MediaQuery | None = None) -> QualityReport:
    reasons: list[str] = []
    warnings: list[str] = []
    fmt = document.format.casefold().lstrip(".")
    if fmt not in {"srt", "ass", "ssa"}:
        return QualityReport(False, format=fmt, reasons=("unsupported_subtitle_format",))
    try:
        text, encoding = decode_subtitle(document.content)
    except ValueError as error:
        return QualityReport(False, format=fmt, reasons=(str(error),))
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if re.search(r"(?i)<(?:!doctype|html|script|iframe)|\x00|[\x01-\x08\x0b\x0c\x0e-\x1f]|\ufffd", text):
        reasons.append("invalid_or_non_subtitle_content")
    cues, invalid = _parse_srt(text) if fmt == "srt" else _parse_ass(text, warnings)
    if fmt in {"ass", "ssa"} and _unresolved_ass_import(text):
        reasons.append("unresolved_ass_import")
    if invalid:
        reasons.append("malformed_cues")
    duration = query.duration_seconds if query else None
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        duration = None
    minimum_cues = 3 if duration and duration <= 180 else 20
    if len(cues) < minimum_cues:
        reasons.append("insufficient_dialogue")
    if not cues:
        return QualityReport(False, encoding=encoding, format=fmt, reasons=tuple(dict.fromkeys(reasons or ["no_cues"])))
    # Co-timed ASS language layers are one dialogue cue. This also prevents
    # duplicate shadow/style layers from changing bilingual classification.
    grouped: dict[tuple[float, float], set[str]] = {}
    for cue in cues:
        grouped.setdefault((cue.start, cue.end), set()).add(cue.text)
    cues = [_Cue(start, end, "\n".join(sorted(parts))) for (start, end), parts in grouped.items()]
    plain = "\n".join(cue.text for cue in cues)
    chinese_cues = [cue for cue in cues if len(_CJK.findall(cue.text)) >= 2]
    chinese_ratio = len(chinese_cues) / len(cues)
    if chinese_ratio < 0.4 or len(_CJK.findall(plain)) < 12:
        reasons.append("insufficient_chinese_dialogue")
    if _foreign_dialogue(cues):
        reasons.append("non_chinese_or_multilingual_dialogue")
    if _source_fragment(document.filename, cues, query):
        reasons.append("subtitle_source_fragment")
    piece = split_subtitle_piece(document.filename, query.media_type if query else "movie")
    requested_piece = split_subtitle_piece(query.release_name or query.video_path, query.media_type) if query else None
    if piece is not None and piece != requested_piece:
        reasons.append("likely_split_subtitle")
    language, script_reasons = _chinese_script(plain)
    reasons.extend(script_reasons)
    # English needs at least three words in a cue: names, logos, and short
    # technical terms alone are not evidence of a bilingual translation.
    english_cues = [cue for cue in cues if len(_WORDS.findall(cue.text)) >= 3]
    # Sweep sorted cue intervals instead of comparing every cue with every
    # English cue (feature subtitles can contain tens of thousands of events).
    english_cues.sort(key=lambda cue: cue.start)
    paired, index = 0, 0
    for cue in sorted(chinese_cues, key=lambda item: item.start):
        while index < len(english_cues) and english_cues[index].end <= cue.start:
            index += 1
        for other in english_cues[index: index + 20]:
            if other.start >= cue.end:
                break
            overlap = min(cue.end, other.end) - max(cue.start, other.start)
            if overlap >= min(cue.end - cue.start, other.end - other.start) * 0.5:
                paired += 1
                break
    bilingual_ratio = paired / max(1, len(chinese_cues))
    bilingual = bilingual_ratio >= 0.3
    if 0.15 <= bilingual_ratio < 0.3:
        reasons.append("partial_bilingual_dialogue")
    first, last = min(cue.start for cue in cues), max(cue.end for cue in cues)
    if any(cue.end - cue.start > 120 for cue in cues):
        reasons.append("implausible_cue_duration")
    if last > 12 * 3600:
        reasons.append("implausible_timeline")
    if duration:
        if last > duration + max(10, duration * 0.02):
            reasons.append("timeline_exceeds_video")
        if duration > 600 and (last < duration * 0.55 or first > duration * 0.35):
            reasons.append("likely_partial_subtitle")
        warnings.append("dialogue_sync_unverified")
    else:
        warnings.append("video_timing_unverified")
    expected = getattr(query, "expected_duration_seconds", None) if query else None
    if not duration and expected is not None and math.isfinite(expected) and expected > 600:
        # Metadata runtime is only an approximate completeness reference. It
        # cannot prove video alignment and differs between legitimate cuts.
        if last < expected * 0.55 or first > expected * 0.35:
            reasons.append("likely_partial_subtitle")
        if last > expected * 1.35 + 120:
            reasons.append("timeline_exceeds_expected_duration")
        warnings.append("metadata_runtime_reference")
    # Credits repeated throughout the file are not a usable movie subtitle.
    counts = Counter(cue.text for cue in cues)
    if len(cues) >= 20 and max(counts.values()) / len(cues) > 0.6:
        reasons.append("repetitive_dialogue")
    if not query or not query.original_language:
        warnings.append("original_language_unknown")
    return QualityReport(
        accepted=not reasons, language=language, bilingual=bilingual,
        cue_count=len(cues), first_start=first, last_end=last, encoding=encoding,
        timing_verified=False, format=fmt, reasons=tuple(dict.fromkeys(reasons)),
        warnings=tuple(warnings), chinese_ratio=round(chinese_ratio, 3),
        bilingual_ratio=round(bilingual_ratio, 3), normalized_text=text,
    )


def subtitle_priority(query: MediaQuery, report: QualityReport) -> int:
    """Higher wins; zero means outside the user's accepted language policy."""
    if not report.accepted or report.language not in {"chi", "cht"} or not query.original_language:
        return 0
    english = query.original_language.casefold() in {"en", "eng", "english"}
    if english:
        if report.language == "chi":
            if report.bilingual and report.format in {"ass", "ssa"}:
                return 400
            if report.bilingual and report.format == "srt":
                return 300
            if not report.bilingual and report.format == "srt":
                return 200
            return 0
        return 100
    if report.bilingual:
        return 0
    return 200 if report.language == "chi" else 100


def rank_subtitle(query: MediaQuery, candidate: SubtitleCandidate, document: SubtitleDocument,
                  report: QualityReport) -> tuple[int, ...] | None:
    match = match_document(query, candidate, document)
    priority = subtitle_priority(query, report)
    if not match.accepted or not priority:
        return None
    # Apply user's language/format order before release confidence; providers'
    # popularity is only a final tie breaker among valid matching subtitles.
    return (priority, match.score, -len(report.warnings), min(report.cue_count, 2000), max(0, candidate.downloads))
