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
                        "idMal": 16,
                        "type": "ANIME",
                        "format": "TV",
                        "status": "FINISHED",
                        "seasonYear": 2005,
                        "episodes": 12,
                        "duration": 24,
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
                        "relations": {
                            "edges": [{
                                "relationType": "SEQUEL",
                                "node": {
                                    "id": 1143,
                                    "type": "ANIME",
                                    "format": "TV",
                                    "status": "FINISHED",
                                    "seasonYear": 2006,
                                    "episodes": 12,
                                    "siteUrl": "https://anilist.co/anime/1143",
                                    "title": {
                                        "native": "ハチミツとクローバーII",
                                        "romaji": "Hachimitsu to Clover II",
                                        "english": "Honey and Clover II",
                                    },
                                },
                            }],
                        },
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
        self.assertEqual(result[0]["release_format"], "TV")
        self.assertEqual(result[0]["status"], "FINISHED")
        self.assertEqual(result[0]["episode_count"], 12)
        self.assertEqual(result[0]["runtime_minutes"], 24)
        self.assertEqual(result[0]["cover_url"], "https://img.example/1142.jpg")
        self.assertEqual(result[0]["genres"], ["Drama", "Romance"])
        self.assertEqual(result[0]["external_ids"], {
            "anilist": "1142",
            "myanimelist": "16",
        })
        self.assertEqual(result[0]["relations"], [{
            "relation_type": "SEQUEL",
            "anilist_id": "1143",
            "release_format": "TV",
            "status": "FINISHED",
            "year": "2006",
            "episode_count": 12,
            "url": "https://anilist.co/anime/1143",
            "title_native": "ハチミツとクローバーII",
            "title_romaji": "Hachimitsu to Clover II",
            "title_english": "Honey and Clover II",
        }])
        self.assertIn("relations", post.call_args.args[0])

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

    def test_relation_evidence_is_bounded(self):
        fact = anilist._normalize_media({
            "id": 1,
            "format": "TV",
            "title": {"romaji": "Example Anime"},
            "relations": {
                "edges": [{
                    "relationType": "SEQUEL",
                    "node": {
                        "id": number,
                        "title": {"romaji": f"Example Anime {number}"},
                    },
                } for number in range(2, 62)],
            },
        })

        self.assertEqual(len(fact["relations"]), 50)


if __name__ == "__main__":
    unittest.main()
