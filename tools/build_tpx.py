#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.plugin_artifact import build_tpx, verify_tpx


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a deterministic Telepiplex Feature artifact."
    )
    parser.add_argument("SOURCE_DIR", type=Path)
    parser.add_argument("OUTPUT.tpx", type=Path)
    args = parser.parse_args(argv)
    output = build_tpx(getattr(args, "SOURCE_DIR"), getattr(args, "OUTPUT.tpx"))
    verified = verify_tpx(output)
    print(f"{output} {verified.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
