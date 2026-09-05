#!/usr/bin/env python3
"""Audit the real telepiplex Search business flow with capture-only downloads."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from telepiplex_search.live_pipeline_audit import load_real_media_corpus, run_business_case

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / 'tests/fixtures/real_media_corpus.json'


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=DEFAULT_CORPUS)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=('offline', 'public', 'prowlarr'), default='offline')
    parser.add_argument('--allow-network', action='store_true', help='explicitly permit read-only provider queries')
    parser.add_argument('--config', type=Path, help='Search YAML config; required for real Prowlarr queries')
    parser.add_argument('--scenario', choices=('success', 'cancel', 'missing_directory',
        'missing_inventory', 'missing_episode', 'conflicting_inventory', 'wrong_exact_identity',
        'partial_releases', 'source_failure', 'prowlarr_failure', 'submit_response_loss',
        'restart', 'ongoing_season'), default='success',
        help='explicit simulated scenario; available only in offline mode')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--case-id', action='append', default=[])
    parser.add_argument('--all-full', action='store_true', help='compatibility option: all selected cases already use the real business flow')
    return parser


async def run(args):
    cases = load_real_media_corpus(args.corpus)
    if args.case_id:
        selected = set(args.case_id)
        unknown = selected - {case['case_id'] for case in cases}
        if unknown:
            raise ValueError('unknown_case_id:' + ','.join(sorted(unknown)))
        cases = [case for case in cases if case['case_id'] in selected]
    if args.limit:
        cases = cases[:args.limit]
    config = None
    prerequisite = ''
    if args.mode != 'offline' and not args.allow_network:
        prerequisite = 'explicit_network_opt_in_required'
    elif args.mode == 'prowlarr' and not args.config:
        prerequisite = 'prowlarr_config_required'
    elif args.config:
        import yaml
        try:
            config = yaml.safe_load(args.config.read_text(encoding='utf-8'))
        except (OSError, yaml.YAMLError):
            prerequisite = 'config_unavailable'
        if not isinstance(config, dict):
            prerequisite = 'config_invalid'
        elif args.mode == 'prowlarr':
            prowlarr = (config.get('search') or {}).get('prowlarr') or {}
            if not prowlarr.get('api_key') or not prowlarr.get('base_url'):
                prerequisite = 'prowlarr_credentials_missing'
    if args.mode != 'offline' and args.scenario != 'success':
        raise ValueError('simulated_scenarios_require_offline_mode')
    started = time.monotonic()
    reports = []
    for index, case in enumerate(cases, 1):
        if args.scenario != 'success':
            expected = 'source_failure' if args.scenario in {
                'source_failure', 'prowlarr_failure', 'submit_response_loss'} else 'safe_rejection'
            case = {**case, 'scenario': args.scenario, 'expected_outcome': expected}
        if prerequisite:
            report = {'case_id': case['case_id'], 'outcome': 'skipped', 'passed': False,
                      'reason_code': prerequisite, 'failure_code': prerequisite, 'stages': {}}
        else:
            report = await run_business_case(case, mode=args.mode, config=config, allow_network=args.allow_network)
        reports.append(report)
        print(f"{index:02d}/{len(cases):02d} {report['outcome']} {case['case_id']} {report['reason_code']}", flush=True)
    counts = Counter(item['outcome'] for item in reports)
    summary = {key: counts[key] for key in (
        'business_success', 'safe_rejection', 'source_failure', 'unexpected_failure', 'skipped')}
    executed = len(reports) - summary['skipped']
    summary.update(total=len(reports), executed=executed,
                   expected_passed=sum(item['passed'] for item in reports),
                   business_completion_rate=round(summary['business_success'] / executed, 4) if executed else None)
    return {
        'schema_version': 2, 'mode': args.mode,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'corpus': str(Path(args.corpus).resolve()),
        'duration_seconds': round(time.monotonic() - started, 3),
        'safety': {'prowlarr_called': any(item.get('prowlarr_called') for item in reports),
                   'download_submitted': False, 'file_operations_executed': False,
                   'provider_credentials_logged': False},
        'summary': summary, 'full_reports': reports,
        'online_results': {mode: {'outcome': 'executed' if args.mode == mode and executed else 'skipped',
                                 'reason_code': prerequisite if args.mode == mode else 'not_requested'}
                           for mode in ('public', 'prowlarr')},
    }


def main():
    args = build_parser().parse_args()
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False), flush=True)
    if report['summary']['executed'] == 0:
        return 2
    return 1 if any(not item['passed'] for item in report['full_reports'] if item['outcome'] != 'skipped') else 0


if __name__ == '__main__':
    raise SystemExit(main())
