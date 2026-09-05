"""Local controlled provider latency and immutable locale-budget regressions."""
import asyncio
from copy import deepcopy
import threading
import unittest
from unittest.mock import patch
import pytest

from telepiplex_search.errors import SearchPlanningError
from telepiplex_search.input_contract import classify_search_input
from telepiplex_search.work_discovery import discover_root_works
from telepiplex_search.work_discovery import build_root_work_search_plan
from telepiplex_search.service import SearchFeature


def locale_plan():
    entity = deepcopy(ENTITY)
    entity['external_ids']['douban_subject'] = '100'
    return build_root_work_search_plan('示例', 'locale-plan',
        lambda _: deepcopy(WIKIPEDIA), lambda _: {'Q1': entity},
        wikidata_search=lambda _: ['Q1'])


LOCALE = {'subject_id': '100', 'chinese_title': '本地化示例',
          'media_type': 'movie', 'external_ids': {'douban_subject': '100'}}


class LocaleBudgetTest(unittest.IsolatedAsyncioTestCase):
    async def test_fake_clock_budget_returns_original_and_discards_late_binding(self):
        feature = SearchFeature(config={}, host=None)
        feature.candidate_locale_timeout = 2
        plan = locale_plan()
        frozen = deepcopy(plan)
        release = threading.Event()
        started = asyncio.Event()
        loop = asyncio.get_running_loop()
        clock = [loop.time()]

        def lookup(subject):
            loop.call_soon_threadsafe(started.set)
            release.wait(2)
            return deepcopy(LOCALE)

        with patch('telepiplex_search.service.lookup_douban_subject', lookup):
            with patch.object(loop, 'time', lambda: clock[0]):
                pending = asyncio.create_task(feature._localize_exact_douban_candidates(
                    plan, plan_id='locale-plan'))
                try:
                    await started.wait()
                    clock[0] += 2.01
                    for _ in range(20):
                        await asyncio.sleep(0)
                    self.assertTrue(pending.done(), 'locale budget must finish without provider completion')
                    result = pending.result()
                    self.assertEqual(result, frozen)
                    self.assertFalse(release.is_set(), 'underlying I/O has not completed')
                finally:
                    release.set()
                    await pending
            # Scheduler owns the shielded read. Wait for real completion, and
            # prove no old worker can mutate the returned or source plans.
            while feature.source_scheduler.in_flight_count:
                await asyncio.sleep(.001)
            self.assertEqual(plan, frozen)
            self.assertEqual(result, frozen)

    async def test_revision_change_discards_in_progress_locale_transaction(self):
        feature = SearchFeature(config={}, host=None)
        plan = locale_plan()
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()
        def lookup(subject):
            loop.call_soon_threadsafe(started.set)
            release.wait(2)
            return deepcopy(LOCALE)
        with patch('telepiplex_search.service.lookup_douban_subject', lookup):
            pending = asyncio.create_task(feature._localize_exact_douban_candidates(plan, plan_id='locale-plan'))
            await started.wait()
            plan['display_revision'] = 2
            plan['candidates'][0]['media_metadata']['retrieval']['scope'] = 'episode'
            expected = deepcopy(plan)
            release.set()
            result = await pending
        self.assertEqual(result, expected)

    async def test_normal_and_conflicting_locale_preserve_root_scope_and_order(self):
        for fact, title in ((LOCALE, '本地化示例'), ({**LOCALE, 'subject_id': 'wrong'}, '示例')):
            with self.subTest(title=title):
                plan = locale_plan()
                frozen = deepcopy(plan)
                feature = SearchFeature(config={}, host=None)
                with patch('telepiplex_search.service.lookup_douban_subject', return_value=fact):
                    result = await feature._localize_exact_douban_candidates(plan, plan_id='locale-plan')
                candidate = result['candidates'][0]
                self.assertEqual(candidate['candidate_id'], 'wikipedia:Q1')
                self.assertEqual(candidate['media_metadata']['identity']['chinese_title'], title)
                self.assertEqual(candidate['media_metadata']['retrieval'], frozen['candidates'][0]['media_metadata']['retrieval'])
                self.assertEqual(plan, frozen)

    async def test_cancelling_locale_waiter_does_not_cancel_shared_read_or_publish(self):
        feature = SearchFeature(config={}, host=None)
        plan = locale_plan()
        frozen = deepcopy(plan)
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()
        calls = []
        def lookup(subject):
            calls.append(subject)
            loop.call_soon_threadsafe(started.set)
            release.wait(2)
            return deepcopy(LOCALE)
        with patch('telepiplex_search.service.lookup_douban_subject', lookup):
            first = asyncio.create_task(feature._localize_exact_douban_candidates(plan, plan_id='first'))
            await started.wait()
            second = asyncio.create_task(feature._localize_exact_douban_candidates(deepcopy(plan), plan_id='second'))
            for _ in range(8):
                await asyncio.sleep(0)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertEqual(feature.source_scheduler.in_flight_count, 1)
            release.set()
            result = await second
        self.assertEqual(calls, ['100'])
        self.assertEqual(result['candidates'][0]['media_metadata']['identity']['chinese_title'], '本地化示例')
        self.assertEqual(plan, frozen)


