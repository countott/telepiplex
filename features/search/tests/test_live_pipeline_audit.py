import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from telepiplex_search.live_pipeline_audit import (
    audit_live_full_case,
    audit_full_case,
    audit_root_case,
    load_real_media_corpus,
)


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/real_media_corpus.json"


class RealMediaCorpusContractTest(unittest.TestCase):
    def test_corpus_exercises_broad_real_pipeline_dimensions(self):
        cases = load_real_media_corpus(CORPUS)

        self.assertGreaterEqual(len(cases), 60)
        self.assertGreaterEqual(
            len({case["country_group"] for case in cases}),
            12,
        )
        self.assertEqual(
            {case["media_type"] for case in cases},
            {"movie", "series"},
        )
        self.assertTrue(any(case["ambiguity_group"] for case in cases))
        self.assertTrue(any(case["scope"] == "season" for case in cases))
        self.assertTrue(any(case["scope"] == "episode" for case in cases))
        self.assertTrue(any(case["single_season"] is True for case in cases))
        self.assertTrue(any(case["multi_season"] is True for case in cases))
        self.assertGreaterEqual(
            sum(case["full_pipeline"] for case in cases),
            20,
        )
        self.assertGreaterEqual(
            sum(case["japanese_animation"] for case in cases),
            5,
        )

    def test_corpus_has_unique_ids_and_literal_expected_root_identity(self):
        raw = json.loads(CORPUS.read_text(encoding="utf-8"))
        cases = load_real_media_corpus(CORPUS)

        self.assertEqual(len(cases), len(raw))
        self.assertEqual(len({case["case_id"] for case in cases}), len(cases))
        self.assertTrue(all(case["query"] for case in cases))
        self.assertTrue(all(case["year"] for case in cases))
        self.assertTrue(all(case["expected_titles"] for case in cases))

    def test_root_audit_finds_literal_expected_identity_and_records_boundaries(self):
        case = {
            "case_id": "fargo-series",
            "query": "Fargo",
            "expected_titles": ["Fargo", "冰血暴"],
            "year": "2014",
            "media_type": "series",
            "scope": "work",
            "season_number": None,
            "episode_number": None,
        }

        report = audit_root_case(
            case,
            wikipedia_lookup=lambda _payload: {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "language": "en",
                    "search_rank": 1,
                    "page_id": 1,
                    "is_disambiguation": False,
                    "title": "Fargo (TV series)",
                    "url": "https://en.wikipedia.org/wiki/Fargo_(TV_series)",
                    "wikibase_item": "Q15931555",
                }],
            },
            wikidata_lookup=lambda qids: {
                qid: {
                    "wikibase_item": qid,
                    "chinese_title": "冰血暴",
                    "english_title": "Fargo",
                    "aliases": [],
                    "media_type": "series",
                    "year": "2014",
                    "countries": [],
                    "season_count": 5,
                    "episode_count": 51,
                }
                for qid in qids
            },
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["matched_qid"], "Q15931555")
        self.assertEqual(
            report["stages"],
            {
                "input": "ok",
                "wikipedia": "ok",
                "wikidata": "ok",
                "root_match": "ok",
            },
        )

    def test_root_audit_records_wikidata_failure_without_aborting_batch(self):
        report = audit_root_case(
            {
                "case_id": "provider-timeout",
                "query": "Fargo",
                "expected_titles": ["Fargo"],
                "year": "2014",
                "media_type": "series",
                "scope": "work",
                "season_number": None,
                "episode_number": None,
            },
            wikipedia_lookup=lambda _payload: {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "language": "en",
                    "search_rank": 1,
                    "page_id": 1,
                    "is_disambiguation": False,
                    "title": "Fargo (TV series)",
                    "url": "https://en.wikipedia.org/wiki/Fargo_(TV_series)",
                    "wikibase_item": "Q15931555",
                }],
            },
            wikidata_lookup=lambda _qids: (_ for _ in ()).throw(
                TimeoutError("upstream timeout")
            ),
        )

        self.assertFalse(report["passed"])
        self.assertEqual(report["stages"]["wikidata"], "TimeoutError")
        self.assertEqual(report["failure_code"], "expected_root_not_found")

    def test_root_audit_accepts_verified_wikidata_only_root_when_wikipedia_has_no_page(self):
        case = {
            "case_id": "tokyo-love-story",
            "query": "东京爱情故事 1991 剧集",
            "expected_titles": ["东京爱情故事", "Tokyo Love Story"],
            "year": "1991",
            "media_type": "series",
            "scope": "work",
            "season_number": None,
            "episode_number": None,
        }

        report = audit_root_case(
            case,
            wikipedia_lookup=lambda _payload: {
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
            wikidata_lookup=lambda qids: {
                qid: {
                    "wikibase_item": qid,
                    "chinese_title": "东京爱情故事",
                    "english_title": "Tokyo Love Story",
                    "aliases": [],
                    "media_type": "series",
                    "year": "1991",
                    "countries": ["Q17"],
                    "season_count": 1,
                    "episode_count": 11,
                }
                for qid in qids
                if qid == "Q130345128"
            },
            wikidata_search=lambda _query: ["Q706584", "Q130345128"],
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["matched_qid"], "Q130345128")

    def test_full_audit_validates_root_contract_scope_query_and_sdk_round_trip(self):
        case = {
            "case_id": "veep-season-one",
            "query": "Veep S01",
            "expected_titles": ["Veep", "副人之仁"],
            "year": "2012",
            "media_type": "series",
            "scope": "season",
            "season_number": 1,
            "episode_number": None,
        }
        wikipedia = lambda _payload: {
            "source": "wikipedia",
            "status": "ok",
            "facts": [{
                "language": "en",
                "search_rank": 1,
                "page_id": 1,
                "is_disambiguation": False,
                "title": "Veep",
                "url": "https://en.wikipedia.org/wiki/Veep",
                "wikibase_item": "Q74801",
            }],
        }
        wikidata = lambda qids: {
            qid: {
                "wikibase_item": qid,
                "chinese_title": "副人之仁",
                "english_title": "Veep",
                "aliases": [],
                "media_type": "series",
                "year": "2012",
                "countries": [],
                "season_count": 7,
                "episode_count": 65,
            }
            for qid in qids
        }

        report = audit_full_case(
            case,
            wikipedia_lookup=wikipedia,
            wikidata_lookup=wikidata,
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["retrieval_scope"], "season")
        self.assertEqual(
            report["queries"],
            ["Veep S01", "Veep Season 01"],
        )
        self.assertEqual(report["sdk_metadata_id"], "audit:veep-season-one")
        self.assertEqual(report["stages"]["downstream_contract"], "ok")


class LiveFullAuditSafetyContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_episode_inventory_is_an_expected_safe_rejection(self):
        case = {
            "case_id": "office-episode",
            "query": "The Office S02E03",
            "expected_titles": ["The Office"],
            "year": "2005",
            "media_type": "series",
            "scope": "episode",
            "season_number": 2,
            "episode_number": 3,
        }
        candidate = {
            "candidate_id": "wikipedia:Q23831",
            "media_metadata": {
                "identity": {
                    "content_kind": "series",
                    "english_title": "The Office",
                    "year": "2005",
                },
            },
        }
        plan = {"candidates": [candidate]}
        feature = SimpleNamespace(
            _wikipedia_provider=lambda _payload: {},
            _supplement_selected_candidate=lambda selected, _query: selected,
        )

        async def supplement(selected, _query):
            return selected

        feature._supplement_selected_candidate = supplement
        with (
            patch(
                "telepiplex_search.live_pipeline_audit.build_root_work_search_plan",
                return_value=plan,
            ),
            patch(
                "telepiplex_search.live_pipeline_audit.hydrate_frozen_candidate",
                side_effect=Exception("metadata_incomplete:verified_scope"),
            ),
        ):
            report = await audit_live_full_case(
                case,
                feature,
                wikipedia_lookup=lambda _payload: {},
                wikidata_lookup=lambda _qids: {},
            )

        self.assertTrue(report["passed"])
        self.assertEqual(report["outcome"], "safe_rejection")
        self.assertEqual(report["stages"]["exact_read"], "safe_rejected")


if __name__ == "__main__":
    unittest.main()
