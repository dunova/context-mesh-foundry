#!/usr/bin/env python3
"""Lightweight quality gate for the standalone context system."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTEXT_CLI = REPO_ROOT / "scripts" / "context_cli.py"


@dataclass
class CaseResult:
    name: str
    passed: bool
    detail: str
    elapsed_sec: float


def session_db_path(storage_root: Path) -> Path:
    return storage_root / "index" / "session_index.db"


def prepare_fixture_home(home: Path) -> None:
    codex_root = home / ".codex" / "sessions" / "2026" / "03" / "25"
    codex_root.mkdir(parents=True, exist_ok=True)
    (codex_root / "sample.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "gate-codex",
                            "cwd": "/tmp/contextgo-gate",
                            "timestamp": "2026-03-25T00:00:00Z",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "NotebookLM gate validation"},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    claude_root = home / ".claude" / "projects" / "gate"
    claude_root.mkdir(parents=True, exist_ok=True)
    (claude_root / "session.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "sessionId": "gate-claude",
                        "cwd": "/tmp/contextgo-gate",
                        "timestamp": "2026-03-25T01:00:00Z",
                    }
                ),
                json.dumps({"type": "user", "message": {"content": "NotebookLM claude gate"}}),
            ]
        ),
        encoding="utf-8",
    )

    (home / ".codex").mkdir(parents=True, exist_ok=True)
    (home / ".codex" / "history.jsonl").write_text(
        json.dumps({"message": "NotebookLM codex history"}, ensure_ascii=False),
        encoding="utf-8",
    )

    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "history.jsonl").write_text(
        json.dumps({"text": "NotebookLM claude history"}, ensure_ascii=False),
        encoding="utf-8",
    )

    (home / ".local" / "state" / "opencode").mkdir(parents=True, exist_ok=True)
    (home / ".local" / "state" / "opencode" / "prompt-history.jsonl").write_text(
        json.dumps([{"prompt": "NotebookLM opencode history"}], ensure_ascii=False),
        encoding="utf-8",
    )

    (home / ".zsh_history").write_text("pwd\nNotebookLM zsh history\n", encoding="utf-8")
    (home / ".bash_history").write_text("ls\nNotebookLM bash history\n", encoding="utf-8")


def run_cmd(args: list[str], env: dict[str, str], timeout: int = 20) -> tuple[int, str, str]:
    proc = subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def case_health(env: dict[str, str]) -> CaseResult:
    t0 = time.time()
    rc, out, err = run_cmd(["python3", str(CONTEXT_CLI), "health"], env)
    payload = {}
    text = out or err
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        payload = json.loads(text[start : end + 1])
    ok = rc == 0 and bool(payload.get("all_ok"))
    detail = f"rc={rc}, all_ok={payload.get('all_ok')}, mode={payload.get('remote_sync_policy', {}).get('mode')}"
    return CaseResult("health", ok, detail, time.time() - t0)


def case_save_and_readback(env: dict[str, str]) -> CaseResult:
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
        ],
        env,
    )
    rc_sem, out_sem, _ = run_cmd(["python3", str(CONTEXT_CLI), "semantic", marker, "--limit", "3"], env)
    ok = rc_save == 0 and rc_sem == 0 and marker in out_sem
    detail = f"save_rc={rc_save}, semantic_rc={rc_sem}, semantic_has_marker={marker in out_sem}"
    return CaseResult("save-readback", ok, detail, time.time() - t0)


def case_session_index_sources(env: dict[str, str], storage_root: Path) -> CaseResult:
    t0 = time.time()
    run_cmd(["python3", str(CONTEXT_CLI), "health"], env, timeout=60)
    session_db = session_db_path(storage_root)
    if not session_db.exists():
        return CaseResult("session-index-sources", False, f"db missing: {session_db}", time.time() - t0)
    conn = sqlite3.connect(str(session_db))
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


def case_local_search(env: dict[str, str]) -> CaseResult:
    t0 = time.time()
    rc, out, err = run_cmd(
        ["python3", str(CONTEXT_CLI), "search", "NotebookLM", "--limit", "3", "--literal"],
        env,
        timeout=60,
    )
    text = (out or err).strip()
    ok = rc == 0 and "Found" in text
    detail = text[:200]
    return CaseResult("local-search", ok, detail, time.time() - t0)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="contextgo-gate-") as tmpdir:
        fake_home = Path(tmpdir)
        storage_root = fake_home / ".unified_context_data"
        prepare_fixture_home(fake_home)
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(fake_home),
                "UNIFIED_CONTEXT_STORAGE_ROOT": str(storage_root),
                "CONTEXT_MESH_STORAGE_ROOT": str(storage_root),
                "OPENVIKING_STORAGE_ROOT": str(storage_root),
                "CMF_SESSION_SYNC_MIN_INTERVAL_SEC": "0",
                "CONTEXT_MESH_SOURCE_CACHE_TTL_SEC": "0",
            }
        )
        cases = [
            case_health(env),
            case_save_and_readback(env),
            case_session_index_sources(env, storage_root),
            case_local_search(env),
        ]
    failed = [c for c in cases if not c.passed]
    for case in cases:
        status = "PASS" if case.passed else "FAIL"
        print(f"[{status}] {case.name} ({case.elapsed_sec:.2f}s) - {case.detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
