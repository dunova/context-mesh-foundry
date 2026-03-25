#!/usr/bin/env python3
"""Smoke-test the installed Context Mesh runtime."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def resolve_install_root() -> Path:
    explicit = os.environ.get("CONTEXT_MESH_INSTALL_ROOT") or os.environ.get("CMF_INSTALL_ROOT")
    if explicit:
        base = Path(explicit).expanduser()
        return base if base.name == "scripts" else base / "scripts"

    primary_root = Path.home() / ".local" / "share" / "context-mesh-foundry" / "scripts"
    fallback_root = Path.home() / ".local" / "share" / "contextmesh" / "scripts"
    if (primary_root / "context_cli.py").exists() or not fallback_root.exists():
        return primary_root
    return fallback_root


INSTALL_ROOT = resolve_install_root()

try:
    from context_smoke import run_smoke
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from context_smoke import run_smoke


def main() -> int:
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
