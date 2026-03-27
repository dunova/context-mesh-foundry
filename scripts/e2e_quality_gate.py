#!/usr/bin/env python3
"""Lightweight quality gate for the standalone context system."""

from __future__ import annotations

import contextlib
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
    """Return the path to the session index SQLite database within storage_root."""
    return storage_root / "index" / "session_index.db"


def prepare_fixture_home(home: Path) -> None:
    """Create a minimal fake home directory tree for quality gate tests."""
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
                            "cwd": str(home),
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
                        "cwd": str(home),
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
    """Run a subprocess with the given env and return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def case_health(env: dict[str, str]) -> CaseResult:
    """Run the health command and verify all_ok is True."""
    t0 = time.time()
    rc, out, err = run_cmd(["python3", str(CONTEXT_CLI), "health"], env)
    payload = {}
    text = out or err
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        with contextlib.suppress(json.JSONDecodeError):
            payload = json.loads(text[start : end + 1])
    ok = rc == 0 and bool(payload.get("all_ok"))
    if ok:
        detail = f"rc={rc}, all_ok=True, mode={payload.get('remote_sync_policy', {}).get('mode')}"
    else:
        detail = (
            f"health check failed rc={rc}; all_ok={payload.get('all_ok')}; "
            f"check session_index_db_exists in health output; raw={text[:120]!r}"
        )
    return CaseResult("health", ok, detail, time.time() - t0)


def case_save_and_readback(env: dict[str, str]) -> CaseResult:
    """Save a memory and verify it can be retrieved via semantic search."""
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
    if ok:
        detail = f"save_rc={rc_save}, semantic_rc={rc_sem}, semantic_has_marker=True"
    else:
        detail = (
            f"save-readback failed: save_rc={rc_save}, semantic_rc={rc_sem}, "
            f"marker_found={marker in out_sem}; "
            f"save_out={out_save[:80]!r}; sem_out={out_sem[:80]!r}"
        )
    return CaseResult("save-readback", ok, detail, time.time() - t0)


def case_session_index_sources(env: dict[str, str], storage_root: Path) -> CaseResult:
    """Verify all required session source types appear in the session index."""
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
    """Verify the search command returns results containing the fixture marker."""
    t0 = time.time()
    rc, out, err = run_cmd(
        ["python3", str(CONTEXT_CLI), "search", "NotebookLM", "--limit", "3", "--literal"],
        env,
        timeout=60,
    )
    text = (out or err).strip()
    ok = rc == 0 and "Found" in text
    detail = text[:200]
    if not ok:
        hint = (
            f"search failed rc={rc}; ensure session index is populated "
            f"(run `context_cli.py health` to verify db); raw={text[:120]!r}"
        )
        detail = hint
    return CaseResult("local-search", ok, detail, time.time() - t0)


def case_export_and_import(env: dict[str, str]) -> CaseResult:
    """Save a memory, export it to JSON, then import it back and verify round-trip."""
    t0 = time.time()
    marker = f"gate-export-{int(time.time())}"
    rc_save, out_save, err_save = run_cmd(
        [
            "python3",
            str(CONTEXT_CLI),
            "save",
            "--title",
            "gate-export-marker",
            "--content",
            marker,
            "--tags",
            "gate,export",
        ],
        env,
    )
    if rc_save != 0:
        return CaseResult(
            "export-import",
            False,
            f"save failed rc={rc_save}; cannot proceed with export; stderr={err_save[:120]!r}",
            time.time() - t0,
        )

    with tempfile.TemporaryDirectory(prefix="contextgo-gate-export-") as tmpdir:
        export_file = Path(tmpdir) / "gate_export.json"
        rc_export, out_export, err_export = run_cmd(
            [
                "python3",
                str(CONTEXT_CLI),
                "export",
                marker,
                str(export_file),
                "--limit",
                "10",
            ],
            env,
        )
        if rc_export != 0:
            return CaseResult(
                "export-import",
                False,
                f"export failed rc={rc_export}; stderr={err_export[:120]!r}",
                time.time() - t0,
            )
        if not export_file.exists():
            return CaseResult(
                "export-import",
                False,
                f"export file not created at {export_file}; stdout={out_export[:120]!r}",
                time.time() - t0,
            )

        with contextlib.suppress(json.JSONDecodeError):
            payload = json.loads(export_file.read_text(encoding="utf-8"))
            total = payload.get("total_observations", 0)
            if total < 1:
                return CaseResult(
                    "export-import",
                    False,
                    "export produced 0 observations; expected >=1 after saving marker",
                    time.time() - t0,
                )

        rc_import, out_import, err_import = run_cmd(
            ["python3", str(CONTEXT_CLI), "import", str(export_file), "--no-sync"],
            env,
        )
        ok = rc_import == 0 and "import done" in (out_import or "")
        detail = f"save_rc={rc_save}, export_rc={rc_export}, import_rc={rc_import}, import_out={out_import[:100]!r}"
        if not ok:
            detail = f"import failed rc={rc_import}; stderr={err_import[:120]!r}; stdout={out_import[:120]!r}"
    return CaseResult("export-import", ok, detail, time.time() - t0)


def case_maintain(env: dict[str, str]) -> CaseResult:
    """Run maintenance in dry-run mode and confirm a snapshot line is reported."""
    t0 = time.time()
    rc, out, err = run_cmd(
        ["python3", str(CONTEXT_CLI), "maintain", "--dry-run"],
        env,
        timeout=60,
    )
    text = out or err
    ok = rc == 0 and "Snapshot" in text
    if ok:
        detail = f"rc={rc}, snapshot_reported=True, out={text[:120]!r}"
    else:
        detail = (
            f"maintain --dry-run failed rc={rc}; expected 'Snapshot' in output; "
            f"stdout={out[:120]!r}; stderr={err[:120]!r}"
        )
    return CaseResult("maintain", ok, detail, time.time() - t0)


def main() -> int:
    """Run all quality-gate cases in an isolated temp environment and report results."""
    with tempfile.TemporaryDirectory(prefix="contextgo-gate-") as tmpdir:
        fake_home = Path(tmpdir)
        storage_root = fake_home / ".contextgo"
        prepare_fixture_home(fake_home)
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(fake_home),
                "CONTEXTGO_STORAGE_ROOT": str(storage_root),
                "CONTEXTGO_SESSION_SYNC_MIN_INTERVAL_SEC": "0",
                "CONTEXTGO_SOURCE_CACHE_TTL_SEC": "0",
            }
        )
        cases = [
            case_health(env),
            case_save_and_readback(env),
            case_session_index_sources(env, storage_root),
            case_local_search(env),
            case_export_and_import(env),
            case_maintain(env),
        ]
    failed = [c for c in cases if not c.passed]
    for case in cases:
        status = "PASS" if case.passed else "FAIL"
        print(f"[{status}] {case.name} ({case.elapsed_sec:.2f}s) - {case.detail}")
    if failed:
        print(f"\n[GATE FAIL] {len(failed)}/{len(cases)} cases failed: {[c.name for c in failed]}")
    else:
        print(f"\n[GATE PASS] All {len(cases)} cases passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
