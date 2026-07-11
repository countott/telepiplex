# AI + Wikipedia Download Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every `/search` and `/s` request pass through two mandatory AI stages, enrich it with soft-failing Wikipedia/Douban/TVDB evidence, obtain one confirmed download plan, and let the renaming module safely consume official or temporary `S00E100+` placement without coupling search to Plex.

**Architecture:** Execute in an isolated `codex/ai-wikipedia-download-planner` worktree created from local `main`, because the feature crosses the independently packaged media-search and renaming modules. The search side creates and confirms an ephemeral `DownloadPlan` embedded in the existing metadata dictionary; the renaming side consumes only the confirmed subset and performs constrained post-download mapping. Wikipedia, Douban, and TVDB are evidence providers that may fail independently; both search-stage AI calls are mandatory.

**Tech Stack:** Python 3.12, `asyncio`, `requests`, `python-telegram-bot`, `unittest`, `pytest`, existing Telepiplex module registry and 115 storage provider.

## Global Constraints

- Execute from a new worktree based on local `main`; do not modify the current `feature/media-search` worktree in place.
- Keep search, renaming, and integration changes in separate commits so they can be reviewed or ported independently.
- Do not implement Plex scanning, Plex metadata writes, Plex retries, or imports from a Plex module.
- Use the neutral contract key `placement`, not `plex_placement`; Task 10 updates that single field name in the approved spec to reflect the final no-Plex-coupling decision without changing behavior.
- Do not create a persistent metadata database or write temporary plan state under `/config`.
- The first and second search-stage AI calls are mandatory; either failure stops before Prowlarr.
- Wikipedia, Douban, and TVDB are soft-failing evidence providers; any or all may report `server_down` while planning continues.
- A temporary related special requires a findable `source_entry`; API unavailability may yield `ai_supplied_unverified`, but a missing locator blocks temporary grouping.
- TVDB official specials keep their official number; temporary related specials reserve the first free number starting at `S00E100`.
- Temporary reservations live only in memory; restart means re-plan or move the unresolved content to the unorganized path.
- The user confirms exactly once before Prowlarr; selection of a Prowlarr release does not reopen media-type, relationship, destination, or episode decisions.
- After confirmation, downstream code may bind files to the plan but may not silently change the target series, library type, or episode number.
- Preserve unrelated Prowlarr progress-timer work and existing module contracts.
- Verification must include targeted tests, complete `unittest`, `pytest -q`, `py_compile`, `pip check`, and Telepiplex-aware `git diff --check`.

## Execution Topology

- Use `superpowers:using-git-worktrees` before Task 1.
- Create branch `codex/ai-wikipedia-download-planner` from local `main`.
- Cherry-pick design-only commit `d8a649f` into that worktree so the approved spec travels with the implementation.
- Do not merge or push during this plan. After Task 10, use `superpowers:finishing-a-development-branch` to choose integration and any module-branch synchronization.

---

### Task 1: Ephemeral DownloadPlan Contract and S00E100 Allocator

**Files:**
- Create: `app/utils/search_plan.py`
- Create: `tests/test_search_plan.py`

**Interfaces:**
- Consumes: second-stage AI JSON dictionaries and observed occupied episode numbers.
- Produces: `validate_draft_download_plan(value: object) -> dict | None`, `TemporarySpecialAllocator.reserve(plan_id: str, occupied: set[int]) -> int`, `TemporarySpecialAllocator.release(plan_id: str) -> None`, `finalize_download_plan(draft: dict, allocator: TemporarySpecialAllocator, occupied: set[int]) -> dict`, `confirm_download_plan(plan: dict) -> dict`, and `attach_download_plan(metadata: dict | None, plan: dict) -> dict`.

- [ ] **Step 1: Write failing contract and allocator tests**

```python
import unittest

from app.utils.search_plan import (
    TemporarySpecialAllocator,
    attach_download_plan,
    confirm_download_plan,
    finalize_download_plan,
    validate_draft_download_plan,
)


class SearchPlanTest(unittest.TestCase):
    def _draft(self):
        return {
            "schema_version": 1,
            "plan_id": "plan-a",
            "display_title": "想见你",
            "english_title": "Someday or One Day The Movie",
            "year": "2022",
            "content_identity": "extension_movie",
            "relation": {
                "type": "sequel",
                "target_series_title": "Someday or One Day",
                "target_series_year": "2019",
                "source": "wikipedia",
            },
            "placement": {
                "library_type": "series",
                "category_kind": "live_action_series",
                "season_number": 0,
                "episode_number": None,
                "mapping_kind": "temporary_related_special",
                "mapping_source": "local_allocator",
            },
            "source_entry": {
                "title": "想见你 (电影)",
                "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
                "provider": "wikipedia",
                "availability": "ok",
                "verification": "verified",
            },
            "prowlarr_queries": ["Someday or One Day The Movie 2022"],
            "evidence": {},
            "warnings": [],
            "confirmed": False,
        }

    def test_temporary_plan_requires_findable_source_entry(self):
        draft = self._draft()
        draft["source_entry"]["url"] = ""
        self.assertIsNone(validate_draft_download_plan(draft))

    def test_allocator_starts_at_100_and_skips_occupied_and_reserved(self):
        allocator = TemporarySpecialAllocator()
        self.assertEqual(allocator.reserve("plan-a", {100}), 101)
        self.assertEqual(allocator.reserve("plan-b", {100}), 102)
        self.assertEqual(allocator.reserve("plan-a", set()), 101)

    def test_finalize_then_confirm_and_attach_is_non_mutating(self):
        draft = self._draft()
        allocator = TemporarySpecialAllocator()
        final_plan = finalize_download_plan(draft, allocator, {100})
        confirmed = confirm_download_plan(final_plan)
        metadata = attach_download_plan({"source": "confirmed"}, confirmed)
        self.assertEqual(final_plan["placement"]["episode_number"], 101)
        self.assertFalse(final_plan["confirmed"])
        self.assertTrue(metadata["download_plan"]["confirmed"])
        self.assertEqual(draft["placement"]["episode_number"], None)

    def test_new_allocator_after_restart_has_no_old_reservations(self):
        allocator = TemporarySpecialAllocator()
        self.assertEqual(allocator.reserve("plan-a", set()), 100)
        restarted_allocator = TemporarySpecialAllocator()
        self.assertEqual(restarted_allocator.reserve("plan-b", set()), 100)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify the missing module failure**

Run: `python3 -m unittest tests.test_search_plan -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.utils.search_plan'`.

- [ ] **Step 3: Implement validation, reservation, finalization, and metadata attachment**

```python
# app/utils/search_plan.py
from __future__ import annotations

from copy import deepcopy
from threading import Lock


TEMPORARY_MAPPING_KIND = "temporary_related_special"
VALID_LIBRARY_TYPES = {"movie", "series"}
VALID_CATEGORY_KINDS = {
    "live_action_movie",
    "animated_movie",
    "live_action_series",
    "animated_series",
}


def _text(value) -> str:
    return " ".join(str(value or "").split())


def validate_draft_download_plan(value: object) -> dict | None:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return None
    if not _text(value.get("plan_id")) or not _text(value.get("display_title")):
        return None
    placement = value.get("placement")
    if not isinstance(placement, dict):
        return None
    if placement.get("library_type") not in VALID_LIBRARY_TYPES:
        return None
    if placement.get("category_kind") not in VALID_CATEGORY_KINDS:
        return None
    queries = value.get("prowlarr_queries")
    if not isinstance(queries, list) or not any(_text(item) for item in queries):
        return None
    if placement.get("mapping_kind") == TEMPORARY_MAPPING_KIND:
        if placement.get("season_number") != 0 or placement.get("episode_number") is not None:
            return None
        source_entry = value.get("source_entry")
        if not isinstance(source_entry, dict):
            return None
        if not _text(source_entry.get("title")):
            return None
        if not (_text(source_entry.get("url")) or _text(source_entry.get("external_id"))):
            return None
    result = deepcopy(value)
    result["confirmed"] = False
    return result


class TemporarySpecialAllocator:
    def __init__(self):
        self._lock = Lock()
        self._reservations: dict[str, int] = {}

    def reserve(self, plan_id: str, occupied: set[int]) -> int:
        with self._lock:
            if plan_id in self._reservations:
                return self._reservations[plan_id]
            unavailable = {int(item) for item in occupied if int(item) >= 100}
            unavailable.update(self._reservations.values())
            candidate = 100
            while candidate in unavailable:
                candidate += 1
            self._reservations[plan_id] = candidate
            return candidate

    def release(self, plan_id: str) -> None:
        with self._lock:
            self._reservations.pop(plan_id, None)


def finalize_download_plan(
    draft: dict,
    allocator: TemporarySpecialAllocator,
    occupied: set[int],
) -> dict:
    validated = validate_draft_download_plan(draft)
    if validated is None:
        raise ValueError("invalid draft download plan")
    placement = validated["placement"]
    if placement.get("mapping_kind") == TEMPORARY_MAPPING_KIND:
        placement["episode_number"] = allocator.reserve(validated["plan_id"], occupied)
    return validated


def confirm_download_plan(plan: dict) -> dict:
    confirmed = deepcopy(plan)
    confirmed["confirmed"] = True
    return confirmed


def attach_download_plan(metadata: dict | None, plan: dict) -> dict:
    result = deepcopy(metadata) if isinstance(metadata, dict) else {}
    result["download_plan"] = deepcopy(plan)
    return result
```

- [ ] **Step 4: Run the focused test**

Run: `python3 -m unittest tests.test_search_plan -v`

Expected: 4 tests, all PASS.

- [ ] **Step 5: Commit the contract**

```bash
git add app/utils/search_plan.py tests/test_search_plan.py
git commit -m "feat: add ephemeral download plan contract"
```

---

### Task 2: Wikipedia Evidence Adapter

**Files:**
- Create: `app/adapters/wikipedia.py`
- Create: `tests/test_wikipedia_adapter.py`

