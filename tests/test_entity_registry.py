import tempfile
import unittest
from pathlib import Path

from telepiplex_media_search.entity_registry import CanonicalEntityRegistry


class CanonicalEntityRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "media_entities.db"
        self.registry = CanonicalEntityRegistry(self.path)
        self.entity = {
            "entity_key": "tvdb:movie:123",
            "content_kind": "movie",
            "year": "2014",
            "chinese_title": "布达佩斯大饭店",
            "original_title": "The Grand Budapest Hotel",
            "original_language": "en",
            "official_english_title": "The Grand Budapest Hotel",
            "romanized_original_title": "",
            "canonical_search_title": "The Grand Budapest Hotel",
            "search_title_policy": "official_english",
            "canonical_latin_title": "The Grand Budapest Hotel",
            "poster_url": "https://image/old.jpg",
            "poster_source": "tvdb",
            "external_ids": {"tvdb": "123"},
            "scoring_version": "media-entity-v1",
        }
        self.relation = {
            "relation_type": "standalone",
            "mapping_kind": "standalone",
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def test_only_explicit_upsert_creates_a_row(self):
        self.assertEqual(self.registry.count(), 0)

        self.registry.upsert_selected(self.entity, self.relation)

        self.assertEqual(self.registry.count(), 1)

    def test_upsert_is_idempotent_and_does_not_store_evidence(self):
        self.registry.upsert_selected(self.entity, self.relation)
        self.registry.upsert_selected(
            {**self.entity, "poster_url": "https://image/new.jpg"},
            self.relation,
        )

        self.assertEqual(self.registry.count(), 1)
        self.assertEqual(
            self.registry.get(self.entity["entity_key"])["poster_url"],
            "https://image/new.jpg",
        )
        columns = self.registry.raw_columns_for_test("canonical_entities")
        self.assertNotIn("evidence", columns)
        self.assertNotIn("scorecard", columns)
        self.assertNotIn("query", columns)

    def test_exact_resolution_requires_canonical_title_and_year(self):
        self.registry.upsert_selected(self.entity, self.relation)

        self.assertIsNotNone(
            self.registry.resolve_exact("The Grand Budapest Hotel 2014")
        )
        self.assertIsNone(self.registry.resolve_exact("Grand Budapest"))
        self.assertEqual(
            self.registry.resolve_exact("tvdb:123")["entity_key"],
            "tvdb:movie:123",
        )

    def test_relation_is_returned_with_selected_entity(self):
        relation = {
            "relation_type": "extension_movie",
            "target_entity_key": "tvdb:series:456",
            "target_chinese_title": "想见你",
            "target_canonical_latin_title": "Someday or One Day",
            "target_year": "2019",
            "target_external_ids": {"tvdb": "456"},
            "mapping_kind": "temporary_related_special",
            "season_number": 0,
            "episode_number": 101,
            "tvdb_episode_id": "",
        }
        self.registry.upsert_selected(self.entity, relation)

        resolved = self.registry.get(self.entity["entity_key"])

        self.assertEqual(resolved["relation"]["target_entity_key"], "tvdb:series:456")
        self.assertEqual(resolved["relation"]["episode_number"], 101)


if __name__ == "__main__":
    unittest.main()
