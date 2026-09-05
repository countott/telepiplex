"""Business audit regressions: real Search service, external providers isolated."""
import asyncio
import importlib.util
from pathlib import Path

import pytest

from telepiplex_search import live_pipeline_audit as audit


@pytest.mark.parametrize('year,kind', [('1996', 'movie'), ('2014', 'series')])
def test_offline_audit_requires_real_v2_submission_and_frozen_identity(year, kind):
    assert hasattr(audit, 'run_business_case'), 'missing real command/callback audit'
    case = {'case_id': 'fargo-' + year, 'query': 'Fargo',
            'expected_titles': ['Fargo'], 'year': year, 'media_type': kind,
            'scope': 'work', 'season_number': None, 'episode_number': None}
    report = asyncio.run(audit.run_business_case(case))
    assert report['outcome'] == 'business_success', report
    assert report['passed'] is True
    assert report['submission']['media_metadata']['schema_version'] == 2
    assert report['submission']['media_metadata']['identity']['year'] == int(year)
    assert report['submission']['media_metadata']['identity']['media_type'] == kind
    assert report['candidate_identities'] == ['Q91000001', 'Q91000002']
    assert report['submission_count'] == 1
    assert report['submission']['release']['title'] == (
        'Fargo.1996.1080p.WEB-DL' if kind == 'movie' else 'Fargo.S01-S02.1080p.WEB-DL')
    assert report['duplicate_submission_count'] == 0
    assert report['idempotency_key'].endswith(':release:' + report['release_id'])
    assert report['stages']['command'] == 'ok'
    assert report['stages']['candidate_confirmation'] == 'ok'
    assert report['stages']['download_capture'] == 'ok'


def test_absent_fixture_is_skipped_and_never_counts_as_success():
    assert hasattr(audit, 'run_business_case'), 'missing skipped classification'
    report = asyncio.run(audit.run_business_case({
        'case_id': 'missing', 'query': 'No fixture', 'expected_titles': ['No fixture'],
        'year': '2020', 'media_type': 'movie', 'scope': 'work',
    }))
    assert report['outcome'] == 'skipped'
    assert report['passed'] is False
    assert report['reason_code'] == 'offline_fixture_unavailable'


