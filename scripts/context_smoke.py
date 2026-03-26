#!/usr/bin/env python3
"""Smoke-test helpers for ContextGO runtimes."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid


def run_cmd(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    proc = subprocess.run(args, capture_output=True, text=False, timeout=timeout)
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    return proc.returncode, stdout, stderr


def test_health(cli_path: Path) -> dict:
    rc, out, err = run_cmd([sys.executable, str(cli_path), "health"])
    text = (out or err).strip()
    detail = {}
    try:
        payload = json.loads(text)
        detail = payload
        ok = bool(payload.get("all_ok"))
        error = None
    except json.JSONDecodeError as exc:
        payload = None
        ok = False
        error = str(exc)
    if error:
        detail["error"] = error
        detail["raw"] = text
    return {"name": "health", "rc": rc, "ok": ok, "detail": detail}


def test_quality_gate(quality_gate_path: Path) -> dict:
    rc, out, err = run_cmd([sys.executable, str(quality_gate_path)], timeout=120)
    text = (out or err).strip()
    return {"name": "quality_gate", "rc": rc, "ok": rc == 0, "detail": text}


def test_healthcheck(healthcheck_path: Path) -> dict:
    rc, out, err = run_cmd(["bash", str(healthcheck_path), "--quiet"])
    text = (out or err).strip()
    return {
        "name": "healthcheck",
        "rc": rc,
        "ok": rc == 0,
        "detail": {
            "output": text[:400],
            "stderr": err[:400],
            "stdout": out[:400],
        },
    }


def test_rw_cycle(cli_path: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="cmf-smoke-") as tmpdir:
        tmpdir = Path(tmpdir)
        export_path = tmpdir / "export.json"
        marker = f"smoke-{uuid.uuid4().hex[:12]}"
        r1 = run_cmd([sys.executable, str(cli_path), "save", "--title", "smoke", "--content", marker, "--tags", "smoke"])
        r2 = run_cmd([sys.executable, str(cli_path), "semantic", marker, "--limit", "3"])
        r3 = run_cmd([sys.executable, str(cli_path), "export", marker, str(export_path), "--limit", "10"])
        export_payload = json.loads(export_path.read_text()) if export_path.exists() else {}
        r4 = run_cmd([sys.executable, str(cli_path), "import", str(export_path), "--no-sync"]) if export_path.exists() else (1, "", "no export")
        ok = (
            r1[0] == 0
            and r2[0] == 0
            and marker in r2[1]
            and r3[0] == 0
            and export_payload.get("total_observations") == 1
            and r4[0] == 0
        )
        return {
            "name": "rw_cycle",
            "rc": 0 if ok else 1,
            "ok": ok,
            "detail": {
                "save_rc": r1[0],
                "semantic_rc": r2[0],
                "semantic_found": marker in r2[1],
                "export_rc": r3[0],
                "export_count": export_payload.get("total_observations"),
                "import_rc": r4[0],
            },
        }


def test_maintain(cli_path: Path) -> dict:
    rc, out, err = run_cmd([sys.executable, str(cli_path), "maintain", "--dry-run"])
    text = out or err
    return {
        "name": "maintain",
        "rc": rc,
        "ok": rc == 0 and "Snapshot" in text,
        "detail": text[:400],
    }


def test_viewer(cli_path: Path) -> dict:
    port = 38880
    proc = subprocess.Popen(
        [sys.executable, str(cli_path), "serve", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
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
            except Exception as exc:
                last_err = str(exc)
                time.sleep(0.2)
        return {
            "name": "viewer",
            "rc": 0 if ok else 1,
            "ok": ok,
            "detail": {"body_head": body[:200], "last_err": last_err},
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()


def _available_native_backends(cli_path: Path) -> list[str]:
    rc, out, err = run_cmd([sys.executable, str(cli_path), "health"])
    text = (out or err).strip()
    if rc != 0 or not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    native = payload.get("native_backends") or {}
    backends = native.get("available_backends") or []
    return [str(item) for item in backends if str(item) in {"rust", "go"}]


def _write_native_fixture(root: Path, marker: str) -> tuple[Path, Path]:
    codex_root = root / "codex"
    claude_root = root / "claude"
    target = codex_root / "2026" / "03" / "26"
    target.mkdir(parents=True, exist_ok=True)
    claude_root.mkdir(parents=True, exist_ok=True)
    session_file = target / "native-fixture.jsonl"
    lines = [
        {
            "type": "session_meta",
            "payload": {
                "id": "native-fixture-session",
                "cwd": "/tmp/contextgo-native-fixture",
                "timestamp": "2026-03-26T00:00:00Z",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "output": f"# AGENTS.md instructions for /tmp {marker}",
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
                        "text": f"最终交付：ContextGO native smoke marker {marker} 已验证。",
                    }
                ],
            },
        },
    ]
    session_file.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in lines), encoding="utf-8")
    return codex_root, claude_root


def test_native_scan_contract(cli_path: Path) -> dict:
    backends = _available_native_backends(cli_path)
    if not backends:
        return {
            "name": "native_scan",
            "rc": 0,
            "ok": True,
            "detail": {"skipped": True, "reason": "no native backend available"},
        }

    marker = f"smoke-native-{int(time.time())}"
    backend_results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="contextgo-native-smoke-") as tmpdir:
        codex_root, claude_root = _write_native_fixture(Path(tmpdir), marker)
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
            rc, out, err = run_cmd(args, timeout=120)
            transient = "resource temporarily unavailable"
            if rc != 0 and transient in ((out or "") + "\n" + (err or "")).lower():
                time.sleep(0.5)
                rc, out, err = run_cmd(args, timeout=120)
            text = (out or err).strip()
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                backend_results.append(
                    {
                        "backend": backend,
                        "rc": rc,
                        "ok": False,
                        "error": f"invalid json: {exc}",
                        "raw": text[:400],
                    }
                )
                continue
            matches = payload.get("matches") or []
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
    ok = all(item.get("ok") for item in backend_results)
    return {
        "name": "native_scan",
        "rc": 0 if ok else 1,
        "ok": ok,
        "detail": {"backends": backend_results},
    }


def run_smoke(cli_path: Path, quality_gate_path: Path) -> dict:
    healthcheck_path = cli_path.with_name("context_healthcheck.sh")
    results = [
        test_health(cli_path),
        test_native_scan_contract(cli_path),
        test_healthcheck(healthcheck_path),
        test_quality_gate(quality_gate_path),
        test_rw_cycle(cli_path),
        test_maintain(cli_path),
        test_viewer(cli_path),
    ]
    return {
        "healthcheck_path": str(healthcheck_path),
        "summary": summarize_results(results),
        "results": results,
    }


def summarize_results(results: list[dict]) -> dict:
    failed = [item for item in results if not item.get("ok")]
    return {
        "status": "pass" if not failed else "fail",
        "total": len(results),
        "failed": len(failed),
        "failed_names": [item.get("name") for item in failed],
    }


def main() -> int:
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
