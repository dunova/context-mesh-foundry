#!/usr/bin/env python3
"""Unit tests for context_maintenance module."""

from __future__ import annotations

import io
import json
import runpy
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
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
        self.assertEqual(args.db, "~/.contextgo/db/contextgo.db")
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


class TestMainDryRunNoDB(unittest.TestCase):
    """Tests for main() when DB does not exist but --dry-run is set (lines 354-364)."""

    def test_dry_run_no_db_returns_0(self) -> None:
        """--dry-run with missing DB should return 0 (lines 354-364)."""
        result = context_maintenance.main(["--db", "/nonexistent/path/db.db", "--dry-run"])
        self.assertEqual(result, 0)

    def test_dry_run_no_db_prints_snapshot(self) -> None:
        """--dry-run with missing DB should print a snapshot header."""
        with unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            context_maintenance.main(["--db", "/nonexistent/path/db.db", "--dry-run"])
            output = mock_out.getvalue()
        self.assertIn("=== Snapshot ===", output)
        self.assertIn("sessions=0", output)
        self.assertIn("dry_run: no DB changes applied (database missing, treated as empty)", output)

    def test_dry_run_no_db_with_codex_files(self) -> None:
        """--dry-run no DB counts codex files and reports missing_codex correctly."""
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / "codex"
            codex_root.mkdir()
            (codex_root / "s1.jsonl").write_text("{}", encoding="utf-8")
            (codex_root / "s2.jsonl").write_text("{}", encoding="utf-8")
            with unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                result = context_maintenance.main(
                    [
                        "--db",
                        "/nonexistent/db.db",
                        "--dry-run",
                        "--codex-root",
                        str(codex_root),
                    ]
                )
            output = mock_out.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("missing_codex=2", output)

    def test_dry_run_no_db_with_claude_files(self) -> None:
        """--dry-run no DB counts claude files and reports missing_claude_main correctly."""
        with tempfile.TemporaryDirectory() as tmp:
            claude_root = Path(tmp) / "claude"
            claude_root.mkdir()
            (claude_root / "sess.jsonl").write_text("{}", encoding="utf-8")
            with unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                result = context_maintenance.main(
                    [
                        "--db",
                        "/nonexistent/db.db",
                        "--dry-run",
                        "--claude-root",
                        str(claude_root),
                    ]
                )
            output = mock_out.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("missing_claude_main=1", output)

    def test_dry_run_no_db_local_files_count(self) -> None:
        """--dry-run no DB shows correct local_files total."""
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / "codex"
            codex_root.mkdir()
            (codex_root / "a.jsonl").write_text("{}", encoding="utf-8")
            claude_root = Path(tmp) / "claude"
            claude_root.mkdir()
            (claude_root / "b.jsonl").write_text("{}", encoding="utf-8")
            with unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                result = context_maintenance.main(
                    [
                        "--db",
                        "/nonexistent/db.db",
                        "--dry-run",
                        "--codex-root",
                        str(codex_root),
                        "--claude-root",
                        str(claude_root),
                    ]
                )
            output = mock_out.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("local_files=2", output)


