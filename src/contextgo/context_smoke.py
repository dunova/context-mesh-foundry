#!/usr/bin/env python3
"""Smoke-test suite for ContextGO runtimes.

Each ``test_*`` function returns a result dict with the shape::

    {
        "name":   str,   # test identifier
        "rc":     int,   # synthesised return code (0 = pass)
        "ok":     bool,  # overall pass/fail
        "detail": ...,   # test-specific diagnostic payload
    }

``run_smoke`` orchestrates all tests and returns an aggregated report.
``main`` serialises the report to stdout as JSON and exits non-zero on failure.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import date
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Low-level subprocess helper
# ---------------------------------------------------------------------------


def run_cmd(
    args: list[str],
    timeout: int = 60,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run *args* as a subprocess and return ``(returncode, stdout, stderr)``.

    Both output streams are decoded from UTF-8 with replacement so callers
    always receive ``str`` regardless of the child process encoding.

    *env* is merged on top of the current process environment when provided.
    """
    merged_env: dict[str, str] | None = None
    if env:
        merged_env = {**os.environ, **env}
    proc = subprocess.run(args, capture_output=True, timeout=timeout, env=merged_env, check=False)
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    return proc.returncode, stdout, stderr


def _free_port() -> int:
    """Return an ephemeral TCP port that is free at the time of the call."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------------------
# Individual smoke tests
# ---------------------------------------------------------------------------


def test_health(cli_path: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Verify that ``context_cli.py health`` returns valid JSON with ``all_ok``."""
    rc, out, err = run_cmd([sys.executable, str(cli_path), "health"], env=env)
    raw = (out or err).strip()
    try:
        payload: dict[str, Any] = json.loads(raw)
        ok = bool(payload.get("all_ok"))
        detail: dict[str, Any] = payload
    except json.JSONDecodeError as exc:
        ok = False
        detail = {"error": f"JSON decode failed: {exc}", "raw": raw}
    return {"name": "health", "rc": rc, "ok": ok, "detail": detail}


