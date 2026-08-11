import html
import re
import unittest

from telepiplex_search.planner import build_confirmable_search_plan
from telepiplex_search.search_plan import TemporarySpecialAllocator
from telepiplex_search.service import SearchFeature


REAL_WORLD_SCENARIOS = ({
    "query": "ODDTAXI",
    "duplicate_first_fact": True,
    "works": ({
        "key": "odd-taxi-series",
        "kind": "series",
        "year": "2021",
        "chinese": "奇巧计程车",
        "original": "オッドタクシー",
        "language": "ja",
        "english": "ODDTAXI",
        "romanized": "Odd Taxi",
        "genres": ("Anime",),
    }, {
        "key": "odd-taxi-movie",
        "kind": "movie",
        "year": "2022",
        "chinese": "奇巧计程车 剧场版",
        "original": "映画 オッドタクシー イン・ザ・ウッズ",
        "language": "ja",
        "english": "ODDTAXI: In the Woods",
        "romanized": "Eiga Odd Taxi: In the Woods",
        "genres": ("Anime",),
    }, {
        "key": "root-series",
        "kind": "series",
        "year": "2024",
        "chinese": "根源 / RoOT",
        "original": "RoOT / ルート",
        "language": "ja",
        "english": "RoOT / Route of OddTaxi",
        "romanized": "RoOT",
        "genres": ("Drama",),
    }),
    "expected_categories": {
        "animated_series",
        "animated_movie",
        "live_action_series",
    },
}, {
    "query": "冰果",
    "works": ({
        "key": "hyouka-series",
        "kind": "series",
        "year": "2012",
        "chinese": "冰果",
        "original": "氷菓",
        "language": "ja",
        "english": "Hyouka",
        "romanized": "Hyouka",
        "genres": ("Anime",),
    }, {
        "key": "hyouka-live-movie",
        "kind": "movie",
        "year": "2017",
        "chinese": "冰果 真人版",
        "original": "氷菓",
        "language": "ja",
        "english": "Hyouka",
        "romanized": "Hyouka",
        "genres": ("Drama",),
    }),
    "expected_categories": {
        "animated_series",
        "live_action_movie",
    },
}, {
    "query": "蜂蜜与四叶草",
    "duplicate_first_fact": True,
    "works": ({
        "key": "honey-anime",
        "kind": "series",
        "year": "2005",
        "chinese": "蜂蜜与四叶草",
        "original": "ハチミツとクローバー",
        "language": "ja",
        "english": "Honey and Clover",
        "romanized": "Hachimitsu to Clover",
        "genres": ("Anime",),
    }, {
        "key": "honey-live-movie",
        "kind": "movie",
        "year": "2006",
        "chinese": "蜂蜜与四叶草",
        "original": "ハチミツとクローバー",
        "language": "ja",
        "english": "Honey and Clover",
        "romanized": "Hachimitsu to Clover",
        "genres": ("Drama",),
    }, {
        "key": "honey-jp-drama",
        "kind": "series",
        "year": "2008",
        "chinese": "蜂蜜与四叶草",
        "original": "ハチミツとクローバー",
        "language": "ja",
        "english": "Honey and Clover (Japan)",
        "romanized": "Hachimitsu to Clover",
        "genres": ("Drama",),
    }, {
        "key": "honey-tw-drama",
        "kind": "series",
        "year": "2008",
        "chinese": "蜂蜜幸运草",
        "original": "蜂蜜幸運草",
        "language": "zh",
        "english": "Honey and Clover (Taiwan)",
        "romanized": "",
        "genres": ("Drama",),
    }),
    "expected_categories": {
        "animated_series",
        "live_action_movie",
        "live_action_series",
    },
}, {
    "query": "1917",
    "works": ({
        "key": "1917-film",
        "kind": "movie",
        "year": "2019",
        "chinese": "1917",
        "original": "1917",
        "language": "en",
        "english": "1917",
        "romanized": "",
        "genres": ("Drama",),
    },),
    "expected_categories": {"live_action_movie"},
}, {
    "query": "想见你",
    "works": ({
        "key": "someday-series",
        "kind": "series",
        "year": "2019",
        "chinese": "想见你",
        "original": "想見你",
        "language": "zh",
        "english": "Someday or One Day",
        "romanized": "",
        "genres": ("Drama",),
    }, {
        "key": "someday-movie",
        "kind": "movie",
        "year": "2022",
        "chinese": "想见你",
        "original": "想見你",
        "language": "zh",
        "english": "Someday or One Day: The Movie",
        "romanized": "",
        "genres": ("Drama",),
    }),
    "expected_categories": {
        "live_action_series",
        "live_action_movie",
    },
}, {
    "query": "进击的巨人",
    "complex_series": True,
    "risks": (
        "multi_season",
        "animation_live_action",
        "spin_off",
    ),
    "works": ({
        "key": "attack-on-titan-anime",
        "kind": "series",
        "year": "2013",
        "season_years": ("2013", "2017", "2018", "2020"),
        "chinese": "进击的巨人",
        "original": "進撃の巨人",
        "language": "ja",
        "english": "Attack on Titan",
        "romanized": "Shingeki no Kyojin",
        "genres": ("Anime",),
    }, {
        "key": "attack-on-titan-live-movie",
        "kind": "movie",
        "year": "2015",
        "chinese": "进击的巨人 真人版：前篇",
        "original": "進撃の巨人 ATTACK ON TITAN",
        "language": "ja",
        "english": "Attack on Titan",
        "romanized": "Shingeki no Kyojin",
        "genres": ("Drama",),
    }, {
        "key": "attack-on-titan-junior-high",
        "kind": "series",
        "year": "2015",
        "chinese": "进击！巨人中学",
        "original": "進撃！巨人中学校",
        "language": "ja",
        "english": "Attack on Titan: Junior High",
        "romanized": "Shingeki! Kyojin Chuugakkou",
        "genres": ("Anime",),
    }),
    "expected_categories": {
        "animated_series",
        "live_action_movie",
    },
}, {
    "query": "深夜食堂",
    "complex_series": True,
    "risks": (
        "multi_season",
        "regional_adaptation",
        "movie_series_collision",
    ),
    "works": ({
        "key": "midnight-diner-japan",
        "kind": "series",
        "year": "2009",
        "season_years": ("2009", "2011", "2014"),
        "chinese": "深夜食堂",
        "original": "深夜食堂",
        "language": "ja",
        "english": "Midnight Diner",
        "romanized": "Shinya Shokudo",
        "genres": ("Drama",),
    }, {
        "key": "midnight-diner-film",
        "kind": "movie",
        "year": "2015",
        "chinese": "深夜食堂电影版",
        "original": "映画 深夜食堂",
        "language": "ja",
        "english": "Midnight Diner",
        "romanized": "Eiga Shinya Shokudo",
        "genres": ("Drama",),
    }, {
        "key": "late-night-restaurant-korea",
        "kind": "series",
        "year": "2015",
        "chinese": "深夜食堂 韩国版",
        "original": "심야식당",
        "language": "ko",
        "english": "Late Night Restaurant",
        "romanized": "",
        "genres": ("Drama",),
    }, {
        "key": "midnight-diner-china",
        "kind": "series",
        "year": "2017",
        "chinese": "深夜食堂 华语版",
        "original": "深夜食堂",
        "language": "zh",
        "english": "Midnight Diner (China)",
        "romanized": "",
        "genres": ("Drama",),
    }, {
        "key": "midnight-diner-china-film",
        "kind": "movie",
        "year": "2019",
        "chinese": "深夜食堂 华语电影版",
        "original": "深夜食堂",
        "language": "zh",
        "english": "Midnight Diner (Chinese Film)",
        "romanized": "",
        "genres": ("Drama",),
    }),
    "expected_categories": {
        "live_action_series",
        "live_action_movie",
    },
}, {
    "query": "三体",
    "complex_series": True,
    "risks": (
        "regional_adaptation",
        "animation_live_action",
    ),
    "works": ({
        "key": "three-body-animation",
        "kind": "series",
        "year": "2022",
        "chinese": "三体 动画版",
        "original": "三体",
        "language": "zh",
        "english": "The Three-Body Problem Animation",
        "romanized": "",
        "genres": ("Animation",),
    }, {
        "key": "three-body-tencent",
        "kind": "series",
        "year": "2023",
        "chinese": "三体",
        "original": "三体",
        "language": "zh",
        "english": "Three-Body",
        "romanized": "",
        "genres": ("Drama",),
    }, {
        "key": "three-body-netflix",
        "kind": "series",
        "year": "2024",
        "chinese": "三体 第一季",
        "original": "3 Body Problem",
        "language": "en",
        "english": "3 Body Problem",
        "romanized": "",
        "genres": ("Drama",),
    }),
    "expected_categories": {
        "animated_series",
        "live_action_series",
    },
}, {
    "query": "西部世界",
    "complex_series": True,
    "risks": (
        "multi_season",
        "movie_series_collision",
    ),
    "works": ({
        "key": "westworld-film",
        "kind": "movie",
        "year": "1973",
        "chinese": "西部世界",
        "original": "Westworld",
        "language": "en",
        "english": "Westworld",
        "romanized": "",
        "genres": ("Drama",),
    }, {
        "key": "westworld-series",
        "kind": "series",
        "year": "2016",
        "season_years": ("2016", "2018", "2020", "2022"),
        "chinese": "西部世界",
        "original": "Westworld",
        "language": "en",
        "english": "Westworld",
        "romanized": "",
        "genres": ("Drama",),
    }),
    "expected_categories": {
        "live_action_movie",
        "live_action_series",
    },
}, {
    "query": "雪国列车",
    "complex_series": True,
    "risks": (
        "multi_season",
        "movie_series_collision",
    ),
    "works": ({
        "key": "snowpiercer-film",
        "kind": "movie",
        "year": "2013",
        "chinese": "雪国列车",
        "original": "설국열차",
        "language": "ko",
        "english": "Snowpiercer",
        "romanized": "",
        "genres": ("Drama",),
    }, {
        "key": "snowpiercer-series",
        "kind": "series",
        "year": "2020",
        "season_years": ("2020", "2021", "2022", "2024"),
        "chinese": "雪国列车 剧版",
        "original": "Snowpiercer",
        "language": "en",
        "english": "Snowpiercer",
        "romanized": "",
        "genres": ("Drama",),
    }),
    "expected_categories": {
        "live_action_movie",
        "live_action_series",
    },
}, {
    "query": "汉尼拔",
    "complex_series": True,
    "risks": (
        "multi_season",
        "movie_series_collision",
    ),
    "works": ({
        "key": "hannibal-film",
        "kind": "movie",
        "year": "2001",
        "chinese": "汉尼拔",
        "original": "Hannibal",
        "language": "en",
        "english": "Hannibal",
        "romanized": "",
        "genres": ("Drama",),
    }, {
        "key": "hannibal-series",
        "kind": "series",
        "year": "2013",
        "season_years": ("2013", "2014", "2015"),
        "chinese": "汉尼拔",
        "original": "Hannibal",
        "language": "en",
        "english": "Hannibal",
        "romanized": "",
        "genres": ("Drama",),
    }),
    "expected_categories": {
        "live_action_movie",
        "live_action_series",
    },
}, {
    "query": "东京爱情故事",
    "complex_series": True,
    "risks": ("same_title_reboot",),
    "works": ({
        "key": "tokyo-love-story-1991",
        "kind": "series",
        "year": "1991",
        "chinese": "东京爱情故事",
        "original": "東京ラブストーリー",
        "language": "ja",
        "english": "Tokyo Love Story",
        "romanized": "Tokyo Love Story",
        "genres": ("Drama",),
    }, {
        "key": "tokyo-love-story-2020",
        "kind": "series",
        "year": "2020",
        "chinese": "东京爱情故事2020",
        "original": "東京ラブストーリー",
        "language": "ja",
        "english": "Tokyo Love Story (2020)",
        "romanized": "Tokyo Love Story",
        "genres": ("Drama",),
    }),
    "expected_categories": {"live_action_series"},
}, {
    "query": "射雕英雄传",
    "complex_series": True,
    "risks": (
        "same_title_reboot",
        "regional_adaptation",
    ),
    "works": ({
        "key": "legend-condor-heroes-1983",
        "kind": "series",
        "year": "1983",
        "chinese": "射雕英雄传 1983",
        "original": "射鵰英雄傳",
        "language": "zh",
        "english": "The Legend of the Condor Heroes (1983)",
        "romanized": "",
        "genres": ("Drama",),
    }, {
        "key": "legend-condor-heroes-2003",
        "kind": "series",
        "year": "2003",
        "chinese": "射雕英雄传 2003",
        "original": "射雕英雄传",
        "language": "zh",
        "english": "The Legend of the Condor Heroes (2003)",
        "romanized": "",
        "genres": ("Drama",),
    }, {
        "key": "legend-condor-heroes-2008",
        "kind": "series",
        "year": "2008",
        "chinese": "射雕英雄传 2008",
        "original": "射雕英雄传",
        "language": "zh",
        "english": "The Legend of the Condor Heroes (2008)",
        "romanized": "",
        "genres": ("Drama",),
    }, {
        "key": "legend-condor-heroes-2017",
        "kind": "series",
        "year": "2017",
        "chinese": "射雕英雄传 2017",
        "original": "射雕英雄传",
        "language": "zh",
        "english": "The Legend of the Condor Heroes (2017)",
        "romanized": "",
        "genres": ("Drama",),
    }),
    "expected_categories": {"live_action_series"},
}, {
    "query": "大奥",
    "complex_series": True,
    "risks": (
        "same_title_reboot",
        "movie_series_collision",
        "animation_live_action",
    ),
    "works": ({
        "key": "oh-oku-fuji-series",
        "kind": "series",
        "year": "2003",
        "chinese": "大奥",
        "original": "大奥",
        "language": "ja",
        "english": "Oh-Oku: The Women of the Inner Palace",
        "romanized": "Oh-Oku",
        "genres": ("Drama",),
    }, {
        "key": "oh-oku-film",
        "kind": "movie",
        "year": "2010",
        "chinese": "大奥：女将军与她的后宫三千美男",
        "original": "大奥",
        "language": "ja",
        "english": "The Lady Shogun and Her Men",
        "romanized": "Oh-Oku",
        "genres": ("Drama",),
    }, {
        "key": "oh-oku-inner-chambers-anime",
        "kind": "series",
        "year": "2023",
        "chinese": "大奥 动画版",
        "original": "大奥",
        "language": "ja",
        "english": "Ōoku: The Inner Chambers",
        "romanized": "Ooku",
        "genres": ("Anime",),
    }, {
        "key": "oh-oku-nhk-series",
        "kind": "series",
        "year": "2023",
        "chinese": "大奥 NHK版",
        "original": "大奥",
        "language": "ja",
        "english": "Ōoku (NHK Drama)",
        "romanized": "Ooku",
        "genres": ("Drama",),
    }),
    "expected_categories": {
        "live_action_series",
        "live_action_movie",
        "animated_series",
    },
}, {
    "query": "康斯坦丁",
    "complex_series": True,
    "risks": (
        "movie_series_collision",
        "animation_live_action",
        "spin_off",
    ),
    "works": ({
        "key": "constantine-film",
        "kind": "movie",
        "year": "2005",
        "chinese": "康斯坦丁",
        "original": "Constantine",
        "language": "en",
        "english": "Constantine",
        "romanized": "",
        "genres": ("Drama",),
    }, {
        "key": "constantine-series",
        "kind": "series",
        "year": "2014",
        "chinese": "康斯坦丁",
        "original": "Constantine",
        "language": "en",
        "english": "Constantine",
        "romanized": "",
        "genres": ("Drama",),
    }, {
        "key": "constantine-city-of-demons",
        "kind": "series",
        "year": "2018",
        "chinese": "康斯坦丁：恶魔之城",
        "original": "Constantine: City of Demons",
        "language": "en",
        "english": "Constantine: City of Demons",
        "romanized": "",
        "genres": ("Animation",),
    }),
    "expected_categories": {
        "live_action_movie",
        "live_action_series",
        "animated_series",
    },
}, {
    "query": "Fargo",
    "complex_series": True,
    "risks": (
        "multi_season",
        "movie_series_collision",
    ),
    "works": ({
        "key": "fargo-film",
        "kind": "movie",
        "year": "1996",
        "chinese": "冰血暴",
        "original": "Fargo",
        "language": "en",
        "english": "Fargo",
        "romanized": "",
        "genres": ("Drama",),
    }, {
        "key": "fargo-series",
        "kind": "series",
        "year": "2014",
        "season_years": ("2014", "2015", "2017", "2020", "2023"),
        "chinese": "冰血暴",
        "original": "Fargo",
        "language": "en",
        "english": "Fargo",
        "romanized": "",
        "genres": ("Drama",),
    }),
    "expected_categories": {
        "live_action_movie",
        "live_action_series",
    },
}, {
    "query": "Watchmen",
    "complex_series": True,
    "risks": (
        "movie_series_collision",
        "animation_live_action",
    ),
    "works": ({
        "key": "watchmen-film",
        "kind": "movie",
        "year": "2009",
        "chinese": "守望者",
        "original": "Watchmen",
        "language": "en",
        "english": "Watchmen",
        "romanized": "",
        "genres": ("Drama",),
    }, {
        "key": "watchmen-series",
        "kind": "series",
        "year": "2019",
        "chinese": "守望者 剧版",
        "original": "Watchmen",
        "language": "en",
        "english": "Watchmen",
        "romanized": "",
        "genres": ("Drama",),
    }, {
        "key": "watchmen-chapter-one",
        "kind": "movie",
        "year": "2024",
        "chinese": "守望者（上）",
        "original": "Watchmen Chapter I",
        "language": "en",
        "english": "Watchmen Chapter I",
        "romanized": "",
        "genres": ("Animation",),
    }),
    "expected_categories": {
        "live_action_movie",
        "live_action_series",
        "animated_movie",
    },
})


