import unittest

from telepiplex_media_search.entity_graph import (
    build_search_graph,
    merge_verified_equivalence_edges,
)
from telepiplex_media_search.evidence_verifier import (
    EvidenceVerificationError,
    validate_orchestrator_output,
)


def _source(provider, facts):
    return {
        "source": provider,
        "status": "ok",
        "facts": facts,
        "source_urls": [],
    }


def _graph(*, conflicting_year=False, conflicting_id=False):
    douban = {
        "subject_id": "1295644",
        "title": "蝙蝠侠：侠影之谜",
        "chinese_title": "蝙蝠侠：侠影之谜",
        "year": "2005",
        "media_type": "movie",
        "external_ids": {
            "douban_subject": "1295644",
            "imdb": "tt0372784",
        },
    }
    wikipedia = {
        "wikibase_item": "Q166262",
        "title": "Batman Begins",
        "english_title": "Batman Begins",
        "year": "2006" if conflicting_year else "2005",
        "media_type": "movie",
        "external_ids": {
            "wikipedia": "Q166262",
            "imdb": "tt9999999" if conflicting_id else "tt0372784",
        },
    }
    if not conflicting_id:
        # Remove the shared stable ID so deterministic graph building leaves
        # two cross-language candidates for the AI edge to connect.
        douban["external_ids"].pop("imdb")
        wikipedia["external_ids"].pop("imdb")
    return build_search_graph([
        _source("douban", [douban]),
        _source("wikipedia", [wikipedia]),
    ])


def _payload(graph):
    candidates = list(graph.candidates)
    return {
        "status": "resolved",
        "intent": {
            "title_hints": ["蝙蝠侠：侠影之谜", "Batman Begins"],
            "media_type_hint": "movie",
            "year_hint": "2005",
            "scope": "work",
            "season_number": None,
            "episode_number": None,
        },
        "equivalence_edges": [{
            "left_fact_id": candidates[0].facts[0].fact_id,
            "right_fact_id": candidates[1].facts[0].fact_id,
            "relation": "same_entity",
            "reason": "cross-language titles with matching year and type",
        }],
        "candidate_assessments": [{
            "candidate_key": candidate.candidate_key,
            "supporting_fact_ids": [
                fact.fact_id for fact in candidate.facts
            ],
            "conflicting_fact_ids": [],
            "reason": "source-backed candidate",
        } for candidate in candidates],
        "recommended_next_action": "confirm",
    }


class EvidenceVerifierTest(unittest.TestCase):
    def test_valid_cross_language_edge_merges_existing_facts(self):
        graph = _graph()
        decision = validate_orchestrator_output(_payload(graph), graph)

        merged = merge_verified_equivalence_edges(
            graph,
            decision.equivalence_edges,
        )

        self.assertEqual(len(graph.candidates), 2)
        self.assertEqual(len(merged.candidates), 1)
        self.assertEqual(
            merged.candidates[0].providers,
            frozenset({"douban", "wikipedia"}),
        )

    def test_unknown_fact_and_unknown_output_field_are_rejected(self):
        graph = _graph()
        unknown_fact = _payload(graph)
        unknown_fact["equivalence_edges"][0]["left_fact_id"] = "fake:1"
        with self.assertRaises(EvidenceVerificationError) as raised:
            validate_orchestrator_output(unknown_fact, graph)
        self.assertEqual(raised.exception.code, "unknown_fact_id")

        unknown_field = _payload(graph)
        unknown_field["invented_stable_id"] = "tvdb:1"
        with self.assertRaises(EvidenceVerificationError) as raised:
            validate_orchestrator_output(unknown_field, graph)
        self.assertEqual(raised.exception.code, "ai_output_invalid")

    def test_year_conflict_rejects_semantic_edge(self):
        graph = _graph(conflicting_year=True)

        with self.assertRaises(EvidenceVerificationError) as raised:
            validate_orchestrator_output(_payload(graph), graph)

        self.assertEqual(raised.exception.code, "hard_fact_conflict")

    def test_conflicting_shared_stable_id_rejects_semantic_edge(self):
        graph = _graph(conflicting_id=True)
        self.assertEqual(len(graph.candidates), 2)

        with self.assertRaises(EvidenceVerificationError) as raised:
            validate_orchestrator_output(_payload(graph), graph)

        self.assertEqual(raised.exception.code, "hard_fact_conflict")

    def test_every_temporary_candidate_must_be_assessed_once(self):
        graph = _graph()
        payload = _payload(graph)
        payload["candidate_assessments"] = payload["candidate_assessments"][:1]

        with self.assertRaises(EvidenceVerificationError) as raised:
            validate_orchestrator_output(payload, graph)

        self.assertEqual(raised.exception.code, "candidate_assessment_mismatch")


if __name__ == "__main__":
    unittest.main()
