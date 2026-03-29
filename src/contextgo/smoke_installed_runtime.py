#!/usr/bin/env python3
"""Smoke-test the installed ContextGO runtime.

Runs entirely inside a temporary sandbox directory so that no user data is
read or written during the test.  Set ``CONTEXTGO_SMOKE_SKIP_SANDBOX=1`` to
run against the real storage root (useful for production health checks).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
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


def _sandbox_env(sandbox_dir: str) -> dict[str, str]:
    """Return an env-var overlay that redirects all storage to *sandbox_dir*.

    This prevents the smoke suite from touching the user's real ContextGO data.
    """
    return {
        "HOME": sandbox_dir,
        "CONTEXTGO_STORAGE_ROOT": sandbox_dir,
        "CONTEXTGO_SESSION_SYNC_MIN_INTERVAL_SEC": "0",
        "CONTEXTGO_SOURCE_CACHE_TTL_SEC": "0",
    }


def main() -> int:
    """Run the installed-runtime smoke suite and print a JSON result.

    By default the suite runs in a temporary sandbox to protect user data.
    Pass ``CONTEXTGO_SMOKE_SKIP_SANDBOX=1`` to skip sandbox isolation.
    """
    cli_path = INSTALL_ROOT / "context_cli.py"
    quality_gate_path = INSTALL_ROOT / "e2e_quality_gate.py"

    skip_sandbox = os.environ.get("CONTEXTGO_SMOKE_SKIP_SANDBOX", "").strip() == "1"

    if skip_sandbox:
        payload = run_smoke(cli_path, quality_gate_path)
        sandbox_used = False
    else:
        with tempfile.TemporaryDirectory(prefix="contextgo-installed-smoke-") as sandbox_dir:
            env_overlay = _sandbox_env(sandbox_dir)
            payload = run_smoke(cli_path, quality_gate_path, env=env_overlay)
        sandbox_used = True

    output = {
        "scope": "installed",
        "install_root": str(INSTALL_ROOT),
        "cli_path": str(cli_path),
        "quality_gate_path": str(quality_gate_path),
        "sandbox_mode": sandbox_used,
        **payload,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if payload["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