**Interfaces:**
- Consumes: query strings from first-stage AI and optional language order.
- Produces: `lookup_wikipedia_evidence(queries: list[str], languages: tuple[str, ...] = ("zh", "en"), timeout: float = 10) -> dict` with `source`, `status`, `facts`, `source_urls`, and `error`.

- [ ] **Step 1: Write failing adapter tests**

```python
import unittest
from unittest.mock import Mock, patch

from app.adapters.wikipedia import lookup_wikipedia_evidence


class WikipediaAdapterTest(unittest.TestCase):
    @patch("app.adapters.wikipedia.requests.get")
    def test_returns_extract_and_findable_page_url(self, get_mock):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "query": {
                "pages": {
                    "1": {
                        "title": "想見你 (電影)",
                        "extract": "2022年上映，為電視劇《想見你》的同名續篇電影。",
                        "pageprops": {"wikibase_item": "Q115000000"},
                        "fullurl": "https://zh.wikipedia.org/wiki/想見你_(電影)",
                    }
                }
            }
        }
        get_mock.return_value = response

        result = lookup_wikipedia_evidence(["想见你 电影 2022"], languages=("zh",))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["facts"][0]["wikibase_item"], "Q115000000")
        self.assertIn("续篇电影", result["facts"][0]["extract"])
        self.assertEqual(result["source_urls"], ["https://zh.wikipedia.org/wiki/想見你_(電影)"])

    @patch("app.adapters.wikipedia.requests.get", side_effect=OSError("dns failed"))
    def test_server_failure_is_soft_evidence(self, _get_mock):
        result = lookup_wikipedia_evidence(["想见你"], languages=("zh",))
        self.assertEqual(result["status"], "server_down")
        self.assertEqual(result["facts"], [])
        self.assertIn("dns failed", result["error"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify the missing module failure**

Run: `python3 -m unittest tests.test_wikipedia_adapter -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.adapters.wikipedia'`.

- [ ] **Step 3: Implement the MediaWiki Action API adapter**

```python
# app/adapters/wikipedia.py
from __future__ import annotations

from urllib.parse import quote

import requests


USER_AGENT = "Telepiplex/1.0 (media metadata lookup)"


def _empty(status: str, error: str = "") -> dict:
    return {
        "source": "wikipedia",
        "status": status,
        "facts": [],
        "source_urls": [],
        "error": str(error or ""),
    }


def lookup_wikipedia_evidence(
    queries: list[str],
    languages: tuple[str, ...] = ("zh", "en"),
    timeout: float = 10,
) -> dict:
    cleaned_queries = [" ".join(str(item or "").split()) for item in queries]
    cleaned_queries = [item for item in cleaned_queries if item]
    if not cleaned_queries:
        return _empty("not_found")

    facts = []
    urls = []
    last_error = ""
    for language in languages:
        endpoint = f"https://{language}.wikipedia.org/w/api.php"
        for query in cleaned_queries:
            try:
                response = requests.get(
                    endpoint,
                    params={
                        "action": "query",
                        "generator": "search",
                        "gsrsearch": query,
                        "gsrlimit": 5,
                        "prop": "extracts|pageprops|info",
                        "exintro": 1,
                        "explaintext": 1,
                        "inprop": "url",
                        "format": "json",
                        "formatversion": 2,
                    },
                    headers={"User-Agent": USER_AGENT},
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                last_error = str(exc)
                continue

            pages = ((payload or {}).get("query") or {}).get("pages") or []
            if isinstance(pages, dict):
                pages = list(pages.values())
            for page in pages:
                if not isinstance(page, dict):
                    continue
                title = " ".join(str(page.get("title") or "").split())
                extract = " ".join(str(page.get("extract") or "").split())
                if not title or not extract:
                    continue
                page_url = str(page.get("fullurl") or "").strip()
                if not page_url:
                    page_url = f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
                facts.append(
                    {
                        "language": language,
                        "query": query,
                        "title": title,
                        "extract": extract,
                        "url": page_url,
                        "wikibase_item": str((page.get("pageprops") or {}).get("wikibase_item") or ""),
                    }
                )
                if page_url not in urls:
                    urls.append(page_url)
        if facts:
            break

    if facts:
        return {
            "source": "wikipedia",
            "status": "ok",
            "facts": facts,
            "source_urls": urls,
            "error": "",
        }
    return _empty("server_down" if last_error else "not_found", last_error)
```

- [ ] **Step 4: Run the focused test**

Run: `python3 -m unittest tests.test_wikipedia_adapter -v`

Expected: 2 tests, all PASS.

- [ ] **Step 5: Commit the adapter**

```bash
git add app/adapters/wikipedia.py tests/test_wikipedia_adapter.py
git commit -m "feat: add Wikipedia evidence adapter"
```

---

### Task 3: Mandatory Two-Stage AI Contracts

**Files:**
- Modify: `app/utils/ai.py`
- Create: `tests/test_search_ai_pipeline.py`

**Interfaces:**
- Consumes: raw user input for stage one; evidence context for stage two.
- Produces: `infer_search_hypotheses_with_ai(raw_query: str) -> dict | None` and `infer_download_plan_with_ai(context: dict) -> dict | None`.

- [ ] **Step 1: Write failing prompt-boundary tests**

```python
import unittest
from unittest.mock import patch

from app.utils.ai import infer_download_plan_with_ai, infer_search_hypotheses_with_ai


class SearchAiPipelineTest(unittest.TestCase):
    @patch("app.utils.ai.check_ai_api_available", return_value=True)
    @patch("app.utils.ai.chat_completion")
    def test_stage_one_returns_source_queries_without_prowlarr_query(self, chat_mock, _available):
        chat_mock.return_value = {
            "choices": [{"message": {"content": '{"status":"ok","hypotheses":[],"source_queries":{"wikipedia":["想见你 电影"],"douban":["想见你"],"tvdb":["Someday or One Day"]},"warnings":[]}'}}]
        }
        result = infer_search_hypotheses_with_ai("想见你")
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("prowlarr_query", result)
        self.assertIn("wikipedia", result["source_queries"])

    @patch("app.utils.ai.check_ai_api_available", return_value=True)
    @patch("app.utils.ai.chat_completion")
    def test_stage_two_accepts_all_sources_down_and_returns_draft(self, chat_mock, _available):
        chat_mock.return_value = {
            "choices": [{"message": {"content": '{"schema_version":1,"plan_id":"p1","display_title":"想见你","english_title":"Someday or One Day The Movie","year":"2022","content_identity":"extension_movie","relation":{"type":"sequel","target_series_title":"Someday or One Day","target_series_year":"2019","source":"ai"},"placement":{"library_type":"series","category_kind":"live_action_series","season_number":0,"episode_number":null,"mapping_kind":"temporary_related_special","mapping_source":"local_allocator"},"source_entry":{"title":"想见你 (电影)","url":"https://zh.wikipedia.org/wiki/想見你_(電影)","provider":"wikipedia","availability":"server_down","verification":"ai_supplied_unverified"},"prowlarr_queries":["Someday or One Day The Movie 2022"],"evidence":{},"warnings":["Wikipedia 未实时验证"],"confirmed":false}'}}]
        }
        result = infer_download_plan_with_ai({"sources": [{"source": "tvdb", "status": "server_down"}]})
        self.assertEqual(result["placement"]["mapping_kind"], "temporary_related_special")
        self.assertIsNone(result["placement"]["episode_number"])

    @patch("app.utils.ai.check_ai_api_available", return_value=False)
    def test_both_stages_fail_closed_without_ai(self, _available):
        self.assertIsNone(infer_search_hypotheses_with_ai("想见你"))
        self.assertIsNone(infer_download_plan_with_ai({"sources": []}))

    @patch("app.utils.ai.check_ai_api_available", return_value=True)
    @patch("app.utils.ai.chat_completion")
    def test_ai_inferred_tvdb_episode_keeps_explicit_warning(self, chat_mock, _available):
        chat_mock.return_value = {
            "choices": [{"message": {"content": '{"schema_version":1,"plan_id":"p2","display_title":"想见你","english_title":"Someday or One Day The Movie","year":"2022","content_identity":"extension_movie","relation":{"type":"sequel","target_series_title":"Someday or One Day","target_series_year":"2019","source":"ai"},"placement":{"library_type":"series","category_kind":"live_action_series","season_number":0,"episode_number":5,"mapping_kind":"ai_inferred_tvdb","mapping_source":"ai_only"},"source_entry":{"title":"想见你 (电影)","url":"https://zh.wikipedia.org/wiki/想見你_(電影)","provider":"wikipedia","availability":"server_down","verification":"ai_supplied_unverified"},"prowlarr_queries":["Someday or One Day The Movie 2022"],"evidence":{},"warnings":["S00E05 仅由 AI 推断，未实时通过 TVDB 校验"],"confirmed":false}'}}]
        }
        result = infer_download_plan_with_ai({"sources": [{"source": "tvdb", "status": "server_down"}]})
        self.assertEqual(result["placement"]["episode_number"], 5)
        self.assertIn("未实时通过 TVDB 校验", result["warnings"][0])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify missing function imports**

Run: `python3 -m unittest tests.test_search_ai_pipeline -v`

Expected: FAIL because `infer_search_hypotheses_with_ai` and `infer_download_plan_with_ai` do not exist.

- [ ] **Step 3: Add complete stage-one and stage-two prompt contracts**

```python
# Add to app/utils/ai.py above the function definitions.
SEARCH_HYPOTHESIS_PROMPT = """你是影视搜索检索假设生成器。只返回JSON。
每次搜索必须执行本步骤。区分用户明确表达与模型推断；保留同名电影/剧集歧义。
输出 status、hypotheses、source_queries 和 warnings。source_queries 必须分别包含 wikipedia、douban、tvdb 数组。
不得输出 Prowlarr query，不得冻结最终目录，不得分配临时 S00E100+。
JSON结构：
{"status":"ok|blocked","hypotheses":[{"title":"string","year":"string","content_identity":"movie|series|main_episode|ova|narrative_bonus|non_narrative_extra|special|prequel_movie|sequel_movie|extension_movie|spin_off","scope":"movie|whole_series|season|episode","season_number":null,"episode_number":null,"possible_related_series":["string"],"explicit_facts":["string"],"inferred_facts":["string"]}],"source_queries":{"wikipedia":["string"],"douban":["string"],"tvdb":["string"]},"warnings":["string"]}
用户输入：
"""

