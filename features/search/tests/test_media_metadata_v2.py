import copy
import unittest

from telepiplex_plugin_sdk.media_metadata_v2 import (
    validate_media_metadata_v2,
)
from telepiplex_search.media_metadata_v2 import (
    project_confirmed_media_metadata_v2,
)


class SearchMediaMetadataV2ProjectionTest(unittest.TestCase):
    def _candidate(self):
        return {
            "anchor_fact_id": "tmdb:456",
            "media_metadata": {
                "schema_version": 1,
                "identity": {
                    "chinese_title": "死神：千年血战篇",
                    "english_title": "Bleach: Thousand-Year Blood War",
                    "original_title": "BLEACH 千年血戦篇",
                    "year": "2022",
                    "content_kind": "series",
                    "external_ids": {
                        "wikidata": "Q114103300",
                        "tmdb": "456",
                        "douban": "999",
                    },
                },
                "retrieval": {"media_type": "series"},
                "placement": {
                    "library_type": "series",
                    "category_kind": "animated_series",
                },
                "countries": ["日本"],
                "genres": ["anime"],
                "evidence": {"private": True},
                "items": [{"season_number": 1, "episode_number": 1}],
            },
            "source_links": [
                {
                    "provider": "tmdb",
                    "fact_id": "tmdb:456",
                    "external_ids": {"tmdb": "456"},
                    "verification": "fact_verified",
                    "role": "series_root",
                },
                {
                    "provider": "wikidata",
                    "fact_id": "wikidata:Q114103300",
                    "external_ids": {"wikidata": "Q114103300"},
                    "verification": "fact_verified",
                    "role": "series_root",
                },
                {
                    "provider": "douban",
                    "fact_id": "douban:999",
                    "external_ids": {"douban": "999"},
                    "verification": "ai_supplied_unverified",
                    "role": "series_root",
                },
            ],
            "poster_url": "https://example.invalid/poster.jpg",
            "fact_snapshot": {"private": True},
        }

    def test_projects_only_verified_minimal_fields_without_mutating_candidate(self):
        candidate = self._candidate()
        before = copy.deepcopy(candidate)

        projected = project_confirmed_media_metadata_v2(
            candidate,
            requested_scope={
                "kind": "season",
                "season_number": 1,
                "episode_number": None,
            },
        )

        self.assertIsNotNone(validate_media_metadata_v2(projected))
        self.assertEqual(projected["identity"], {
            "primary_ref": {"provider": "tmdb_tv", "id": "456"},
            "provider_refs": {
                "tmdb_tv": "456",
                "wikidata": "Q114103300",
            },
            "media_type": "series",
            "title_zh": "死神: 千年血战篇",
            "title_original": "BLEACH 千年血戦篇",
            "year": 2022,
        })
        self.assertEqual(projected["scope"], {
            "kind": "season",
            "season_number": 1,
            "episode_number": None,
        })
        self.assertEqual(
            projected["placement"],
            {"category_kind": "animated_series"},
        )
        self.assertEqual(candidate, before)
        self.assertNotIn("poster_url", projected)
        self.assertNotIn("evidence", projected)
        self.assertNotIn("items", projected)

    def test_fails_when_no_verified_stable_provider_ref_exists(self):
        candidate = self._candidate()
        candidate["source_links"] = [candidate["source_links"][-1]]

        with self.assertRaisesRegex(ValueError, "verified_provider_ref_required"):
            project_confirmed_media_metadata_v2(
                candidate,
                requested_scope={
                    "kind": "whole_series",
                    "season_number": None,
                    "episode_number": None,
                },
            )


if __name__ == "__main__":
    unittest.main()
