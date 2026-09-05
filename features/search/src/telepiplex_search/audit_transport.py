"""Isolated audit transports; simulated fixtures are not live media evidence.

Only provider adapters and Host transport are replaced. Search discovery,
exact-link resolution, hydration, state transitions and release gates stay real.
The patch context is process-local: run audit cases sequentially, never alongside
an installed Feature. No credentials or download URLs are included in reports.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import hashlib
from unittest.mock import patch
from urllib.parse import unquote


# Literal simulated identities/inventories. Q91... identifiers belong to this
# fixture namespace only; they must never be used as verified real-world IDs.
WORKS = (
    ('Q91000001', 'Fargo', '冰血暴', '1996', 'movie', 0),
    ('Q91000002', 'Fargo', '冰血暴', '2014', 'series', 2),
    ('Q91000003', 'Westworld', '西部世界', '1973', 'movie', 0),
    ('Q91000004', 'Westworld', '西部世界', '2016', 'series', 2),
    ('Q91000005', 'Someday or One Day', '想见你', '2019', 'series', 1),
    ('Q91000006', 'Someday or One Day', '想见你', '2022', 'movie', 0),
    ('Q91000007', 'The Office', '办公室', '2005', 'series', 2),
    ('Q91000008', 'Sherlock', '神探夏洛克', '2010', 'series', 2),
)


def audit_config() -> dict:
    return {
        'category_folder': [
            {'kind': kind, 'path': '/audit/' + kind, 'plex_library_id': ''}
            for kind in ('live_action_movie', 'animated_movie',
                         'live_action_series', 'animated_series')
        ],
        'metadata': {
            'wikipedia': {'enable': True, 'languages': ['zh', 'en'], 'timeout': 10},
            'douban': {'enable': False}, 'tmdb': {'enable': False},
            'tvdb': {'enable': False}, 'anilist': {'enable': False},
        },
    }


class AuditRuntime:
    def __init__(self):
        self.tasks = []

    def spawn(self, awaitable, *, task_id):
        task = asyncio.create_task(awaitable, name=task_id)
        self.tasks.append(task)
        return task

    async def drain(self):
        while self.tasks:
            tasks, self.tasks = self.tasks, []
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=45)

    async def close(self, feature):
        tasks = set(self.tasks)
        for stored in feature.plans.values():
            for value in stored.values():
                if isinstance(value, asyncio.Task):
                    tasks.add(value)
                elif isinstance(value, list):
                    tasks.update(item for item in value if isinstance(item, asyncio.Task))
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()


class AuditHost:
    """Capture-only Host. There is no delegate or actual download client."""
    def __init__(self, *, lose_submit_response=False):
        self.submissions = []
        self.reports = []
        self.lose_submit_response = lose_submit_response

    async def call_capability(self, capability, method, payload, **kwargs):
        if (capability, method) != ('download.provider', 'submit'):
            raise AssertionError('audit_forbidden_capability')
        self.submissions.append((deepcopy(payload), deepcopy(kwargs)))
        if self.lose_submit_response:
            raise TimeoutError('simulated_response_loss')
        return {'accepted': True, 'job_id': 'audit-capture-only'}

    async def report_operation(self, operation):
        self.reports.append(deepcopy(operation))
        return {'accepted': True, 'revision': operation['revision'],
                'state': operation['state']}

    async def seal_operation_segment(self, *args, **kwargs):
        return {'accepted': True, 'state': 'sealed'}

    async def publish_operation_milestone(self, *args, **kwargs):
        return {'accepted': True, 'duplicate': False}

    async def seal_operation_stage(self, *args, **kwargs):
        return {'accepted': True, 'duplicate': False}


class FixtureProviders:
    def __init__(self, case, *, scenario='success'):
        self.scenario = scenario
        self.calls = []
        self.query_calls = []
        self.match = next((row for row in WORKS if row[3] == str(case.get('year'))
            and row[4] == case.get('media_type')
            and ({row[1], row[2]} & set(case.get('expected_titles') or ()))), None)
        self.works = [row for row in WORKS if self.match and row[1] == self.match[1]]

    @staticmethod
    def fact(row):
        qid, title, zh, year, kind, seasons = row
        page = f'{title} ({year} {"film" if kind == "movie" else "TV series"})'
        return {
            'wikibase_item': qid, 'external_ids': {'wikidata': qid},
            'title': page, 'canonical_title': page, 'english_title': title,
            'official_english_title': title, 'original_title': title,
            'original_language': 'en', 'chinese_title': zh,
            'aliases': [title, zh], 'media_type': kind, 'year': year,
            'countries': ['Q30'], 'instance_of': ['Q11424' if kind == 'movie' else 'Q5398426'],
            'season_count': seasons or None, 'episode_count': seasons * 3 or None,
            'url': 'https://en.wikipedia.org/wiki/' + page.replace(' ', '_'),
            'language': 'en', 'page_id': int(qid[1:]), 'search_rank': 1,
            'is_disambiguation': False,
        }

    def wikipedia(self, *args, **kwargs):
        self.calls.append('wikipedia.search')
        if self.scenario == 'source_failure':
            return {'source': 'wikipedia', 'status': 'server_down', 'facts': []}
        return {'source': 'wikipedia', 'status': 'ok',
                'facts': [self.fact(row) for row in self.works], 'source_urls': []}

    def wikidata(self, ids):
        self.calls.append('wikidata.exact')
        if self.scenario == 'source_failure':
            return {}
        return {row[0]: self.fact(row) for row in self.works if row[0] in ids}

    def wikidata_search(self, query):
        self.calls.append('wikidata.search')
        return [] if self.scenario == 'source_failure' else [row[0] for row in self.works]

    def page(self, language, title):
        self.calls.append('wikipedia.exact')
        normalized = unquote(title).replace('_', ' ')
        fact = next((self.fact(row) for row in self.works
                     if self.fact(row)['title'] == normalized), None)
        if fact and self.scenario == "wrong_exact_identity":
            fact["wikibase_item"] = "Q91999999"
            fact["external_ids"] = {"wikidata": "Q91999999"}
        return fact

    def episodes(self, language, title):
        fact = self.page(language, title)
        seasons = fact.get('season_count') or 0
        missing = self.scenario == 'missing_inventory'
        return {
            'status': 'absent' if missing else 'complete',
            'items': [] if missing else [
                {'season_number': season, 'episode_number': episode,
                 'air_date': ('2999-01-03' if self.scenario == 'ongoing_season' and (season, episode) == (2, 3)
                              else f'{int(fact["year"]) + season - 1}-01-0{episode}'),
                 'air_date_conflict': self.scenario == 'conflicting_inventory'}
                for season in range(1, seasons + 1) for episode in range(1, 4)
                if not (self.scenario == "missing_episode" and (season, episode) == (2, 3))
            ],
            'season_totals': {} if missing else {season: 3 for season in range(1, seasons + 1)},
            'source_language': language, 'source_url': fact['url'],
            'revision_id': 1, 'episode_list_links': [],
            'error': 'wikipedia_table_absent' if missing else '',
        }

    def releases(self, query, media_type):
        self.calls.append('prowlarr.fixture_query')
        self.query_calls.append(query)
        if self.scenario == 'prowlarr_failure':
            raise TimeoutError('simulated_indexer_timeout')
        titles = []
        for _, title, _, year, kind, seasons in self.works:
            if kind == 'movie':
                titles.append(f'{title}.{year}.1080p.WEB-DL')
            else:
                if self.scenario != 'partial_releases':
                    titles.append(f'{title}.S01-S{seasons:02d}.1080p.WEB-DL')
                titles.extend(f'{title}.S{season:02d}.1080p.WEB-DL'
                              for season in range(1, seasons + 1))
                titles.extend(f'{title}.S{season:02d}E{ep:02d}.1080p.WEB-DL'
                              for season in range(1, seasons + 1) for ep in range(1, 4))
        return [{'title': title, 'magnet_url': 'magnet:?xt=urn:btih:' + hashlib.sha1(title.encode()).hexdigest(),
                 'size': 1024**3, 'seeders': 10, 'indexer': 'audit-fixture'}
                for title in titles]

    @contextmanager
    def active(self):
        disabled = lambda *args, **kwargs: {'source': 'douban', 'status': 'disabled', 'facts': []}
        with ExitStack() as stack:
            for module, name, replacement in (
                ('service', 'lookup_wikipedia_evidence', self.wikipedia),
                ('service', 'enrich_wikidata_entities', self.wikidata),
                ('service', 'search_wikidata_entities', self.wikidata_search),
                ('direct_link', 'enrich_wikidata_entities', self.wikidata),
                ('direct_link', 'lookup_wikipedia_page', self.page),
                ('direct_link', 'lookup_wikipedia_episode_page', self.episodes),
                ('service', 'lookup_douban_evidence', disabled),
                ('service', 'search_tmdb', lambda *a, **kw: []),
                ('service', 'search_tvdb_series', lambda *a, **kw: []),
                ('service', 'search_tvdb_movies', lambda *a, **kw: []),
                ('service', 'search_anilist', lambda *a, **kw: []),
                ('service', 'search_prowlarr', self.releases),
                ('service', 'list_prowlarr_indexers', lambda: []),
                ('service', 'get_prowlarr_indexer_summary', lambda *args, **kwargs: {}),
            ):
                stack.enter_context(patch(f'telepiplex_search.{module}.{name}', replacement))
            yield self


class OfflineNetworkGuard:
    """Fail closed even when a provider catches the transport exception."""
    def __init__(self):
        self.attempts = 0
        self.stack = ExitStack()

    def __enter__(self):
        def reject(*args, **kwargs):
            self.attempts += 1
            raise RuntimeError('offline_network_attempt')
        for target in ('socket.socket.connect', 'socket.socket.connect_ex',
                       'socket.create_connection', 'socket.getaddrinfo'):
            self.stack.enter_context(patch(target, reject))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)
