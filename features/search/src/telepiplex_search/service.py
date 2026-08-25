from __future__ import annotations

import asyncio
from collections import Counter
from contextvars import ContextVar
import html
import json
import math
import re
import time
import unicodedata
import uuid
from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType

from telepiplex_plugin_sdk import FeatureError
from telepiplex_plugin_sdk.media_metadata import resolve_category_route

from .adapters.douban import (
    lookup_douban_evidence,
    lookup_douban_subject,
)
from .adapters.anilist import (
    AniListConfigError,
    AniListRequestError,
    get_anilist_media,
    search_anilist,
)
from .adapters.prowlarr import (
    ProwlarrRequestError,
    get_prowlarr_indexer_summary,
    list_prowlarr_indexers,
    resolve_prowlarr_download_url,
    search_prowlarr,
    search_prowlarr_indexer,
)
from .adapters.tvdb import (
    TvdbAuthenticationError,
    TvdbConfigError,
    TvdbRequestError,
    get_tvdb_series,
    search_tvdb_movies,
    search_tvdb_series,
)
from .adapters.tmdb import (
    TmdbAuthenticationError,
    TmdbConfigError,
    TmdbRequestError,
    find_tmdb_by_external_id,
    get_tmdb_entity,
    search_tmdb,
)
from .adapters.wikipedia import lookup_wikipedia_evidence
from .adapters.wikidata import (
    enrich_wikidata_entities,
    search_wikidata_entities,
)
from .config_wizard import SearchConfigWizard
from .confirmed_enrichment import (
    ConfirmedIdentity,
    build_anilist_query,
    build_tmdb_query,
    build_tvdb_query,
    build_wikipedia_queries,
    select_unique_anilist_fact,
    select_unique_douban_fact,
    select_unique_tmdb_fact,
    select_unique_tvdb_series,
    select_unique_wikipedia_fact,
)
from .context import runtime_context
from .candidate_hydration import (
    CandidateHydrationError,
    hydrate_frozen_candidate,
    hydrate_frozen_candidate_anchor,
)
from .candidate_locale import (
    localize_candidate_from_exact_douban,
    localize_candidate_from_verified_douban,
)
from .direct_link import (
    DirectLinkError,
    resolve_direct_link,
    resolve_shared_metadata_link,
)
from .direct_plan import (
    build_direct_entity_plan,
)
from .input_contract import classify_search_input, contains_url
from .identity_presentation import build_identity_presentation
from .log_sanitizer import sanitize_log_value
from .metadata_resolutions import MetadataResolutionStore
from .errors import SearchPlanningError
from .enrichment_policy import (
    apply_deferred_presentation,
    needs_authoritative_scope_enrichment,
)
from .prowlarr_query import (
    build_prowlarr_query,
    build_prowlarr_query_chain,
)
from .prowlarr_waves import plan_prowlarr_waves
from .source_schedule import SourceRequestKey, SourceScheduler
from .release_gate import gate_releases
from .release_identity import deduplicate_releases, stable_release_id
from .release_report import format_release_report, release_keyboard
from .release_score import rank_releases
from .search_plan import (
    TemporarySpecialAllocator,
    confirm_media_metadata,
    finalize_search_plan,
)
from .search_resolution import parse_search_intent
from .search_logging import (
    bind_search_log_context,
    log_search_event,
    log_search_measurement,
)
from .series_scope import (
    SeriesScopeError,
    apply_inventory_probe_scope,
    apply_series_scope,
    series_inventory,
    series_seasons,
    series_scope_options,
)
from .work_discovery import build_root_work_search_plan


_LATIN = re.compile(r"[A-Za-z]")


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _selected_https_cover(fact: dict | None) -> bool:
    return bool(
        isinstance(fact, dict)
        and _text(
            fact.get("cover_url") or fact.get("poster_url")
        ).startswith("https://")
    )


def _flat_source_result(provider: str, value) -> dict:
    facts = (
        value
        if isinstance(value, list)
        else [value] if isinstance(value, dict) else []
    )
    return {
        "source": provider,
        "status": "ok" if facts else "not_found",
        "facts": facts[:5],
    }


def _cacheable_tmdb_raw(
    value,
    identity: ConfirmedIdentity,
    *,
    require_https_cover: bool = False,
) -> bool:
    try:
        selected = select_unique_tmdb_fact(
            _flat_source_result("tmdb", value),
            identity,
        )
    except Exception:
        return False
    return bool(
        isinstance(selected, dict)
        and (
            not require_https_cover
            or _selected_https_cover(selected)
        )
    )


def _cacheable_anilist_raw(value, identity: ConfirmedIdentity) -> bool:
    try:
        selected = select_unique_anilist_fact(
            _flat_source_result("anilist", value),
            identity,
        )
    except Exception:
        return False
    return isinstance(selected, dict)


def _cacheable_wikipedia_raw(value, identity: ConfirmedIdentity) -> bool:
    try:
        selected = select_unique_wikipedia_fact(value, identity)
    except Exception:
        return False
    return isinstance(selected, dict)


def _cacheable_douban_raw(value, identity: ConfirmedIdentity) -> bool:
    try:
        selected = select_unique_douban_fact(value, identity)
    except Exception:
        return False
    return isinstance(selected, dict)


def _cacheable_douban_subject(value, subject_id: str) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    external_ids = (
        value.get("external_ids")
        if isinstance(value.get("external_ids"), dict)
        else {}
    )
    actual = _text(
        value.get("subject_id")
        or external_ids.get("douban_subject")
    )
    return bool(actual and actual == _text(subject_id))


def _tvdb_source_result(value) -> dict:
    series = (
        value
        if isinstance(value, list)
        else [value] if isinstance(value, dict) else []
    )
    return {
        "source": "tvdb",
        "status": "ok" if series else "not_found",
        "facts": [{
            "movies": [],
            "series": series[:5],
            "episodes_by_series": {},
        }],
    }


def _cacheable_tvdb_raw(
    value,
    identity: ConfirmedIdentity,
    *,
    require_episodes: bool = False,
) -> bool:
    try:
        selected = select_unique_tvdb_series(
            _tvdb_source_result(value),
            identity,
        )
    except Exception:
        return False
    return bool(
        isinstance(selected, dict)
        and (
            not require_episodes
            or selected.get("episodes")
        )
    )


def _cacheable_poster_raw(value, identity, selector) -> bool:
    if isinstance(value, dict):
        raw_facts = value.get("facts") or ()
    elif isinstance(value, list):
        raw_facts = value
    else:
        raw_facts = ()
    facts = [item for item in raw_facts if isinstance(item, dict)]
    try:
        selected = selector(facts, identity)
    except Exception:
        return False
    return _selected_https_cover(selected)


def _poster_search_identity(
    *,
    endpoint: str,
    query: str,
    title: str,
    year: str,
    media_type: str,
    stable_id: str,
) -> str:
    return json.dumps({
        "endpoint": _text(endpoint),
        "query": _text(query),
        "title": _text(title),
        "year": _text(year)[:4],
        "media_type": _text(media_type).casefold(),
        "stable_id": _text(stable_id),
    }, ensure_ascii=False, sort_keys=True)


def _compact_summary(value, limit: int = 240) -> str:
    value = _text(value)
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


def _normalized_title(value) -> str:
    return "".join(
        character
        for character in unicodedata.normalize(
            "NFKC",
            _text(value),
        ).casefold()
        if character.isalnum()
    )


def _candidate_title_component(value, limit: int | None) -> str:
    value = _text(value)
    if limit is None or len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


def _candidate_display_title(
    identity: dict,
    *,
    component_limit: int | None = None,
) -> str:
    identity = identity if isinstance(identity, dict) else {}
    chinese = _text(
        identity.get("chinese_title")
        or identity.get("english_title")
        or "未知"
    )
    original = _text(identity.get("original_title"))
    year = _text(identity.get("year")) or "年份未知"
    include_original = (
        bool(original)
        and _normalized_title(original) != _normalized_title(chinese)
    )
    chinese = _candidate_title_component(chinese, component_limit)
    if include_original:
        original = _candidate_title_component(original, component_limit)
        return f"{chinese} ({original}) {year}"
    return f"{chinese} {year}"