def test_cli_defaults_to_offline_and_records_online_modes_as_unexecuted(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'tools/run_live_pipeline_audit.py'
    spec = importlib.util.spec_from_file_location('audit_cli_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, 'build_parser'), 'missing explicit mode parser'
    args = module.build_parser().parse_args([
        '--output', str(tmp_path / 'audit.json'), '--case-id', 'us-fargo-movie'])
    assert args.mode == 'offline'
    report = asyncio.run(module.run(args))
    assert report['summary']['business_success'] == 1
    assert report['summary']['safe_rejection'] == 0
    assert report['online_results']['public']['outcome'] == 'skipped'
    assert report['online_results']['prowlarr']['outcome'] == 'skipped'
    assert report['safety']['download_submitted'] is False


def fargo_series(**changes):
    return {'case_id': 'fargo-series', 'query': 'Fargo', 'expected_titles': ['Fargo'],
            'year': '2014', 'media_type': 'series', 'scope': 'work',
            'season_number': None, 'episode_number': None, **changes}


@pytest.mark.parametrize('scenario,outcome,reason', [
    ('cancel', 'safe_rejection', 'user_cancelled'),
    ('missing_directory', 'safe_rejection', 'category_route_missing'),
    ('partial_releases', 'safe_rejection', 'no_eligible_release'),
    ('source_failure', 'source_failure', 'expected_candidate_unavailable'),
    ('prowlarr_failure', 'source_failure', 'release_source_unavailable'),
    ('submit_response_loss', 'source_failure', 'submission_response_lost'),
])
def test_failures_and_cancel_never_count_as_business_success(scenario, outcome, reason):
    case = fargo_series(scenario=scenario, expected_outcome=outcome)
    report = asyncio.run(audit.run_business_case(case))
    assert report['outcome'] == outcome, report
    assert report['reason_code'] == reason, report
    assert report['passed'] is True
    assert report['submission_count'] == (1 if scenario == 'submit_response_loss' else 0)


@pytest.mark.parametrize('scope,query,episode', [
    ('season', 'Fargo S02', None), ('episode', 'Fargo', 3),
])
def test_explicit_scope_reaches_v2_without_widening(scope, query, episode):
    case = fargo_series(query=query, scope=scope, season_number=2, episode_number=episode)
    report = asyncio.run(audit.run_business_case(case))
    assert report['outcome'] == 'business_success', report
    assert report['submission']['media_metadata']['scope'] == {
        'kind': scope, 'season_number': 2, 'episode_number': episode}
    title = report['submission']['release']['title']
    assert ('S02E03' if episode else 'S02.') in title


def test_missing_episode_inventory_is_safe_rejection_and_not_sdk_success():
    report = asyncio.run(audit.run_business_case(fargo_series(
        query='Fargo S02E03', scope='episode', season_number=2, episode_number=3,
        scenario='missing_inventory', expected_outcome='safe_rejection')))
    assert report['outcome'] == 'safe_rejection', report
    assert report['passed'] is True
    assert report['submission_count'] == 0
    assert report['stages']['download_capture'] == 'pending'


@pytest.mark.parametrize('title,year,kind,qid', [
    ('Westworld', '1973', 'movie', 'Q91000003'),
    ('Westworld', '2016', 'series', 'Q91000004'),
    ('想见你', '2019', 'series', 'Q91000005'),
    ('想见你', '2022', 'movie', 'Q91000006'),
])
def test_named_same_title_works_keep_distinct_frozen_v2_identity(title, year, kind, qid):
    case = fargo_series(query=title, expected_titles=[title], year=year, media_type=kind)
    report = asyncio.run(audit.run_business_case(case))
    assert report['outcome'] == 'business_success', report
    assert report['submission']['media_metadata']['identity']['provider_refs']['wikidata'] == qid
    assert report['submission']['media_metadata']['identity']['year'] == int(year)


def test_live_entrypoint_requires_explicit_opt_in_before_any_network():
    report = asyncio.run(audit.run_business_case(fargo_series(), mode='public'))
    assert report['outcome'] == 'skipped'
    assert report['reason_code'] == 'explicit_network_opt_in_required'


def test_explicit_episode_matches_public_callback_with_same_complete_wikipedia_table():
    direct = asyncio.run(audit.run_business_case(fargo_series(
        query='Fargo S02E03', scope='episode', season_number=2, episode_number=3)))
    interactive = asyncio.run(audit.run_business_case(fargo_series(
        scope='episode', season_number=2, episode_number=3)))
    assert direct['outcome'] == 'business_success', direct
    assert interactive['outcome'] == 'business_success', interactive
    assert direct['submission']['media_metadata'] == interactive['submission']['media_metadata']
    assert direct['submission']['release'] == interactive['submission']['release']
    assert direct['submission']['media_metadata']['scope'] == {
        'kind': 'episode', 'season_number': 2, 'episode_number': 3}


def test_offline_transport_blocks_unexpected_network_and_reports_failure(monkeypatch):
    import socket
    from telepiplex_search.audit_transport import FixtureProviders
    original = FixtureProviders.wikipedia
    def attempts_network(self, *args, **kwargs):
        try:
            with socket.socket() as connection:
                connection.connect(('203.0.113.1', 443))
        except Exception:
            pass
        return original(self, *args, **kwargs)
    monkeypatch.setattr(FixtureProviders, 'wikipedia', attempts_network)
    report = asyncio.run(audit.run_business_case(fargo_series()))
    assert report['outcome'] == 'unexpected_failure'
    assert report['reason_code'] == 'offline_network_attempt'
    assert report['passed'] is False


def test_no_ai_or_tvdb_credentials_are_required_to_load_live_metadata_config(tmp_path, monkeypatch):
    from tests.test_live_search_usability import _live_config, LIVE_CONFIG_ENV
    config = tmp_path / 'search.yaml'
    config.write_text('metadata:\n  wikipedia:\n    enable: true\n')
    monkeypatch.setenv(LIVE_CONFIG_ENV, str(config))
    import unittest
    try:
        loaded = _live_config()
    except unittest.SkipTest as exc:
        pytest.fail('obsolete live prerequisites blocked execution: ' + str(exc))
    assert loaded['metadata']['wikipedia']['enable'] is True


@pytest.mark.parametrize('scenario', ['missing_inventory', 'missing_episode', 'conflicting_inventory', 'wrong_exact_identity'])
def test_explicit_episode_rejects_absent_or_conflicting_same_work_evidence(scenario):
    report = asyncio.run(audit.run_business_case(fargo_series(
        query='Fargo S02E03', scope='episode', season_number=2, episode_number=3,
        scenario=scenario, expected_outcome='safe_rejection')))
    assert report['outcome'] == 'safe_rejection', report
    assert report['submission_count'] == 0


def test_prowlarr_entrypoint_skips_missing_credentials_even_with_network_opt_in():
    report = asyncio.run(audit.run_business_case(fargo_series(), mode='prowlarr', allow_network=True))
    assert report['outcome'] == 'skipped'
    assert report['reason_code'] == 'prowlarr_credentials_missing'


def test_readonly_public_mode_never_follows_release_download_url(monkeypatch):
    from telepiplex_search.audit_transport import FixtureProviders
    case = fargo_series(media_type='movie', year='1996')
    with FixtureProviders(case).active():
        # Public mode keeps real Search processing, while surrounding fixtures
        # replace metadata network for this transport-safety regression.
        original = FixtureProviders.releases
        def url_only(self, *args, **kwargs):
            results = original(self, *args, **kwargs)
            for item in results:
                item['download_url'] = 'https://indexer.invalid/grab?apikey=private'
                item.pop('magnet_url', None)
            return results
        monkeypatch.setattr(FixtureProviders, 'releases', url_only)
        report = asyncio.run(audit.run_business_case(case, mode='public', allow_network=True))
    assert report['outcome'] != 'business_success'
    assert report['submission_count'] == 0
    assert 'private' not in str(report)


def test_restart_expires_command_session_without_replaying_submission():
    report = asyncio.run(audit.run_business_case(fargo_series(
        scenario='restart', expected_outcome='safe_rejection')))
    assert report['outcome'] == 'safe_rejection'
    assert report['reason_code'] == 'session_expired_after_restart'
    assert report['submission_count'] == 0


def test_ongoing_season_cannot_be_submitted_as_complete_aggregate():
    report = asyncio.run(audit.run_business_case(fargo_series(
        scenario='ongoing_season', scope='season', season_number=2,
        expected_outcome='safe_rejection')))
    assert report['outcome'] == 'safe_rejection', report
    assert report['submission_count'] == 0


def test_ongoing_season_can_submit_only_an_aired_selected_episode():
    report = asyncio.run(audit.run_business_case(fargo_series(
        scenario='ongoing_season', scope='episode', season_number=2, episode_number=2)))
    assert report['outcome'] == 'business_success', report
    assert report['submission']['media_metadata']['scope'] == {
        'kind': 'episode', 'season_number': 2, 'episode_number': 2}


def test_exact_metadata_provider_outage_is_source_failure_not_safe_rejection(monkeypatch):
    from telepiplex_search.audit_transport import FixtureProviders
    from telepiplex_search.adapters.wikipedia import WikipediaPageLookupError
    def unavailable(self, *args):
        raise WikipediaPageLookupError('timeout')
    monkeypatch.setattr(FixtureProviders, 'page', unavailable)
    report = asyncio.run(audit.run_business_case(fargo_series()))
    assert report['outcome'] == 'source_failure', report
    assert report['reason_code'] == 'metadata_source_unavailable'
    assert report['submission_count'] == 0


def test_explicit_future_episode_stays_rejected_after_wikipedia_scope_fix():
    report = asyncio.run(audit.run_business_case(fargo_series(
        query='Fargo S02E03', scenario='ongoing_season', scope='episode',
        season_number=2, episode_number=3, expected_outcome='safe_rejection')))
    assert report['outcome'] == 'safe_rejection', report
    assert report['submission_count'] == 0