DOWNLOAD_PLAN_PROMPT = """你是影视下载方案规划器。只返回JSON。
AI是主决策层；Wikipedia、豆瓣和TVDB仅提供可选证据，全部server_down时仍须规划。
临时关联特别篇必须提供可定位source_entry；找不到标题加URL或外部ID时不得选择temporary_related_special。
TVDB官方Special优先；强叙事关系可归入主线剧集Season 00；弱演员或主创关系不得合并。
AI推断TVDB具体编号必须添加未实时校验警告。temporary_related_special的episode_number必须为null，由确定性分配器填写。
把所有证据中已知的Season 00集号写入evidence.occupied_special_numbers整数数组，供确定性分配器避让。
输出schema_version=1、plan_id、标题、content_identity、relation、placement、source_entry、prowlarr_queries、evidence、warnings、confirmed=false。
输入事实：
"""
```

- [ ] **Step 4: Add the two AI entry points using the existing sanitized logging and parser**

```python
def infer_search_hypotheses_with_ai(raw_query: str):
    if not check_ai_api_available():
        return None
    prompt = SEARCH_HYPOTHESIS_PROMPT + str(raw_query or "").strip()
    _log_ai_info(f"AI搜索假设输入 raw={_compact_json_for_log(raw_query)}")
    result = chat_completion(prompt, max_tokens=4096)
    _log_ai_info(f"AI搜索假设原始响应 result={_compact_json_for_log(result)}")
    parsed = parse_ai_json_response(result)
    if not isinstance(parsed, dict):
        return None
    source_queries = parsed.get("source_queries")
    if not isinstance(source_queries, dict):
        return None
    for name in ("wikipedia", "douban", "tvdb"):
        if not isinstance(source_queries.get(name), list):
            return None
    parsed.pop("prowlarr_query", None)
    return parsed


def infer_download_plan_with_ai(context: dict):
    if not check_ai_api_available():
        return None
    prompt = DOWNLOAD_PLAN_PROMPT + json.dumps(context or {}, ensure_ascii=False, indent=2)
    _log_ai_info(f"AI下载方案输入 context={_compact_json_for_log(context)}")
    result = chat_completion(prompt, max_tokens=8192)
    _log_ai_info(f"AI下载方案原始响应 result={_compact_json_for_log(result)}")
    parsed = parse_ai_json_response(result)
    return parsed if isinstance(parsed, dict) else None
```

- [ ] **Step 5: Run the focused AI tests**

Run: `python3 -m unittest tests.test_search_ai_pipeline -v`

Expected: 4 tests, all PASS.

- [ ] **Step 6: Commit the AI contracts**

```bash
git add app/utils/ai.py tests/test_search_ai_pipeline.py
git commit -m "feat: add mandatory search planning AI stages"
```

---

### Task 4: Evidence Orchestration Service

**Files:**
- Create: `app/services/__init__.py`
- Create: `app/services/search_planner.py`
- Create: `tests/test_search_planner_service.py`

**Interfaces:**
- Consumes: raw query, injected evidence provider callables, occupied-number callback, and Task 1/3 AI functions.
- Produces: `SearchPlanningError`, `collect_evidence(hypotheses: dict, providers: dict[str, Callable]) -> list[dict]`, and `build_confirmable_plan(raw_query: str, plan_id: str, providers: dict[str, Callable], occupied_loader: Callable[[dict], set[int]], allocator: TemporarySpecialAllocator) -> dict`.

- [ ] **Step 1: Write failing orchestration tests**

```python
import unittest
from unittest.mock import Mock, patch

from app.services.search_planner import SearchPlanningError, build_confirmable_plan
from app.utils.search_plan import TemporarySpecialAllocator


