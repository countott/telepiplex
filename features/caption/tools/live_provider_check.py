"""Low-volume real retrieval of dated library examples, with no media writes.

PYTHONPATH=src:../../sdk/src python tools/live_provider_check.py --providers assrt,subhd,xunlei --limit 4 --output /tmp/caption-live.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time

import yaml

from telepiplex_caption.engine import CaptionEngine
from telepiplex_caption.models import MediaQuery
from telepiplex_caption.providers import build_providers


async def run(args):
    module_root = Path(__file__).resolve().parents[1]
    cases = json.loads((module_root / "docs/library-samples-2026-09-28.json").read_text())["cases"]
    if args.titles:
        wanted = set(args.titles.split(","))
        cases = [case for case in cases if case["title"] in wanted]
        if {case["title"] for case in cases} != wanted:
            raise ValueError("title is not in the verified library sample list")
    cases = cases[:args.limit]
    config = yaml.safe_load((args.config or module_root / "config.default.yaml").read_text()) or {}
    config["max_candidates"] = args.candidates
    providers = build_providers(config)
    if args.providers:
        wanted = set(args.providers.split(","))
        if not wanted <= {p.name for p in providers}:
            raise ValueError("unknown provider requested")
        providers = [p for p in providers if p.name in wanted]
    engine = CaptionEngine(config, providers=providers)
    report = {"started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scope": "Sequential low-volume live provider retrieval using historical inventory titles. Test identities are explicit inputs, not a live Search resolution. No media writes, video access, playback sync or semantic translation verification.",
        "providers": [p.name for p in providers], "cases": []}
    for case in cases:
        query = MediaQuery.from_dict({**case, "release_name": case["inventory_filename"]})
        start = time.perf_counter()
        try:
            selections, diagnostics = await asyncio.wait_for(engine.retrieve(query), 300)
            selected = [{**s.summary(), "bytes": len(s.document.content),
                "sha256": hashlib.sha256(s.document.content).hexdigest(),
                "first_start": s.quality.first_start, "last_end": s.quality.last_end} for s in selections]
        except TimeoutError:
            selected, diagnostics = [], {"status": "overall_timeout"}
        report["cases"].append({"title": case["title"], "year": query.year, "original_language": query.original_language,
            "media_type": query.media_type, "season": query.season, "episode": query.episode,
            "elapsed_seconds": round(time.perf_counter() - start, 3), "selected": selected, "diagnostics": diagnostics})
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"title": case["title"], "status": diagnostics["status"],
            "selected": [(s["provider"], s["language"], s["format"], s["bilingual"]) for s in selected]}, ensure_ascii=False), flush=True)
        await asyncio.sleep(1)
    report["completed_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["matched_cases"] = sum(bool(c["selected"]) for c in report["cases"])
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", default="")
    parser.add_argument("--titles", default="")
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--candidates", type=int, default=12)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.limit <= 16 or not 1 <= args.candidates <= 30:
        parser.error("limit must be 1..16 and candidates 1..30")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
