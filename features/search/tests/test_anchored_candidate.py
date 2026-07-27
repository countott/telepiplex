import unittest

from telepiplex_search.anchored_candidate import (
    CandidateBindingError,
    materialize_anchored_candidates,
)
from telepiplex_search.entity_graph import build_search_graph


def _honey_and_clover_graph():
    return build_search_graph([
        {
            "source": "tvdb",
            "status": "ok",
            "facts": [{
                "series": [{
                    "tvdb_series_id": "900",
                    "name": "Honey and Clover",
                    "chinese_title": "蜂蜜与四叶草",
                    "official_english_title": "Honey and Clover",
                    "year": "2005",
                    "url": "https://thetvdb.com/series/honey-and-clover",
                    "cover_url": "https://art.example/tvdb-root.jpg",
                }],
                "movies": [],
                "episodes_by_series": {
                    "900": [
                        {
                            "tvdb_episode_id": "s1e1",
                            "season_number": 1,
                            "episode_number": 1,
                        },
                        {
                            "tvdb_episode_id": "s2e1",
                            "season_number": 2,
                            "episode_number": 1,
                        },
                    ],
                },
            }],
        },
        {
            "source": "douban",
            "status": "ok",
            "facts": [
                {
                    "subject_id": "101",
                    "title": "蜂蜜与四叶草",
                    "original_title": "ハチミツとクローバー",
                    "year": "2005",
                    "media_type": "series",
                    "url": "https://movie.douban.com/subject/101/",
                    "cover_url": "https://art.example/douban-s1.jpg",
                },
                {
                    "subject_id": "102",
                    "title": "蜂蜜与四叶草II",
                    "original_title": "ハチミツとクローバーII",
                    "year": "2006",
                    "media_type": "series",
                    "url": "https://movie.douban.com/subject/102/",
                    "cover_url": "https://art.example/douban-s2.jpg",
                },
            ],
        },
        {
            "source": "wikipedia",
            "status": "ok",
            "facts": [{
                "wikibase_item": "Q1",
                "title": "Honey and Clover",
                "year": "2005",
                "media_type": "series",
                "url": "https://en.wikipedia.org/wiki/Honey_and_Clover",
            }],
        },
    ])


def _series_binding_payload(*, season_two=2):
    return {
        "status": "resolved",
        "candidates": [{
            "candidate_id": "candidate-1",
            "anchor_fact_id": "tvdb:900",
            "identity_role": "series_root",
            "intended_scope": "whole_series",
            "fact_bindings": [
                {
                    "fact_id": "tvdb:900",
                    "role": "series_root",
                    "season_number": None,
                    "episode_number": None,
                },
                {
                    "fact_id": "douban:101",
                    "role": "season",
                    "season_number": 1,
                    "episode_number": None,
                },
                {
                    "fact_id": "douban:102",
                    "role": "season",
                    "season_number": season_two,
                    "episode_number": None,
                },
                {
                    "fact_id": "wikipedia:Q1",
                    "role": "series_root",
                    "season_number": None,
                    "episode_number": None,
                },
            ],
            "ai_confidence": 0.96,
            "ai_reason": "TVDB root and both Douban season entries describe one series.",
        }],
    }