def test_quality_gate(quality_gate_path: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Run the e2e quality-gate script and assert it exits cleanly."""
    rc, out, err = run_cmd([sys.executable, str(quality_gate_path)], timeout=120, env=env)
    text = (out or err).strip()
    return {"name": "quality_gate", "rc": rc, "ok": rc == 0, "detail": text}


def test_healthcheck(healthcheck_path: Path) -> dict[str, Any]:
    """Execute the shell healthcheck script in quiet mode.

    Skipped gracefully when the script does not exist.
    """
    if not healthcheck_path.exists():
        return {
            "name": "healthcheck",
            "rc": 0,
            "ok": True,
            "detail": {"skipped": True, "reason": f"not found: {healthcheck_path}"},
        }
    rc, out, err = run_cmd(["bash", str(healthcheck_path), "--quiet"])
    return {
        "name": "healthcheck",
        "rc": rc,
        "ok": rc == 0,
        "detail": {
            "stdout": out[:400],
            "stderr": err[:400],
        },
    }


def test_rw_cycle(cli_path: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Exercise the full save → semantic-search → export → import cycle."""
    with tempfile.TemporaryDirectory(prefix="cmf-smoke-") as _tmpdir:
        tmp = Path(_tmpdir)
        export_path = tmp / "export.json"
        marker = f"smoke-{uuid.uuid4().hex[:12]}"

        r_save = run_cmd(
            [
                sys.executable,
                str(cli_path),
                "save",
                "--title",
                "smoke",
                "--content",
                marker,
                "--tags",
                "smoke",
            ],
            env=env,
        )
        r_semantic = run_cmd(
            [sys.executable, str(cli_path), "semantic", marker, "--limit", "3"],
            env=env,
        )
        r_export = run_cmd(
            [
                sys.executable,
                str(cli_path),
                "export",
                marker,
                str(export_path),
                "--limit",
                "10",
            ],
            env=env,
        )

        export_payload: dict[str, Any] = {}
        if export_path.exists():
            with contextlib.suppress(json.JSONDecodeError):
                export_payload = json.loads(export_path.read_text(encoding="utf-8"))

        if export_path.exists():
            r_import = run_cmd(
                [sys.executable, str(cli_path), "import", str(export_path), "--no-sync"],
                env=env,
            )
        else:
            r_import = (1, "", "export file not produced")

        semantic_found = marker in r_semantic[1]
        export_count = export_payload.get("total_observations", 0)

        ok = (
            r_save[0] == 0
            and r_semantic[0] == 0
            and semantic_found
            and r_export[0] == 0
            and export_count >= 1  # sandbox may have prior data; >= 1 is sufficient
            and r_import[0] == 0
        )
        return {
            "name": "rw_cycle",
            "rc": 0 if ok else 1,
            "ok": ok,
            "detail": {
                "save_rc": r_save[0],
                "semantic_rc": r_semantic[0],
                "semantic_found": semantic_found,
                "export_rc": r_export[0],
                "export_count": export_count,
                "import_rc": r_import[0],
            },
        }


def test_maintain(cli_path: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Run maintenance in dry-run mode and confirm a snapshot is reported."""
    rc, out, err = run_cmd(
        [sys.executable, str(cli_path), "maintain", "--dry-run"],
        env=env,
    )
    text = out or err
    return {
        "name": "maintain",
        "rc": rc,
        "ok": rc == 0 and "Snapshot" in text,
        "detail": text[:400],
    }


def test_viewer(cli_path: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Start the local viewer server and confirm the health endpoint responds."""
    try:
        port = _free_port()
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.EACCES}:
            return {
                "name": "viewer",
                "rc": 0,
                "ok": True,
                "detail": {"skipped": True, "reason": f"loopback socket unavailable: {exc}"},
            }
        return {
            "name": "viewer",
            "rc": 1,
            "ok": False,
            "detail": {"error": f"failed to reserve loopback port: {exc}"},
        }

    try:
        proc = subprocess.Popen(
            [sys.executable, str(cli_path), "serve", "--host", "127.0.0.1", "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, **(env or {})},
        )
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.EACCES}:
            return {
                "name": "viewer",
                "rc": 0,
                "ok": True,
                "detail": {"skipped": True, "reason": f"viewer launch not permitted: {exc}"},
            }
        return {
            "name": "viewer",
            "rc": 1,
            "ok": False,
            "detail": {"error": f"failed to launch viewer: {exc}"},
        }
    try:
        deadline = time.time() + 15
        body = ""
        ok = False
        last_err = ""
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as resp:
                    body = resp.read().decode("utf-8")
                    ok = resp.status == 200
                    break
            except (OSError, urllib.error.URLError) as exc:
                last_err = str(exc)
                time.sleep(0.2)
        return {
            "name": "viewer",
            "rc": 0 if ok else 1,
            "ok": ok,
            "detail": {"port": port, "body_head": body[:200], "last_err": last_err},
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---------------------------------------------------------------------------
# Native-backend helpers
# ---------------------------------------------------------------------------


def _available_native_backends(
    cli_path: Path,
    env: dict[str, str] | None = None,
) -> list[str]:
    """Return the list of available native backends reported by ``health``.

    Returns an empty list when the health command fails or the response is not
    valid JSON — the native-scan test is simply skipped in those cases.
    """
    rc, out, err = run_cmd([sys.executable, str(cli_path), "health"], env=env)
    raw = (out or err).strip()
    if rc != 0 or not raw:
        return []
    try:
        payload: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        return []
    native = payload.get("native_backends") or {}
    backends = native.get("available_backends") or []
    return [str(b) for b in backends if str(b) in {"rust", "go"}]


def _write_native_fixture(root: Path, marker: str) -> tuple[Path, Path]:
    """Write a minimal JSONL session fixture under *root* for native-scan tests.

    The fixture uses today's date for the directory structure so that native
    scanners configured to scan recent sessions will always discover it.

    Returns ``(codex_root, claude_root)``.
    """
    today = date.today()
    codex_root = root / "codex"
    claude_root = root / "claude"
    target = codex_root / str(today.year) / f"{today.month:02d}" / f"{today.day:02d}"
    target.mkdir(parents=True, exist_ok=True)
    claude_root.mkdir(parents=True, exist_ok=True)

    session_file = target / "native-fixture.jsonl"
    lines = [
        {
            "type": "session_meta",
            "payload": {
                "id": "native-fixture-session",
                "cwd": str(root),
                "timestamp": f"{today.isoformat()}T00:00:00Z",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "output": f"# AGENTS.md instructions for {root} {marker}",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": (
                            f"Final delivery: ContextGO native smoke marker {marker} verified. "
                            f"最终交付：ContextGO native smoke marker {marker} 已验证。"
                        ),
                    }
                ],
            },
        },
    ]
    session_file.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in lines),
        encoding="utf-8",
    )
    return codex_root, claude_root


