"""Hard-gate Prowlarr releases by confirmed identity and retrieval scope."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
import unicodedata
from urllib.parse import parse_qs, urlparse


_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_SPECIAL_WORDS = {
    "special",
    "specials",
    "ova",
    "oad",
    "extra",
    "extras",
    "bonus",
    "bonuses",
}
_RELEASE_WORDS = {
    "complete",
    "collection",
    "series",
    "season",
    "seasons",
    "pack",
    "全集",
    "全剧",
    "全季",
    "web",
    "webrip",
    "webdl",
    "dl",
    "bluray",
    "bdrip",
    "remux",
    "hdtv",
    "uhd",
    "dvdrip",
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "avc",
    "hdr",
    "hdr10",
    "dv",
    "dovi",
    "atmos",
    "truehd",
    "dts",
    "eac3",
    "aac",
    "flac",
    "nf",
    "netflix",
    "amzn",
    "amazon",
    "dsnp",
    "hmax",
    "atvp",
    "itunes",
    "korean",
    "japanese",
    "english",
    "chinese",
    "mandarin",
    "cantonese",
    "dual",
    "multi",
    "subbed",
    "dubbed",
    "proper",
    "repack",
    "internal",
    "directors",
    "director",
    "cut",
    "extended",
    "unrated",
    "theatrical",
    "remastered",
    "criterion",
    "imax",
}
_WHOLE_SERIES = re.compile(
    r"(?i)\b(?:complete(?:[ ._-]+series)?|full[ ._-]+series|"
    r"all[ ._-]+seasons|complete[ ._-]+collection)\b|全集|全剧"
)
_EPISODE_RANGE = re.compile(
    r"(?i)\bS(\d{1,2})E(\d{1,3})\s*(?:-|~|TO)\s*"
    r"(?:S(\d{1,2}))?E?(\d{1,3})\b"
)
_EPISODE_CHAIN = re.compile(r"(?i)\bS(\d{1,2})((?:E\d{1,3}){2,})\b")
_EPISODE = re.compile(r"(?i)\bS(\d{1,2})E(\d{1,3})\b")
_X_EPISODE = re.compile(r"(?i)\b(\d{1,2})x(\d{1,3})\b")
_SEASON_RANGE = re.compile(
    r"(?i)\bS(\d{1,2})\s*(?:-|~|TO)\s*S?(\d{1,2})\b"
)
_SEASON = re.compile(r"(?i)\bS(\d{1,2})(?!E\d)\b")


@dataclass(frozen=True)
class ReleaseClassification:
    identity_match: bool
    release_scope: str
    observed_seasons: tuple[int, ...]
    observed_episodes: tuple[tuple[int, int], ...]
    scope_match: bool
    evidence: tuple[str, ...]
    rejection_reason: str = ""


@dataclass(frozen=True)
class ReleaseGateResult:
    raw_count: int
    eligible: tuple[dict, ...]
    rejection_counts: dict[str, int]
    classifications: tuple[ReleaseClassification, ...]


def _text(value) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(value or ""))
        .replace("\xa0", " ")
        .split()
    )


def _tokens(value: str) -> tuple[str, ...]:
    value = re.sub(r"^(?:\s*\[[^\]]+\]\s*)+", "", _text(value))
    return tuple(token.casefold() for token in _TOKEN.findall(value))


def _identity_aliases(contract: dict) -> tuple[tuple[str, ...], ...]:
    identity = contract.get("identity") or {}
    values = [
        identity.get("english_title"),
        identity.get("official_english_title"),
        identity.get("romanized_original_title"),
        identity.get("canonical_search_title"),
        identity.get("original_title"),
        *((identity.get("aliases") or []) if isinstance(
            identity.get("aliases"), list
        ) else []),
    ]
    retrieval_query = str(
        (contract.get("retrieval") or {}).get("query") or ""
    )
    retrieval_query = re.sub(
        r"(?i)\bS\d{1,2}(?:E\d{1,3})?\b",
        " ",
        retrieval_query,
    )
    values.append(retrieval_query)
    aliases = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("name") or value.get("title")
        alias = _tokens(value)
        if alias and alias not in aliases:
            aliases.append(alias)
    aliases.sort(key=lambda item: (-len(item), item))
    return tuple(aliases)


def _is_release_marker(token: str) -> bool:
    return bool(
        token in _RELEASE_WORDS
        or _YEAR.fullmatch(token)
        or re.fullmatch(r"\d{3,4}p", token)
        or re.fullmatch(r"[48]k", token)
        or re.fullmatch(r"s\d{1,2}(?:e\d{1,3})?", token)
        or re.fullmatch(r"\d{1,2}x\d{1,3}", token)
    )


def _identity_match(
    title: str,
    aliases: tuple[tuple[str, ...], ...],
) -> tuple[bool, tuple[int, int], str]:
    release_tokens = _tokens(title)
    for alias in aliases:
        if len(alias) > len(release_tokens):
            continue
        if release_tokens[:len(alias)] != alias:
            continue
        tail = release_tokens[len(alias):]
        if tail and not _is_release_marker(tail[0]):
            continue
        return True, (0, len(alias)), "identity:" + " ".join(alias)
    return False, (0, 0), ""


def _contains_special_content(
    title: str,
    *,
    media_type: str,
    alias_span: tuple[int, int],
) -> bool:
    tokens = _tokens(title)
    if media_type == "movie":
        start, end = alias_span
        tokens = tokens[:start] + tokens[end:]
    for index, token in enumerate(tokens):
        if token in _SPECIAL_WORDS:
            return True
        if re.fullmatch(r"s00|sp\d*", token):
            return True
        if (
            token == "season"
            and index + 1 < len(tokens)
            and tokens[index + 1] == "0"
        ):
            return True
    return False


def _expand_range(start: int, end: int, *, maximum: int) -> set[int]:
    if start <= 0 or end < start or end - start > maximum:
        return set()
    return set(range(start, end + 1))


def _classify_scope(
    title: str,
    media_type: str,
) -> tuple[str, tuple[int, ...], tuple[tuple[int, int], ...], tuple[str, ...]]:
    value = _text(title)
    seasons: set[int] = set()
    episodes: set[tuple[int, int]] = set()
    evidence = []
    multi_episode = False

    for match in _EPISODE_RANGE.finditer(value):
        season = int(match.group(1))
        episode = int(match.group(2))
        end_season = int(match.group(3) or season)
        end_episode = int(match.group(4))
        seasons.update((season, end_season))
        if season == end_season:
            expanded = _expand_range(episode, end_episode, maximum=500)
            episodes.update((season, item) for item in expanded)
        else:
            episodes.update(((season, episode), (end_season, end_episode)))
        multi_episode = True
        evidence.append(match.group(0))

    for match in _EPISODE_CHAIN.finditer(value):
        season = int(match.group(1))
        values = [
            int(item)
            for item in re.findall(r"(?i)E(\d{1,3})", match.group(2))
        ]
        seasons.add(season)
        episodes.update((season, item) for item in values)
        multi_episode = multi_episode or len(values) > 1
        evidence.append(match.group(0))

    for pattern in (_EPISODE, _X_EPISODE):
        for match in pattern.finditer(value):
            season, episode = int(match.group(1)), int(match.group(2))
            seasons.add(season)
            episodes.add((season, episode))
            evidence.append(match.group(0))

    if episodes:
        release_scope = (
            "multi_episode"
            if multi_episode or len(episodes) > 1
            else "single_episode"
        )
        return (
            release_scope,
            tuple(sorted(seasons)),
            tuple(sorted(episodes)),
            tuple(dict.fromkeys(evidence)),
        )

    for match in _SEASON_RANGE.finditer(value):
        start, end = int(match.group(1)), int(match.group(2))
        seasons.update(_expand_range(start, end, maximum=100))
        evidence.append(match.group(0))
    for match in _SEASON.finditer(value):
        seasons.add(int(match.group(1)))
        evidence.append(match.group(0))

    lexical = bool(_WHOLE_SERIES.search(value))
    if len(seasons) > 1:
        release_scope = "multi_season_pack"
    elif len(seasons) == 1:
        release_scope = "single_season_pack"
    elif lexical:
        release_scope = "whole_series_lexical"
    elif media_type == "movie":
        release_scope = "movie"
    else:
        release_scope = "unknown"
    if lexical:
        evidence.append("whole_series_lexical")
    return (
        release_scope,
        tuple(sorted(seasons)),
        (),
        tuple(dict.fromkeys(evidence)),
    )


def _expected_seasons(contract: dict) -> tuple[int, ...]:
    seasons = {
        int(item.get("season_number"))
        for item in contract.get("items") or []
        if isinstance(item, dict)
        and item.get("content_role", "main_episode") == "main_episode"
        and str(item.get("season_number") or "").isdigit()
        and int(item.get("season_number")) > 0
    }
    return tuple(sorted(seasons))


def _target_numbers(contract: dict) -> tuple[int | None, int | None]:
    decision = ((contract.get("evidence") or {}).get("decision") or {})
    placement = contract.get("placement") or {}
    season = decision.get("season_number")
    episode = decision.get("episode_number")
    if season is None:
        season = placement.get("season_number")
    if episode is None:
        episode = placement.get("episode_number")
    try:
        season = int(season) if season is not None else None
    except (TypeError, ValueError):
        season = None
    try:
        episode = int(episode) if episode is not None else None
    except (TypeError, ValueError):
        episode = None
    return season, episode


def _scope_matches(
    *,
    target_scope: str,
    release_scope: str,
    observed_seasons: tuple[int, ...],
    observed_episodes: tuple[tuple[int, int], ...],
    expected_seasons: tuple[int, ...],
    season_number: int | None,
    episode_number: int | None,
) -> bool:
    if target_scope == "movie":
        return release_scope == "movie"
    if target_scope == "whole_series":
        if release_scope == "whole_series_lexical":
            return (
                not observed_seasons
                or observed_seasons == expected_seasons
            )
        if release_scope in {"single_season_pack", "multi_season_pack"}:
            return bool(expected_seasons) and (
                observed_seasons == expected_seasons
            )
        return False
    if target_scope == "season":
        return (
            release_scope == "single_season_pack"
            and season_number is not None
            and observed_seasons == (season_number,)
        )
    if target_scope == "episode":
        return (
            release_scope == "single_episode"
            and season_number is not None
            and episode_number is not None
            and observed_episodes == ((season_number, episode_number),)
        )
    return False


def _scope_label(
    target_scope: str,
    release_scope: str,
    seasons: tuple[int, ...],
    episodes: tuple[tuple[int, int], ...],
) -> str:
    if target_scope == "movie":
        return "电影"
    if target_scope == "whole_series":
        if seasons:
            marker = (
                f"S{seasons[0]:02d}"
                if len(seasons) == 1
                else f"S{seasons[0]:02d}-S{seasons[-1]:02d}"
            )
            return f"全剧（{marker}）"
        return "全剧（完整包）"
    if target_scope == "season" and seasons:
        return f"第 {seasons[0]} 季整季"
    if target_scope == "episode" and episodes:
        season, episode = episodes[0]
        width = 2 if episode < 100 else 3
        return f"S{season:02d}E{episode:0{width}d}"
    return release_scope


def _download_key(item: dict) -> tuple[str, str]:
    magnet = str(
        item.get("magnet_url")
        or item.get("magnetUrl")
        or item.get("magnet")
        or ""
    ).strip()
    if magnet:
        query = parse_qs(urlparse(magnet).query)
        xt = next(iter(query.get("xt") or []), "")
        if xt.casefold().startswith("urn:btih:"):
            return "infohash", xt.rsplit(":", 1)[-1].upper()
        return "magnet", magnet
    download = str(
        item.get("download_url")
        or item.get("downloadUrl")
        or ""
    ).strip()
    if download:
        return "download", download
    return "", ""


def gate_releases(items: list[dict], contract: dict) -> ReleaseGateResult:
    raw_items = [item for item in items or [] if isinstance(item, dict)]
    aliases = _identity_aliases(contract)
    identity = contract.get("identity") or {}
    retrieval = contract.get("retrieval") or {}
    media_type = str(
        retrieval.get("media_type")
        or (contract.get("placement") or {}).get("library_type")
        or ""
    )
    target_scope = str(retrieval.get("scope") or "")
    if media_type == "movie":
        target_scope = "movie"
    expected_year = str(identity.get("year") or "")[:4]
    expected_seasons = _expected_seasons(contract)
    season_number, episode_number = _target_numbers(contract)
    rejections = Counter()
    eligible = []
    classifications = []
    seen = set()

    for item in raw_items:
        key = _download_key(item)
        if not all(key):
            reason = "missing_download_url"
            rejections[reason] += 1
            classifications.append(ReleaseClassification(
                False, "unknown", (), (), False, (), reason
            ))
            continue
        if key in seen:
            reason = "duplicate"
            rejections[reason] += 1
            classifications.append(ReleaseClassification(
                False, "unknown", (), (), False, (), reason
            ))
            continue
        seen.add(key)

        title = _text(item.get("title"))
        identity_match, alias_span, identity_evidence = _identity_match(
            title,
            aliases,
        )
        if not identity_match:
            reason = "identity_mismatch"
            rejections[reason] += 1
            classifications.append(ReleaseClassification(
                False, "unknown", (), (), False, (), reason
            ))
            continue
        years = set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", title))
        if expected_year and years and expected_year not in years:
            reason = "year_mismatch"
            rejections[reason] += 1
            classifications.append(ReleaseClassification(
                True,
                "unknown",
                (),
                (),
                False,
                (identity_evidence,),
                reason,
            ))
            continue
        if _contains_special_content(
            title,
            media_type=media_type,
            alias_span=alias_span,
        ):
            reason = "unsupported_special_content"
            rejections[reason] += 1
            classifications.append(ReleaseClassification(
                True,
                "unknown",
                (),
                (),
                False,
                (identity_evidence,),
                reason,
            ))
            continue

        release_scope, seasons, episodes, scope_evidence = _classify_scope(
            title,
            media_type,
        )
        scope_match = _scope_matches(
            target_scope=target_scope,
            release_scope=release_scope,
            observed_seasons=seasons,
            observed_episodes=episodes,
            expected_seasons=expected_seasons,
            season_number=season_number,
            episode_number=episode_number,
        )
        evidence = tuple(
            item for item in (identity_evidence, *scope_evidence) if item
        )
        reason = "" if scope_match else "scope_mismatch"
        classifications.append(ReleaseClassification(
            True,
            release_scope,
            seasons,
            episodes,
            scope_match,
            evidence,
            reason,
        ))
        if not scope_match:
            rejections[reason] += 1
            continue
        accepted = item.copy()
        accepted["release_scope"] = release_scope
        accepted["scope_label"] = _scope_label(
            target_scope,
            release_scope,
            seasons,
            episodes,
        )
        accepted["gate_evidence"] = list(evidence)
        eligible.append(accepted)

    return ReleaseGateResult(
        raw_count=len(raw_items),
        eligible=tuple(eligible),
        rejection_counts=dict(rejections),
        classifications=tuple(classifications),
    )
