#!/usr/bin/env python3
"""Unit tests for context_maintenance module."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import context_maintenance  # noqa: E402
from context_maintenance import (  # noqa: E402
    collect_local_session_files,
    enqueue_missing,
    fetch_existing_session_paths,
    parse_args,
    repair_queue,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    session_file_path TEXT,
    session_title TEXT
);
CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS turn_content (id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT,
    dedupe_key TEXT UNIQUE,
    payload TEXT,
    status TEXT,
    priority INTEGER DEFAULT 100,
    attempts INTEGER DEFAULT 0,
    next_run_at TEXT,
    reschedule INTEGER DEFAULT 0,
    locked_until TEXT,
    locked_by TEXT,
    last_error TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


def _make_db() -> tuple[sqlite3.Connection, sqlite3.Cursor]:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    return conn, conn.cursor()


# ---------------------------------------------------------------------------
# Tests: parse_args
# ---------------------------------------------------------------------------


class TestParseArgs(unittest.TestCase):
    def test_defaults(self) -> None:
        args = parse_args([])
        self.assertEqual(args.db, "~/.aline/db/aline.db")
        self.assertEqual(args.max_enqueue, 2000)
        self.assertEqual(args.stale_minutes, 15)
        self.assertFalse(args.dry_run)
        self.assertFalse(args.repair_queue)
        self.assertFalse(args.enqueue_missing)

    def test_dry_run_flag(self) -> None:
        args = parse_args(["--dry-run"])
        self.assertTrue(args.dry_run)

    def test_repair_queue_flag(self) -> None:
        args = parse_args(["--repair-queue"])
        self.assertTrue(args.repair_queue)

    def test_enqueue_missing_flag(self) -> None:
        args = parse_args(["--enqueue-missing"])
        self.assertTrue(args.enqueue_missing)

    def test_custom_max_enqueue(self) -> None:
        args = parse_args(["--max-enqueue", "500"])
        self.assertEqual(args.max_enqueue, 500)

    def test_custom_stale_minutes(self) -> None:
        args = parse_args(["--stale-minutes", "30"])
        self.assertEqual(args.stale_minutes, 30)

    def test_include_subagents_flag(self) -> None:
        args = parse_args(["--include-subagents"])
        self.assertTrue(args.include_subagents)


# ---------------------------------------------------------------------------
# Tests: collect_local_session_files
# ---------------------------------------------------------------------------


class TestCollectLocalSessionFiles(unittest.TestCase):
    def test_empty_dirs_return_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / "codex"
            claude_root = Path(tmp) / "claude"
            result = collect_local_session_files(codex_root, claude_root, False)
        self.assertEqual(result, [])

    def test_collects_codex_jsonl_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / "codex"
            codex_root.mkdir()
            (codex_root / "session1.jsonl").write_text("{}", encoding="utf-8")
            (codex_root / "session2.jsonl").write_text("{}", encoding="utf-8")
            claude_root = Path(tmp) / "claude"
            result = collect_local_session_files(codex_root, claude_root, False)
        self.assertEqual(len(result), 2)
        source_types = {item[0] for item in result}
        self.assertEqual(source_types, {"codex"})

    def test_collects_claude_jsonl_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / "codex"
            claude_root = Path(tmp) / "claude"
            claude_root.mkdir()
            (claude_root / "sess.jsonl").write_text("{}", encoding="utf-8")
            result = collect_local_session_files(codex_root, claude_root, False)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "claude")

    def test_skips_subagents_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claude_root = Path(tmp) / "claude"
            subagent_dir = claude_root / "subagents"
            subagent_dir.mkdir(parents=True)
            (subagent_dir / "sub.jsonl").write_text("{}", encoding="utf-8")
            codex_root = Path(tmp) / "codex"
            result = collect_local_session_files(codex_root, claude_root, False)
        self.assertEqual(result, [])

    def test_includes_subagents_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claude_root = Path(tmp) / "claude"
            subagent_dir = claude_root / "subagents"
            subagent_dir.mkdir(parents=True)
            (subagent_dir / "sub.jsonl").write_text("{}", encoding="utf-8")
            codex_root = Path(tmp) / "codex"
            result = collect_local_session_files(codex_root, claude_root, True)
        self.assertEqual(len(result), 1)

    def test_session_id_is_stem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / "codex"
            codex_root.mkdir()
            (codex_root / "mysession.jsonl").write_text("{}", encoding="utf-8")
            claude_root = Path(tmp) / "claude"
            result = collect_local_session_files(codex_root, claude_root, False)
        self.assertEqual(result[0][2], "mysession")

    def test_nested_codex_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / "codex"
            nested = codex_root / "2026" / "03"
            nested.mkdir(parents=True)
            (nested / "deep.jsonl").write_text("{}", encoding="utf-8")
            claude_root = Path(tmp) / "claude"
            result = collect_local_session_files(codex_root, claude_root, False)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "codex")


# ---------------------------------------------------------------------------
# Tests: fetch_existing_session_paths
# ---------------------------------------------------------------------------


class TestFetchExistingSessionPaths(unittest.TestCase):
    def test_empty_table_returns_empty_set(self) -> None:
        conn, cur = _make_db()
        result = fetch_existing_session_paths(cur)
        conn.close()
        self.assertEqual(result, set())

    def test_returns_all_paths(self) -> None:
        conn, cur = _make_db()
        cur.execute("INSERT INTO sessions (id, session_file_path) VALUES (?, ?)", ("s1", "/tmp/a.jsonl"))
        cur.execute("INSERT INTO sessions (id, session_file_path) VALUES (?, ?)", ("s2", "/tmp/b.jsonl"))
        result = fetch_existing_session_paths(cur)
        conn.close()
        self.assertEqual(result, {"/tmp/a.jsonl", "/tmp/b.jsonl"})

    def test_ignores_null_paths(self) -> None:
        conn, cur = _make_db()
        cur.execute("INSERT INTO sessions (id, session_file_path) VALUES (?, ?)", ("s1", None))
        result = fetch_existing_session_paths(cur)
        conn.close()
        self.assertEqual(result, set())


# ---------------------------------------------------------------------------
# Tests: repair_queue
# ---------------------------------------------------------------------------


class TestRepairQueue(unittest.TestCase):
    def _insert_job(
        self,
        cur: sqlite3.Cursor,
        job_id: str,
        status: str,
        updated_at: str,
        locked_until: str | None = None,
    ) -> None:
        cur.execute(
            """INSERT INTO jobs (id, kind, dedupe_key, payload, status, updated_at, locked_until)
               VALUES (?, 'session_process', ?, '{}', ?, ?, ?)""",
            (job_id, f"key:{job_id}", status, updated_at, locked_until),
        )

    def test_dry_run_counts_but_does_not_update(self) -> None:
        conn, cur = _make_db()
        # Insert a clearly stale processing job (1970 epoch)
        self._insert_job(cur, "j1", "processing", "1970-01-01 00:00:00")
        count = repair_queue(cur, stale_minutes=15, dry_run=True)
        # Should count >= 1 stale jobs
        self.assertGreaterEqual(count, 1)
        # Job status should NOT have changed (dry run)
        row = cur.execute("SELECT status FROM jobs WHERE id='j1'").fetchone()
        conn.close()
        self.assertEqual(row[0], "processing")

    def test_release_stale_jobs(self) -> None:
        conn, cur = _make_db()
        self._insert_job(cur, "j2", "processing", "1970-01-01 00:00:00")
        count = repair_queue(cur, stale_minutes=15, dry_run=False)
        self.assertGreaterEqual(count, 1)
        row = cur.execute("SELECT status FROM jobs WHERE id='j2'").fetchone()
        conn.close()
        self.assertEqual(row[0], "queued")

    def test_fresh_job_not_released(self) -> None:
        conn, cur = _make_db()
        # A job updated just now with a future locked_until
        cur.execute(
            """INSERT INTO jobs (id, kind, dedupe_key, payload, status, updated_at, locked_until)
               VALUES ('j_fresh', 'session_process', 'key:j_fresh', '{}', 'processing',
                       datetime('now'), datetime('now', '+1 hour'))"""
        )
        count = repair_queue(cur, stale_minutes=15, dry_run=False)
        self.assertEqual(count, 0)
        row = cur.execute("SELECT status FROM jobs WHERE id='j_fresh'").fetchone()
        conn.close()
        self.assertEqual(row[0], "processing")

    def test_non_processing_jobs_untouched(self) -> None:
        conn, cur = _make_db()
        self._insert_job(cur, "jq", "queued", "1970-01-01 00:00:00")
        self._insert_job(cur, "jd", "done", "1970-01-01 00:00:00")
        repair_queue(cur, stale_minutes=15, dry_run=False)
        rows = {r[0]: r[1] for r in cur.execute("SELECT id, status FROM jobs").fetchall()}
        conn.close()
        self.assertEqual(rows["jq"], "queued")
        self.assertEqual(rows["jd"], "done")


# ---------------------------------------------------------------------------
# Tests: enqueue_missing
# ---------------------------------------------------------------------------


class TestEnqueueMissing(unittest.TestCase):
    def _make_missing(self, n: int, source_type: str = "codex") -> list:
        return [(source_type, Path(f"/fake/{i}.jsonl"), f"sid{i}") for i in range(n)]

    def test_inserts_new_jobs(self) -> None:
        conn, cur = _make_db()
        missing = self._make_missing(3)
        inserted, revived = enqueue_missing(cur, missing, max_enqueue=10, dry_run=False)
        conn.commit()
        self.assertEqual(inserted, 3)
        self.assertEqual(revived, 0)
        count = cur.execute("SELECT count(*) FROM jobs").fetchone()[0]
        conn.close()
        self.assertEqual(count, 3)

    def test_dry_run_does_not_insert(self) -> None:
        conn, cur = _make_db()
        missing = self._make_missing(3)
        inserted, revived = enqueue_missing(cur, missing, max_enqueue=10, dry_run=True)
        self.assertEqual(inserted, 3)
        count = cur.execute("SELECT count(*) FROM jobs").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_max_enqueue_limits_inserts(self) -> None:
        conn, cur = _make_db()
        missing = self._make_missing(10)
        inserted, revived = enqueue_missing(cur, missing, max_enqueue=4, dry_run=False)
        conn.commit()
        self.assertEqual(inserted, 4)
        count = cur.execute("SELECT count(*) FROM jobs").fetchone()[0]
        conn.close()
        self.assertEqual(count, 4)

    def test_revives_terminal_jobs(self) -> None:
        conn, cur = _make_db()
        # Pre-insert a "done" job for sid0
        cur.execute(
            "INSERT INTO jobs (id, kind, dedupe_key, payload, status) VALUES (?,?,?,?,?)",
            ("existing-id", "session_process", "session_process:sid0", "{}", "done"),
        )
        conn.commit()
        missing = self._make_missing(1)  # sid0
        inserted, revived = enqueue_missing(cur, missing, max_enqueue=10, dry_run=False)
        conn.commit()
        self.assertEqual(inserted, 0)
        self.assertEqual(revived, 1)
        row = cur.execute("SELECT status FROM jobs WHERE dedupe_key='session_process:sid0'").fetchone()
        conn.close()
        self.assertEqual(row[0], "queued")

    def test_skips_already_active_jobs(self) -> None:
        conn, cur = _make_db()
        # Pre-insert a "queued" job for sid0
        cur.execute(
            "INSERT INTO jobs (id, kind, dedupe_key, payload, status) VALUES (?,?,?,?,?)",
            ("existing-id2", "session_process", "session_process:sid0", "{}", "queued"),
        )
        conn.commit()
        missing = self._make_missing(1)  # sid0
        inserted, revived = enqueue_missing(cur, missing, max_enqueue=10, dry_run=False)
        conn.commit()
        self.assertEqual(inserted, 0)
        self.assertEqual(revived, 0)
        count = cur.execute("SELECT count(*) FROM jobs").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_payload_contains_session_info(self) -> None:
        conn, cur = _make_db()
        missing = [("claude", Path("/fake/mysess.jsonl"), "mysess")]
        enqueue_missing(cur, missing, max_enqueue=10, dry_run=False)
        conn.commit()
        row = cur.execute("SELECT payload FROM jobs").fetchone()
        conn.close()
        payload = json.loads(row[0])
        self.assertEqual(payload["session_id"], "mysess")
        self.assertEqual(payload["session_type"], "claude")
        self.assertEqual(payload["session_file_path"], "/fake/mysess.jsonl")

    def test_zero_max_enqueue_inserts_nothing(self) -> None:
        conn, cur = _make_db()
        missing = self._make_missing(5)
        inserted, revived = enqueue_missing(cur, missing, max_enqueue=0, dry_run=False)
        conn.close()
        self.assertEqual(inserted, 0)
        self.assertEqual(revived, 0)


# ---------------------------------------------------------------------------
# Tests: main() — via CLI args, requires real temp db
# ---------------------------------------------------------------------------


class TestMainFunction(unittest.TestCase):
    def _create_temp_db(self) -> Path:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_name = tmp.name
        conn = sqlite3.connect(tmp_name)
        conn.executescript(_DDL)
        conn.close()
        return Path(tmp_name)

    def test_main_with_nonexistent_db_returns_1(self) -> None:
        result = context_maintenance.main(["--db", "/nonexistent/path/db.db"])
        self.assertEqual(result, 1)

    def test_main_with_valid_db_returns_0(self) -> None:
        db_path = self._create_temp_db()
        try:
            result = context_maintenance.main(["--db", str(db_path)])
        finally:
            db_path.unlink(missing_ok=True)
        self.assertEqual(result, 0)

    def test_main_repair_queue_dry_run(self) -> None:
        db_path = self._create_temp_db()
        try:
            result = context_maintenance.main(["--db", str(db_path), "--repair-queue", "--dry-run"])
        finally:
            db_path.unlink(missing_ok=True)
        self.assertEqual(result, 0)

    def test_main_enqueue_missing_dry_run(self) -> None:
        db_path = self._create_temp_db()
        try:
            result = context_maintenance.main(["--db", str(db_path), "--enqueue-missing", "--dry-run"])
        finally:
            db_path.unlink(missing_ok=True)
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
