#!/usr/bin/env python3
"""Smoke-test the installed Context Mesh runtime."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request


INSTALL_ROOT = Path.home() / ".local" / "share" / "context-mesh-foundry" / "scripts"
CLI = INSTALL_ROOT / "context_cli.py"
QUALITY_GATE = INSTALL_ROOT / "e2e_quality_gate.py"


def run_cmd(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def test_health() -> dict:
    rc, out, err = run_cmd([sys.executable, str(CLI), "health"])
    text = out or err
    payload = json.loads(text)
    return {"name": "health", "rc": rc, "ok": bool(payload.get("all_ok")), "detail": payload}


def test_quality_gate() -> dict:
    rc, out, err = run_cmd([sys.executable, str(QUALITY_GATE)], timeout=120)
    text = (out or err).strip()
    return {"name": "quality_gate", "rc": rc, "ok": rc == 0, "detail": text}


def test_rw_cycle() -> dict:
    with tempfile.TemporaryDirectory(prefix="cmf-smoke-") as tmpdir:
        tmpdir = Path(tmpdir)
        export_path = tmpdir / "export.json"
        marker = f"smoke-installed-{int(time.time())}"
        r1 = run_cmd([sys.executable, str(CLI), "save", "--title", "smoke installed", "--content", marker, "--tags", "smoke,installed"])
        r2 = run_cmd([sys.executable, str(CLI), "semantic", marker, "--limit", "3"])
        r3 = run_cmd([sys.executable, str(CLI), "export", marker, str(export_path), "--limit", "10"])
        export_payload = json.loads(export_path.read_text()) if export_path.exists() else {}
        r4 = run_cmd([sys.executable, str(CLI), "import", str(export_path), "--no-sync"]) if export_path.exists() else (1, "", "no export")
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


def test_viewer() -> dict:
    port = 38880
    proc = subprocess.Popen(
        [sys.executable, str(CLI), "serve", "--host", "127.0.0.1", "--port", str(port)],
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


def main() -> int:
    results = [
        test_health(),
        test_quality_gate(),
        test_rw_cycle(),
        test_viewer(),
    ]
    failed = [item for item in results if not item["ok"]]
    print(json.dumps({"install_root": str(INSTALL_ROOT), "results": results}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