def _provider_sources(scenario):
    wikipedia = []
    douban = []
    tvdb = []
    for index, work in enumerate(scenario["works"], 1):
        wikipedia.append({
            "wikibase_item": f"Q-usability-{work['key']}",
            "title": work["english"],
            "chinese_title": work["chinese"],
            "original_title": work["original"],
            "original_language": work["language"],
            "official_english_title": work["english"],
            "romanized_original_title": work["romanized"],
            "year": work["year"],
            "media_type": work["kind"],
            "genres": list(work["genres"]),
            "url": f"https://en.wikipedia.org/wiki/{work['key']}",
        })
        season_years = tuple(work.get("season_years") or ())
        douban_entries = [
            {
                "subject_id": (
                    f"usability-{index}-{work['key']}-s{season_number}"
                ),
                "title": f"{work['chinese']} 第{season_number}季",
                "chinese_title": work["chinese"],
                "original_title": work["original"],
                "original_language": work["language"],
                "official_english_title": work["english"],
                "romanized_original_title": work["romanized"],
                "year": season_year,
                "media_type": "series",
                "genres": list(work["genres"]),
                "url": (
                    "https://movie.douban.com/subject/"
                    f"usability-{index}-{work['key']}-s{season_number}/"
                ),
            }
            for season_number, season_year
            in enumerate(season_years, 1)
        ]
        if not douban_entries:
            douban_entries = [{
                "subject_id": f"usability-{index}-{work['key']}",
                "title": work["chinese"],
                "chinese_title": work["chinese"],
                "original_title": work["original"],
                "original_language": work["language"],
                "official_english_title": work["english"],
                "romanized_original_title": work["romanized"],
                "year": work["year"],
                "media_type": work["kind"],
                "genres": list(work["genres"]),
                "url": (
                    "https://movie.douban.com/subject/"
                    f"usability-{index}-{work['key']}/"
                ),
            }]
        douban.extend(douban_entries)
        tvdb_id = f"usability-{work['key']}"
        entry = {
            f"tvdb_{work['kind']}_id": tvdb_id,
            "name": work["english"],
            "chinese_title": work["chinese"],
            "original_title": work["original"],
            "original_language": work["language"],
            "official_english_title": work["english"],
            "romanized_original_title": work["romanized"],
            "year": work["year"],
            "genres": list(work["genres"]),
            "url": (
                f"https://thetvdb.com/"
                f"{'movies' if work['kind'] == 'movie' else 'series'}/"
                f"{tvdb_id}"
            ),
            "cover_url": f"https://art.example/{work['key']}.jpg",
        }
        tvdb.append({
            "movies": [entry] if work["kind"] == "movie" else [],
            "series": [entry] if work["kind"] == "series" else [],
            "episodes_by_series": (
                {
                    tvdb_id: [{
                        "tvdb_episode_id": (
                            f"{tvdb_id}-s{season_number}e1"
                        ),
                        "season_number": season_number,
                        "episode_number": 1,
                        "name": f"Season {season_number} Episode 1",
                    } for season_number in range(
                        1,
                        len(season_years) + 1
                        if season_years
                        else 2,
                    )],
                }
                if work["kind"] == "series"
                else {}
            ),
        })

    if scenario.get("duplicate_first_fact"):
        wikipedia.append({
            **wikipedia[0],
            "language": "zh",
            "query": scenario["query"],
            "title": scenario["works"][0]["chinese"],
        })
        tvdb.append({
            **tvdb[0],
            "query": scenario["works"][0]["romanized"],
        })

    def provider(name, facts):
        return lambda _hypotheses: {
            "source": name,
            "status": "ok",
            "facts": facts,
        }

    return {
        "wikipedia": provider("wikipedia", wikipedia),
        "douban": provider("douban", douban),
        "tvdb": provider("tvdb", tvdb),
    }


