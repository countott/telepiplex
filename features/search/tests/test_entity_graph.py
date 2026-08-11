import unittest

from telepiplex_search.entity_graph import (
    build_discovery_graph,
    build_search_graph,
)


class SearchEntityGraphTest(unittest.TestCase):
    def test_discovery_graph_preserves_conflicting_same_qid_occurrences(self):
        graph = build_discovery_graph([{
            "source": "wikipedia",
            "status": "ok",
            "facts": [{
                "wikibase_item": "Q1",
                "title": "作品",
                "year": "2013",
                "media_type": "series",
                "url": "https://zh.wikipedia.org/wiki/A",
            }, {
                "wikibase_item": "Q1",
                "title": "Work",
                "year": "2014",
                "media_type": "movie",
                "url": "https://en.wikipedia.org/wiki/A",
            }],
        }])

        facts = [
            fact
            for candidate in graph.candidates
            for fact in candidate.facts
        ]
        self.assertEqual(len(facts), 2)
        self.assertEqual(
            {fact.stable_fact_id for fact in facts},
            {"wikipedia:Q1"},
        )
        self.assertEqual(len({fact.fact_id for fact in facts}), 2)
        self.assertTrue(all(
            fact.fact_id.startswith("wikipedia:Q1@occurrence:")
            for fact in facts
        ))

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

    def test_same_tvdb_numeric_id_never_merges_movie_and_series(self):
        graph = build_search_graph([{
            "source": "tvdb",
            "status": "ok",
            "facts": [{
                "movies": [{
                    "tvdb_movie_id": "855",
                    "name": "康斯坦丁",
                    "english_title": "Constantine",
                    "year": "2005",
                }],
                "series": [{
                    "tvdb_series_id": "855",
                    "name": "康斯坦丁",
                    "english_title": "Constantine",
                    "year": "2014",
                }],
            }],
        }])

        self.assertEqual(len(graph.candidates), 2)
        self.assertEqual(
            {candidate.media_types for candidate in graph.candidates},
            {frozenset({"movie"}), frozenset({"series"})},
        )
        self.assertEqual(
            {
                fact.fact_id
                for candidate in graph.candidates
                for fact in candidate.facts
            },
            {"tvdb:movie:855", "tvdb:series:855"},
        )

    def test_tvdb_generic_id_is_a_typed_stable_fact_id(self):
        graph = build_search_graph([{
            "source": "tvdb",
            "status": "ok",
            "facts": [{
                "movies": [{
                    "id": 855,
                    "name": "Constantine",
                    "year": "2005",
                }],
                "series": [{
                    "id": 273690,
                    "name": "Constantine",
                    "year": "2014",
                }],
            }],
        }])

        self.assertEqual(
            {
                fact.fact_id
                for candidate in graph.candidates
                for fact in candidate.facts
            },
            {"tvdb:movie:855", "tvdb:series:273690"},
        )

    def test_missing_provider_id_uses_nonempty_opaque_fallback(self):
        graph = build_search_graph([{
            "source": "wikipedia",
            "status": "ok",
            "facts": [{
                "title": "Provider ID missing",
                "year": "2024",
                "media_type": "movie",
                "external_ids": {"imdb": "tt1234567"},
            }],
        }])

        fact = graph.candidates[0].facts[0]
        self.assertRegex(
            fact.fact_id,
            r"^wikipedia:request:[0-9a-f]{16}$",
        )
        self.assertNotIn("tt1234567", fact.fact_id)

    def test_same_wikipedia_item_from_multiple_languages_becomes_one_fact(self):
        graph = build_search_graph([{
            "source": "wikipedia",
            "status": "ok",
            "facts": [{
                "wikibase_item": "Q546916",
                "query": "蜂蜜与四叶草",
                "language": "zh",
                "title": "蜂蜜与四叶草",
                "chinese_title": "蜂蜜与四叶草",
                "year": "2005",
                "media_type": "series",
                "url": "https://zh.wikipedia.org/wiki/蜂蜜与四叶草",
            }, {
                "wikibase_item": "Q546916",
                "query": "Honey and Clover",
                "language": "en",
                "title": "Honey and Clover",
                "official_english_title": "Honey and Clover",
                "year": "2005",
                "media_type": "series",
                "url": "https://en.wikipedia.org/wiki/Honey_and_Clover",
            }],
        }])

        self.assertEqual(len(graph.candidates), 1)
        self.assertEqual(len(graph.candidates[0].facts), 1)
        fact = graph.candidates[0].facts[0]
        self.assertEqual(fact.fact_id, "wikipedia:Q546916")
        self.assertEqual(
            set(fact.titles),
            {"蜂蜜与四叶草", "Honey and Clover"},
        )
        self.assertEqual(fact.chinese_title, "蜂蜜与四叶草")
        self.assertEqual(fact.official_english_title, "Honey and Clover")
        self.assertEqual(
            [
                (item.provider, item.fact_id, item.occurrences)
                for item in graph.fact_merges
            ],
            [("wikipedia", "wikipedia:Q546916", 2)],
        )

    def test_same_tvdb_series_merges_complementary_episode_inventories(self):
        graph = build_search_graph([{
            "source": "tvdb",
            "status": "ok",
            "facts": [{
                "query": "蜂蜜与四叶草",
                "movies": [],
                "series": [{
                    "tvdb_series_id": "79044",
                    "name": "ハチミツとクローバー",
                    "year": "2005",
                }],
                "episodes_by_series": {
                    "79044": [{
                        "tvdb_episode_id": "e1",
                        "season_number": 1,
                        "episode_number": 1,
                        "name": "Episode 1",
                    }],
                },
            }, {
                "query": "Honey and Clover",
                "movies": [],
                "series": [{
                    "tvdb_series_id": "79044",
                    "name": "Honey and Clover",
                    "official_english_title": "Honey and Clover",
                    "year": "2005",
                }],
                "episodes_by_series": {
                    "79044": [{
                        "tvdb_episode_id": "e1",
                        "season_number": 1,
                        "episode_number": 1,
                        "name": "Episode 1",
                    }, {
                        "tvdb_episode_id": "e2",
                        "season_number": 1,
                        "episode_number": 2,
                        "name": "Episode 2",
                    }],
                },
            }],
        }])

        self.assertEqual(len(graph.candidates), 1)
        self.assertEqual(len(graph.candidates[0].facts), 1)
        fact = graph.candidates[0].facts[0]
        self.assertEqual(fact.fact_id, "tvdb:series:79044")
        self.assertEqual(
            {
                (
                    episode["tvdb_episode_id"],
                    episode["season_number"],
                    episode["episode_number"],
                )
                for episode in fact.episodes
            },
            {("e1", 1, 1), ("e2", 1, 2)},
        )

    def test_idless_episode_merges_into_matching_id_backed_episode(self):
        graph = build_search_graph([{
            "source": "tvdb",
            "status": "ok",
            "facts": [{
                "movies": [],
                "series": [{
                    "tvdb_series_id": "79044",
                    "name": "Honey and Clover",
                    "year": "2005",
                }],
                "episodes_by_series": {
                    "79044": [{
                        "tvdb_episode_id": "e1",
                        "season_number": 1,
                        "episode_number": 1,
                        "name": "Episode 1",
                    }],
                },
            }, {
                "movies": [],
                "series": [{
                    "tvdb_series_id": "79044",
                    "name": "Honey and Clover",
                    "year": "2005",
                }],
                "episodes_by_series": {
                    "79044": [{
                        "season_number": "1",
                        "episode_number": "1",
                        "overview": "The opening episode.",
                    }],
                },
            }],
        }])

        episodes = graph.candidates[0].facts[0].episodes
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["tvdb_episode_id"], "e1")
        self.assertEqual(episodes[0]["overview"], "The opening episode.")

    def test_same_episode_id_with_conflicting_coordinates_is_rejected(self):
        with self.assertRaises(ValueError) as raised:
            build_search_graph([{
                "source": "tvdb",
                "status": "ok",
                "facts": [{
                    "movies": [],
                    "series": [{
                        "tvdb_series_id": "79044",
                        "name": "Honey and Clover",
                        "year": "2005",
                    }],
                    "episodes_by_series": {
                        "79044": [{
                            "tvdb_episode_id": "e1",
                            "season_number": 1,
                            "episode_number": 1,
                        }],
                    },
                }, {
                    "movies": [],
                    "series": [{
                        "tvdb_series_id": "79044",
                        "name": "Honey and Clover",
                        "year": "2005",
                    }],
                    "episodes_by_series": {
                        "79044": [{
                            "tvdb_episode_id": "e1",
                            "season_number": 1,
                            "episode_number": 2,
                        }],
                    },
                }],
            }])

        self.assertEqual(
            getattr(raised.exception, "fact_id", ""),
            "tvdb:series:79044",
        )
        self.assertEqual(
            getattr(raised.exception, "conflicting_fields", ()),
            ("episodes.e1.episode_number",),
        )

    def test_fact_convergence_is_independent_of_provider_result_order(self):
        facts = [{
            "wikibase_item": "Q546916",
            "title": "蜂蜜与四叶草",
            "chinese_title": "蜂蜜与四叶草",
            "year": "2005",
            "media_type": "series",
            "url": "https://zh.wikipedia.org/wiki/蜂蜜与四叶草",
        }, {
            "wikibase_item": "Q546916",
            "title": "Honey and Clover",
            "official_english_title": "Honey and Clover",
            "year": "2005",
            "media_type": "series",
            "url": "https://en.wikipedia.org/wiki/Honey_and_Clover",
        }]

        forward = build_search_graph([{
            "source": "wikipedia",
            "status": "ok",
            "facts": facts,
        }])
        reverse = build_search_graph([{
            "source": "wikipedia",
            "status": "ok",
            "facts": list(reversed(facts)),
        }])

        self.assertEqual(forward, reverse)

    def test_same_stable_fact_with_conflicting_year_is_rejected(self):
        with self.assertRaises(ValueError) as raised:
            build_search_graph([{
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "wikibase_item": "Q-conflict",
                    "title": "Conflicting Work",
                    "year": "2005",
                    "media_type": "movie",
                }, {
                    "wikibase_item": "Q-conflict",
                    "title": "Conflicting Work",
                    "year": "2006",
                    "media_type": "movie",
                }],
            }])

        self.assertEqual(
            getattr(raised.exception, "fact_id", ""),
            "wikipedia:Q-conflict",
        )
        self.assertEqual(
            getattr(raised.exception, "conflicting_fields", ()),
            ("year",),
        )

    def test_same_stable_fact_with_conflicting_external_id_is_rejected(self):
        with self.assertRaises(ValueError) as raised:
            build_search_graph([{
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "wikibase_item": "Q-conflict",
                    "title": "Conflicting Work",
                    "year": "2005",
                    "media_type": "movie",
                    "external_ids": {"imdb": "tt0000001"},
                }, {
                    "wikibase_item": "Q-conflict",
                    "title": "Conflicting Work",
                    "year": "2005",
                    "media_type": "movie",
                    "external_ids": {"imdb": "tt0000002"},
                }],
            }])

        self.assertEqual(
            getattr(raised.exception, "fact_id", ""),
            "wikipedia:Q-conflict",
        )
        self.assertEqual(
            getattr(raised.exception, "conflicting_fields", ()),
            ("external_ids.imdb",),
        )

    def test_all_identity_conflicts_are_reported_together(self):
        with self.assertRaises(ValueError) as raised:
            build_search_graph([{
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "wikibase_item": "Q-multi-conflict",
                    "title": "Conflicting Work",
                    "year": "2005",
                    "media_type": "movie",
                    "external_ids": {"imdb": "tt0000001"},
                }, {
                    "wikibase_item": "Q-multi-conflict",
                    "title": "Conflicting Work",
                    "year": "2006",
                    "media_type": "series",
                    "external_ids": {"imdb": "tt0000002"},
                }],
            }])

        self.assertEqual(
            getattr(raised.exception, "conflicting_fields", ()),
            ("external_ids.imdb", "media_type", "year"),
        )

    def test_untyped_fact_cannot_bridge_movie_and_series_by_different_ids(self):
        graph = build_search_graph([
            {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "wikibase_item": "Q-constantine",
                    "title": "Constantine",
                    "year": "2005",
                    "external_ids": {
                        "imdb": "tt0360486",
                        "tvdb": "273690",
                    },
                }],
            },
            {
                "source": "douban",
                "status": "ok",
                "facts": [{
                    "subject_id": "1295644",
                    "title": "Constantine",
                    "year": "2005",
                    "media_type": "movie",
                    "external_ids": {"imdb": "tt0360486"},
                }],
            },
            {
                "source": "tvdb",
                "status": "ok",
                "facts": [{
                    "movies": [],
                    "series": [{
                        "tvdb_series_id": "273690",
                        "name": "Constantine",
                        "year": "2014",
                    }],
                }],
            },
        ])

        self.assertEqual(len(graph.candidates), 2)
        self.assertNotIn(
            frozenset({"movie", "series"}),
            {candidate.media_types for candidate in graph.candidates},
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

    def test_tmdb_and_tvdb_merge_by_shared_external_id(self):
        graph = build_search_graph([
            {
                "source": "tmdb",
                "status": "ok",
                "facts": [{
                    "id": 438631,
                    "title": "Dune",
                    "year": "2021",
                    "media_type": "movie",
                    "external_ids": {
                        "tmdb": "438631",
                        "tvdb": "769",
                        "imdb": "tt1160419",
                    },
                }],
            },
            {
                "source": "tvdb",
                "status": "ok",
                "facts": [{
                    "movies": [{
                        "tvdb_movie_id": "769",
                        "name": "Dune",
                        "year": "2021",
                    }],
                    "series": [],
                }],
            },
        ])

        self.assertEqual(len(graph.candidates), 1)
        candidate = graph.candidates[0]
        self.assertEqual(candidate.providers, frozenset({"tmdb", "tvdb"}))
        self.assertEqual(
            dict(candidate.external_ids),
            {"tmdb": "438631", "tvdb": "769", "imdb": "tt1160419"},
        )
        self.assertEqual(
            {fact.fact_id for fact in candidate.facts},
            {"tmdb:438631", "tvdb:movie:769"},
        )

    def test_anilist_uses_stable_id_and_keeps_descriptive_fields(self):
        graph = build_search_graph([{
            "source": "anilist",
            "status": "ok",
            "facts": [{
                "id": 1142,
                "title": "Hachimitsu to Clover",
                "romanized_original_title": "Hachimitsu to Clover",
                "original_title": "ハチミツとクローバー",
                "year": "2005",
                "media_type": "series",
                "external_ids": {"anilist": "1142"},
                "status": "FINISHED",
                "season_count": 1,
                "episode_count": 24,
                "studios": ["J.C.STAFF"],
            }],
        }])

        fact = graph.candidates[0].facts[0]
        self.assertEqual(fact.fact_id, "anilist:1142")
        self.assertEqual(fact.status, "FINISHED")
        self.assertEqual(fact.season_count, 1)
        self.assertEqual(fact.episode_count, 24)
        self.assertEqual(fact.studios, ("J.C.STAFF",))


if __name__ == "__main__":
    unittest.main()
