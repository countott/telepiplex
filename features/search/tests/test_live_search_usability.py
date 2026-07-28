"""Opt-in live usability gate for real Provider and AI credentials."""

from __future__ import annotations

import os
from pathlib import Path
import unittest

import yaml

from telepiplex_search.adapters.douban import lookup_douban_evidence
from telepiplex_search.adapters.wikipedia import (
    lookup_wikipedia_evidence,
)
from telepiplex_search.context import runtime_context
from telepiplex_search.service import SearchFeature


LIVE_CONFIG_ENV = "TELEPIPLEX_SEARCH_LIVE_CONFIG"
PUBLIC_LIVE_ENV = "TELEPIPLEX_SEARCH_PUBLIC_LIVE"
COMPLEX_LIVE_QUERIES = {
    "进击的巨人",
    "深夜食堂",
    "三体",
    "西部世界",
    "雪国列车",
    "汉尼拔",
    "东京爱情故事",
    "射雕英雄传",
    "大奥",
    "康斯坦丁",
    "Fargo",
    "Watchmen",
}
PUBLIC_COMPLEX_CASES = {
    "进击的巨人": ("进击的巨人", "進擊的巨人"),
    "深夜食堂": ("深夜食堂",),
    "三体": ("三体", "三體"),
    "西部世界": ("西部世界",),
    "雪国列车": ("雪国列车", "末日列車"),
    "汉尼拔": ("汉尼拔",),
    "东京爱情故事": ("东京爱情故事", "東京愛情故事"),
    "射雕英雄传": ("射雕英雄传", "射鵰英雄傳"),
    "大奥": ("大奥", "大奧"),
    "康斯坦丁": ("康斯坦丁", "魔間行者"),
    "Fargo": ("fargo", "冰血暴"),
    "Watchmen": ("watchmen", "守望者", "守護者"),
}
LIVE_CASES = ({
    "query": "ODDTAXI",
    "minimum_candidates": 3,
    "expected": {
        ("2021", "series"),
        ("2022", "movie"),
        ("2024", "series"),
    },
}, {
    "query": "冰果",
    "minimum_candidates": 2,
    "expected": {
        ("2012", "series"),
        ("2017", "movie"),
    },
}, {
    "query": "蜂蜜与四叶草",
    "minimum_candidates": 4,
    "expected": {
        ("2005", "series"),
        ("2006", "movie"),
        ("2008", "series"),
    },
}, {
    "query": '"1917"',
    "minimum_candidates": 1,
    "expected": {
        ("2019", "movie"),
    },
}, {
    "query": "想见你",
    "minimum_candidates": 2,
    "expected": {
        ("2019", "series"),
        ("2022", "movie"),
    },
}, {
    "query": "进击的巨人",
    "minimum_candidates": 2,
    "expected": {
        ("2013", "series"),
        ("2015", "movie"),
    },
}, {
    "query": "深夜食堂",
    "minimum_candidates": 5,
    "expected": {
        ("2009", "series"),
        ("2015", "movie"),
        ("2015", "series"),
        ("2017", "series"),
        ("2019", "movie"),
    },
}, {
    "query": "三体",
    "minimum_candidates": 3,
    "expected": {
        ("2022", "series"),
        ("2023", "series"),
        ("2024", "series"),
    },
}, {
    "query": "西部世界",
    "minimum_candidates": 2,
    "expected": {
        ("1973", "movie"),
        ("2016", "series"),
    },
}, {
    "query": "雪国列车",
    "minimum_candidates": 2,
    "expected": {
        ("2013", "movie"),
        ("2020", "series"),
    },
}, {
    "query": "汉尼拔",
    "minimum_candidates": 2,
    "expected": {
        ("2001", "movie"),
        ("2013", "series"),
    },
}, {
    "query": "东京爱情故事",
    "minimum_candidates": 2,
    "expected": {
        ("1991", "series"),
        ("2020", "series"),
    },
}, {
    "query": "射雕英雄传",
    "minimum_candidates": 4,
    "expected": {
        ("1983", "series"),
        ("2003", "series"),
        ("2008", "series"),
        ("2017", "series"),
    },
}, {
    "query": "大奥",
    "minimum_candidates": 3,
    "expected": {
        ("2003", "series"),
        ("2010", "movie"),
        ("2023", "series"),
    },
}, {
    "query": "康斯坦丁",
    "minimum_candidates": 3,
    "expected": {
        ("2005", "movie"),
        ("2014", "series"),
        ("2018", "series"),
    },
}, {
    "query": "Fargo",
    "minimum_candidates": 2,
    "expected": {
        ("1996", "movie"),
        ("2014", "series"),
    },
}, {
    "query": "Watchmen",
    "minimum_candidates": 2,
    "expected": {
        ("2009", "movie"),
        ("2019", "series"),
    },
})


