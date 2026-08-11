import unittest
from unittest.mock import patch

from telepiplex_search.adapters import anilist
from telepiplex_search.context import runtime_context


class AniListAdapterTest(unittest.TestCase):
    def setUp(self):
        runtime_context.configure({
            "metadata": {
                "anilist": {
                    "enable": True,
                    "endpoint": "https://graphql.anilist.co",
                    "timeout": 15,
                },
            },
        })

    def test_disabled_has_explicit_status_code(self):
        runtime_context.configure({"metadata": {"anilist": {"enable": False}}})

        with self.assertRaises(anilist.AniListConfigError) as raised:
            anilist._get_anilist_config()

        self.assertEqual(raised.exception.code, "disabled")

    @patch.object(anilist, "_anilist_post")
    def test_search_normalizes_native_romaji_english_and_synonyms(self, post):
        post.return_value = {
            "data": {
                "Page": {
                    "media": [{
                        "id": 1142,
                        "type": "ANIME",
                        "format": "TV",
                        "status": "FINISHED",
                        "seasonYear": 2005,
                        "episodes": 12,
                        "countryOfOrigin": "JP",
                        "title": {
                            "native": "ハチミツとクローバーII",
                            "romaji": "Hachimitsu to Clover II",
                            "english": "Honey and Clover II",
                        },
                        "synonyms": ["Honey & Clover II"],
                        "genres": ["Drama", "Romance"],
                        "coverImage": {"extraLarge": "https://img.example/1142.jpg"},
                        "siteUrl": "https://anilist.co/anime/1142",
                    }],
                },
            },
        }

        result = anilist.search_anilist("ハチミツとクローバーII", "2005")

        self.assertEqual(result[0]["anilist_id"], "1142")
        self.assertEqual(result[0]["original_title"], "ハチミツとクローバーII")
        self.assertEqual(result[0]["romanized_original_title"], "Hachimitsu to Clover II")
        self.assertEqual(result[0]["official_english_title"], "Honey and Clover II")
        self.assertIn("Honey & Clover II", result[0]["aliases"])
        self.assertEqual(result[0]["media_type"], "series")

    @patch.object(anilist, "_anilist_post")
    def test_get_media_by_id_uses_stable_identity(self, post):
        post.return_value = {
            "data": {
                "Media": {
                    "id": 199,
                    "type": "ANIME",
                    "format": "MOVIE",
                    "seasonYear": 2001,
                    "countryOfOrigin": "JP",
                    "title": {
                        "native": "千と千尋の神隠し",
                        "romaji": "Sen to Chihiro no Kamikakushi",
                        "english": "Spirited Away",
                    },
                    "synonyms": [],
                    "genres": ["Adventure"],
                    "coverImage": {"large": "https://img.example/199.jpg"},
                    "siteUrl": "https://anilist.co/anime/199",
                },
            },
        }

        fact = anilist.get_anilist_media("199")

        self.assertEqual(fact["external_ids"], {"anilist": "199"})
        self.assertEqual(fact["media_type"], "movie")


if __name__ == "__main__":
    unittest.main()
