import unittest

from telepiplex_search.entity_graph import build_search_graph


class SearchEntityGraphTest(unittest.TestCase):
    def test_same_title_movie_and_series_do_not_merge(self):
        graph = build_search_graph([
            {
                "source": "douban",
                "status": "ok",
                "facts": [{
                    "subject_id": "movie-1",
                    "title": "想见你",
                    "chinese_title": "想见你",
                    "english_title": "Someday or One Day The Movie",
                    "year": "2022",
                    "media_type": "movie",
                }],
            },
            {
                "source": "tvdb",
                "status": "ok",
                "facts": [{
                    "series": [{
                        "tvdb_series_id": "series-1",
                        "name": "想见你",
                        "english_title": "Someday or One Day",
                        "year": "2019",
                    }],
                    "movies": [],
                }],
            },
        ])

        self.assertEqual(len(graph.candidates), 2)
        self.assertEqual(
            {next(iter(item.media_types)) for item in graph.candidates},
            {"movie", "series"},
        )

    def test_title_year_and_type_merge_independent_sources(self):
        graph = build_search_graph([
            {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "wikibase_item": "Q123",
                    "title": "The Grand Budapest Hotel",
                    "english_title": "The Grand Budapest Hotel",
                    "year": "2014",
                    "media_type": "movie",
                    "url": "https://en.wikipedia.org/wiki/The_Grand_Budapest_Hotel",
                }],
            },
            {
                "source": "douban",
                "status": "ok",
                "facts": [{
                    "subject_id": "11525673",
                    "title": "The Grand Budapest Hotel",
                    "chinese_title": "布达佩斯大饭店",
                    "english_title": "The Grand Budapest Hotel",
                    "year": "2014",
                    "media_type": "movie",
                }],
            },
            {
                "source": "tvdb",
                "status": "ok",
                "facts": [{
                    "series": [],
                    "movies": [{
                        "tvdb_movie_id": "12345",
                        "name": "The Grand Budapest Hotel",
                        "english_title": "The Grand Budapest Hotel",
                        "year": "2014",
                    }],
                }],
            },
        ])

        self.assertEqual(len(graph.candidates), 1)
        candidate = graph.candidates[0]
        self.assertEqual(
            candidate.providers,
            frozenset({"wikipedia", "douban", "tvdb"}),
        )
        self.assertEqual(len(candidate.facts), 3)

    def test_search_mentions_do_not_merge_into_exact_title(self):
        graph = build_search_graph([
            {
                "source": "wikipedia",
                "status": "ok",
                "facts": [
                    {
                        "wikibase_item": "Qnoise",
                        "title": "下一站，幸福",
                        "chinese_title": "下一站，幸福",
                        "year": "2009",
                        "media_type": "series",
                        "extract": "搜索摘要提到了杀马特我爱你。",
                    },
                    {
                        "wikibase_item": "Qshamate",
                        "title": "杀马特我爱你",
                        "chinese_title": "杀马特我爱你",
                        "english_title": "We Were Smart",
                        "year": "2019",
                        "media_type": "movie",
                    },
                ],
            },
            {
                "source": "douban",
                "status": "ok",
                "facts": [{
                    "subject_id": "34937935",
                    "title": "杀马特我爱你",
                    "chinese_title": "杀马特我爱你",
                    "english_title": "We Were Smart",
                    "year": "2019",
                    "media_type": "movie",
                }],
            },
        ])

        exact = [
            item for item in graph.candidates
            if "杀马特我爱你" in item.titles
        ]
        self.assertEqual(len(exact), 1)
        self.assertEqual(
            exact[0].providers,
            frozenset({"wikipedia", "douban"}),
        )
        self.assertNotIn("下一站，幸福", exact[0].titles)

    def test_poster_prefers_original_language_over_provider_priority(self):
        graph = build_search_graph([
            {
                "source": "tvdb",
                "status": "ok",
                "facts": [{
                    "movies": [{
                        "tvdb_movie_id": "855",
                        "name": "Constantine",
                        "english_title": "Constantine",
                        "year": "2005",
                        "original_language": "en",
                        "cover_url": "https://art.example/zh-poster.jpg",
                        "poster_language": "zh",
                    }],
                    "series": [],
                }],
            },
            {
                "source": "douban",
                "status": "ok",
                "facts": [{
                    "subject_id": "1295644",
                    "title": "Constantine",
                    "english_title": "Constantine",
                    "year": "2005",
                    "media_type": "movie",
                    "original_language": "en",
                    "cover_url": "https://art.example/en-poster.jpg",
                    "poster_language": "en",
                }],
            },
        ])

        self.assertEqual(len(graph.candidates), 1)
        self.assertEqual(
            graph.candidates[0].poster_url,
            "https://art.example/en-poster.jpg",
        )


if __name__ == "__main__":
    unittest.main()