class AnchoredCandidateTest(unittest.TestCase):
    def test_materializes_all_verified_links_posters_and_season_roles(self):
        candidates = materialize_anchored_candidates(
            _honey_and_clover_graph(),
            _series_binding_payload(),
            provider_statuses={
                "wikipedia": "ok",
                "douban": "ok",
                "tvdb": "ok",
            },
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.anchor_fact_id, "tvdb:900")
        self.assertEqual(
            [(link.provider, link.role, link.season_number) for link in candidate.source_links],
            [
                ("tvdb", "series_root", None),
                ("douban", "season", 1),
                ("douban", "season", 2),
                ("wikipedia", "series_root", None),
            ],
        )
        self.assertEqual(
            {poster.url for poster in candidate.poster_assets},
            {
                "https://art.example/tvdb-root.jpg",
                "https://art.example/douban-s1.jpg",
                "https://art.example/douban-s2.jpg",
            },
        )
        self.assertEqual(candidate.unresolved_sources, ())
        self.assertTrue(all(link.url for link in candidate.source_links))
        self.assertEqual(
            candidate.source_links[2].verification,
            "tvdb_inventory_verified",
        )

    def test_unknown_fact_id_and_model_generated_url_are_rejected(self):
        graph = _honey_and_clover_graph()
        payload = _series_binding_payload()
        payload["candidates"][0]["fact_bindings"][0]["url"] = (
            "https://invented.example/item"
        )
        with self.assertRaisesRegex(CandidateBindingError, "ai_output_invalid"):
            materialize_anchored_candidates(graph, payload)

        payload = _series_binding_payload()
        payload["candidates"][0]["fact_bindings"][0]["fact_id"] = "tvdb:invented"
        with self.assertRaisesRegex(CandidateBindingError, "unknown_fact_id"):
            materialize_anchored_candidates(graph, payload)

    def test_invalid_shortlist_item_does_not_discard_a_valid_candidate(self):
        payload = _series_binding_payload()
        invalid = {
            **payload["candidates"][0],
            "candidate_id": "invalid-candidate",
            "anchor_fact_id": "tvdb:invented",
            "fact_bindings": [{
                "fact_id": "tvdb:invented",
                "role": "series_root",
                "season_number": None,
                "episode_number": None,
            }],
        }
        payload["candidates"].append(invalid)

        candidates = materialize_anchored_candidates(
            _honey_and_clover_graph(),
            payload,
        )

        self.assertEqual(
            [candidate.candidate_id for candidate in candidates],
            ["candidate-1"],
        )

    def test_unverified_season_number_is_preserved_as_unresolved_not_attached(self):
        candidates = materialize_anchored_candidates(
            _honey_and_clover_graph(),
            _series_binding_payload(season_two=3),
        )

        season_three = next(
            link for link in candidates[0].source_links
            if link.fact_id == "douban:102"
        )
        self.assertEqual(season_three.verification, "unresolved_scope_link")
        self.assertIsNone(season_three.season_number)
        self.assertEqual(season_three.proposed_season_number, 3)
        self.assertIn(
            "douban:102:unresolved_scope_link",
            candidates[0].unresolved_sources,
        )

    def test_direct_link_anchor_cannot_be_changed_or_split(self):
        payload = _series_binding_payload()
        payload["candidates"][0]["anchor_fact_id"] = "douban:102"
        payload["candidates"][0]["identity_role"] = "season"
        payload["candidates"][0]["intended_scope"] = "season"
        payload["candidates"][0]["fact_bindings"][1]["role"] = "related_work"

        candidates = materialize_anchored_candidates(
            _honey_and_clover_graph(),
            payload,
            locked_anchor_fact_id="douban:102",
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].anchor_fact_id, "douban:102")
        self.assertEqual(candidates[0].intended_scope, "season")

        payload["candidates"].append(dict(payload["candidates"][0]))
        with self.assertRaisesRegex(CandidateBindingError, "locked_anchor_invalid"):
            materialize_anchored_candidates(
                _honey_and_clover_graph(),
                payload,
                locked_anchor_fact_id="douban:102",
            )

    def test_no_match_allows_zero_candidates_but_resolved_caps_at_six(self):
        self.assertEqual(
            materialize_anchored_candidates(
                _honey_and_clover_graph(),
                {"status": "no_match", "candidates": []},
            ),
            (),
        )
        payload = _series_binding_payload()
        payload["candidates"] = [
            {
                **payload["candidates"][0],
                "candidate_id": f"candidate-{index}",
            }
            for index in range(7)
        ]
        with self.assertRaisesRegex(CandidateBindingError, "candidate_count_invalid"):
            materialize_anchored_candidates(
                _honey_and_clover_graph(),
                payload,
            )


if __name__ == "__main__":
    unittest.main()
