#!/usr/bin/env python3
"""Smoke-test the installed Context Mesh runtime."""

from __future__ import annotations

from pathlib import Path
import json
import sys
INSTALL_ROOT = Path.home() / ".local" / "share" / "context-mesh-foundry" / "scripts"

try:
    from context_smoke import run_smoke
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from context_smoke import run_smoke


def main() -> int:
    payload = run_smoke(INSTALL_ROOT / "context_cli.py", INSTALL_ROOT / "e2e_quality_gate.py")
    print(
        json.dumps(
            {
                "install_root": str(INSTALL_ROOT),
                "summary": payload["summary"],
                "results": payload["results"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if payload["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
