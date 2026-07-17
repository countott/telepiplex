import unittest

from telepiplex_media_search.candidate_score import (
    CandidateScore,
    apply_thresholds,
    combine_score,
    program_score,
    validate_ai_candidate_score,
)
from telepiplex_media_search.entity_graph import CandidateEntity, EvidenceFact


def candidate(*, year="2022", media_type="series"):
    facts = tuple(
        EvidenceFact(
            fact_id=f"{provider}:1",
            provider=provider,
            titles=("黑暗荣耀", "The Glory"),
            year=year,
            media_type=media_type,
            external_ids={"tvdb": "411469"},
            official_english_title="The Glory",
        )
        for provider in ("tvdb", "douban", "wikipedia")
    )
    return CandidateEntity("tvdb:series:411469", facts)


class CandidateScoreTest(unittest.TestCase):
    def test_program_score_is_fixed_sixty_point_model(self):
        score = program_score(
            candidate(),
            {"year": "2022", "media_type": "series", "scope": "whole_series"},
            None,
        )

        self.assertEqual(score.total, 60)
        self.assertEqual(score.version, "media-entity-v1")
        self.assertFalse(score.excluded)

    def test_wrong_user_year_penalizes_without_hard_gate(self):
        score = program_score(
            candidate(),
            {"year": "2019", "media_type": "series"},
            None,
        )

        self.assertFalse(score.excluded)
        self.assertEqual(score.release_consistency, 0)

    def test_explicit_type_conflict_is_hard_gate(self):
        score = program_score(candidate(media_type="series"), {"media_type": "movie"}, None)

        self.assertTrue(score.excluded)
        self.assertIn("explicit_type_conflict", score.reason_codes)

    def test_thresholds_and_lead_are_fixed(self):
        program = program_score(candidate(), {}, None)
        high = CandidateScore("a", program, 90)
        close = CandidateScore("b", program, 82)

        ranked = apply_thresholds([high, close])

        self.assertFalse(ranked[0].recommended)
        self.assertTrue(ranked[0].selectable)

    def test_threshold_marks_clear_leader_recommended(self):
        program = program_score(candidate(), {}, None)
        high = CandidateScore("a", program, 90)
        low = CandidateScore("b", program, 75)

        ranked = apply_thresholds([high, low])

        self.assertTrue(ranked[0].recommended)
        self.assertFalse(ranked[1].recommended)

    def test_ai_scorecard_is_recomputed_from_fact_bound_dimensions(self):
        score = validate_ai_candidate_score(
            {
                "candidate_key": "tvdb:series:411469",
                "title_equivalence": 18,
                "intent_relevance": 9,
                "relation_consistency": 8,
                "fact_ids": ["tvdb:1", "douban:1", "wikipedia:1"],
                "total": 99,
            },
            candidate_key="tvdb:series:411469",
            allowed_fact_ids={"tvdb:1", "douban:1", "wikipedia:1"},
        )

        self.assertEqual(score.total, 35)
        self.assertEqual(
            combine_score(
                "tvdb:series:411469",
                program_score(
                    candidate(),
                    {
                        "year": "2022",
                        "media_type": "series",
                        "scope": "whole_series",
                    },
                    None,
                ),
                score,
            ).total,
            95,
        )

    def test_unknown_fact_or_out_of_range_dimension_discards_ai_score(self):
        for payload in (
            {
                "candidate_key": "tvdb:series:411469",
                "title_equivalence": 21,
                "intent_relevance": 9,
                "relation_consistency": 8,
                "fact_ids": ["tvdb:1"],
            },
            {
                "candidate_key": "tvdb:series:411469",
                "title_equivalence": 18,
                "intent_relevance": 9,
                "relation_consistency": 8,
                "fact_ids": ["fact:invented"],
            },
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(validate_ai_candidate_score(
                    payload,
                    candidate_key="tvdb:series:411469",
                    allowed_fact_ids={"tvdb:1"},
                ))

    def test_hard_gate_candidate_is_selectable_without_ai_points(self):
        program = program_score(candidate(), {}, None)

        ranked = apply_thresholds([
            combine_score("tvdb:series:411469", program, None)
        ])

        self.assertEqual(ranked[0].total, 57)
        self.assertTrue(ranked[0].selectable)
        self.assertFalse(ranked[0].recommended)


if __name__ == "__main__":
    unittest.main()