def _live_config() -> dict:
    raw_path = os.environ.get(LIVE_CONFIG_ENV, "").strip()
    if not raw_path:
        raise unittest.SkipTest(
            f"set {LIVE_CONFIG_ENV} to a real Search config file"
        )
    config_path = Path(raw_path).expanduser()
    if not config_path.is_file():
        raise unittest.SkipTest("live Search config file is missing")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    tvdb = ((config.get("metadata") or {}).get("tvdb") or {})
    ai = config.get("ai") or {}
    if not tvdb.get("api_key"):
        raise unittest.SkipTest("live Search config has no TVDB API key")
    if not (
        ai.get("api_url")
        and ai.get("api_key")
        and ai.get("model")
    ):
        raise unittest.SkipTest("live Search config has incomplete AI settings")
    return config


class LiveSearchCorpusContractTest(unittest.TestCase):
    def test_live_gate_contains_the_complex_series_corpus(self):
        queries = {case["query"] for case in LIVE_CASES}

        self.assertGreaterEqual(len(LIVE_CASES), 17)
        self.assertTrue(COMPLEX_LIVE_QUERIES.issubset(queries))
        self.assertTrue(all(
            case["minimum_candidates"] >= 2
            for case in LIVE_CASES
            if case["query"] in COMPLEX_LIVE_QUERIES
        ))


@unittest.skipUnless(
    os.environ.get(PUBLIC_LIVE_ENV, "").strip() == "1",
    f"set {PUBLIC_LIVE_ENV}=1 to query public Providers",
)
class PublicSourceLiveUsabilityTest(unittest.TestCase):
    def test_complex_series_queries_have_real_public_source_recall(self):
        for query, aliases in PUBLIC_COMPLEX_CASES.items():
            with self.subTest(query=query):
                wikipedia = lookup_wikipedia_evidence(
                    [query],
                    languages=("zh",),
                    timeout=12,
                    min_interval=1.1,
                    rate_limit_cooldown=2,
                )
                douban = lookup_douban_evidence(
                    [query],
                    timeout=12,
                    cache_ttl=0,
                    max_concurrency=2,
                    circuit_breaker_failures=20,
                    circuit_breaker_seconds=1,
                )

                self.assertEqual(douban["status"], "ok")
                self.assertGreater(len(douban["facts"]), 0)
                douban_titles = " ".join(
                    str(fact.get("title") or "").casefold()
                    for fact in douban["facts"]
                    if isinstance(fact, dict)
                )
                self.assertTrue(any(
                    alias.casefold() in douban_titles
                    for alias in aliases
                ))
                self.assertIn(
                    wikipedia["status"],
                    {"ok", "rate_limited"},
                )
                if wikipedia["status"] == "ok":
                    self.assertGreater(len(wikipedia["facts"]), 0)


class LiveSearchUsabilityTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = _live_config()
        runtime_context.configure(cls.config)
        cls.feature = SearchFeature(config=cls.config, host=None)

    async def test_named_queries_reach_grounded_selectable_candidates(self):
        for index, case in enumerate(LIVE_CASES, 1):
            with self.subTest(query=case["query"]):
                plan = await self.feature._build_plan(
                    case["query"],
                    f"live-usability-{index}",
                )
                candidates = [
                    candidate
                    for candidate in plan.get("candidates") or []
                    if candidate.get("selectable") is not False
                ]
                self.assertGreaterEqual(
                    len(candidates),
                    case["minimum_candidates"],
                )
                actual = {
                    (
                        str(
                            (
                                candidate.get("media_metadata") or {}
                            ).get("identity", {}).get("year") or ""
                        ),
                        str(
                            (
                                candidate.get("media_metadata") or {}
                            ).get("placement", {}).get("library_type") or ""
                        ),
                    )
                    for candidate in candidates
                }
                self.assertTrue(case["expected"].issubset(actual))
                self.assertTrue(all(
                    candidate.get("metadata_ready")
                    and candidate.get("source_links")
                    and candidate.get("prowlarr_queries")
                    for candidate in candidates
                ))


if __name__ == "__main__":
    unittest.main()
