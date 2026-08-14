import unittest

from telepiplex_search.candidate_locale import (
    CandidateLocaleError,
    localize_candidate_from_exact_douban,
)


class CandidateLocaleTest(unittest.TestCase):
    def test_exact_wikidata_binding_replaces_preview_title_before_selection(self):
        candidate = {
            "candidate_id": "wikipedia:Q124175370",
            "identity_role": "series_root",
            "media_metadata": {"identity": {
                "chinese_title": "百年孤寂",
                "english_title": "One Hundred Years of Solitude",
                "content_kind": "series",
                "aliases": [],
                "external_ids": {
                    "wikidata": "Q124175370",
                    "douban_subject": "30482958",
                },
            }},
            "source_links": [{
                "provider": "wikipedia",
                "fact_id": "wikipedia:Q124175370",
                "url": "https://zh.wikipedia.org/wiki/百年孤寂",
                "external_ids": {
                    "wikidata": "Q124175370",
                    "douban_subject": "30482958",
                },
                "role": "series_root",
                "season_number": None,
                "episode_number": None,
                "verification": "fact_verified",
            }],
        }
        fact = {
            "subject_id": "30482958",
            "url": "https://movie.douban.com/subject/30482958/",
            "douban_title_raw": "百年孤独 第一季",
            "chinese_title": "百年孤独",
            "english_title": "One Hundred Years of Solitude",
            "media_type": "series",
            "season_number": 1,
            "external_ids": {"douban_subject": "30482958"},
        }

        localized = localize_candidate_from_exact_douban(candidate, fact)

        identity = localized["media_metadata"]["identity"]
        self.assertEqual(identity["chinese_title"], "百年孤独")
        self.assertIn("百年孤寂", identity["aliases"])
        self.assertEqual(localized["localization"]["match_mode"], "wikidata_exact")
        self.assertEqual(localized["source_links"][-1]["provider"], "douban")

    def test_mismatched_subject_id_is_rejected(self):
        candidate = {"media_metadata": {"identity": {
            "content_kind": "series",
            "external_ids": {"douban_subject": "1"},
        }}}
        with self.assertRaises(CandidateLocaleError):
            localize_candidate_from_exact_douban(candidate, {
                "subject_id": "2",
                "chinese_title": "错误条目",
                "media_type": "series",
            })


if __name__ == "__main__":
    unittest.main()
