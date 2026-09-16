import copy
import unittest

from telepiplex_plugin_sdk.media_metadata_v2 import (
    validate_media_metadata_v2,
)
from telepiplex_search.media_metadata_v2 import (
    project_confirmed_media_metadata_v2,
)


class SearchMediaMetadataV2ProjectionTest(unittest.TestCase):
    def test_all_series_scopes_freeze_root_year_without_changing_private_years(self):
        for category in ("live_action_series", "animated_series"):
            for kind, season, episode in (("whole_series", None, None), ("season", 2, None), ("episode", 2, 3)):
                with self.subTest(category=category, kind=kind):
                    candidate = self._candidate()
                    private = candidate["media_metadata"]
                    private["placement"]["category_kind"] = category
                    private["identity"].update(year="2022", root_year="2004", scope_year="2026")
                    before = copy.deepcopy(candidate)
                    result = project_confirmed_media_metadata_v2(candidate, requested_scope={
                        "kind": kind, "season_number": season, "episode_number": episode,
                    })
                    self.assertEqual(result["identity"]["year"], 2004)
                    self.assertEqual(candidate, before)

    def test_missing_or_invalid_root_year_never_uses_later_entry_year(self):
        for year in (None, "", "unknown", 123, 12345, True):
            with self.subTest(year=year):
                candidate = self._candidate()
                candidate["media_metadata"]["identity"].update(root_year=year, year="2022")
                with self.assertRaisesRegex(ValueError, "series_root_year_required"):
                    project_confirmed_media_metadata_v2(candidate, requested_scope={
                        "kind": "season", "season_number": 2, "episode_number": None,
                    })

    def test_movie_keeps_its_release_year(self):
        candidate = self._candidate()
        candidate["media_metadata"]["identity"].update(root_year="2004", year="2022")
        candidate["media_metadata"]["retrieval"]["media_type"] = "movie"
        candidate["media_metadata"]["placement"]["category_kind"] = "animated_movie"
        result = project_confirmed_media_metadata_v2(candidate, requested_scope={
            "kind": "movie", "season_number": None, "episode_number": None,
        })
        self.assertEqual(result["identity"]["year"], 2022)

    def test_freezes_naming_title_by_category_without_replacing_english(self):
        for category, country, expected_kind in (
            ("animated_movie", "日本", "romaji"),
            ("animated_series", "JP", "romaji"),
            ("live_action_movie", "日本", "english"),
            ("live_action_series", "日本", "english"),
            ("animated_series", "China", "english"),
        ):
            with self.subTest(category=category, country=country):
                candidate = self._candidate()
                movie = category.endswith("_movie")
                private = candidate["media_metadata"]
                private["retrieval"]["media_type"] = "movie" if movie else "series"
                private["placement"]["category_kind"] = category
                private["identity"].update({
                    "official_english_title": "Official English",
                    "english_title": "Source Romaji",
                    "romanized_original_title": "Source Romaji",
                    "original_language": "ja",
                    "countries": [country],
                    "search_title_policy": "romanized_original",
                })
                before = copy.deepcopy(candidate)
                result = project_confirmed_media_metadata_v2(candidate, requested_scope={
                    "kind": "movie" if movie else "whole_series",
                    "season_number": None,
                    "episode_number": None,
                })
                self.assertEqual(result["identity"]["title_en"], "Official English")
                self.assertEqual(result["identity"]["naming_title_kind"], expected_kind)
                self.assertEqual(result["identity"]["naming_title"],
                                 "Source Romaji" if expected_kind == "romaji" else "Official English")
                self.assertEqual(candidate, before)

    def test_romaji_without_english_and_verified_english_fallback(self):
        candidate = self._candidate()
        identity = candidate["media_metadata"]["identity"]
        identity.update({
            "official_english_title": "",
            "english_title": "Source Romaji",
            "romanized_original_title": "Source Romaji",
            "original_language": "ja",
            "search_title_policy": "romanized_original",
        })
        scope = {"kind": "whole_series", "season_number": None, "episode_number": None}
        result = project_confirmed_media_metadata_v2(candidate, requested_scope=scope)
        self.assertEqual(result["identity"]["title_en"], "")
        self.assertEqual(result["identity"]["naming_title"], "Source Romaji")
        identity.update(romanized_original_title="", official_english_title="Official English")
        result = project_confirmed_media_metadata_v2(candidate, requested_scope=scope)
        self.assertEqual(result["identity"]["naming_title_kind"], "english")
        self.assertEqual(result["identity"]["naming_title"], "Official English")

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
                    "root_year": "2022",
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
            "title_en": "Bleach: Thousand-Year Blood War",
            "title_original": "BLEACH 千年血戦篇",
            "naming_title": "Bleach: Thousand-Year Blood War",
            "naming_title_kind": "english",
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

    def test_keeps_verified_english_separate_from_japanese_original(self):
        candidate = self._candidate()
        candidate["media_metadata"]["identity"].update({
            "chinese_title": "游戏人生",
            "english_title": "No Game, No Life",
            "original_title": "ノーゲーム・ノーライフ",
        })

        projected = project_confirmed_media_metadata_v2(
            candidate,
            requested_scope={
                "kind": "whole_series",
                "season_number": None,
                "episode_number": None,
            },
        )

        self.assertEqual(projected["identity"]["title_en"], "No Game, No Life")
        self.assertEqual(
            projected["identity"]["title_original"],
            "ノーゲーム・ノーライフ",
        )

    def test_anilist_entry_is_primary_ref_without_expanding_public_schema(self):
        candidate = self._candidate()
        candidate["source_links"].append({
            "provider": "anilist",
            "fact_id": "anilist:116674",
            "external_ids": {
                "anilist": "116674",
                "myanimelist": "41467",
            },
            "verification": "fact_verified",
            "role": "anime_entry",
        })
        candidate["media_metadata"]["identity"].update({
            "work_root_ref": {
                "provider": "wikidata",
                "id": "Q114103300",
            },
            "anime_entry_ref": {
                "provider": "anilist",
                "id": "116674",
            },
            "binding_kind": "root_to_entry",
        })

        projected = project_confirmed_media_metadata_v2(
            candidate,
            requested_scope={
                "kind": "whole_series",
                "season_number": None,
                "episode_number": None,
            },
        )

        self.assertEqual(projected["identity"]["primary_ref"], {
            "provider": "anilist",
            "id": "116674",
        })
        self.assertEqual(projected["identity"]["provider_refs"], {
            "tmdb_tv": "456",
            "wikidata": "Q114103300",
            "anilist": "116674",
        })
        self.assertEqual(set(projected["identity"]), {
            "primary_ref",
            "provider_refs",
            "media_type",
            "title_zh",
            "title_en",
            "title_original",
            "naming_title",
            "naming_title_kind",
            "year",
        })
        changed_root = copy.deepcopy(candidate)
        changed_root["source_links"][1]["external_ids"]["wikidata"] = "Q999"
        root_projection = project_confirmed_media_metadata_v2(
            changed_root,
            requested_scope={
                "kind": "whole_series",
                "season_number": None,
                "episode_number": None,
            },
        )
        self.assertEqual(root_projection["metadata_id"], projected["metadata_id"])
        changed_entry = copy.deepcopy(candidate)
        changed_entry["source_links"][-1]["external_ids"]["anilist"] = "159322"
        changed_entry["media_metadata"]["identity"]["anime_entry_ref"]["id"] = (
            "159322"
        )
        entry_projection = project_confirmed_media_metadata_v2(
            changed_entry,
            requested_scope={
                "kind": "whole_series",
                "season_number": None,
                "episode_number": None,
            },
        )
        self.assertNotEqual(entry_projection["metadata_id"], projected["metadata_id"])

        mismatched = copy.deepcopy(candidate)
        mismatched["media_metadata"]["identity"]["anime_entry_ref"] = {
            "provider": "anilist",
            "id": "999999",
        }
        with self.assertRaisesRegex(ValueError, "anime_entry_ref_conflict"):
            project_confirmed_media_metadata_v2(
                mismatched,
                requested_scope={
                    "kind": "whole_series",
                    "season_number": None,
                    "episode_number": None,
                },
            )

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
