import unittest

from telepiplex_search.entity_graph import CandidateEntity, EvidenceFact
from telepiplex_search.title_policy import TitlePolicyError, resolve_title_policy


def fact(**overrides):
    values = {
        "fact_id": "tvdb:1",
        "provider": "tvdb",
        "titles": ("布达佩斯大饭店", "The Grand Budapest Hotel"),
        "year": "2014",
        "media_type": "movie",
        "external_ids": {"tvdb": "1"},
        "original_title": "The Grand Budapest Hotel",
        "original_language": "en",
        "official_english_title": "The Grand Budapest Hotel",
    }
    values.update(overrides)
    return EvidenceFact(**values)


class TitlePolicyTest(unittest.TestCase):
    def test_non_japanese_uses_official_english(self):
        titles = resolve_title_policy(CandidateEntity("tvdb:movie:1", (fact(),)))

        self.assertEqual(titles.canonical_search_title, "The Grand Budapest Hotel")
        self.assertEqual(titles.canonical_latin_title, "The Grand Budapest Hotel")
        self.assertEqual(titles.search_title_policy, "official_english")
        self.assertEqual(titles.chinese_title, "布达佩斯大饭店")

    def test_japanese_uses_romaji_not_english_translation(self):
        candidate = CandidateEntity("tvdb:series:2", (fact(
            fact_id="tvdb:2",
            titles=("进击的巨人", "進撃の巨人", "Attack on Titan", "Shingeki no Kyojin"),
            original_title="進撃の巨人",
            original_language="ja",
            official_english_title="Attack on Titan",
            romanized_original_title="Shingeki no Kyojin",
            media_type="series",
        ),))

        titles = resolve_title_policy(candidate)

        self.assertEqual(titles.official_english_title, "Attack on Titan")
        self.assertEqual(titles.romanized_original_title, "Shingeki no Kyojin")
        self.assertEqual(titles.canonical_search_title, "Shingeki no Kyojin")
        self.assertEqual(titles.canonical_latin_title, "Shingeki no Kyojin")
        self.assertEqual(titles.search_title_policy, "romanized_original")

    def test_japanese_without_verified_romaji_is_not_finalizable(self):
        candidate = CandidateEntity("tvdb:series:3", (fact(
            fact_id="tvdb:3",
            titles=("进击的巨人", "進撃の巨人", "Attack on Titan"),
            original_title="進撃の巨人",
            original_language="ja",
            official_english_title="Attack on Titan",
            romanized_original_title="",
            media_type="series",
        ),))

        with self.assertRaisesRegex(TitlePolicyError, "canonical_title_unavailable"):
            resolve_title_policy(candidate)

    def test_non_japanese_without_official_english_is_not_finalizable(self):
        candidate = CandidateEntity("douban:movie:4", (fact(
            fact_id="douban:4",
            provider="douban",
            titles=("杀马特我爱你",),
            original_title="杀马特我爱你",
            original_language="zh",
            official_english_title="",
        ),))

        with self.assertRaisesRegex(TitlePolicyError, "canonical_title_unavailable"):
            resolve_title_policy(candidate)


if __name__ == "__main__":
    unittest.main()
