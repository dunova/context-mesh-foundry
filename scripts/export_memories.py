#!/usr/bin/env python3
"""Legacy wrapper for `context_cli.py export`."""

from __future__ import annotations

import argparse
from pathlib import Path

import context_cli


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Context Mesh memories.")
    parser.add_argument("query", help="Search query. Use empty string for all.", nargs="?", default="")
    parser.add_argument("output", help="Output JSON path.")
    parser.add_argument("--limit", type=int, default=5000, help="Max observations to export.")
    parser.add_argument("--source-type", default="all", choices=["all", "history", "conversation"])
    args = parser.parse_args()
    argv = [
        "export",
        args.query,
        args.output,
        "--limit",
        str(args.limit),
        "--source-type",
        args.source_type,
    ]
    return context_cli.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