@pytest.mark.parametrize('scenario', ['success', 'cancel', 'restart', 'wrong_exact_identity'])
def test_locale_budget_with_real_page_confirmation_cancel_and_restart(scenario):
    """A late external read must not change actual Search callback contracts."""
    from telepiplex_search.audit_transport import AuditHost, AuditRuntime, FixtureProviders, audit_config
    from telepiplex_search.context import runtime_context
    from telepiplex_plugin_sdk.media_metadata_v2 import validate_media_metadata_v2

    async def run():
        case = {'query': 'Fargo', 'expected_titles': ['Fargo'], 'year': '2014', 'media_type': 'series'}
        providers = FixtureProviders(case, scenario=scenario)
        original_fact = providers.fact
        providers.fact = lambda row: {
            **original_fact(row), 'cover_url': 'https://fixtures.invalid/poster.jpg',
        }
        # Six literal works exercise the second real candidate page. The extra
        # entries remain provider fixtures; no planning/confirmation is stubbed.
        providers.works += [
            ('Q91000011', 'Fargo', '冰血暴', '2021', 'movie', 0),
            ('Q91000012', 'Fargo', '冰血暴', '2022', 'movie', 0),
            ('Q91000013', 'Fargo', '冰血暴', '2023', 'movie', 0),
            ('Q91000014', 'Fargo', '冰血暴', '2024', 'movie', 0),
        ]
        config = audit_config()
        config['metadata']['douban']['enable'] = True
        host, runtime = AuditHost(), AuditRuntime()
        gate = threading.Event()
        started = threading.Event()
        completed = threading.Event()
        def locale(queries, **kwargs):
            started.set()
            gate.wait(5)
            completed.set()
            return {'status': 'ok', 'source': 'douban', 'facts': [
                {**LOCALE, 'chinese_title': '晚到的错误名称', 'year': '1800', 'english_title': 'Unrelated'}]}
        previous = deepcopy(runtime_context.config)
        with providers.active(), patch('telepiplex_search.service.lookup_douban_evidence', locale):
            runtime_context.configure(config)
            feature = SearchFeature(config=config, host=host,
                release_resolver=lambda release: release.get('magnet_url') or '')
            feature.candidate_locale_timeout = .02
            feature.bind_runtime(runtime)
            request = {'chat_id': 91001, 'user_id': 91001}
            async def callback(payload, target=feature):
                result = await target.callback({**request, 'payload': payload})
                await runtime.drain()
                return result
            try:
                await feature.command({**request, 'command': 's', 'args': ['Fargo']})
                await runtime.drain()
                assert started.is_set() and not gate.is_set()
                plan_id, stored = next(iter(feature.plans.items()))
                ids = [item['candidate_id'] for item in stored['candidates']]
                assert ids == ['wikipedia:Q91000001', 'wikipedia:Q91000002',
                    'wikipedia:Q91000011', 'wikipedia:Q91000012', 'wikipedia:Q91000013', 'wikipedia:Q91000014']
                await callback(f'candidate_page:{plan_id}:1')
                assert [item['candidate_id'] for item in stored['candidates']] == ids
                if scenario == 'cancel':
                    await callback(f'cancel:{plan_id}')
                    assert plan_id not in feature.plans
                elif scenario == 'restart':
                    await runtime.close(feature)
                    restarted = SearchFeature(config=config, host=host)
                    restarted.bind_runtime(runtime)
                    response = await callback(f'select:{plan_id}:1', restarted)
                    assert response['session']['state'] == 'close'
                    assert not restarted.plans
                else:
                    await callback(f'select:{plan_id}:1')
                    if scenario == 'wrong_exact_identity':
                        assert not stored.get('selected_candidate')
                    else:
                        await callback(f'scope:{plan_id}:episode:2:3')
                        contract = stored['confirmed_contract']
                        assert validate_media_metadata_v2(contract, require_confirmed=True) is not None
                        assert contract['scope'] == {'kind': 'episode', 'season_number': 2, 'episode_number': 3}
                        assert contract['identity']['provider_refs']['wikidata'] == 'Q91000002'
                        release_id = next(iter(stored['release_by_id']))
                        await callback(f'release:{plan_id}:{release_id}')
                        assert len(host.submissions) == 1
                frozen = deepcopy({k: stored.get(k) for k in ('candidates', 'selected_candidate',
                    'confirmed_contract', 'active_prowlarr_queries', 'selected_path')})
                submissions = deepcopy(host.submissions)
                assert not completed.is_set(), 'selection must finish before the locale provider'
                gate.set()
                while feature.source_scheduler.in_flight_count:
                    await asyncio.sleep(.001)
                for _ in range(10):
                    await asyncio.sleep(0)
                assert {k: stored.get(k) for k in frozen} == frozen
                assert host.submissions == submissions
                assert len(host.submissions) == (1 if scenario == 'success' else 0)
            finally:
                gate.set()
                await runtime.close(feature)
                while feature.source_scheduler.in_flight_count:
                    await asyncio.sleep(.001)
                runtime_context.configure(previous)
    asyncio.run(run())


