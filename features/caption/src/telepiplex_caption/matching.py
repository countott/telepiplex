"""Conservative identity/release matching; popularity never overrides identity."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import PurePosixPath

from .models import MatchResult, MediaQuery, SubtitleCandidate, SubtitleDocument

_EPISODE = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,2})[ ._-]*e(\d{1,3})(?:[ ._-]*(?:e|-e?)(\d{1,3}))?")
_SEASON = re.compile(r"(?i)(?<![a-z0-9])(?:s|season[ ._-]*)(\d{1,2})(?!\d)")
_EP_ONLY = re.compile(r"(?i)(?<![a-z0-9])(?:e|ep)[ ._-]*(\d{1,3})(?!\d)")
_YEARS = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_NOISE = re.compile(r"(?i)(?:\b(?:480|576|720|1080|2160)p\b|\b(?:bluray|blu-ray|bdrip|brrip|webrip|web-dl|webdl|hdtv|dvdrip|remux|x264|x265|h264|h265|hevc|avc|aac|dts|srt|ass|ssa|chs|cht|chi|eng|english|subtitles?)\b)")


@lru_cache(maxsize=1)
def _title_converter():
    from opencc import OpenCC
    return OpenCC("t2s")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))
    try:
        text = _title_converter().convert(text)
    except ImportError:
        pass
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", text)


def _title_matches(title: str, text: str) -> bool:
    a, b = _normalize(title), _normalize(text)
    if not a or not b:
        return False
    if a == b:
        return True
    # A film title contained in another title ("The Science of Interstellar")
    # is not the film. Evaluate the work name before release/year descriptors.
    work = re.split(r"(?i)(?<!\d)(?:19|20)\d{2}(?!\d)|\b(?:480|576|720|1080|2160)p\b|blu[ ._-]?ray|web[ ._-]?(?:dl|rip)|\bs\d{1,2}[ ._-]*e?\d*\b|\b(?:e|ep)[ ._-]*\d{1,3}\b", text, maxsplit=1)[0]
    work = re.sub(r"(?i)\b(?:imax|subtitles?|simplified|traditional|chinese|english|mandarin|cantonese|bilingual|chs|cht|chi|eng|srt|ass|ssa)\b", "", work)
    work = re.sub(r"简体|簡體|繁体|繁體|中文字幕|中英|双语|雙語|简中|簡中|繁中|字幕|特效|国配|國配|英文", "", work)
    if re.fullmatch(r"[a-z0-9]+", a):
        title = "".join(char for char in unicodedata.normalize("NFKD", title) if not unicodedata.combining(char))
        work = "".join(char for char in unicodedata.normalize("NFKD", work) if not unicodedata.combining(char))
        expression = r"(?i)(?<![a-z0-9])" + r"[^a-z0-9\u3400-\u9fff]*".join(re.escape(word) for word in re.findall(r"[a-z0-9]+", title, re.I)) + r"(?![a-z0-9])"
        match = re.search(expression, work)
        if not match:
            # Years may be part of the title itself, e.g. "2001: A Space
            # Odyssey". Require a literal title at the start in that case.
            return bool(_YEARS.search(title) and re.match(expression, text))
        remainder = work[:match.start()] + work[match.end():]
        # A translated Chinese alias may accompany the English title, but
        # additional English title words require an independently known alias.
        return not re.search(r"[a-z0-9]", remainder, re.I)
    if len(a) < 2:
        return False
    # Normalize simplified/traditional aliases, but prevent CJK title prefixes
    # ("星际穿越" versus "星际穿越的科学") from matching related works.
    normalized_work = unicodedata.normalize("NFKC", work)
    try:
        normalized_work = _title_converter().convert(normalized_work)
        normalized_title = _title_converter().convert(unicodedata.normalize("NFKC", title))
    except ImportError:
        normalized_title = unicodedata.normalize("NFKC", title)
    return bool(re.search(r"(?<![\u3400-\u9fff])" + re.escape(normalized_title) + r"(?![\u3400-\u9fff])", normalized_work, re.I))


def _episode_coordinates(text: str) -> tuple[int | None, set[int]]:
    match = _EPISODE.search(text)
    if match:
        season, first = int(match[1]), int(match[2])
        last = int(match[3]) if match[3] else first
        return season, set(range(first, last + 1)) if first <= last <= first + 100 else {first}
    match = re.search(r"(?i)(?<!\d)(\d{1,2})x(\d{1,3})(?!\d)", text)
    if match:
        return int(match[1]), {int(match[2])}
    season_match = _SEASON.search(text)
    chinese_season = re.search(r"第\s*(\d{1,2})\s*季", text)
    episode_match = _EP_ONLY.search(text) or re.search(r"第\s*(\d{1,3})\s*集", text)
    return (int((season_match or chinese_season)[1]) if season_match or chinese_season else None,
            {int(episode_match[1])} if episode_match else set())


def episode_coordinates(text: str) -> tuple[int | None, set[int]]:
    # A season pack directory may name E01-E12 while a member names only E03.
    # Start at the member; parents can supply a missing season, not replace its
    # explicit episode with the entire pack's range.
    season, episodes = None, set()
    for part in reversed(text.replace("\\", "/").split("/")):
        found_season, found_episodes = _episode_coordinates(part)
        if season is None:
            season = found_season
        if not episodes:
            episodes = found_episodes
    return season, episodes


def _source(text: str) -> str:
    if re.search(r"(?i)blu[ ._-]?ray|bd[ ._-]?rip|brrip|remux", text):
        return "bluray"
    if re.search(r"(?i)web[ ._-]?(?:dl|rip)", text):
        return "web"
    if re.search(r"(?i)dvd[ ._-]?rip", text):
        return "dvd"
    if re.search(r"(?i)hdtv", text):
        return "hdtv"
    return ""


def _cut(text: str) -> str:
    for kind, expression in (("extended", r"extended|加长|加長"), ("director", r"director.?s?[ ._-]*cut|导演剪辑|導演剪輯"), ("theatrical", r"theatrical|院线|院線"), ("encore", r"\bencore\b")):
        if re.search(expression, text, re.I):
            return kind
    return ""


def _media_type(value: str) -> str:
    return {"tv": "series", "television": "series", "film": "movie"}.get(value.casefold(), value.casefold())


def _release_years(text: str, query: MediaQuery) -> set[int]:
    # Numeric titles (Reply 1988, 2001: A Space Odyssey) are identity, not
    # release-year evidence. Remove only a complete confirmed title occurrence;
    # an additional 2015/1968/2017 release year remains available for rejection.
    text = unicodedata.normalize("NFKC", text)
    for title in (query.title, query.original_title, *query.aliases):
        if not title or not _YEARS.search(title):
            continue
        chars = [c for c in unicodedata.normalize("NFKC", title) if c.isalnum()]
        if not chars:
            continue
        pattern = r"(?<!\w)" + r"[\W_]*".join(re.escape(c) for c in chars) + r"(?!\w)"
        text = re.sub(pattern, "TITLE", text, flags=re.I)
    return {int(value) for value in _YEARS.findall(text)}


def _disc(text: str) -> int | None:
    match = re.search(r"(?i)(?<![a-z0-9])(?:cd|disc|disk)[ ._-]*(\d{1,2})(?!\d)", text)
    return int(match[1]) if match else None


def split_subtitle_piece(text: str, media_type: str = "movie") -> int | None:
    """Recognize a numbered split after a release, without treating anime
    absolute episode names such as 'Show - 001.ass' as split films.
    """
    if not text:
        return None
    name = PurePosixPath(text.replace("\\", "/")).name
    match = re.search(r"(?i)-0(\d{2})(?=(?:[._ -]track\d+)?(?:[._ -](?:chi|chs|cht|eng|en|sc|tc))*\.(?:ass|ssa|srt|mkv|mp4)$)", name)
    if not match:
        return None
    if _media_type(media_type) == "series" and not _episode_coordinates(name[:match.start()])[1]:
        return None
    return int(match[1])


def match_candidate(query: MediaQuery, candidate: SubtitleCandidate) -> MatchResult:
    reasons: list[str] = []
    warnings: list[str] = []
    score = 0
    try:
        if float(candidate.metadata.get("delay_ms") or 0) != 0:
            reasons.append("timing_offset_unapplied")
    except (ValueError, TypeError):
        reasons.append("invalid_timing_offset")
    candidate_text = " ".join((candidate.title, candidate.release_name))
    id_match = False
    for name in ("tmdb_id", "imdb_id"):
        requested, found = str(getattr(query, name) or ""), str(candidate.metadata.get(name) or "")
        if requested and found:
            if requested != found:
                reasons.append(f"{name}_mismatch")
            else:
                score += 100
                id_match = True
    hash_match = bool(query.shooter_hash and candidate.metadata.get("hash_match"))
    if hash_match:
        score += 150
    titles = [query.title, query.original_title, *(query.aliases or ())]
    if any(_title_matches(title, text) for title in titles if title for text in (candidate.title, candidate.release_name) if text):
        score += 40
    elif not (id_match or hash_match):
        reasons.append("title_mismatch")
    candidate_years = {int(candidate.year)} if candidate.year else _release_years(candidate_text, query)
    if query.year and candidate_years:
        if int(query.year) not in candidate_years:
            if _media_type(query.media_type) == "series" and id_match:
                warnings.append("series_year_differs")
            else:
                reasons.append("year_mismatch")
        else:
            score += 15
    elif query.year:
        warnings.append("year_unverified")
    if candidate.media_type and query.media_type and _media_type(candidate.media_type) != _media_type(query.media_type):
        reasons.append("media_type_mismatch")
    season, episodes = episode_coordinates(candidate_text)
    if candidate.season is not None:
        season = candidate.season
    if candidate.episode is not None:
        episodes = {candidate.episode}
    if query.season is not None and season is not None:
        if query.season != season:
            reasons.append("season_mismatch")
        else:
            score += 10
    if query.episode is not None and episodes:
        if query.episode not in episodes:
            reasons.append("episode_mismatch")
        else:
            score += 10
    requested_release = query.release_name or query.video_path
    candidate_release = candidate.release_name or candidate.title
    found_piece = split_subtitle_piece(candidate_release, query.media_type)
    if found_piece is not None and split_subtitle_piece(requested_release, query.media_type) != found_piece:
        reasons.append("likely_split_subtitle")
    if _disc(candidate_release) is not None and _disc(requested_release) != _disc(candidate_release):
        reasons.append("partial_disc_subtitle")
    wanted_source, found_source = _source(requested_release), _source(candidate_release)
    if wanted_source and found_source:
        if wanted_source != found_source:
            reasons.append("source_mismatch")
        else:
            score += 15
    elif requested_release:
        warnings.append("release_unverified")
    wanted_cut, found_cut = _cut(requested_release), _cut(candidate_release)
    if wanted_cut != found_cut and (wanted_cut or found_cut):
        reasons.append("edition_unverified" if not (wanted_cut and found_cut) else "edition_mismatch")
    candidate_fps = candidate.metadata.get("fps")
    if query.fps and candidate_fps:
        try:
            if abs(float(query.fps) - float(candidate_fps)) > 0.1:
                reasons.append("fps_mismatch")
            else:
                score += 5
        except (ValueError, TypeError):
            warnings.append("fps_unverified")
    return MatchResult(not reasons, score, tuple(reasons), tuple(warnings))


def match_document(query: MediaQuery, candidate: SubtitleCandidate, document: SubtitleDocument) -> MatchResult:
    result = match_candidate(query, candidate)
    reasons, warnings = list(result.reasons), list(result.warnings)
    score = result.score
    season, episodes = episode_coordinates(document.filename)
    if candidate.metadata.get("episode_mapping"):
        from .catalog_providers import mapped_episode
        mapped = mapped_episode(candidate, document.filename)
        if mapped:
            season, episodes = mapped[0], {mapped[1]}
        else:
            reasons.append("episode_mapping_unresolved")
    if candidate.metadata.get("requires_episode_mapping") and season is None and candidate.season is None:
        candidate_season, _ = episode_coordinates(candidate.title + " " + candidate.release_name)
        if candidate_season is None:
            reasons.append("episode_mapping_unresolved")
    if query.season is not None and season is not None and query.season != season:
        reasons.append("document_season_mismatch")
    if query.episode is not None:
        if episodes:
            if query.episode not in episodes:
                reasons.append("document_episode_mismatch")
            elif len(episodes) != 1:
                reasons.append("document_multi_episode")
            else:
                score += 20
        elif candidate.episode != query.episode:
            _, candidate_episodes = episode_coordinates(candidate.title + " " + candidate.release_name)
            if candidate_episodes != {query.episode}:
                reasons.append("document_episode_unverified")
    requested_release = query.release_name or query.video_path
    found_piece = split_subtitle_piece(document.filename, query.media_type)
    if found_piece is not None and split_subtitle_piece(requested_release, query.media_type) != found_piece:
        reasons.append("likely_split_subtitle")
    if _disc(document.filename) is not None and _disc(requested_release) != _disc(document.filename):
        reasons.append("document_partial_disc")
    wanted_source, found_source = _source(requested_release), _source(document.filename)
    if wanted_source and found_source and wanted_source != found_source:
        reasons.append("document_source_mismatch")
    wanted_cut, found_cut = _cut(requested_release), _cut(document.filename)
    if found_cut and wanted_cut != found_cut:
        reasons.append("document_edition_mismatch")
    # A package may include several films. A descriptive member must identify the
    # requested film; short names such as "chs.srt" inherit package identity.
    stem = PurePosixPath(document.filename.replace("\\", "/")).stem
    opaque_numeric = bool(re.fullmatch(r"\d{6,20}", stem))
    meaningful = _YEARS.sub("", _NOISE.sub("", _EPISODE.sub("", stem)))
    meaningful = re.sub(r"简体|簡體|繁体|繁體|中文|中英|双语|雙語|简中|簡中|繁中|字幕|特效|国配|國配|英文|简|繁|中字", "", meaningful)
    meaningful = re.sub(r"(?i)\b(?:simplified|traditional|chinese|mandarin|cantonese|bilingual|zh|zhcn|zhtw|cn|tw|gb|big5|utf8)\b", "", meaningful)
    if not opaque_numeric and len(_normalize(meaningful)) >= 5:
        titles = [query.title, query.original_title, *(query.aliases or ())]
        catalog_aliases = candidate.metadata.get("document_aliases", [])
        catalog_identity = False
        if isinstance(catalog_aliases, list) and catalog_aliases:
            from .catalog_providers import _normalized, _work_name
            catalog_identity = any(_normalized(_work_name(stem)) == _normalized(alias) for alias in catalog_aliases if isinstance(alias, str) and alias)
        if not catalog_identity and not any(_title_matches(title, stem) for title in titles if title) and not (query.shooter_hash and candidate.metadata.get("hash_match")):
            # Generic language labels inherit the package identity; a
            # descriptive member for another film never does.
            if _YEARS.search(stem) or len(_normalize(meaningful)) >= 8 or len(re.findall(r"[\u3400-\u9fff]", meaningful)) >= 2:
                reasons.append("document_title_mismatch")
    years = set() if opaque_numeric else _release_years(stem, query)
    if query.year and years and query.year not in years:
        if _media_type(query.media_type) == "series" and "series_year_differs" in warnings:
            warnings.append("document_episode_year_unverified")
        else:
            reasons.append("document_year_mismatch")
    return MatchResult(not reasons, score, tuple(dict.fromkeys(reasons)), tuple(dict.fromkeys(warnings)))
