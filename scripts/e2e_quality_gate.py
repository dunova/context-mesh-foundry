#!/usr/bin/env python3
"""Lightweight quality gate for the standalone context system."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTEXT_CLI = REPO_ROOT / "scripts" / "context_cli.py"
SESSION_DB = Path.home() / ".unified_context_data" / "index" / "session_index.db"


@dataclass
class CaseResult:
    name: str
    passed: bool
    detail: str
    elapsed_sec: float


def run_cmd(args: list[str], timeout: int = 20) -> tuple[int, str, str]:
    proc = subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def case_health() -> CaseResult:
    t0 = time.time()
    rc, out, err = run_cmd(["python3", str(CONTEXT_CLI), "health"])
    payload = {}
    text = out or err
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        payload = json.loads(text[start : end + 1])
    ok = rc == 0 and bool(payload.get("all_ok"))
    detail = f"rc={rc}, all_ok={payload.get('all_ok')}, mode={payload.get('remote_sync_policy', {}).get('mode')}"
    return CaseResult("health", ok, detail, time.time() - t0)


def case_save_and_readback() -> CaseResult:
    t0 = time.time()
    marker = f"gate-marker-{int(time.time())}"
    rc_save, out_save, _ = run_cmd(
        [
            "python3",
            str(CONTEXT_CLI),
            "save",
            "--title",
            "quality-gate-marker",
            "--content",
            marker,
            "--tags",
            "gate,test",
        ]
    )
    rc_sem, out_sem, _ = run_cmd(["python3", str(CONTEXT_CLI), "semantic", marker, "--limit", "3"])
    ok = rc_save == 0 and rc_sem == 0 and marker in out_sem
    detail = f"save_rc={rc_save}, semantic_rc={rc_sem}, semantic_has_marker={marker in out_sem}"
    return CaseResult("save-readback", ok, detail, time.time() - t0)


def case_session_index_sources() -> CaseResult:
    t0 = time.time()
    run_cmd(["python3", str(CONTEXT_CLI), "health"], timeout=60)
    if not SESSION_DB.exists():
        return CaseResult("session-index-sources", False, f"db missing: {SESSION_DB}", time.time() - t0)
    conn = sqlite3.connect(str(SESSION_DB))
    try:
        rows = conn.execute("select source_type, count(*) from session_documents group by source_type").fetchall()
    finally:
        conn.close()
    sources = {row[0]: row[1] for row in rows}
    required = {"codex_session", "claude_session", "shell_zsh"}
    missing = sorted(required - set(sources))
    ok = not missing
    detail = f"sources={sources}, missing={missing}"
    return CaseResult("session-index-sources", ok, detail, time.time() - t0)


def case_local_search() -> CaseResult:
    t0 = time.time()
    rc, out, err = run_cmd(["python3", str(CONTEXT_CLI), "search", "NotebookLM", "--limit", "3", "--literal"], timeout=60)
    text = (out or err).strip()
    ok = rc == 0 and "Found" in text
    detail = text[:200]
    return CaseResult("local-search", ok, detail, time.time() - t0)


def main() -> int:
    cases = [
        case_health(),
        case_save_and_readback(),
        case_session_index_sources(),
        case_local_search(),
    ]
    failed = [c for c in cases if not c.passed]
    for case in cases:
        status = "PASS" if case.passed else "FAIL"
        print(f"[{status}] {case.name} ({case.elapsed_sec:.2f}s) - {case.detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
