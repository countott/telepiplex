import unittest
from unittest.mock import patch

from telepiplex_search.input_contract import classify_search_input
from telepiplex_search.service import SearchFeature
from telepiplex_search.work_discovery import (
    build_root_work_search_plan,
    discover_root_works,
)


def wikipedia_result():
    return {
        "source": "wikipedia",
        "status": "ok",
        "facts": [
            {
                "language": "zh",
                "search_rank": 1,
                "page_id": 101,
                "is_disambiguation": True,
                "title": "副总统",
                "extract": "消歧义页面",
                "url": "https://zh.wikipedia.org/wiki/副总统",
                "wikibase_item": "Q-disambiguation",
            },
            {
                "language": "zh",
                "search_rank": 2,
                "page_id": 102,
                "is_disambiguation": False,
                "title": "副总统 (电视剧)",
                "chinese_title": "副总统",
                "official_english_title": "Veep",
                "extract": "美国电视喜剧",
                "url": "https://zh.wikipedia.org/wiki/副总统_(电视剧)",
                "wikibase_item": "Q74801",
            },
            {
                "language": "zh",
                "search_rank": 3,
                "page_id": 103,
                "is_disambiguation": False,
                "title": "副总统先生",
                "chinese_title": "副总统先生",
                "official_english_title": "Mr. Vice President",
                "extract": "一部电影",
                "url": "https://zh.wikipedia.org/wiki/副总统先生",
                "wikibase_item": "Q200",
            },
            {
                "language": "zh",
                "search_rank": 4,
                "page_id": 104,
                "is_disambiguation": False,
                "title": "副总统列表",
                "extract": "人物列表",
                "url": "https://zh.wikipedia.org/wiki/副总统列表",
                "wikibase_item": "Q300",
            },
            {
                "language": "en",
                "search_rank": 1,
                "page_id": 201,
                "is_disambiguation": False,
                "title": "Veep",
                "english_title": "Veep",
                "extract": "American television series",
                "url": "https://en.wikipedia.org/wiki/Veep",
                "wikibase_item": "Q74801",
            },
        ],
        "source_urls": [],
        "error": "",
    }


