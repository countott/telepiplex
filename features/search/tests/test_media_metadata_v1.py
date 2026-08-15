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
    countries=(),
    episodes=(),
    release_date="",
    runtime=None,
    status="",
    studios=(),
    networks=(),
    cast=(),
    crew=(),
    certifications=(),
    backdrops=(),
    season_count=None,
    episode_count=None,
    episode_inventory=None,
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
        countries=tuple(countries),
        episodes=tuple(episodes),
        original_release_date=release_date,
        runtime_minutes=runtime,
        status=status,
        studios=tuple(studios),
        networks=tuple(networks),
        cast=tuple(cast),
        crew=tuple(crew),
        certifications=tuple(certifications),
        backdrop_urls=tuple(backdrops),
        season_count=season_count,
        episode_count=episode_count,
        episode_inventory=episode_inventory or {},
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
    def test_latin_fallback_never_populates_semantic_chinese_title(self):
        fact = _fact(
            "wikipedia:Q3786532",
            "wikipedia",
            titles=("Honey and Clover", "ハチミツとクローバー"),
            year="2005",
            media_type="series",
            url="https://en.wikipedia.org/wiki/Honey_and_Clover",
            external_ids={"wikidata": "Q3786532"},
            original="ハチミツとクローバー",
            language="ja",
            english="Honey and Clover",
        )
        candidate = AnchoredCandidate(
            candidate_id="wikipedia:Q3786532",
            anchor_fact_id=fact.fact_id,
            identity_role="series_root",
            intended_scope="whole_series",
            source_links=(SourceLink(
                provider=fact.provider,
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
            ai_reason="Exact Wikidata identity.",
            facts=(fact,),
        )

        contract = build_media_metadata_v1(
            candidate,
            metadata_id="honey-latin-only",
            raw_query="Honey and Clover",
        )

        self.assertEqual(contract["identity"]["chinese_title"], "")
        self.assertEqual(contract["identity"]["root_year"], "2005")
        self.assertEqual(contract["identity"]["scope_year"], "")
        self.assertEqual(
            contract["identity"]["english_title"],
            "Honey and Clover",
        )
    def test_wikipedia_inventory_precedes_conflicting_tvdb_default_order(self):
        wikipedia_episodes = tuple(
            {
                "season_number": season,
                "episode_number": episode,
                "overall_number": (season - 1) * 8 + episode,
                "aired": "2024-12-11" if season == 1 else "2026-08-05",
            }
            for season in (1, 2)
            for episode in range(1, 9)
        )
        tvdb_episodes = tuple(
            {
                "tvdb_episode_id": f"tvdb-{episode}",
                "season_number": 1,
                "episode_number": episode,
                "aired": "2024-12-11" if episode < 9 else "2026-08-05",
            }
            for episode in range(1, 17)
        )
        wikipedia = _fact(
            "wikipedia:Q124175370",
            "wikipedia",
            titles=("百年孤寂", "One Hundred Years of Solitude"),
            year="2024",
            media_type="series",
            url="https://en.wikipedia.org/wiki/One_Hundred_Years_of_Solitude_(TV_series)",
            external_ids={"wikidata": "Q124175370"},
            english="One Hundred Years of Solitude",
            original="Cien años de soledad",
            language="es",
            episodes=wikipedia_episodes,
        )
        tvdb = _fact(
            "tvdb:426288",
            "tvdb",
            titles=("One Hundred Years of Solitude",),
            year="2024",
            media_type="series",
            url="https://thetvdb.com/series/426288",
            external_ids={"tvdb": "426288"},
            english="One Hundred Years of Solitude",
            original="Cien años de soledad",
            language="es",
            episodes=tvdb_episodes,
        )
        candidate = AnchoredCandidate(
            candidate_id="wikipedia:Q124175370",
            anchor_fact_id=wikipedia.fact_id,
            identity_role="series_root",
            intended_scope="whole_series",
            source_links=(
                SourceLink(
                    provider="wikipedia", fact_id=wikipedia.fact_id,
                    url=wikipedia.source_url, external_ids=wikipedia.external_ids,
                    role="series_root", season_number=None, episode_number=None,
                    verification="fact_verified",
                ),
                SourceLink(
                    provider="tvdb", fact_id=tvdb.fact_id,
                    url=tvdb.source_url, external_ids=tvdb.external_ids,
                    role="series_root", season_number=None, episode_number=None,
                    verification="fact_verified",
                ),
            ),
            poster_assets=(),
            unresolved_sources=(),
            ai_confidence=1,
            ai_reason="Wikipedia identity with downstream TVDB metadata.",
            facts=(wikipedia, tvdb),
        )

        contract = build_media_metadata_v1(
            candidate,
            metadata_id="one-hundred-years",
            raw_query="百年孤独",
        )

        self.assertEqual(
            sorted({item["season_number"] for item in contract["items"]}),
            [1, 2],
        )
        self.assertTrue(all(
            item["inventory_source"] == "wikipedia"
            for item in contract["items"]
        ))
        self.assertEqual(
            contract["evidence"]["series_inventory"]["source"],
            "wikipedia",
        )
        self.assertEqual(
            contract["evidence"]["series_inventory"]["season_totals"],
            {1: 8, 2: 8},
        )

    def test_wikipedia_parse_error_remains_visible_after_tvdb_fallback(self):
        wikipedia = _fact(
            "wikipedia:Q1",
            "wikipedia",
            titles=("Example",),
            year="2024",
            media_type="series",
            url="https://en.wikipedia.org/wiki/Example",
            external_ids={"wikidata": "Q1"},
            english="Example",
            episode_inventory={
                "status": "parse_error",
                "season_totals": {},
                "source_revisions": {"en": 123},
                "error": "wikipedia_parse_error",
            },
        )
        tvdb = _fact(
            "tvdb:1",
            "tvdb",
            titles=("Example",),
            year="2024",
            media_type="series",
            url="https://thetvdb.com/series/1",
            external_ids={"tvdb": "1"},
            english="Example",
            episodes=({
                "tvdb_episode_id": "1-1",
                "season_number": 1,
                "episode_number": 1,
                "aired": "2024-01-01",
            },),
        )
        candidate = _candidate(
            intended_scope="whole_series",
            facts=(wikipedia, tvdb),
        )

        contract = build_media_metadata_v1(
            candidate,
            metadata_id="example",
            raw_query="Example",
        )

        inventory = contract["evidence"]["series_inventory"]
        self.assertEqual(inventory["source"], "tvdb")
        self.assertEqual(inventory["wikipedia_status"], "parse_error")
        self.assertEqual(inventory["fallback_reason"], "wikipedia_parse_error")
        self.assertIn(
            "warning:wikipedia_episode_parse_error",
            contract["warnings"],
        )

    def test_tvdb_tmdb_fallback_merges_only_consistent_coordinates(self):
        wikipedia = _fact(
            "wikipedia:Q2",
            "wikipedia",
            titles=("Fallback",),
            year="2024",
            media_type="series",
            url="https://en.wikipedia.org/wiki/Fallback",
            external_ids={"wikidata": "Q2"},
            english="Fallback",
            episode_inventory={
                "status": "absent",
                "error": "wikipedia_table_absent",
            },
        )
        tvdb = _fact(
            "tvdb:2",
            "tvdb",
            titles=("Fallback",),
            year="2024",
            media_type="series",
            url="https://thetvdb.com/series/2",
            external_ids={"tvdb": "2"},
            english="Fallback",
            episodes=({
                "tvdb_episode_id": "tvdb-1",
                "season_number": 1,
                "episode_number": 1,
                "aired": "2024-01-01",
            },),
        )
        tmdb = _fact(
            "tmdb:2",
            "tmdb",
            titles=("Fallback",),
            year="2024",
            media_type="series",
            url="https://www.themoviedb.org/tv/2",
            external_ids={"tmdb": "2"},
            english="Fallback",
            episodes=({
                "tmdb_episode_id": "tmdb-1",
                "season_number": 1,
                "episode_number": 1,
                "air_date": "2024-01-01",
            },),
        )

        contract = build_media_metadata_v1(
            _candidate(
                intended_scope="whole_series",
                facts=(wikipedia, tvdb, tmdb),
            ),
            metadata_id="fallback",
            raw_query="Fallback",
        )

        self.assertEqual(len(contract["items"]), 1)
        item = contract["items"][0]
        self.assertEqual(item["inventory_source"], "tvdb_tmdb")
        self.assertEqual(item["tvdb_episode_id"], "tvdb-1")
        self.assertEqual(item["tmdb_episode_id"], "tmdb-1")

    def test_tvdb_tmdb_fallback_date_conflict_becomes_unknown(self):
        facts = []
        for provider, aired in (("tvdb", "2024-01-01"), ("tmdb", "2024-01-02")):
            facts.append(_fact(
                f"{provider}:3",
                provider,
                titles=("Conflict",),
                year="2024",
                media_type="series",
                url=f"https://example.com/{provider}/3",
                external_ids={provider: "3"},
                english="Conflict",
                episodes=({
                    f"{provider}_episode_id": f"{provider}-1",
                    "season_number": 1,
                    "episode_number": 1,
                    "aired": aired,
                },),
            ))

        contract = build_media_metadata_v1(
            _candidate(intended_scope="whole_series", facts=tuple(facts)),
            metadata_id="conflict",
            raw_query="Conflict",
        )

        self.assertEqual(contract["items"][0]["aired"], "")
        self.assertTrue(contract["items"][0]["air_date_conflict"])
        self.assertEqual(
            contract["evidence"]["series_inventory"]["status"],
            "conflict",
        )

    def test_divergent_provider_orders_select_a_complete_profile_not_intersection(self):
        wikipedia = _fact(
            "wikipedia:Q5362638",
            "wikipedia",
            titles=("死神", "Bleach"),
            year="2004",
            media_type="series",
            url="https://en.wikipedia.org/wiki/Bleach_(TV_series)",
            external_ids={"wikidata": "Q5362638"},
            english="Bleach",
            episode_count=406,
            episode_inventory={
                "status": "absent",
                "error": "wikipedia_table_absent",
            },
        )
        tvdb = _fact(
            "tvdb:74796",
            "tvdb",
            titles=("Bleach",),
            year="2004",
            media_type="series",
            url="https://thetvdb.com/series/74796",
            external_ids={"tvdb": "74796"},
            english="Bleach",
            episodes=tuple(
                {
                    "season_number": season,
                    "episode_number": episode,
                    "aired": "2004-10-05",
                    "tvdb_episode_id": f"tvdb-{season}-{episode}",
                }
                for season, total in ((1, 20), (2, 21), (3, 22))
                for episode in range(1, total + 1)
            ),
        )
        tmdb = _fact(
            "tmdb:30984",
            "tmdb",
            titles=("Bleach",),
            year="2004",
            media_type="series",
            url="https://www.themoviedb.org/tv/30984",
            external_ids={"tmdb": "30984"},
            english="Bleach",
            episodes=tuple(
                {
                    "season_number": season,
                    "episode_number": episode,
                    "air_date": "2004-10-05",
                    "tmdb_episode_id": f"tmdb-{season}-{episode}",
                }
                for season, total in ((1, 366), (2, 40))
                for episode in range(1, total + 1)
            ),
        )

        contract = build_media_metadata_v1(
            _candidate(
                intended_scope="whole_series",
                facts=(wikipedia, tvdb, tmdb),
            ),
            metadata_id="bleach",
            raw_query="死神",
        )

        self.assertEqual(len(contract["items"]), 406)
        self.assertEqual(
            contract["evidence"]["series_inventory"]["source"],
            "tmdb",
        )
        self.assertEqual(
            contract["evidence"]["series_inventory"]["season_totals"],
            {1: 366, 2: 40},
        )

    def test_contract_preserves_anchor_country_for_candidate_presentation(self):
        fact = _fact(
            "douban:35981510",
            "douban",
            titles=("繁花", "Blossoms Shanghai"),
            year="2023",
            media_type="series",
            url="https://movie.douban.com/subject/35981510/",
            external_ids={"douban_subject": "35981510"},
            poster="https://img.example/blossoms.jpg",
            chinese="繁花",
            english="Blossoms Shanghai",
            countries=("中国大陆",),
        )

        contract = build_media_metadata_v1(
            _candidate(
                intended_scope="whole_series",
                facts=(fact,),
                unresolved=("tvdb:unavailable",),
            ),
            metadata_id="blossoms",
            raw_query="繁花 2023",
        )

        self.assertEqual(contract["identity"]["countries"], ["中国大陆"])

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

    def test_bare_series_keeps_work_scope_until_user_selects_download_range(self):
        fact = _fact(
            "tvdb:79044",
            "tvdb",
            titles=("蜂蜜与四叶草", "Honey and Clover"),
            year="2005",
            media_type="series",
            url="https://thetvdb.com/series/79044",
            external_ids={"tvdb": "79044"},
            chinese="蜂蜜与四叶草",
            english="Honey and Clover",
            episodes=({
                "tvdb_episode_id": "79044-s1e1",
                "season_number": 1,
                "episode_number": 1,
                "aired": "2005-04-15",
            },),
        )
        candidate = AnchoredCandidate(
            candidate_id="tvdb:79044",
            anchor_fact_id=fact.fact_id,
            identity_role="series_root",
            intended_scope="work",
            source_links=(SourceLink(
                provider="tvdb",
                fact_id=fact.fact_id,
                url=fact.source_url,
                external_ids=fact.external_ids,
                role="series_root",
                season_number=None,
                episode_number=None,
                verification="fact_verified",
            ),),
            poster_assets=(),
            unresolved_sources=(),
            ai_confidence=0,
            ai_reason="deterministic_tvdb_root",
            facts=(fact,),
        )

        contract = build_media_metadata_v1(
            candidate,
            metadata_id="bare-series",
            raw_query="蜂蜜与四叶草",
        )

        self.assertEqual(contract["retrieval"]["scope"], "work")
        self.assertEqual(contract["evidence"]["decision"]["scope"], "work")

    def test_contract_converges_peer_descriptive_metadata_with_field_evidence(self):
        douban = _fact(
            "douban:1295644",
            "douban",
            titles=("康斯坦丁", "Constantine"),
            year="2005",
            url="https://movie.douban.com/subject/1295644/",
            external_ids={"douban_subject": "1295644"},
            chinese="康斯坦丁",
            original="Constantine",
            english="Constantine",
            genres=("Action",),
            summary="A detective story.",
        )
        tmdb = _fact(
            "tmdb:561",
            "tmdb",
            titles=("Constantine",),
            year="2005",
            url="https://www.themoviedb.org/movie/561",
            external_ids={"tmdb": "561", "imdb": "tt0360486"},
            original="Constantine",
            english="Constantine",
            release_date="2005-02-08",
            runtime=121,
            status="Released",
            studios=("Warner Bros. Pictures",),
            cast=({"name": "Keanu Reeves", "character": "John Constantine"},),
            crew=({"name": "Francis Lawrence", "job": "Director"},),
            certifications=("R",),
            backdrops=("https://image.tmdb.org/backdrop.jpg",),
            summary=(
                "John Constantine investigates a supernatural mystery "
                "that threatens the human world."
            ),
        )

        contract = build_media_metadata_v1(
            _candidate(facts=(douban, tmdb)),
            metadata_id="constantine",
            raw_query="康斯坦丁",
        )

        identity = contract["identity"]
        self.assertEqual(identity["runtime_minutes"], 121)
        self.assertEqual(
            identity["summary"],
            "John Constantine investigates a supernatural mystery that threatens the human world.",
        )
        self.assertEqual(identity["original_release_date"], "2005-02-08")
        self.assertEqual(identity["studios"], ["Warner Bros. Pictures"])
        self.assertEqual(identity["cast"][0]["name"], "Keanu Reeves")
        self.assertEqual(identity["external_ids"]["tmdb"], "561")
        self.assertEqual(
            set(identity["query_titles"]),
            {"Constantine"},
        )
        resolution = contract["evidence"]["field_resolutions"][
            "official_english_title"
        ]
        self.assertFalse(resolution["conflict"])
        self.assertEqual(
            {item["provider"] for item in resolution["sources"]},
            {"douban", "tmdb"},
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
            language="zh",
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

    def test_contract_keeps_sources_and_uses_deterministic_decision(self):
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
        self.assertNotIn("ai", evidence)
        self.assertEqual(
            evidence["decision"]["mode"],
            "deterministic_fact_binding",
        )
        self.assertEqual(
            contract["identity"]["root_fact_id"],
            "douban:1",
        )
        self.assertIn("wikipedia:not_found", evidence["unresolved"])
        self.assertIn("warning:source_unresolved", contract["warnings"])

    def test_poster_priority_is_tmdb_then_douban_then_wikipedia_and_ignores_anilist(self):
        facts = (
            _fact(
                "wikipedia:Q1",
                "wikipedia",
                titles=("Spirited Away",),
                year="2001",
                url="https://en.wikipedia.org/wiki/Spirited_Away",
                external_ids={"wikipedia": "Q1"},
                poster="https://art.example/wikipedia.jpg",
                english="Spirited Away",
            ),
            _fact(
                "douban:1",
                "douban",
                titles=("千与千寻", "Spirited Away"),
                year="2001",
                url="https://movie.douban.com/subject/1/",
                external_ids={"douban_subject": "1"},
                poster="https://art.example/douban.jpg",
                chinese="千与千寻",
                english="Spirited Away",
            ),
            _fact(
                "tmdb:129",
                "tmdb",
                titles=("Spirited Away",),
                year="2001",
                url="https://www.themoviedb.org/movie/129",
                external_ids={"tmdb": "129"},
                poster="https://art.example/tmdb.jpg",
                english="Spirited Away",
            ),
            _fact(
                "anilist:199",
                "anilist",
                titles=("Sen to Chihiro no Kamikakushi",),
                year="2001",
                url="https://anilist.co/anime/199",
                external_ids={"anilist": "199"},
                poster="https://art.example/anilist.jpg",
                romanized="Sen to Chihiro no Kamikakushi",
                english="Spirited Away",
            ),
        )

        contract = build_media_metadata_v1(
            _candidate(facts=facts),
            metadata_id="poster-priority",
            raw_query="千与千寻",
        )

        self.assertEqual(
            contract["identity"]["poster_url"],
            "https://art.example/tmdb.jpg",
        )
        self.assertEqual(contract["identity"]["poster_source"], "tmdb")

    def test_movie_query_uses_bounded_verified_titles_with_year(self):
        contract = build_media_metadata_v1(
            _candidate(),
            metadata_id="m2",
            raw_query="千与千寻",
        )

        queries = build_prowlarr_query_chain(contract, "千与千寻")

        self.assertEqual(
            queries,
            [
                "Sen to Chihiro no Kamikakushi 2001",
                "Spirited Away 2001",
            ],
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
            [
                "Hachimitsu to Clover S02",
                "Honey and Clover S02",
                "Hachimitsu to Clover Season 02",
            ],
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
            [
                "Sen to Chihiro no Kamikakushi 2001",
                "Spirited Away 2001",
            ],
        )

    def test_verified_veep_season_uses_root_title_query_variants(self):
        episodes = ({
            "season_number": 1,
            "episode_number": 1,
            "aired": "2012-04-22",
        },)
        douban = _fact(
            "douban:5379824",
            "douban",
            titles=("副总统 第一季", "Veep Season 1"),
            year="2012",
            media_type="series",
            url="https://movie.douban.com/subject/5379824/",
            external_ids={"douban_subject": "5379824"},
            chinese="副总统 第一季",
            original="Veep Season 1",
            language="en",
            english="Veep Season 1",
        )
        tvdb = _fact(
            "tvdb:series:75978",
            "tvdb",
            titles=("Veep",),
            year="2012",
            media_type="series",
            url="https://thetvdb.com/series/veep",
            external_ids={"tvdb": "75978"},
            original="Veep",
            language="en",
            english="Veep",
            episodes=episodes,
        )
        candidate = AnchoredCandidate(
            candidate_id="douban:5379824",
            anchor_fact_id=douban.fact_id,
            identity_role="season",
            intended_scope="season",
            source_links=(
                SourceLink(
                    provider="douban",
                    fact_id=douban.fact_id,
                    url=douban.source_url,
                    external_ids=douban.external_ids,
                    role="season",
                    season_number=1,
                    episode_number=None,
                    verification="tvdb_inventory_verified",
                ),
                SourceLink(
                    provider="tvdb",
                    fact_id=tvdb.fact_id,
                    url=tvdb.source_url,
                    external_ids=tvdb.external_ids,
                    role="series_root",
                    season_number=None,
                    episode_number=None,
                    verification="fact_verified",
                ),
            ),
            poster_assets=(),
            unresolved_sources=(),
            ai_confidence=1,
            ai_reason="Douban season verified against TVDB inventory.",
            facts=(douban, tvdb),
        )

        contract = build_media_metadata_v1(
            candidate,
            metadata_id="veep-season-1",
            raw_query="veep",
        )

        self.assertEqual(contract["identity"]["english_title"], "Veep")
        self.assertEqual(contract["retrieval"]["scope"], "season")
        self.assertEqual(contract["evidence"]["decision"]["season_number"], 1)
        self.assertEqual(
            build_prowlarr_query_chain(contract, "veep"),
            ["Veep S01", "Veep Season 01"],
        )

    def test_query_chain_is_deduplicated_and_capped_at_three(self):
        contract = build_media_metadata_v1(
            _candidate(),
            metadata_id="bounded-chain",
            raw_query="千与千寻",
        )
        contract["identity"]["query_titles"] = [
            "Sen to Chihiro no Kamikakushi",
            "Spirited Away",
            "Sen to Chihiro no Kamikakushi",
            "Le Voyage de Chihiro",
            "Chihiros Reise ins Zauberland",
        ]

        self.assertEqual(
            build_prowlarr_query_chain(contract, "ignored raw noise"),
            [
                "Sen to Chihiro no Kamikakushi 2001",
                "Spirited Away 2001",
                "Le Voyage de Chihiro 2001",
            ],
        )

    def test_foreign_work_without_latin_title_never_queries_chinese_only(self):
        contract = {
            "identity": {
                "canonical_search_title": "龙之家族",
                "official_english_title": "",
                "english_title": "",
                "query_titles": ["龙之家族"],
                "original_language": "en",
                "year": "2022",
            },
            "retrieval": {"scope": "season", "media_type": "series"},
            "evidence": {"decision": {
                "scope": "season",
                "season_number": 3,
                "episode_number": None,
            }},
        }

        with self.assertRaisesRegex(ValueError, "foreign_search_title_missing"):
            build_prowlarr_query_chain(contract, "龙之家族 第三季")

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
