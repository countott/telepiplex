import unittest

from telepiplex_search.anchored_candidate import (
    AnchoredCandidate,
    PosterAsset,
    SourceLink,
)
from telepiplex_search.entity_graph import EvidenceFact
from telepiplex_search.media_metadata_v1 import (
    MetadataV1Error,
    build_media_metadata_v1,
)
from telepiplex_search.prowlarr_query import build_prowlarr_query_chain


def _fact(
    fact_id,
    provider,
    *,
    titles,
    year="2024",
    media_type="movie",
    url,
    external_ids,
    poster="",
    chinese="",
    original="",
    language="en",
    english="",
    romanized="",
    summary="",
    genres=(),
    episodes=(),
):
    return EvidenceFact(
        fact_id=fact_id,
        provider=provider,
        titles=tuple(titles),
        year=year,
        media_type=media_type,
        external_ids=external_ids,
        source_url=url,
        poster_url=poster,
        chinese_title=chinese,
        original_title=original,
        original_language=language,
        official_english_title=english,
        romanized_original_title=romanized,
        summary=summary,
        genres=tuple(genres),
        episodes=tuple(episodes),
    )


def _candidate(*, intended_scope="movie", facts=None, unresolved=()):
    facts = facts or (
        _fact(
            "douban:1",
            "douban",
            titles=("千与千寻", "Spirited Away"),
            year="2001",
            url="https://movie.douban.com/subject/1/",
            external_ids={"douban_subject": "1"},
            poster="https://art.example/douban.jpg",
            chinese="千与千寻",
            original="千と千尋の神隠し",
            language="ja",
            english="Spirited Away",
            romanized="Sen to Chihiro no Kamikakushi",
            genres=("Animation",),
        ),
        _fact(
            "tvdb:2",
            "tvdb",
            titles=("Spirited Away", "Sen to Chihiro no Kamikakushi"),
            year="2001",
            url="https://thetvdb.com/movies/2",
            external_ids={"tvdb": "2"},
            poster="https://art.example/tvdb.jpg",
            original="千と千尋の神隠し",
            language="ja",
            english="Spirited Away",
            romanized="Sen to Chihiro no Kamikakushi",
            genres=("Animation",),
        ),
    )
    links = tuple(
        SourceLink(
            provider=fact.provider,
            fact_id=fact.fact_id,
            url=fact.source_url,
            external_ids=fact.external_ids,
            role="movie",
            season_number=None,
            episode_number=None,
            verification="fact_verified",
        )
        for fact in facts
    )
    posters = tuple(
        PosterAsset(
            provider=fact.provider,
            fact_id=fact.fact_id,
            url=fact.poster_url,
            role="movie",
            season_number=None,
            language=fact.poster_language,
        )
        for fact in facts
        if fact.poster_url
    )
    return AnchoredCandidate(
        candidate_id="candidate-1",
        anchor_fact_id=facts[0].fact_id,
        identity_role="movie",
        intended_scope=intended_scope,
        source_links=links,
        poster_assets=posters,
        unresolved_sources=tuple(unresolved),
        ai_confidence=0.94,
        ai_reason="Verified sources describe the same film.",
        facts=tuple(facts),
    )


