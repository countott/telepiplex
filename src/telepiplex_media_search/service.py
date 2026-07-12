from __future__ import annotations

import asyncio
import re
import time
import uuid
from copy import deepcopy

from telepiplex_plugin_sdk import FeatureError
from telepiplex_plugin_sdk.media_metadata import resolve_category_route

from .adapters.prowlarr import resolve_prowlarr_download_url, search_prowlarr
from .adapters.tvdb import (
    TvdbConfigError,
    TvdbRequestError,
    get_tvdb_series_episodes,
    search_tvdb_movies,
    search_tvdb_series,
)
from .adapters.wikipedia import lookup_wikipedia_evidence
from .planner import SearchPlanningError, build_confirmable_search_plan
from .release_score import rank_releases
from .search_plan import TemporarySpecialAllocator, confirm_media_metadata


_LATIN = re.compile(r"[A-Za-z]")


class MediaSearchFeature:
    def __init__(
        self,
        *,
        config: dict,
        core,
        plan_builder=None,
        release_search=None,
        release_rank=None,
        release_resolver=None,
    ):
        self.config = config
        self.core = core
        self.allocator = TemporarySpecialAllocator()
        self.plan_builder = plan_builder or self._build_plan
        self.release_search = release_search or self._search_releases
        self.release_rank = release_rank or rank_releases
        self.release_resolver = release_resolver or resolve_prowlarr_download_url
        self.plans = {}
        self.awaiting_queries = set()

    async def command(self, request: dict) -> dict:
        command = str(request.get("command") or "")
        if command not in {"search", "s"}:
            raise FeatureError("not_found", f"unknown media-search command: {command}")
        raw_query = " ".join(str(item) for item in request.get("args") or []).strip()
        if not raw_query:
            self.awaiting_queries.add(self._owner_key(request))
            return {
                "actions": [{"kind": "send_message", "text": "请输入片名或影视条目链接。"}],
                "session": {"state": "open"},
            }
        return await self._prepare_plan(raw_query, request)

    async def message(self, request: dict) -> dict:
        key = self._owner_key(request)
        if key not in self.awaiting_queries:
            return {
                "actions": [{"kind": "send_message", "text": "⚠️ 搜索会话已失效。"}],
                "session": {"state": "close"},
            }
        self.awaiting_queries.discard(key)
        return await self._prepare_plan(str(request.get("text") or "").strip(), request)

    async def callback(self, request: dict) -> dict:
        payload = str(request.get("payload") or "")
        parts = payload.split(":")
        if len(parts) < 2:
            raise FeatureError("invalid_callback", "media-search callback is invalid")
        action, plan_id = parts[:2]
        stored = self.plans.get(plan_id)
        if not stored or stored["owner"] != self._owner_key(request):
            return self._closed("⚠️ 搜索任务已过期，请重新搜索。")
        if action == "cancel":
            self._release_plan(plan_id)
            return self._closed("已取消本次搜索。")
        if action == "confirm":
            return await self._confirm_and_search(plan_id, stored)
        if action == "release" and len(parts) == 3:
            return await self._submit_release(plan_id, stored, parts[2])
        raise FeatureError("invalid_callback", "media-search callback action is invalid")

    async def _prepare_plan(self, raw_query: str, request: dict) -> dict:
        if not raw_query:
            return self._closed("⚠️ 搜索内容不能为空。")
        plan_id = uuid.uuid4().hex[:10]
        try:
            plan = await self.plan_builder(raw_query, plan_id)
        except SearchPlanningError as exc:
            return self._closed(f"❌ 无法生成媒体元数据：{exc}")
        except Exception as exc:
            return self._closed(f"❌ 媒体规划失败：{type(exc).__name__}")
        route = resolve_category_route(
            self.config,
            (plan.get("media_metadata", {}).get("placement") or {}).get("category_kind"),
        )
        if not route:
            self.allocator.release(plan_id)
            return self._closed("❌ 媒体分类没有对应保存目录。")
        self.plans[plan_id] = {
            "owner": self._owner_key(request),
            "created_at": time.time(),
            "plan": plan,
            "selected_path": route["path"],
            "results": [],
        }
        contract = plan["media_metadata"]
        identity = contract["identity"]
        placement = contract["placement"]
        title = identity.get("chinese_title") or identity.get("english_title") or "未知"
        marker = placement.get("mapping_kind") or "unknown"
        return {
            "actions": [{
                "kind": "send_message",
                "text": f"媒体方案：{title} ({identity.get('year') or '年份未知'})\n映射：{marker}\n确认后搜索片源。",
                "data": {"keyboard": [[
                    {"text": "确认并搜索", "callback_data": f"media-search:confirm:{plan_id}"},
                    {"text": "取消", "callback_data": f"media-search:cancel:{plan_id}"},
                ]]},
            }],
            "session": {"state": "close"},
        }

    async def _confirm_and_search(self, plan_id: str, stored: dict) -> dict:
        plan = stored["plan"]
        contract = confirm_media_metadata(plan)
        query = self._english_prowlarr_query(plan, contract)
        media_type = str((contract.get("placement") or {}).get("library_type") or "")
        try:
            items = await asyncio.to_thread(self.release_search, query, media_type)
            limit = int((((self.config.get("search") or {}).get("prowlarr") or {}).get("result_limit") or 8))
            results = self.release_rank(items, limit)
        except Exception as exc:
            self._release_plan(plan_id)
            return self._closed(f"❌ Prowlarr 搜索失败：{type(exc).__name__}")
        if not results:
            self._release_plan(plan_id)
            return self._closed("⚠️ Prowlarr 未找到可用片源。")
        stored["confirmed_contract"] = contract
        stored["results"] = results
        keyboard = [[{
            "text": self._release_label(item, index),
            "callback_data": f"media-search:release:{plan_id}:{index}",
        }] for index, item in enumerate(results)]
        return {
            "actions": [{
                "kind": "edit_message",
                "text": f"找到 {len(results)} 个片源；Prowlarr 查询：{query}",
                "data": {"keyboard": keyboard},
            }]
        }

    async def _submit_release(self, plan_id: str, stored: dict, raw_index: str) -> dict:
        try:
            index = int(raw_index)
            item = stored["results"][index]
        except (ValueError, IndexError):
            raise FeatureError("invalid_release", "selected release is invalid") from None
        try:
            link = await asyncio.to_thread(self.release_resolver, item)
        except Exception as exc:
            return {"actions": [{"kind": "send_message", "text": f"❌ 无法解析下载链接：{type(exc).__name__}"}]}
        if not str(link).startswith("magnet:?"):
            return {"actions": [{"kind": "send_message", "text": "❌ 片源没有可用 magnet 链接。"}]}
        contract = deepcopy(stored["confirmed_contract"])
        identity = contract["identity"]
        result = await self.core.call_capability(
            "download.provider",
            "submit",
            {
                "link": link,
                "selected_path": stored["selected_path"],
                "user_id": stored["owner"][1],
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
            idempotency_key=f"{plan_id}:release:{index}",
        )
        self._release_plan(plan_id)
        return {
            "actions": [{
                "kind": "edit_message",
                "text": f"✅ 已提交下载任务：{result.get('job_id') or plan_id}",
            }]
        }

    async def _build_plan(self, raw_query: str, plan_id: str):
        providers = {
            "wikipedia": self._wikipedia_provider,
            "douban": lambda _hypotheses: {
                "source": "douban", "status": "disabled", "facts": [],
                "source_urls": [], "error": "not configured",
            },
            "tvdb": self._tvdb_provider,
        }
        return await build_confirmable_search_plan(
            raw_query,
            plan_id,
            providers,
            lambda contract: set((contract.get("evidence") or {}).get("occupied_special_numbers") or []),
            self.allocator,
        )

    def _wikipedia_provider(self, hypotheses: dict):
        config = (((self.config.get("metadata") or {}).get("wikipedia") or {}))
        if not config.get("enable", True):
            return {"source": "wikipedia", "status": "disabled", "facts": [], "source_urls": [], "error": ""}
        queries = ((hypotheses.get("source_queries") or {}).get("wikipedia") or [])
        return lookup_wikipedia_evidence(
            queries,
            languages=tuple(config.get("languages") or ["zh", "en"]),
            timeout=float(config.get("timeout") or 10),
        )

    def _tvdb_provider(self, hypotheses: dict):
        facts = []
        try:
            for hypothesis in hypotheses.get("hypotheses") or []:
                title = hypothesis.get("title") or ""
                year = hypothesis.get("year") or ""
                movies = search_tvdb_movies(title, year=year)[:5]
                series = search_tvdb_series(title, year=year)[:5]
                episodes = {
                    str(item.get("tvdb_series_id")): get_tvdb_series_episodes(str(item.get("tvdb_series_id")))
                    for item in series if item.get("tvdb_series_id")
                }
                if movies or series or any(episodes.values()):
                    facts.append({
                        "hypothesis": hypothesis,
                        "movies": movies,
                        "series": series,
                        "episodes_by_series": episodes,
                    })
        except TvdbConfigError as exc:
            return {"source": "tvdb", "status": "disabled", "facts": [], "source_urls": [], "error": str(exc)}
        except (TvdbRequestError, OSError) as exc:
            return {"source": "tvdb", "status": "server_down", "facts": [], "source_urls": [], "error": str(exc)}
        return {"source": "tvdb", "status": "ok" if facts else "not_found", "facts": facts, "source_urls": [], "error": ""}

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
    def _english_prowlarr_query(plan: dict, contract: dict) -> str:
        identity = contract.get("identity") or {}
        english = " ".join(str(identity.get("english_title") or "").split())
        year = " ".join(str(identity.get("year") or "").split())
        placement = contract.get("placement") or {}
        if placement.get("library_type") == "series":
            season = placement.get("season_number")
            episode = placement.get("episode_number")
            if season is None:
                first = next(iter(contract.get("items") or []), {})
                season = first.get("season_number")
                episode = first.get("episode_number")
            if english and _LATIN.search(english) and season is not None:
                marker = f"S{int(season):02d}"
                if episode is not None:
                    width = 2 if int(episode) < 100 else 3
                    marker += f"E{int(episode):0{width}d}"
                return f"{english} {marker}"
        if english and _LATIN.search(english):
            return " ".join(item for item in (english, year) if item)
        for query in plan.get("prowlarr_queries") or []:
            query = " ".join(str(query).split())
            if _LATIN.search(query):
                return query
        raise FeatureError("english_title_missing", "Prowlarr search requires an English title")

    def _release_plan(self, plan_id: str):
        self.plans.pop(plan_id, None)
        self.allocator.release(plan_id)

    @staticmethod
    def _release_label(item: dict, index: int) -> str:
        title = " ".join(str(item.get("title") or f"Result {index + 1}").split())
        return title[:48]

    @staticmethod
    def _owner_key(request):
        return int(request.get("chat_id") or 0), int(request.get("user_id") or 0)

    @staticmethod
    def _closed(text: str):
        return {"actions": [{"kind": "send_message", "text": text}], "session": {"state": "close"}}