def _candidate_editor(
    scenario,
    available_providers=("tvdb", "douban", "wikipedia"),
):
    available = set(available_providers)
    candidates = []
    for index, work in enumerate(scenario["works"], 1):
        tvdb_id = f"usability-{work['key']}"
        role = "movie" if work["kind"] == "movie" else "series_root"
        bindings = []
        if "tvdb" in available:
            bindings.append({
                "fact_id": f"tvdb:{work['kind']}:{tvdb_id}",
                "role": role,
                "season_number": None,
                "episode_number": None,
            })
        if "douban" in available:
            season_years = tuple(work.get("season_years") or ())
            if season_years:
                bindings.extend({
                    "fact_id": (
                        f"douban:usability-{index}-{work['key']}"
                        f"-s{season_number}"
                    ),
                    "role": "season",
                    "season_number": season_number,
                    "episode_number": None,
                } for season_number in range(1, len(season_years) + 1))
            else:
                bindings.append({
                    "fact_id": (
                        f"douban:usability-{index}-{work['key']}"
                    ),
                    "role": role,
                    "season_number": None,
                    "episode_number": None,
                })
        if "wikipedia" in available:
            bindings.append({
                "fact_id": f"wikipedia:Q-usability-{work['key']}",
                "role": role,
                "season_number": None,
                "episode_number": None,
            })
        candidates.append({
            "candidate_id": work["key"],
            "anchor_fact_id": bindings[0]["fact_id"],
            "identity_role": role,
            "intended_scope": (
                "movie" if work["kind"] == "movie" else "whole_series"
            ),
            "fact_bindings": bindings,
            "ai_confidence": 0.94,
            "ai_reason": "三个来源的标题、年份与媒体类型相互吻合。",
        })
    return lambda _context: {
        "status": "resolved",
        "candidates": candidates,
    }


