#!/usr/bin/env python3
"""Smoke-test the installed `contextgo` CLI wrapper.

Runs a small set of wrapper-level checks in an isolated sandbox so we verify
the actual executable on PATH, not just the copied runtime tree.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def resolve_contextgo_executable() -> Path | None:
    """Return the installed `contextgo` executable path, if available."""
    explicit = os.environ.get("CONTEXTGO_EXECUTABLE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    found = shutil.which("contextgo")
    return Path(found) if found else None


def _sandbox_env(sandbox_dir: str) -> dict[str, str]:
    base = os.environ.copy()
    base.update(
        {
            "HOME": sandbox_dir,
            "CONTEXTGO_STORAGE_ROOT": sandbox_dir,
            "CONTEXTGO_SESSION_SYNC_MIN_INTERVAL_SEC": "0",
            "CONTEXTGO_SOURCE_CACHE_TTL_SEC": "0",
        }
    )
    return base


def _run_case(exe: Path, args: list[str], env: dict[str, str]) -> dict[str, Any]:
    proc = subprocess.run(
        [str(exe), *args],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env=env,
    )
    return {
        "args": args,
        "rc": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def main() -> int:
    exe = resolve_contextgo_executable()
    if exe is None:
        payload = {
            "scope": "installed-cli",
            "ok": False,
            "error": "contextgo executable not found on PATH",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    with tempfile.TemporaryDirectory(prefix="contextgo-cli-smoke-") as sandbox_dir:
        env = _sandbox_env(sandbox_dir)

        help_case = _run_case(exe, ["--help"], env)
        health_case = _run_case(exe, ["health"], env)
        serve_help_case = _run_case(exe, ["serve", "--help"], env)
        maintain_help_case = _run_case(exe, ["maintain", "--help"], env)
        shell_init_case = _run_case(exe, ["shell-init"], env)

    help_ok = help_case["rc"] == 0 and "ContextGO unified CLI" in help_case["stdout"]
    serve_ok = serve_help_case["rc"] == 0 and "--port" in serve_help_case["stdout"]
    maintain_ok = maintain_help_case["rc"] == 0 and "--dry-run" in maintain_help_case["stdout"]
    shell_init_ok = shell_init_case["rc"] == 0 and "contextgo shell-init" in shell_init_case["stdout"]

    try:
        health_json = json.loads(health_case["stdout"] or "{}")
    except json.JSONDecodeError:
        health_json = {}
    health_ok = health_case["rc"] == 0 and bool(health_json.get("all_ok"))

    payload = {
        "scope": "installed-cli",
        "executable": str(exe),
        "ok": all([help_ok, health_ok, serve_ok, maintain_ok, shell_init_ok]),
        "checks": {
            "help": {"ok": help_ok, "rc": help_case["rc"]},
            "health": {"ok": health_ok, "rc": health_case["rc"], "all_ok": health_json.get("all_ok")},
            "serve_help": {"ok": serve_ok, "rc": serve_help_case["rc"]},
            "maintain_help": {"ok": maintain_ok, "rc": maintain_help_case["rc"]},
            "shell_init": {"ok": shell_init_ok, "rc": shell_init_case["rc"]},
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
