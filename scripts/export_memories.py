#!/usr/bin/env python3
"""Legacy wrapper for `context_cli.py export`."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import context_cli


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Context Mesh memories.")
    parser.add_argument("query", help="Search query. Use empty string for all.", nargs="?", default="")
    parser.add_argument("output", help="Output JSON path.")
    parser.add_argument("--limit", type=int, default=5000, help="Max observations to export.")
    parser.add_argument("--source-type", default="all", choices=["all", "history", "conversation"])
    args = parser.parse_args()
    return context_cli.run(
        context_cli.build_parser().parse_args(
            [
                "export",
                args.query,
                args.output,
                "--limit",
                str(args.limit),
                "--source-type",
                args.source_type,
            ]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