class TestMainOperationalError(unittest.TestCase):
    """Tests for the sqlite3.OperationalError branch in main() (lines 370-372)."""

    def test_operational_error_on_connect_returns_1(self) -> None:
        """When sqlite3.connect raises OperationalError, main() returns 1."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create a real file so db_path.exists() passes, but it's a directory
            # — sqlite3.connect will raise OperationalError.
            db_path = Path(tmp) / "notadb"
            db_path.mkdir()
            result = context_maintenance.main(["--db", str(db_path)])
        self.assertEqual(result, 1)

    def test_operational_error_prints_error_message(self) -> None:
        """When sqlite3.connect raises OperationalError, an error is printed to stderr."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "notadb"
            db_path.mkdir()
            with unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as mock_err:
                context_maintenance.main(["--db", str(db_path)])
                err_output = mock_err.getvalue()
        self.assertIn("ERROR", err_output)

    def test_operational_error_via_mock(self) -> None:
        """Mock sqlite3.connect to raise OperationalError directly."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name

        with unittest.mock.patch("sqlite3.connect", side_effect=sqlite3.OperationalError("mocked error")):
            result = context_maintenance.main(["--db", tmp_path])
        self.assertEqual(result, 1)


class TestMainDatabaseError(unittest.TestCase):
    """Tests for the sqlite3.DatabaseError except branch (lines 409-412)."""

    def _create_temp_db(self) -> Path:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_name = tmp.name
        conn = sqlite3.connect(tmp_name)
        conn.executescript(_DDL)
        conn.close()
        return Path(tmp_name)

    def test_database_error_returns_1(self) -> None:
        """When a DatabaseError occurs during operations, main() returns 1."""
        db_path = self._create_temp_db()
        try:
            with unittest.mock.patch(
                "context_maintenance.fetch_existing_session_paths",
                side_effect=sqlite3.DatabaseError("simulated db error"),
            ):
                result = context_maintenance.main(["--db", str(db_path)])
        finally:
            db_path.unlink(missing_ok=True)
        self.assertEqual(result, 1)

    def test_database_error_prints_to_stderr(self) -> None:
        """When a DatabaseError occurs, the error message is printed to stderr."""
        db_path = self._create_temp_db()
        try:
            with (
                unittest.mock.patch(
                    "context_maintenance.fetch_existing_session_paths",
                    side_effect=sqlite3.DatabaseError("simulated db error"),
                ),
                unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as mock_err,
            ):
                context_maintenance.main(["--db", str(db_path)])
                err_output = mock_err.getvalue()
        finally:
            db_path.unlink(missing_ok=True)
        self.assertIn("ERROR: database error", err_output)


class TestMainWithExistingPaths(unittest.TestCase):
    """Tests for the 'path already in existing_paths → continue' branch (line 385)."""

    def _create_temp_db(self) -> Path:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_name = tmp.name
        conn = sqlite3.connect(tmp_name)
        conn.executescript(_DDL)
        conn.close()
        return Path(tmp_name)

    def test_skips_paths_already_in_db(self) -> None:
        """main() skips local files already recorded in the sessions table."""
        with tempfile.TemporaryDirectory() as tmp:
            # Resolve the temp dir so the path stored in the DB matches the
            # resolved path that collect_local_session_files() produces.
            # On macOS ``/tmp`` is a symlink to ``/private/tmp``, causing a
            # mismatch without this resolve().
            codex_root = Path(tmp).resolve() / "codex"
            codex_root.mkdir()
            session_file = codex_root / "already.jsonl"
            session_file.write_text("{}", encoding="utf-8")
            # Use a non-existent claude_root so no system files are picked up.
            claude_root = Path(tmp).resolve() / "no_claude"

            db_path = self._create_temp_db()
            try:
                # Pre-record the file in the DB so it appears in existing_paths.
                conn = sqlite3.connect(str(db_path))
                conn.execute(
                    "INSERT INTO sessions (id, session_file_path) VALUES (?, ?)",
                    ("s-pre", str(session_file)),
                )
                conn.commit()
                conn.close()

                with unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                    result = context_maintenance.main(
                        [
                            "--db",
                            str(db_path),
                            "--enqueue-missing",
                            "--codex-root",
                            str(codex_root),
                            "--claude-root",
                            str(claude_root),
                        ]
                    )
                output = mock_out.getvalue()
            finally:
                db_path.unlink(missing_ok=True)

        self.assertEqual(result, 0)
        # inserted=0 because the file was already present in DB
        self.assertIn("inserted=0", output)

    def test_counts_missing_claude_sessions(self) -> None:
        """main() increments missing_claude counter for claude-type missing sessions."""
        with tempfile.TemporaryDirectory() as tmp:
            claude_root = Path(tmp) / "claude"
            claude_root.mkdir()
            (claude_root / "new_sess.jsonl").write_text("{}", encoding="utf-8")
            # Use non-existent codex root so no system files are picked up.
            codex_root = Path(tmp) / "no_codex"

            db_path = self._create_temp_db()
            try:
                with unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                    result = context_maintenance.main(
                        [
                            "--db",
                            str(db_path),
                            "--claude-root",
                            str(claude_root),
                            "--codex-root",
                            str(codex_root),
                        ]
                    )
                output = mock_out.getvalue()
            finally:
                db_path.unlink(missing_ok=True)

        self.assertEqual(result, 0)
        # missing_claude_main should be 1
        self.assertIn("missing_claude_main=1", output)


class TestEnqueueMissingDryRunRevive(unittest.TestCase):
    """Tests for enqueue_missing dry-run revive path (line 323->325 branch)."""

    def test_dry_run_revive_terminal_job_counts_but_no_update(self) -> None:
        """dry_run=True with a terminal existing job: revived incremented, DB unchanged."""
        conn, cur = _make_db()
        # Pre-insert a terminal "failed" job.
        cur.execute(
            "INSERT INTO jobs (id, kind, dedupe_key, payload, status) VALUES (?,?,?,?,?)",
            ("j-fail", "session_process", "session_process:drysid", "{}", "failed"),
        )
        conn.commit()
        missing = [("codex", Path("/fake/drysid.jsonl"), "drysid")]
        inserted, revived = enqueue_missing(cur, missing, max_enqueue=10, dry_run=True)
        self.assertEqual(inserted, 0)
        self.assertEqual(revived, 1)
        # Status must remain "failed" because dry_run skips the UPDATE.
        row = cur.execute("SELECT status FROM jobs WHERE id='j-fail'").fetchone()
        conn.close()
        self.assertEqual(row[0], "failed")

    def test_dry_run_revive_error_status(self) -> None:
        """dry_run=True with 'error' terminal status also increments revived."""
        conn, cur = _make_db()
        cur.execute(
            "INSERT INTO jobs (id, kind, dedupe_key, payload, status) VALUES (?,?,?,?,?)",
            ("j-err", "session_process", "session_process:errsid", "{}", "error"),
        )
        conn.commit()
        missing = [("codex", Path("/fake/errsid.jsonl"), "errsid")]
        inserted, revived = enqueue_missing(cur, missing, max_enqueue=10, dry_run=True)
        self.assertEqual(revived, 1)
        row = cur.execute("SELECT status FROM jobs WHERE id='j-err'").fetchone()
        conn.close()
        self.assertEqual(row[0], "error")


class TestMainClaudeMissingCount(unittest.TestCase):
    """Tests exercising the elif _SOURCE_CLAUDE branch in main() (line 388)."""

    def _create_temp_db(self) -> Path:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_name = tmp.name
        conn = sqlite3.connect(tmp_name)
        conn.executescript(_DDL)
        conn.close()
        return Path(tmp_name)

    def test_mixed_sources_both_counters_incremented(self) -> None:
        """Both missing_codex and missing_claude_main reported correctly."""
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / "codex"
            codex_root.mkdir()
            (codex_root / "csess.jsonl").write_text("{}", encoding="utf-8")
            claude_root = Path(tmp) / "claude"
            claude_root.mkdir()
            (claude_root / "clsess.jsonl").write_text("{}", encoding="utf-8")

            db_path = self._create_temp_db()
            try:
                with unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                    result = context_maintenance.main(
                        [
                            "--db",
                            str(db_path),
                            "--codex-root",
                            str(codex_root),
                            "--claude-root",
                            str(claude_root),
                        ]
                    )
                output = mock_out.getvalue()
            finally:
                db_path.unlink(missing_ok=True)

        self.assertEqual(result, 0)
        self.assertIn("missing_codex=1", output)
        self.assertIn("missing_claude_main=1", output)

    def test_unknown_source_type_not_counted(self) -> None:
        """A session with an unknown stype falls through both if/elif branches (389->383)."""
        with tempfile.TemporaryDirectory() as tmp:
            # Empty codex and claude roots so only the mocked item appears.
            codex_root = Path(tmp) / "no_codex"
            claude_root = Path(tmp) / "no_claude"
            fake_item = [("other_source", Path(tmp) / "s.jsonl", "s")]

            db_path = self._create_temp_db()
            try:
                with (
                    unittest.mock.patch(
                        "context_maintenance.collect_local_session_files",
                        return_value=fake_item,
                    ),
                    unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out,
                ):
                    result = context_maintenance.main(
                        [
                            "--db",
                            str(db_path),
                            "--codex-root",
                            str(codex_root),
                            "--claude-root",
                            str(claude_root),
                        ]
                    )
                output = mock_out.getvalue()
            finally:
                db_path.unlink(missing_ok=True)

        self.assertEqual(result, 0)
        # Neither counter is incremented — both remain 0.
        self.assertIn("missing_codex=0", output)
        self.assertIn("missing_claude_main=0", output)


class TestMainEntryPoint(unittest.TestCase):
    """Test the __main__ block (line 420)."""

    def test_main_entry_point_via_module_run(self) -> None:
        """Verify __name__ == '__main__' path raises SystemExit wrapping main()."""
        # We simulate what happens when the module is run directly.
        # Import the module's main, confirm SystemExit is raised with int code.
        with unittest.mock.patch("context_maintenance.main", return_value=0) as mock_main:
            with self.assertRaises(SystemExit) as ctx:
                raise SystemExit(mock_main())
        self.assertEqual(ctx.exception.code, 0)

    def test_main_entry_point_error_code(self) -> None:
        """SystemExit propagates a non-zero return code from main()."""
        with unittest.mock.patch("context_maintenance.main", return_value=1) as mock_main:
            with self.assertRaises(SystemExit) as ctx:
                raise SystemExit(mock_main())
        self.assertEqual(ctx.exception.code, 1)

    def test_module_run_as_script_exits_0(self) -> None:
        """Running the module as __main__ via subprocess covers line 420."""
        repo_root = Path(__file__).resolve().parents[1]
        scripts_dir = str(repo_root / "scripts")
        module_path = str(repo_root / "scripts" / "context_maintenance.py")
        with tempfile.TemporaryDirectory() as tmp:
            # Ensure no real DB or session dirs interfere.
            fake_db = Path(tmp) / "no.db"
            proc = subprocess.run(
                [sys.executable, module_path, "--db", str(fake_db), "--dry-run"],
                capture_output=True,
                text=True,
                cwd=scripts_dir,
                check=False,
            )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("=== Snapshot ===", proc.stdout)

    def test_module_runpy_covers_main_block(self) -> None:
        """Use runpy.run_path to execute the __main__ block in-process for coverage."""
        module_path = str(Path(__file__).resolve().parents[1] / "scripts" / "context_maintenance.py")
        with tempfile.TemporaryDirectory() as tmp:
            fake_db = str(Path(tmp) / "no.db")
            saved_argv = sys.argv[:]
            sys.argv = [module_path, "--db", fake_db, "--dry-run"]
            try:
                with self.assertRaises(SystemExit) as ctx:
                    runpy.run_path(module_path, run_name="__main__")
            finally:
                sys.argv = saved_argv
        # main() returns 0 for --dry-run with missing DB.
        self.assertEqual(ctx.exception.code, 0)


# ---------------------------------------------------------------------------
# Edge-case Tests: R6 hardening
# ---------------------------------------------------------------------------


class TestRepairQueueEmptyDatabase(unittest.TestCase):
    """Edge cases: repair_queue and enqueue_missing on an empty database."""

    def test_repair_queue_on_empty_jobs_table_returns_zero(self) -> None:
        """repair_queue on a DB with no jobs returns 0 (nothing to release)."""
        conn, cur = _make_db()
        count = repair_queue(cur, stale_minutes=15, dry_run=False)
        conn.close()
        self.assertEqual(count, 0)

    def test_repair_queue_dry_run_on_empty_returns_zero(self) -> None:
        """repair_queue dry_run on empty DB returns 0."""
        conn, cur = _make_db()
        count = repair_queue(cur, stale_minutes=15, dry_run=True)
        conn.close()
        self.assertEqual(count, 0)

    def test_enqueue_missing_on_empty_missing_list(self) -> None:
        """enqueue_missing with an empty missing list inserts nothing."""
        conn, cur = _make_db()
        inserted, revived = enqueue_missing(cur, [], max_enqueue=100, dry_run=False)
        conn.close()
        self.assertEqual(inserted, 0)
        self.assertEqual(revived, 0)

    def test_fetch_existing_paths_on_empty_sessions(self) -> None:
        """fetch_existing_session_paths on an empty sessions table returns empty set."""
        conn, cur = _make_db()
        result = fetch_existing_session_paths(cur)
        conn.close()
        self.assertEqual(result, set())

    def test_collect_session_files_both_roots_nonexistent(self) -> None:
        """collect_local_session_files returns empty list when both roots don't exist."""
        result = collect_local_session_files(
            Path("/nonexistent/codex_root_xyz"),
            Path("/nonexistent/claude_root_xyz"),
            include_subagents=False,
        )
        self.assertEqual(result, [])

    def test_collect_session_files_empty_existing_dirs(self) -> None:
        """collect_local_session_files returns empty list for existing but empty dirs."""
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / "codex"
            claude_root = Path(tmp) / "claude"
            codex_root.mkdir()
            claude_root.mkdir()
            result = collect_local_session_files(codex_root, claude_root, include_subagents=False)
        self.assertEqual(result, [])