class WorkDiscoveryTest(unittest.TestCase):
    def test_top_non_media_wikidata_english_title_gets_one_bounded_retry(self):
        calls = []

        def wikipedia(payload):
            calls.append(payload)
            query = payload["source_queries"]["wikipedia_en"][0]
            if query.startswith("冰果"):
                return {
                    "source": "wikipedia",
                    "status": "ok",
                    "facts": [{
                        "language": "zh",
                        "search_rank": 1,
                        "page_id": 1,
                        "is_disambiguation": False,
                        "title": "冰菓 (小说)",
                        "url": "https://zh.wikipedia.org/wiki/冰菓",
                        "wikibase_item": "Q1339165",
                    }],
                }
            return {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "language": "en",
                    "search_rank": 1,
                    "page_id": 2,
                    "is_disambiguation": False,
                    "title": "Hyouka (TV series)",
                    "url": "https://en.wikipedia.org/wiki/Hyouka_(TV_series)",
                    "wikibase_item": "Q99853668",
                }],
            }

        entities = {
            "Q1339165": {
                "wikibase_item": "Q1339165",
                "chinese_title": "冰菓",
                "english_title": "Hyouka",
                "aliases": ["冰果"],
                "media_type": "",
                "year": "2001",
                "countries": ["Q17"],
            },
            "Q99853668": {
                "wikibase_item": "Q99853668",
                "chinese_title": "冰菓",
                "english_title": "Hyouka",
                "aliases": [],
                "media_type": "series",
                "year": "2012",
                "countries": ["Q17"],
                "season_count": 1,
                "episode_count": 22,
            },
            "Q17": {
                "chinese_title": "日本",
                "english_title": "Japan",
            },
        }

        roots = discover_root_works(
            classify_search_input("冰果 2012 剧集"),
            wikipedia,
            lambda qids: {
                qid: entities[qid] for qid in qids if qid in entities
            },
        )

        self.assertEqual([item["qid"] for item in roots], ["Q99853668"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[1]["source_queries"]["wikipedia_en"],
            ["Hyouka 2012 TV series", "Hyouka"],
        )

    def test_non_media_retry_is_capped_when_alias_still_has_no_media_root(self):
        calls = []
        result = discover_root_works(
            classify_search_input("Monster 2004 剧集"),
            lambda payload: calls.append(payload) or {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "language": "en",
                    "search_rank": 1,
                    "page_id": 1,
                    "is_disambiguation": False,
                    "title": "Monster (manga)",
                    "url": "https://en.wikipedia.org/wiki/Monster_(manga)",
                    "wikibase_item": "Q858384",
                }],
            },
            lambda _qids: {"Q858384": {
                "wikibase_item": "Q858384",
                "chinese_title": "MONSTER",
                "english_title": "Monster",
                "aliases": ["Monsutā"],
                "media_type": "",
                "year": "1994",
                "countries": ["Q17"],
            }},
        )

        self.assertEqual(result, [])
        self.assertEqual(len(calls), 2)

    def test_wikidata_fallback_follows_one_verified_adaptation_edge(self):
        entities = {
            "Q858384": {
                "wikibase_item": "Q858384",
                "chinese_title": "MONSTER",
                "english_title": "Monster",
                "aliases": [],
                "media_type": "",
                "year": "1994",
                "countries": ["Q17"],
                "adaptation_ids": ["Q100944081"],
            },
            "Q100944081": {
                "wikibase_item": "Q100944081",
                "chinese_title": "",
                "english_title": "Monster",
                "aliases": [],
                "media_type": "series",
                "year": "2004",
                "countries": ["Q17"],
                "original_language": "ja",
                "genres": ["anime"],
                "season_count": 1,
                "episode_count": 74,
            },
            "Q17": {
                "chinese_title": "日本",
                "english_title": "Japan",
            },
        }

        roots = discover_root_works(
            classify_search_input("Monster 2004 剧集"),
            lambda _payload: {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "language": "en",
                    "search_rank": 1,
                    "page_id": 1,
                    "is_disambiguation": False,
                    "title": "Monster (manga)",
                    "url": "https://en.wikipedia.org/wiki/Monster_(manga)",
                    "wikibase_item": "Q858384",
                }],
            },
            lambda qids: {
                qid: entities[qid] for qid in qids if qid in entities
            },
            wikidata_search=lambda _title: ["Q858384"],
        )

        self.assertEqual([item["qid"] for item in roots], ["Q100944081"])
        self.assertEqual(roots[0]["source_provider"], "wikidata")
        self.assertEqual(roots[0]["season_count"], 1)

    def test_wikidata_only_plan_does_not_fabricate_wikipedia_external_id(self):
        entities = {
            "Q130345128": {
                "wikibase_item": "Q130345128",
                "chinese_title": "东京爱情故事",
                "english_title": "Tokyo Love Story",
                "aliases": [],
                "media_type": "series",
                "year": "1991",
                "countries": ["Q17"],
                "season_count": 1,
                "episode_count": 11,
            },
            "Q17": {"chinese_title": "日本", "english_title": "Japan"},
        }
        plan = build_root_work_search_plan(
            "东京爱情故事 1991 剧集",
            "wikidata-only-plan",
            lambda _payload: {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "language": "zh",
                    "search_rank": 1,
                    "page_id": 1,
                    "is_disambiguation": False,
                    "title": "东京爱情故事",
                    "url": "https://zh.wikipedia.org/wiki/东京爱情故事",
                    "wikibase_item": "Q706584",
                }],
            },
            lambda qids: {
                qid: entities[qid] for qid in qids if qid in entities
            },
            wikidata_search=lambda _title: ["Q130345128"],
        )

        external_ids = plan["candidates"][0]["media_metadata"]["identity"][
            "external_ids"
        ]
        self.assertEqual(external_ids, {"wikidata": "Q130345128"})

    def test_wikidata_fallback_follows_relevant_part_then_adaptation(self):
        entities = {
            "Q60985207": {
                "wikibase_item": "Q60985207",
                "chinese_title": "〈古籍研究社〉系列",
                "english_title": "Classic Literature Club series",
                "aliases": ["冰菓系列", "冰果"],
                "media_type": "",
                "year": "",
                "part_ids": ["Q1339165"],
            },
            "Q1339165": {
                "wikibase_item": "Q1339165",
                "chinese_title": "冰菓",
                "english_title": "Hyouka",
                "aliases": ["冰果"],
                "media_type": "",
                "year": "2001",
                "adaptation_ids": ["Q99853668", "Q58411052"],
            },
            "Q99853668": {
                "wikibase_item": "Q99853668",
                "chinese_title": "冰菓",
                "english_title": "Hyouka",
                "aliases": [],
                "media_type": "series",
                "year": "2012",
                "countries": ["Q17"],
                "original_language": "ja",
                "genres": ["anime"],
                "season_count": 1,
                "episode_count": 22,
            },
            "Q58411052": {
                "wikibase_item": "Q58411052",
                "chinese_title": "冰菓",
                "english_title": "Hyouka",
                "aliases": ["冰果"],
                "media_type": "movie",
                "year": "2017",
                "countries": ["Q17"],
            },
            "Q17": {"chinese_title": "日本", "english_title": "Japan"},
        }

        roots = discover_root_works(
            classify_search_input("冰果 2012 剧集"),
            lambda _payload: {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "language": "zh",
                    "search_rank": 1,
                    "page_id": 1,
                    "is_disambiguation": False,
                    "title": "古籍研究社系列",
                    "url": "https://zh.wikipedia.org/wiki/古籍研究社系列",
                    "wikibase_item": "Q60985207",
                }],
            },
            lambda qids: {
                qid: entities[qid] for qid in qids if qid in entities
            },
            wikidata_search=lambda _title: [],
        )

        self.assertEqual([item["qid"] for item in roots], ["Q99853668"])

    def test_same_year_and_type_without_title_relevance_is_rejected(self):
        roots = discover_root_works(
            classify_search_input("Monster 2004 剧集"),
            lambda _payload: {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "language": "en",
                    "search_rank": 1,
                    "page_id": 1,
                    "is_disambiguation": False,
                    "title": "Lost",
                    "url": "https://en.wikipedia.org/wiki/Lost_(TV_series)",
                    "wikibase_item": "Q23567",
                }],
            },
            lambda qids: {
                "Q23567": {
                    "wikibase_item": "Q23567",
                    "chinese_title": "迷失",
                    "english_title": "Lost",
                    "aliases": ["Lost: The Full Story"],
                    "media_type": "series",
                    "year": "2004",
                    "countries": ["Q30"],
                },
            },
        )

        self.assertEqual(roots, [])

    def test_lead_extract_title_preserves_a_regional_wikipedia_alias(self):
        roots = discover_root_works(
            classify_search_input("副总统 2012 剧集"),
            lambda _payload: {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "language": "zh",
                    "search_rank": 1,
                    "page_id": 1,
                    "is_disambiguation": False,
                    "title": "副人之仁",
                    "extract": "《副总统》（英语：Veep）是一部美国情景喜剧。",
                    "url": "https://zh.wikipedia.org/wiki/副人之仁",
                    "wikibase_item": "Q74801",
                }],
            },
            lambda qids: {
                "Q74801": {
                    "wikibase_item": "Q74801",
                    "chinese_title": "副人之仁",
                    "english_title": "Veep",
                    "aliases": [],
                    "media_type": "series",
                    "year": "2012",
                    "countries": [],
                    "season_count": 7,
                    "episode_count": 65,
                },
            },
        )

        self.assertEqual([item["qid"] for item in roots], ["Q74801"])

    def test_untyped_title_expands_to_movie_and_series_queries(self):
        calls = []
        with self.assertRaisesRegex(Exception, "no_match"):
            build_root_work_search_plan(
                "副总统",
                "untyped-language-query",
                lambda payload: calls.append(payload) or {
                    "source": "wikipedia",
                    "status": "not_found",
                    "facts": [],
                },
                lambda _qids: {},
            )

        self.assertEqual(
            calls[0]["source_queries"],
            {
                "wikipedia_zh": [
                    "副总统 电视剧",
                    "副总统 电影",
                ],
                "wikipedia_en": [
                    "副总统 TV series",
                    "副总统 film",
                ],
            },
        )

    def test_source_queries_use_language_specific_media_type_terms(self):
        calls = []
        with self.assertRaisesRegex(Exception, "no_match"):
            build_root_work_search_plan(
                "The Office S02E03",
                "office-language-query",
                lambda payload: calls.append(payload) or wikipedia_result(),
                lambda qids: {
                    "Q74801": {
                        "wikibase_item": "Q74801",
                        "chinese_title": "副总统",
                        "english_title": "Veep",
                        "aliases": [],
                        "media_type": "series",
                        "year": "2012",
                        "countries": [],
                        "season_count": 7,
                        "episode_count": 65,
                    },
                },
            )

        self.assertEqual(
            calls[0]["source_queries"],
            {
                "wikipedia_zh": ["The Office 电视剧", "The Office"],
                "wikipedia_en": ["The Office TV series", "The Office"],
            },
        )
    def test_filters_by_exact_identity_deduplicates_qid_and_keeps_rank(self):
        calls = []

        def wikidata_lookup(qids):
            calls.append(tuple(qids))
            if qids == ["Q30"]:
                return {"Q30": {"chinese_title": "美国", "english_title": "United States"}}
            return {
                "Q74801": {
                    "wikibase_item": "Q74801",
                    "chinese_title": "副总统",
                    "english_title": "Veep",
                    "aliases": ["副人之仁"],
                    "media_type": "series",
                    "year": "2012",
                    "countries": ["Q30"],
                    "season_count": 7,
                    "episode_count": 65,
                },
                "Q200": {
                    "wikibase_item": "Q200",
                    "chinese_title": "副总统先生",
                    "english_title": "Mr. Vice President",
                    "aliases": [],
                    "media_type": "movie",
                    "year": "2015",
                    "countries": ["Q30"],
                    "season_count": None,
                    "episode_count": None,
                },
                "Q300": {
                    "wikibase_item": "Q300",
                    "chinese_title": "副总统列表",
                    "english_title": "List of vice presidents",
                    "aliases": [],
                    "media_type": "",
                    "year": "",
                    "countries": [],
                    "season_count": None,
                    "episode_count": None,
                },
            }

        candidates = discover_root_works(
            classify_search_input("副总统"),
            lambda _payload: wikipedia_result(),
            wikidata_lookup,
        )

        self.assertEqual(
            [item["qid"] for item in candidates],
            ["Q74801"],
        )
        self.assertEqual(candidates[0]["display_title"], "副总统")
        self.assertEqual(candidates[0]["english_title"], "Veep")
        self.assertEqual(candidates[0]["countries"], ["美国"])
        self.assertEqual(candidates[0]["season_count"], 7)
        self.assertEqual(calls[-1], ("Q30",))

    def test_explicit_year_and_media_type_are_hard_filters(self):
        entities = {
            "Q74801": {
                "wikibase_item": "Q74801",
                "chinese_title": "副总统",
                "english_title": "Veep",
                "aliases": [],
                "media_type": "series",
                "year": "2012",
                "countries": [],
                "season_count": 7,
                "episode_count": 65,
            },
            "Q200": {
                "wikibase_item": "Q200",
                "chinese_title": "副总统先生",
                "english_title": "Mr. Vice President",
                "aliases": [],
                "media_type": "movie",
                "year": "2015",
                "countries": [],
                "season_count": None,
                "episode_count": None,
            },
        }

        candidates = discover_root_works(
            classify_search_input("副总统 2012 剧集"),
            lambda _payload: wikipedia_result(),
            lambda qids: {qid: entities[qid] for qid in qids if qid in entities},
        )

        self.assertEqual([item["qid"] for item in candidates], ["Q74801"])

    def test_root_plan_never_auto_confirms_a_unique_text_result(self):
        entities = {
            "Q74801": {
                "wikibase_item": "Q74801",
                "chinese_title": "副总统",
                "english_title": "Veep",
                "aliases": [],
                "media_type": "series",
                "year": "2012",
                "countries": [],
                "season_count": 7,
                "episode_count": 65,
            },
            "Q200": {
                "wikibase_item": "Q200",
                "chinese_title": "副总统先生",
                "english_title": "Mr. Vice President",
                "aliases": [],
                "media_type": "",
                "year": "2015",
                "countries": [],
                "season_count": None,
                "episode_count": None,
            },
            "Q300": {
                "wikibase_item": "Q300",
                "chinese_title": "副总统列表",
                "english_title": "List of vice presidents",
                "aliases": [],
                "media_type": "",
                "year": "",
                "countries": [],
                "season_count": None,
                "episode_count": None,
            },
        }

        plan = build_root_work_search_plan(
            "副总统",
            "root-plan",
            lambda _payload: wikipedia_result(),
            lambda qids: {qid: entities[qid] for qid in qids if qid in entities},
        )

        self.assertFalse(plan["auto_confirm"])
        self.assertEqual(plan["selection_mode"], "user_root_identity")
        self.assertEqual(len(plan["candidates"]), 1)
        candidate = plan["candidates"][0]
        self.assertEqual(candidate["candidate_id"], "wikipedia:Q74801")
        self.assertTrue(candidate["links_frozen"])
        self.assertEqual(
            candidate["media_metadata"]["identity"]["chinese_title"],
            "副总统",
        )
        self.assertEqual(
            candidate["media_metadata"]["identity"]["season_count"],
            7,
        )

    def test_explicit_season_is_bound_to_the_selected_root_for_later_verification(self):
        entities = {
            "Q74801": {
                "wikibase_item": "Q74801",
                "chinese_title": "副总统",
                "english_title": "Veep",
                "aliases": [],
                "media_type": "series",
                "year": "2012",
                "countries": [],
                "season_count": 7,
                "episode_count": 65,
            },
            "Q200": {"media_type": ""},
            "Q300": {"media_type": ""},
        }

        plan = build_root_work_search_plan(
            "Veep S01",
            "root-season-plan",
            lambda _payload: wikipedia_result(),
            lambda qids: {qid: entities[qid] for qid in qids if qid in entities},
        )

        candidate = plan["candidates"][0]
        self.assertEqual(candidate["identity_role"], "season")
        self.assertEqual(candidate["intended_scope"], "season")
        self.assertEqual(candidate["requested_season_number"], 1)
        self.assertEqual(candidate["source_links"][0]["role"], "season")
        self.assertEqual(candidate["source_links"][0]["season_number"], 1)
        self.assertEqual(
            candidate["source_links"][0]["verification"],
            "wikipedia_season_count_verified",
        )
        self.assertIsNone(
            candidate["source_links"][0]["proposed_season_number"]
        )


class WorkDiscoveryServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_plain_title_route_uses_wikipedia_without_search_ai_or_douban(self):
        feature = SearchFeature(config={}, host=None)
        entities = {
            "Q74801": {
                "wikibase_item": "Q74801",
                "chinese_title": "副总统",
                "english_title": "Veep",
                "aliases": [],
                "media_type": "series",
                "year": "2012",
                "countries": [],
                "season_count": 7,
                "episode_count": 65,
            },
            "Q200": {"media_type": ""},
            "Q300": {"media_type": ""},
        }

        with (
            patch.object(
                feature,
                "_wikipedia_provider",
                return_value=wikipedia_result(),
            ) as wikipedia,
            patch.object(
                feature,
                "_douban_provider",
                side_effect=AssertionError("Douban discovery must not run"),
            ),
            patch(
                "telepiplex_search.service.enrich_wikidata_entities",
                side_effect=lambda qids: {
                    qid: entities[qid]
                    for qid in qids
                    if qid in entities
                },
            ),
        ):
            plan = await feature._build_plan("副总统", "service-root")

        self.assertEqual(plan["selection_mode"], "user_root_identity")
        self.assertFalse(plan["auto_confirm"])
        self.assertEqual(
            [item["candidate_id"] for item in plan["candidates"]],
            ["wikipedia:Q74801"],
        )
        wikipedia.assert_called_once()

    async def test_exact_douban_binding_localizes_candidate_before_display(self):
        feature = SearchFeature(config={}, host=None)
        entity = {
            "wikibase_item": "Q124175370",
            "chinese_title": "百年孤寂",
            "english_title": "One Hundred Years of Solitude",
            "aliases": [],
            "media_type": "series",
            "year": "2024",
            "countries": [],
            "season_count": 2,
            "episode_count": 16,
            "external_ids": {
                "wikidata": "Q124175370",
                "douban_subject": "30482958",
            },
        }
        wikipedia = {
            "source": "wikipedia",
            "status": "ok",
            "facts": [{
                "language": "zh",
                "search_rank": 1,
                "page_id": 1,
                "is_disambiguation": False,
                "title": "百年孤寂",
                "extract": "《百年孤独》电视剧",
                "url": "https://zh.wikipedia.org/wiki/百年孤寂",
                "wikibase_item": "Q124175370",
            }],
        }
        with (
            patch.object(feature, "_wikipedia_provider", return_value=wikipedia),
            patch(
                "telepiplex_search.service.search_wikidata_entities",
                return_value=["Q124175370"],
            ),
            patch(
                "telepiplex_search.service.enrich_wikidata_entities",
                side_effect=lambda qids: {
                    qid: entity for qid in qids if qid == "Q124175370"
                },
            ),
            patch(
                "telepiplex_search.service.lookup_douban_subject",
                return_value={
                    "subject_id": "30482958",
                    "url": "https://movie.douban.com/subject/30482958/",
                    "douban_title_raw": "百年孤独 第一季",
                    "chinese_title": "百年孤独",
                    "english_title": "One Hundred Years of Solitude",
                    "media_type": "series",
                    "season_number": 1,
                    "external_ids": {"douban_subject": "30482958"},
                },
            ),
        ):
            plan = await feature._build_plan("百年孤独", "localized-candidate")

        candidate = plan["candidates"][0]
        self.assertEqual(
            candidate["media_metadata"]["identity"]["chinese_title"],
            "百年孤独",
        )
        self.assertEqual(
            [link["provider"] for link in candidate["source_links"]],
            ["wikipedia", "douban"],
        )

    @staticmethod
    def _lookup(entities):
        return lambda qids: {
            qid: entities[qid] for qid in qids if qid in entities
        }

    def test_weak_partial_hit_cannot_suppress_verified_one_piece_graph(self):
        entities = {
            "Q85884426": {
                "wikibase_item": "Q85884426",
                "chinese_title": "贼王",
                "english_title": "King of Thieves",
                "aliases": [],
                "media_type": "movie",
                "year": "1998",
                "countries": [],
            },
            "Q28667972": {
                "wikibase_item": "Q28667972",
                "chinese_title": "海贼王",
                "english_title": "One Piece",
                "aliases": ["航海王"],
                "media_type": "",
                "year": "",
                "adaptation_ids": ["Q710324"],
                "part_ids": ["Q4431905"],
            },
            "Q710324": {
                "wikibase_item": "Q710324",
                "chinese_title": "海贼王",
                "english_title": "One Piece",
                "aliases": ["ONE PIECE"],
                "media_type": "series",
                "year": "1999",
                "countries": ["Q17"],
                "genres": ["anime"],
            },
            "Q4431905": {
                "wikibase_item": "Q4431905",
                "chinese_title": "海贼王剧场版",
                "english_title": "One Piece films",
                "aliases": [],
                "media_type": "",
                "year": "",
                "part_ids": ["Q1209459", "Q56313751"],
            },
            "Q1209459": {
                "wikibase_item": "Q1209459",
                "chinese_title": "海贼王 黄金岛冒险",
                "english_title": "One Piece: The Movie",
                "aliases": [],
                "media_type": "movie",
                "year": "2000",
                "countries": ["Q17"],
            },
            "Q56313751": {
                "wikibase_item": "Q56313751",
                "chinese_title": "航海王：夺宝争霸战",
                "english_title": "One Piece: Stampede",
                "aliases": [],
                "media_type": "movie",
                "year": "2019",
                "countries": ["Q17"],
            },
            "Q17": {"chinese_title": "日本", "english_title": "Japan"},
        }
        roots = discover_root_works(
            classify_search_input("海贼王"),
            lambda _payload: {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "language": "zh",
                    "search_rank": 1,
                    "page_id": 1,
                    "is_disambiguation": False,
                    "title": "贼王",
                    "url": "https://zh.wikipedia.org/wiki/贼王",
                    "wikibase_item": "Q85884426",
                }],
            },
            self._lookup(entities),
            wikidata_search=lambda _title: ["Q28667972", "Q85884426"],
        )

        self.assertEqual(
            [item["qid"] for item in roots],
            ["Q710324", "Q1209459", "Q56313751"],
        )
        self.assertNotIn("Q85884426", {item["qid"] for item in roots})
        self.assertTrue(all(item["relation_path"] for item in roots))

    def test_wikipedia_and_wikidata_exact_roots_are_always_unioned(self):
        qids = ["Q1987", "Q2009", "Q2007", "Q1950"]
        entities = {
            qid: {
                "wikibase_item": qid,
                "chinese_title": "男儿本色",
                "english_title": f"True Colours {year}",
                "aliases": [],
                "media_type": media_type,
                "year": year,
                "countries": [],
            }
            for qid, year, media_type in (
                ("Q1987", "1987", "series"),
                ("Q2009", "2009", "series"),
                ("Q2007", "2007", "movie"),
                ("Q1950", "1950", "movie"),
            )
        }
        roots = discover_root_works(
            classify_search_input("男儿本色"),
            lambda _payload: {
                "source": "wikipedia",
                "status": "ok",
                "facts": [
                    {
                        "language": "zh",
                        "search_rank": rank,
                        "page_id": rank,
                        "is_disambiguation": False,
                        "title": "男儿本色",
                        "url": f"https://zh.wikipedia.org/wiki/{qid}",
                        "wikibase_item": qid,
                    }
                    for rank, qid in enumerate(qids[:3], 1)
                ],
            },
            self._lookup(entities),
            wikidata_search=lambda _title: ["Q1950", "Q2007"],
        )

        self.assertEqual({item["qid"] for item in roots}, set(qids))

    async def test_wikipedia_disambiguation_link_routes_to_root_candidates(self):
        feature = SearchFeature(config={}, host=None)
        entities = {
            "Q74801": {
                "wikibase_item": "Q74801",
                "chinese_title": "副总统",
                "english_title": "Veep",
                "aliases": [],
                "media_type": "series",
                "year": "2012",
                "countries": [],
                "season_count": 7,
                "episode_count": 65,
            },
            "Q200": {"media_type": ""},
            "Q300": {"media_type": ""},
        }
        from telepiplex_search.direct_link import DirectLinkError

        with (
            patch(
                "telepiplex_search.service.resolve_direct_link",
                side_effect=DirectLinkError(
                    "wikipedia_disambiguation",
                    ("副总统",),
                ),
            ),
            patch.object(
                feature,
                "_wikipedia_provider",
                return_value=wikipedia_result(),
            ),
            patch(
                "telepiplex_search.service.enrich_wikidata_entities",
                side_effect=lambda qids: {
                    qid: entities[qid]
                    for qid in qids
                    if qid in entities
                },
            ),
        ):
            plan = await feature._build_plan(
                "https://zh.wikipedia.org/wiki/副总统",
                "disambiguation-link",
            )

        self.assertEqual(plan["selection_mode"], "user_root_identity")
        self.assertFalse(plan["auto_confirm"])
        self.assertEqual(plan["raw_query"], "副总统")

    async def test_unique_root_result_is_delivered_as_a_poster_grid_placeholder(self):
        entities = {
            "Q74801": {
                "wikibase_item": "Q74801",
                "chinese_title": "副总统",
                "english_title": "Veep",
                "aliases": [],
                "media_type": "series",
                "year": "2012",
                "countries": [],
                "season_count": 7,
                "episode_count": 65,
            },
            "Q200": {"media_type": ""},
            "Q300": {"media_type": ""},
        }
        plan = build_root_work_search_plan(
            "副总统",
            "unique-root",
            lambda _payload: wikipedia_result(),
            lambda qids: {qid: entities[qid] for qid in qids if qid in entities},
        )

        async def plan_builder(_query, _plan_id):
            return plan

        async def no_poster(_candidate, _provider):
            return ""

        feature = SearchFeature(
            config={},
            host=None,
            plan_builder=plan_builder,
            candidate_poster_lookup=no_poster,
        )
        result = await feature._prepare_plan(
            "副总统",
            {"chat_id": 1, "user_id": 2},
            plan_id="unique-root",
            operation_id="operation-1",
        )

        action = result["actions"][0]
        self.assertEqual(action["kind"], "send_photo_grid")
        self.assertEqual(action["data"]["poster_items"][0]["poster_url"], "")
        self.assertIn("来源：维基百科", action["text"])


if __name__ == "__main__":
    unittest.main()