def test_native_scan_contract(
    cli_path: Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Verify that every available native backend can locate a known fixture."""
    backends = _available_native_backends(cli_path, env=env)
    if not backends:
        return {
            "name": "native_scan",
            "rc": 0,
            "ok": True,
            "detail": {"skipped": True, "reason": "no native backend available"},
        }

    marker = f"smoke-native-{int(time.time())}"
    backend_results: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="contextgo-native-smoke-") as _tmpdir:
        codex_root, claude_root = _write_native_fixture(Path(_tmpdir), marker)

        for backend in backends:
            args = [
                sys.executable,
                str(cli_path),
                "native-scan",
                "--backend",
                backend,
                "--codex-root",
                str(codex_root),
                "--claude-root",
                str(claude_root),
                "--query",
                marker,
                "--limit",
                "3",
                "--json",
            ]
            rc, out, err = run_cmd(args, timeout=120, env=env)

            # Retry once on transient resource-lock errors (e.g. file-system busy).
            if rc != 0 and "resource temporarily unavailable" in ((out or "") + "\n" + (err or "")).lower():
                time.sleep(0.5)
                rc, out, err = run_cmd(args, timeout=120, env=env)

            raw = (out or err).strip()
            try:
                payload: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError as exc:
                backend_results.append(
                    {
                        "backend": backend,
                        "rc": rc,
                        "ok": False,
                        "error": f"invalid JSON response: {exc}",
                        "raw": raw[:400],
                    }
                )
                continue

            matches: list[dict[str, Any]] = payload.get("matches") or []
            first = matches[0] if matches else {}
            snippet = str(first.get("snippet") or "")

            ok = (
                rc == 0
                and bool(matches)
                and marker in snippet
                and "# AGENTS.md instructions" not in snippet
                and first.get("session_id") == "native-fixture-session"
            )
            backend_results.append(
                {
                    "backend": backend,
                    "rc": rc,
                    "ok": ok,
                    "match_count": len(matches),
                    "session_id": first.get("session_id"),
                    "snippet_head": snippet[:160],
                }
            )

    all_ok = all(item.get("ok") for item in backend_results)
    return {
        "name": "native_scan",
        "rc": 0 if all_ok else 1,
        "ok": all_ok,
        "detail": {"backends": backend_results},
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Produce a pass/fail summary over a list of test result dicts."""
    failed = [item for item in results if not item.get("ok")]
    return {
        "status": "pass" if not failed else "fail",
        "total": len(results),
        "failed": len(failed),
        "failed_names": [item.get("name") for item in failed],
    }


def run_smoke(
    cli_path: Path,
    quality_gate_path: Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run the full smoke suite and return an aggregated report dict.

    *env* is forwarded to every subprocess invocation so that sandbox
    isolation (e.g. ``CONTEXTGO_STORAGE_ROOT``) is consistently applied
    across the entire suite.
    """
    healthcheck_path = cli_path.with_name("context_healthcheck.sh")
    results = [
        test_health(cli_path, env=env),
        test_native_scan_contract(cli_path, env=env),
        test_healthcheck(healthcheck_path),
        test_quality_gate(quality_gate_path, env=env),
        test_rw_cycle(cli_path, env=env),
        test_maintain(cli_path, env=env),
        test_viewer(cli_path, env=env),
    ]
    return {
        "summary": summarize_results(results),
        "results": results,
    }


def main() -> int:
    """Entry point: run smoke suite and print JSON report to stdout."""
    root = Path(__file__).resolve().parent
    cli_path = root / "context_cli.py"
    quality_gate_path = root / "e2e_quality_gate.py"

    payload = run_smoke(cli_path, quality_gate_path)
    output = {
        "scope": "workspace",
        "workspace_root": str(root),
        "cli_path": str(cli_path),
        "quality_gate_path": str(quality_gate_path),
        **payload,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if payload["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