class TestCorruptedIndexEntries(unittest.TestCase):
    """Edge cases: corrupted / malformed DB entries."""

    def test_fetch_existing_session_paths_with_none_values_filtered(self) -> None:
        """fetch_existing_session_paths excludes NULL session_file_path entries."""
        conn, cur = _make_db()
        cur.execute("INSERT INTO sessions (id, session_file_path) VALUES (?, ?)", ("s-null", None))
        cur.execute("INSERT INTO sessions (id, session_file_path) VALUES (?, ?)", ("s-valid", "/path/to/file.jsonl"))
        result = fetch_existing_session_paths(cur)
        conn.close()
        self.assertNotIn(None, result)
        self.assertIn("/path/to/file.jsonl", result)

    def test_enqueue_missing_with_path_containing_special_chars(self) -> None:
        """enqueue_missing handles paths with special characters without crashing."""
        conn, cur = _make_db()
        special_path = Path("/tmp/session with spaces & symbols !@#$.jsonl")
        missing = [("codex", special_path, "special-session")]
        inserted, revived = enqueue_missing(cur, missing, max_enqueue=10, dry_run=False)
        conn.commit()
        self.assertEqual(inserted, 1)
        row = cur.execute("SELECT payload FROM jobs").fetchone()
        conn.close()
        payload = json.loads(row[0])
        self.assertEqual(payload["session_file_path"], str(special_path))

    def test_enqueue_missing_with_unicode_session_id(self) -> None:
        """enqueue_missing handles session IDs with Unicode characters."""
        conn, cur = _make_db()
        unicode_sid = "会话-边缘案例-2026"
        missing = [("claude", Path(f"/tmp/{unicode_sid}.jsonl"), unicode_sid)]
        inserted, revived = enqueue_missing(cur, missing, max_enqueue=10, dry_run=False)
        conn.commit()
        self.assertEqual(inserted, 1)
        row = cur.execute("SELECT payload FROM jobs").fetchone()
        conn.close()
        payload = json.loads(row[0])
        self.assertEqual(payload["session_id"], unicode_sid)

    def test_enqueue_missing_payload_source_event_field(self) -> None:
        """enqueue_missing includes source_event=manual_full_backfill in payload."""
        conn, cur = _make_db()
        missing = [("codex", Path("/tmp/s.jsonl"), "s")]
        enqueue_missing(cur, missing, max_enqueue=10, dry_run=False)
        conn.commit()
        row = cur.execute("SELECT payload FROM jobs").fetchone()
        conn.close()
        payload = json.loads(row[0])
        self.assertEqual(payload["source_event"], "manual_full_backfill")

    def test_repair_queue_with_null_locked_until(self) -> None:
        """repair_queue treats NULL locked_until as stale when updated_at is old."""
        conn, cur = _make_db()
        cur.execute(
            """INSERT INTO jobs (id, kind, dedupe_key, payload, status, updated_at, locked_until)
               VALUES ('j-null-lock', 'session_process', 'key:null', '{}', 'processing',
                       '1970-01-01 00:00:00', NULL)"""
        )
        conn.commit()
        count = repair_queue(cur, stale_minutes=15, dry_run=False)
        row = cur.execute("SELECT status FROM jobs WHERE id='j-null-lock'").fetchone()
        conn.close()
        self.assertGreaterEqual(count, 1)
        self.assertEqual(row[0], "queued")

    def test_repair_queue_releases_locked_until_in_past(self) -> None:
        """repair_queue releases jobs whose locked_until has already expired."""
        conn, cur = _make_db()
        cur.execute(
            """INSERT INTO jobs (id, kind, dedupe_key, payload, status, updated_at, locked_until)
               VALUES ('j-expired', 'session_process', 'key:expired', '{}', 'processing',
                       datetime('now'), datetime('now', '-1 hour'))"""
        )
        conn.commit()
        count = repair_queue(cur, stale_minutes=15, dry_run=False)
        row = cur.execute("SELECT status FROM jobs WHERE id='j-expired'").fetchone()
        conn.close()
        self.assertGreaterEqual(count, 1)
        self.assertEqual(row[0], "queued")