class SearchUsabilityTest(unittest.IsolatedAsyncioTestCase):
    def test_corpus_contains_at_least_twelve_complex_series_families(self):
        complex_scenarios = [
            scenario
            for scenario in REAL_WORLD_SCENARIOS
            if scenario.get("complex_series")
        ]

        self.assertGreaterEqual(len(complex_scenarios), 12)
        self.assertTrue(all(
            len(scenario["works"]) >= 2
            and any(
                work["kind"] == "series"
                for work in scenario["works"]
            )
            for scenario in complex_scenarios
        ))
        covered_risks = {
            risk
            for scenario in complex_scenarios
            for risk in scenario.get("risks", ())
        }
        self.assertTrue({
            "multi_season",
            "same_title_reboot",
            "movie_series_collision",
            "regional_adaptation",
            "animation_live_action",
            "spin_off",
        }.issubset(covered_risks))
        self.assertTrue(all(
            any(len(work.get("season_years") or ()) >= 2
                for work in scenario["works"])
            for scenario in complex_scenarios
            if "multi_season" in scenario.get("risks", ())
        ))

    async def test_named_real_world_queries_produce_selectable_human_candidates(self):
        feature = SearchFeature(config={}, host=None)
        for scenario in REAL_WORLD_SCENARIOS:
            with self.subTest(query=scenario["query"]):
                plan = await build_confirmable_search_plan(
                    scenario["query"],
                    f"usability-{scenario['query']}",
                    _provider_sources(scenario),
                    lambda _contract: set(),
                    TemporarySpecialAllocator(),
                    candidate_editor=_candidate_editor(scenario),
                )

                candidates = plan["candidates"]
                self.assertEqual(len(candidates), len(scenario["works"]))
                self.assertTrue(all(
                    candidate["metadata_ready"]
                    and candidate["candidate_version"] == "v1"
                    and candidate["selectable"]
                    and len(candidate["source_links"]) >= 3
                    and candidate["prowlarr_queries"]
                    for candidate in candidates
                ))
                self.assertEqual(
                    {
                        candidate["media_metadata"]["placement"][
                            "category_kind"
                        ]
                        for candidate in candidates
                    },
                    scenario["expected_categories"],
                )
                self.assertEqual(
                    len({
                        (
                            candidate["media_metadata"]["identity"][
                                "chinese_title"
                            ],
                            candidate["media_metadata"]["identity"]["year"],
                            candidate["media_metadata"]["placement"][
                                "library_type"
                            ],
                        )
                        for candidate in candidates
                    }),
                    len(candidates),
                )

                action = feature._candidate_grid_action({
                    "plan": plan,
                    "candidates": candidates,
                })
                visible = html.unescape(
                    re.sub(r"<[^>]+>", "", action["text"])
                )
                self.assertIn("请选择作品候选", visible)
                self.assertIn("来源：豆瓣", visible)
                self.assertNotIn("来源完整", visible)
                self.assertNotIn("匹配参考", visible)
                self.assertNotIn("维基百科", visible)
                self.assertIn("豆瓣", visible)
                self.assertNotIn("TVDB", visible)
                self.assertEqual(
                    action["data"]["keyboard"][-1],
                    [{
                        "text": "都不是",
                        "callback_data": (
                            f"search:reject:{plan['plan_id']}"
                        ),
                    }],
                )
                for internal_label in (
                    "candidate_id",
                    "fact_id",
                    "canonical_latin_title",
                    "metadata_ready",
                    "series_root",
                    " · v1",
                ):
                    self.assertNotIn(internal_label, visible)

    async def test_rate_limited_wikipedia_does_not_erase_grounded_candidates(self):
        scenario = REAL_WORLD_SCENARIOS[0]
        providers = _provider_sources(scenario)
        providers["wikipedia"] = lambda _hypotheses: {
            "source": "wikipedia",
            "status": "rate_limited",
            "facts": [],
        }

        plan = await build_confirmable_search_plan(
            scenario["query"],
            "usability-wikipedia-limited",
            providers,
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            candidate_editor=_candidate_editor(
                scenario,
                ("tvdb", "douban"),
            ),
        )

        self.assertEqual(len(plan["candidates"]), 3)
        self.assertTrue(all(
            candidate["selectable"]
            and candidate["metadata_ready"]
            and candidate["candidate_version"] == "v0"
            and "wikipedia:rate_limited"
            in candidate["unresolved_sources"]
            for candidate in plan["candidates"]
        ))
        action = SearchFeature(
            config={},
            host=None,
        )._candidate_grid_action({
            "plan": plan,
            "candidates": plan["candidates"],
        })
        self.assertIn("来源：豆瓣", action["text"])
        self.assertNotIn("维基百科查询受限", action["text"])
        self.assertNotIn("wikipedia:rate_limited", action["text"])

    async def test_missing_tvdb_keeps_movie_and_series_candidates_visible(self):
        scenario = REAL_WORLD_SCENARIOS[1]
        providers = _provider_sources(scenario)
        providers["tvdb"] = lambda _hypotheses: {
            "source": "tvdb",
            "status": "credential_missing",
            "facts": [],
        }

        plan = await build_confirmable_search_plan(
            scenario["query"],
            "usability-tvdb-missing",
            providers,
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            candidate_editor=_candidate_editor(
                scenario,
                ("douban", "wikipedia"),
            ),
        )

        self.assertEqual(len(plan["candidates"]), 2)
        self.assertTrue(all(
            candidate["selectable"]
            and candidate["candidate_version"] == "v0"
            and "tvdb:credential_missing"
            in candidate["unresolved_sources"]
            for candidate in plan["candidates"]
        ))
        readiness = {
            candidate["media_metadata"]["placement"]["library_type"]:
            candidate["metadata_ready"]
            for candidate in plan["candidates"]
        }
        self.assertEqual(readiness, {
            "series": True,
            "movie": True,
        })
        series = next(
            candidate
            for candidate in plan["candidates"]
            if candidate["media_metadata"]["placement"]["library_type"]
            == "series"
        )
        self.assertIn(
            "warning:tvdb_inventory_unavailable",
            series["media_metadata"]["warnings"],
        )
        action = SearchFeature(
            config={},
            host=None,
        )._candidate_grid_action({
            "plan": plan,
            "candidates": plan["candidates"],
        })
        self.assertNotIn("TVDB缺少凭据", action["text"])
        self.assertNotIn("tvdb:credential_missing", action["text"])

    async def test_japanese_animation_without_source_backed_latin_title_stays_unready(self):
        scenario = {
            "query": "オッドタクシー",
            "works": ({
                "key": "odd-taxi-kana-fallback",
                "kind": "series",
                "year": "2021",
                "chinese": "奇巧计程车",
                "original": "オッドタクシー",
                "language": "ja",
                "english": "",
                "romanized": "",
                "genres": ("Anime",),
            },),
            "expected_categories": {"animated_series"},
        }

        plan = await build_confirmable_search_plan(
            scenario["query"],
            "usability-kana-fallback",
            _provider_sources(scenario),
            lambda _contract: set(),
            TemporarySpecialAllocator(),
            candidate_editor=_candidate_editor(scenario),
        )

        candidate = plan["candidates"][0]
        identity = candidate["media_metadata"]["identity"]
        self.assertFalse(candidate["metadata_ready"])
        self.assertEqual(candidate["metadata_error"]["code"], "metadata_incomplete")
        self.assertIn(
            "canonical_latin_title",
            candidate["metadata_error"]["missing_fields"],
        )
        self.assertEqual(
            identity.get("romanized_original_title", ""),
            "",
        )
        self.assertEqual(identity.get("english_title", ""), "オッドタクシー")
        self.assertNotEqual(identity.get("english_title", ""), "Oddotakushii")


if __name__ == "__main__":
    unittest.main()
