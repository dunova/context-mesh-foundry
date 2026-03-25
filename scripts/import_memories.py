#!/usr/bin/env python3
"""Legacy wrapper for `context_cli.py import`."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import context_cli

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import Context Mesh memories.")
    parser.add_argument("input", help="Input JSON path exported by export_memories.py")
    parser.add_argument("--no-sync", action="store_true", help="Skip sync_index_from_storage after import.")
    args = parser.parse_args(argv)
    forwarded = ["import", str(Path(args.input).expanduser())]
    if args.no_sync:
        forwarded.append("--no-sync")
    return context_cli.run(context_cli.build_parser().parse_args(forwarded))


if __name__ == "__main__":
    raise SystemExit(main())