class TestConcurrentMaintenanceOperations(unittest.TestCase):
    """Edge cases: concurrent and re-entrant maintenance calls."""

    def _create_temp_db(self) -> Path:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_name = tmp.name
        conn = sqlite3.connect(tmp_name)
        conn.executescript(_DDL)
        conn.close()
        return Path(tmp_name)

    def test_enqueue_missing_idempotent_on_second_call(self) -> None:
        """Running enqueue_missing twice on the same sessions does not double-insert."""
        conn, cur = _make_db()
        missing = [("codex", Path("/tmp/sess1.jsonl"), "sess1")]
        inserted1, _ = enqueue_missing(cur, missing, max_enqueue=10, dry_run=False)
        conn.commit()
        # Second call — job already exists as 'queued' → should be skipped (not inserted/revived)
        inserted2, revived2 = enqueue_missing(cur, missing, max_enqueue=10, dry_run=False)
        conn.commit()
        count = cur.execute("SELECT count(*) FROM jobs").fetchone()[0]
        conn.close()
        self.assertEqual(inserted1, 1)
        self.assertEqual(inserted2, 0)
        self.assertEqual(revived2, 0)
        self.assertEqual(count, 1)

    def test_repair_queue_called_twice_is_idempotent(self) -> None:
        """Calling repair_queue twice releases stale jobs only once (second call returns 0)."""
        conn, cur = _make_db()
        cur.execute(
            """INSERT INTO jobs (id, kind, dedupe_key, payload, status, updated_at, locked_until)
               VALUES ('j-stale2', 'session_process', 'key:stale2', '{}', 'processing',
                       '1970-01-01 00:00:00', NULL)"""
        )
        conn.commit()
        count1 = repair_queue(cur, stale_minutes=15, dry_run=False)
        conn.commit()
        count2 = repair_queue(cur, stale_minutes=15, dry_run=False)
        conn.close()
        self.assertGreaterEqual(count1, 1)
        self.assertEqual(count2, 0)  # second call: no more 'processing' stale jobs

    def test_enqueue_missing_with_large_batch(self) -> None:
        """enqueue_missing handles a large batch (1000 items) without error."""
        conn, cur = _make_db()
        missing = [("codex", Path(f"/tmp/s{i}.jsonl"), f"s{i}") for i in range(1000)]
        inserted, revived = enqueue_missing(cur, missing, max_enqueue=1000, dry_run=False)
        conn.commit()
        count = cur.execute("SELECT count(*) FROM jobs").fetchone()[0]
        conn.close()
        self.assertEqual(inserted, 1000)
        self.assertEqual(count, 1000)

    def test_main_repair_and_enqueue_together(self) -> None:
        """Running both --repair-queue and --enqueue-missing together returns 0."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_temp_db()
            codex_root = Path(tmp) / "codex"
            codex_root.mkdir()
            (codex_root / "sess.jsonl").write_text("{}", encoding="utf-8")
            try:
                result = context_maintenance.main(
                    [
                        "--db",
                        str(db_path),
                        "--repair-queue",
                        "--enqueue-missing",
                        "--codex-root",
                        str(codex_root),
                        "--claude-root",
                        str(Path(tmp) / "no_claude"),
                    ]
                )
            finally:
                db_path.unlink(missing_ok=True)
        self.assertEqual(result, 0)

    def test_main_commit_path_writes_to_db(self) -> None:
        """Running main() without --dry-run actually commits inserts to the DB."""
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / "codex"
            codex_root.mkdir()
            (codex_root / "newfile.jsonl").write_text("{}", encoding="utf-8")
            db_path = self._create_temp_db()
            try:
                result = context_maintenance.main(
                    [
                        "--db",
                        str(db_path),
                        "--enqueue-missing",
                        "--codex-root",
                        str(codex_root),
                        "--claude-root",
                        str(Path(tmp) / "no_claude"),
                    ]
                )
                # Verify the job was actually committed
                conn = sqlite3.connect(str(db_path))
                count = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
                conn.close()
            finally:
                db_path.unlink(missing_ok=True)
        self.assertEqual(result, 0)
        self.assertEqual(count, 1)

    def test_main_dry_run_does_not_commit(self) -> None:
        """Running main() with --dry-run does NOT commit inserts to the DB."""
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / "codex"
            codex_root.mkdir()
            (codex_root / "drysess.jsonl").write_text("{}", encoding="utf-8")
            db_path = self._create_temp_db()
            try:
                result = context_maintenance.main(
                    [
                        "--db",
                        str(db_path),
                        "--enqueue-missing",
                        "--dry-run",
                        "--codex-root",
                        str(codex_root),
                        "--claude-root",
                        str(Path(tmp) / "no_claude"),
                    ]
                )
                conn = sqlite3.connect(str(db_path))
                count = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
                conn.close()
            finally:
                db_path.unlink(missing_ok=True)
        self.assertEqual(result, 0)
        self.assertEqual(count, 0)

    def test_negative_max_enqueue_inserts_nothing(self) -> None:
        """enqueue_missing with negative max_enqueue inserts nothing (max clamps to 0)."""
        conn, cur = _make_db()
        missing = [("codex", Path("/tmp/s.jsonl"), "s")]
        inserted, revived = enqueue_missing(cur, missing, max_enqueue=-5, dry_run=False)
        count = cur.execute("SELECT count(*) FROM jobs").fetchone()[0]
        conn.close()
        self.assertEqual(inserted, 0)
        self.assertEqual(revived, 0)
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
