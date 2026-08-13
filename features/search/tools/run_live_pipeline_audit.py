#!/usr/bin/env python3
"""Run the opt-in real Wikipedia/Wikidata Search pipeline audit."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from telepiplex_search.adapters.wikidata import (
    enrich_wikidata_entities,
    search_wikidata_entities,
)
from telepiplex_search.live_pipeline_audit import (
    audit_live_full_case,
    audit_root_case,
    load_real_media_corpus,
)
from telepiplex_search.service import SearchFeature


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "tests/fixtures/real_media_corpus.json"


def _cached(callable_, *, attempts: int = 3):
    cache = {}

    def wrapped(value):
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if key in cache:
            return deepcopy(cache[key])
        last_error = None
        result = None
        for attempt in range(max(1, attempts)):
            try:
                result = callable_(value)
            except Exception as exc:
                last_error = exc
            else:
                status = (
                    str(result.get("status") or "")
                    if isinstance(result, dict)
                    else ""
                )
                if status not in {"server_down", "timeout", "rate_limited"}:
                    cache[key] = result
                    return deepcopy(result)
            if attempt + 1 < max(1, attempts):
                time.sleep(min(2.0, 0.5 * (attempt + 1)))
        if last_error is not None:
            raise last_error
        cache[key] = result
        return deepcopy(cache[key])

    return wrapped


def _config(min_interval: float) -> dict:
    return {
        "category_folder": [
            {"kind": "live_action_movie", "path": "/真人电影", "plex_library_id": ""},
            {"kind": "animated_movie", "path": "/动画电影", "plex_library_id": ""},
            {"kind": "live_action_series", "path": "/真人剧集", "plex_library_id": ""},
            {"kind": "animated_series", "path": "/动画剧集", "plex_library_id": ""},
        ],
        "metadata": {
            "wikipedia": {
                "enable": True,
                "languages": ["zh", "en"],
                "timeout": 15,
                "min_interval": min_interval,
                "max_queries": 2,
                "rate_limit_cooldown": 30,
            },
            "douban": {"enable": True, "timeout": 10},
            "tmdb": {"enable": True, "api_key": "", "timeout": 10},
            "tvdb": {"enable": True, "api_key": "", "timeout": 10},
            "anilist": {"enable": True, "timeout": 10},
        },
    }


async def run(args) -> dict:
    cases = load_real_media_corpus(args.corpus)
    if args.case_id:
        selected_ids = set(args.case_id)
        cases = [case for case in cases if case["case_id"] in selected_ids]
        missing_ids = selected_ids.difference(
            case["case_id"] for case in cases
        )
        if missing_ids:
            raise ValueError(
                "unknown_case_id:" + ",".join(sorted(missing_ids))
            )
    if args.limit:
        cases = cases[: args.limit]
    feature = SearchFeature(config=_config(args.min_interval), host=None)
    wikipedia = _cached(feature._wikipedia_provider)
    feature._wikipedia_provider = wikipedia
    wikidata = _cached(enrich_wikidata_entities)
    wikidata_search = _cached(search_wikidata_entities)
    started = time.monotonic()
    root_reports = []
    for index, case in enumerate(cases, 1):
        report = await asyncio.to_thread(
            audit_root_case,
            case,
            wikipedia_lookup=wikipedia,
            wikidata_lookup=wikidata,
            wikidata_search=wikidata_search,
        )
        root_reports.append(report)
        print(
            f"root {index:02d}/{len(cases):02d} "
            f"{'PASS' if report['passed'] else 'FAIL'} {case['case_id']} "
            f"{report.get('failure_code') or report.get('matched_qid') or ''}",
            flush=True,
        )

    full_cases = (
        list(cases)
        if args.all_full
        else [case for case in cases if case["full_pipeline"]]
    )
    full_reports = []
    for index, case in enumerate(full_cases, 1):
        report = await audit_live_full_case(
            case,
            feature,
            wikipedia_lookup=wikipedia,
            wikidata_lookup=wikidata,
            wikidata_search=wikidata_search,
        )
        full_reports.append(report)
        print(
            f"full {index:02d}/{len(full_cases):02d} "
            f"{'PASS' if report['passed'] else 'FAIL'} {case['case_id']} "
            f"{report.get('failure_code') or ', '.join(report.get('queries') or ())}",
            flush=True,
        )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus": str(Path(args.corpus).resolve()),
        "duration_seconds": round(time.monotonic() - started, 3),
        "safety": {
            "prowlarr_called": False,
            "download_submitted": False,
            "provider_credentials_logged": False,
        },
        "summary": {
            "root_total": len(root_reports),
            "root_passed": sum(item["passed"] for item in root_reports),
            "root_failed": sum(not item["passed"] for item in root_reports),
            "full_total": len(full_reports),
            "full_passed": sum(item["passed"] for item in full_reports),
            "full_failed": sum(not item["passed"] for item in full_reports),
        },
        "root_reports": root_reports,
        "full_reports": full_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument(
        "--all-full",
        action="store_true",
        help="run the downstream dry-run chain for every selected root case",
    )
    parser.add_argument("--min-interval", type=float, default=0.2)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    return 0 if not (
        report["summary"]["root_failed"]
        or report["summary"]["full_failed"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