class MediaMetadataV1Test(unittest.TestCase):
    def test_contract_returns_source_overview_in_confirmed_identity(self):
        douban = _fact(
            "douban:36235977",
            "douban",
            titles=("后室", "Backrooms"),
            year="2026",
            url="https://movie.douban.com/subject/36235977/",
            external_ids={"douban_subject": "36235977"},
            chinese="后室",
            original="Backrooms",
            language="en",
            english="Backrooms",
        )
        tvdb = _fact(
            "tvdb:movie:363177",
            "tvdb",
            titles=("Backrooms",),
            year="2026",
            url="https://thetvdb.com/movies/363177",
            external_ids={"tvdb": "363177"},
            original="Backrooms",
            language="en",
            english="Backrooms",
            summary="A young filmmaker encounters the unsettling Backrooms.",
        )

        contract = build_media_metadata_v1(
            _candidate(facts=(douban, tvdb)),
            metadata_id="backrooms",
            raw_query="后室",
        )

        self.assertEqual(
            contract["identity"]["summary"],
            "A young filmmaker encounters the unsettling Backrooms.",
        )

    def test_tvdb_unavailable_series_degrades_to_whole_series(self):
        fact = _fact(
            "douban:20",
            "douban",
            titles=("繁花",),
            year="2023",
            media_type="series",
            url="https://movie.douban.com/subject/20/",
            external_ids={"douban_subject": "20"},
            chinese="繁花",
            english="",
        )
        candidate = AnchoredCandidate(
            candidate_id="douban:20",
            anchor_fact_id=fact.fact_id,
            identity_role="series_root",
            intended_scope="whole_series",
            source_links=(SourceLink(
                provider="douban",
                fact_id=fact.fact_id,
                url=fact.source_url,
                external_ids=fact.external_ids,
                role="series_root",
                season_number=None,
                episode_number=None,
                verification="fact_verified",
            ),),
            poster_assets=(),
            unresolved_sources=("tvdb:unavailable",),
            ai_confidence=1,
            ai_reason="confirmed_douban_identity",
            facts=(fact,),
        )

        contract = build_media_metadata_v1(
            candidate,
            metadata_id="degraded-series",
            raw_query="繁花 第一季",
        )

        self.assertEqual(contract["retrieval"]["scope"], "whole_series")
        self.assertEqual(contract["items"], [])
        self.assertNotIn("tvdb", contract["identity"]["external_ids"])
        self.assertEqual(contract["retrieval"]["query"], "繁花")
        self.assertIn(
            "warning:tvdb_inventory_unavailable",
            contract["warnings"],
        )

    def test_contract_keeps_every_link_poster_field_source_and_ai_decision(self):
        contract = build_media_metadata_v1(
            _candidate(unresolved=("wikipedia:not_found",)),
            metadata_id="m1",
            raw_query="千与千寻",
        )

        evidence = contract["evidence"]
        self.assertEqual(
            {item["provider"] for item in evidence["source_links"]},
            {"douban", "tvdb"},
        )
        self.assertEqual(len(evidence["poster_assets"]), 2)
        self.assertEqual(
            evidence["ai"]["reason"],
            "Verified sources describe the same film.",
        )
        self.assertEqual(
            contract["identity"]["root_fact_id"],
            "douban:1",
        )
        self.assertIn("wikipedia:not_found", evidence["unresolved"])
        self.assertIn("warning:source_unresolved", contract["warnings"])

    def test_movie_query_uses_one_verified_canonical_title_with_year(self):
        contract = build_media_metadata_v1(
            _candidate(),
            metadata_id="m2",
            raw_query="千与千寻",
        )

        queries = build_prowlarr_query_chain(contract, "千与千寻")

        self.assertEqual(
            queries,
            ["Sen to Chihiro no Kamikakushi 2001"],
        )
        self.assertEqual(contract["retrieval"]["queries"], queries)

    def test_external_id_records_keep_multiple_same_provider_links(self):
        candidate = _candidate()
        extra_link = SourceLink(
            provider="douban",
            fact_id="douban:season-2",
            url="https://movie.douban.com/subject/season-2/",
            external_ids={"douban_subject": "season-2"},
            role="season",
            season_number=None,
            episode_number=None,
            verification="unresolved_scope_link",
            proposed_season_number=2,
        )
        candidate = AnchoredCandidate(
            **{
                **candidate.__dict__,
                "source_links": (*candidate.source_links, extra_link),
            }
        )

        contract = build_media_metadata_v1(
            candidate,
            metadata_id="multiple-provider-ids",
            raw_query="千与千寻",
        )

        douban_records = [
            record
            for record in contract["identity"]["external_id_records"]
            if record["provider"] == "douban"
        ]
        self.assertEqual(
            [record["external_ids"]["douban_subject"] for record in douban_records],
            ["1", "season-2"],
        )
        self.assertEqual(
            douban_records[1]["proposed_season_number"],
            2,
        )

    def test_series_season_scope_uses_only_verified_v1_scope_numbers(self):
        episodes = (
            {
                "tvdb_episode_id": "e1",
                "season_number": 2,
                "episode_number": 1,
            },
        )
        fact = _fact(
            "tvdb:20",
            "tvdb",
            titles=("Honey and Clover", "蜂蜜与四叶草"),
            year="2005",
            media_type="series",
            url="https://thetvdb.com/series/20",
            external_ids={"tvdb": "20"},
            chinese="蜂蜜与四叶草",
            original="ハチミツとクローバー",
            language="ja",
            english="Honey and Clover",
            romanized="Hachimitsu to Clover",
            genres=("Anime",),
            episodes=episodes,
        )
        candidate = AnchoredCandidate(
            candidate_id="candidate-series",
            anchor_fact_id=fact.fact_id,
            identity_role="season",
            intended_scope="season",
            source_links=(SourceLink(
                provider="tvdb",
                fact_id=fact.fact_id,
                url=fact.source_url,
                external_ids=fact.external_ids,
                role="season",
                season_number=2,
                episode_number=None,
                verification="tvdb_inventory_verified",
            ),),
            poster_assets=(),
            unresolved_sources=(),
            ai_confidence=1,
            ai_reason="The link is TVDB season 2.",
            facts=(fact,),
        )

        contract = build_media_metadata_v1(
            candidate,
            metadata_id="m3",
            raw_query="蜂蜜与四叶草 第二季",
        )

        self.assertEqual(contract["evidence"]["decision"]["season_number"], 2)
        self.assertEqual(
            build_prowlarr_query_chain(contract, "蜂蜜与四叶草 第二季"),
            ["Hachimitsu to Clover S02"],
        )

    def test_query_ignores_unverified_aliases_and_raw_query_noise(self):
        contract = build_media_metadata_v1(
            _candidate(),
            metadata_id="clean-query",
            raw_query="千与千寻 4K 国语 导演剪辑版",
        )
        contract["identity"]["aliases"] = [
            "El viaje de Chihiro",
            "Sen to Chihiro no Kamikakushi Extended",
        ]

        self.assertEqual(
            build_prowlarr_query_chain(
                contract,
                "千与千寻 4K 国语 导演剪辑版",
            ),
            ["Sen to Chihiro no Kamikakushi 2001"],
        )

    def test_media_type_conflict_and_incomplete_scope_fail_explicitly(self):
        movie = _candidate().facts[0]
        series = _fact(
            "tvdb:series",
            "tvdb",
            titles=("Wrong series",),
            media_type="series",
            url="https://thetvdb.com/series/series",
            external_ids={"tvdb": "series"},
            english="Wrong series",
        )
        conflicting = _candidate(facts=(movie, series))
        with self.assertRaisesRegex(MetadataV1Error, "metadata_conflict"):
            build_media_metadata_v1(
                conflicting,
                metadata_id="conflict",
                raw_query="title",
            )

        broken = AnchoredCandidate(
            **{
                **_candidate().__dict__,
                "unresolved_sources": ("tvdb:unresolved_scope_link",),
                "intended_scope": "season",
            }
        )
        with self.assertRaisesRegex(MetadataV1Error, "metadata_incomplete"):
            build_media_metadata_v1(
                broken,
                metadata_id="incomplete",
                raw_query="title season 2",
            )


if __name__ == "__main__":
    unittest.main()