class SearchPlannerServiceTest(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.search_planner.infer_download_plan_with_ai")
    @patch("app.services.search_planner.infer_search_hypotheses_with_ai")
    async def test_all_providers_run_and_soft_failures_reach_second_ai(self, hypothesis_mock, plan_mock):
        hypothesis_mock.return_value = {
            "status": "ok",
            "hypotheses": [],
            "source_queries": {"wikipedia": ["想见你"], "douban": ["想见你"], "tvdb": ["Someday or One Day"]},
            "warnings": [],
        }
        plan_mock.return_value = {
            "schema_version": 1,
            "plan_id": "plan-a",
            "display_title": "想见你",
            "english_title": "Someday or One Day The Movie",
            "year": "2022",
            "content_identity": "extension_movie",
            "relation": {"type": "sequel", "target_series_title": "Someday or One Day", "target_series_year": "2019", "source": "ai"},
            "placement": {"library_type": "series", "category_kind": "live_action_series", "season_number": 0, "episode_number": None, "mapping_kind": "temporary_related_special", "mapping_source": "local_allocator"},
            "source_entry": {"title": "想见你 (电影)", "url": "https://zh.wikipedia.org/wiki/想見你_(電影)", "provider": "wikipedia", "availability": "server_down", "verification": "ai_supplied_unverified"},
            "prowlarr_queries": ["Someday or One Day The Movie 2022"],
            "evidence": {},
            "warnings": ["未实时验证"],
            "confirmed": False,
        }
        providers = {
            "wikipedia": Mock(return_value={"source": "wikipedia", "status": "server_down", "facts": [], "source_urls": [], "error": "dns"}),
            "douban": Mock(return_value={"source": "douban", "status": "server_down", "facts": [], "source_urls": [], "error": "dns"}),
            "tvdb": Mock(return_value={"source": "tvdb", "status": "server_down", "facts": [], "source_urls": [], "error": "dns"}),
        }

        plan = await build_confirmable_plan(
            "想见你",
            "plan-a",
            providers,
            lambda _draft: {100},
            TemporarySpecialAllocator(),
        )

        self.assertEqual(plan["placement"]["episode_number"], 101)
        self.assertEqual(len(plan_mock.call_args.args[0]["sources"]), 3)
        for provider in providers.values():
            provider.assert_called_once()

    @patch("app.services.search_planner.infer_search_hypotheses_with_ai", return_value=None)
    async def test_missing_first_ai_raises_before_providers(self, _hypothesis_mock):
        provider = Mock()
        with self.assertRaisesRegex(SearchPlanningError, "ai_hypothesis_unavailable"):
            await build_confirmable_plan("想见你", "plan-a", {"wikipedia": provider}, lambda _draft: set(), TemporarySpecialAllocator())
        provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify the missing service failure**

Run: `python3 -m unittest tests.test_search_planner_service -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services'`.

- [ ] **Step 3: Implement provider isolation and mandatory AI sequencing**

```python
# app/services/search_planner.py
from __future__ import annotations

import asyncio
from collections.abc import Callable

from app.utils.ai import infer_download_plan_with_ai, infer_search_hypotheses_with_ai
from app.utils.search_plan import TemporarySpecialAllocator, finalize_download_plan


class SearchPlanningError(RuntimeError):
    pass


def _provider_failure(name: str, exc: Exception) -> dict:
    return {
        "source": name,
        "status": "server_down",
        "facts": [],
        "source_urls": [],
        "error": str(exc),
    }


async def collect_evidence(hypotheses: dict, providers: dict[str, Callable]) -> list[dict]:
    names = list(providers)
    tasks = [asyncio.to_thread(providers[name], hypotheses) for name in names]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    evidence = []
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            evidence.append(_provider_failure(name, result))
        elif isinstance(result, dict):
            evidence.append(result)
        else:
            evidence.append(_provider_failure(name, RuntimeError("invalid provider response")))
    return evidence


async def build_confirmable_plan(
    raw_query: str,
    plan_id: str,
    providers: dict[str, Callable],
    occupied_loader: Callable[[dict], set[int]],
    allocator: TemporarySpecialAllocator,
) -> dict:
    hypotheses = await asyncio.to_thread(infer_search_hypotheses_with_ai, raw_query)
    if not isinstance(hypotheses, dict):
        raise SearchPlanningError("ai_hypothesis_unavailable")
    sources = await collect_evidence(hypotheses, providers)
    context = {
        "raw_query": raw_query,
        "plan_id": plan_id,
        "hypotheses": hypotheses,
        "sources": sources,
    }
    draft = await asyncio.to_thread(infer_download_plan_with_ai, context)
    if not isinstance(draft, dict):
        raise SearchPlanningError("ai_download_plan_unavailable")
    draft["plan_id"] = plan_id
    occupied = set(occupied_loader(draft) or set())
    try:
        return finalize_download_plan(draft, allocator, occupied)
    except ValueError as exc:
        raise SearchPlanningError("invalid_download_plan") from exc
```

Create an empty `app/services/__init__.py` so imports remain package-safe.

- [ ] **Step 4: Run focused service tests**

Run: `python3 -m unittest tests.test_search_planner_service -v`

Expected: 2 tests, all PASS.

- [ ] **Step 5: Commit the service**

```bash
git add app/services/__init__.py app/services/search_planner.py tests/test_search_planner_service.py
git commit -m "feat: orchestrate AI planning and soft evidence"
```

---

### Task 5: One-Confirmation Telegram Search Flow

**Files:**
- Modify: `app/handlers/search_handler.py`
- Modify: `tests/test_media_metadata_fusion.py`
- Create: `tests/test_search_download_plan_flow.py`

**Interfaces:**
- Consumes: Task 4 `build_confirmable_plan`, current Douban/TVDB helpers, current configured categories, and Prowlarr results.
- Produces: `_build_download_plan_text(plan: dict) -> str`, `_resolve_plan_selected_path(plan: dict) -> str`, provider wrappers, one `plan_confirm` callback, and a `DownloadRequest.metadata["download_plan"]` payload.

- [ ] **Step 1: Write failing one-confirmation and dispatch tests**

```python
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.handlers import search_handler


class SearchDownloadPlanFlowTest(unittest.IsolatedAsyncioTestCase):
    def _plan(self):
        return {
            "schema_version": 1,
            "plan_id": "plan-a",
            "display_title": "想见你",
            "english_title": "Someday or One Day The Movie",
            "year": "2022",
            "content_identity": "extension_movie",
            "relation": {"type": "sequel", "target_series_title": "Someday or One Day", "target_series_year": "2019", "source": "wikipedia"},
            "placement": {"library_type": "series", "category_kind": "live_action_series", "season_number": 0, "episode_number": 100, "mapping_kind": "temporary_related_special", "mapping_source": "local_allocator"},
            "source_entry": {"title": "想见你 (电影)", "url": "https://zh.wikipedia.org/wiki/想見你_(電影)", "provider": "wikipedia", "availability": "ok", "verification": "verified"},
            "prowlarr_queries": ["Someday or One Day The Movie 2022"],
            "evidence": {},
            "warnings": [],
            "confirmed": False,
        }

    @patch.object(search_handler, "build_confirmable_plan", new_callable=AsyncMock)
    async def test_start_search_shows_one_full_plan_confirmation(self, planner_mock):
        planner_mock.return_value = self._plan()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        context = SimpleNamespace()

        state = await search_handler._start_entry_resolution(update, context, "想见你")

        self.assertEqual(state, search_handler.SEARCH_CONFIRM_DOWNLOAD_PLAN)
        text = update.message.reply_text.await_args.args[0]
        self.assertIn("S00E100", text)
        self.assertIn("来源条目", text)
        self.assertIn("Someday or One Day The Movie 2022", text)

    @patch.object(search_handler, "_resolve_selected_link", new_callable=AsyncMock, return_value="magnet:?xt=urn:btih:" + "a" * 40)
    @patch.object(search_handler, "_submit_download_request")
    async def test_release_pick_dispatches_without_directory_callback(self, submit_mock, _resolve_mock):
        task_id = "task-a"
        plan = self._plan()
        plan["confirmed"] = True
        search_handler.pending_search_tasks[task_id] = {
            "created_at": search_handler.time.time(),
            "user_id": 1,
            "query": plan["prowlarr_queries"][0],
            "selected_path": "/真人剧集",
            "download_plan": plan,
            "results": [{"title": "release", "magnet_url": "magnet:?xt=urn:btih:" + "a" * 40}],
            "metadata": {"source": "confirmed"},
            "naming_metadata": {"source": "confirmed"},
        }
        callback = SimpleNamespace(data=f"search_pick:{task_id}:0", answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(callback_query=callback, effective_user=SimpleNamespace(id=1))
        context = SimpleNamespace(user_data={}, application=SimpleNamespace(bot_data={}))

        state = await search_handler.select_search_result(update, context)

        self.assertEqual(state, search_handler.ConversationHandler.END)
        request = submit_mock.call_args.args[1]
        self.assertEqual(request.selected_path, "/真人剧集")
        self.assertTrue(request.metadata["download_plan"]["confirmed"])
```

- [ ] **Step 2: Run the focused test and verify missing flow symbols**

Run: `python3 -m unittest tests.test_search_download_plan_flow -v`

Expected: FAIL because `SEARCH_CONFIRM_DOWNLOAD_PLAN` and the new planning flow do not exist.

- [ ] **Step 3: Add planner imports, the allocator singleton, category resolution, and confirmation text**

```python
# Add imports in app/handlers/search_handler.py.
from app.adapters.wikipedia import lookup_wikipedia_evidence
from app.services.search_planner import SearchPlanningError, build_confirmable_plan
from app.utils.search_plan import (
    TemporarySpecialAllocator,
    attach_download_plan,
    confirm_download_plan,
)

SEARCH_SELECT_RESULT, SEARCH_CONFIRM_DOWNLOAD_PLAN = range(30, 32)
temporary_special_allocator = TemporarySpecialAllocator()


def _build_download_plan_text(plan: dict) -> str:
    placement = plan.get("placement") or {}
    relation = plan.get("relation") or {}
    source_entry = plan.get("source_entry") or {}
    episode = placement.get("episode_number")
    episode_number = int(episode) if episode is not None else None
    episode_width = 3 if episode_number is not None and episode_number >= 100 else 2
    episode_text = (
        f"S{int(placement.get('season_number') or 0):02d}E{episode_number:0{episode_width}d}"
        if episode_number is not None
        else "未分配"
    )
    lines = [
        "📋 下载方案",
        "",
        f"目标：{plan.get('display_title') or ''} / {plan.get('english_title') or ''} ({plan.get('year') or '年份未知'})",
        f"内容身份：{plan.get('content_identity') or 'unknown'}",
        f"关联剧集：{relation.get('target_series_title') or '无'}",
        f"关系依据：{relation.get('source') or 'ai'}",
        f"归属：{placement.get('library_type') or 'unknown'} / Season {int(placement.get('season_number') or 0):02d}",
        f"集号：{episode_text}",
        f"来源条目：{source_entry.get('title') or '无'}",
        f"搜索词：{(plan.get('prowlarr_queries') or [''])[0]}",
    ]
    for warning in plan.get("warnings") or []:
        lines.append(f"⚠️ {warning}")
    return "\n".join(lines)


def _resolve_plan_selected_path(plan: dict) -> str:
    expected_name = {
        "live_action_movie": "真人电影",
        "animated_movie": "动画电影",
        "live_action_series": "真人剧集",
        "animated_series": "动画剧集",
    }.get((plan.get("placement") or {}).get("category_kind"))
    if not expected_name:
        return ""
    for item in get_save_directories():
        if item.get("name") == expected_name and item.get("path"):
            return str(item["path"])
    return ""
```

- [ ] **Step 4: Add evidence-provider wrappers and replace candidate confirmation with plan confirmation**

```python
def _wikipedia_plan_provider(hypotheses: dict) -> dict:
    config = (((init.bot_config or {}).get("metadata") or {}).get("wikipedia") or {})
    if not config.get("enable", True):
        return {"source": "wikipedia", "status": "disabled", "facts": [], "source_urls": [], "error": ""}
    queries = ((hypotheses.get("source_queries") or {}).get("wikipedia") or [])
    languages = tuple(str(item) for item in (config.get("languages") or ["zh", "en"]) if str(item).strip())
    timeout = float(config.get("timeout") or 10)
    return lookup_wikipedia_evidence(queries, languages=languages, timeout=timeout)


def _douban_plan_provider(hypotheses: dict) -> dict:
    facts = []
    for query in ((hypotheses.get("source_queries") or {}).get("douban") or []):
        try:
            metadata = _fetch_douban_metadata_for_plain_query(query)
        except Exception as exc:
            return {"source": "douban", "status": "server_down", "facts": [], "source_urls": [], "error": str(exc)}
        if metadata:
            facts.append(metadata)
    return {"source": "douban", "status": "ok" if facts else "not_found", "facts": facts, "source_urls": [], "error": ""}


def _tvdb_plan_provider(hypotheses: dict) -> dict:
    facts = []
    try:
        for hypothesis in hypotheses.get("hypotheses") or []:
            title = hypothesis.get("title") or ""
            year = hypothesis.get("year") or ""
            movies = search_tvdb_movies(title, year=year)
            series = search_tvdb_series(title, year=year)
            episodes_by_series = {}
            for item in series[:5]:
                series_id = str(item.get("tvdb_series_id") or "")
                if series_id:
                    episodes_by_series[series_id] = get_tvdb_series_episodes(series_id)
            facts.append(
                {
                    "hypothesis": hypothesis,
                    "movies": movies[:5],
                    "series": series[:5],
                    "episodes_by_series": episodes_by_series,
                }
            )
    except TvdbConfigError as exc:
        return {"source": "tvdb", "status": "disabled", "facts": [], "source_urls": [], "error": str(exc)}
    except (TvdbRequestError, OSError) as exc:
        return {"source": "tvdb", "status": "server_down", "facts": [], "source_urls": [], "error": str(exc)}
    return {"source": "tvdb", "status": "ok" if facts else "not_found", "facts": facts, "source_urls": [], "error": ""}


def _occupied_special_numbers_from_draft(draft: dict) -> set[int]:
    values = ((draft.get("evidence") or {}).get("occupied_special_numbers") or [])
    occupied = set()
    for value in values:
        try:
            episode = int(value)
        except (TypeError, ValueError):
            continue
        if episode > 0:
            occupied.add(episode)
    return occupied


async def _start_entry_resolution(update, context, raw_query: str):
    plan_id = uuid.uuid4().hex[:10]
    providers = {
        "wikipedia": _wikipedia_plan_provider,
        "douban": _douban_plan_provider,
        "tvdb": _tvdb_plan_provider,
    }
    try:
        plan = await build_confirmable_plan(
            raw_query,
            plan_id,
            providers,
            _occupied_special_numbers_from_draft,
            temporary_special_allocator,
        )
    except SearchPlanningError as exc:
        await update.message.reply_text(f"❌ 无法生成下载方案：{exc}")
        return ConversationHandler.END
    selected_path = _resolve_plan_selected_path(plan)
    if not selected_path:
        temporary_special_allocator.release(plan_id)
        await update.message.reply_text("❌ 下载方案无法对应到已配置的保存目录。")
        return ConversationHandler.END
    pending_entry_confirmations[plan_id] = {
        "created_at": time.time(),
        "user_id": update.effective_user.id,
        "plan": plan,
        "selected_path": selected_path,
    }
    await update.message.reply_text(
        _build_download_plan_text(plan),
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("确认并搜索", callback_data=f"plan_confirm:{plan_id}"),
                InlineKeyboardButton("取消", callback_data=f"plan_cancel:{plan_id}"),
            ]]
        ),
        disable_web_page_preview=True,
    )
    return SEARCH_CONFIRM_DOWNLOAD_PLAN
```

- [ ] **Step 5: Confirm once, search with the frozen plan, and dispatch directly after release selection**

```python
async def confirm_download_plan_callback(update, context):
    callback = update.callback_query
    await callback.answer()
    action, plan_id = (callback.data or "").split(":", 1)
    task = get_pending_entry_confirmation(plan_id)
    if not task or not _owner_matches(task, update.effective_user.id):
        await callback.edit_message_text("⚠️ 下载方案已过期，请重新搜索。")
        return ConversationHandler.END
    if action == "plan_cancel":
        pending_entry_confirmations.pop(plan_id, None)
        temporary_special_allocator.release(plan_id)
        await callback.edit_message_text("已取消本次搜索。")
        return ConversationHandler.END
    plan = confirm_download_plan(task["plan"])
    pending_entry_confirmations.pop(plan_id, None)
    await callback.edit_message_text(f"✅ 已确认下载方案：{plan.get('display_title') or ''}")
    query = (plan.get("prowlarr_queries") or [""])[0]
    metadata = attach_download_plan({"source": "confirmed"}, plan)
    return await _send_search_results(
        update,
        context,
        query,
        naming_metadata={
            "source": "confirmed",
            "media_type": "series" if plan["placement"]["library_type"] == "series" else "movie",
            "chinese_title": plan.get("display_title") or "",
            "english_title": plan.get("english_title") or "",
            "year": plan.get("year") or "",
        },
        metadata=metadata,
        download_plan=plan,
        selected_path=task["selected_path"],
    )
```

Replace the pending-task expiry helpers so temporary reservations are released on timeout:

```python
def get_pending_search_task(task_id: str):
    task = pending_search_tasks.get(task_id)
    if not task:
        return None
    if time.time() - task.get("created_at", 0) > SEARCH_TASK_TTL_SECONDS:
        pending_search_tasks.pop(task_id, None)
        plan = task.get("download_plan") or {}
        temporary_special_allocator.release(str(plan.get("plan_id") or ""))
        return None
    return task


def get_pending_entry_confirmation(task_id: str):
    task = pending_entry_confirmations.get(task_id)
    if not task:
        return None
    if time.time() - task.get("created_at", 0) > SEARCH_TASK_TTL_SECONDS:
        pending_entry_confirmations.pop(task_id, None)
        temporary_special_allocator.release(task_id)
        return None
    return task
```

Extend `_send_search_results` with `download_plan=None` and `selected_path: str = ""`, preserve its existing Prowlarr progress behavior, and move pending-task construction into this complete helper:

```python
def _store_pending_search_task(
    update,
    query: str,
    results: list[dict],
    naming_metadata,
    metadata,
    download_plan,
    selected_path: str,
) -> str:
    task_id = uuid.uuid4().hex[:10]
    pending_search_tasks[task_id] = {
        "created_at": time.time(),
        "query": query,
        "results": results,
        "user_id": update.effective_user.id,
        "naming_metadata": naming_metadata,
        "metadata": metadata or (_metadata_from_naming_metadata(naming_metadata, query=query) if naming_metadata else None),
        "download_plan": deepcopy(download_plan) if isinstance(download_plan, dict) else None,
        "selected_path": selected_path,
    }
    return task_id
```

After Prowlarr returns ranked results, replace the existing inline dictionary assignment with:

```python
task_id = _store_pending_search_task(
    update,
    query,
    results,
    naming_metadata,
    metadata,
    download_plan,
    selected_path,
)
```

Import `deepcopy` from `copy`. Replace `select_search_result` with direct dispatch after the release choice:

```python
async def select_search_result(update, context):
    callback = update.callback_query
    await callback.answer()
    data = callback.data or ""
    if data.startswith("search_cancel:"):
        task_id = data.split(":", 1)[1]
        task = pending_search_tasks.pop(task_id, None) or {}
        plan = task.get("download_plan") or {}
        temporary_special_allocator.release(str(plan.get("plan_id") or ""))
        await callback.edit_message_text("已取消本次搜索。")
        return ConversationHandler.END

    _, task_id, index_text = data.split(":", 2)
    task = get_pending_search_task(task_id)
    if not task or not _owner_matches(task, update.effective_user.id):
        await callback.edit_message_text("⚠️ 搜索任务已过期，请重新发起搜索。")
        return ConversationHandler.END
    try:
        selected_item = task["results"][int(index_text)]
    except (IndexError, ValueError):
        await callback.edit_message_text("⚠️ 候选资源不可用，请重新搜索。")
        return ConversationHandler.END

    context.user_data["search_task_id"] = task_id
    context.user_data["search_selected_item"] = selected_item
    await callback.edit_message_text("⏳ 正在解析下载链接，请稍候。")
    try:
        link = await _resolve_selected_link(context)
    except ProwlarrRequestError as exc:
        await callback.edit_message_text(f"❌ {exc}")
        return ConversationHandler.END

    plan = task.get("download_plan") or {}
    metadata = attach_download_plan(_metadata_for_selected_release(task, selected_item), plan)
    naming_metadata = _naming_metadata_for_selected_release(task, selected_item)
    try:
        _submit_download_request(
            context,
            DownloadRequest(
                link=link,
                selected_path=task["selected_path"],
                user_id=update.effective_user.id,
                naming_metadata=naming_metadata,
                metadata=metadata,
                source="media-search",
            ),
        )
    except DownloadProviderUnavailable as exc:
        await callback.edit_message_text(f"❌ {exc}")
        return ConversationHandler.END

    pending_search_tasks.pop(task_id, None)
    await callback.edit_message_text("✅ 已加入下载队列。\n系统将按已确认下载方案处理，请稍后查看结果。")
    return ConversationHandler.END
```

Do not release a temporary number after successful dispatch: the search module has no reliable download-completion callback, so the conservative in-process rule is to retain dispatched reservations until restart. Release only on plan cancellation, confirmation timeout, Prowlarr failure before task creation, Prowlarr-result timeout, or search cancellation. In each `_send_search_results` exception/no-results branch, call:

```python
if isinstance(download_plan, dict):
    temporary_special_allocator.release(str(download_plan.get("plan_id") or ""))
```

Remove `SEARCH_SELECT_SUB_CATEGORY` from the media-search conversation states and delete `select_search_sub_category` from this handler; leave the separate `/magnet` save-path flow unchanged.

```python
states={
    SEARCH_CONFIRM_DOWNLOAD_PLAN: [
        CallbackQueryHandler(
            confirm_download_plan_callback,
            pattern=r"^plan_(confirm|cancel):",
        )
    ],
    SEARCH_SELECT_RESULT: [
        CallbackQueryHandler(select_search_result, pattern=r"^search_(pick|cancel):")
    ],
}
```

- [ ] **Step 6: Run focused search-flow regressions**

Run: `python3 -m unittest tests.test_search_download_plan_flow tests.test_media_metadata_fusion -v`

Expected: all tests PASS; no test enters `SEARCH_SELECT_SUB_CATEGORY` for `/search` or `/s`.

- [ ] **Step 7: Commit the Telegram flow**

```bash
git add app/handlers/search_handler.py tests/test_media_metadata_fusion.py tests/test_search_download_plan_flow.py
git commit -m "feat: confirm one AI download plan before search"
```

---

### Task 6: Wikipedia Configuration, Runtime Markers, and Search-Module Verification

**Files:**
- Modify: `app/config.yaml.example`
- Modify: `config/config.yaml.example`
- Modify: `app/modules/media_search.py`
- Create: `tests/test_media_search_config.py`

**Interfaces:**
- Consumes: `metadata.wikipedia` configuration.
- Produces: documented `enable`, `languages`, and `timeout` defaults; module registry declaration; startup/search logs proving two AI stages and source states.

- [ ] **Step 1: Write a failing config-contract test**

```python
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class MediaSearchConfigTest(unittest.TestCase):
    def test_both_templates_expose_wikipedia_soft_provider(self):
        for path in (ROOT / "app/config.yaml.example", ROOT / "config/config.yaml.example"):
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            wikipedia = payload["metadata"]["wikipedia"]
            self.assertTrue(wikipedia["enable"])
            self.assertEqual(wikipedia["languages"], ["zh", "en"])
            self.assertEqual(wikipedia["timeout"], 10)

    def test_media_search_module_declares_wikipedia_section(self):
        from app.core.module_registry import ModuleRegistry
        from app.modules.media_search import register_module

        registry = ModuleRegistry()
        register_module(registry)
        self.assertIn("metadata.wikipedia", registry.config_sections)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify missing config keys**

Run: `python3 -m unittest tests.test_media_search_config -v`

Expected: FAIL with missing `metadata.wikipedia`.

- [ ] **Step 3: Add identical Wikipedia defaults to both templates**

```yaml
metadata:
  wikipedia:
    enable: true
    languages:
      - zh
      - en
    timeout: 10
  tvdb:
    enable: false
    base_url: "https://api4.thetvdb.com/v4"
    api_key: ""
    subscriber_pin: ""
    timeout: 15
```

Update `app/modules/media_search.py`:

```python
def register_module(registry):
    registry.add_commands(
        [
            ("search", "搜索片源"),
            ("s", "搜索片源"),
        ]
    )
    registry.add_config_sections(["search.prowlarr", "metadata.wikipedia", "metadata.tvdb", "ai"])
    registry.add_handlers(_register_handlers)
```

- [ ] **Step 4: Add source-by-source status logging in the planning service**

```python
# In collect_evidence after building evidence.
try:
    import init
    for item in evidence:
        if init.logger:
            init.logger.info(
                "search_evidence "
                f"source={item.get('source')} status={item.get('status')} "
                f"facts={len(item.get('facts') or [])}"
            )
except Exception:
    pass
```

Log `ai_stage=hypothesis status=ok`, `ai_stage=download_plan status=ok`, `plan_id`, relationship source, and the final Prowlarr query without logging keys or full download URLs.

- [ ] **Step 5: Run the complete media-search branch test surface**

Run: `python3 -m unittest tests.test_search_plan tests.test_wikipedia_adapter tests.test_search_ai_pipeline tests.test_search_planner_service tests.test_search_download_plan_flow tests.test_media_metadata_fusion tests.test_media_search_surface tests.test_media_search_utils tests.test_tvdb_adapter tests.test_prowlarr_search_progress -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit config and observability**

```bash
git add app/config.yaml.example config/config.yaml.example app/modules/media_search.py app/services/search_planner.py tests/test_media_search_config.py
git commit -m "feat: configure Wikipedia search evidence"
```

---

### Task 7: Confirmed-Plan Consumer and Source-Whitelist Removal

**Files:**
- Create: `app/utils/confirmed_download_plan.py`
- Modify: `app/utils/media_naming.py`
- Create: `tests/test_confirmed_download_plan.py`
- Modify: `tests/test_media_auto_rename.py`

**Interfaces:**
- Consumes: `metadata["download_plan"]` created in Task 5.
- Produces: `extract_confirmed_download_plan(metadata: dict | None) -> dict | None`, `locked_episode(plan: dict) -> tuple[int, int] | None`, and field-complete generic naming independent of `source` labels.

- [ ] **Step 1: Write failing confirmed-plan and `source=confirmed` tests**

```python
import unittest

from app.utils.confirmed_download_plan import extract_confirmed_download_plan, locked_episode


class ConfirmedDownloadPlanTest(unittest.TestCase):
    def test_extracts_only_confirmed_schema_v1_plan(self):
        plan = {
            "schema_version": 1,
            "plan_id": "plan-a",
            "confirmed": True,
            "placement": {"library_type": "series", "season_number": 0, "episode_number": 100, "mapping_kind": "temporary_related_special"},
            "relation": {"target_series_title": "Someday or One Day"},
            "source_entry": {"title": "想见你 (电影)", "url": "https://zh.wikipedia.org/wiki/想見你_(電影)"},
        }
        extracted = extract_confirmed_download_plan({"download_plan": plan})
        self.assertEqual(locked_episode(extracted), (0, 100))
        plan["confirmed"] = False
        self.assertIsNone(extract_confirmed_download_plan({"download_plan": plan}))
```

Add to `tests/test_media_auto_rename.py`:

```python
def test_build_movie_plan_accepts_complete_confirmed_metadata(self):
    plan = build_media_naming_plan(
        {
            "source": "confirmed",
            "chinese_title": "想见你",
            "english_title": "Someday or One Day The Movie",
        },
        "Someday.or.One.Day.The.Movie.2022.2160p",
        "movie.mkv",
    )
    self.assertEqual(
        plan.target_relative_dir,
        "想见你 (Someday or One Day The Movie)",
    )
```

- [ ] **Step 2: Run focused tests and verify failures**

Run: `python3 -m unittest tests.test_confirmed_download_plan tests.test_media_auto_rename -v`

Expected: FAIL because the confirmed-plan helper is missing and `build_media_naming_plan` rejects `source=confirmed`.

- [ ] **Step 3: Implement the confirmed-plan reader**

```python
# app/utils/confirmed_download_plan.py
from __future__ import annotations

from copy import deepcopy


def extract_confirmed_download_plan(metadata: dict | None) -> dict | None:
    if not isinstance(metadata, dict):
        return None
    plan = metadata.get("download_plan")
    if not isinstance(plan, dict):
        return None
    if plan.get("schema_version") != 1 or plan.get("confirmed") is not True:
        return None
    placement = plan.get("placement")
    if not isinstance(placement, dict):
        return None
    if placement.get("library_type") not in {"movie", "series"}:
        return None
    if placement.get("mapping_kind") == "temporary_related_special":
        source_entry = plan.get("source_entry")
        if not isinstance(source_entry, dict):
            return None
        if not source_entry.get("title") or not (source_entry.get("url") or source_entry.get("external_id")):
            return None
    return deepcopy(plan)


def locked_episode(plan: dict) -> tuple[int, int] | None:
    placement = plan.get("placement") if isinstance(plan, dict) else None
    if not isinstance(placement, dict) or placement.get("library_type") != "series":
        return None
    try:
        season = int(placement.get("season_number"))
        episode = int(placement.get("episode_number"))
    except (TypeError, ValueError):
        return None
    return (season, episode) if season >= 0 and episode > 0 else None
```

- [ ] **Step 4: Replace the generic source whitelist with field validation**

```python
# In build_media_naming_plan in app/utils/media_naming.py.
metadata = metadata or {}
source = str(metadata.get("source") or "").strip()
chinese_folder = sanitize_path_name(metadata.get("chinese_title"))
english_folder = sanitize_path_name(metadata.get("english_title"))
if not english_folder and source in {"search_query", "filename"}:
    english_folder = infer_english_title_from_release(release_title)
if not chinese_folder and source == "filename":
    chinese_folder = english_folder
if not chinese_folder or not english_folder:
    return None
```

Remove only the `source not in {douban, search_query, filename}` early return. Keep all existing path sanitation, collection, episode, and filename behavior.

- [ ] **Step 5: Run focused tests**

Run: `python3 -m unittest tests.test_confirmed_download_plan tests.test_media_auto_rename -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit the downstream contract**

```bash
git add app/utils/confirmed_download_plan.py app/utils/media_naming.py tests/test_confirmed_download_plan.py tests/test_media_auto_rename.py
git commit -m "feat: consume confirmed download plans in renaming"
```

---

### Task 8: Plan-Locked Rename Builder with Partial Mapping

**Files:**
- Modify: `app/utils/tvdb_rename.py`
- Modify: `tests/test_tvdb_rename.py`

**Interfaces:**
- Consumes: confirmed plan, actual file tree, and AI episode map.
- Produces: `build_confirmed_rename_plan(final_path: str, selected_path: str, metadata: dict, confirmed_plan: dict, ai_plan: dict, file_tree: list[dict]) -> dict | None` containing `operations`, `unmatched_sources`, locked series/season/episode values, and warnings.

- [ ] **Step 1: Write failing temporary-special and partial-mapping tests**

```python
def test_confirmed_temporary_special_uses_locked_s00e100_without_tvdb_ids(self):
    plan = build_confirmed_rename_plan(
        final_path="/真人剧集/Raw.Release",
        selected_path="/真人剧集",
        metadata={"chinese_title": "想见你", "english_title": "Someday or One Day"},
        confirmed_plan={
            "schema_version": 1,
            "confirmed": True,
            "relation": {"target_series_title": "Someday or One Day"},
            "placement": {"library_type": "series", "season_number": 0, "episode_number": 100, "mapping_kind": "temporary_related_special"},
            "source_entry": {"title": "想见你 (电影)", "url": "https://zh.wikipedia.org/wiki/想見你_(電影)"},
        },
        ai_plan={"episode_map": [{"source_file": "Movie.mkv", "season_number": 0, "episode_number": 100}]},
        file_tree=[{"name": "Movie.mkv", "relative_path": "Movie.mkv", "is_dir": False}],
    )
    self.assertEqual(plan["operations"][0]["rename_to"], "Someday or One Day S00E100.mkv")
    self.assertEqual(plan["unmatched_sources"], [])


def test_confirmed_plan_allows_partial_mapping_and_reports_unmatched(self):
    plan = build_confirmed_rename_plan(
        final_path="/真人剧集/Raw.Release",
        selected_path="/真人剧集",
        metadata={"chinese_title": "测试剧", "english_title": "Test Show"},
        confirmed_plan={
            "schema_version": 1,
            "confirmed": True,
            "relation": {"target_series_title": "Test Show"},
            "placement": {"library_type": "series", "season_number": 1, "episode_number": 1, "mapping_kind": "tvdb_official"},
            "source_entry": {},
            "items": [
                {"content_role": "main_episode", "season_number": 1, "episode_number": 1},
                {"content_role": "ova", "season_number": 0, "episode_number": 3},
            ],
        },
        ai_plan={"episode_map": [
            {"source_file": "Main.mkv", "season_number": 1, "episode_number": 1},
            {"source_file": "OVA.mkv", "season_number": 0, "episode_number": 3},
        ]},
        file_tree=[
            {"name": "Main.mkv", "relative_path": "Main.mkv", "is_dir": False},
            {"name": "OVA.mkv", "relative_path": "OVA.mkv", "is_dir": False},
            {"name": "Unknown.mkv", "relative_path": "Unknown.mkv", "is_dir": False},
        ],
    )
    self.assertEqual(len(plan["operations"]), 2)
    self.assertEqual(
        {operation["rename_to"] for operation in plan["operations"]},
        {"Test Show S01E01.mkv", "Test Show S00E03.mkv"},
    )
    self.assertEqual(plan["unmatched_sources"], ["Unknown.mkv"])
```

Extend the imports at the top of `tests/test_tvdb_rename.py`:

```python
from app.utils.tvdb_rename import build_confirmed_rename_plan
```

- [ ] **Step 2: Run the targeted tests and verify the missing builder**

Run: `python3 -m unittest tests.test_tvdb_rename -v`

Expected: FAIL because `build_confirmed_rename_plan` is missing.

- [ ] **Step 3: Implement locked mapping and unmatched reporting**

```python
def build_confirmed_rename_plan(
    final_path: str,
    selected_path: str,
    metadata: dict,
    confirmed_plan: dict,
    ai_plan: dict,
    file_tree: list[dict],
) -> dict | None:
    placement = confirmed_plan.get("placement") or {}
    relation = confirmed_plan.get("relation") or {}
    if confirmed_plan.get("confirmed") is not True or placement.get("library_type") != "series":
        return None
    allowed_targets = set()
    for item in confirmed_plan.get("items") or []:
        try:
            target = (int(item.get("season_number")), int(item.get("episode_number")))
        except (TypeError, ValueError):
            continue
        if target[0] >= 0 and target[1] > 0:
            allowed_targets.add(target)
    if not allowed_targets:
        try:
            target = (int(placement.get("season_number")), int(placement.get("episode_number")))
        except (TypeError, ValueError):
            return None
        if target[0] < 0 or target[1] < 1:
            return None
        allowed_targets.add(target)

    source_lookup = _source_index(file_tree)
    source_video_paths = {node["relative_path"] for node in _video_file_nodes(file_tree)}
    series_name = sanitize_path_name(
        relation.get("target_series_title")
        or metadata.get("english_title")
        or metadata.get("query")
    )
    chinese_title = sanitize_path_name(metadata.get("chinese_title"))
    if not series_name:
        return None
    target_root = _join_path(selected_path, _display_folder(chinese_title, series_name))
    operations = []
    seen_sources = set()
    seen_targets = set()
    for item in ai_plan.get("episode_map") or []:
        source_file = _clean_path(item.get("source_file") or "")
        source_node = source_lookup.get(source_file)
        if not source_node:
            continue
        season_number = int(item.get("season_number") or 0)
        episode_number = int(item.get("episode_number") or 0)
        if (season_number, episode_number) not in allowed_targets:
            continue
        source_relative_path = source_node["relative_path"]
        if source_relative_path in seen_sources:
            continue
        marker = _episode_marker_text(season_number, episode_number)
        suffix = PurePosixPath(source_relative_path).suffix
        rename_to = f"{series_name} {marker}{suffix}"
        target_dir = _join_path(target_root, f"{series_name} Season {season_number:02d}")
        target_key = _join_path(target_dir, rename_to)
        if target_key in seen_targets:
            continue
        seen_sources.add(source_relative_path)
        seen_targets.add(target_key)
        source_path = _join_path(final_path, source_relative_path)
        source_parent = "/".join(source_relative_path.split("/")[:-1])
        operations.append(
            {
                "source_relative_path": source_relative_path,
                "source_path": source_path,
                "rename_to": rename_to,
                "renamed_source_path": _join_path(final_path, source_parent, rename_to),
                "target_dir": target_dir,
            }
        )
    if not operations:
        return None
    return {
        "target_root": target_root,
        "series_name": series_name,
        "operations": operations,
        "unmatched_sources": sorted(source_video_paths - seen_sources),
        "warnings": [str(item) for item in confirmed_plan.get("warnings") or [] if str(item).strip()],
    }
```

- [ ] **Step 4: Run existing and new rename-plan tests**

Run: `python3 -m unittest tests.test_tvdb_rename -v`

Expected: all tests PASS; existing TVDB-validated rename behavior remains unchanged.

- [ ] **Step 5: Commit the pure rename builder**

```bash
git add app/utils/tvdb_rename.py tests/test_tvdb_rename.py
git commit -m "feat: build plan-locked special rename operations"
```

---

### Task 9: Constrained Post-Download AI and Unmatched-File Fallback

**Files:**
- Modify: `app/utils/ai.py`
- Modify: `app/modules/renaming.py`
- Modify: `tests/test_composable_renaming.py`

**Interfaces:**
- Consumes: confirmed plan from Task 7, actual storage file tree, optional TVDB evidence, and Task 8 pure builder.
- Produces: mapped files in the confirmed target, unmatched files moved under `media.unorganized_path`, and no dependency on a Plex module.

- [ ] **Step 1: Write failing processor tests for TVDB-down temporary mapping and unmatched fallback**

Extend the test imports first:

```python
from unittest.mock import Mock, patch
```

```python
def test_confirmed_temporary_special_runs_when_tvdb_is_down(self):
    import init
    from app.core.module_registry import DownloadCompletedEvent
    from app.modules import renaming

    init.logger = Mock()
    init.bot_config = {
        "ai": {"api_url": "https://ai.example", "api_key": "key", "model": "model"},
        "media": {"unorganized_path": "/未整理"},
    }
    storage = Mock()
    storage.create_dir_recursive.return_value = True
    storage.rename.return_value = True
    storage.move_file.return_value = True
    storage.get_file_info.return_value = None
    event = DownloadCompletedEvent(
        link="magnet:?xt=urn:btih:" + "b" * 40,
        selected_path="/真人剧集",
        user_id=1,
        final_path="/真人剧集/Raw.Release",
        resource_name="Someday.or.One.Day.The.Movie.2022",
        metadata={
            "chinese_title": "想见你",
            "english_title": "Someday or One Day",
            "download_plan": {
                "schema_version": 1,
                "confirmed": True,
                "relation": {"target_series_title": "Someday or One Day"},
                "placement": {"library_type": "series", "season_number": 0, "episode_number": 100, "mapping_kind": "temporary_related_special"},
                "source_entry": {"title": "想见你 (电影)", "url": "https://zh.wikipedia.org/wiki/想見你_(電影)"},
            },
        },
        storage=storage,
    )
    rename_plan = {
        "target_root": "/真人剧集/想见你 (Someday or One Day)",
        "series_name": "Someday or One Day",
        "operations": [{
            "target_dir": "/真人剧集/想见你 (Someday or One Day)/Someday or One Day Season 00",
            "source_path": "/真人剧集/Raw.Release/Movie.mkv",
            "rename_to": "Someday or One Day S00E100.mkv",
            "renamed_source_path": "/真人剧集/Raw.Release/Someday or One Day S00E100.mkv",
        }],
        "unmatched_sources": ["Bonus.mkv"],
        "warnings": [],
    }

    with patch.object(renaming, "collect_storage_file_tree", return_value=[
        {"name": "Movie.mkv", "relative_path": "Movie.mkv", "is_dir": False},
        {"name": "Bonus.mkv", "relative_path": "Bonus.mkv", "is_dir": False},
    ]), patch.object(renaming, "infer_tvdb_episode_plan_with_ai", return_value={
        "episode_map": [{"source_file": "Movie.mkv", "season_number": 0, "episode_number": 100}]
    }), patch.object(renaming, "build_confirmed_rename_plan", return_value=rename_plan), patch.object(
        renaming, "_get_tvdb_candidates_and_episodes", return_value=([], [])
    ):
        result = renaming.process_tvdb_episode(event)

    self.assertTrue(result.handled)
    self.assertTrue(result.should_stop)
    storage.create_dir_recursive.assert_any_call("/未整理/Raw.Release")
    storage.move_file.assert_any_call("/真人剧集/Raw.Release/Bonus.mkv", "/未整理/Raw.Release")

def test_confirmed_target_conflict_is_reported_before_any_move(self):
    from app.modules.renaming import ConfirmedPlanConflict, _assert_no_target_conflicts

    storage = Mock()
    storage.get_file_info.return_value = {"file_id": "occupied"}
    rename_plan = {
        "operations": [{
            "target_dir": "/真人剧集/想见你 (Someday or One Day)/Someday or One Day Season 00",
            "rename_to": "Someday or One Day S00E100.mkv",
        }]
    }

    with self.assertRaisesRegex(ConfirmedPlanConflict, "S00E100"):
        _assert_no_target_conflicts(storage, rename_plan)

    storage.rename.assert_not_called()
    storage.move_file.assert_not_called()

def test_confirmed_mapping_failure_moves_source_directory_to_unorganized(self):
    import init
    from app.core.module_registry import DownloadCompletedEvent
    from app.modules import renaming

    init.logger = Mock()
    init.bot_config = {
        "ai": {"api_url": "https://ai.example", "api_key": "key", "model": "model"},
        "media": {"unorganized_path": "/未整理"},
    }
    storage = Mock()
    storage.create_dir_recursive.return_value = True
    storage.move_file.return_value = True
    event = DownloadCompletedEvent(
        link="magnet:?xt=urn:btih:" + "c" * 40,
        selected_path="/真人剧集",
        user_id=1,
        final_path="/真人剧集/Raw.Failed",
        resource_name="Raw.Failed",
        metadata={"download_plan": {
            "schema_version": 1,
            "confirmed": True,
            "relation": {"target_series_title": "Someday or One Day"},
            "placement": {"library_type": "series", "season_number": 0, "episode_number": 100, "mapping_kind": "temporary_related_special"},
            "source_entry": {"title": "想见你 (电影)", "url": "https://zh.wikipedia.org/wiki/想見你_(電影)"},
        }},
        storage=storage,
    )

    with patch.object(renaming, "_attempt_tvdb_ai_episode_rename", return_value=None):
        result = renaming.process_tvdb_episode(event)

    self.assertTrue(result.handled)
    self.assertEqual(result.final_path, "/未整理/Raw.Failed")
    storage.move_file.assert_called_once_with("/真人剧集/Raw.Failed", "/未整理")
```

- [ ] **Step 2: Run the processor test and verify current TVDB prerequisite failure**

Run: `python3 -m unittest tests.test_composable_renaming -v`

Expected: FAIL because the current processor returns before AI mapping when TVDB candidates are unavailable.

- [ ] **Step 3: Extend the episode-mapping AI prompt with confirmed-plan locks**

Add these hard rules to `TVDB_EPISODE_PLAN_PROMPT` in `app/utils/ai.py`:

```python
8. 如果输入包含 confirmed_download_plan，主线剧集、library_type、season_number 和 episode_number 都是已确认锁，禁止改写。
9. temporary_related_special 可以没有 TVDB episode ID，但必须复用输入中的可定位 source_entry。
10. 只映射能够从 file_tree 精确定位的文件；无法可靠映射的文件不要写入 episode_map。
```

- [ ] **Step 4: Route confirmed plans through the new builder even when TVDB is unavailable**

```python
# Add imports in app/modules/renaming.py.
from app.utils.confirmed_download_plan import extract_confirmed_download_plan
from app.utils.tvdb_rename import build_confirmed_rename_plan


def _move_unmatched_to_unorganized(event, unmatched_sources):
    if not unmatched_sources:
        return ""
    unorganized_root = str(((init.bot_config or {}).get("media") or {}).get("unorganized_path") or "").rstrip("/")
    if not unorganized_root:
        raise RuntimeError("未匹配文件存在，但 media.unorganized_path 未配置")
    source_leaf = str(event.final_path or "").rstrip("/").rsplit("/", 1)[-1]
    target_dir = f"{unorganized_root}/{source_leaf}"
    storage = _storage(event)
    if not storage.create_dir_recursive(target_dir):
        raise RuntimeError(f"无法创建未整理目录 {target_dir}")
    for relative_path in unmatched_sources:
        source_path = f"{str(event.final_path).rstrip('/')}/{str(relative_path).strip('/')}"
        if storage.move_file(source_path, target_dir) is not True:
            raise RuntimeError(f"无法移动未匹配文件 {source_path}")
    return target_dir


def _move_confirmed_failure_to_unorganized(event):
    unorganized_root = str(((init.bot_config or {}).get("media") or {}).get("unorganized_path") or "").rstrip("/")
    if not unorganized_root:
        raise RuntimeError("确认方案映射失败，但 media.unorganized_path 未配置")
    storage = _storage(event)
    source_path = str(event.final_path or "").rstrip("/")
    source_leaf = source_path.rsplit("/", 1)[-1]
    if not storage.create_dir_recursive(unorganized_root):
        raise RuntimeError(f"无法创建未整理目录 {unorganized_root}")
    if storage.move_file(source_path, unorganized_root) is not True:
        raise RuntimeError(f"无法移动确认方案失败目录 {source_path}")
    return f"{unorganized_root}/{source_leaf}"


class ConfirmedPlanConflict(RuntimeError):
    pass


def _assert_no_target_conflicts(storage, rename_plan):
    for operation in rename_plan.get("operations") or []:
        target_path = f"{str(operation['target_dir']).rstrip('/')}/{operation['rename_to']}"
        if storage.get_file_info(target_path):
            raise ConfirmedPlanConflict(f"已确认目标编号发生冲突：{operation['rename_to']}")
```

Rename the current `_attempt_tvdb_ai_episode_rename` implementation to `_attempt_legacy_tvdb_ai_episode_rename`, leaving its TVDB requirements unchanged. Add this confirmed-plan wrapper under it:

```python
def _attempt_tvdb_ai_episode_rename(event: DownloadCompletedEvent, metadata):
    confirmed_plan = extract_confirmed_download_plan(event.metadata)
    if not confirmed_plan:
        return _attempt_legacy_tvdb_ai_episode_rename(event, metadata)
    if not metadata or not _has_ai_episode_inference_config():
        return None

    storage = _storage(event)
    tvdb_candidates, tvdb_episodes = _get_tvdb_candidates_and_episodes(metadata)
    file_tree = collect_storage_file_tree(storage, event.final_path)
    if not [item for item in file_tree if not item.get("is_dir")]:
        init.logger.warn(f"确认方案整理跳过：目录中未找到视频文件 {event.final_path}")
        return None

    context = {
        "metadata": metadata,
        "confirmed_download_plan": confirmed_plan,
        "release_title": metadata.get("release_title") or event.resource_name,
        "resource_name": event.resource_name,
        "download_path": event.final_path,
        "file_tree": file_tree,
        "tvdb_candidates": tvdb_candidates,
        "tvdb_episodes": tvdb_episodes,
    }
    ai_plan = infer_tvdb_episode_plan_with_ai(context)
    rename_plan = build_confirmed_rename_plan(
        final_path=event.final_path,
        selected_path=event.selected_path,
        metadata=metadata,
        confirmed_plan=confirmed_plan,
        ai_plan=ai_plan or {},
        file_tree=file_tree,
    )
    if not rename_plan:
        init.logger.warn(f"确认方案整理跳过：AI文件映射未通过锁定校验 path={event.final_path}")
        return None

    _assert_no_target_conflicts(storage, rename_plan)
    for operation in rename_plan["operations"]:
        if not storage.create_dir_recursive(operation["target_dir"]):
            raise RuntimeError(f"确认方案整理失败：无法创建 {operation['target_dir']}")
        current_source_path = operation["source_path"]
        if Path(operation["source_path"]).name != operation["rename_to"]:
            if storage.rename(operation["source_path"], operation["rename_to"]) is not True:
                raise RuntimeError(f"确认方案整理失败：重命名失败 {operation['source_path']}")
            current_source_path = operation["renamed_source_path"]
        if storage.move_file(current_source_path, operation["target_dir"]) is not True:
            raise RuntimeError(f"确认方案整理失败：移动失败 {current_source_path}")

    unmatched_dir = _move_unmatched_to_unorganized(event, rename_plan.get("unmatched_sources") or [])
    rename_plan["unmatched_target"] = unmatched_dir
    _cleanup_source_directory(storage, event.final_path)
    return rename_plan
```

Before any rename or move, call `_assert_no_target_conflicts(storage, rename_plan)`. Wrap the confirmed execution in `process_tvdb_episode` so the registry cannot fall through to generic renaming:

```python
try:
    rename_plan = _attempt_tvdb_ai_episode_rename(event, metadata)
except ConfirmedPlanConflict as exc:
    return PostDownloadResult(
        True,
        final_path=event.final_path,
        message=f"⚠️ {exc}\n文件保持原位，请重新确认下载方案。",
        should_stop=True,
    )
if not rename_plan and extract_confirmed_download_plan(event.metadata):
    unorganized_target = _move_confirmed_failure_to_unorganized(event)
    return PostDownloadResult(
        True,
        final_path=unorganized_target,
        message=f"⚠️ 下载后 AI 文件映射失败，已移入未整理目录。\n\n保存目录：`{unorganized_target}`",
        should_stop=True,
    )
```

After successful mapped-file operations, call `_move_unmatched_to_unorganized(event, rename_plan["unmatched_sources"])`. Delete the source directory only when no unmatched files remain there.

- [ ] **Step 5: Run renaming regressions**

Run: `python3 -m unittest tests.test_confirmed_download_plan tests.test_media_auto_rename tests.test_tvdb_rename tests.test_composable_renaming -v`

Expected: all tests PASS; TVDB-down temporary mapping succeeds, unmatched files move to `/未整理`, and legacy TVDB behavior remains green.

- [ ] **Step 6: Commit constrained post-download execution**

```bash
git add app/utils/ai.py app/modules/renaming.py tests/test_composable_renaming.py
git commit -m "feat: execute confirmed special plans after download"
```

---

### Task 10: End-to-End Regression, Documentation Alignment, and Branch Handoff

**Files:**
- Modify: `tests/test_composable_integration.py`
- Modify: `docs/superpowers/specs/2026-07-11-ai-wikipedia-download-planner-design.md` only if implementation names differ from the approved names; behavior must not change.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: one end-to-end contract proving search metadata reaches the renaming processor, plus a verified branch ready for the finishing workflow.

- [ ] **Step 1: Add an end-to-end metadata handoff test**

First align the approved spec's schema key with the final module boundary:

```diff
-  "plex_placement": {
+  "placement": {
```

Then add the integration test:

```python
def test_confirmed_download_plan_survives_request_event_and_renaming_pipeline(self):
    from app.core.module_registry import DownloadCompletedEvent, DownloadRequest, ModuleRegistry
    from app.utils.search_plan import attach_download_plan, confirm_download_plan

    plan = confirm_download_plan({
        "schema_version": 1,
        "plan_id": "plan-a",
        "display_title": "想见你",
        "english_title": "Someday or One Day The Movie",
        "year": "2022",
        "content_identity": "extension_movie",
        "relation": {"type": "sequel", "target_series_title": "Someday or One Day", "target_series_year": "2019", "source": "wikipedia"},
        "placement": {"library_type": "series", "category_kind": "live_action_series", "season_number": 0, "episode_number": 100, "mapping_kind": "temporary_related_special", "mapping_source": "local_allocator"},
        "source_entry": {"title": "想见你 (电影)", "url": "https://zh.wikipedia.org/wiki/想見你_(電影)", "provider": "wikipedia", "availability": "ok", "verification": "verified"},
        "prowlarr_queries": ["Someday or One Day The Movie 2022"],
        "evidence": {},
        "warnings": [],
        "confirmed": False,
    })
    request = DownloadRequest(
        link="magnet:?xt=urn:btih:" + "a" * 40,
        selected_path="/真人剧集",
        user_id=1,
        metadata=attach_download_plan({"source": "confirmed"}, plan),
    )
    event = DownloadCompletedEvent(
        link=request.link,
        selected_path=request.selected_path,
        user_id=request.user_id,
        final_path="/真人剧集/Raw.Release",
        resource_name="Raw.Release",
        metadata=request.metadata,
    )
    seen = []
    registry = ModuleRegistry()
    registry.add_post_download_processor(
        lambda current: seen.append(current.metadata["download_plan"]) or None,
        priority=100,
        name="test.capture_plan",
    )
    registry.run_post_download_pipeline(event)
    self.assertEqual(seen[0]["placement"]["episode_number"], 100)
    self.assertTrue(seen[0]["confirmed"])
```

- [ ] **Step 2: Run the focused integration test**

Run: `python3 -m unittest tests.test_composable_integration -v`

Expected: all tests PASS.

- [ ] **Step 3: Run the complete unittest suite**

Run: `python3 -m unittest discover tests -v`

Expected: all discovered tests PASS with zero failures and zero errors.

- [ ] **Step 4: Run pytest**

Run: `python3 -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 5: Compile all tracked Python files**

Run: `python3 -m py_compile $(git ls-files '*.py')`

Expected: exit code 0 and no output.

- [ ] **Step 6: Check installed dependency consistency**

Run: `python3 -m pip check`

Expected: `No broken requirements found.`

- [ ] **Step 7: Run the Telepiplex whitespace check**

Run: `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check main...HEAD`

Expected: exit code 0 and no output.

- [ ] **Step 8: Verify scope and module isolation**

Run: `git diff --name-status main...HEAD`

Expected: only the approved spec/plan, media-search files, renaming files, config templates, and their tests appear; no Plex implementation file appears.

Run: `rg -n "plex" app/handlers/search_handler.py app/services/search_planner.py app/adapters/wikipedia.py`

Expected: no import or API call to a Plex module; any occurrence is limited to plan field names or user-facing placement text.

- [ ] **Step 9: Commit the integration regression**

```bash
git add tests/test_composable_integration.py docs/superpowers/specs/2026-07-11-ai-wikipedia-download-planner-design.md
git commit -m "test: cover AI download plan handoff"
```

- [ ] **Step 10: Enter the finishing workflow**

Invoke `superpowers:finishing-a-development-branch`. Present merge, PR, keep-branch, and cleanup options. Do not push, merge into `main`, or synchronize `feature/media-search` and `feature/renaming` without the user's selected option.
