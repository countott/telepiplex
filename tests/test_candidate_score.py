import unittest

from telepiplex_media_search.candidate_score import (
    CandidateScore,
    apply_thresholds,
    program_score,
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


if __name__ == "__main__":
    unittest.main()
