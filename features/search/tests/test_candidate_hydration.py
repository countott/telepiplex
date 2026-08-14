import unittest

from telepiplex_search.candidate_hydration import (
    CandidateHydrationError,
    hydrate_frozen_candidate,
)
from telepiplex_search.direct_link import DirectEntity, DirectLinkError


def _candidate():
    return {
        "candidate_key": "film-1",
        "candidate_id": "film-1",
        "anchor_fact_id": "tvdb:movie:77",
        "identity_role": "movie",
        "intended_scope": "movie",
        "links_frozen": True,
        "ai_confidence": 0.97,
        "ai_reason": "Verified film.",
        "unresolved_sources": [],
        "source_links": [{
            "provider": "douban",
            "fact_id": "douban:11",
            "url": "https://movie.douban.com/subject/11/",
            "external_ids": {"douban_subject": "11"},
            "role": "movie",
            "season_number": None,
            "episode_number": None,
            "verification": "fact_verified",
        }, {
            "provider": "tvdb",
            "fact_id": "tvdb:movie:77",
            "url": "https://thetvdb.com/movies/77",
            "external_ids": {"tvdb": "77"},
            "role": "movie",
            "season_number": None,
            "episode_number": None,
            "verification": "fact_verified",
        }, {
            "provider": "wikipedia",
            "fact_id": "wikipedia:Q77",
            "url": "https://en.wikipedia.org/wiki/The_Grand_Budapest_Hotel",
            "external_ids": {"wikipedia": "Q77"},
            "role": "movie",
            "season_number": None,
            "episode_number": None,
            "verification": "fact_verified",
        }],
    }


def _resolver(calls, *, fail=()):
    def resolve(link):
        calls.append(link.url)
        if link.provider in fail:
            raise DirectLinkError("direct_link_not_found")
        if link.provider == "douban":
            fact = {
                "subject_id": "11",
                "title": "布达佩斯大饭店",
                "douban_title_raw": "布达佩斯大饭店 第一季",
                "chinese_title": "布达佩斯大饭店",
                "season_number": 1,
                "official_english_title": "The Grand Budapest Hotel",
                "original_title": "The Grand Budapest Hotel",
                "original_language": "en",
                "year": "2014",
                "media_type": "movie",
                "url": link.url,
                "cover_url": "https://art.example/douban.jpg",
            }
            stable = ("douban_subject", "11")
            evidence = {
                "source": "douban",
                "status": "ok",
                "facts": [fact],
                "source_urls": [link.url],
            }
        elif link.provider == "tvdb":
            fact = {
                "movies": [{
                    "tvdb_movie_id": "77",
                    "name": "The Grand Budapest Hotel",
                    "chinese_title": "布达佩斯大饭店",
                    "official_english_title": "The Grand Budapest Hotel",
                    "original_title": "The Grand Budapest Hotel",
                    "original_language": "en",
                    "year": "2014",
                    "url": link.url,
                    "cover_url": "https://art.example/tvdb.jpg",
                }],
                "series": [],
                "episodes_by_series": {},
            }
            stable = ("tvdb", "77")
            evidence = {
                "source": "tvdb",
                "status": "ok",
                "facts": [fact],
                "source_urls": [link.url],
            }
        else:
            fact = {
                "wikibase_item": "Q77",
                "title": "The Grand Budapest Hotel",
                "chinese_title": "布达佩斯大饭店",
                "official_english_title": "The Grand Budapest Hotel",
                "original_title": "The Grand Budapest Hotel",
                "original_language": "en",
                "year": "2014",
                "media_type": "movie",
                "url": link.url,
            }
            stable = ("wikipedia", "Q77")
            evidence = {
                "source": "wikipedia",
                "status": "ok",
                "facts": [fact],
                "source_urls": [link.url],
            }
        return DirectEntity(
            provider=link.provider,
            evidence=evidence,
            stable_identity=stable,
            title="The Grand Budapest Hotel",
            year="2014",
            media_type="movie",
            scope="work",
        )
    return resolve