ENTITY = {"chinese_title": "示例", "english_title": "Example", "year": "2020",
          "media_type": "movie", "external_ids": {"wikidata": "Q1"}}
WIKIPEDIA = {"status": "ok", "facts": [{"language": "zh", "title": "示例",
    "wikibase_item": "Q1", "search_rank": 1, "page_id": 1}]}


class DiscoveryOverlapTest(unittest.TestCase):
    def test_title_lookup_overlaps_entities_after_successful_first_wikipedia(self):
        title_started = threading.Event()
        entities_started = threading.Event()
        wiki_done = threading.Event()
        overlapped = []

        def wikipedia(payload):
            wiki_done.set()
            return deepcopy(WIKIPEDIA)

        def entities(qids):
            entities_started.set()
            overlapped.append(title_started.wait(.2))
            return {"Q1": deepcopy(ENTITY)}

        def title(query):
            assert wiki_done.is_set()
            title_started.set()
            assert entities_started.wait(.2)
            return ["Q1"]

        roots = discover_root_works(classify_search_input("示例"), wikipedia,
                                    entities, wikidata_search=title)
        self.assertEqual([root["qid"] for root in roots], ["Q1"])
        self.assertEqual(overlapped, [True])

    def test_first_wikipedia_failure_never_starts_title_lookup(self):
        titles = []
        with self.assertRaises(SearchPlanningError) as raised:
            discover_root_works(classify_search_input("示例"),
                lambda _: {"status": "rate_limited", "error": "limited"},
                lambda _: {}, wikidata_search=lambda q: titles.append(q))
        self.assertEqual(raised.exception.code, "source_rate_limited")
        self.assertEqual(titles, [])

    def test_entity_failure_keeps_priority_over_speculative_title_failure(self):
        titles = []
        def title(query):
            titles.append(query)
            raise RuntimeError("title_down")
        def entities(qids):
            raise RuntimeError("entity_down")
        with self.assertRaises(SearchPlanningError) as raised:
            discover_root_works(classify_search_input("示例"),
                lambda _: deepcopy(WIKIPEDIA), entities, wikidata_search=title)
        self.assertEqual(raised.exception.reason_codes, ("wikidata:entity_down",))
        self.assertEqual(len(titles), 2)  # one logical lookup, existing retry bound
