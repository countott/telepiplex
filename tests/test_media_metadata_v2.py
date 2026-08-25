import copy
import unittest

from telepiplex_plugin_sdk.media_metadata_v2 import (
    attach_media_metadata_v2,
    build_media_metadata_v2_id,
    convert_media_metadata_v1_to_v2,
    extract_confirmed_media_metadata_v2,
    validate_media_metadata_v2,
    validate_media_metadata_v2_detailed,
)


class MediaMetadataV2Test(unittest.TestCase):
    def _value(self, kind="whole_series"):
        scope = {
            "kind": kind,
            "season_number": 1 if kind in {"season", "episode"} else None,
            "episode_number": 2 if kind == "episode" else None,
        }
        media_type = "movie" if kind == "movie" else "series"
        value = {
            "schema_version": 2,
            "confirmed": True,
            "identity": {
                "primary_ref": {"provider": "wikidata", "id": "Q123"},
                "provider_refs": {
                    "wikidata": "Q123",
                    "tmdb_tv": "456",
                },
                "media_type": media_type,
                "title_zh": "死神: 千年血战篇",
                "title_original": "BLEACH 千年血戦篇",
                "year": 2022,
            },
            "scope": scope,
            "placement": {
                "category_kind": (
                    "animated_movie" if media_type == "movie"
                    else "animated_series"
                ),
            },
        }
        if media_type == "movie":
            value["identity"]["provider_refs"] = {"wikidata": "Q123"}
        value["metadata_id"] = build_media_metadata_v2_id(value)
        return value

    def test_accepts_all_four_scopes_and_returns_a_deep_copy(self):
        for kind in ("movie", "whole_series", "season", "episode"):
            with self.subTest(kind=kind):
                value = self._value(kind)
                validated = validate_media_metadata_v2(value)
                self.assertEqual(validated, value)
                self.assertIsNot(validated, value)
                self.assertIsNot(validated["identity"], value["identity"])

    def test_id_ignores_titles_year_and_additional_verified_refs(self):
        first = self._value()
        second = copy.deepcopy(first)
        second["identity"].update({
            "title_zh": "另一个显示名",
            "title_original": "Another display title",
            "year": 2023,
        })
        second["identity"]["provider_refs"]["tvdb_series"] = "789"

        self.assertEqual(
            build_media_metadata_v2_id(first),
            build_media_metadata_v2_id(second),
        )

    def test_rejects_unknown_or_rich_fields_at_every_public_boundary(self):
        mutations = (
            lambda value: value.update({"countries": ["日本"]}),
            lambda value: value["identity"].update({"genres": ["anime"]}),
            lambda value: value["identity"]["primary_ref"].update({
                "url": "https://example.invalid",
            }),
            lambda value: value["scope"].update({"inventory": []}),
            lambda value: value["placement"].update({"path": "/anime"}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                value = self._value()
                mutate(value)
                self.assertIsNone(validate_media_metadata_v2(value))

    def test_rejects_invalid_identity_scope_and_placement_invariants(self):
        mutations = (
            lambda value: value.update({"confirmed": False}),
            lambda value: value["identity"]["primary_ref"].update({"id": ""}),
            lambda value: value["identity"]["provider_refs"].update({
                "imdb": "tt123",
            }),
            lambda value: value["identity"]["provider_refs"].update({
                "wikidata": "Q999",
            }),
            lambda value: value["placement"].update({
                "category_kind": "documentary",
            }),
            lambda value: value["scope"].update({
                "kind": "season",
                "season_number": None,
            }),
            lambda value: value["scope"].update({
                "kind": "episode",
                "season_number": 1,
                "episode_number": None,
            }),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                value = self._value()
                mutate(value)
                self.assertIsNone(validate_media_metadata_v2(value))

    def test_rejects_a_stale_or_fabricated_metadata_id(self):
        value = self._value()
        value["metadata_id"] = "media-v2:not-the-contract-id"

        validated, issue = validate_media_metadata_v2_detailed(value)

        self.assertIsNone(validated)
        self.assertEqual(issue["path"], "$.metadata_id")
        self.assertEqual(issue["reason_code"], "metadata_id_mismatch")

    def test_attach_and_extract_are_explicit_and_mutation_safe(self):
        value = self._value("season")
        metadata = attach_media_metadata_v2({"source": "search"}, value)
        value["identity"]["title_zh"] = "被调用方修改"

        extracted = extract_confirmed_media_metadata_v2(metadata)

        self.assertEqual(extracted["identity"]["title_zh"], "死神: 千年血战篇")
        self.assertEqual(metadata["source"], "search")

    def test_legacy_v1_converts_once_without_rich_fields(self):
        legacy = {
            "schema_version": 1,
            "metadata_id": "legacy-1",
            "confirmed": True,
            "identity": {
                "chinese_title": "死神：千年血战篇",
                "english_title": "BLEACH 千年血戦篇",
                "year": "2022",
                "content_kind": "series",
                "external_ids": {"wikidata": "Q114103300"},
            },
            "retrieval": {
                "media_type": "series",
                "scope": "season",
            },
            "relation": {"target_series": None},
            "placement": {
                "library_type": "series",
                "category_kind": "animated_series",
                "season_number": None,
                "episode_number": None,
            },
            "evidence": {"decision": {
                "scope": "season",
                "season_number": 1,
                "episode_number": None,
            }},
            "countries": ["日本"],
            "items": [{"season_number": 1, "episode_number": 1}],
        }

        converted, issue = convert_media_metadata_v1_to_v2(legacy)

        self.assertIsNone(issue)
        self.assertIsNotNone(validate_media_metadata_v2(converted))
        self.assertEqual(converted["scope"], {
            "kind": "season",
            "season_number": 1,
            "episode_number": None,
        })
        self.assertNotIn("countries", converted)
        self.assertNotIn("items", converted)

    def test_legacy_without_a_stable_verified_ref_fails_closed(self):
        legacy = {
            "schema_version": 1,
            "confirmed": True,
            "identity": {
                "chinese_title": "无锚点作品",
                "english_title": "",
                "year": "2020",
                "content_kind": "movie",
                "external_ids": {},
            },
            "placement": {
                "library_type": "movie",
                "category_kind": "live_action_movie",
            },
        }

        converted, issue = convert_media_metadata_v1_to_v2(legacy)

        self.assertIsNone(converted)
        self.assertEqual(issue["reason_code"], "legacy_metadata_incomplete")


if __name__ == "__main__":
    unittest.main()