class CandidateHydrationTest(unittest.TestCase):
    def test_exact_reads_only_saved_urls_and_rebuilds_v1(self):
        calls = []
        candidate = _candidate()
        candidate["douban_match_mode"] = "imdb_exact"

        hydrated = hydrate_frozen_candidate(
            candidate,
            metadata_id="m1",
            raw_query="布达佩斯大饭店",
            resolver=_resolver(calls),
        )

        self.assertCountEqual(
            calls,
            [item["url"] for item in _candidate()["source_links"]],
        )
        self.assertTrue(hydrated["metadata_hydrated"])
        self.assertEqual(len(hydrated["source_links"]), 3)
        self.assertEqual(
            hydrated["media_metadata"]["identity"]["english_title"],
            "The Grand Budapest Hotel",
        )
        chinese_sources = hydrated["media_metadata"]["evidence"][
            "field_sources"
        ]["chinese_title"]
        douban_source = next(
            item for item in chinese_sources
            if item["provider"] == "douban"
        )
        self.assertEqual(douban_source["match_mode"], "imdb_exact")
        self.assertEqual(
            douban_source["douban_title_raw"],
            "布达佩斯大饭店 第一季",
        )
        self.assertEqual(douban_source["season_number"], 1)

    def test_partial_exact_read_failure_continues_when_v1_is_complete(self):
        calls = []

        hydrated = hydrate_frozen_candidate(
            _candidate(),
            metadata_id="m2",
            raw_query="布达佩斯大饭店",
            resolver=_resolver(calls, fail={"wikipedia"}),
        )

        self.assertEqual(len(hydrated["source_links"]), 2)
        self.assertIn(
            "warning:source_unresolved",
            hydrated["media_metadata"]["warnings"],
        )
        self.assertTrue(any(
            "wikipedia:Q77:fixed_link_read_failed" in item
            for item in hydrated["unresolved_sources"]
        ))

    def test_fixed_link_anchor_failure_is_explicit(self):
        candidate = _candidate()
        candidate["anchor_fact_id"] = "douban:11"

        with self.assertRaisesRegex(
            CandidateHydrationError,
            "fixed_link_read_failed",
        ):
            hydrate_frozen_candidate(
                candidate,
                metadata_id="m3",
                raw_query="link",
                require_anchor=True,
                resolver=_resolver([], fail={"douban"}),
            )

    def test_conflicting_exact_source_identity_is_a_structured_hydration_error(self):
        candidate = _candidate()
        candidate["anchor_fact_id"] = "wikipedia:Q77"
        candidate["source_links"] = [candidate["source_links"][2]]

        def conflicting(link):
            return DirectEntity(
                provider="wikipedia",
                evidence={
                    "source": "wikipedia",
                    "status": "ok",
                    "facts": [{
                        "wikibase_item": "Q77",
                        "title": "The Grand Budapest Hotel",
                        "year": "2014",
                        "media_type": "movie",
                        "url": link.url,
                    }, {
                        "wikibase_item": "Q77",
                        "title": "The Grand Budapest Hotel",
                        "year": "2015",
                        "media_type": "movie",
                        "url": link.url,
                    }],
                    "source_urls": [link.url],
                },
                stable_identity=("wikipedia", "Q77"),
                title="The Grand Budapest Hotel",
                year="2014",
                media_type="movie",
                scope="work",
            )

        with self.assertRaises(Exception) as raised:
            hydrate_frozen_candidate(
                candidate,
                metadata_id="conflicting-exact",
                raw_query="布达佩斯大饭店",
                require_anchor=True,
                resolver=conflicting,
            )

        self.assertIsInstance(
            raised.exception,
            CandidateHydrationError,
        )
        self.assertEqual(
            getattr(raised.exception, "code", ""),
            "source_fact_conflict",
        )
        self.assertEqual(
            getattr(raised.exception, "details", ()),
            ("wikipedia:Q77", "field:year"),
        )

    def test_discovery_occurrence_anchor_maps_to_exact_stable_fact(self):
        candidate = _candidate()
        occurrence_id = "wikipedia:Q77@occurrence:abc123"
        candidate["anchor_fact_id"] = occurrence_id
        candidate["source_links"][2]["fact_id"] = occurrence_id

        hydrated = hydrate_frozen_candidate(
            candidate,
            metadata_id="occurrence-anchor",
            raw_query="布达佩斯大饭店",
            require_anchor=True,
            resolver=_resolver([]),
        )

        self.assertEqual(hydrated["anchor_fact_id"], "wikipedia:Q77")
        self.assertIn(
            "wikipedia:Q77",
            {
                link["fact_id"]
                for link in hydrated["source_links"]
            },
        )

    def test_non_anchor_source_conflict_is_quarantined(self):
        candidate = _candidate()
        candidate["candidate_version"] = "v1"

        def resolver(link):
            if link.provider != "wikipedia":
                return _resolver([])(link)
            return DirectEntity(
                provider="wikipedia",
                evidence={
                    "source": "wikipedia",
                    "status": "ok",
                    "facts": [{
                        "wikibase_item": "Q77",
                        "title": "The Grand Budapest Hotel",
                        "year": "2014",
                        "media_type": "movie",
                        "url": link.url,
                    }, {
                        "wikibase_item": "Q77",
                        "title": "The Grand Budapest Hotel",
                        "year": "2015",
                        "media_type": "movie",
                        "url": link.url,
                    }],
                    "source_urls": [link.url],
                },
                stable_identity=("wikipedia", "Q77"),
                title="The Grand Budapest Hotel",
                year="2014",
                media_type="movie",
                scope="work",
            )

        hydrated = hydrate_frozen_candidate(
            candidate,
            metadata_id="non-anchor-conflict",
            raw_query="布达佩斯大饭店",
            require_anchor=True,
            resolver=resolver,
        )

        self.assertEqual(
            {link["provider"] for link in hydrated["source_links"]},
            {"douban", "tvdb"},
        )
        self.assertEqual(hydrated["candidate_version"], "v0")
        self.assertTrue(any(
            item.startswith("wikipedia:source_fact_conflict:")
            for item in hydrated["unresolved_sources"]
        ))

    def test_exact_tvdb_inventory_verifies_preserved_season_scope(self):
        candidate = {
            "candidate_key": "honey-and-clover-s2",
            "candidate_id": "honey-and-clover-s2",
            "anchor_fact_id": "douban:102",
            "identity_role": "season",
            "intended_scope": "season",
            "links_frozen": True,
            "ai_confidence": 0.98,
            "ai_reason": "The Douban entry is season 2 of the TVDB series.",
            "unresolved_sources": [
                "douban:102:unresolved_scope_link",
                "wikipedia:rate_limited",
            ],
            "source_links": [{
                "provider": "douban",
                "fact_id": "douban:102",
                "url": "https://movie.douban.com/subject/102/",
                "external_ids": {"douban_subject": "102"},
                "role": "season",
                "season_number": None,
                "episode_number": None,
                "verification": "unresolved_scope_link",
                "proposed_season_number": 2,
                "proposed_episode_number": None,
            }, {
                "provider": "tvdb",
                "fact_id": "tvdb:series:900",
                "url": "https://thetvdb.com/series/900",
                "external_ids": {"tvdb": "900"},
                "role": "series_root",
                "season_number": None,
                "episode_number": None,
                "verification": "fact_verified",
                "proposed_season_number": None,
                "proposed_episode_number": None,
            }],
        }

        def resolver(link):
            if link.provider == "douban":
                fact = {
                    "subject_id": "102",
                    "title": "蜂蜜与四叶草II",
                    "chinese_title": "蜂蜜与四叶草II",
                    "official_english_title": "Honey and Clover II",
                    "original_title": "ハチミツとクローバーII",
                    "romanized_original_title": "Hachimitsu to Clover II",
                    "original_language": "ja",
                    "year": "2006",
                    "media_type": "series",
                    "url": link.url,
                }
                stable = ("douban_subject", "102")
                evidence = {
                    "source": "douban",
                    "status": "ok",
                    "facts": [fact],
                    "source_urls": [link.url],
                }
            else:
                fact = {
                    "movies": [],
                    "series": [{
                        "tvdb_series_id": "900",
                        "name": "Honey and Clover",
                        "chinese_title": "蜂蜜与四叶草",
                        "official_english_title": "Honey and Clover",
                        "original_title": "ハチミツとクローバー",
                        "romanized_original_title": "Hachimitsu to Clover",
                        "original_language": "ja",
                        "year": "2005",
                        "genres": ["Anime"],
                        "url": link.url,
                    }],
                    "episodes_by_series": {
                        "900": [{
                            "tvdb_episode_id": "s2e1",
                            "season_number": 2,
                            "episode_number": 1,
                        }],
                    },
                }
                stable = ("tvdb", "900")
                evidence = {
                    "source": "tvdb",
                    "status": "ok",
                    "facts": [fact],
                    "source_urls": [link.url],
                }
            return DirectEntity(
                provider=link.provider,
                evidence=evidence,
                stable_identity=stable,
                title="Honey and Clover",
                year="2005",
                media_type="series",
                scope="work",
            )

        hydrated = hydrate_frozen_candidate(
            candidate,
            metadata_id="honey-s2",
            raw_query="蜂蜜与四叶草 第二季",
            require_anchor=True,
            resolver=resolver,
        )

        season_link = next(
            link for link in hydrated["source_links"]
            if link["fact_id"] == "douban:102"
        )
        self.assertEqual(season_link["season_number"], 2)
        self.assertEqual(
            season_link["verification"],
            "tvdb_inventory_verified",
        )
        self.assertEqual(
            hydrated["media_metadata"]["evidence"]["decision"]["season_number"],
            2,
        )
        self.assertNotIn(
            "douban:102:unresolved_scope_link",
            hydrated["unresolved_sources"],
        )
        self.assertIn(
            "wikipedia:rate_limited",
            hydrated["unresolved_sources"],
        )

    def test_douban_foreign_season_hydrates_to_english_prowlarr_query(self):
        candidate = {
            "candidate_key": "douban_subject:36666949",
            "candidate_id": "douban_subject:36666949",
            "anchor_fact_id": "douban:36666949",
            "identity_role": "season",
            "intended_scope": "season",
            "links_frozen": True,
            "ai_confidence": 0,
            "ai_reason": "Deterministic direct season link.",
            "unresolved_sources": [
                "douban:36666949:unresolved_scope_link",
            ],
            "source_links": [{
                "provider": "douban",
                "fact_id": "douban:36666949",
                "url": "https://movie.douban.com/subject/36666949/",
                "external_ids": {"douban_subject": "36666949"},
                "role": "season",
                "season_number": None,
                "episode_number": None,
                "verification": "unresolved_scope_link",
                "proposed_season_number": 3,
                "proposed_episode_number": None,
            }, {
                "provider": "tmdb",
                "fact_id": "tmdb:94997",
                "url": "https://www.themoviedb.org/tv/94997",
                "external_ids": {"tmdb": "94997"},
                "role": "series_root",
                "season_number": None,
                "episode_number": None,
                "verification": "fact_verified",
                "proposed_season_number": None,
                "proposed_episode_number": None,
            }],
        }

        def resolver(link):
            if link.provider == "douban":
                fact = {
                    "subject_id": "36666949",
                    "title": "龙之家族",
                    "chinese_title": "龙之家族",
                    "douban_title_raw": "龙之家族 第三季",
                    "official_english_title": "House of the Dragon",
                    "original_title": "House of the Dragon",
                    "original_language": "en",
                    "year": "",
                    "media_type": "series",
                    "url": link.url,
                }
                stable = ("douban_subject", "36666949")
            else:
                fact = {
                    "tmdb_id": "94997",
                    "title": "House of the Dragon",
                    "official_english_title": "House of the Dragon",
                    "original_title": "House of the Dragon",
                    "original_language": "en",
                    "year": "2022",
                    "media_type": "series",
                    "url": link.url,
                    "episodes": [{
                        "tmdb_episode_id": "s3e1",
                        "season_number": 3,
                        "episode_number": 1,
                    }],
                }
                stable = ("tmdb", "94997")
            return DirectEntity(
                provider=link.provider,
                evidence={
                    "source": link.provider,
                    "status": "ok",
                    "facts": [fact],
                    "source_urls": [link.url],
                },
                stable_identity=stable,
                title="House of the Dragon",
                year="2022",
                media_type="series",
                scope="work",
            )

        hydrated = hydrate_frozen_candidate(
            candidate,
            metadata_id="house-of-the-dragon-s3",
            raw_query="https://movie.douban.com/subject/36666949/",
            require_anchor=True,
            resolver=resolver,
        )

        self.assertEqual(
            hydrated["media_metadata"]["retrieval"]["queries"][0],
            "House of the Dragon S03",
        )
        self.assertEqual(
            hydrated["media_metadata"]["evidence"]["decision"]["season_number"],
            3,
        )


if __name__ == "__main__":
    unittest.main()
