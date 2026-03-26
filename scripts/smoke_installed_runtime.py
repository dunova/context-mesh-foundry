#!/usr/bin/env python3
"""Smoke-test the installed ContextGO runtime."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def resolve_install_root() -> Path:
    """Return the scripts directory path for the installed ContextGO runtime."""
    explicit = os.environ.get("CONTEXTGO_INSTALL_ROOT")
    if explicit:
        base = Path(explicit).expanduser()
        return base if base.name == "scripts" else base / "scripts"

    return Path.home() / ".local" / "share" / "contextgo" / "scripts"


INSTALL_ROOT = resolve_install_root()

try:
    from context_smoke import run_smoke
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from context_smoke import run_smoke


def main() -> int:
    """Run the installed-runtime smoke suite and print a JSON result."""
    cli_path = INSTALL_ROOT / "context_cli.py"
    quality_gate_path = INSTALL_ROOT / "e2e_quality_gate.py"
    payload = run_smoke(cli_path, quality_gate_path)
    output = {
        "scope": "installed",
        "install_root": str(INSTALL_ROOT),
        "cli_path": str(cli_path),
        "quality_gate_path": str(quality_gate_path),
        **payload,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if payload["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