def _positive_integer(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _probe_media_type(probe: dict) -> str:
    if not isinstance(probe, dict):
        return ""
    shape = _text(probe.get("content_shape")).casefold()
    if shape == "movie":
        return "movie"
    if (
        "season" in shape
        or "episode" in shape
        or probe.get("observed_seasons")
        or probe.get("observed_episodes")
    ):
        return "series"
    return ""


def _candidate_media_type(candidate: dict) -> str:
    contract = (
        candidate.get("media_metadata")
        if isinstance(candidate, dict)
        else {}
    ) or {}
    identity = contract.get("identity") or {}
    content_kind = _text(identity.get("content_kind")).casefold()
    if content_kind == "movie" or content_kind.endswith("_movie"):
        return "movie"
    if content_kind == "series" or content_kind.endswith("_series"):
        return "series"
    retrieval_type = _text(
        (contract.get("retrieval") or {}).get("media_type")
    ).casefold()
    if retrieval_type in {"movie", "series"}:
        return retrieval_type
    placement = contract.get("placement") or {}
    if placement.get("mapping_kind") == "temporary_related_special":
        return "movie"
    library_type = _text(placement.get("library_type")).casefold()
    return library_type if library_type in {"movie", "series"} else ""


def _candidate_media_type_label(candidate: dict) -> str:
    media_type = _candidate_media_type(candidate)
    contract = (
        candidate.get("media_metadata")
        if isinstance(candidate, dict)
        else {}
    ) or {}
    identity = contract.get("identity") or {}
    genres = {
        _text(item).casefold()
        for item in identity.get("genres") or ()
        if _text(item)
    }
    category_kind = _text(
        (contract.get("placement") or {}).get("category_kind")
    ).casefold()
    is_animation = (
        category_kind.startswith("animated_")
        or any(
            label in genres
            for label in {"animation", "anime", "动画", "アニメ"}
        )
    )
    if media_type == "movie":
        return "动画电影" if is_animation else "电影"
    if media_type == "series":
        return "动画剧集" if is_animation else "剧集"
    return "未知"


def _metadata_candidate_ref(candidate: dict) -> str:
    value = _text(
        candidate.get("candidate_key")
        or candidate.get("candidate_id")
    )
    if value:
        return value[:160]
    for link in candidate.get("source_links") or ():
        if not isinstance(link, dict):
            continue
        provider = _text(link.get("provider")).casefold()
        identifiers = link.get("external_ids") or {}
        if provider and isinstance(identifiers, dict):
            stable_id = next(
                (_text(item) for item in identifiers.values() if _text(item)),
                "",
            )
            if stable_id:
                return f"{provider}:{stable_id}"[:160]
    return ""


def _metadata_candidate_preview(candidate: dict) -> dict:
    contract = candidate.get("media_metadata") or {}
    identity = contract.get("identity") or {}
    countries = [
        _text(item)
        for item in identity.get("countries") or ()
        if _text(item)
    ]
    return {
        "ref": _metadata_candidate_ref(candidate),
        "title": _text(
            identity.get("chinese_title")
            or identity.get("english_title")
            or "未知作品"
        ),
        "original_title": _text(
            identity.get("official_english_title")
            or identity.get("english_title")
            or identity.get("original_title")
        ),
        "year": _text(identity.get("year")),
        "countries": countries,
        "media_type": _candidate_media_type(candidate),
        "media_type_label": _candidate_media_type_label(candidate),
        "poster_url": _text(
            candidate.get("poster_url")
            or identity.get("poster_url")
        ),
    }


def _probe_scope(contract: dict, probe: dict) -> tuple[str, int | None, int | None]:
    if not isinstance(probe, dict):
        return "", None, None
    shape = str(probe.get("content_shape") or "").strip().casefold()
    observed_episodes = [
        item
        for item in (probe.get("observed_episodes") or [])
        if isinstance(item, dict)
    ]
    observed_seasons = set()
    for item in probe.get("observed_seasons") or []:
        value = (
            item.get("season_number")
            if isinstance(item, dict)
            else item
        )
        if (season := _positive_integer(value)) is not None:
            observed_seasons.add(season)
    observed_seasons.update(
        season
        for item in observed_episodes
        if (
            season := _positive_integer(item.get("season_number"))
        ) is not None
    )
    if shape == "single_episode" and len(observed_episodes) == 1:
        season_number = _positive_integer(
            observed_episodes[0].get("season_number")
        )
        episode_number = _positive_integer(
            observed_episodes[0].get("episode_number")
        )
        if season_number is not None and episode_number is not None:
            return "episode", season_number, episode_number
    if len(observed_seasons) > 1:
        return "whole_series", None, None
    if len(observed_seasons) == 1:
        return "season", next(iter(observed_seasons)), None
    if shape not in {
        "episode_pack_unscoped",
        "single_episode_unscoped",
    }:
        return "", None, None
    episode_numbers = {
        episode
        for item in observed_episodes
        if (
            episode := _positive_integer(item.get("episode_number"))
        ) is not None
    }
    if not episode_numbers:
        return "", None, None
    inventory = series_inventory(contract)
    matching_seasons = [
        season
        for season, episodes in inventory.all_by_season.items()
        if episode_numbers.issubset(set(episodes))
    ]
    if len(matching_seasons) != 1:
        return "", None, None
    season_number = matching_seasons[0]
    if shape == "single_episode_unscoped" and len(episode_numbers) == 1:
        return "episode", season_number, next(iter(episode_numbers))
    return "season", season_number, None


def _ambiguous_host_report_error(exc: Exception) -> bool:
    return not isinstance(exc, FeatureError) or exc.code in {
        "host_unavailable", "deadline_exceeded", "invalid_response",
    }


def _ambiguous_milestone_error(exc: Exception) -> bool:
    return _ambiguous_host_report_error(exc) or (
        isinstance(exc, FeatureError) and exc.code == "internal_error"
    )

_PLANNING_ERROR_MESSAGES = {
    "ambiguous_candidates": "存在多个候选，请补充年份或电影/剧集类型。",
    "evidence_conflict": "不同来源的年份或媒体类型冲突，请补充更明确的信息。",
    "insufficient_independent_support": "独立证据来源不足，无法安全生成计划。",
    "missing_bilingual_identity": "缺少可验证的中英文媒体身份。",
    "missing_year": "缺少可交叉验证的发行年份。",
    "tvdb_identity_required": "剧集缺少唯一 TVDB 身份，无法安全生成计划。",
    "tvdb_scope_not_verified": "TVDB 无法验证请求的季或集。",
    "ambiguous_numeric_role": "片名末尾数字无法证明是正式标题的一部分，请补充年份、完整片名或条目链接。",
    "unsupported_metadata_link": "链接不是可识别的豆瓣、TVDB 或 Wikipedia 作品、季或单集地址。",
    "multiple_metadata_entities": "链接无效，请一次只分享一个作品链接。",
    "unsupported_scope_syntax": "不支持范围、1x02 或英文数字单词写法；请使用作品名、S01、S01E01 或数字季/集。",
    "unsupported_special_scope": "暂不支持 Special、Season 0、OVA、OAD 或附加内容下载。",
    "direct_link_not_found": "无法读取该豆瓣/TVDB条目，请检查链接是否有效。",
    "direct_link_invalid": "链接条目缺少可验证的标题或稳定ID。",
    "no_match": "Wikipedia 未找到可验证的影视作品，请修改明确片名后重试。",
    "source_failure": "Wikipedia 查询失败，尚未形成可判断的候选。",
    "source_rate_limited": "来源请求受到限流，请稍后重试。",
    "source_fact_conflict": "来源事实存在冲突，无法安全确认作品身份，请重试。",
    "candidate_binding_failed": "候选无法绑定到本次来源事实，请重试。",
    "direct_link_anchor_missing": "固定链接锚点在来源事实中丢失，无法继续。",
    "fixed_link_read_failed": "固定链接读取失败，请重试或退出。",
    "metadata_conflict": "已选候选的媒体类型事实冲突。",
    "metadata_incomplete": "已选候选不足以形成严格媒体元数据。",
    "prowlarr_failure": "资源搜索失败，请重试或退出。",
}

_PROVIDER_LABELS = {
    "wikipedia": "维基百科",
    "wikidata": "维基数据",
    "douban": "豆瓣",
    "tmdb": "TMDB",
    "tvdb": "TVDB",
    "anilist": "AniList",
}
_MEDIA_TYPE_LABELS = {
    "movie": "电影",
    "series": "剧集",
    "unknown": "未知",
}
_CANDIDATE_ROLE_LABELS = {
    "movie": "电影",
    "series_root": "整部剧集",
    "season": "季度",
    "episode": "单集",
    "related_work": "关联作品",
    "work": "作品",
}
_RELATION_LABELS = {
    "standalone": "独立作品",
    "prequel": "前传",
    "sequel": "续作",
    "spin_off": "衍生作品",
    "special": "特别篇",
    "extension_movie": "延伸电影",
}
_CANDIDATE_VERSION_LABELS = {
    "v1": "来源完整",
    "v0": "来源待补充",
}
_METADATA_FIELD_LABELS = {
    "canonical_latin_title": "规范拉丁标题",
    "canonical_search_title": "规范检索标题",
    "chinese_title": "中文标题",
    "official_english_title": "官方英文标题",
    "romanized_original_title": "原名罗马字",
    "year": "发行年份",
    "media_type": "媒体类型",
    "source_links": "来源链接",
    "tvdb_root": "TVDB 剧集根条目",
    "tvdb_inventory": "TVDB 剧集清单",
    "verified_scope": "已验证季集范围",
}


def _human_media_type(value) -> str:
    normalized = _text(value).casefold()
    return _MEDIA_TYPE_LABELS.get(normalized, "未知")


def _human_candidate_role(value) -> str:
    normalized = _text(value).casefold()
    return _CANDIDATE_ROLE_LABELS.get(normalized, "作品")


def _human_relation(value) -> str:
    normalized = _text(value).casefold()
    return _RELATION_LABELS.get(normalized, "关联关系待确认")


def _human_metadata_field(value) -> str:
    normalized = _text(value)
    return _METADATA_FIELD_LABELS.get(
        normalized,
        normalized.replace("_", " ") or "必要字段",
    )


def _human_unresolved_source(value) -> str:
    normalized = _text(value)
    if normalized.endswith(":unresolved_scope_link"):
        return "季集关系尚未通过 TVDB 验证"
    if normalized.endswith(":source_url_missing"):
        return "已绑定来源缺少可读取链接"
    provider, separator, status = normalized.partition(":")
    if not separator:
        return normalized
    provider_label = _PROVIDER_LABELS.get(provider.casefold(), provider)
    status_label = {
        "not_bound": "尚未绑定到此候选",
        "not_found": "未找到匹配条目",
        "server_down": "暂时不可用",
        "timeout": "查询超时",
        "rate_limited": "查询受限",
        "blocked": "访问受限",
        "disabled": "未启用",
        "unavailable": "当前不可用",
        "authentication_failed": "认证失败",
        "credential_missing": "缺少凭据",
    }.get(status.casefold(), "状态待确认")
    return f"{provider_label}{status_label}"


class SearchFeature:
    def __init__(
        self,
        *,
        config: dict,
        host,
        plan_builder=None,
        release_search=None,
        release_rank=None,
        release_resolver=None,
        indexer_summary=None,
        indexer_loader=None,
        indexer_search=None,
        exact_link_resolver=None,
        selected_candidate_supplementer=None,
        candidate_poster_lookup=None,
        metadata_resolution_store=None,
        source_scheduler=None,
    ):
        self.config = config
        self.host = host
        self.allocator = TemporarySpecialAllocator()
        self.plan_builder = plan_builder or self._build_plan
        self.release_search = release_search or self._search_releases
        self.release_rank = release_rank or rank_releases
        self.release_resolver = release_resolver or resolve_prowlarr_download_url
        self.indexer_summary = (
            indexer_summary or get_prowlarr_indexer_summary
        )
        self.indexer_loader = indexer_loader or list_prowlarr_indexers
        self.indexer_search = indexer_search or search_prowlarr_indexer
        self.exact_link_resolver = exact_link_resolver or resolve_direct_link
        self._uses_default_selected_candidate_supplementer = (
            selected_candidate_supplementer is None
        )
        self.selected_candidate_supplementer = (
            selected_candidate_supplementer
            or self._supplement_selected_candidate
        )
        self.candidate_poster_lookup = (
            candidate_poster_lookup or self._lookup_candidate_poster
        )
        self.candidate_poster_timeout = 12.0
        self.metadata_resolution_store = (
            metadata_resolution_store or MetadataResolutionStore()
        )
        self._measurement_session_id = ContextVar(
            "search_measurement_session_id",
            default="",
        )
        self.source_scheduler = source_scheduler or SourceScheduler(
            observer=self._observe_source_request,
        )
        self.plans = {}
        self.awaiting_queries = set()
        self.awaiting_scope_inputs = {}
        self.config_wizard = SearchConfigWizard(config)
        self.runtime = None
        self.operations = {}
        self.owner_operations = {}

    @staticmethod
    def _log_measurement(
        event: str,
        *,
        search_session_id: str,
        status: str = "completed",
        duration_ms=None,
        **facts,
    ) -> None:
        log_search_measurement(
            runtime_context.logger,
            event,
            search_session_id=search_session_id,
            status=status,
            duration_ms=duration_ms,
            **facts,
        )

    def _observe_source_request(self, event: str, facts: dict) -> None:
        self._log_measurement(
            event,
            search_session_id=self._measurement_session_id.get(),
            **facts,
        )

    @staticmethod
    def _source_coordinates(candidate: dict) -> tuple[str, int | None, int | None]:
        contract = candidate.get("media_metadata") or {}
        decision = ((contract.get("evidence") or {}).get("decision") or {})
        scope = _text(
            candidate.get("intended_scope")
            or (contract.get("retrieval") or {}).get("scope")
            or decision.get("scope")
            or "work"
        ).casefold()

        def positive(value):
            if isinstance(value, bool):
                return None
            if isinstance(value, float) and not value.is_integer():
                return None
            try:
                value = int(value)
            except (TypeError, ValueError):
                return None
            return value if value > 0 else None

        return (
            scope,
            positive(
                candidate.get("requested_season_number")
                or decision.get("season_number")
            ),
            positive(
                candidate.get("requested_episode_number")
                or decision.get("episode_number")
            ),
        )

    async def _run_source_request(
        self,
        *,
        provider: str,
        purpose: str,
        media_type: str,
        identity: str,
        scope: str,
        season_number: int | None,
        episode_number: int | None,
        fetch,
        cacheable,
    ):
        return await self.source_scheduler.run(
            SourceRequestKey(
                provider=provider,
                purpose=purpose,
                media_type=media_type,
                identity=identity,
                scope=scope,
                season_number=season_number,
                episode_number=episode_number,
            ),
            fetch,
            cacheable=cacheable,
        )

    @staticmethod
    def _exact_link_signature(link) -> tuple[str, str, str, str, str]:
        return (
            _text(getattr(link, "provider", "")),
            _text(getattr(link, "media_type", "")),
            _text(getattr(link, "entity_id", "")),
            _text(getattr(link, "scope", "")),
            _text(getattr(link, "url", "")),
        )

    @staticmethod
    def _direct_matches_frozen(frozen: dict, direct) -> bool:
        stable_identity = getattr(direct, "stable_identity", ())
        if (
            not isinstance(stable_identity, tuple)
            or len(stable_identity) != 2
        ):
            return False
        key, value = (_text(item) for item in stable_identity)
        if not key or not value:
            return False
        frozen_ids = (
            frozen.get("external_ids")
            if isinstance(frozen.get("external_ids"), dict)
            else {}
        )
        expected = _text(frozen_ids.get(key))
        if expected and expected != value:
            return False
        evidence = getattr(direct, "evidence", None)
        return bool(
            isinstance(evidence, dict)
            and _text(evidence.get("status")).casefold() == "ok"
            and any(
                isinstance(item, dict) and bool(item)
                for item in evidence.get("facts") or ()
            )
        )

    async def _prefetch_exact_resolver(self, candidate: dict):
        candidate_scope, candidate_season, candidate_episode = (
            self._source_coordinates(candidate)
        )
        media_type = _candidate_media_type(candidate)
        requests = []
        for frozen in candidate.get("source_links") or ():
            if not isinstance(frozen, dict):
                continue
            parsed = classify_search_input(_text(frozen.get("url")))
            if parsed.kind != "link" or parsed.link is None:
                continue
            link = parsed.link
            signature = self._exact_link_signature(link)
            frozen_ids = (
                frozen.get("external_ids")
                if isinstance(frozen.get("external_ids"), dict)
                else {}
            )
            expected_ids = tuple(sorted(
                (_text(key), _text(value))
                for key, value in frozen_ids.items()
                if _text(key) and _text(value)
            ))
            identity = json.dumps({
                "request": {
                    "provider": signature[0],
                    "media_type": signature[1],
                    "entity_id": signature[2],
                    "scope": signature[3],
                    "url": signature[4],
                },
                "expected_fact_id": _text(frozen.get("fact_id")).split(
                    "@occurrence:",
                    1,
                )[0],
                "expected_ids": expected_ids,
            }, ensure_ascii=False, sort_keys=True)
            scope = _text(frozen.get("role") or candidate_scope).casefold()
            season_number = (
                frozen.get("season_number")
                or frozen.get("proposed_season_number")
                or candidate_season
            )
            episode_number = (
                frozen.get("episode_number")
                or frozen.get("proposed_episode_number")
                or candidate_episode
            )
            requests.append((
                signature,
                asyncio.create_task(self._run_source_request(
                    provider=signature[0],
                    purpose="anchor",
                    media_type=signature[1] or media_type,
                    identity=identity,
                    scope=scope,
                    season_number=season_number,
                    episode_number=episode_number,
                    fetch=lambda link=link: asyncio.to_thread(
                        self.exact_link_resolver,
                        link,
                    ),
                    cacheable=lambda value, frozen=frozen: (
                        self._direct_matches_frozen(frozen, value)
                    ),
                )),
            ))

        results = await asyncio.gather(
            *(task for _signature, task in requests),
            return_exceptions=True,
        )
        collected = {}
        for (signature, _task), result in zip(requests, results):
            collected.setdefault(signature, []).append(result)
        raw_by_link = MappingProxyType({
            signature: tuple(values)
            for signature, values in collected.items()
        })
        offsets = {}

        def resolve(link):
            signature = self._exact_link_signature(link)
            values = raw_by_link.get(signature, ())
            offset = offsets.get(signature, 0)
            if offset >= len(values):
                raise DirectLinkError("direct_link_prefetch_missing")
            offsets[signature] = offset + 1
            value = values[offset]
            if isinstance(value, BaseException):
                raise value
            return deepcopy(value)

        return resolve

    def bind_runtime(self, runtime):
        self.runtime = runtime

    async def _hydrate_selected_candidate(
        self,
        candidate: dict,
        *,
        metadata_id: str,
        raw_query: str,
        require_anchor: bool,
    ) -> dict:
        self._measurement_session_id.set(metadata_id)
        started_at = time.monotonic()
        try:
            anchor_resolver = await self._prefetch_exact_resolver(candidate)
            hydrated = await asyncio.to_thread(
                hydrate_frozen_candidate_anchor,
                candidate,
                metadata_id=metadata_id,
                raw_query=raw_query,
                require_anchor=require_anchor,
                resolver=anchor_resolver,
            )
            if (
                hydrated.get("metadata_hydrated")
                and not needs_authoritative_scope_enrichment(hydrated)
            ):
                self._log_measurement(
                    "search.hydration.completed",
                    search_session_id=metadata_id,
                    duration_ms=round((time.monotonic() - started_at) * 1000),
                    frozen_link_count=len(candidate.get("source_links") or ()),
                    anchor_required=bool(require_anchor),
                    metadata_hydrated=True,
                    enrichment_needed=False,
                )
                return hydrated
            try:
                if self._uses_default_selected_candidate_supplementer:
                    enriched = await self._supplement_selected_candidate(
                        hydrated,
                        raw_query,
                        purpose="authoritative_scope",
                    )
                else:
                    enriched = await self.selected_candidate_supplementer(
                        hydrated,
                        raw_query,
                    )
            except Exception as exc:
                if runtime_context.logger:
                    runtime_context.logger.warning(
                        "search_supplement status=failed "
                        "purpose=authoritative_scope "
                        f"error={type(exc).__name__}"
                    )
                raise CandidateHydrationError(
                    "metadata_incomplete",
                    ("verified_scope",),
                ) from exc
            strict_resolver = await self._prefetch_exact_resolver(enriched)
            result = await asyncio.to_thread(
                hydrate_frozen_candidate,
                enriched,
                metadata_id=metadata_id,
                raw_query=raw_query,
                require_anchor=require_anchor,
                resolver=strict_resolver,
            )
            self._log_measurement(
                "search.hydration.completed",
                search_session_id=metadata_id,
                duration_ms=round((time.monotonic() - started_at) * 1000),
                frozen_link_count=len(candidate.get("source_links") or ()),
                anchor_required=bool(require_anchor),
                metadata_hydrated=bool(result.get("metadata_hydrated")),
                enrichment_needed=True,
            )
            return result
        except CandidateHydrationError as exc:
            self._log_measurement(
                "search.hydration.failed",
                search_session_id=metadata_id,
                status="failed",
                duration_ms=round((time.monotonic() - started_at) * 1000),
                error_code=exc.code,
            )
            raise
        except Exception as exc:
            self._log_measurement(
                "search.hydration.failed",
                search_session_id=metadata_id,
                status="failed",
                duration_ms=round((time.monotonic() - started_at) * 1000),
                error_code=type(exc).__name__,
            )
            raise

    @staticmethod
    def _log_completed_once(
        plan_id: str,
        stored: dict | None,
        *,
        terminal_status: str,
        **fields,
    ) -> None:
        if isinstance(stored, dict) and stored.get(
            "search_completed_logged"
        ):
            return
        log_search_event(
            runtime_context.logger,
            "search.completed",
            search_session_id=plan_id,
            terminal_status=terminal_status,
            **fields,
        )
        if isinstance(stored, dict):
            stored["search_completed_logged"] = True

    async def metadata_capability(self, request: dict) -> dict:
        method = str(request.get("method") or "")
        if method not in {"resolve_metadata", "confirm_metadata"}:
            raise FeatureError(
                "method_not_allowed",
                "media.search method is not allowed",
            )
        payload = request.get("payload") or {}
        resolution_id = ""
        candidate_ref = ""
        if method == "confirm_metadata":
            resolution_id = _text(payload.get("resolution_id"))
            candidate_ref = _text(payload.get("candidate_ref"))
            if not resolution_id:
                return {
                    "status": "unresolved",
                    "reason_code": "resolution_id_required",
                }
            resolution_state, frozen = self.metadata_resolution_store.load(
                resolution_id
            )
            if resolution_state != "found" or frozen is None:
                return {
                    "status": "unresolved",
                    "reason_code": (
                        "resolution_expired"
                        if resolution_state == "expired"
                        else "resolution_not_found"
                    ),
                }
            selected_ref = _text(frozen.get("selected_candidate_ref"))
            cached_result = frozen.get("result")
            if isinstance(cached_result, dict):
                if selected_ref != candidate_ref:
                    return {
                        "status": "unresolved",
                        "reason_code": "resolution_already_confirmed",
                    }
                return deepcopy(cached_result)
            raw_query = _text(frozen.get("query"))
            probe = deepcopy(frozen.get("probe") or {})
            plan = deepcopy(frozen.get("plan") or {})
            plan_id = resolution_id
        else:
            raw_query = " ".join(str(payload.get("query") or "").split())
            if not raw_query:
                raise FeatureError("invalid_query", "metadata query is required")
            probe = (
                payload.get("probe")
                if isinstance(payload.get("probe"), dict)
                else {}
            )
            plan_id = f"cap-{uuid.uuid4().hex[:10]}"
        if probe and runtime_context.logger:
            runtime_context.logger.info(
                "metadata_probe "
                f"content_shape={str(probe.get('content_shape') or 'unknown')} "
                f"seasons={len(probe.get('observed_seasons') or [])} "
                f"episodes={len(probe.get('observed_episodes') or [])} "
                f"videos={str(probe.get('video_count') or 0)}"
            )
        if method == "resolve_metadata":
            try:
                plan = await self.plan_builder(raw_query, plan_id)
            except SearchPlanningError as exc:
                raise FeatureError(
                    "metadata_unresolved",
                    f"metadata resolution failed: {exc.code}",
                ) from exc
        all_candidates = [
            item
            for item in plan.get("candidates") or []
            if item.get("selectable") is not False
        ]
        candidates = list(all_candidates)
        probe_media_type = _probe_media_type(probe)
        if probe_media_type:
            unconstrained_count = len(candidates)
            candidates = [
                item
                for item in candidates
                if _candidate_media_type(item) == probe_media_type
            ]
            if runtime_context.logger:
                runtime_context.logger.info(
                    "metadata_probe_constraint "
                    f"media_type={probe_media_type} "
                    f"before={unconstrained_count} "
                    f"after={len(candidates)}"
                )
        if not candidates:
            return {
                "status": "unresolved",
                "reason_code": (
                    "media_type_mismatch"
                    if probe_media_type and all_candidates
                    else "no_candidate"
                ),
            }
        if method == "confirm_metadata":
            candidates = [
                item
                for item in candidates
                if _metadata_candidate_ref(item) == candidate_ref
            ]
            if len(candidates) != 1:
                return {
                    "status": "unresolved",
                    "reason_code": "invalid_candidate_ref",
                }
        elif len(candidates) != 1:
            frozen_candidates = deepcopy(candidates[:5])
            previews = [
                preview
                for item in frozen_candidates
                if (preview := _metadata_candidate_preview(item))["ref"]
            ]
            if not previews:
                return {
                    "status": "unresolved",
                    "reason_code": "candidate_ref_missing",
                }
            frozen_plan = deepcopy(plan)
            frozen_plan["candidates"] = frozen_candidates
            self.metadata_resolution_store.save(plan_id, {
                "query": raw_query,
                "probe": deepcopy(probe),
                "plan": frozen_plan,
            })
            return {
                "status": "confirmation_required",
                "resolution_id": plan_id,
                "query": raw_query,
                "probe": deepcopy(probe),
                "candidates": previews,
            }
        selected = deepcopy(candidates[0])
        if selected.get("links_frozen"):
            try:
                selected = await self._hydrate_selected_candidate(
                    selected,
                    metadata_id=plan_id,
                    raw_query=raw_query,
                    require_anchor=plan.get("entry_kind") == "link",
                )
            except CandidateHydrationError as exc:
                details = ",".join(exc.details)
                suffix = f":{details}" if details else ""
                raise FeatureError(
                    "metadata_unresolved",
                    f"exact metadata hydration failed: {exc.code}{suffix}",
                ) from exc
        selected_plan = {
            "plan_id": plan_id,
            "media_metadata": deepcopy(selected.get("media_metadata") or {}),
            "prowlarr_queries": list(selected.get("prowlarr_queries") or []),
        }
        contract = selected_plan["media_metadata"]
        placement = contract.get("placement") or {}
        if placement.get("mapping_kind") == "temporary_related_special":
            prefix = (
                "animated"
                if str(placement.get("category_kind") or "").startswith("animated_")
                else "live_action"
            )
            placement.update({
                "library_type": "movie",
                "category_kind": f"{prefix}_movie",
                "season_number": None,
                "episode_number": None,
                "mapping_kind": "standalone",
                "mapping_source": "noninteractive_standalone",
                "tvdb_episode_id": "",
            })
            contract["items"] = []
        elif placement.get("library_type") == "series":
            has_inventory_probe = bool(
                (probe or {}).get("observed_episodes")
                or (probe or {}).get("observed_seasons")
            )
            if has_inventory_probe:
                try:
                    contract = apply_inventory_probe_scope(contract, probe)
                except SeriesScopeError as exc:
                    reason = str(exc).split(None, 1)[0]
                    return {
                        "status": "unresolved",
                        "reason_code": reason,
                        "detail": str(exc),
                    }
                selected_plan["media_metadata"] = contract
            else:
                decision = ((contract.get("evidence") or {}).get("decision") or {})
                scope = str(decision.get("scope") or "movie_or_series")
                season_number = decision.get("season_number")
                episode_number = decision.get("episode_number")
                if scope == "episode":
                    contract = apply_series_scope(
                        contract,
                        "episode",
                        season_number=season_number,
                        episode_number=episode_number,
                    )
                elif scope == "season":
                    contract = apply_series_scope(
                        contract,
                        "season",
                        season_number=season_number,
                    )
                elif scope == "whole_series":
                    contract = apply_series_scope(
                        contract,
                        "whole_series",
                    )
                else:
                    return {
                        "status": "unresolved",
                        "reason_code": "scope_unresolved",
                    }
                selected_plan["media_metadata"] = contract
        try:
            contract = confirm_media_metadata(selected_plan)
        except ValueError as exc:
            raise FeatureError(
                "metadata_unresolved",
                "resolved metadata did not pass the canonical contract: "
                f"{exc}",
            ) from exc
        identity = contract["identity"]
        result = {
            "status": "resolved",
            "media_metadata": contract,
            "naming_metadata": {
                "source": "search-live",
                "media_type": (
                    (contract.get("retrieval") or {}).get("media_type")
                    or contract["placement"]["library_type"]
                ),
                "chinese_title": identity.get("chinese_title") or "",
                "english_title": identity.get("english_title") or "",
                "year": identity.get("year") or "",
            },
            "presentation": build_identity_presentation(contract),
        }
        if method == "confirm_metadata":
            self.metadata_resolution_store.cache_result(
                resolution_id,
                candidate_ref,
                result,
            )
        return result

    async def command(self, request: dict) -> dict:
        command = str(request.get("command") or "")
        if command == "search_config":
            owner = self._owner_key(request)
            if owner in self.awaiting_queries or any(
                item.get("owner") == owner for item in self.plans.values()
            ):
                return self._closed(
                    "⚠️ 请先完成或退出当前搜索，再打开 search 配置。"
                )
            result = self.config_wizard.start(request)
            operation = self._new_operation(
                request,
                state="awaiting_input",
                stage="config_section",
                status_text="选择搜索配置。",
                control="exit",
                kind="config",
            )
            result["operation"] = operation
            return result
        if command not in {"search", "s"}:
            raise FeatureError("not_found", f"unknown search command: {command}")
        self.config_wizard.clear(request)
        raw_query = " ".join(str(item) for item in request.get("args") or []).strip()
        if not raw_query:
            owner = self._owner_key(request)
            self.awaiting_queries.add(owner)
            operation = self._new_operation(
                request,
                state="awaiting_input",
                stage="query_input",
                status_text="输入片名。",
                control="exit",
                kind="search",
            )
            return {
                "actions": [{
                    "kind": "send_message",
                    "text": "请输入片名。",
                    "data": {"keyboard": [[{
                        "text": "退出",
                        "callback_data": "search:exit",
                    }]]},
                }],
                "session": {"state": "open"},
                "operation": operation,
            }
        if contains_url(raw_query):
            search_session_id = uuid.uuid4().hex[:10]
            bind_search_log_context(
                search_session_id,
                chat_id=request.get("chat_id"),
                user_id=request.get("user_id"),
                update_id=request.get("update_id"),
            )
            log_search_event(
                runtime_context.logger,
                "search.command_url_rejected",
                search_session_id=search_session_id,
                chat_id=request.get("chat_id"),
                user_id=request.get("user_id"),
            )
            log_search_event(
                runtime_context.logger,
                "search.completed",
                search_session_id=search_session_id,
                terminal_status="invalid_link",
            )
            return self._closed(
                "⚠️ /s 只接受片名；平台链接请直接发送到当前对话。"
            )
        return self._start_plan_task(raw_query, request)

    async def message(self, request: dict) -> dict:
        if self.config_wizard.has_session(request):
            return self._decorate_config_result(
                request, self.config_wizard.message(request)
            )
        key = self._owner_key(request)
        if key in self.awaiting_scope_inputs:
            return self._handle_scope_input(request, key)
        if key not in self.awaiting_queries:
            raw_query = str(request.get("text") or "").strip()
            parsed = classify_search_input(raw_query)
            if parsed.kind in {"link", "resolvable_link", "invalid_link"}:
                return self._start_plan_task(raw_query, request)
            return {
                "actions": [{"kind": "send_message", "text": "会话已失效，请重新开始。"}],
                "session": {"state": "close"},
            }
        self.awaiting_queries.discard(key)
        raw_query = str(request.get("text") or "").strip()
        if contains_url(raw_query):
            return self._closed(
                "⚠️ /s 只接受片名；平台链接请退出输入状态后直接发送。"
            )
        return self._start_plan_task(
            raw_query, request, reuse_owner=True
        )

    async def callback(self, request: dict) -> dict:
        payload = str(request.get("payload") or "")
        if payload.startswith("config:"):
            return self._decorate_config_result(
                request, self.config_wizard.callback(request)
            )
        if payload == "exit":
            return self._exit_owner_operation(request)
        parts = payload.split(":")
        if len(parts) < 2:
            raise FeatureError("invalid_callback", "search callback is invalid")
        action, plan_id = parts[:2]
        stored = self.plans.get(plan_id)
        if not stored or stored["owner"] != self._owner_key(request):
            return self._closed("⚠️ 搜索任务已过期，请重新搜索。")
        if action == "cancel":
            operation_id = stored.get("operation_id")
            terminal_status = (
                "source_unavailable"
                if stored.get("kind") == "planning_failure"
                else "cancelled"
            )
            self._log_completed_once(
                plan_id,
                stored,
                terminal_status=terminal_status,
            )
            self._cancel_release_tasks(stored)
            self._release_plan(plan_id)
            result = self._closed("已退出本次搜索。")
            if operation_id:
                result["operation"] = self._advance_operation(
                    operation_id,
                    state="cancelled",
                    stage="cancelled",
                    status_text="已退出搜索。",
                    control="",
                )
            return result
        if action == "reject":
            operation_id = stored.get("operation_id")
            log_search_event(
                runtime_context.logger,
                "search.user_rejected",
                search_session_id=plan_id,
            )
            self._log_completed_once(
                plan_id,
                stored,
                terminal_status="user_rejected",
            )
            self._release_plan(plan_id)
            result = self._closed("已结束本次搜索，没有继续查询其他来源。")
            if operation_id:
                result["operation"] = self._advance_operation(
                    operation_id,
                    state="cancelled",
                    stage="user_rejected",
                    status_text="已取消搜索。",
                    control="",
                )
            return result
        if action == "confirm":
            return self._start_release_search_task(plan_id, stored)
        if action == "clarify" and len(parts) == 3:
            return self._clarify_choice(plan_id, stored, parts[2], request)
        if action == "retry" and len(parts) == 2:
            stored_plan = stored.get("plan") or {}
            raw_query = str(stored_plan.get("raw_query") or "")
            raw_lock = stored_plan.get("locked_identity")
            locked_identity = (
                tuple(raw_lock)
                if isinstance(raw_lock, (list, tuple))
                and len(raw_lock) == 2
                else None
            )
            self._log_completed_once(
                plan_id,
                stored,
                terminal_status=(
                    "source_unavailable"
                    if stored.get("kind") == "planning_failure"
                    else "retry"
                ),
            )
            self._release_plan(plan_id)
            result = self._start_plan_task(
                raw_query,
                request,
                reuse_owner=True,
                locked_identity=locked_identity,
            )
            retry_action = (result.get("actions") or [{}])[0]
            retry_action["kind"] = "edit_message"
            return result
        if action == "browse" and len(parts) == 3:
            return self._browse_candidate(plan_id, stored, parts[2])
        if action == "candidate_page" and len(parts) == 3:
            return self._candidate_page(plan_id, stored, parts[2])
        if action == "select" and len(parts) == 3:
            return await self._select_candidate(plan_id, stored, parts[2])
        if action == "scope" and len(parts) >= 3:
            return self._scope_callback(
                plan_id,
                stored,
                parts[2],
                request,
                *parts[3:],
            )
        if action == "placement" and len(parts) == 3:
            return self._placement_callback(plan_id, stored, parts[2])
        if action == "release" and len(parts) == 3:
            return self._start_submission_task(plan_id, stored, parts[2])
        raise FeatureError("invalid_callback", "search callback action is invalid")

    def _start_plan_task(
        self,
        raw_query: str,
        request: dict,
        *,
        reuse_owner: bool = False,
        locked_identity: tuple[str, str] | None = None,
    ) -> dict:
        if self.runtime is None:
            raise FeatureError("not_ready", "search runtime is not ready")
        owner = self._owner_key(request)
        operation = self._operation_for_owner(owner) if reuse_owner else None
        if operation is None:
            operation_view = self._new_operation(
                request,
                state="running",
                stage="planning",
                status_text="正在识别媒体。",
                control="cancel",
                kind="search",
            )
            operation = self.operations[operation_view["operation_id"]]
        else:
            operation_view = self._advance_operation(
                operation["operation_id"],
                state="running",
                stage="planning",
                status_text="正在识别媒体。",
                control="cancel",
                details={},
            )
        plan_id = uuid.uuid4().hex[:10]
        parsed = classify_search_input(raw_query)
        bind_search_log_context(
            plan_id,
            chat_id=request.get("chat_id"),
            user_id=request.get("user_id"),
            operation_id=operation["operation_id"],
            update_id=request.get("update_id"),
        )
        log_search_event(
            runtime_context.logger,
            "search.input_classified",
            search_session_id=plan_id,
            chat_id=request.get("chat_id"),
            user_id=request.get("user_id"),
            input_kind=parsed.kind,
            reason=parsed.reason,
        )
        if parsed.kind in {"link", "resolvable_link", "invalid_link"}:
            log_search_event(
                runtime_context.logger,
                "search.direct_link_received",
                search_session_id=plan_id,
                input_kind=parsed.kind,
                url_count=len(parsed.urls),
            )
        task_id = f"search-plan-{operation['operation_id']}"
        task = self.runtime.spawn(
            self._prepare_plan_task(
                raw_query,
                dict(request),
                plan_id,
                operation["operation_id"],
                locked_identity,
            ),
            task_id=task_id,
        )
        operation.update({"task": task, "task_id": task_id, "plan_id": plan_id})
        return {
            "actions": [{
                "kind": "send_message",
                "text": "正在识别媒体...",
            }],
            "session": {"state": "close"},
            "operation": operation_view,
        }

    async def _prepare_plan_task(
        self,
        raw_query,
        request,
        plan_id,
        operation_id,
        locked_identity=None,
    ):
        try:
            result = await self._prepare_plan(
                raw_query,
                request,
                plan_id=plan_id,
                operation_id=operation_id,
                locked_identity=locked_identity,
            )
            action = (result.get("actions") or [{}])[0]
            returned_operation = (
                result.get("operation")
                if isinstance(result.get("operation"), dict)
                else None
            )
            if returned_operation is not None:
                await self._report_operation(
                    operation_id,
                    state=str(
                        returned_operation.get("state") or "running"
                    ),
                    stage=str(
                        returned_operation.get("stage") or "planning"
                    ),
                    status_text=str(
                        action.get("text") or "媒体方案已生成。"
                    ),
                    control=str(
                        returned_operation.get("control") or ""
                    ),
                    details=deepcopy(action.get("data") or {}),
                )
                return
            if plan_id in self.plans:
                clarification = (
                    self.plans[plan_id].get("kind") == "clarification"
                )
                recovery = (
                    self.plans[plan_id].get("kind")
                    == "planning_failure"
                )
                candidate_selection = bool(
                    (self.plans[plan_id].get("plan") or {}).get(
                        "links_frozen"
                    )
                    and not (self.plans[plan_id].get("plan") or {}).get(
                        "auto_confirm"
                    )
                )
                await self._report_operation(
                    operation_id,
                    state="awaiting_input",
                    stage=(
                        "clarification"
                        if clarification
                        else "candidate_recovery"
                        if recovery
                        else "candidate_selection"
                        if candidate_selection
                        else "plan_confirmation"
                    ),
                    status_text=str(action.get("text") or "媒体方案已生成。"),
                    control="exit",
                    details=deepcopy(action.get("data") or {}),
                )
                stored = self.plans.get(plan_id)
                accepted = (
                    stored.get("initial_candidate_report_accepted")
                    if isinstance(stored, dict)
                    else None
                )
                if accepted is not None:
                    accepted.set()
            else:
                await self._report_operation(
                    operation_id,
                    state="failed",
                    stage="planning",
                    status_text=str(action.get("text") or "媒体规划失败。"),
                    control="",
                )
        except asyncio.CancelledError:
            self._release_plan(plan_id)
            await self._report_operation(
                operation_id,
                state="cancelled",
                stage="planning",
                status_text="媒体规划已取消。",
                control="",
            )
        except Exception as exc:
            error_code = str(
                getattr(exc, "code", "")
                or type(exc).__name__
            )
            log_search_event(
                runtime_context.logger,
                "search.background_task_failed",
                search_session_id=plan_id,
                level="warning",
                operation_id=operation_id,
                stage="planning",
                error_code=error_code,
                error_type=type(exc).__name__,
            )
            self._release_plan(plan_id)
            if (self.operations.get(operation_id) or {}).get(
                "_host_report_rejected"
            ):
                log_search_event(
                    runtime_context.logger,
                    "search.completed",
                    search_session_id=plan_id,
                    level="warning",
                    terminal_status="operation_rejected",
                )
                return
            try:
                await self._report_operation(
                    operation_id,
                    state="failed",
                    stage="planning",
                    status_text=f"媒体规划失败：{type(exc).__name__}",
                    control="",
                )
            except Exception as report_exc:
                log_search_event(
                    runtime_context.logger,
                    "search.background_task_failed",
                    search_session_id=plan_id,
                    level="warning",
                    operation_id=operation_id,
                    stage="planning_finalization",
                    error_code=str(
                        getattr(report_exc, "code", "")
                        or type(report_exc).__name__
                    ),
                    error_type=type(report_exc).__name__,
                )

    def _start_release_search_task(self, plan_id: str, stored: dict) -> dict:
        operation_id = stored["operation_id"]
        task_id = f"search-releases-{operation_id}"
        task = self.runtime.spawn(
            self._release_search_task(plan_id, stored, operation_id),
            task_id=task_id,
        )
        self.operations[operation_id].update({"task": task, "task_id": task_id})
        return {
            "actions": [],
            "operation": self._operation_view(
                self.operations[operation_id]
            ),
        }

    async def _release_search_task(self, plan_id, stored, operation_id):
        try:
            result = await self._confirm_and_search(plan_id, stored)
            if stored.get("selection_frozen"):
                return
            action = (result.get("actions") or [{}])[0]
            recovery_details = deepcopy(action.get("data") or {})
            if plan_id in self.plans and stored.get("results"):
                await self._report_operation(
                    operation_id,
                    state="awaiting_input",
                    stage="release_selection",
                    status_text=str(action.get("text") or "请选择片源。"),
                    control="exit",
                    details=deepcopy(action.get("data") or {}),
                )
            elif (
                plan_id in self.plans
                and recovery_details.get("keyboard")
            ):
                await self._report_operation(
                    operation_id,
                    state="awaiting_input",
                    stage="prowlarr_recovery",
                    status_text=str(
                        action.get("text") or "资源搜索失败。"
                    ),
                    control="exit",
                    details=recovery_details,
                )
            else:
                await self._report_operation(
                    operation_id,
                    state="failed",
                    stage="prowlarr_search",
                    status_text=str(action.get("text") or "资源搜索失败。"),
                    control="",
                    details=self._prowlarr_status_details(operation_id),
                )
        except asyncio.CancelledError:
            if stored.get("selection_frozen"):
                return
            self._log_completed_once(
                plan_id,
                stored,
                terminal_status="cancelled",
            )
            self._release_plan(plan_id)
            await self._report_operation(
                operation_id,
                state="cancelled",
                stage="prowlarr_search",
                status_text="已取消搜索。",
                control="",
                details=self._prowlarr_status_details(operation_id),
            )
        except Exception as exc:
            error_code = str(
                getattr(exc, "code", "")
                or type(exc).__name__
            )
            log_search_event(
                runtime_context.logger,
                "search.background_task_failed",
                search_session_id=plan_id,
                level="warning",
                operation_id=operation_id,
                stage="prowlarr_search",
                error_code=error_code,
                error_type=type(exc).__name__,
            )
            self._log_completed_once(
                plan_id,
                stored,
                terminal_status="source_unavailable",
                error=error_code,
            )
            self._release_plan(plan_id)
            if not (self.operations.get(operation_id) or {}).get(
                "_host_report_rejected"
            ):
                try:
                    await self._report_operation(
                        operation_id,
                        state="failed",
                        stage="prowlarr_search",
                        status_text=(
                            f"资源搜索失败：{error_code}"
                        ),
                        control="",
                        details=self._prowlarr_status_details(
                            operation_id
                        ),
                    )
                except Exception:
                    pass

    def _start_submission_task(self, plan_id, stored, release_id):
        operation_id = stored["operation_id"]
        release_id = str(release_id or "")
        release_by_id = stored.get("release_by_id") or {}
        if release_id not in release_by_id:
            raise FeatureError(
                "invalid_release",
                "selected release is invalid",
            )
        stored["selection_frozen"] = True
        stored["selected_release_id"] = release_id
        self._cancel_release_tasks(stored)
        operation_view = self._advance_operation(
            operation_id,
            state="running",
            stage="resolving_release",
            status_text="正在获取下载链接。",
            control="cancel",
            details={},
        )
        task_id = f"search-submit-{operation_id}"
        task = self.runtime.spawn(
            self._submission_task(plan_id, stored, release_id, operation_id),
            task_id=task_id,
        )
        self.operations[operation_id].update({"task": task, "task_id": task_id})
        return {
            "actions": [{
                "kind": "edit_message",
                "text": "正在提交下载...",
            }],
            "operation": operation_view,
        }

    def _cancel_release_tasks(self, stored: dict):
        operation = self.operations.get(stored.get("operation_id")) or {}
        search_task = operation.get("task")
        if (
            search_task is not None
            and hasattr(search_task, "cancel")
            and not search_task.done()
        ):
            search_task.cancel()
        for task in stored.get("indexer_tasks") or ():
            if hasattr(task, "cancel") and not task.done():
                task.cancel()
        for field in ("wave_launcher_task", "incremental_report_task"):
            task = stored.get(field)
            if (
                task is not None
                and hasattr(task, "cancel")
                and not task.done()
            ):
                task.cancel()

    async def _submission_task(self, plan_id, stored, raw_index, operation_id):
        try:
            result = await self._submit_release(
                plan_id, stored, raw_index, operation_id
            )
            if result.get("release_resolution_recovered"):
                action = (result.get("actions") or [{}])[0]
                await self._report_operation(
                    operation_id,
                    state="awaiting_input",
                    stage="release_selection",
                    status_text=str(
                        action.get("text") or "请改选其他片源。"
                    ),
                    control="exit",
                    details=deepcopy(action.get("data") or {}),
                )
                return
            if self.operations[operation_id]["state"] != "handed_off":
                action = (result.get("actions") or [{}])[0]
                await self._report_operation(
                    operation_id,
                    state="failed",
                    stage="resolving_release",
                    status_text=str(action.get("text") or "片源提交失败。"),
                    control="",
                )
        except asyncio.CancelledError:
            self._log_completed_once(
                plan_id,
                stored,
                terminal_status="cancelled",
            )
            self._release_plan(plan_id)
            await self._report_operation(
                operation_id,
                state="cancelled",
                stage="resolving_release",
                status_text="已取消下载提交。",
                control="",
            )
        except Exception as exc:
            error_code = str(
                getattr(exc, "code", "")
                or type(exc).__name__
            )
            log_search_event(
                runtime_context.logger,
                "search.background_task_failed",
                search_session_id=plan_id,
                level="warning",
                operation_id=operation_id,
                stage="resolving_release",
                error_code=error_code,
                error_type=type(exc).__name__,
            )
            self._log_completed_once(
                plan_id,
                stored,
                terminal_status="source_unavailable",
                error=error_code,
            )
            self._release_plan(plan_id)
            if (
                not (self.operations.get(operation_id) or {}).get(
                    "_host_report_rejected"
                )
                and self.operations[operation_id]["state"] != "failed"
            ):
                try:
                    await self._report_operation(
                        operation_id,
                        state="failed",
                        stage="resolving_release",
                        status_text=(
                            f"片源提交失败：{error_code}"
                        ),
                        control="",
                    )
                except Exception:
                    pass

    async def _prepare_plan(
        self,
        raw_query: str,
        request: dict,
        *,
        plan_id: str,
        operation_id: str,
        locked_identity: tuple[str, str] | None = None,
    ) -> dict:
        if not raw_query:
            return self._closed("⚠️ 搜索内容不能为空。")
        self._measurement_session_id.set(plan_id)
        discovery_started_at = time.monotonic()
        input_kind = classify_search_input(raw_query).kind
        try:
            if locked_identity:
                plan = await self.plan_builder(
                    raw_query,
                    plan_id,
                    locked_identity=locked_identity,
                )
            else:
                plan = await self.plan_builder(raw_query, plan_id)
        except SearchPlanningError as exc:
            code = getattr(exc, "code", str(exc))
            self._log_measurement(
                "search.discovery.failed",
                search_session_id=plan_id,
                status="failed",
                duration_ms=max(
                    0,
                    round((time.monotonic() - discovery_started_at) * 1000),
                ),
                entry_kind=input_kind,
                query_chars=len(str(raw_query or "")),
                error_code=code,
            )
            reason_codes = tuple(
                getattr(exc, "reason_codes", ()) or ()
            )
            log_search_event(
                runtime_context.logger,
                "search.planning_failed",
                search_session_id=plan_id,
                level="warning",
                error_code=code,
                reason_codes=list(reason_codes),
                query_chars=len(str(raw_query or "")),
            )
            message = _PLANNING_ERROR_MESSAGES.get(
                code,
                "媒体证据无法形成有效计划，请补充信息后重试。",
            )
            if code in {
                "source_failure",
                "source_rate_limited",
                "fixed_link_read_failed",
            } and reason_codes:
                message = (
                    f"{message.rstrip('。')}："
                    f"{'、'.join(reason_codes)}。"
                )
            if code == "metadata_incomplete" and reason_codes:
                message = (
                    f"{message.rstrip('。')}；缺失字段："
                    f"{'、'.join(reason_codes)}。"
                )
            recoverable_codes = {
                "source_failure",
                "source_rate_limited",
                "source_fact_conflict",
                "candidate_binding_failed",
                "fixed_link_read_failed",
                "metadata_conflict",
                "metadata_incomplete",
            }
            if code in recoverable_codes:
                self.plans[plan_id] = {
                    "kind": "planning_failure",
                    "owner": self._owner_key(request),
                    "created_at": time.time(),
                    "plan": {
                        "plan_id": plan_id,
                        "raw_query": raw_query,
                        "locked_identity": locked_identity,
                    },
                    "candidates": (),
                    "selected_path": "",
                    "results": [],
                    "operation_id": operation_id,
                }
                return {
                    "actions": [{
                        "kind": "send_message",
                        "text": f"❌ {message}",
                        "data": {"keyboard": [[{
                        "text": "重试",
                        "callback_data": f"search:retry:{plan_id}",
                    }], [{
                        "text": "退出",
                        "callback_data": f"search:cancel:{plan_id}",
                    }]]},
                    }],
                    "session": {"state": "close"},
                }
            log_search_event(
                runtime_context.logger,
                "search.completed",
                search_session_id=plan_id,
                level="warning",
                terminal_status=(
                    "invalid_link"
                    if "link" in code
                    else "no_match"
                ),
                code=code,
            )
            return self._closed(f"❌ 无法生成媒体元数据：{message}")
        except Exception as exc:
            self._log_measurement(
                "search.discovery.failed",
                search_session_id=plan_id,
                status="failed",
                duration_ms=max(
                    0,
                    round((time.monotonic() - discovery_started_at) * 1000),
                ),
                entry_kind=input_kind,
                query_chars=len(str(raw_query or "")),
                error_code=type(exc).__name__,
            )
            log_search_event(
                runtime_context.logger,
                "search.completed",
                search_session_id=plan_id,
                level="error",
                terminal_status="internal_error",
                error=type(exc).__name__,
            )
            return self._closed(f"❌ 媒体规划失败：{type(exc).__name__}")
        discovery_summary = (
            plan.get("discovery_summary")
            if isinstance(plan.get("discovery_summary"), dict)
            else {}
        )
        discovered_candidates = plan.get("candidates")
        self._log_measurement(
            "search.discovery.completed",
            search_session_id=plan_id,
            duration_ms=max(
                0,
                round((time.monotonic() - discovery_started_at) * 1000),
            ),
            entry_kind=input_kind,
            query_chars=len(str(raw_query or "")),
            candidate_count=(
                len(discovered_candidates)
                if isinstance(discovered_candidates, list)
                and discovered_candidates
                else 1
            ),
            exact_count=int(discovery_summary.get("exact_count") or 0),
            relation_count=int(discovery_summary.get("relation_count") or 0),
        )
        clarification = (
            plan.get("clarification")
            if isinstance(plan.get("clarification"), dict)
            else None
        )
        if (
            plan.get("status") == "needs_clarification"
            and clarification is not None
        ):
            options = []
            for item in (clarification.get("options") or [])[:6]:
                if not (
                    isinstance(item, dict)
                    and str(item.get("label") or "").strip()
                    and str(item.get("query") or "").strip()
                ):
                    continue
                option = {
                    "label": " ".join(
                        str(item.get("label") or "").split()
                    ),
                    "query": " ".join(
                        str(item.get("query") or "").split()
                    ),
                    "media_type": str(
                        item.get("media_type") or ""
                    ).strip(),
                    "year": str(item.get("year") or "").strip(),
                }
                raw_lock = item.get("locked_identity")
                if (
                    isinstance(raw_lock, dict)
                    and str(raw_lock.get("key") or "").strip()
                    in {"tvdb", "douban_subject", "wikipedia"}
                    and str(raw_lock.get("value") or "").strip()
                ):
                    option["locked_identity"] = (
                        str(raw_lock["key"]).strip(),
                        str(raw_lock["value"]).strip(),
                    )
                options.append(option)
            if not options:
                return self._closed(
                    "❌ 无法生成媒体元数据：澄清选项无效。"
                )
            self.plans[plan_id] = {
                "kind": "clarification",
                "owner": self._owner_key(request),
                "created_at": time.time(),
                "plan": plan,
                "clarification_reason": " ".join(
                    str(clarification.get("reason") or "").split()
                ),
                "clarification_options": tuple(options),
                "candidates": (),
                "selected_path": "",
                "results": [],
                "operation_id": operation_id,
            }
            return {
                "actions": [
                    self._clarification_action(
                        self.plans[plan_id],
                        edit=False,
                    )
                ],
                "session": {"state": "close"},
            }
        candidates = plan.get("candidates") if isinstance(plan.get("candidates"), list) else []
        if not candidates:
            candidates = [{
                "candidate_key": f"legacy:{plan_id}",
                "score": {"total": 100},
                "recommended": True,
                "selectable": True,
                "media_metadata": plan.get("media_metadata") or {},
                "prowlarr_queries": list(plan.get("prowlarr_queries") or []),
                "poster_url": (plan.get("media_metadata") or {}).get("identity", {}).get("poster_url") or "",
                "reasons": [],
            }]
        selectable = [item for item in candidates if item.get("selectable") is not False]
        if not selectable:
            log_search_event(
                runtime_context.logger,
                "search.completed",
                search_session_id=plan_id,
                level="warning",
                terminal_status="no_match",
                reason="no_selectable_candidate",
            )
            self.allocator.release(plan_id)
            return self._closed("❌ 候选最高评分低于 65，受控检索后仍不足以安全确认。")
        route = None
        if not plan.get("links_frozen"):
            route = resolve_category_route(
                self.config,
                (
                    selectable[0].get(
                        "media_metadata", {}
                    ).get("placement") or {}
                ).get("category_kind"),
            )
            if not route:
                log_search_event(
                    runtime_context.logger,
                    "search.completed",
                    search_session_id=plan_id,
                    level="error",
                    terminal_status="source_unavailable",
                    reason="category_route_missing",
                )
                self.allocator.release(plan_id)
                return self._closed("❌ 媒体分类没有对应保存目录。")
        self.plans[plan_id] = {
            "owner": self._owner_key(request),
            "created_at": time.time(),
            "plan": plan,
            "candidates": tuple(deepcopy(candidates)),
            "selected_path": route["path"] if route else "",
            "results": [],
            "operation_id": operation_id,
            "initial_candidate_report_accepted": asyncio.Event(),
        }
        if plan.get("links_frozen") and plan.get("auto_confirm") is True:
            log_search_event(
                runtime_context.logger,
                "search.user_confirmed",
                search_session_id=plan_id,
                confirmation_mode="program_auto",
                candidate_id=selectable[0].get("candidate_id"),
            )
            selected_result = await self._select_candidate(
                plan_id,
                self.plans[plan_id],
                str(candidates.index(selectable[0])),
            )
            return selected_result
        self._start_candidate_poster_enrichment(
            plan_id,
            self.plans[plan_id],
        )
        action = (
            self._candidate_grid_action(self.plans[plan_id])
            if plan.get("links_frozen") and selectable
            else self._candidate_action(
                self.plans[plan_id],
                0,
                edit=False,
            )
        )
        log_search_event(
            runtime_context.logger,
            "search.candidates_displayed",
            search_session_id=plan_id,
            candidate_count=len(selectable),
            candidate_ids=[
                item.get("candidate_id") or item.get("candidate_key")
                for item in selectable
            ],
        )
        return {
            "actions": [action],
            "session": {"state": "close"},
        }

    def _clarification_action(self, stored: dict, *, edit: bool) -> dict:
        plan_id = str((stored.get("plan") or {}).get("plan_id") or "")
        reason = (
            str(stored.get("clarification_reason") or "").strip()
            or "存在多个可能目标，请选择后继续验证。"
        )
        keyboard = [[{
            "text": option["label"],
            "callback_data": f"search:clarify:{plan_id}:{index}",
        }] for index, option in enumerate(
            stored.get("clarification_options") or ()
        )]
        keyboard.append([{
            "text": "退出",
            "callback_data": f"search:cancel:{plan_id}",
        }])
        return {
            "kind": "edit_message" if edit else "send_message",
            "text": f"需要确认搜索目标\n{reason}",
            "data": {"keyboard": keyboard},
        }

    def _clarify_choice(
        self,
        plan_id: str,
        stored: dict,
        raw_index: str,
        request: dict,
    ) -> dict:
        try:
            option = stored["clarification_options"][int(raw_index)]
        except (KeyError, ValueError, IndexError):
            raise FeatureError(
                "invalid_callback",
                "search clarification option is invalid",
            ) from None
        operation_id = str(stored.get("operation_id") or "")
        refined_query = str(option.get("query") or "").strip()
        label = str(option.get("label") or refined_query).strip()
        locked_identity = option.get("locked_identity")
        self._log_completed_once(
            plan_id,
            stored,
            terminal_status="retry",
            reason="clarification_selected",
        )
        self._release_plan(plan_id)
        result = self._start_plan_task(
            refined_query,
            request,
            reuse_owner=True,
            locked_identity=locked_identity,
        )
        action = (result.get("actions") or [{}])[0]
        action.update({
            "kind": "edit_message",
            "text": f"已选择{label}，正在确认媒体身份。",
        })
        if operation_id:
            result["operation"] = self._operation_view(
                self.operations[operation_id]
            )
        return result

    def _candidate_action(self, stored: dict, index: int, *, edit: bool) -> dict:
        candidates = stored["candidates"]
        candidate = candidates[index]
        contract = candidate["media_metadata"]
        identity = contract.get("identity") or {}
        placement = contract.get("placement") or {}
        if (stored.get("plan") or {}).get("links_frozen"):
            title = _candidate_display_title(
                identity,
                component_limit=48,
            )
            providers = list(dict.fromkeys(
                _text(link.get("provider")).casefold()
                for link in candidate.get("source_links") or ()
                if isinstance(link, dict)
                and _text(link.get("provider"))
            ))
            source = "、".join(
                _PROVIDER_LABELS.get(provider, provider)
                for provider in providers
            ) or "已验证元数据"
            lines = [title]
            lines.extend([
                f"类型：{_human_media_type(placement.get('library_type'))}",
                "国家/地区："
                + (
                    "、".join(
                        _text(item)
                        for item in identity.get("countries") or ()
                        if _text(item)
                    )
                    or "未知"
                ),
                f"来源：{source}",
            ])
            if summary := _compact_summary(identity.get("summary")):
                lines.append(f"总览：{summary}")
            plan_id = stored["plan"]["plan_id"]
            keyboard = []
            if candidate.get("selectable") is not False:
                keyboard.append([{
                    "text": "就是它",
                    "callback_data": f"search:select:{plan_id}:{index}",
                }])
            keyboard.append([{
                "text": "都不是",
                "callback_data": f"search:reject:{plan_id}",
            }])
            poster = _text(candidate.get("poster_url"))
            data = {"keyboard": keyboard}
            if poster.startswith("https://"):
                data["photo_url"] = poster
                kind = "edit_photo" if edit else "send_photo"
            else:
                kind = "edit_message" if edit else "send_message"
            return {
                "kind": kind,
                "text": "\n".join(lines),
                "data": data,
            }
        score = candidate.get("score") or {}
        title = _candidate_display_title(
            identity,
            component_limit=48,
        )
        relation = (contract.get("relation") or {}).get("type") or "standalone"
        recommended = " · 推荐" if candidate.get("recommended") else ""
        text = (
            f"候选 {index + 1}/{len(candidates)}{recommended}\n"
            f"{title}\n"
            f"类型：{_human_media_type(placement.get('library_type'))}"
            f" · 关系：{_human_relation(relation)}\n"
            f"评分：{score.get('total', 0)}/100"
        )
        if summary := _compact_summary(identity.get("summary")):
            text += f"\n总览：{summary}"
        navigation = []
        if len(candidates) > 1:
            navigation = [{
                "text": "上一项",
                "callback_data": f"search:browse:{stored['plan']['plan_id']}:{(index - 1) % len(candidates)}",
            }, {
                "text": "下一项",
                "callback_data": f"search:browse:{stored['plan']['plan_id']}:{(index + 1) % len(candidates)}",
            }]
        keyboard = [navigation] if navigation else []
        if candidate.get("selectable") is not False:
            callback_data = (
                f"search:confirm:{stored['plan']['plan_id']}"
                if str(candidate.get("candidate_key") or "").startswith("legacy:")
                else f"search:select:{stored['plan']['plan_id']}:{index}"
            )
            keyboard.append([{
                "text": "选择并验证",
                "callback_data": callback_data,
            }])
        keyboard.append([{
            "text": "退出",
            "callback_data": f"search:cancel:{stored['plan']['plan_id']}",
        }])
        poster = str(candidate.get("poster_url") or "")
        data = {"keyboard": keyboard}
        if poster.startswith("https://"):
            data["photo_url"] = poster
            kind = "edit_photo" if edit else "send_photo"
        else:
            kind = "edit_message" if edit else "send_message"
        return {"kind": kind, "text": text, "data": data}

    def _candidate_grid_action(self, stored: dict, *, page: int = 0) -> dict:
        all_candidates = list(stored.get("candidates") or ())
        page_size = 5
        page_count = max(1, (len(all_candidates) + page_size - 1) // page_size)
        page = max(0, min(int(page), page_count - 1))
        start = page * page_size
        candidates = all_candidates[start:start + page_size]
        lines = [
            "请选择作品候选："
            if page_count == 1
            else f"请选择作品候选（第 {page + 1}/{page_count} 页）："
        ]
        poster_items = []
        keyboard = []
        has_poster = False
        plan_id = str((stored.get("plan") or {}).get("plan_id") or "")
        for local_index, candidate in enumerate(candidates, 1):
            index = start + local_index
            contract = candidate.get("media_metadata") or {}
            identity = contract.get("identity") or {}
            placement = contract.get("placement") or {}
            chinese_title = _text(
                identity.get("chinese_title")
                or identity.get("english_title")
                or "未知"
            )[:36]
            title = _candidate_display_title(
                identity,
                component_limit=36,
            )
            lines.append(
                f"{index}. <b>{html.escape(title)}</b>"
            )
            lines.extend([
                "类型："
                + html.escape(
                    _candidate_media_type_label(candidate)
                ),
                "国家/地区："
                + html.escape(
                    "、".join(
                        _text(item)
                        for item in identity.get("countries") or ()
                        if _text(item)
                    )
                    or "未知"
                ),
                "来源：" + (
                    "、".join(
                        _PROVIDER_LABELS.get(provider, provider)
                        for provider in dict.fromkeys(
                            _text(link.get("provider")).casefold()
                            for link in candidate.get("source_links") or ()
                            if isinstance(link, dict)
                            and _text(link.get("provider"))
                        )
                    )
                    or "已验证元数据"
                ),
            ])
            if len(all_candidates) == 1 and (
                summary := _compact_summary(identity.get("summary"))
            ):
                lines.append(f"总览：{html.escape(summary)}")
            poster_url = _text(candidate.get("poster_url"))
            has_poster = has_poster or poster_url.startswith("https://")
            poster_items.append({
                "number": index,
                "title": chinese_title,
                "poster_url": (
                    poster_url
                    if poster_url.startswith("https://")
                    else ""
                ),
            })
            keyboard.append([{
                "text": (
                    f"{index}. "
                    + _candidate_display_title(
                        identity,
                        component_limit=18,
                    )
                ),
                "callback_data": f"search:select:{plan_id}:{index - 1}",
            }])
        navigation = []
        if page > 0:
            navigation.append({
                "text": "上一页",
                "callback_data": (
                    f"search:candidate_page:{plan_id}:{page - 1}"
                ),
            })
        if page + 1 < page_count:
            navigation.append({
                "text": "下一页",
                "callback_data": (
                    f"search:candidate_page:{plan_id}:{page + 1}"
                ),
            })
        if navigation:
            keyboard.append(navigation)
        keyboard.append([{
            "text": "都不是",
            "callback_data": f"search:reject:{plan_id}",
        }])
        data = {
            "keyboard": keyboard,
            "parse_mode": "HTML",
        }
        kind = "send_photo_grid"
        data["poster_items"] = poster_items
        caption = "\n".join(lines)
        visible_caption = html.unescape(
            re.sub(r"<[^>]+>", "", caption)
        )
        if len(visible_caption) > 1024:
            caption = caption[:1000]
        return {
            "kind": kind,
            "text": caption,
            "parse_mode": "HTML",
            "data": data,
        }

    def _candidate_page(
        self,
        plan_id: str,
        stored: dict,
        raw_page: str,
    ) -> dict:
        try:
            page = int(raw_page)
        except ValueError:
            raise FeatureError(
                "invalid_candidate",
                "candidate page is invalid",
            ) from None
        total = len(stored.get("candidates") or ())
        if page < 0 or page * 5 >= total:
            raise FeatureError(
                "invalid_candidate",
                "candidate page is invalid",
            )
        action = self._candidate_grid_action(stored, page=page)
        operation = self._advance_operation(
            stored["operation_id"],
            state="awaiting_input",
            stage="candidate_selection",
            status_text=f"候选第 {page + 1} 页",
            control="exit",
            details=deepcopy(action["data"]),
        )
        return {"actions": [action], "operation": operation}

    def _browse_candidate(self, plan_id: str, stored: dict, raw_index: str) -> dict:
        try:
            index = int(raw_index)
            stored["candidates"][index]
        except (ValueError, IndexError):
            raise FeatureError("invalid_candidate", "selected candidate is invalid") from None
        action = self._candidate_action(stored, index, edit=True)
        operation = self._advance_operation(
            stored["operation_id"],
            state="awaiting_input",
            stage="candidate_selection",
            status_text=action["text"],
            control="exit",
            details=deepcopy(action["data"]),
        )
        return {"actions": [action], "operation": operation}

    async def _select_candidate(
        self, plan_id: str, stored: dict, raw_index: str
    ) -> dict:
        try:
            index = int(raw_index)
            candidate = deepcopy(stored["candidates"][index])
        except (ValueError, IndexError):
            raise FeatureError("invalid_candidate", "selected candidate is invalid") from None
        if candidate.get("selectable") is False:
            return {"actions": [self._candidate_action(stored, index, edit=True)]}
        if not (stored.get("plan") or {}).get("auto_confirm"):
            log_search_event(
                runtime_context.logger,
                "search.user_confirmed",
                search_session_id=plan_id,
                confirmation_mode="user",
                candidate_id=(
                    candidate.get("candidate_id")
                    or candidate.get("candidate_key")
                ),
            )
        if candidate.get("links_frozen"):
            raw_query = str(
                (stored.get("plan") or {}).get("raw_query") or ""
            )
            try:
                candidate = await self._hydrate_selected_candidate(
                    candidate,
                    metadata_id=plan_id,
                    raw_query=raw_query,
                    require_anchor=True,
                )
                stored["candidates"][index].update(deepcopy(candidate))
            except CandidateHydrationError as exc:
                source_links = [
                    item
                    for item in candidate.get("source_links") or ()
                    if isinstance(item, dict)
                ]
                anchor_provider = next(
                    (
                        _text(item.get("provider")).casefold()
                        for item in source_links
                        if _text(item.get("fact_id"))
                        == _text(candidate.get("anchor_fact_id"))
                    ),
                    "",
                )
                media_type = _text(
                    (
                        (candidate.get("media_metadata") or {}).get(
                            "identity"
                        )
                        or {}
                    ).get("content_kind")
                ).casefold()
                can_degrade_tvdb = bool(
                    media_type == "series"
                    and _text(candidate.get("intended_scope"))
                    in {"work", "whole_series"}
                    and anchor_provider != "tvdb"
                    and any(
                        _text(item.get("provider")).casefold() == "tvdb"
                        for item in source_links
                    )
                )
                if can_degrade_tvdb:
                    degraded = deepcopy(candidate)
                    degraded["source_links"] = [
                        item
                        for item in source_links
                        if _text(item.get("provider")).casefold()
                        != "tvdb"
                    ]
                    degraded["intended_scope"] = "whole_series"
                    degraded["requested_season_number"] = None
                    degraded["requested_episode_number"] = None
                    degraded["unresolved_sources"] = list(dict.fromkeys([
                        *(
                            degraded.get("unresolved_sources")
                            or ()
                        ),
                        "tvdb:unavailable",
                    ]))
                    try:
                        candidate = await asyncio.to_thread(
                            hydrate_frozen_candidate,
                            degraded,
                            metadata_id=plan_id,
                            raw_query=raw_query,
                            require_anchor=True,
                            resolver=self.exact_link_resolver,
                        )
                    except CandidateHydrationError as degraded_exc:
                        exc = degraded_exc
                    else:
                        stored["candidates"][index].update(
                            deepcopy(candidate)
                        )
                        log_search_event(
                            runtime_context.logger,
                            "search.tvdb_completed",
                            search_session_id=plan_id,
                            level="warning",
                            status="unavailable",
                            matched=False,
                            degraded_scope="whole_series",
                        )
                        exc = None
                if exc is None:
                    pass
                else:
                    action = self._candidate_action(stored, index, edit=True)
                    detail = {
                        "fixed_link_read_failed": "固定链接读取失败",
                        "candidate_binding_failed": "来源绑定失败",
                        "source_fact_conflict": "来源事实存在冲突",
                        "metadata_conflict": "元数据类型冲突",
                        "metadata_incomplete": "严格媒体元数据不完整",
                    }.get(exc.code, "候选精确读取失败")
                    missing = (
                        "（"
                        + "、".join(
                            _human_metadata_field(item)
                            for item in exc.details
                        )
                        + "）"
                        if exc.details
                        and exc.code != "source_fact_conflict"
                        else ""
                    )
                    keyboard = (action.get("data") or {}).get("keyboard") or []
                    retry_callback = (
                        f"search:select:{plan_id}:{index}"
                    )
                    if exc.code == "fixed_link_read_failed":
                        action["text"] += (
                            f"\n❌ {detail}{missing}。可重试精确读取或退出。"
                        )
                        retry_button = next(
                            (
                                button
                                for row in keyboard
                                for button in row
                                if button.get("callback_data")
                                == retry_callback
                            ),
                            None,
                        )
                        if retry_button is None:
                            keyboard.insert(0, [{
                                "text": "重试精确读取",
                                "callback_data": retry_callback,
                            }])
                        else:
                            retry_button["text"] = "重试精确读取"
                    else:
                        action["text"] += (
                            f"\n❌ {detail}{missing}。"
                            "请查看其他候选，或退出。"
                        )
                        keyboard[:] = [
                            [
                                button
                                for button in row
                                if button.get("callback_data")
                                != retry_callback
                            ]
                            for row in keyboard
                        ]
                        keyboard[:] = [row for row in keyboard if row]
                    return {"actions": [action]}
        stored["selected_candidate"] = deepcopy(candidate)
        selected_plan = {
            "plan_id": plan_id,
            "media_metadata": candidate["media_metadata"],
            "prowlarr_queries": list(candidate.get("prowlarr_queries") or []),
            "source_queries": deepcopy(stored["plan"].get("source_queries") or {}),
        }
        try:
            await self._apply_selected_relation(candidate, selected_plan, stored)
            contract = selected_plan["media_metadata"]
            placement = contract.get("placement") or {}
            if placement.get("mapping_kind") == "temporary_related_special":
                stored["plan"] = selected_plan
                stored["selected_candidate_key"] = candidate.get("candidate_key") or ""
                self._invalidate_candidate_poster_enrichment(stored)
                return self._related_placement_action(plan_id, stored)
            if placement.get("library_type") == "series":
                retrieval = contract.get("retrieval") or {}
                decision = ((contract.get("evidence") or {}).get("decision") or {})
                degraded_whole_series = bool(
                    not contract.get("items")
                    and _text(
                        retrieval.get("scope")
                    ) == "whole_series"
                    and "warning:tvdb_inventory_unavailable"
                    in (contract.get("warnings") or ())
                )
                degraded_bounded_season = bool(
                    not contract.get("items")
                    and _text(retrieval.get("scope")) == "season"
                    and int(decision.get("season_number") or 0) > 0
                    and "warning:episode_inventory_unavailable"
                    in (contract.get("warnings") or ())
                )
                if (
                    not contract.get("items")
                    and not degraded_whole_series
                    and not degraded_bounded_season
                ):
                    raise SeriesScopeError("tvdb_scope_not_verified")
                scope = str(decision.get("scope") or "movie_or_series")
                if degraded_whole_series:
                    selected_plan["media_metadata"] = apply_series_scope(
                        contract, "whole_series"
                    )
                elif scope == "episode":
                    selected_plan["media_metadata"] = apply_series_scope(
                        contract,
                        "episode",
                        season_number=decision.get("season_number"),
                        episode_number=decision.get("episode_number"),
                    )
                elif scope == "whole_series":
                    selected_plan["media_metadata"] = apply_series_scope(
                        contract, "whole_series"
                    )
                else:
                    stored["plan"] = selected_plan
                    stored["selected_candidate_key"] = (
                        candidate.get("candidate_key") or ""
                    )
                    self._invalidate_candidate_poster_enrichment(stored)
                    return self._series_scope_action(plan_id, stored)
        except (ValueError, SeriesScopeError):
            action = self._candidate_action(stored, index, edit=True)
            action["text"] += "\n❌ TVDB 无法验证该剧集的季集范围，请重试或提供 TVDB 链接。"
            return {"actions": [action]}
        stored["plan"] = selected_plan
        stored["selected_candidate_key"] = candidate.get("candidate_key") or ""
        self._invalidate_candidate_poster_enrichment(stored)
        return self._start_selected_release(plan_id, stored)

    async def _apply_selected_relation(
        self,
        candidate: dict,
        selected_plan: dict,
        stored: dict,
    ) -> None:
        del candidate, selected_plan, stored
        # Search handles only root movies and regular series scopes. Related
        # specials are intentionally excluded from this pipeline.
        return

    def _series_scope_action(self, plan_id: str, stored: dict) -> dict:
        contract = stored["plan"]["media_metadata"]
        inventory = series_inventory(contract)
        decision = ((contract.get("evidence") or {}).get("decision") or {})
        scope = str(decision.get("scope") or "movie_or_series")
        seasons = series_seasons(contract)
        digits = ("零", "一", "二", "三", "四", "五", "六", "七", "八", "九")

        def ordinal(number: int, unit: str) -> str:
            if 0 < number <= 20:
                if number < 10:
                    text = digits[number]
                elif number == 10:
                    text = "十"
                elif number < 20:
                    text = "十" + digits[number - 10]
                else:
                    text = "二十"
                return f"第{text}{unit}"
            return f"第 {number} {unit}"

        if scope == "season":
            season = int(decision.get("season_number") or 0)
            return self._series_episode_scope_action(
                plan_id,
                stored,
                season,
            )

        keyboard = []
        all_completed = bool(seasons) and all(
            inventory.state_by_season.get(season) == "completed"
            for season in seasons
        )
        if all_completed and len(seasons) == 1:
            keyboard.append([{
                "text": "全剧（共 1 季）",
                "callback_data": f"search:scope:{plan_id}:whole_series",
            }])
        elif all_completed:
            keyboard.append([{
                "text": "全剧",
                "callback_data": f"search:scope:{plan_id}:whole_series",
            }])
            for season in seasons:
                keyboard.append([{
                    "text": ordinal(season, "季"),
                    "callback_data": f"search:scope:{plan_id}:season:{season}",
                }])
        else:
            for season in seasons:
                state = inventory.state_by_season.get(season, "unknown")
                aired_count = len(
                    inventory.aired_by_season.get(season, ())
                )
                total = inventory.season_totals.get(season)
                if state == "completed":
                    label = f"{ordinal(season, '季')}（全季）"
                elif total:
                    label = (
                        f"{ordinal(season, '季')}"
                        f"（已播 {aired_count}/{total}）"
                    )
                else:
                    label = (
                        f"{ordinal(season, '季')}"
                        f"（已播 {aired_count} 集）"
                    )
                keyboard.append([{
                    "text": label,
                    "callback_data": f"search:scope:{plan_id}:season:{season}",
                }])
        keyboard.append([{
            "text": "返回",
            "callback_data": f"search:cancel:{plan_id}",
        }])
        if all_completed:
            text = (
                f"已确认剧集，共 {len(seasons)} 季。"
                "请选择本次下载范围。"
            )
        else:
            text = (
                f"已确认剧集，共 {len(seasons)} 季；"
                "存在尚未播完或日期未确认的季度，已隐藏全剧搜索。"
            )
        inventory_evidence = (
            (contract.get("evidence") or {}).get("series_inventory") or {}
        )
        log_search_event(
            runtime_context.logger,
            "search.series_scope_inventory",
            search_session_id=plan_id,
            inventory_source=inventory_evidence.get("source"),
            wikipedia_status=inventory_evidence.get("status"),
            source_revisions=inventory_evidence.get("source_revisions"),
            season_states=inventory.state_by_season,
            aired_counts={
                season: len(inventory.aired_by_season.get(season, ()))
                for season in seasons
            },
            season_totals=inventory.season_totals,
            fallback_reason=inventory_evidence.get("fallback_reason"),
            hidden_whole_series_reason=(
                "incomplete_or_unknown_season"
                if seasons and not all_completed
                else ""
            ),
            hidden_scheduled_counts={
                season: len(inventory.scheduled_by_season.get(season, ()))
                for season in seasons
            },
            hidden_unknown_counts={
                season: len(inventory.unknown_by_season.get(season, ()))
                for season in seasons
            },
        )
        action = {
            "kind": "edit_message",
            "text": text,
            "data": {"keyboard": keyboard},
        }
        operation = self._advance_operation(
            stored["operation_id"],
            state="awaiting_input",
            stage="series_scope",
            status_text=text,
            control="exit",
            details=deepcopy(action["data"]),
        )
        return {"actions": [action], "operation": operation}

    def _series_episode_scope_action(
        self,
        plan_id: str,
        stored: dict,
        season: int,
    ) -> dict:
        contract = stored["plan"]["media_metadata"]
        inventory = series_inventory(contract)
        state = inventory.state_by_season.get(season, "unknown")
        digits = ("零", "一", "二", "三", "四", "五", "六", "七", "八", "九")

        def ordinal(number: int, unit: str) -> str:
            if 0 < number <= 20:
                if number < 10:
                    text = digits[number]
                elif number == 10:
                    text = "十"
                elif number < 20:
                    text = "十" + digits[number - 10]
                else:
                    text = "二十"
                return f"第{text}{unit}"
            return f"第 {number} {unit}"

        keyboard = []
        if state == "completed":
            keyboard.append([{
                "text": f"{ordinal(season, '季')} 全季",
                "callback_data": f"search:scope:{plan_id}:season:{season}",
            }])
            episodes = inventory.all_by_season.get(season, ())
        else:
            episodes = inventory.aired_by_season.get(season, ())
        for episode in episodes:
            keyboard.append([{
                "text": ordinal(episode, "集"),
                "callback_data": (
                    f"search:scope:{plan_id}:episode:{season}:{episode}"
                ),
            }])
        keyboard.append([{
            "text": "返回",
            "callback_data": f"search:cancel:{plan_id}",
        }])
        aired_count = len(inventory.aired_by_season.get(season, ()))
        total = inventory.season_totals.get(season)
        if state == "completed":
            text = (
                f"已确认第 {season} 季：共 {total or len(episodes)} 集，"
                "请选择下载范围。"
            )
        elif total:
            text = (
                f"第 {season} 季尚未播完：已播 {aired_count}/{total}。"
                "请选择已播单集。"
            )
        else:
            text = (
                f"第 {season} 季尚未确认完整集数：已播 {aired_count} 集。"
                "请选择已播单集。"
            )
        log_search_event(
            runtime_context.logger,
            "search.series_episode_menu",
            search_session_id=plan_id,
            season_number=season,
            season_state=state,
            aired_count=aired_count,
            season_total=total,
            hidden_scheduled_count=len(
                inventory.scheduled_by_season.get(season, ())
            ),
            hidden_unknown_count=len(
                inventory.unknown_by_season.get(season, ())
            ),
        )
        action = {
            "kind": "edit_message",
            "text": text,
            "data": {"keyboard": keyboard},
        }
        operation = self._advance_operation(
            stored["operation_id"],
            state="awaiting_input",
            stage="series_scope",
            status_text=text,
            control="exit",
            details=deepcopy(action["data"]),
        )
        return {"actions": [action], "operation": operation}

    def _related_placement_action(self, plan_id: str, stored: dict) -> dict:
        contract = stored["plan"]["media_metadata"]
        relation = contract.get("relation") or {}
        target = relation.get("target_series") or {}
        target_title = (
            target.get("chinese_title")
            or target.get("english_title")
            or "目标剧集"
        )
        text = (
            f"已验证该电影与《{target_title}》存在"
            f"{relation.get('type') or '关联'}关系。请选择本次整理方式；"
            "无论如何，资源搜索都按电影标题和年份检索。"
        )
        data = {"keyboard": [
            [{
                "text": f"归入《{target_title}》Specials（推荐）",
                "callback_data": f"search:placement:{plan_id}:special",
            }],
            [{
                "text": "按独立电影整理",
                "callback_data": f"search:placement:{plan_id}:standalone",
            }],
            [{
                "text": "退出",
                "callback_data": f"search:cancel:{plan_id}",
            }],
        ]}
        action = {"kind": "edit_message", "text": text, "data": data}
        operation = self._advance_operation(
            stored["operation_id"],
            state="awaiting_input",
            stage="related_movie_placement",
            status_text=text,
            control="exit",
            details=deepcopy(data),
        )
        return {"actions": [action], "operation": operation}

    def _placement_callback(
        self,
        plan_id: str,
        stored: dict,
        choice: str,
    ) -> dict:
        plan = deepcopy(stored["plan"])
        contract = plan["media_metadata"]
        placement = contract.get("placement") or {}
        if choice == "standalone":
            prefix = (
                "animated"
                if str(placement.get("category_kind") or "").startswith("animated_")
                else "live_action"
            )
            placement.update({
                "library_type": "movie",
                "category_kind": f"{prefix}_movie",
                "season_number": None,
                "episode_number": None,
                "mapping_kind": "standalone",
                "mapping_source": "user_selected_standalone",
                "tvdb_episode_id": "",
            })
            contract["items"] = []
        elif choice == "special":
            official = (
                (contract.get("evidence") or {}).get(
                    "tvdb_official_special_candidates"
                )
                or []
            )
            if len(official) == 1:
                selected = official[0]
                placement.update({
                    "season_number": 0,
                    "episode_number": int(selected.get("episode_number") or 0),
                    "mapping_kind": "tvdb_official",
                    "mapping_source": "tvdb_official",
                    "tvdb_episode_id": str(selected.get("episode_id") or ""),
                })
                contract["items"] = [{
                    "item_id": str(selected.get("episode_id") or ""),
                    "content_role": "special",
                    "season_number": 0,
                    "episode_number": int(selected.get("episode_number") or 0),
                }]
            else:
                try:
                    plan = finalize_search_plan(
                        plan,
                        self.allocator,
                        set(
                            (contract.get("evidence") or {}).get(
                                "occupied_special_numbers"
                            )
                            or []
                        ),
                    )
                except ValueError:
                    return self._closed("❌ 无法为本次任务分配临时 Special 编号。")
        else:
            raise FeatureError(
                "invalid_callback", "related movie placement is invalid"
            )
        stored["plan"] = plan
        return self._start_selected_release(plan_id, stored)

    def _scope_callback(
        self,
        plan_id: str,
        stored: dict,
        choice: str,
        request: dict,
        *coordinates: str,
    ) -> dict:
        contract = stored["plan"]["media_metadata"]
        decision = ((contract.get("evidence") or {}).get("decision") or {})
        if choice == "whole_series":
            stored["plan"]["media_metadata"] = apply_series_scope(
                contract, "whole_series"
            )
            return self._start_selected_release(plan_id, stored)
        if choice == "season" and len(coordinates) == 1:
            season = int(coordinates[0])
            inventory = series_inventory(contract)
            if inventory.state_by_season.get(season) == "completed":
                stored["plan"]["media_metadata"] = apply_series_scope(
                    contract,
                    "season",
                    season_number=season,
                )
                return self._start_selected_release(plan_id, stored)
            return self._series_episode_scope_action(
                plan_id,
                stored,
                season,
            )
        if choice == "episode" and len(coordinates) == 2:
            stored["plan"]["media_metadata"] = apply_series_scope(
                contract,
                "episode",
                season_number=int(coordinates[0]),
                episode_number=int(coordinates[1]),
            )
            return self._start_selected_release(plan_id, stored)
        if choice == "season_all":
            stored["plan"]["media_metadata"] = apply_series_scope(
                contract,
                "season",
                season_number=decision.get("season_number"),
            )
            return self._start_selected_release(plan_id, stored)
        if choice == "season":
            return self._scope_input_action(
                plan_id, stored, request, phase="season"
            )
        if choice == "season_episode":
            return self._scope_input_action(
                plan_id,
                stored,
                request,
                phase="episode",
                season_number=decision.get("season_number"),
            )
        if choice == "episode":
            seasons = series_inventory(contract).seasons
            if len(seasons) == 1:
                return self._scope_input_action(
                    plan_id,
                    stored,
                    request,
                    phase="episode",
                    season_number=seasons[0],
                )
            return self._scope_input_action(
                plan_id, stored, request, phase="episode_season"
            )
        raise FeatureError("invalid_callback", "series scope choice is invalid")

    def _scope_input_action(
        self,
        plan_id: str,
        stored: dict,
        request: dict,
        *,
        phase: str,
        season_number=None,
    ) -> dict:
        owner = self._owner_key(request)
        self.awaiting_scope_inputs[owner] = {
            "plan_id": plan_id,
            "phase": phase,
            "season_number": season_number,
        }
        text = {
            "season": "请输入季号，例如：2",
            "episode_season": "请先输入季号，例如：2",
            "episode": f"请输入第 {season_number} 季的集号，例如：3",
        }[phase]
        action = {
            "kind": "edit_message",
            "text": text,
            "data": {"keyboard": [[{
                "text": "退出",
                "callback_data": f"search:cancel:{plan_id}",
            }]]},
        }
        operation = self._advance_operation(
            stored["operation_id"],
            state="awaiting_input",
            stage="series_scope_number",
            status_text=text,
            control="exit",
            details=deepcopy(action["data"]),
        )
        return {
            "actions": [action],
            "session": {"state": "open"},
            "operation": operation,
        }

    def _handle_scope_input(self, request: dict, owner) -> dict:
        pending = self.awaiting_scope_inputs.get(owner) or {}
        plan_id = str(pending.get("plan_id") or "")
        stored = self.plans.get(plan_id)
        if not stored:
            self.awaiting_scope_inputs.pop(owner, None)
            return self._closed("⚠️ 搜索任务已过期，请重新搜索。")
        raw = " ".join(str(request.get("text") or "").split())
        if not raw.isdigit() or int(raw) < 1:
            return {
                "actions": [{"kind": "send_message", "text": "请输入大于 0 的数字。"}],
                "session": {"state": "open"},
            }
        number = int(raw)
        contract = stored["plan"]["media_metadata"]
        inventory = series_inventory(contract)
        phase = pending.get("phase")
        if phase in {"season", "episode_season"}:
            if number not in inventory.seasons:
                return {
                    "actions": [{"kind": "send_message", "text": "该季不存在，请重新输入。"}],
                    "session": {"state": "open"},
                }
            if phase == "episode_season":
                return self._scope_input_action(
                    plan_id,
                    stored,
                    request,
                    phase="episode",
                    season_number=number,
                )
            if inventory.state_by_season.get(number) != "completed":
                self.awaiting_scope_inputs.pop(owner, None)
                return self._series_episode_scope_action(
                    plan_id,
                    stored,
                    number,
                )
            self.awaiting_scope_inputs.pop(owner, None)
            stored["plan"]["media_metadata"] = apply_series_scope(
                contract, "season", season_number=number
            )
            return self._start_selected_release(plan_id, stored)
        if phase == "episode":
            try:
                scoped = apply_series_scope(
                    contract,
                    "episode",
                    season_number=pending.get("season_number"),
                    episode_number=number,
                )
            except SeriesScopeError as exc:
                message = (
                    "该集尚未播出，请输入已播集号。"
                    if str(exc) == "episode_not_aired"
                    else "该集不存在，请重新输入。"
                )
                return {
                    "actions": [{"kind": "send_message", "text": message}],
                    "session": {"state": "open"},
                }
            self.awaiting_scope_inputs.pop(owner, None)
            stored["plan"]["media_metadata"] = scoped
            return self._start_selected_release(plan_id, stored)
        raise FeatureError("invalid_state", "series scope input state is invalid")

    def _start_selected_release(self, plan_id: str, stored: dict) -> dict:
        contract = stored["plan"]["media_metadata"]
        route = resolve_category_route(
            self.config,
            (contract.get("placement") or {}).get("category_kind"),
        )
        if not route:
            self._release_plan(plan_id)
            return self._closed("❌ 媒体分类没有对应保存目录。")
        stored["selected_path"] = route["path"]
        stored["identity_presentation"] = build_identity_presentation(
            contract
        )
        return self._start_release_search_task(plan_id, stored)

    def _start_deferred_presentation_enrichment(
        self,
        plan_id: str,
        stored: dict,
    ) -> None:
        current = stored.get("deferred_enrichment_task")
        if current is not None and not current.done():
            return
        stored.pop("deferred_contract", None)
        confirmed_contract = deepcopy(stored["confirmed_contract"])
        candidate = deepcopy(stored.get("selected_candidate") or {
            "media_metadata": confirmed_contract,
        })
        raw_query = str((stored.get("plan") or {}).get("raw_query") or "")

        async def enrich() -> None:
            try:
                enriched_candidate = await self._supplement_selected_candidate(
                    candidate,
                    raw_query,
                    purpose="presentation",
                )
                poster_store = {
                    "candidates": (deepcopy(enriched_candidate),),
                }
                await self._supplement_candidate_posters(poster_store)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if runtime_context.logger:
                    runtime_context.logger.warning(
                        "search_deferred_enrichment status=failed "
                        f"error={type(exc).__name__}"
                    )
                return
            if self.plans.get(plan_id) is not stored:
                return
            deferred = apply_deferred_presentation(
                confirmed_contract,
                enriched_candidate,
            )
            poster_candidates = poster_store.get("candidates") or ()
            if poster_candidates:
                deferred = apply_deferred_presentation(
                    deferred,
                    poster_candidates[0],
                )
            stored["deferred_contract"] = deferred

        stored["deferred_enrichment_task"] = asyncio.create_task(enrich())

    async def _confirm_and_search(self, plan_id: str, stored: dict) -> dict:
        self._measurement_session_id.set(plan_id)
        plan = stored["plan"]
        contract = confirm_media_metadata(plan)
        presentation = build_identity_presentation(contract)
        stored["identity_presentation"] = deepcopy(presentation)
        if (
            stored.get("identity_milestone_id")
            != presentation["milestone_id"]
        ):
            if stored["operation_id"] in self.operations:
                await self._report_operation(
                    stored["operation_id"],
                    state="running",
                    stage="identity_confirmation",
                    status_text=(
                        "正在确认媒体身份："
                        f"{_text(presentation.get('title')) or '未知作品'}"
                    ),
                    control="cancel",
                    details={},
                )
            for attempt in range(3):
                try:
                    response = await self.host.publish_operation_milestone(
                        stored["operation_id"],
                        presentation["milestone_id"],
                        presentation["text"],
                        photo_url=presentation["photo_url"],
                        deadline=45,
                    )
                except Exception as exc:
                    if (
                        _ambiguous_milestone_error(exc)
                        and attempt < 2
                    ):
                        await asyncio.sleep(0.25 * (2 ** attempt))
                        continue
                    if runtime_context.logger:
                        runtime_context.logger.warning(
                            "search_identity_milestone "
                            "status=failed "
                            f"error_code={getattr(exc, 'code', type(exc).__name__)} "
                            f"error_type={type(exc).__name__}"
                        )
                    raise FeatureError(
                        "identity_delivery_failed",
                        "Host did not deliver the confirmed media identity",
                    ) from exc
                delivered = bool(
                    isinstance(response, dict)
                    and (
                        response.get("accepted") is True
                        or response.get("duplicate") is True
                    )
                )
                if not delivered:
                    raise FeatureError(
                        "identity_delivery_failed",
                        "Host did not deliver the confirmed media identity",
                    )
                stored["identity_milestone_id"] = (
                    presentation["milestone_id"]
                )
                break
        if stored["operation_id"] in self.operations:
            await self._report_operation(
                stored["operation_id"],
                state="running",
                stage="prowlarr_search",
                status_text="已确认身份，开始搜索",
                control="cancel",
                details=(self._prowlarr_status_details(
                    stored["operation_id"]
                ) | {"telegram_visibility": "silent"}),
            )
        evidence = contract.get("evidence") or {}
        if isinstance(evidence.get("source_links"), list):
            queries = build_prowlarr_query_chain(
                contract,
                str(plan.get("raw_query") or ""),
            )
        else:
            queries = self._english_prowlarr_queries(plan, contract)
        media_type = str(
            (contract.get("retrieval") or {}).get("media_type")
            or (contract.get("placement") or {}).get("library_type")
            or ""
        )
        log_search_event(
            runtime_context.logger,
            "search.prowlarr_query_built",
            search_session_id=plan_id,
            queries=queries,
            media_type=media_type,
            scope=(contract.get("retrieval") or {}).get("scope"),
            title_policy=(contract.get("identity") or {}).get(
                "search_title_policy"
            ),
            year=(contract.get("identity") or {}).get("year"),
        )
        stored["confirmed_contract"] = contract
        stored["active_prowlarr_queries"] = list(queries)
        try:
            indexers = await asyncio.to_thread(self.indexer_loader)
        except Exception as exc:
            indexers = []
            indexer_list_error = f"{type(exc).__name__}: {exc}"
        else:
            indexer_list_error = ""
        if indexers:
            return await self._confirm_and_search_indexers(
                plan_id,
                stored,
                queries,
                media_type,
                contract,
                indexers,
            )
        return await self._confirm_and_search_aggregate(
            plan_id,
            stored,
            queries,
            media_type,
            contract,
            indexer_list_error=indexer_list_error,
        )

    async def _confirm_and_search_aggregate(
        self,
        plan_id: str,
        stored: dict,
        queries: list[str],
        media_type: str,
        contract: dict,
        *,
        indexer_list_error: str = "",
    ) -> dict:
        tasks = [
            asyncio.create_task(
                asyncio.to_thread(
                    self.release_search,
                    query,
                    media_type,
                )
            )
            for query in queries
        ]
        stored["indexer_tasks"] = list(tasks)
        self._start_deferred_presentation_enrichment(plan_id, stored)
        try:
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            stored["indexer_tasks"] = []
        raw_items = []
        errors = {}
        successful_queries = 0
        for query, outcome in zip(queries, outcomes):
            if isinstance(outcome, BaseException):
                error = self._prowlarr_error(outcome)
                errors[query] = error
                if runtime_context.logger:
                    runtime_context.logger.warning(
                        "prowlarr_search_failed "
                        f"query={query!r} media_type={media_type} "
                        f"error={json.dumps(error, ensure_ascii=False)}"
                    )
                continue
            successful_queries += 1
            batch = outcome if isinstance(outcome, list) else []
            raw_items.extend(
                item for item in batch if isinstance(item, dict)
            )
            if runtime_context.logger:
                runtime_context.logger.info(
                    "prowlarr_search_variant "
                    f"query={query!r} media_type={media_type} "
                    f"raw={len(batch)}"
                )
        if not successful_queries:
            error = self._all_query_variants_error(queries, errors)
            return {
                "actions": [{
                    "kind": "edit_message",
                    "text": (
                        "❌ 资源搜索失败\n"
                        "搜索词："
                        + " / ".join(queries)
                        + "\n"
                        f"{str(error['message']).replace('Prowlarr ', '')}"
                    ),
                    "data": {"keyboard": [[{
                        "text": "重试搜索",
                        "callback_data": f"search:confirm:{plan_id}",
                    }], [{
                        "text": "退出",
                        "callback_data": f"search:cancel:{plan_id}",
                    }]]},
                }],
                "session": {"state": "close"},
            }
        try:
            indexer_summary = await asyncio.to_thread(
                self.indexer_summary,
                raw_items,
            )
        except Exception as exc:
            indexer_summary = {
                "enabled_indexers": [],
                "result_sources": {},
                "down_indexers": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        if indexer_list_error:
            existing_error = str(indexer_summary.get("error") or "")
            indexer_summary["error"] = "；".join(
                value
                for value in (indexer_list_error, existing_error)
                if value
            )
        indexer_summary["final"] = True
        return self._finalize_release_results(
            plan_id,
            stored,
            " | ".join(queries),
            contract,
            raw_items,
            indexer_summary,
        )

    @staticmethod
    def _prowlarr_error(exc: Exception) -> dict:
        if isinstance(exc, ProwlarrRequestError):
            return exc.as_dict()
        return {
            "kind": "unexpected_error",
            "http_status": 0,
            "message": f"{type(exc).__name__}: {exc}",
            "retryable": False,
        }

    @staticmethod
    def _all_query_variants_error(
        queries: list[str],
        errors: dict[str, dict],
    ) -> dict:
        ordered = [
            errors[query]
            for query in queries
            if query in errors
        ]
        if len(queries) == 1 and ordered:
            return dict(ordered[0])
        first = ordered[0] if ordered else {
            "kind": "unexpected_error",
            "http_status": 0,
            "message": "unknown error",
            "retryable": False,
        }
        messages = [
            str(errors[query].get("message") or "unknown error")
            for query in queries
            if query in errors
        ]
        return {
            "kind": str(first.get("kind") or "unexpected_error"),
            "http_status": int(first.get("http_status") or 0),
            "message": "all query variants failed: " + "; ".join(messages),
            "retryable": bool(ordered) and all(
                bool(error.get("retryable")) for error in ordered
            ),
        }

    def _release_limit(self) -> int:
        try:
            configured_limit = int(
                (((self.config.get("search") or {}).get("prowlarr") or {})
                 .get("result_limit") or 12)
            )
        except (TypeError, ValueError):
            configured_limit = 12
        return min(12, max(1, configured_limit))

    def _global_prowlarr_timeout(self) -> float:
        value = (
            ((self.config.get("search") or {}).get("prowlarr") or {})
            .get("timeout", 200)
        )
        try:
            return max(1, float(value))
        except (TypeError, ValueError):
            return 200

    def _first_wave_indexer_ids(self) -> list[int]:
        raw = (
            ((self.config.get("search") or {}).get("prowlarr") or {})
            .get("first_wave_indexer_ids", [])
        )
        if not isinstance(raw, (list, tuple)):
            return []
        result = []
        for value in raw:
            if isinstance(value, bool):
                continue
            if isinstance(value, float) and not value.is_integer():
                continue
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            if value > 0 and value not in result:
                result.append(value)
        return result

    def _prowlarr_wave_delay(self) -> float:
        raw = (
            ((self.config.get("search") or {}).get("prowlarr") or {})
            .get("wave_delay", 1.5)
        )
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 1.5
        if not math.isfinite(value):
            return 1.5
        return min(30.0, max(0.0, value))

    def _prowlarr_indexer_scores(self) -> dict:
        scores = (
            ((self.config.get("search") or {}).get("scoring") or {})
            .get("indexer_scores", {})
        )
        return dict(scores) if isinstance(scores, dict) else {}

    def _update_release_results(
        self,
        stored: dict,
        raw_items,
        contract: dict,
    ):
        deduplicated = deduplicate_releases(raw_items)
        gate = gate_releases(deduplicated, contract)
        results = self.release_rank(
            list(gate.eligible),
            self._release_limit(),
        )
        if runtime_context.logger:
            log_search_event(
                runtime_context.logger,
                "search.release_gate_evaluated",
                search_session_id=str(
                    contract.get("metadata_id") or ""
                ),
                queries=stored.get("active_prowlarr_queries") or [],
                media_type=(contract.get("retrieval") or {}).get(
                    "media_type"
                ),
                scope=(contract.get("retrieval") or {}).get("scope"),
                raw_count=len(raw_items or []),
                deduplicated_count=len(deduplicated),
                eligible_count=len(gate.eligible),
                rejections=gate.rejection_counts,
            )
        release_by_id = stored.setdefault("release_by_id", {})
        for item in results:
            release_by_id[stable_release_id(item)] = item
        stored["results"] = results
        stored["gate_report"] = gate
        return gate, results

    def _finalize_release_results(
        self,
        plan_id: str,
        stored: dict,
        query: str,
        contract: dict,
        raw_items,
        indexer_summary: dict,
    ) -> dict:
        gate, results = self._update_release_results(
            stored,
            raw_items,
            contract,
        )
        text = format_release_report(
            _text(
                (stored.get("identity_presentation") or {}).get("title")
            ) or query,
            gate,
            results,
            indexer_summary,
            search_queries=stored.get("active_prowlarr_queries") or [],
        )
        if not results:
            self._log_completed_once(
                plan_id,
                stored,
                terminal_status="no_match",
                release_result_count=0,
            )
            self._release_plan(plan_id)
            return self._closed(text)
        stored["indexer_summary"] = indexer_summary
        keyboard = release_keyboard(plan_id, results)
        self._log_completed_once(
            plan_id,
            stored,
            terminal_status="success",
            release_result_count=len(results),
        )
        return {
            "actions": [{
                "kind": "edit_message",
                "text": text,
                "data": {"keyboard": keyboard},
            }]
        }

    async def _confirm_and_search_indexers(
        self,
        plan_id: str,
        stored: dict,
        queries: list[str],
        media_type: str,
        contract: dict,
        indexers,
    ) -> dict:
        first_wave, remaining_wave = plan_prowlarr_waves(
            indexers,
            explicit_ids=self._first_wave_indexer_ids(),
            indexer_scores=self._prowlarr_indexer_scores(),
        )
        normalized_indexers = [*first_wave, *remaining_wave]
        if not normalized_indexers:
            return await self._confirm_and_search_aggregate(
                plan_id,
                stored,
                queries,
                media_type,
                contract,
                indexer_list_error=(
                    "Prowlarr enabled indexer list contained no valid IDs"
                ),
            )
        enabled_names = [item["name"] for item in normalized_indexers]
        wave_by_indexer_id = {
            item["id"]: "first" for item in first_wave
        } | {
            item["id"]: "tail" for item in remaining_wave
        }
        wave_indexer_ids = {
            "first": {item["id"] for item in first_wave},
            "tail": {item["id"] for item in remaining_wave},
        }
        completed_wave_indexer_ids = {"first": set(), "tail": set()}
        raw_items = []
        down_indexers = []
        completed = 0
        started = time.monotonic()
        timeout = self._global_prowlarr_timeout()
        task_count = len(normalized_indexers) * len(queries)
        semaphore = asyncio.Semaphore(min(12, max(1, task_count)))

        async def search_variant(item: dict, query: str):
            async with semaphore:
                return await asyncio.to_thread(
                    self.indexer_search,
                    query,
                    media_type,
                    item["id"],
                )

        tasks = {}
        pending = set()
        states = {
            item["id"]: {
                "item": item,
                "remaining": len(queries),
                "completed_queries": set(),
                "successful_queries": 0,
                "errors": {},
                "finalized": False,
            }
            for item in normalized_indexers
        }

        def finalize_indexer(state: dict) -> None:
            nonlocal completed
            if state["finalized"] or state["remaining"] > 0:
                return
            state["finalized"] = True
            completed += 1
            wave = wave_by_indexer_id[state["item"]["id"]]
            completed_wave_indexer_ids[wave].add(state["item"]["id"])
            if (
                wave_indexer_ids[wave]
                and completed_wave_indexer_ids[wave]
                == wave_indexer_ids[wave]
            ):
                self._log_measurement(
                    "search.prowlarr.wave.completed",
                    search_session_id=plan_id,
                    duration_ms=max(
                        0,
                        round((time.monotonic() - started) * 1000),
                    ),
                    wave=wave,
                    indexer_count=len(wave_indexer_ids[wave]),
                    query_count=len(queries),
                    raw_count=len(raw_items),
                    status_class="completed",
                )
            if state["successful_queries"]:
                return
            error = self._all_query_variants_error(
                queries,
                state["errors"],
            )
            down_indexers.append({
                "source": state["item"]["name"],
                **error,
            })

        stored["indexer_tasks"] = []

        def launch_wave(wave) -> None:
            for item in wave:
                for query in queries:
                    task = asyncio.create_task(
                        search_variant(item, query)
                    )
                    tasks[task] = (item, query)
                    pending.add(task)
                    stored["indexer_tasks"].append(task)

        launch_wave(first_wave)
        self._start_deferred_presentation_enrichment(plan_id, stored)
        stored["selection_frozen"] = False
        stored["last_incremental_report"] = 0.0
        remaining_launched = not bool(remaining_wave)
        wave_timer = (
            asyncio.create_task(
                asyncio.sleep(max(
                    0.0,
                    self._prowlarr_wave_delay()
                    - (time.monotonic() - started),
                ))
            )
            if remaining_wave
            else None
        )
        stored["wave_launcher_task"] = wave_timer
        incremental_report_task = None
        incremental_report_dirty = False
        stored["incremental_report_task"] = None
        first_wave_ids = {item["id"] for item in first_wave}
        global_timed_out = False

        async def cancel_wave_timer() -> None:
            nonlocal wave_timer
            if wave_timer is None:
                stored["wave_launcher_task"] = None
                return
            if not wave_timer.done():
                wave_timer.cancel()
            await asyncio.gather(wave_timer, return_exceptions=True)
            wave_timer = None
            stored["wave_launcher_task"] = None

        async def settle_incremental_report(
            *,
            cancel: bool = False,
        ) -> None:
            nonlocal incremental_report_task
            task = incremental_report_task
            if task is None:
                stored["incremental_report_task"] = None
                return
            actively_cancelled = cancel and not task.done()
            if actively_cancelled:
                task.cancel()
            outcome = await asyncio.gather(task, return_exceptions=True)
            incremental_report_task = None
            stored["incremental_report_task"] = None
            error = outcome[0] if outcome else None
            if not isinstance(error, BaseException):
                return
            if (
                actively_cancelled
                and isinstance(error, asyncio.CancelledError)
            ):
                return
            raise error

        def schedule_incremental_report(
            gate,
            results,
            summary,
        ) -> None:
            nonlocal incremental_report_dirty, incremental_report_task
            if (
                incremental_report_task is not None
                or not incremental_report_dirty
            ):
                return
            incremental_report_dirty = False
            incremental_report_task = asyncio.create_task(
                self._report_incremental_releases(
                    plan_id,
                    stored,
                    " | ".join(queries),
                    deepcopy(gate),
                    deepcopy(results),
                    deepcopy(summary),
                )
            )
            stored["incremental_report_task"] = incremental_report_task

        def launch_remaining() -> None:
            nonlocal remaining_launched
            if remaining_launched:
                return
            launch_wave(remaining_wave)
            remaining_launched = True

        try:
            while (
                (pending or wave_timer is not None)
                and not stored.get("selection_frozen")
            ):
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    done = set()
                else:
                    waiters = set(pending)
                    if wave_timer is not None:
                        waiters.add(wave_timer)
                    if incremental_report_task is not None:
                        waiters.add(incremental_report_task)
                    done, _still_waiting = await asyncio.wait(
                        waiters,
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                if (
                    incremental_report_task is not None
                    and incremental_report_task.done()
                ):
                    await settle_incremental_report()
                if not done:
                    global_timed_out = True
                    timeout_error = {
                        "message": (
                            "超过 Prowlarr 全局搜索超时"
                            f"（{int(timeout)} 秒）"
                        ),
                        "kind": "timeout",
                        "http_status": 0,
                        "retryable": True,
                    }
                    for task in pending:
                        task.cancel()
                    for state in states.values():
                        for query in queries:
                            if query in state["completed_queries"]:
                                continue
                            state["completed_queries"].add(query)
                            state["errors"][query] = dict(timeout_error)
                        state["remaining"] = 0
                        finalize_indexer(state)
                    await cancel_wave_timer()
                    break
                if stored.get("selection_frozen"):
                    break
                timer_fired = bool(
                    wave_timer is not None and wave_timer in done
                )
                if timer_fired:
                    completed_timer = wave_timer
                    wave_timer = None
                    stored["wave_launcher_task"] = None
                    await asyncio.gather(
                        completed_timer,
                        return_exceptions=True,
                    )
                done_tasks = [task for task in done if task in tasks]
                pending.difference_update(done_tasks)
                if done_tasks:
                    incremental_report_dirty = True
                for task in done_tasks:
                    item, query = tasks[task]
                    state = states[item["id"]]
                    state["remaining"] -= 1
                    state["completed_queries"].add(query)
                    try:
                        batch = task.result()
                    except asyncio.CancelledError:
                        if stored.get("selection_frozen"):
                            continue
                        raise
                    except Exception as exc:
                        error = self._prowlarr_error(exc)
                        state["errors"][query] = error
                        if runtime_context.logger:
                            runtime_context.logger.warning(
                                "prowlarr_indexer_search_failed "
                                f"query={query!r} media_type={media_type} "
                                f"indexer={item['name']!r} "
                                f"error={json.dumps(error, ensure_ascii=False)}"
                            )
                        finalize_indexer(state)
                        continue
                    state["successful_queries"] += 1
                    batch = batch if isinstance(batch, list) else []
                    if runtime_context.logger:
                        runtime_context.logger.info(
                            "prowlarr_indexer_search "
                            f"query={query!r} media_type={media_type} "
                            f"indexer={item['name']!r} raw={len(batch)}"
                        )
                    for raw_item in batch or []:
                        if not isinstance(raw_item, dict):
                            continue
                        normalized = dict(raw_item)
                        normalized["indexer"] = (
                            str(normalized.get("indexer") or "").strip()
                            or item["name"]
                        )
                        raw_items.append(normalized)
                    finalize_indexer(state)
                gate, results = self._update_release_results(
                    stored,
                    raw_items,
                    contract,
                )
                first_wave_complete = bool(first_wave_ids) and all(
                    states[indexer_id]["finalized"]
                    for indexer_id in first_wave_ids
                )
                if not remaining_launched and (
                    timer_fired
                    or (first_wave_complete and not results)
                ):
                    if not timer_fired:
                        await cancel_wave_timer()
                    launch_remaining()
                search_incomplete = bool(
                    pending
                    or (remaining_wave and not remaining_launched)
                )
                summary = self._incremental_indexer_summary(
                    enabled_names,
                    raw_items,
                    down_indexers,
                    completed,
                    final=not search_incomplete,
                )
                stored["indexer_summary"] = summary
                if results and search_incomplete:
                    schedule_incremental_report(gate, results, summary)
            await settle_incremental_report(
                cancel=bool(
                    stored.get("selection_frozen")
                    or global_timed_out
                ),
            )
            last_report = float(stored.get("last_incremental_report") or 0)
            if last_report:
                delay = 1.25 - (time.monotonic() - last_report)
                if delay > 0:
                    await asyncio.sleep(delay)
        finally:
            await cancel_wave_timer()
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            stored["indexer_tasks"] = []
            await settle_incremental_report(cancel=True)

        summary = self._incremental_indexer_summary(
            enabled_names,
            raw_items,
            down_indexers,
            len(normalized_indexers),
            final=True,
        )
        stored["indexer_summary"] = summary
        return self._finalize_release_results(
            plan_id,
            stored,
            " | ".join(queries),
            contract,
            raw_items,
            summary,
        )

    @staticmethod
    def _incremental_indexer_summary(
        enabled_names,
        raw_items,
        down_indexers,
        completed,
        *,
        final,
    ) -> dict:
        result_sources = Counter(
            str(item.get("indexer") or "未知").strip() or "未知"
            for item in raw_items
            if isinstance(item, dict)
        )
        return {
            "enabled_indexers": list(enabled_names),
            "result_sources": dict(result_sources),
            "down_indexers": deepcopy(down_indexers),
            "error": "",
            "completed_indexers": int(completed),
            "total_indexers": len(enabled_names),
            "final": bool(final),
        }

    async def _report_incremental_releases(
        self,
        plan_id,
        stored,
        query,
        gate,
        results,
        indexer_summary,
    ):
        now = time.monotonic()
        last_report = float(stored.get("last_incremental_report") or 0)
        if last_report and now - last_report < 1.25:
            return
        text = format_release_report(
            _text(
                (stored.get("identity_presentation") or {}).get("title")
            ) or query,
            gate,
            results,
            indexer_summary,
            search_queries=stored.get("active_prowlarr_queries") or [],
        )
        await self._report_operation(
            stored["operation_id"],
            state="running",
            stage="prowlarr_search",
            status_text=text,
            control="cancel",
            details={
                "allow_running_callbacks": True,
                "keyboard": release_keyboard(plan_id, results),
            },
        )
        stored["last_incremental_report"] = time.monotonic()

    def _remove_unresolvable_release(
        self,
        plan_id: str,
        stored: dict,
        release_id: str,
        error_kind: str,
    ) -> dict:
        release_by_id = stored.setdefault("release_by_id", {})
        release_by_id.pop(release_id, None)
        remaining = [
            item
            for item in stored.get("results") or []
            if stable_release_id(item) != release_id
        ]
        stored["results"] = remaining
        stored["selection_frozen"] = False
        stored.pop("selected_release_id", None)
        if runtime_context.logger:
            runtime_context.logger.warning(
                "search_release_resolution "
                "status=removed "
                f"plan_id={sanitize_log_value(plan_id, max_chars=80)} "
                f"release_id={sanitize_log_value(release_id, max_chars=80)} "
                f"error_kind={sanitize_log_value(error_kind, max_chars=120)} "
                f"remaining={len(remaining)}"
            )
        if remaining:
            report = format_release_report(
                _text(
                    (stored.get("identity_presentation") or {}).get("title")
                )
                or " | ".join(
                    stored.get("active_prowlarr_queries") or []
                ),
                stored.get("gate_report"),
                remaining,
                stored.get("indexer_summary") or {},
                search_queries=(
                    stored.get("active_prowlarr_queries") or []
                ),
            )
            text = (
                "⚠️ 所选片源的下载内容获取失败，已从结果中移除。"
                "请改选其他片源。\n\n"
                f"{report}"
            )
            keyboard = release_keyboard(plan_id, remaining)
        else:
            text = (
                "❌ 当前搜索结果均无法取得下载内容。"
                "请退出后重新搜索。"
            )
            keyboard = [[{
                "text": "退出",
                "callback_data": f"search:cancel:{plan_id}",
            }]]
        return {
            "actions": [{
                "kind": "edit_message",
                "text": text,
                "data": {"keyboard": keyboard},
            }],
            "release_resolution_recovered": True,
        }

    async def _submit_release(
        self,
        plan_id: str,
        stored: dict,
        release_id: str,
        operation_id: str,
    ) -> dict:
        try:
            item = (stored.get("release_by_id") or {})[release_id]
        except KeyError:
            raise FeatureError("invalid_release", "selected release is invalid") from None
        try:
            link = await asyncio.to_thread(self.release_resolver, item)
        except Exception as exc:
            return self._remove_unresolvable_release(
                plan_id,
                stored,
                release_id,
                type(exc).__name__,
            )
        if not str(link).startswith("magnet:?"):
            return self._remove_unresolvable_release(
                plan_id,
                stored,
                release_id,
                "magnet_missing",
            )
        deferred_task = stored.get("deferred_enrichment_task")
        deferred_contract = stored.get("deferred_contract")
        contract = deepcopy(
            deferred_contract
            if (
                deferred_task is not None
                and deferred_task.done()
                and not deferred_task.cancelled()
                and isinstance(deferred_contract, dict)
            )
            else stored["confirmed_contract"]
        )
        identity = contract["identity"]
        operation = self.operations[operation_id]
        handoff = operation.get("handoff_operation")
        if not isinstance(handoff, dict):
            handoff = self._advance_operation(
                operation_id,
                state="handed_off",
                stage="submitting_download",
                status_text="已提交下载",
                control="cancel",
                next_plugin_id="download",
            )
            operation["handoff_operation"] = deepcopy(handoff)
        try:
            response = await self.host.report_operation(handoff)
        except Exception as exc:
            if _ambiguous_host_report_error(exc):
                operation["handoff_pending"] = True
            raise
        if not isinstance(response, dict) or response.get("accepted") is not True:
            if (
                isinstance(response, dict)
                and response.get("error_code")
                == "handoff_target_unavailable"
                and response.get("target_plugin_id") == "download"
            ):
                operation.pop("handoff_operation", None)
                await self._report_operation(
                    operation_id,
                    state="failed",
                    stage="submitting_download",
                    status_text="115 下载未安装，无法提交片源。",
                    control="",
                )
                raise FeatureError(
                    "handoff_target_unavailable",
                    "download Feature is not active",
                )
            operation.update({
                "state": "interrupted",
                "status_text": "Host 已结束协调任务，未提交 115 下载。",
                "control": "",
                "next_plugin_id": "",
            })
            raise FeatureError(
                "operation_rejected",
                "Host rejected search handoff ownership",
            )
        operation["handoff_pending"] = False
        await self._seal_search_stage(
            operation_id,
            f"search-stage-complete:{plan_id}:{release_id}",
        )
        try:
            result = await self.host.call_capability(
                "download.provider",
                "submit",
                {
                    "link": link,
                    "selected_path": stored["selected_path"],
                    "chat_id": stored["owner"][0],
                    "user_id": stored["owner"][1],
                    "operation_id": operation_id,
                    "operation_revision": handoff["revision"],
                    "media_metadata": contract,
                    "naming_metadata": {
                        "source": "confirmed",
                        "media_type": contract["placement"]["library_type"],
                        "chinese_title": identity.get("chinese_title") or "",
                        "english_title": identity.get("english_title") or "",
                        "year": identity.get("year") or "",
                    },
                    "release": {
                        "title": item.get("title") or "",
                        "indexer": item.get("indexer") or "",
                        "size": item.get("size") or 0,
                    },
                },
                deadline=30,
                idempotency_key=f"{plan_id}:release:{release_id}",
            )
        except Exception as exc:
            await self._report_operation(
                operation_id,
                state="failed",
                stage="submitting_download",
                status_text=f"下载任务提交失败：{type(exc).__name__}",
                control="",
            )
            raise
        self._release_plan(plan_id)
        return {
            "actions": [{
                "kind": "edit_message",
                "text": "已提交下载",
            }]
        }

    async def _seal_search_stage(
        self,
        operation_id: str,
        milestone_id: str,
    ) -> None:
        for attempt in range(3):
            try:
                response = await self.host.seal_operation_stage(
                    operation_id,
                    milestone_id,
                    "已提交下载",
                    deadline=45,
                )
            except Exception as exc:
                if not _ambiguous_milestone_error(exc) or attempt == 2:
                    raise
                await asyncio.sleep(0.25 * (2 ** attempt))
                continue
            if isinstance(response, dict) and (
                response.get("accepted") is True
                or response.get("duplicate") is True
            ):
                return
            raise FeatureError(
                "stage_seal_failed",
                "Host did not seal the completed search stage",
            )

    async def _localize_exact_douban_candidates(
        self,
        plan: dict,
        *,
        plan_id: str,
    ) -> dict:
        if not isinstance(plan, dict):
            return plan
        candidates = [
            deepcopy(value) for value in plan.get("candidates") or ()
            if isinstance(value, dict)
        ]
        if not candidates:
            return plan
        semaphore = asyncio.Semaphore(4)

        async def localize(index: int, candidate: dict):
            identity = (
                (candidate.get("media_metadata") or {}).get("identity") or {}
            )
            scope, season_number, episode_number = self._source_coordinates(
                candidate
            )
            candidate_media_type = _candidate_media_type(candidate)
            subject_id = _text(
                (identity.get("external_ids") or {}).get("douban_subject")
            )
            if subject_id:
                try:
                    async with semaphore:
                        fact = await self._run_source_request(
                            provider="douban",
                            purpose="presentation_locale",
                            media_type=candidate_media_type,
                            identity=f"douban_subject:{subject_id}",
                            scope=scope,
                            season_number=season_number,
                            episode_number=episode_number,
                            fetch=lambda: asyncio.to_thread(
                                lookup_douban_subject,
                                subject_id,
                            ),
                            cacheable=lambda value: (
                                _cacheable_douban_subject(value, subject_id)
                            ),
                        )
                    localized = localize_candidate_from_exact_douban(
                        candidate,
                        fact,
                    )
                    return index, localized, "wikidata_exact"
                except Exception:
                    return index, candidate, "douban_exact_binding_failed"
            if index >= 5:
                return index, candidate, "not_bound"

            english_title = _text(
                identity.get("official_english_title")
                or identity.get("english_title")
                or identity.get("original_title")
            )
            year = _text(identity.get("year"))[:4]
            query = _text(" ".join(filter(None, (english_title, year))))
            media_type = _text(identity.get("content_kind")).casefold()
            if not query or media_type not in {"movie", "series"}:
                return index, candidate, "not_bound"
            confirmed = ConfirmedIdentity(
                provider="candidate_preview",
                stable_id=_text(
                    candidate.get("candidate_id")
                    or candidate.get("candidate_key")
                ),
                chinese_title=_text(identity.get("chinese_title")),
                english_title=english_title,
                original_title=_text(identity.get("original_title")),
                year=year,
                media_type=media_type,
                requested_scope=_text(
                    candidate.get("intended_scope")
                    or (candidate.get("media_metadata") or {})
                    .get("retrieval", {}).get("scope")
                ).casefold(),
                original_language=_text(
                    identity.get("original_language")
                ).casefold(),
                genres=tuple(
                    _text(item)
                    for item in identity.get("genres") or ()
                    if _text(item)
                ),
                external_ids={
                    _text(key): _text(value)
                    for key, value in (
                        identity.get("external_ids") or {}
                    ).items()
                    if _text(key) and _text(value)
                },
                countries=tuple(
                    _text(item)
                    for item in identity.get("countries") or ()
                    if _text(item)
                ),
                cast_names=tuple(
                    _text(
                        item.get("name")
                        if isinstance(item, dict)
                        else item
                    )
                    for item in (
                        list(identity.get("cast") or ())
                        + list(identity.get("crew") or ())
                    )
                    if _text(
                        item.get("name")
                        if isinstance(item, dict)
                        else item
                    )
                ),
            )
            try:
                async with semaphore:
                    result = await self._run_source_request(
                        provider="douban",
                        purpose="presentation_locale",
                        media_type=media_type,
                        identity=f"query:{query}",
                        scope=scope,
                        season_number=season_number,
                        episode_number=episode_number,
                        fetch=lambda: asyncio.to_thread(
                            self._douban_provider,
                            {"source_queries": {"douban": [query]}},
                        ),
                        cacheable=lambda value: _cacheable_douban_raw(
                            value,
                            confirmed,
                        ),
                    )
                fact = select_unique_douban_fact(result, confirmed)
                if not isinstance(fact, dict) or _text(
                    fact.get("douban_match_mode")
                ) != "strong_fields":
                    return index, candidate, "not_bound"
                localized = localize_candidate_from_verified_douban(
                    candidate,
                    fact,
                    match_mode="strong_fields",
                )
                return index, localized, "strong_fields"
            except Exception:
                return index, candidate, "douban_strong_fields_failed"

        localized = await asyncio.gather(*(
            localize(index, candidate)
            for index, candidate in enumerate(candidates)
        ))
        for index, candidate, match_mode in localized:
            candidates[index] = candidate
            if match_mode != "not_bound":
                log_search_event(
                    runtime_context.logger,
                    "search.candidate_localized",
                    search_session_id=plan_id,
                    candidate_id=(
                        candidate.get("candidate_id")
                        or candidate.get("candidate_key")
                    ),
                    provider="douban",
                    match_mode=match_mode,
                )
        result = deepcopy(plan)
        result["candidates"] = candidates
        if candidates:
            result["media_metadata"] = deepcopy(
                candidates[0].get("media_metadata") or {}
            )
            result["prowlarr_queries"] = list(
                candidates[0].get("prowlarr_queries") or ()
            )
        summary = result.get("discovery_summary") or {}
        if summary:
            log_search_event(
                runtime_context.logger,
                "search.discovery_graph_completed",
                search_session_id=plan_id,
                **summary,
            )
        return result

    async def _build_plan(
        self,
        raw_query: str,
        plan_id: str,
        *,
        locked_identity: tuple[str, str] | None = None,
    ):
        del locked_identity
        parsed = classify_search_input(raw_query)
        if parsed.kind in {"invalid_link", "unsupported_text"}:
            raise SearchPlanningError(parsed.reason)
        if parsed.kind in {"link", "resolvable_link"}:
            try:
                stable_link, fallback_title = await asyncio.to_thread(
                    resolve_shared_metadata_link,
                    parsed,
                )
                if stable_link is None:
                    if not fallback_title:
                        raise DirectLinkError("direct_link_invalid")
                    log_search_event(
                        runtime_context.logger,
                        "search.link_downgraded",
                        search_session_id=plan_id,
                        fallback_title=fallback_title,
                    )
                    plan = await asyncio.to_thread(
                        build_root_work_search_plan,
                        fallback_title,
                        plan_id,
                        self._wikipedia_provider,
                        enrich_wikidata_entities,
                        search_wikidata_entities,
                    )
                    return await self._localize_exact_douban_candidates(
                        plan,
                        plan_id=plan_id,
                    )
                direct = await asyncio.to_thread(
                    resolve_direct_link,
                    stable_link,
                )
                log_search_event(
                    runtime_context.logger,
                    "search.link_resolved",
                    search_session_id=plan_id,
                    provider=direct.provider,
                    stable_identity=list(direct.stable_identity),
                    media_type=direct.media_type,
                    scope=direct.scope,
                )
            except DirectLinkError as exc:
                if (
                    getattr(exc, "code", "")
                    == "wikipedia_disambiguation"
                    and getattr(exc, "details", ())
                    and _text(exc.details[0])
                ):
                    plan = await asyncio.to_thread(
                        build_root_work_search_plan,
                        _text(exc.details[0]),
                        plan_id,
                        self._wikipedia_provider,
                        enrich_wikidata_entities,
                        search_wikidata_entities,
                    )
                    return await self._localize_exact_douban_candidates(
                        plan,
                        plan_id=plan_id,
                    )
                raise SearchPlanningError(
                    getattr(exc, "code", str(exc)),
                    getattr(exc, "details", ()),
                ) from exc
            return build_direct_entity_plan(
                direct,
                raw_query=raw_query,
                plan_id=plan_id,
            )
        plan = await asyncio.to_thread(
            build_root_work_search_plan,
            raw_query,
            plan_id,
            self._wikipedia_provider,
            enrich_wikidata_entities,
            search_wikidata_entities,
        )
        return await self._localize_exact_douban_candidates(
            plan,
            plan_id=plan_id,
        )

    @staticmethod
    async def _resolve_confirmed_tmdb(
        identity: ConfirmedIdentity,
        *,
        source_scheduler: SourceScheduler | None = None,
        purpose: str = "authoritative_scope",
    ) -> tuple[dict | None, str]:
        async def fetch_raw(request_identity: str, fetch, cacheable):
            if source_scheduler is None:
                return await fetch()
            return await source_scheduler.run(
                SourceRequestKey(
                    provider="tmdb",
                    purpose=purpose,
                    media_type=identity.media_type,
                    identity=request_identity,
                    scope=identity.requested_scope,
                    season_number=identity.season_number,
                    episode_number=None,
                ),
                fetch,
                cacheable=cacheable,
            )

        try:
            async def verified_detail(tmdb_id: str):
                detail = await fetch_raw(
                    f"detail:{identity.media_type}:{tmdb_id}",
                    lambda: asyncio.to_thread(
                        get_tmdb_entity,
                        identity.media_type,
                        tmdb_id,
                    ),
                    lambda value: _cacheable_tmdb_raw(
                        value,
                        identity,
                        require_https_cover=(purpose == "poster"),
                    ),
                )
                return select_unique_tmdb_fact({
                    "source": "tmdb",
                    "status": "ok" if detail else "not_found",
                    "facts": [detail] if isinstance(detail, dict) else [],
                }, identity)

            direct_tmdb_id = _text(identity.external_ids.get("tmdb"))
            if direct_tmdb_id:
                verified = await verified_detail(direct_tmdb_id)
                return (
                    (verified, "ok")
                    if verified
                    else (None, "not_found")
                )

            exact_sources = [
                (source, _text(identity.external_ids.get(source)))
                for source in ("imdb", "tvdb", "wikidata")
                if _text(identity.external_ids.get(source))
            ]
            for source, external_id in exact_sources:
                candidates = await fetch_raw(
                    f"find:{source}:{external_id}:{identity.media_type}",
                    lambda: asyncio.to_thread(
                        find_tmdb_by_external_id,
                        source,
                        external_id,
                        identity.media_type,
                    ),
                    lambda value: _cacheable_tmdb_raw(
                        value,
                        identity,
                        require_https_cover=(purpose == "poster"),
                    ),
                )
                selected = select_unique_tmdb_fact({
                    "source": "tmdb",
                    "status": "ok" if candidates else "not_found",
                    "facts": candidates[:5],
                }, identity)
                if selected is None:
                    continue
                tmdb_id = _text(
                    selected.get("tmdb_id")
                    or selected.get("id")
                    or (selected.get("external_ids") or {}).get("tmdb")
                )
                verified = await verified_detail(tmdb_id)
                if verified:
                    return verified, "ok"
            if exact_sources:
                return None, "not_found"

            query = build_tmdb_query(identity)
            if query is None:
                return None, "unavailable"
            candidates = await fetch_raw(
                (
                    f"search:{query['title']}:{query['media_type']}:"
                    f"{query['year']}"
                ),
                lambda: asyncio.to_thread(
                    search_tmdb,
                    query["title"],
                    query["media_type"],
                    query["year"],
                ),
                lambda value: _cacheable_tmdb_raw(
                    value,
                    identity,
                    require_https_cover=(purpose == "poster"),
                ),
            )
            result = {
                "source": "tmdb",
                "status": "ok" if candidates else "not_found",
                "facts": candidates[:5],
            }
            selected = select_unique_tmdb_fact(result, identity)
            if selected is None:
                return None, (
                    "not_unique" if candidates else "not_found"
                )
            tmdb_id = _text(
                selected.get("tmdb_id")
                or selected.get("id")
                or (selected.get("external_ids") or {}).get("tmdb")
            )
            verified = await verified_detail(tmdb_id)
            return (verified, "ok") if verified else (None, "not_found")
        except TmdbConfigError as exc:
            return None, exc.code
        except TmdbAuthenticationError:
            return None, "authentication_failed"
        except TmdbRequestError as exc:
            return None, exc.code
        except OSError:
            return None, "server_down"
        except Exception:
            return None, "unavailable"

    @staticmethod
    async def _resolve_confirmed_anilist(
        identity: ConfirmedIdentity,
        *,
        source_scheduler: SourceScheduler | None = None,
        purpose: str = "optional_peer",
    ) -> tuple[dict | None, str]:
        query = build_anilist_query(identity)
        if query is None:
            return None, "not_applicable"

        async def fetch_raw(request_identity: str, fetch, cacheable):
            if source_scheduler is None:
                return await fetch()
            return await source_scheduler.run(
                SourceRequestKey(
                    provider="anilist",
                    purpose=purpose,
                    media_type=identity.media_type,
                    identity=request_identity,
                    scope=identity.requested_scope,
                    season_number=identity.season_number,
                    episode_number=None,
                ),
                fetch,
                cacheable=cacheable,
            )

        try:
            candidates = await fetch_raw(
                f"search:{query['title']}:{query['year']}",
                lambda: asyncio.to_thread(
                    search_anilist,
                    query["title"],
                    query["year"],
                ),
                lambda value: _cacheable_anilist_raw(value, identity),
            )
            result = {
                "source": "anilist",
                "status": "ok" if candidates else "not_found",
                "facts": candidates[:5],
            }
            selected = select_unique_anilist_fact(result, identity)
            if selected is None:
                return None, (
                    "not_unique" if candidates else "not_found"
                )
            anilist_id = _text(
                selected.get("anilist_id")
                or selected.get("id")
                or (selected.get("external_ids") or {}).get("anilist")
            )
            detail = await fetch_raw(
                f"detail:{anilist_id}",
                lambda: asyncio.to_thread(
                    get_anilist_media,
                    anilist_id,
                ),
                lambda value: _cacheable_anilist_raw(value, identity),
            )
            verified = select_unique_anilist_fact({
                "source": "anilist",
                "status": "ok" if detail else "not_found",
                "facts": [detail] if isinstance(detail, dict) else [],
            }, identity)
            return (verified, "ok") if verified else (None, "not_found")
        except AniListConfigError as exc:
            return None, exc.code
        except AniListRequestError as exc:
            return None, exc.code
        except OSError:
            return None, "server_down"
        except Exception:
            return None, "unavailable"

    @staticmethod
    def _candidate_confirmed_identity(candidate: dict) -> ConfirmedIdentity:
        contract = candidate.get("media_metadata") or {}
        identity = contract.get("identity") or {}
        source_links = [
            item
            for item in candidate.get("source_links") or ()
            if isinstance(item, dict)
        ]
        external_ids = {
            _text(key): _text(value)
            for key, value in (identity.get("external_ids") or {}).items()
            if _text(key) and _text(value)
        }
        for link in source_links:
            for key, value in (link.get("external_ids") or {}).items():
                if _text(key) and _text(value):
                    external_ids.setdefault(_text(key), _text(value))
        anchor = source_links[0] if source_links else {}
        anchor_ids = anchor.get("external_ids") or {}
        decision = ((contract.get("evidence") or {}).get("decision") or {})
        return ConfirmedIdentity(
            provider=_text(anchor.get("provider")).casefold(),
            stable_id=_text(next(iter(anchor_ids.values()), "")),
            chinese_title=_text(identity.get("chinese_title")),
            english_title=_text(
                identity.get("official_english_title")
                or identity.get("english_title")
            ),
            original_title=_text(identity.get("original_title")),
            year=_text(identity.get("year"))[:4],
            media_type=_candidate_media_type(candidate),
            requested_scope="work",
            original_language=_text(
                identity.get("original_language")
            ).casefold(),
            genres=tuple(
                _text(item)
                for item in identity.get("genres") or ()
                if _text(item)
            ),
            external_ids=external_ids,
            countries=tuple(
                _text(item)
                for item in identity.get("countries") or ()
                if _text(item)
            ),
            cast_names=tuple(
                _text(
                    item.get("name") if isinstance(item, dict) else item
                )
                for item in (
                    list(identity.get("cast") or ())
                    + list(identity.get("crew") or ())
                )
                if _text(
                    item.get("name") if isinstance(item, dict) else item
                )
            ),
            season_number=(
                int(decision.get("season_number"))
                if str(decision.get("season_number") or "").isdigit()
                else None
            ),
            root_year=_text(
                identity.get("root_year")
                or identity.get("year")
            )[:4],
            scope_year=_text(identity.get("scope_year"))[:4],
        )

    @staticmethod
    def _select_unique_douban_poster_fact(
        facts: list[dict],
        identity: ConfirmedIdentity,
    ) -> dict | None:
        subject_id = _text(identity.external_ids.get("douban_subject"))
        if subject_id:
            exact = [
                item for item in facts
                if _text(
                    item.get("subject_id")
                    or (item.get("external_ids") or {}).get(
                        "douban_subject"
                    )
                ) == subject_id
            ]
            if len(exact) == 1:
                return exact[0]
        expected_titles = {
            value for value in (
                _normalized_title(identity.chinese_title),
                _normalized_title(identity.english_title),
                _normalized_title(identity.original_title),
            )
            if value
        }
        matches = []
        for item in facts:
            titles = {
                value for value in (
                    _normalized_title(item.get("title")),
                    _normalized_title(item.get("chinese_title")),
                    _normalized_title(item.get("english_title")),
                    _normalized_title(item.get("original_title")),
                )
                if value
            }
            item_year = _text(item.get("year"))[:4]
            item_type = _text(item.get("media_type")).casefold()
            if not expected_titles.intersection(titles):
                continue
            if identity.year and item_year and identity.year != item_year:
                continue
            if (
                identity.media_type
                and item_type
                and identity.media_type != item_type
            ):
                continue
            matches.append(item)
        ids = {
            _text(
                item.get("subject_id")
                or (item.get("external_ids") or {}).get("douban_subject")
            )
            for item in matches
        }
        return matches[0] if len(ids - {""}) == 1 else None

    @staticmethod
    def _select_unique_tvdb_poster_fact(
        facts: list[dict],
        identity: ConfirmedIdentity,
    ) -> dict | None:
        tvdb_id = _text(identity.external_ids.get("tvdb"))
        if tvdb_id:
            exact = [
                item
                for item in facts
                if _text(
                    item.get("tvdb_id")
                    or item.get("tvdb_series_id")
                    or item.get("tvdb_movie_id")
                    or item.get("id")
                ) == tvdb_id
            ]
            if len(exact) == 1:
                return exact[0]
        expected_titles = {
            value
            for value in (
                _normalized_title(identity.chinese_title),
                _normalized_title(identity.english_title),
                _normalized_title(identity.original_title),
            )
            if value
        }
        matches = []
        for item in facts:
            titles = {
                value
                for value in (
                    _normalized_title(item.get("name")),
                    _normalized_title(item.get("english_title")),
                    _normalized_title(item.get("original_title")),
                    *(
                        _normalized_title(alias)
                        for alias in item.get("aliases") or ()
                    ),
                )
                if value
            }
            item_year = _text(item.get("year"))[:4]
            if not expected_titles.intersection(titles):
                continue
            if identity.year and item_year and identity.year != item_year:
                continue
            matches.append(item)
        ids = {
            _text(
                item.get("tvdb_id")
                or item.get("tvdb_series_id")
                or item.get("tvdb_movie_id")
                or item.get("id")
            )
            for item in matches
        }
        return matches[0] if len(ids - {""}) == 1 else None

    async def _lookup_candidate_poster(
        self,
        candidate: dict,
        provider: str,
    ) -> str:
        identity = self._candidate_confirmed_identity(candidate)
        if identity.media_type not in {"movie", "series"}:
            return ""
        scope, season_number, episode_number = self._source_coordinates(
            candidate
        )
        if provider == "tmdb":
            fact, _status = await self._resolve_confirmed_tmdb(
                identity,
                source_scheduler=self.source_scheduler,
                purpose="poster",
            )
        elif provider == "douban":
            query = _text(" ".join(filter(None, (
                identity.chinese_title
                or identity.english_title
                or identity.original_title,
                identity.year,
            ))))
            if not query:
                return ""
            subject_id = _text(
                identity.external_ids.get("douban_subject")
            )
            result = await self._run_source_request(
                provider="douban",
                purpose="poster",
                media_type=identity.media_type,
                identity=_poster_search_identity(
                    endpoint="search",
                    query=query,
                    title=(
                        identity.chinese_title
                        or identity.english_title
                        or identity.original_title
                    ),
                    year=identity.year,
                    media_type=identity.media_type,
                    stable_id=(
                        f"douban_subject:{subject_id}"
                        if subject_id
                        else ""
                    ),
                ),
                scope=scope,
                season_number=season_number,
                episode_number=episode_number,
                fetch=lambda: asyncio.to_thread(
                    self._douban_provider,
                    {"source_queries": {"douban": [query]}},
                ),
                cacheable=lambda value: _cacheable_poster_raw(
                    value,
                    identity,
                    self._select_unique_douban_poster_fact,
                ),
            )
            fact = self._select_unique_douban_poster_fact(
                list((result or {}).get("facts") or ()),
                identity,
            )
        elif provider == "tvdb":
            query = (
                identity.english_title
                or identity.original_title
                or identity.chinese_title
            )
            if not query:
                return ""
            loader = (
                search_tvdb_series
                if identity.media_type == "series"
                else search_tvdb_movies
            )
            tvdb_id = _text(identity.external_ids.get("tvdb"))
            facts = await self._run_source_request(
                provider="tvdb",
                purpose="poster",
                media_type=identity.media_type,
                identity=_poster_search_identity(
                    endpoint="search",
                    query=query,
                    title=query,
                    year=identity.year,
                    media_type=identity.media_type,
                    stable_id=f"tvdb:{tvdb_id}" if tvdb_id else "",
                ),
                scope=scope,
                season_number=season_number,
                episode_number=episode_number,
                fetch=lambda: asyncio.to_thread(
                    loader,
                    query,
                    identity.year,
                ),
                cacheable=lambda value: _cacheable_poster_raw(
                    value,
                    identity,
                    self._select_unique_tvdb_poster_fact,
                ),
            )
            fact = self._select_unique_tvdb_poster_fact(facts, identity)
        else:
            return ""
        poster_url = _text(
            fact.get("cover_url") if isinstance(fact, dict) else ""
        )
        return poster_url if poster_url.startswith("https://") else ""

    async def _supplement_candidate_posters(self, stored: dict) -> None:
        candidates = [
            deepcopy(item)
            for item in stored.get("candidates") or ()
            if isinstance(item, dict)
        ]
        tasks = {}
        for index, candidate in enumerate(candidates[:5]):
            contract = candidate.get("media_metadata") or {}
            identity = contract.get("identity") or {}
            current = _text(
                candidate.get("poster_url") or identity.get("poster_url")
            )
            if current.startswith("https://"):
                candidate["poster_url"] = current
                continue
            for provider in ("tmdb", "douban", "tvdb"):
                task = asyncio.create_task(
                    self.candidate_poster_lookup(candidate, provider)
                )
                tasks[task] = (index, provider)
        if not tasks:
            stored["candidates"] = tuple(candidates)
            return
        done, pending = await asyncio.wait(
            tasks,
            timeout=max(0.01, float(self.candidate_poster_timeout)),
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        found = {}
        for task in done:
            index, provider = tasks[task]
            try:
                url = _text(task.result())
            except Exception:
                continue
            if url.startswith("https://"):
                found[(index, provider)] = url
        for index, candidate in enumerate(candidates[:5]):
            selected = next(
                (
                    (provider, found[(index, provider)])
                    for provider in ("tmdb", "douban", "tvdb")
                    if (index, provider) in found
                ),
                None,
            )
            if selected is None:
                continue
            provider, poster_url = selected
            candidate["poster_url"] = poster_url
            contract = candidate.get("media_metadata") or {}
            identity = contract.get("identity") or {}
            identity["poster_url"] = poster_url
            identity["poster_source"] = provider
        stored["candidates"] = tuple(candidates)

    def _start_candidate_poster_enrichment(
        self,
        plan_id: str,
        stored: dict,
    ) -> None:
        current = stored.get("candidate_poster_task")
        if current is not None and not current.done():
            return
        generation = int(stored.get("candidate_poster_generation") or 0) + 1
        stored["candidate_poster_generation"] = generation

        async def enrich() -> None:
            preview = {
                "candidates": tuple(deepcopy(stored.get("candidates") or ()))
            }
            try:
                await self._supplement_candidate_posters(preview)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if runtime_context.logger:
                    runtime_context.logger.warning(
                        "search_candidate_posters status=failed "
                        f"error={type(exc).__name__}"
                    )
                return
            accepted = stored.get("initial_candidate_report_accepted")
            if accepted is not None:
                await accepted.wait()
            operation = self.operations.get(stored.get("operation_id")) or {}
            if (
                self.plans.get(plan_id) is not stored
                or stored.get("candidate_poster_generation") != generation
                or not self._is_candidate_screen(operation)
            ):
                return
            stored["candidates"] = tuple(preview["candidates"])
            action = (
                self._candidate_grid_action(stored)
                if (stored.get("plan") or {}).get("links_frozen")
                else self._candidate_action(stored, 0, edit=True)
            )
            try:
                await self._report_operation(
                    stored["operation_id"],
                    state="awaiting_input",
                    stage=str(operation.get("stage") or "candidate_selection"),
                    status_text=action["text"],
                    control="exit",
                    details=deepcopy(action.get("data") or {}),
                )
            except Exception as exc:
                if runtime_context.logger:
                    runtime_context.logger.warning(
                        "search_candidate_posters status=projection_failed "
                        f"error={type(exc).__name__}"
                    )

        stored["candidate_poster_task"] = asyncio.create_task(enrich())

    @staticmethod
    def _invalidate_candidate_poster_enrichment(stored: dict) -> None:
        stored["candidate_poster_generation"] = (
            int(stored.get("candidate_poster_generation") or 0) + 1
        )
        task = stored.get("candidate_poster_task")
        if task is not None and not task.done():
            task.cancel()

    @staticmethod
    def _is_candidate_screen(operation: dict) -> bool:
        return bool(
            isinstance(operation, dict)
            and operation.get("state") == "awaiting_input"
            and operation.get("stage") in {
                "candidate_selection",
                "plan_confirmation",
            }
        )

    async def _supplement_selected_candidate(
        self,
        candidate: dict,
        raw_query: str,
        *,
        purpose: str = "all",
    ) -> dict:
        del raw_query
        if purpose not in {"all", "authoritative_scope", "presentation"}:
            raise ValueError("unsupported enrichment purpose")
        include_authoritative = purpose in {"all", "authoritative_scope"}
        include_presentation = purpose in {"all", "presentation"}
        result = deepcopy(candidate)
        contract = result.get("media_metadata") or {}
        identity_value = contract.get("identity") or {}
        source_links = [
            dict(item)
            for item in result.get("source_links") or ()
            if isinstance(item, dict)
        ]
        anchor_fact_id = _text(result.get("anchor_fact_id"))
        anchor_link = next(
            (
                item
                for item in source_links
                if _text(item.get("fact_id")) == anchor_fact_id
            ),
            source_links[0] if source_links else {},
        )
        anchor_ids = (
            anchor_link.get("external_ids")
            if isinstance(anchor_link.get("external_ids"), dict)
            else {}
        )
        requested_scope = _text(
            result.get("intended_scope")
            or (contract.get("retrieval") or {}).get("scope")
        ).casefold()
        source_scope, source_season, source_episode = (
            self._source_coordinates(result)
        )

        def root_lookup_title(value):
            value = _text(value)
            intent = parse_search_intent(value)
            if (
                requested_scope in {"season", "episode"}
                and intent.get("scope") in {"season", "episode"}
                and _text(intent.get("title"))
            ):
                return _text(intent["title"])
            return value

        confirmed = ConfirmedIdentity(
            provider=_text(anchor_link.get("provider")).casefold(),
            stable_id=_text(next(iter(anchor_ids.values()), "")),
            chinese_title=_text(identity_value.get("chinese_title")),
            english_title=root_lookup_title(
                identity_value.get("official_english_title")
                or identity_value.get("english_title")
            ),
            original_title=root_lookup_title(
                identity_value.get("original_title")
            ),
            year=(
                ""
                if requested_scope in {"season", "episode"}
                else _text(identity_value.get("year"))[:4]
            ),
            media_type=_text(
                identity_value.get("content_kind")
                or (contract.get("placement") or {}).get("library_type")
            ).casefold(),
            requested_scope=requested_scope,
            original_language=_text(
                identity_value.get("original_language")
            ).casefold(),
            genres=tuple(
                _text(item)
                for item in identity_value.get("genres") or ()
                if _text(item)
            ),
            external_ids={
                _text(key): _text(value)
                for key, value in (
                    identity_value.get("external_ids") or {}
                ).items()
                if _text(key) and _text(value)
            },
            countries=tuple(
                _text(item)
                for item in identity_value.get("countries") or ()
                if _text(item)
            ),
            cast_names=tuple(
                _text(
                    item.get("name") if isinstance(item, dict) else item
                )
                for item in (
                    list(identity_value.get("cast") or ())
                    + list(identity_value.get("crew") or ())
                )
                if _text(
                    item.get("name") if isinstance(item, dict) else item
                )
            ),
            season_number=(
                int(
                    result.get("requested_season_number")
                    or (
                        (contract.get("evidence") or {}).get("decision")
                        or {}
                    ).get("season_number")
                )
                if str(
                    result.get("requested_season_number")
                    or (
                        (contract.get("evidence") or {}).get("decision")
                        or {}
                    ).get("season_number")
                    or ""
                ).isdigit()
                else None
            ),
            root_year=_text(
                identity_value.get("root_year")
                or identity_value.get("year")
            )[:4],
            scope_year=_text(identity_value.get("scope_year"))[:4],
        )

        def scoped_source_binding(provider: str, fact: dict) -> dict:
            if (
                confirmed.media_type != "series"
                or requested_scope not in {"season", "episode"}
                or confirmed.season_number is None
            ):
                return {
                    "role": (
                        "movie"
                        if confirmed.media_type == "movie"
                        else "series_root"
                    ),
                    "season_number": None,
                    "episode_number": None,
                    "verification": "fact_verified",
                }
            requested_season = confirmed.season_number
            coordinates = set()
            for item in fact.get("episodes") or ():
                if not isinstance(item, dict):
                    continue
                try:
                    season = int(item.get("season_number"))
                    episode = int(item.get("episode_number"))
                except (TypeError, ValueError):
                    continue
                if season > 0 and episode > 0:
                    coordinates.add((season, episode))
            try:
                season_count = int(fact.get("season_count") or 0)
            except (TypeError, ValueError):
                season_count = 0
            season_verified = (
                any(season == requested_season for season, _ in coordinates)
                or (provider == "wikipedia" and season_count >= requested_season)
            )
            if not season_verified:
                return {
                    "role": "series_root",
                    "season_number": None,
                    "episode_number": None,
                    "verification": "fact_verified",
                }
            verification = {
                "wikipedia": "wikipedia_season_count_verified",
                "tvdb": "tvdb_inventory_verified",
                "tmdb": "tmdb_inventory_verified",
            }.get(provider, "fact_verified")
            return {
                "role": (
                    "episode" if requested_scope == "episode" else "season"
                ),
                "season_number": requested_season,
                "episode_number": (
                    result.get("requested_episode_number")
                    if requested_scope == "episode"
                    else None
                ),
                "verification": verification,
            }
        unresolved = [
            _text(item)
            for item in result.get("unresolved_sources") or ()
            if _text(item)
        ]
        providers = {
            _text(item.get("provider")).casefold()
            for item in source_links
        }
        search_session_id = _text(
            contract.get("metadata_id")
            or result.get("candidate_id")
        )
        wikipedia_fact = None
        tmdb_fact = None
        if include_authoritative and "wikipedia" not in providers:
            queries = build_wikipedia_queries(confirmed)
            log_search_event(
                runtime_context.logger,
                "search.wikipedia_started",
                search_session_id=search_session_id,
                query_count=sum(len(items) for items in queries.values()),
                queries=queries,
            )
            try:
                wikipedia_result = await self._run_source_request(
                    provider="wikipedia",
                    purpose="authoritative_scope",
                    media_type=confirmed.media_type,
                    identity=(
                        "queries:"
                        + json.dumps(
                            queries,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    ),
                    scope=source_scope,
                    season_number=source_season,
                    episode_number=source_episode,
                    fetch=lambda: asyncio.to_thread(
                        self._wikipedia_provider,
                        {"source_queries": queries},
                    ),
                    cacheable=lambda value: _cacheable_wikipedia_raw(
                        value,
                        confirmed,
                    ),
                )
            except Exception:
                wikipedia_result = {
                    "status": "unavailable",
                    "facts": [],
                }
            wikipedia_fact = select_unique_wikipedia_fact(
                wikipedia_result,
                confirmed,
            )
            if wikipedia_fact is not None:
                wikipedia_id = _text(
                    wikipedia_fact.get("wikibase_item")
                    or (wikipedia_fact.get("external_ids") or {}).get(
                        "wikipedia"
                    )
                )
                wikipedia_url = _text(wikipedia_fact.get("url"))
                if wikipedia_id and wikipedia_url:
                    binding = scoped_source_binding(
                        "wikipedia",
                        wikipedia_fact,
                    )
                    source_links.append({
                        "provider": "wikipedia",
                        "fact_id": f"wikipedia:{wikipedia_id}",
                        "url": wikipedia_url,
                        "external_ids": {"wikipedia": wikipedia_id},
                        **binding,
                        "proposed_season_number": None,
                        "proposed_episode_number": None,
                    })
                    providers.add("wikipedia")
            if "wikipedia" not in providers:
                wikipedia_status = _text(
                    wikipedia_result.get("status")
                ).casefold() or "not_found"
                unresolved.append(f"wikipedia:{wikipedia_status}")
            log_search_event(
                runtime_context.logger,
                "search.wikipedia_completed",
                search_session_id=search_session_id,
                level=(
                    "info" if "wikipedia" in providers else "warning"
                ),
                status=(
                    "ok"
                    if "wikipedia" in providers
                    else wikipedia_status
                ),
                matched=bool("wikipedia" in providers),
            )
        else:
            log_search_event(
                runtime_context.logger,
                "search.wikipedia_skipped",
                search_session_id=search_session_id,
                reason=(
                    "already_confirmed_source"
                    if "wikipedia" in providers
                    else "purpose_excluded"
                ),
            )

        if include_authoritative and "tmdb" not in providers:
            tmdb_external_ids = dict(confirmed.external_ids)
            if isinstance(wikipedia_fact, dict):
                wikipedia_ids = (
                    wikipedia_fact.get("external_ids")
                    if isinstance(wikipedia_fact.get("external_ids"), dict)
                    else {}
                )
                tmdb_external_ids.update({
                    _text(key): _text(value)
                    for key, value in wikipedia_ids.items()
                    if _text(key) and _text(value)
                })
                wikidata_id = _text(wikipedia_fact.get("wikibase_item"))
                if wikidata_id.startswith("Q"):
                    tmdb_external_ids["wikidata"] = wikidata_id
            tmdb_identity = replace(
                confirmed,
                external_ids=tmdb_external_ids,
            )
            tmdb_query = build_tmdb_query(tmdb_identity)
            log_search_event(
                runtime_context.logger,
                "search.tmdb_started",
                search_session_id=search_session_id,
                query=tmdb_query or {},
            )
            tmdb_fact, tmdb_status = await self._resolve_confirmed_tmdb(
                tmdb_identity,
                source_scheduler=self.source_scheduler,
                purpose="authoritative_scope",
            )
            if tmdb_fact is not None:
                tmdb_id = _text(
                    tmdb_fact.get("tmdb_id")
                    or tmdb_fact.get("id")
                    or (tmdb_fact.get("external_ids") or {}).get("tmdb")
                )
                external_ids = {
                    _text(key): _text(value)
                    for key, value in (
                        tmdb_fact.get("external_ids") or {}
                    ).items()
                    if _text(key) and _text(value)
                }
                external_ids["tmdb"] = tmdb_id
                binding = scoped_source_binding("tmdb", tmdb_fact)
                source_links.append({
                    "provider": "tmdb",
                    "fact_id": f"tmdb:{tmdb_id}",
                    "url": _text(tmdb_fact.get("url")) or (
                        "https://www.themoviedb.org/"
                        f"{'movie' if confirmed.media_type == 'movie' else 'tv'}/"
                        f"{tmdb_id}"
                    ),
                    "external_ids": external_ids,
                    **binding,
                    "proposed_season_number": None,
                    "proposed_episode_number": None,
                })
                providers.add("tmdb")
            if "tmdb" not in providers:
                unresolved.append(f"tmdb:{tmdb_status}")
            log_search_event(
                runtime_context.logger,
                "search.tmdb_completed",
                search_session_id=search_session_id,
                level="info" if "tmdb" in providers else "warning",
                status="ok" if "tmdb" in providers else tmdb_status,
                matched=bool("tmdb" in providers),
            )
        else:
            log_search_event(
                runtime_context.logger,
                "search.tmdb_skipped",
                search_session_id=search_session_id,
                reason=(
                    "already_confirmed_source"
                    if "tmdb" in providers
                    else "purpose_excluded"
                ),
            )

        if (
            include_authoritative
            and confirmed.media_type == "series"
            and "tvdb" not in providers
        ):
            tvdb_query = build_tvdb_query(
                confirmed,
                wikipedia_fact or tmdb_fact,
            )
            log_search_event(
                runtime_context.logger,
                "search.tvdb_started",
                search_session_id=search_session_id,
                query=tvdb_query or {},
            )
            tvdb_status = "unavailable"
            tvdb_series = None
            if tvdb_query is not None:
                try:
                    stable_tvdb_id = _text(tvdb_query.get("tvdb_id"))
                    tvdb_identity = replace(
                        confirmed,
                        english_title=tvdb_query["title"],
                        external_ids={
                            **confirmed.external_ids,
                            **(
                                {"tvdb": stable_tvdb_id}
                                if stable_tvdb_id
                                else {}
                            ),
                        },
                    )
                    if stable_tvdb_id:
                        tvdb_series = await self._run_source_request(
                            provider="tvdb",
                            purpose="authoritative_scope",
                            media_type=confirmed.media_type,
                            identity=f"series:{stable_tvdb_id}",
                            scope=source_scope,
                            season_number=source_season,
                            episode_number=source_episode,
                            fetch=lambda: asyncio.to_thread(
                                get_tvdb_series,
                                stable_tvdb_id,
                            ),
                            cacheable=lambda value: _cacheable_tvdb_raw(
                                value,
                                tvdb_identity,
                                require_episodes=True,
                            ),
                        )
                    else:
                        tvdb_candidates = await self._run_source_request(
                            provider="tvdb",
                            purpose="authoritative_scope",
                            media_type=confirmed.media_type,
                            identity=(
                                f"search:{tvdb_query['title']}:"
                                f"{tvdb_query['year']}"
                            ),
                            scope=source_scope,
                            season_number=source_season,
                            episode_number=source_episode,
                            fetch=lambda: asyncio.to_thread(
                                search_tvdb_series,
                                tvdb_query["title"],
                                tvdb_query["year"],
                            ),
                            cacheable=lambda value: _cacheable_tvdb_raw(
                                value,
                                tvdb_identity,
                            ),
                        )
                        tvdb_result = {
                            "source": "tvdb",
                            "status": (
                                "ok" if tvdb_candidates else "not_found"
                            ),
                            "facts": [{
                                "movies": [],
                                "series": tvdb_candidates[:5],
                                "episodes_by_series": {},
                            }],
                        }
                        selected = select_unique_tvdb_series(
                            tvdb_result,
                            tvdb_identity,
                        )
                        if selected is not None:
                            tvdb_id = _text(
                                selected.get("tvdb_series_id")
                                or selected.get("tvdb_id")
                                or selected.get("id")
                            )
                            selected_tvdb_identity = replace(
                                tvdb_identity,
                                external_ids={
                                    **tvdb_identity.external_ids,
                                    "tvdb": tvdb_id,
                                },
                            )
                            tvdb_series = await self._run_source_request(
                                provider="tvdb",
                                purpose="authoritative_scope",
                                media_type=confirmed.media_type,
                                identity=f"series:{tvdb_id}",
                                scope=source_scope,
                                season_number=source_season,
                                episode_number=source_episode,
                                fetch=lambda: asyncio.to_thread(
                                    get_tvdb_series,
                                    tvdb_id,
                                ),
                                cacheable=lambda value: _cacheable_tvdb_raw(
                                    value,
                                    selected_tvdb_identity,
                                    require_episodes=True,
                                ),
                            )
                        else:
                            tvdb_status = _text(
                                tvdb_result.get("status")
                            ).casefold() or "not_found"
                            if tvdb_status == "ok":
                                tvdb_status = "not_unique"
                    if not (
                        isinstance(tvdb_series, dict)
                        and tvdb_series.get("episodes")
                    ):
                        tvdb_series = None
                        if tvdb_status not in {"not_found", "not_unique"}:
                            tvdb_status = "unavailable"
                    else:
                        tvdb_status = "ok"
                except TvdbConfigError as exc:
                    tvdb_status = exc.code
                except TvdbAuthenticationError:
                    tvdb_status = "authentication_failed"
                except TvdbRequestError as exc:
                    tvdb_status = exc.code
                except OSError:
                    tvdb_status = "server_down"
                except Exception:
                    tvdb_status = "unavailable"
            if tvdb_series is not None:
                tvdb_id = _text(
                    tvdb_series.get("tvdb_series_id")
                    or tvdb_series.get("tvdb_id")
                    or tvdb_series.get("id")
                )
                binding = scoped_source_binding("tvdb", tvdb_series)
                source_links.append({
                    "provider": "tvdb",
                    "fact_id": f"tvdb:series:{tvdb_id}",
                    "url": _text(tvdb_series.get("url"))
                    or f"https://thetvdb.com/series/{tvdb_id}",
                    "external_ids": {"tvdb": tvdb_id},
                    **binding,
                    "proposed_season_number": None,
                    "proposed_episode_number": None,
                })
                providers.add("tvdb")
            if "tvdb" not in providers:
                unresolved.append(f"tvdb:{tvdb_status}")
                if requested_scope in {"work", "whole_series"}:
                    result["intended_scope"] = "whole_series"
                    result["requested_season_number"] = None
                    result["requested_episode_number"] = None
                    if isinstance(contract.get("retrieval"), dict):
                        contract["retrieval"]["scope"] = "whole_series"
            log_search_event(
                runtime_context.logger,
                "search.tvdb_completed",
                search_session_id=search_session_id,
                level="info" if "tvdb" in providers else "warning",
                status="ok" if "tvdb" in providers else tvdb_status,
                matched=bool("tvdb" in providers),
                tvdb_id=(
                    next(
                        (
                            (item.get("external_ids") or {}).get("tvdb")
                            for item in source_links
                            if item.get("provider") == "tvdb"
                        ),
                        "",
                    )
                ),
                inventory_count=(
                    len(tvdb_series.get("episodes") or ())
                    if isinstance(tvdb_series, dict)
                    else 0
                ),
            )
        else:
            log_search_event(
                runtime_context.logger,
                "search.tvdb_skipped",
                search_session_id=search_session_id,
                reason=(
                    "not_series"
                    if confirmed.media_type != "series"
                    else (
                        "already_confirmed_source"
                        if "tvdb" in providers
                        else "purpose_excluded"
                    )
                ),
            )

        if include_presentation and "douban" not in providers:
            douban_identity_ids = dict(confirmed.external_ids)
            if isinstance(tmdb_fact, dict):
                douban_identity_ids.update({
                    _text(key): _text(value)
                    for key, value in (
                        tmdb_fact.get("external_ids") or {}
                    ).items()
                    if _text(key) and _text(value)
                })
            douban_identity = replace(
                confirmed,
                external_ids=douban_identity_ids,
                original_language=(
                    confirmed.original_language
                    or _text(
                        (tmdb_fact or {}).get("original_language")
                    ).casefold()
                ),
                countries=(
                    confirmed.countries
                    or tuple(
                        _text(item)
                        for item in (tmdb_fact or {}).get("countries") or ()
                        if _text(item)
                    )
                ),
                cast_names=(
                    confirmed.cast_names
                    or tuple(
                        _text(
                            item.get("name")
                            if isinstance(item, dict)
                            else item
                        )
                        for item in (
                            list((tmdb_fact or {}).get("cast") or ())
                            + list((tmdb_fact or {}).get("crew") or ())
                        )
                        if _text(
                            item.get("name")
                            if isinstance(item, dict)
                            else item
                        )
                    )
                ),
            )
            douban_query = _text(" ".join(filter(None, (
                confirmed.english_title or confirmed.original_title,
                confirmed.year,
            ))))
            douban_status = "unavailable"
            douban_fact = None
            if douban_query:
                try:
                    douban_result = await self._run_source_request(
                        provider="douban",
                        purpose="presentation_locale",
                        media_type=confirmed.media_type,
                        identity=f"query:{douban_query}",
                        scope=source_scope,
                        season_number=source_season,
                        episode_number=source_episode,
                        fetch=lambda: asyncio.to_thread(
                            self._douban_provider,
                            {
                                "source_queries": {
                                    "douban": [douban_query],
                                },
                            },
                        ),
                        cacheable=lambda value: _cacheable_douban_raw(
                            value,
                            douban_identity,
                        ),
                    )
                    douban_status = _text(
                        douban_result.get("status")
                        if isinstance(douban_result, dict)
                        else "unavailable"
                    ).casefold() or "not_found"
                    douban_fact = select_unique_douban_fact(
                        douban_result,
                        douban_identity,
                    )
                except Exception:
                    douban_status = "unavailable"
            if douban_fact is not None:
                subject_id = _text(
                    douban_fact.get("subject_id")
                    or (douban_fact.get("external_ids") or {}).get(
                        "douban_subject"
                    )
                )
                douban_external_ids = {
                    _text(key): _text(value)
                    for key, value in (
                        douban_fact.get("external_ids") or {}
                    ).items()
                    if _text(key) and _text(value)
                }
                douban_external_ids["douban_subject"] = subject_id
                source_links.append({
                    "provider": "douban",
                    "fact_id": f"douban:{subject_id}",
                    "url": _text(douban_fact.get("url"))
                    or f"https://movie.douban.com/subject/{subject_id}/",
                    "external_ids": douban_external_ids,
                    "role": (
                        "movie"
                        if confirmed.media_type == "movie"
                        else "series_root"
                    ),
                    "season_number": None,
                    "episode_number": None,
                    "verification": "fact_verified",
                    "proposed_season_number": None,
                    "proposed_episode_number": None,
                })
                result["douban_match_mode"] = _text(
                    douban_fact.get("douban_match_mode")
                )
                log_search_event(
                    runtime_context.logger,
                    "search.douban_title_verified",
                    search_session_id=search_session_id,
                    match_mode=result["douban_match_mode"],
                    douban_title_raw=douban_fact.get("douban_title_raw"),
                    selected_chinese_title=douban_fact.get("chinese_title"),
                    subject_id=subject_id,
                )
                result["source_links"] = source_links
                result = localize_candidate_from_verified_douban(
                    result,
                    douban_fact,
                    match_mode=result["douban_match_mode"],
                )
                source_links = [
                    dict(item)
                    for item in result.get("source_links") or ()
                    if isinstance(item, dict)
                ]
                providers.add("douban")
            if "douban" not in providers:
                unresolved.append(f"douban:{douban_status}")

        anilist_query = build_anilist_query(confirmed)
        if (
            include_presentation
            and anilist_query is not None
            and "anilist" not in providers
        ):
            log_search_event(
                runtime_context.logger,
                "search.anilist_started",
                search_session_id=search_session_id,
                query=anilist_query,
            )
            anilist_fact, anilist_status = (
                await self._resolve_confirmed_anilist(
                    confirmed,
                    source_scheduler=self.source_scheduler,
                    purpose="optional_peer",
                )
            )
            if anilist_fact is not None:
                anilist_id = _text(
                    anilist_fact.get("anilist_id")
                    or anilist_fact.get("id")
                    or (anilist_fact.get("external_ids") or {}).get(
                        "anilist"
                    )
                )
                source_links.append({
                    "provider": "anilist",
                    "fact_id": f"anilist:{anilist_id}",
                    "url": _text(anilist_fact.get("url"))
                    or f"https://anilist.co/anime/{anilist_id}",
                    "external_ids": {"anilist": anilist_id},
                    "role": (
                        "movie"
                        if confirmed.media_type == "movie"
                        else "series_root"
                    ),
                    "season_number": None,
                    "episode_number": None,
                    "verification": "fact_verified",
                    "proposed_season_number": None,
                    "proposed_episode_number": None,
                })
                providers.add("anilist")
            if "anilist" not in providers:
                unresolved.append(f"anilist:{anilist_status}")
            log_search_event(
                runtime_context.logger,
                "search.anilist_completed",
                search_session_id=search_session_id,
                level="info" if "anilist" in providers else "warning",
                status=(
                    "ok" if "anilist" in providers else anilist_status
                ),
                matched=bool("anilist" in providers),
            )
        else:
            log_search_event(
                runtime_context.logger,
                "search.anilist_skipped",
                search_session_id=search_session_id,
                reason=(
                    "not_japanese_animation"
                    if anilist_query is None
                    else (
                        "already_confirmed_source"
                        if "anilist" in providers
                        else "purpose_excluded"
                    )
                ),
            )
        result["source_links"] = source_links
        result["unresolved_sources"] = list(dict.fromkeys(unresolved))
        return result

    def _wikipedia_provider(self, hypotheses: dict):
        config = (((self.config.get("metadata") or {}).get("wikipedia") or {}))
        if not config.get("enable", True):
            return {"source": "wikipedia", "status": "disabled", "facts": [], "source_urls": [], "error": ""}
        source_queries = hypotheses.get("source_queries") or {}
        zh_queries = source_queries.get("wikipedia_zh")
        en_queries = source_queries.get("wikipedia_en")
        timeout = float(config.get("timeout") or 10)
        max_queries = max(
            1,
            min(int(config.get("max_queries") or 2), 6),
        )
        if isinstance(zh_queries, list) or isinstance(en_queries, list):
            zh_queries = list(zh_queries or [])[:max_queries]
            en_queries = list(en_queries or [])[:max_queries]
            results = []
            configured = tuple(config.get("languages") or ["zh", "en"])
            if "zh" in configured and zh_queries:
                results.append(lookup_wikipedia_evidence(
                    zh_queries,
                    languages=("zh",),
                    timeout=timeout,
                    min_interval=float(
                        config.get("min_interval") or 0
                    ),
                    rate_limit_cooldown=float(
                        config.get("rate_limit_cooldown") or 0
                    ),
                ))
            if "en" in configured and en_queries:
                results.append(lookup_wikipedia_evidence(
                    en_queries,
                    languages=("en",),
                    timeout=timeout,
                    min_interval=float(
                        config.get("min_interval") or 0
                    ),
                    rate_limit_cooldown=float(
                        config.get("rate_limit_cooldown") or 0
                    ),
                ))
            if results:
                return self._merge_source_results("wikipedia", results)
        queries = list(
            source_queries.get("wikipedia") or []
        )[:max_queries]
        return lookup_wikipedia_evidence(
            queries,
            languages=tuple(config.get("languages") or ["zh", "en"]),
            timeout=timeout,
            min_interval=float(config.get("min_interval") or 0),
            rate_limit_cooldown=float(
                config.get("rate_limit_cooldown") or 0
            ),
        )

    def _douban_provider(self, hypotheses: dict):
        queries = ((hypotheses.get("source_queries") or {}).get("douban") or [])
        config = ((self.config.get("metadata") or {}).get("douban") or {})
        if not config.get("enable", True):
            return {
                "source": "douban",
                "status": "disabled",
                "facts": [],
                "source_urls": [],
                "error": "",
            }
        return lookup_douban_evidence(
            queries,
            timeout=float(config.get("timeout") or 10),
            cache_ttl=float(config.get("cache_ttl") or 900),
            max_concurrency=int(config.get("max_concurrency") or 2),
            circuit_breaker_failures=int(
                config.get("circuit_breaker_failures") or 3
            ),
            circuit_breaker_seconds=float(
                config.get("circuit_breaker_seconds") or 300
            ),
        )

    @staticmethod
    def _merge_source_results(source: str, results: list[dict]) -> dict:
        facts = []
        urls = []
        errors = []
        statuses = []
        for result in results:
            if not isinstance(result, dict):
                continue
            statuses.append(str(result.get("status") or "server_down"))
            facts.extend(
                item
                for item in (result.get("facts") or [])
                if isinstance(item, dict)
            )
            for url in result.get("source_urls") or []:
                if url and url not in urls:
                    urls.append(url)
            if result.get("error"):
                errors.append(str(result["error"]))
        if facts:
            status = "ok"
        else:
            status = next(
                (
                    candidate
                    for candidate in (
                        "authentication_failed",
                        "credential_missing",
                        "rate_limited",
                        "blocked",
                        "timeout",
                        "server_down",
                        "unavailable",
                        "disabled",
                        "not_found",
                    )
                    if candidate in statuses
                ),
                "server_down",
            )
        return {
            "source": source,
            "status": status,
            "facts": facts,
            "source_urls": urls,
            "error": "; ".join(errors),
        }

    @staticmethod
    def _search_releases(query: str, media_type: str):
        lookup_types = ("movie",) if media_type == "movie" else ("tv",) if media_type == "series" else ("movie", "tv")
        results = []
        seen = set()
        for lookup_type in lookup_types:
            for item in search_prowlarr(query, lookup_type):
                key = item.get("magnet_url") or item.get("download_url") or item.get("title")
                if key and key not in seen:
                    seen.add(key)
                    results.append(item)
        return results

    @staticmethod
    def _english_prowlarr_queries(
        plan: dict,
        contract: dict,
    ) -> list[str]:
        del plan
        retrieval = contract.get("retrieval") or {}
        identity = contract.get("identity") or {}
        english = " ".join(str(identity.get("english_title") or "").split())
        if not english or not _LATIN.search(english):
            raise FeatureError(
                "english_title_missing",
                "Prowlarr search requires a canonical Latin title",
            )
        media_type = str(retrieval.get("media_type") or "")
        scope = str(retrieval.get("scope") or "work")
        if media_type == "movie":
            scope = "movie"
        decision = ((contract.get("evidence") or {}).get("decision") or {})
        placement = contract.get("placement") or {}
        season = decision.get("season_number")
        episode = decision.get("episode_number")
        if season is None:
            season = placement.get("season_number")
        if episode is None:
            episode = placement.get("episode_number")
        if scope in {"season", "episode"} and season is None:
            items = contract.get("items") or []
            first = next(
                (item for item in items if isinstance(item, dict)),
                {},
            )
            season = first.get("season_number")
            if episode is None:
                episode = first.get("episode_number")
        try:
            query = build_prowlarr_query(
                english,
                scope,
                season_number=season,
                episode_number=episode,
            )
        except (TypeError, ValueError) as exc:
            raise FeatureError(
                "bounded_scope_incomplete",
                "Prowlarr search scope is incomplete",
            ) from exc
        if media_type == "series":
            if scope == "season":
                return [
                    query,
                    build_prowlarr_query(
                        f"{english} Season {int(season):02d}",
                        "work",
                    ),
                ]
            if scope == "whole_series":
                seasons = series_inventory(contract).seasons
                if seasons == (1,):
                    return [
                        build_prowlarr_query(
                            english,
                            "season",
                            season_number=1,
                        ),
                        build_prowlarr_query(
                            f"{english} Season 01",
                            "work",
                        ),
                        build_prowlarr_query(
                            f"{english} Complete",
                            "work",
                        ),
                    ]
                if len(seasons) > 1:
                    base = build_prowlarr_query(english, "work")
                    return [
                        f"{base} S{seasons[0]:02d}-S{seasons[-1]:02d}",
                        build_prowlarr_query(
                            f"{english} Complete",
                            "work",
                        ),
                    ]
        return [query]

    @staticmethod
    def _english_prowlarr_query(plan: dict, contract: dict) -> str:
        return SearchFeature._english_prowlarr_queries(
            plan,
            contract,
        )[0]

    def _release_plan(self, plan_id: str):
        stored = self.plans.pop(plan_id, None)
        if isinstance(stored, dict):
            for key in ("candidate_poster_task", "deferred_enrichment_task"):
                task = stored.get(key)
                if task is not None and not task.done():
                    task.cancel()
        for owner, pending in tuple(self.awaiting_scope_inputs.items()):
            if str(pending.get("plan_id") or "") == plan_id:
                self.awaiting_scope_inputs.pop(owner, None)
        self.allocator.release(plan_id)

    def _prowlarr_status_details(self, operation_id: str) -> dict:
        details = (self.operations.get(operation_id) or {}).get("details") or {}
        photo_url = str(details.get("photo_url") or "")
        return {"photo_url": photo_url} if photo_url.startswith("https://") else {}

    async def operation_control(self, request: dict) -> dict:
        operation_id = str(request.get("operation_id") or "")
        operation = self.operations.get(operation_id)
        if operation is None:
            raise FeatureError("not_found", "search operation was not found")
        if operation.get("state") in {"completed", "cancelled", "failed"}:
            return {"actions": [], "operation": self._operation_view(operation)}
        try:
            operation["revision"] = max(
                int(operation.get("revision") or 0),
                int(request.get("revision") or 0),
            )
        except (TypeError, ValueError):
            pass
        action = str(request.get("action") or "")
        if action not in {"exit", "cancel"}:
            raise FeatureError("invalid_control", "search control is invalid")
        owner = (operation["chat_id"], operation["user_id"])
        self.awaiting_queries.discard(owner)
        self.config_wizard.clear({"chat_id": owner[0], "user_id": owner[1]})
        plan_id = str(operation.get("plan_id") or "")
        if plan_id:
            self._log_completed_once(
                plan_id,
                self.plans.get(plan_id),
                terminal_status="cancelled",
            )
            self._release_plan(plan_id)
        task = operation.get("task")
        if task is not None and hasattr(task, "cancel") and not task.done():
            task.cancel()
        if operation.get("state") == "awaiting_input" or task is None:
            terminal = self._advance_operation(
                operation_id,
                state="cancelled",
                stage=operation.get("stage") or "cancelled",
                status_text="已退出搜索。",
                control="",
            )
            return {"actions": [], "operation": terminal}
        cancelling = self._advance_operation(
            operation_id,
            state="cancelling",
            stage=operation.get("stage") or "cancelling",
            status_text="正在取消搜索。",
            control="cancel",
        )
        return {"actions": [], "operation": cancelling}

    async def operation_snapshot(self, request: dict) -> dict:
        requested = str(request.get("operation_id") or "")
        terminal = {"completed", "cancelled", "failed", "handed_off"}
        return {"operations": [
            self._operation_view(operation)
            for operation_id, operation in self.operations.items()
            if operation.get("state") not in terminal
            and (not requested or operation_id == requested)
        ]}

    def _decorate_config_result(self, request, result):
        owner = self._owner_key(request)
        operation = self._operation_for_owner(owner)
        if operation is None:
            return result
        session = result.get("session") if isinstance(result, dict) else None
        if "config_patch" in result:
            view = self._advance_operation(
                operation["operation_id"],
                state="running",
                stage="config_apply",
                status_text="正在保存搜索配置。",
                control="cancel",
            )
        elif isinstance(session, dict) and session.get("state") == "open":
            wizard_session = self.config_wizard.sessions.get(owner) or {}
            view = self._advance_operation(
                operation["operation_id"],
                state="awaiting_input",
                stage=f"config_{wizard_session.get('stage') or 'input'}",
                status_text="输入搜索配置。",
                control="exit",
            )
        else:
            view = self._advance_operation(
                operation["operation_id"],
                state="cancelled",
                stage="config_cancelled",
                status_text="已退出搜索配置。",
                control="",
            )
        result["operation"] = view
        return result

    def _exit_owner_operation(self, request):
        owner = self._owner_key(request)
        operation = self._operation_for_owner(owner)
        if operation is None:
            return self._closed("⚠️ 搜索会话已失效。")
        self.awaiting_queries.discard(owner)
        plan_id = str(operation.get("plan_id") or "")
        if plan_id:
            self._log_completed_once(
                plan_id,
                self.plans.get(plan_id),
                terminal_status="cancelled",
            )
            self._release_plan(plan_id)
        view = self._advance_operation(
            operation["operation_id"],
            state="cancelled",
            stage=operation.get("stage") or "cancelled",
            status_text="已退出搜索。",
            control="",
        )
        result = self._closed("已退出 search 任务。")
        result["operation"] = view
        return result

    def _new_operation(
        self, request, *, state, stage, status_text, control, kind
    ):
        operation_id = uuid.uuid4().hex
        owner = self._owner_key(request)
        operation = {
            "operation_id": operation_id,
            "chat_id": owner[0],
            "user_id": owner[1],
            "state": state,
            "stage": stage,
            "status_text": status_text,
            "control": control,
            "revision": 1,
            "details": {},
            "kind": kind,
        }
        self.operations[operation_id] = operation
        self.owner_operations[owner] = operation_id
        return self._operation_view(operation)

    def _operation_for_owner(self, owner):
        operation_id = self.owner_operations.get(owner)
        return self.operations.get(operation_id) if operation_id else None

    def _advance_operation(
        self,
        operation_id,
        *,
        state,
        stage,
        status_text,
        control,
        details=None,
        next_plugin_id="",
    ):
        operation = self.operations[operation_id]
        operation.update({
            "state": state,
            "stage": stage,
            "status_text": status_text,
            "control": control,
            "revision": int(operation.get("revision") or 0) + 1,
            "next_plugin_id": next_plugin_id if state == "handed_off" else "",
        })
        if details is not None:
            operation["details"] = deepcopy(details)
        return self._operation_view(operation)

    async def _report_operation(self, operation_id, **changes):
        view = self._advance_operation(operation_id, **changes)
        if view["chat_id"] and view["user_id"]:
            operation = self.operations[operation_id]
            operation.pop("_host_report_rejected", None)
            plan_id = str(operation.get("plan_id") or "")
            try:
                response = await self.host.report_operation(view)
            except Exception as exc:
                operation["_host_report_rejected"] = True
                log_search_event(
                    runtime_context.logger,
                    "search.operation_report_failed",
                    search_session_id=plan_id,
                    level="warning",
                    operation_id=operation_id,
                    state=view["state"],
                    stage=view["stage"],
                    revision=view["revision"],
                    error_code=str(
                        getattr(exc, "code", "")
                        or type(exc).__name__
                    ),
                    error_type=type(exc).__name__,
                )
                raise
            if not isinstance(response, dict) or response.get("accepted") is not True:
                operation.update({
                    "state": "interrupted",
                    "status_text": "Host 未接受当前 Feature 的任务所有权。",
                    "control": "",
                    "next_plugin_id": "",
                    "_host_report_rejected": True,
                })
                log_search_event(
                    runtime_context.logger,
                    "search.operation_report_failed",
                    search_session_id=plan_id,
                    level="warning",
                    operation_id=operation_id,
                    state=view["state"],
                    stage=view["stage"],
                    revision=view["revision"],
                    error_code=str(
                        response.get("error_code")
                        if isinstance(response, dict)
                        else "invalid_response"
                    ),
                    error_type="operation_rejected",
                )
                raise FeatureError(
                    "operation_rejected",
                    "Host rejected search operation ownership",
                )
        return view

    @staticmethod
    def _operation_view(operation):
        view = {
            "operation_id": str(operation["operation_id"]),
            "chat_id": int(operation.get("chat_id") or 0),
            "user_id": int(operation.get("user_id") or 0),
            "state": str(operation.get("state") or ""),
            "stage": str(operation.get("stage") or ""),
            "status_text": str(operation.get("status_text") or ""),
            "control": str(operation.get("control") or ""),
            "revision": int(operation.get("revision") or 0),
            "details": deepcopy(operation.get("details") or {}),
        }
        if operation.get("next_plugin_id"):
            view["next_plugin_id"] = str(operation["next_plugin_id"])
        return view

    @staticmethod
    def _owner_key(request):
        return int(request.get("chat_id") or 0), int(request.get("user_id") or 0)

    @staticmethod
    def _closed(text: str):
        return {"actions": [{"kind": "send_message", "text": text}], "session": {"state": "close"}}
