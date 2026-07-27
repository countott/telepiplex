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
        "anchor_fact_id": "tvdb:77",
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
            "fact_id": "tvdb:77",
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
                "chinese_title": "布达佩斯大饭店",
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

        hydrated = hydrate_frozen_candidate(
            _candidate(),
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
                "fact_id": "tvdb:900",
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


if __name__ == "__main__":
    unittest.main()
