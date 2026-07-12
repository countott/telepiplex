import json
import unittest

from app.core.media_metadata import (
    CONTENT_KINDS,
    MEDIA_METADATA_KEY,
    attach_media_metadata,
    enrich_media_metadata_identity,
    extract_confirmed_media_metadata,
    locked_episode,
    merge_resolved_items,
    resolve_category_route,
    series_folder_name,
    series_scope_key,
    series_season_directory_name,
    validate_media_metadata,
)


class CoreMediaMetadataTest(unittest.TestCase):
    def test_category_route_uses_kind_not_display_name(self):
        route = resolve_category_route({
            "category_folder": [{
                "kind": "live_action_series",
                "name": "可改显示名",
                "path": "/真人剧集/",
                "plex_library_id": "13",
            }]
        }, "live_action_series")

        self.assertEqual(route, {
            "kind": "live_action_series",
            "name": "可改显示名",
            "path": "/真人剧集",
            "plex_library_id": "13",
        })
        self.assertIsNone(resolve_category_route({"category_folder": []}, "live_action_series"))
        self.assertIsNone(resolve_category_route({
            "category_folder": [{
                "name": "live_action_series",
                "path": "/真人剧集",
                "plex_library_id": "13",
            }]
        }, "live_action_series"))

    def _value(self):
        return {
            "schema_version": 1,
            "metadata_id": "metadata-a",
            "confirmed": True,
            "identity": {
                "chinese_title": "想见你",
                "english_title": "Someday or One Day The Movie",
                "year": "2022",
                "content_kind": "extension_movie",
                "summary": "电影版延续电视剧故事。",
                "original_release_date": "2022-12-24",
                "poster_url": "https://image.example/poster.jpg",
                "poster_source": "douban",
                "external_ids": {},
            },
            "relation": {
                "type": "sequel",
                "target_series": {
                    "chinese_title": "想见你",
                    "english_title": "Someday or One Day",
                    "year": "2019",
                    "external_ids": {},
                },
                "source": "wikipedia",
            },
            "placement": {
                "library_type": "series",
                "category_kind": "live_action_series",
                "season_number": 0,
                "episode_number": 100,
                "mapping_kind": "temporary_related_special",
                "mapping_source": "local_allocator",
                "tvdb_episode_id": "",
            },
            "source_entry": {
                "title": "想见你 (电影)",
                "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
                "provider": "wikipedia",
                "verification": "verified",
            },
            "items": [],
            "evidence": {},
            "warnings": [],
        }

    def test_valid_contract_round_trips_and_is_deep_copied(self):
        value = self._value()
        attached = attach_media_metadata({"source": "confirmed"}, value)
        extracted = extract_confirmed_media_metadata(attached)
        self.assertEqual(MEDIA_METADATA_KEY, "media_metadata")
        self.assertEqual(locked_episode(extracted), (0, 100))
        self.assertEqual(json.loads(json.dumps(extracted, ensure_ascii=False)), extracted)
        extracted["identity"]["chinese_title"] = "changed"
        self.assertEqual(value["identity"]["chinese_title"], "想见你")

    def test_rejects_wrong_category_pair_and_old_public_key(self):
        value = self._value()
        value["placement"]["category_kind"] = "animated_movie"
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))
        legacy_key = "_".join(("download", "plan"))
        self.assertIsNone(extract_confirmed_media_metadata({legacy_key: self._value()}))

    def test_rejects_unknown_category_with_none_library_type(self):
        value = self._value()
        value["relation"]["target_series"] = {}
        value["placement"].update({
            "library_type": None,
            "category_kind": "invented",
            "season_number": None,
            "episode_number": None,
            "mapping_kind": "standalone",
        })
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_rejects_duplicate_logical_episode_targets(self):
        value = self._value()
        value["items"] = [
            {
                "item_id": "episode-a",
                "content_role": "main_episode",
                "season_number": 1,
                "episode_number": 1,
            },
            {
                "item_id": "episode-b",
                "content_role": "main_episode",
                "season_number": 1,
                "episode_number": 1,
            },
        ]

        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_accepts_exactly_the_four_category_library_pairs(self):
        pairs = {
            "live_action_series": "series",
            "live_action_movie": "movie",
            "animated_movie": "movie",
            "animated_series": "series",
        }
        for category_kind, library_type in pairs.items():
            with self.subTest(category_kind=category_kind):
                value = self._value()
                value["placement"].update({
                    "category_kind": category_kind,
                    "library_type": library_type,
                    "season_number": None,
                    "episode_number": None,
                    "mapping_kind": "standalone",
                })
                value["relation"]["target_series"] = {}
                if library_type == "series":
                    value["identity"]["content_kind"] = "series"
                    value["items"] = [{
                        "content_role": "main_episode",
                        "season_number": 1,
                        "episode_number": 1,
                    }]
                self.assertIsNotNone(validate_media_metadata(value, require_confirmed=True))

    def test_standalone_has_no_series_target_or_episode_lock(self):
        value = self._value()
        value["relation"]["target_series"] = {}
        value["placement"].update({
            "library_type": "movie",
            "category_kind": "live_action_movie",
            "season_number": None,
            "episode_number": None,
            "mapping_kind": "standalone",
        })
        self.assertIsNotNone(validate_media_metadata(value, require_confirmed=True))
        value["relation"]["target_series"] = {"english_title": "Someday or One Day"}
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_primary_series_uses_confirmed_items_for_ordinary_episodes(self):
        value = self._value()
        value["identity"]["content_kind"] = "series"
        value["relation"]["target_series"] = {}
        value["placement"].update({
            "mapping_kind": "standalone",
            "season_number": None,
            "episode_number": None,
        })
        value["items"] = [{
            "item_id": "episode-1",
            "content_role": "main_episode",
            "season_number": 1,
            "episode_number": 1,
        }]
        self.assertIsNotNone(validate_media_metadata(value, require_confirmed=True))
        value["items"] = []
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_all_v1_content_kinds_are_explicit_and_unknown_is_rejected(self):
        for content_kind in CONTENT_KINDS:
            with self.subTest(content_kind=content_kind):
                value = self._value()
                value["identity"]["content_kind"] = content_kind
                self.assertIsNotNone(validate_media_metadata(value, require_confirmed=True))
        value = self._value()
        value["identity"]["content_kind"] = "invented"
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_rejects_non_json_values(self):
        value = self._value()
        value["evidence"]["bad"] = {"not-json"}
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_series_storage_names_are_shared_by_search_and_renaming(self):
        value = self._value()
        self.assertEqual(series_folder_name(value), "想见你 (Someday or One Day)")
        self.assertEqual(series_season_directory_name(value, 0), "Someday or One Day Season 00")
        self.assertEqual(series_scope_key(value), "title:someday or one day:2019")

    def test_attach_rejects_a_legacy_outer_key_instead_of_dual_writing(self):
        legacy_key = "_".join(("download", "plan"))
        with self.assertRaisesRegex(ValueError, "legacy metadata key"):
            attach_media_metadata({legacy_key: {}}, self._value())

    def test_official_mapping_requires_tvdb_series_and_episode_ids(self):
        value = self._value()
        value["placement"].update({
            "mapping_kind": "tvdb_official",
            "episode_number": 5,
            "tvdb_episode_id": "",
        })
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))
        value["placement"]["tvdb_episode_id"] = "episode-5"
        value["relation"]["target_series"]["external_ids"]["tvdb"] = "series-1"
        self.assertIsNotNone(validate_media_metadata(value, require_confirmed=True))
        value["placement"]["season_number"] = 1
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_temporary_mapping_requires_source_locator(self):
        value = self._value()
        value["source_entry"]["url"] = ""
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))
        value["source_entry"]["external_id"] = "wikipedia:想見你_(電影)"
        self.assertIsNotNone(validate_media_metadata(value, require_confirmed=True))
        value["source_entry"]["title"] = ""
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_series_episode_mapping_rejects_item_outside_top_level_lock(self):
        value = self._value()
        value["items"] = [{
            "item_id": "wrong-episode",
            "content_role": "extension_movie",
            "season_number": 0,
            "episode_number": 101,
        }]
        self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_merge_rejects_prebound_item_outside_series_mapping_lock(self):
        value = self._value()
        value["items"] = [{
            "item_id": "wrong-episode",
            "content_role": "extension_movie",
            "season_number": 0,
            "episode_number": 101,
        }]
        with self.assertRaisesRegex(ValueError, "locked target|invalid confirmed"):
            merge_resolved_items(value, [{
                "season_number": 0,
                "episode_number": 101,
                "final_path": "/wrong.mkv",
            }])

    def test_ai_inferred_mapping_requires_nonblank_warning_string(self):
        for warnings in ([{}], [None], [""]):
            with self.subTest(warnings=warnings):
                value = self._value()
                value["placement"]["mapping_kind"] = "ai_inferred_tvdb"
                value["warnings"] = warnings
                self.assertIsNone(validate_media_metadata(value, require_confirmed=True))
        value = self._value()
        value["placement"]["mapping_kind"] = "ai_inferred_tvdb"
        value["warnings"] = ["TVDB episode mapping inferred by AI"]
        self.assertIsNotNone(validate_media_metadata(value, require_confirmed=True))

    def test_required_containers_reject_falsy_wrong_types(self):
        for field_name in ("items", "warnings", "evidence"):
            with self.subTest(field_name=field_name):
                value = self._value()
                value[field_name] = ()
                self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_external_ids_reject_malformed_types_without_raising(self):
        cases = []

        identity_tuple = self._value()
        identity_tuple["identity"]["external_ids"] = ()
        cases.append(("identity_empty_tuple", identity_tuple))

        target_tuple = self._value()
        target_tuple["relation"]["target_series"]["external_ids"] = ()
        cases.append(("target_empty_tuple", target_tuple))

        target_list = self._value()
        target_list["placement"].update({
            "mapping_kind": "tvdb_official",
            "tvdb_episode_id": "episode-100",
        })
        target_list["relation"]["target_series"]["external_ids"] = ["malformed"]
        cases.append(("target_list", target_list))

        for case_name, value in cases:
            with self.subTest(case_name=case_name):
                try:
                    result = validate_media_metadata(value, require_confirmed=True)
                except Exception as exc:
                    self.fail(f"validation raised {type(exc).__name__}: {exc}")
                self.assertIsNone(result)

    def test_rejects_non_finite_json_numbers(self):
        for non_finite in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(non_finite=non_finite):
                value = self._value()
                value["evidence"]["score"] = non_finite
                self.assertIsNone(validate_media_metadata(value, require_confirmed=True))

    def test_merge_resolved_items_cannot_change_locked_target(self):
        value = self._value()
        merged = merge_resolved_items(value, [{
            "content_role": "extension_movie",
            "season_number": 0,
            "episode_number": 100,
            "source_relative_path": "Movie.mkv",
            "final_path": "/真人剧集/想见你/Someday or One Day Season 00/Someday or One Day S00E100.mkv",
        }])
        self.assertEqual(merged["items"][0]["final_path"].rsplit("/", 1)[-1], "Someday or One Day S00E100.mkv")
        with self.assertRaisesRegex(ValueError, "locked target"):
            merge_resolved_items(value, [{
                "season_number": 0,
                "episode_number": 101,
                "final_path": "/wrong.mkv",
            }])

    def test_identity_enrichment_updates_only_missing_canonical_chinese_title(self):
        value = self._value()
        value["identity"]["chinese_title"] = ""
        metadata = attach_media_metadata(
            {"query": "Someday or One Day The Movie 2022"},
            value,
        )
        original_placement = json.loads(json.dumps(value["placement"]))
        original_items = json.loads(json.dumps(value["items"]))

        enriched = enrich_media_metadata_identity(
            metadata,
            chinese_title="想见你",
            source="douban",
            evidence={"query": "Someday or One Day The Movie 2022"},
        )
        contract = extract_confirmed_media_metadata(enriched)

        self.assertEqual(enriched["query"], "Someday or One Day The Movie 2022")
        self.assertEqual(contract["identity"]["chinese_title"], "想见你")
        self.assertEqual(contract["identity"]["english_title"], value["identity"]["english_title"])
        self.assertEqual(contract["metadata_id"], "metadata-a")
        self.assertEqual(contract["placement"], original_placement)
        self.assertEqual(contract["items"], original_items)
        self.assertEqual(
            contract["evidence"]["identity_backfills"],
            [{
                "field": "chinese_title",
                "source": "douban",
                "query": "Someday or One Day The Movie 2022",
            }],
        )
        self.assertEqual(metadata["media_metadata"]["identity"]["chinese_title"], "")

    def test_identity_enrichment_never_overwrites_confirmed_chinese_title(self):
        metadata = attach_media_metadata({}, self._value())

        enriched = enrich_media_metadata_identity(
            metadata,
            chinese_title="错误覆盖",
            source="ai_metadata_backfill",
        )

        contract = extract_confirmed_media_metadata(enriched)
        self.assertEqual(contract["identity"]["chinese_title"], "想见你")
        self.assertNotIn("identity_backfills", contract["evidence"])
