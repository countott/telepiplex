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
        self.assertEqual(titles.chinese_title, "")

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

    def test_japanese_kanji_uses_provider_official_english_when_romaji_is_unavailable(self):
        candidate = CandidateEntity("tvdb:series:3", (fact(
            fact_id="tvdb:3",
            titles=("进击的巨人", "進撃の巨人", "Attack on Titan"),
            original_title="進撃の巨人",
            original_language="ja",
            official_english_title="Attack on Titan",
            romanized_original_title="",
            media_type="series",
            genres=("Animation",),
        ),))

        titles = resolve_title_policy(candidate)

        self.assertEqual(titles.canonical_latin_title, "Attack on Titan")
        self.assertEqual(titles.canonical_search_title, "Attack on Titan")
        self.assertEqual(
            titles.search_title_policy,
            "official_english_fallback",
        )

    def test_japanese_without_romaji_or_official_english_is_not_finalizable(self):
        candidate = CandidateEntity("tvdb:series:missing-latin", (fact(
            fact_id="tvdb:missing-latin",
            titles=("冰果", "氷菓"),
            original_title="氷菓",
            original_language="ja",
            official_english_title="",
            romanized_original_title="",
            media_type="series",
            genres=("Animation",),
        ),))

        with self.assertRaisesRegex(TitlePolicyError, "canonical_title_unavailable"):
            resolve_title_policy(candidate)

    def test_japanese_animation_never_derives_romaji_locally(self):
        for media_type in ("series", "movie"):
            with self.subTest(media_type=media_type):
                candidate = CandidateEntity(f"tvdb:{media_type}:3", (fact(
                    fact_id=f"tvdb:{media_type}:3",
                    titles=("蜂蜜与四叶草", "ハチミツとクローバー"),
                    original_title="ハチミツとクローバー",
                    original_language="ja",
                    official_english_title="Honey and Clover",
                    romanized_original_title="",
                    media_type=media_type,
                    genres=("Animation",),
                ),))

                titles = resolve_title_policy(candidate)

                self.assertEqual(titles.romanized_original_title, "")
                self.assertEqual(titles.canonical_search_title, "Honey and Clover")
                self.assertEqual(
                    titles.search_title_policy,
                    "official_english_fallback",
                )

    def test_kana_only_title_still_uses_source_backed_english(self):
        candidate = CandidateEntity("tvdb:movie:5", (fact(
            fact_id="tvdb:movie:5",
            titles=("キャット・ストーリー",),
            original_title="キャット・ストーリー",
            original_language="ja",
            official_english_title="Cat Story",
            romanized_original_title="",
            media_type="movie",
            genres=("Animation",),
        ),))

        titles = resolve_title_policy(candidate)

        self.assertEqual(titles.romanized_original_title, "")
        self.assertEqual(titles.canonical_search_title, "Cat Story")

    def test_anilist_romaji_is_trusted_for_japanese_animation(self):
        candidate = CandidateEntity("anilist:1142", (
            fact(
                fact_id="tvdb:series:79044",
                titles=("Honey and Clover",),
                original_title="ハチミツとクローバー",
                original_language="ja",
                official_english_title="Honey and Clover",
                romanized_original_title="",
                media_type="series",
                genres=("Anime",),
            ),
            fact(
                fact_id="anilist:1142",
                provider="anilist",
                titles=("Hachimitsu to Clover",),
                original_title="ハチミツとクローバー",
                original_language="ja",
                official_english_title="Honey and Clover",
                romanized_original_title="Hachimitsu to Clover",
                media_type="series",
                external_ids={"anilist": "1142"},
                genres=("Anime",),
            ),
        ))

        titles = resolve_title_policy(candidate)

        self.assertEqual(titles.romanized_original_title, "Hachimitsu to Clover")
        self.assertEqual(titles.canonical_search_title, "Hachimitsu to Clover")

    def test_explicit_japanese_romaji_precedes_derived_value(self):
        candidate = CandidateEntity("tvdb:series:4", (fact(
            fact_id="tvdb:series:4",
            titles=("進撃の巨人",),
            original_title="進撃の巨人",
            original_language="ja",
            official_english_title="Attack on Titan",
            romanized_original_title="Explicit Romaji",
            media_type="series",
            genres=("Anime",),
        ),))

        titles = resolve_title_policy(candidate)

        self.assertEqual(titles.romanized_original_title, "Explicit Romaji")
        self.assertEqual(titles.canonical_search_title, "Explicit Romaji")

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

    def test_verified_user_chinese_alias_beats_taiwan_wikipedia_title(self):
        candidate = CandidateEntity("tvdb:movie:855", (
            fact(
                fact_id="wikipedia:Q219150",
                provider="wikipedia",
                titles=("魔間行者", "Constantine (film)"),
                official_english_title="Constantine (film)",
            ),
            fact(
                fact_id="tvdb:855",
                provider="tvdb",
                titles=("Constantine", "地狱神探", "康斯坦丁"),
                official_english_title="Constantine",
            ),
        ))

        titles = resolve_title_policy(
            candidate,
            preferred_chinese_title="康斯坦丁",
        )

        self.assertEqual(titles.chinese_title, "康斯坦丁")

    def test_wikipedia_taiwan_title_is_not_mainland_chinese_fallback(self):
        candidate = CandidateEntity("wikipedia:movie:Q219150", (fact(
            fact_id="wikipedia:Q219150",
            provider="wikipedia",
            titles=("魔間行者", "Constantine (film)"),
            official_english_title="Constantine",
        ),))

        titles = resolve_title_policy(candidate)

        self.assertEqual(titles.chinese_title, "")

    def test_tvdb_taiwan_title_is_not_mainland_chinese_fallback(self):
        candidate = CandidateEntity("tvdb:movie:855", (fact(
            fact_id="tvdb:855",
            provider="tvdb",
            titles=("魔間行者", "Constantine"),
            official_english_title="Constantine",
        ),))

        titles = resolve_title_policy(candidate)

        self.assertEqual(titles.chinese_title, "")

    def test_douban_chinese_title_beats_wikipedia_title_without_conversion(self):
        candidate = CandidateEntity("douban:movie:1295644", (
            fact(
                fact_id="wikipedia:Q219150",
                provider="wikipedia",
                titles=("魔間行者", "Constantine (film)"),
                official_english_title="Constantine",
            ),
            fact(
                fact_id="douban:1295644",
                provider="douban",
                titles=("康斯坦丁", "Constantine"),
                official_english_title="Constantine",
            ),
        ))

        titles = resolve_title_policy(candidate)

        self.assertEqual(titles.chinese_title, "康斯坦丁")

    def test_verified_simple_chinese_wikipedia_title_is_accepted(self):
        candidate = CandidateEntity("wikipedia:Q74801", (fact(
            fact_id="wikipedia:Q74801",
            provider="wikipedia",
            titles=("副总统", "Veep"),
            chinese_title="副总统",
            official_english_title="Veep",
            original_title="Veep",
            original_language="en",
            media_type="series",
        ),))

        titles = resolve_title_policy(candidate)

        self.assertEqual(titles.chinese_title, "副总统")


if __name__ == "__main__":
    unittest.main()
