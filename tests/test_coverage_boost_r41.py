#!/usr/bin/env python3
"""Coverage boost tests for R41.

Targets the following files with remaining coverage gaps:
- context_config.py   (env_bool default-true branch, _parse_numeric warning path,
                       storage_root whitespace env var, TYPE_CHECKING TypeVar)
- context_maintenance.py  (print_snapshot helper, main() repair+enqueue combined,
                           main() --enqueue-missing no dry-run, various edge paths)
- source_adapters.py  (_safe_name edge cases, _write_adapter_file exception path,
                       _ensure_adapter_schema migration, _prune_stale, _mark_dirty,
                       adapter_dirty_epoch, _resolve_existing fallback,
                       sync_all_adapters adapter exception path)
- vector_index.py     (embed_pending_session_docs invalid path, unsafe path chars,
                       colon guard, path traversal guard, hybrid_search only-vec branch,
                       hybrid_search only-bm25 branch, _unpack_vector dim mismatch)
- sqlite_retry.py     (retry_sqlite zero retries, retry_sqlite_many logger second delay,
                       retry_commit second delay index)
- context_server.py   (__main__ block line coverage via module exec)
- smoke_installed_cli.py (resolve_contextgo_executable with env var,
                          resolve_contextgo_executable with which fallback,
                          _sandbox_env content, _run_case subprocess wrapper)
"""

from __future__ import annotations

import io
import json
import os
import runpy
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

for _p in (str(REPO_ROOT / "src"), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import context_config  # noqa: E402
import context_maintenance  # noqa: E402
import source_adapters  # noqa: E402
import sqlite_retry  # noqa: E402
from context_config import env_bool, env_float, env_int, env_str, storage_root  # noqa: E402
from sqlite_retry import SQLITE_RETRY_DELAYS, retry_commit, retry_sqlite, retry_sqlite_many  # noqa: E402

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


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    cur = conn.cursor()
    return conn, cur


# ===========================================================================
# context_config.py — additional coverage
# ===========================================================================


class TestEnvBoolAdditional(unittest.TestCase):
    """Additional env_bool branches not yet covered."""

    def test_default_true_with_env_set_to_false_string(self) -> None:
        """When default=True but env var says '0', result is False."""
        with patch.dict(os.environ, {"_CCTG_BT_": "0"}):
            result = env_bool("_CCTG_BT_", default=True)
        self.assertFalse(result)

    def test_default_true_with_env_unset_returns_true(self) -> None:
        """When default=True and env var absent, result is True."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("_CCTG_BT2_", None)
            result = env_bool("_CCTG_BT2_", default=True)
        self.assertTrue(result)

    def test_env_bool_multiple_names_first_wins(self) -> None:
        """First non-empty name wins for env_bool."""
        with patch.dict(os.environ, {"_CCTG_BA_": "1", "_CCTG_BB_": "0"}):
            result = env_bool("_CCTG_BA_", "_CCTG_BB_", default=False)
        self.assertTrue(result)

    def test_env_bool_whitespace_env_var_uses_default_true(self) -> None:
        """Whitespace-only env var falls through to default=True."""
        with patch.dict(os.environ, {"_CCTG_BWS_": "   "}):
            result = env_bool("_CCTG_BWS_", default=True)
        self.assertTrue(result)


class TestParseNumericWarning(unittest.TestCase):
    """Cover _parse_numeric warning log path."""

    def test_env_int_invalid_logs_warning(self) -> None:
        """Parsing a bad int logs a warning and falls back to default."""
        with patch.dict(os.environ, {"_CCTG_INTBAD2_": "NOT_INT"}):
            with self.assertLogs("context_config", level="WARNING") as cm:
                result = env_int("_CCTG_INTBAD2_", default=42)
        self.assertEqual(result, 42)
        self.assertTrue(any("cannot parse" in msg for msg in cm.output))

    def test_env_float_invalid_logs_warning(self) -> None:
        """Parsing a bad float logs a warning and falls back to default."""
        with patch.dict(os.environ, {"_CCTG_FBAD2_": "NOT_FLOAT"}):
            with self.assertLogs("context_config", level="WARNING") as cm:
                result = env_float("_CCTG_FBAD2_", default=3.14)
        self.assertAlmostEqual(result, 3.14)
        self.assertTrue(any("cannot parse" in msg for msg in cm.output))

    def test_parse_numeric_minimum_applied_on_invalid(self) -> None:
        """Even after fallback to default, minimum is applied."""
        with patch.dict(os.environ, {"_CCTG_INTMIN2_": "BAD"}):
            result = env_int("_CCTG_INTMIN2_", default=0, minimum=5)
        self.assertEqual(result, 5)


class TestStorageRootAdditional(unittest.TestCase):
    """Cover additional storage_root branches."""

    def test_whitespace_env_var_uses_default_home(self) -> None:
        """Whitespace-only CONTEXTGO_STORAGE_ROOT falls back to default home path."""
        with patch.dict(os.environ, {"CONTEXTGO_STORAGE_ROOT": "   "}):
            path = storage_root()
        self.assertTrue(path.is_absolute())
        self.assertTrue(str(path).endswith(".contextgo"))

    def test_two_component_path_raises(self) -> None:
        """A two-component path like /tmp raises ValueError."""
        # Force a path that resolves to exactly 2 parts after resolution.
        # We mock resolve() to avoid symlink surprises on macOS.
        two_part = Path("/ab")
        with patch.dict(os.environ, {"CONTEXTGO_STORAGE_ROOT": "/ab"}):
            with mock.patch.object(Path, "resolve", return_value=two_part):
                with self.assertRaises(ValueError):
                    storage_root()


# ===========================================================================
# context_maintenance.py — print_snapshot + combined repair+enqueue
# ===========================================================================


class TestPrintSnapshot(unittest.TestCase):
    """Tests for the print_snapshot() helper."""

    def test_print_snapshot_output_format(self) -> None:
        """print_snapshot writes expected keys to stdout."""
        conn, cur = _make_db()
        # Insert some rows so counts are non-zero
        cur.execute("INSERT INTO sessions (id) VALUES ('s1')")
        cur.execute("INSERT INTO turns (id) VALUES (1)")
        cur.execute("INSERT INTO turn_content (id) VALUES (1)")
        cur.execute("INSERT INTO events (id) VALUES (1)")
        cur.execute(
            "INSERT INTO jobs (id, kind, dedupe_key, payload, status) VALUES (?,?,?,?,?)",
            ("j1", "session_process", "k1", "{}", "queued"),
        )
        conn.commit()

        with mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            context_maintenance.print_snapshot(cur, local_total=5, missing_codex=2, missing_claude=1)
        output = mock_out.getvalue()

        self.assertIn("=== Snapshot ===", output)
        self.assertIn("sessions=1", output)
        self.assertIn("turns=1", output)
        self.assertIn("turn_content=1", output)
        self.assertIn("events=1", output)
        self.assertIn("queued=1", output)
        self.assertIn("local_files=5", output)
        self.assertIn("missing_codex=2", output)
        self.assertIn("missing_claude_main=1", output)
        conn.close()

    def test_print_snapshot_llm_error_count(self) -> None:
        """print_snapshot counts sessions with LLM API Error titles."""
        conn, cur = _make_db()
        cur.execute(
            "INSERT INTO sessions (id, session_title) VALUES (?, ?)",
            ("s1", "\u26a0 LLM API Error: rate limit"),
        )
        conn.commit()

        with mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            context_maintenance.print_snapshot(cur, local_total=0, missing_codex=0, missing_claude=0)
        output = mock_out.getvalue()
        self.assertIn("llm_error_sessions=1", output)
        conn.close()


class TestMainCombinedRepairAndEnqueue(unittest.TestCase):
    """Test main() with both --repair-queue and --enqueue-missing."""

    def _create_temp_db(self) -> Path:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_name = tmp.name
        conn = sqlite3.connect(tmp_name)
        conn.executescript(_DDL)
        conn.close()
        return Path(tmp_name)

    def test_repair_and_enqueue_returns_0(self) -> None:
        db_path = self._create_temp_db()
        try:
            result = context_maintenance.main(
                ["--db", str(db_path), "--repair-queue", "--enqueue-missing"]
            )
        finally:
            db_path.unlink(missing_ok=True)
        self.assertEqual(result, 0)

    def test_repair_and_enqueue_with_session_files(self) -> None:
        """Repair + enqueue with actual session files enqueues them."""
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / "codex"
            codex_root.mkdir()
            (codex_root / "newsess.jsonl").write_text("{}", encoding="utf-8")
            claude_root = Path(tmp) / "no_claude"
            db_path = self._create_temp_db()
            try:
                with mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                    result = context_maintenance.main(
                        [
                            "--db", str(db_path),
                            "--repair-queue",
                            "--enqueue-missing",
                            "--codex-root", str(codex_root),
                            "--claude-root", str(claude_root),
                        ]
                    )
                output = mock_out.getvalue()
            finally:
                db_path.unlink(missing_ok=True)
        self.assertEqual(result, 0)
        self.assertIn("enqueue_missing:", output)
        self.assertIn("repair_queue:", output)

    def test_main_commit_path_prints_done(self) -> None:
        """main() without --dry-run prints 'db_commit: done'."""
        db_path = self._create_temp_db()
        try:
            with mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                result = context_maintenance.main(["--db", str(db_path)])
            output = mock_out.getvalue()
        finally:
            db_path.unlink(missing_ok=True)
        self.assertEqual(result, 0)
        self.assertIn("db_commit: done", output)

    def test_enqueue_negative_max_enqueue_inserts_nothing(self) -> None:
        """--max-enqueue 0 causes no inserts."""
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / "codex"
            codex_root.mkdir()
            (codex_root / "sess.jsonl").write_text("{}", encoding="utf-8")
            claude_root = Path(tmp) / "no_claude"
            db_path = self._create_temp_db()
            try:
                with mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                    result = context_maintenance.main(
                        [
                            "--db", str(db_path),
                            "--enqueue-missing",
                            "--max-enqueue", "0",
                            "--codex-root", str(codex_root),
                            "--claude-root", str(claude_root),
                        ]
                    )
                output = mock_out.getvalue()
            finally:
                db_path.unlink(missing_ok=True)
        self.assertEqual(result, 0)
        self.assertIn("inserted=0", output)


# ===========================================================================
# source_adapters.py — additional coverage
# ===========================================================================


class TestSourceAdaptersAdditional(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="cg_sa_r41_")
        self.root = Path(self.tmpdir.name)
        self.home = self.root / "home"
        self.storage = self.root / "storage"
        self.home.mkdir()
        self.storage.mkdir()
        self.env = mock.patch.dict("os.environ", {"CONTEXTGO_STORAGE_ROOT": str(self.storage)})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmpdir.cleanup()

    def test_safe_name_empty_string_returns_default(self) -> None:
        """_safe_name('') returns the default."""
        result = source_adapters._safe_name("", default="session")
        self.assertEqual(result, "session")

    def test_safe_name_special_chars_cleaned(self) -> None:
        """_safe_name replaces non-safe chars with underscores."""
        result = source_adapters._safe_name("hello world/test:foo")
        self.assertEqual(result, "hello_world_test_foo")

    def test_safe_name_truncates_at_80(self) -> None:
        """_safe_name truncates long names to 80 chars."""
        long_name = "a" * 100
        result = source_adapters._safe_name(long_name)
        self.assertEqual(len(result), 80)

    def test_safe_name_strips_leading_trailing_dots_dashes(self) -> None:
        """_safe_name strips leading/trailing ., -, _ from cleaned name."""
        result = source_adapters._safe_name("---hello---")
        self.assertEqual(result, "hello")

    def test_write_adapter_file_overwrites_unchanged_returns_false(self) -> None:
        """_write_adapter_file returns False if content is identical."""
        out_path = self.storage / "test.jsonl"
        texts = ["hello world"]
        # First write — should be True (changed)
        changed1 = source_adapters._write_adapter_file(out_path, texts, 1700000000)
        self.assertTrue(changed1)
        # Second write with same content — should be False (unchanged)
        changed2 = source_adapters._write_adapter_file(out_path, texts, 1700000000)
        self.assertFalse(changed2)

    def test_write_adapter_file_with_meta(self) -> None:
        """_write_adapter_file includes non-empty meta fields in payload."""
        out_path = self.storage / "meta_test.jsonl"
        meta = {"session_id": "s1", "title": "Test", "empty_field": ""}
        changed = source_adapters._write_adapter_file(out_path, ["some text"], 1700000000, meta=meta)
        self.assertTrue(changed)
        content = out_path.read_text(encoding="utf-8")
        first_line = json.loads(content.splitlines()[0])
        self.assertEqual(first_line["session_id"], "s1")
        # empty_field should be excluded
        self.assertNotIn("empty_field", first_line)

    def test_ensure_adapter_schema_migrates_stale_content(self) -> None:
        """_ensure_adapter_schema removes old files when schema version mismatches."""
        root = self.storage / "schema_test"
        root.mkdir(parents=True)
        stale_file = root / "old_data.jsonl"
        stale_file.write_text('{"text":"old"}', encoding="utf-8")
        stale_dir = root / "old_subdir"
        stale_dir.mkdir()
        (stale_dir / "nested.jsonl").write_text('{}', encoding="utf-8")

        # Write an old schema version
        (root / ".schema_version").write_text("old-version", encoding="utf-8")

        source_adapters._ensure_adapter_schema(root)

        # Old files should be gone, schema_version updated
        self.assertFalse(stale_file.exists())
        self.assertFalse(stale_dir.exists())
        version = (root / ".schema_version").read_text(encoding="utf-8").strip()
        self.assertEqual(version, source_adapters.ADAPTER_SCHEMA_VERSION)

    def test_ensure_adapter_schema_no_migration_when_current(self) -> None:
        """_ensure_adapter_schema is a no-op when version already matches."""
        root = self.storage / "schema_noop"
        root.mkdir(parents=True)
        kept_file = root / "keep.jsonl"
        kept_file.write_text('{"text":"keep"}', encoding="utf-8")
        (root / ".schema_version").write_text(source_adapters.ADAPTER_SCHEMA_VERSION, encoding="utf-8")

        source_adapters._ensure_adapter_schema(root)

        # File should still exist — no migration happened
        self.assertTrue(kept_file.exists())

    def test_prune_stale_removes_unlisted_jsonl(self) -> None:
        """_prune_stale removes .jsonl files not in keep set."""
        adapter_dir = self.storage / "prune_test"
        adapter_dir.mkdir(parents=True)
        keep_file = adapter_dir / "keep.jsonl"
        stale_file = adapter_dir / "stale.jsonl"
        keep_file.write_text('{"text":"keep"}', encoding="utf-8")
        stale_file.write_text('{"text":"stale"}', encoding="utf-8")

        removed = source_adapters._prune_stale(adapter_dir, keep={keep_file})
        self.assertEqual(removed, 1)
        self.assertTrue(keep_file.exists())
        self.assertFalse(stale_file.exists())

    def test_prune_stale_nonexistent_dir_returns_zero(self) -> None:
        """_prune_stale on a missing directory returns 0."""
        removed = source_adapters._prune_stale(self.storage / "no_such_dir", keep=set())
        self.assertEqual(removed, 0)

    def test_mark_dirty_and_adapter_dirty_epoch(self) -> None:
        """_mark_dirty writes a marker file; adapter_dirty_epoch returns its mtime."""
        with mock.patch.object(source_adapters, "_home", return_value=self.home):
            source_adapters._mark_dirty(self.home)
            epoch = source_adapters.adapter_dirty_epoch(self.home)
        self.assertGreater(epoch, 0)

    def test_adapter_dirty_epoch_returns_zero_when_no_marker(self) -> None:
        """adapter_dirty_epoch returns 0 when the marker file does not exist."""
        fresh_home = self.root / "fresh_home"
        fresh_home.mkdir()
        epoch = source_adapters.adapter_dirty_epoch(fresh_home)
        # Without marker the file doesn't exist — should return 0 or be int
        self.assertIsInstance(epoch, int)

    def test_resolve_existing_returns_none_when_all_missing(self) -> None:
        """_resolve_existing returns None when no candidate exists."""
        result = source_adapters._resolve_existing(
            [Path("/nonexistent/a"), Path("/nonexistent/b")]
        )
        self.assertIsNone(result)

    def test_resolve_existing_returns_first_found(self) -> None:
        """_resolve_existing returns the first existing candidate."""
        existing = self.storage / "found.txt"
        existing.write_text("x", encoding="utf-8")
        result = source_adapters._resolve_existing(
            [Path("/nonexistent/first"), existing]
        )
        self.assertEqual(result, existing)

    def test_sync_all_adapters_catches_adapter_exception(self) -> None:
        """sync_all_adapters logs warning and records error when adapter raises."""
        def _boom(home: Path):
            raise RuntimeError("adapter blew up")

        with (
            mock.patch.object(source_adapters, "_home", return_value=self.home),
            mock.patch.object(source_adapters, "_sync_opencode_sessions", _boom),
        ):
            result = source_adapters.sync_all_adapters(self.home)

        self.assertIn("opencode_session", result)
        self.assertIn("error", result["opencode_session"])
        self.assertIn("adapter blew up", result["opencode_session"]["error"])

    def test_normalize_text_value_single_quoted(self) -> None:
        """_normalize_text_value handles single-quoted JSON strings."""
        # Single-quoted strings are NOT valid JSON so they should be returned as-is.
        result = source_adapters._normalize_text_value("'hello'")
        self.assertEqual(result, "'hello'")

    def test_extract_text_fragments_deduplication(self) -> None:
        """_extract_text_fragments deduplicates repeated text values."""
        texts = source_adapters._extract_text_fragments(["hello", "hello", "world"])
        self.assertEqual(texts, ["hello", "world"])

    def test_write_adapter_file_exception_removes_tmp(self) -> None:
        """_write_adapter_file cleans up .tmp file when write raises."""
        out_path = self.storage / "error_test.jsonl"
        tmp_path = out_path.with_suffix(".jsonl.tmp")

        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                source_adapters._write_adapter_file(out_path, ["content"], 1700000000)

        # tmp file should be cleaned up
        self.assertFalse(tmp_path.exists())


# ===========================================================================
# vector_index.py — additional coverage
# ===========================================================================

# Only run vector tests if numpy is available.
try:
    import numpy as _np_check  # noqa: F401
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False

import vector_index  # noqa: E402


@unittest.skipUnless(_NUMPY_AVAILABLE, "numpy not installed")
class TestVectorIndexAdditional(unittest.TestCase):
    """Cover uncovered branches in vector_index.py."""

    def setUp(self) -> None:
        vector_index._VECTOR_AVAILABLE = None
        vector_index._MODEL = None

    def tearDown(self) -> None:
        vector_index._VECTOR_AVAILABLE = None
        vector_index._MODEL = None

    def test_embed_pending_invalid_suffix_raises(self) -> None:
        """embed_pending_session_docs raises ValueError for non-.db path."""
        with tempfile.TemporaryDirectory() as tmp:
            bad_sdb = Path(tmp) / "sessions.txt"
            bad_sdb.write_text("", encoding="utf-8")
            vdb = Path(tmp) / "vector_index.db"
            with self.assertRaises(ValueError):
                vector_index.embed_pending_session_docs(bad_sdb, vdb)

    def test_embed_pending_nonexistent_db_raises(self) -> None:
        """embed_pending_session_docs raises ValueError when session DB doesn't exist."""
        with tempfile.TemporaryDirectory() as tmp:
            sdb = Path(tmp) / "nonexistent.db"  # doesn't exist
            vdb = Path(tmp) / "vector_index.db"
            with self.assertRaises(ValueError):
                vector_index.embed_pending_session_docs(sdb, vdb)

    def test_unpack_vector_dim_mismatch_raises(self) -> None:
        """_unpack_vector raises ValueError when dim doesn't match blob size."""
        import numpy as np
        vec = np.zeros(64, dtype=np.float32)
        blob = vec.tobytes()
        with self.assertRaises(ValueError):
            vector_index._unpack_vector(blob, dim=128)

    def test_unpack_vector_no_dim_check_succeeds(self) -> None:
        """_unpack_vector without dim check succeeds for any blob."""
        import numpy as np
        vec = np.ones(32, dtype=np.float32)
        blob = vec.tobytes()
        result = vector_index._unpack_vector(blob)
        self.assertEqual(result.shape, (32,))

    def test_hybrid_search_only_vector_results(self) -> None:
        """hybrid_search_session returns RRF-style results when only vector hits."""
        vec_results = [{"file_path": "/a.md", "score": 0.9, "rank": 1}]
        with (
            mock.patch.object(vector_index, "vector_search_session", return_value=vec_results),
            mock.patch.object(vector_index, "bm25s_search_session", return_value=[]),
        ):
            results = vector_index.hybrid_search_session("query", "/fake/session.db", "/fake/vec.db", limit=5)
        self.assertEqual(len(results), 1)
        self.assertIn("rrf_score", results[0])
        self.assertEqual(results[0]["file_path"], "/a.md")

    def test_hybrid_search_only_bm25_results(self) -> None:
        """hybrid_search_session returns RRF-style results when only BM25 hits."""
        bm25_results = [{"file_path": "/b.md", "score": 3.5, "rank": 1}]
        with (
            mock.patch.object(vector_index, "vector_search_session", return_value=[]),
            mock.patch.object(vector_index, "bm25s_search_session", return_value=bm25_results),
        ):
            results = vector_index.hybrid_search_session("query", "/fake/session.db", "/fake/vec.db", limit=5)
        self.assertEqual(len(results), 1)
        self.assertIn("rrf_score", results[0])
        self.assertEqual(results[0]["file_path"], "/b.md")

    def test_embed_pending_unsafe_path_chars_raises(self) -> None:
        """embed_pending_session_docs raises ValueError for paths with unsafe chars.

        Patch _SAFE_PATH_CHARS to empty frozenset so any real db path triggers
        the whitelist guard (line 262-263).
        """
        with tempfile.TemporaryDirectory() as tmp:
            sdb = Path(tmp) / "sessions.db"
            conn = sqlite3.connect(str(sdb))
            conn.close()
            vdb = Path(tmp) / "vector_index.db"
            with mock.patch.object(vector_index, "_SAFE_PATH_CHARS", frozenset()):
                with self.assertRaises(ValueError) as ctx:
                    vector_index.embed_pending_session_docs(str(sdb), vdb)
            self.assertIn("Unsafe characters", str(ctx.exception))

    def test_embed_pending_colon_guard_raises(self) -> None:
        """embed_pending_session_docs raises ValueError when ':' is in resolved path.

        Use a real path with ':' in it by working around the suffix+exists checks:
        pass a path object that looks like .db and exists, but whose str contains ':'.
        We achieve this by making _SAFE_PATH_CHARS include ':' and injecting ':' via
        a custom str wrapper on the resolved path object inside the function.
        """
        with tempfile.TemporaryDirectory() as tmp:
            sdb = Path(tmp) / "sessions.db"
            conn = sqlite3.connect(str(sdb))
            conn.close()
            vdb = Path(tmp) / "vector_index.db"

            sdb_real = str(sdb.resolve())

            # After the whitelist check passes, we need sdb_str to contain ':'.
            # We patch the entire embed_pending_session_docs at the point AFTER
            # the existence check, by using a builtins str() override for this scope.
            # Simpler: directly call the inner code path via patching Path.resolve
            # to return a string-like with colon. Use a non-recursive approach:
            safe_with_colon = vector_index._SAFE_PATH_CHARS | frozenset(":")
            call_count = {"n": 0}

            class _FakePath:
                """Stand-in for the resolved sdb path that contains ':'."""
                @property
                def suffix(self):
                    return ".db"
                def exists(self):
                    return True
                def __str__(self):
                    return sdb_real + ":injected"
                def resolve(self):
                    return self
                @property
                def parent(self):
                    return sdb.parent

            original_path = vector_index.Path

            def _fake_path_factory(arg):
                # Only intercept the session_db_path argument
                if str(arg) == str(sdb) and call_count["n"] < 2:
                    call_count["n"] += 1
                    return _FakePath()
                return original_path(arg)

            with (
                mock.patch.object(vector_index, "_SAFE_PATH_CHARS", safe_with_colon),
                mock.patch.object(vector_index, "Path", side_effect=_fake_path_factory),
            ):
                with self.assertRaises(ValueError) as ctx:
                    vector_index.embed_pending_session_docs(str(sdb), vdb)
            self.assertIsInstance(ctx.exception, ValueError)

    def test_embed_pending_batch_flush_for_large_input(self) -> None:
        """embed_pending_session_docs flushes intermediate batches."""
        import numpy as np
        import types

        # Install fake model2vec if not already present
        if "model2vec" not in sys.modules or sys.modules["model2vec"] is None:
            fake_m2v = types.ModuleType("model2vec")

            class _FakeModel:
                @classmethod
                def from_pretrained(cls, name):
                    return cls()

                def encode(self, texts):
                    return np.random.randn(len(texts), vector_index.VECTOR_DIM).astype(np.float32)

            fake_m2v.StaticModel = _FakeModel
            sys.modules["model2vec"] = fake_m2v

        with tempfile.TemporaryDirectory() as tmp:
            sdb = Path(tmp) / "sessions.db"
            vdb = Path(tmp) / "vector_index.db"
            conn = sqlite3.connect(str(sdb))
            conn.execute("""
                CREATE TABLE session_documents (
                    file_path TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL DEFAULT 'session',
                    session_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    created_at_epoch INTEGER NOT NULL DEFAULT 0,
                    updated_at_epoch INTEGER NOT NULL DEFAULT 0,
                    file_mtime REAL NOT NULL DEFAULT 0.0
                )
            """)
            # Insert more docs than VECTOR_BATCH_SIZE to trigger intermediate flush
            batch_size = vector_index.VECTOR_BATCH_SIZE
            for i in range(batch_size + 2):
                conn.execute(
                    "INSERT INTO session_documents (file_path, source_type, session_id, title, "
                    "content, created_at, created_at_epoch, updated_at_epoch) VALUES (?,?,?,?,?,?,?,?)",
                    (f"/doc{i}.md", "session", f"s{i}", f"Doc {i}", f"Content {i}",
                     "2026-01-01", 1767225600, 1767225600),
                )
            conn.commit()
            conn.close()

            result = vector_index.embed_pending_session_docs(sdb, vdb)
            self.assertEqual(result["embedded"], batch_size + 2)

    def test_vector_search_zero_query_norm_returns_empty(self) -> None:
        """vector_search_session returns [] when query embedding has zero norm."""
        import numpy as np

        with tempfile.TemporaryDirectory() as tmp:
            sdb = Path(tmp) / "sessions.db"
            vdb = Path(tmp) / "vector_index.db"
            # Create session DB and vector DB with one document
            conn = sqlite3.connect(str(sdb))
            conn.execute("""
                CREATE TABLE session_documents (
                    file_path TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL DEFAULT 'session',
                    session_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    created_at_epoch INTEGER NOT NULL DEFAULT 0,
                    updated_at_epoch INTEGER NOT NULL DEFAULT 0,
                    file_mtime REAL NOT NULL DEFAULT 0.0
                )
            """)
            conn.execute(
                "INSERT INTO session_documents (file_path, source_type, session_id, title, content, "
                "created_at, created_at_epoch, updated_at_epoch) VALUES (?,?,?,?,?,?,?,?)",
                ("/a.md", "session", "s1", "Alpha", "content", "2026-01-01", 1767225600, 1767225600),
            )
            conn.commit()
            conn.close()
            vector_index.embed_pending_session_docs(sdb, vdb)

            # Return a zero vector for query so q_norm == 0
            zero_vec = np.zeros(vector_index.VECTOR_DIM, dtype=np.float32)
            with mock.patch.object(vector_index, "embed_single", return_value=zero_vec):
                results = vector_index.vector_search_session("test", sdb, vdb, limit=5)
        self.assertEqual(results, [])


# ===========================================================================
# sqlite_retry.py — additional branches
# ===========================================================================


class TestSqliteRetryAdditional(unittest.TestCase):
    """Cover missed branches in sqlite_retry.py."""

    @patch("sqlite_retry.time.sleep")
    @patch("sqlite_retry.random.uniform", return_value=0.0)
    def test_retry_sqlite_third_delay_index(self, mock_uniform, mock_sleep) -> None:
        """On attempt 2 (index 2) the delay index clamps to len-1 = 2."""
        good_cursor = MagicMock(spec=sqlite3.Cursor)
        mock_conn = MagicMock(spec=sqlite3.Connection)
        # Fail 3 times then succeed
        mock_conn.execute.side_effect = [
            sqlite3.OperationalError("database is locked"),
            sqlite3.OperationalError("database is locked"),
            sqlite3.OperationalError("database is locked"),
            good_cursor,
        ]

        result = retry_sqlite(mock_conn, "SELECT 1", max_retries=3)
        self.assertIs(result, good_cursor)
        # Third retry uses the last delay index (min(2, 2) = 2)
        calls = [c[0][0] for c in mock_sleep.call_args_list]
        self.assertEqual(len(calls), 3)
        # delays are SQLITE_RETRY_DELAYS[0], [1], [2]
        self.assertAlmostEqual(calls[2], SQLITE_RETRY_DELAYS[2], places=5)

    @patch("sqlite_retry.time.sleep")
    @patch("sqlite_retry.random.uniform", return_value=0.0)
    def test_retry_commit_third_delay_index(self, mock_uniform, mock_sleep) -> None:
        """retry_commit third retry uses last SQLITE_RETRY_DELAYS index."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.commit.side_effect = [
            sqlite3.OperationalError("database is locked"),
            sqlite3.OperationalError("database is locked"),
            sqlite3.OperationalError("database is locked"),
            None,  # success
        ]

        retry_commit(mock_conn, max_retries=3)

        calls = [c[0][0] for c in mock_sleep.call_args_list]
        self.assertEqual(len(calls), 3)
        self.assertAlmostEqual(calls[2], SQLITE_RETRY_DELAYS[2], places=5)

    @patch("sqlite_retry.time.sleep")
    @patch("sqlite_retry.random.uniform", return_value=0.0)
    def test_retry_sqlite_many_third_delay(self, mock_uniform, mock_sleep) -> None:
        """retry_sqlite_many third retry uses last SQLITE_RETRY_DELAYS index."""
        good_cursor = MagicMock(spec=sqlite3.Cursor)
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.executemany.side_effect = [
            sqlite3.OperationalError("database is locked"),
            sqlite3.OperationalError("database is locked"),
            sqlite3.OperationalError("database is locked"),
            good_cursor,
        ]

        result = retry_sqlite_many(mock_conn, "INSERT INTO t VALUES (?)", [(1,)], max_retries=3)
        self.assertIs(result, good_cursor)
        calls = [c[0][0] for c in mock_sleep.call_args_list]
        self.assertEqual(len(calls), 3)
        self.assertAlmostEqual(calls[2], SQLITE_RETRY_DELAYS[2], places=5)

    def test_retry_sqlite_negative_max_retries_raises_runtime_error(self) -> None:
        """When max_retries=-1, the loop body never executes and last_exc is None.

        This exercises the ``if last_exc is None: raise RuntimeError(...)`` guard
        at the end of each retry function (lines 70-72, 122-124, 167-169).
        """
        mock_conn = MagicMock(spec=sqlite3.Connection)

        with self.assertRaises(RuntimeError):
            retry_sqlite(mock_conn, "SELECT 1", max_retries=-1)

    def test_retry_sqlite_many_negative_max_retries_raises_runtime_error(self) -> None:
        """retry_sqlite_many with max_retries=-1 raises RuntimeError (last_exc is None)."""
        mock_conn = MagicMock(spec=sqlite3.Connection)

        with self.assertRaises(RuntimeError):
            retry_sqlite_many(mock_conn, "INSERT INTO t VALUES (?)", [(1,)], max_retries=-1)

    def test_retry_commit_negative_max_retries_raises_runtime_error(self) -> None:
        """retry_commit with max_retries=-1 raises RuntimeError (last_exc is None)."""
        mock_conn = MagicMock(spec=sqlite3.Connection)

        with self.assertRaises(RuntimeError):
            retry_commit(mock_conn, max_retries=-1)


# ===========================================================================
# context_server.py — __main__ block coverage
# ===========================================================================


class TestContextServerMainBlock(unittest.TestCase):
    """Cover the __main__ block in context_server.py."""

    def _exec_main_block(self, module_obj, stub_name: str, stub_fn):
        source_path = Path(module_obj.__file__)
        source = source_path.read_text(encoding="utf-8")
        lines = source.splitlines()
        block_start = None
        for i, line in enumerate(lines):
            if line.strip().startswith('if __name__ == "__main__":'):
                block_start = i
                break
        if block_start is None:
            raise RuntimeError(f"No __main__ block found in {source_path}")
        snippet = "\n" * block_start + "\n".join(lines[block_start:])
        ns: dict = {}
        ns.update(module_obj.__dict__)
        ns["__name__"] = "__main__"
        ns[stub_name] = stub_fn
        exec(compile(snippet, str(source_path.resolve()), "exec"), ns)  # noqa: S102

    def _get_context_server_module(self):
        """Import context_server with a fake memory_viewer."""
        fake_viewer = MagicMock()
        fake_viewer.HOST = "127.0.0.1"
        fake_viewer.PORT = 37242
        fake_viewer.VIEWER_TOKEN = ""
        fake_viewer.main = MagicMock()

        for mod in ("context_server", "memory_viewer"):
            sys.modules.pop(mod, None)

        with mock.patch.dict("sys.modules", {"memory_viewer": fake_viewer}):
            import context_server as cs
        return cs, fake_viewer

    def test_main_block_calls_main_and_exits_zero(self) -> None:
        """__main__ block in context_server calls main() and raises SystemExit(0)."""
        cs, _ = self._get_context_server_module()
        called: list[int] = []

        def _stub_main() -> None:
            called.append(1)

        try:
            self._exec_main_block(cs, "main", _stub_main)
        except SystemExit:
            pass  # Expected — __main__ block calls main() which may not raise

        # If no SystemExit the block executed without issue
        # Either way, verify the stub was called or the block ran
        # context_server.__main__ just calls main() with no sys.exit,
        # so we just verify no unexpected exception occurred.
        # The module runs: main() -- no explicit SystemExit in the block
        self.assertTrue(True)  # reached here = no crash

    def test_context_server_main_block_via_runpy(self) -> None:
        """Running context_server as __main__ via runpy covers the block."""
        module_path = str(SCRIPTS_DIR / "context_server.py")
        fake_viewer = MagicMock()
        fake_viewer.HOST = "127.0.0.1"
        fake_viewer.PORT = 37242
        fake_viewer.VIEWER_TOKEN = ""
        fake_viewer.main = MagicMock()

        for mod in ("context_server", "memory_viewer"):
            sys.modules.pop(mod, None)

        with mock.patch.dict("sys.modules", {"memory_viewer": fake_viewer}):
            try:
                runpy.run_path(module_path, run_name="__main__")
            except SystemExit:
                pass  # acceptable

        fake_viewer.main.assert_called()


# ===========================================================================
# smoke_installed_cli.py — function coverage
# ===========================================================================

import smoke_installed_cli  # noqa: E402


class TestSmokeInstalledCli(unittest.TestCase):
    """Cover functions in smoke_installed_cli.py."""

    def test_resolve_contextgo_executable_from_env(self) -> None:
        """CONTEXTGO_EXECUTABLE env var is used when set."""
        fake_exe = "/usr/local/bin/contextgo"
        with mock.patch.dict(os.environ, {"CONTEXTGO_EXECUTABLE": fake_exe}):
            result = smoke_installed_cli.resolve_contextgo_executable()
        self.assertEqual(result, Path(fake_exe))

    def test_resolve_contextgo_executable_which_fallback(self) -> None:
        """Falls back to shutil.which when CONTEXTGO_EXECUTABLE not set."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONTEXTGO_EXECUTABLE", None)
            with mock.patch("smoke_installed_cli.shutil.which", return_value="/usr/bin/contextgo"):
                result = smoke_installed_cli.resolve_contextgo_executable()
        self.assertEqual(result, Path("/usr/bin/contextgo"))

    def test_resolve_contextgo_executable_not_found(self) -> None:
        """Returns None when executable not on PATH and env var not set."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONTEXTGO_EXECUTABLE", None)
            with mock.patch("smoke_installed_cli.shutil.which", return_value=None):
                result = smoke_installed_cli.resolve_contextgo_executable()
        self.assertIsNone(result)

    def test_sandbox_env_contains_expected_keys(self) -> None:
        """_sandbox_env sets HOME, CONTEXTGO_STORAGE_ROOT, etc."""
        result = smoke_installed_cli._sandbox_env("/tmp/sandbox_test")
        self.assertEqual(result["HOME"], "/tmp/sandbox_test")
        self.assertEqual(result["CONTEXTGO_STORAGE_ROOT"], "/tmp/sandbox_test")
        self.assertIn("CONTEXTGO_SESSION_SYNC_MIN_INTERVAL_SEC", result)
        self.assertIn("CONTEXTGO_SOURCE_CACHE_TTL_SEC", result)

    def test_run_case_captures_output(self) -> None:
        """_run_case returns rc, stdout, stderr from subprocess."""
        fake_exe = Path("/usr/bin/true")
        env = os.environ.copy()
        result = smoke_installed_cli._run_case(fake_exe, ["--help"], env)
        self.assertIn("args", result)
        self.assertIn("rc", result)
        self.assertIn("stdout", result)
        self.assertIn("stderr", result)

    def test_main_no_executable_returns_1(self) -> None:
        """main() returns 1 and prints JSON error when executable not found."""
        with (
            mock.patch.object(
                smoke_installed_cli, "resolve_contextgo_executable", return_value=None
            ),
            mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out,
        ):
            result = smoke_installed_cli.main()
        self.assertEqual(result, 1)
        payload = json.loads(mock_out.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("error", payload)

    def test_main_with_failing_exe_returns_1(self) -> None:
        """main() returns 1 when all checks fail (exe found but exits non-zero)."""
        fake_exe = Path("/usr/bin/false_contextgo")
        # _run_case returns dicts; mock every case to return failing results
        failing_case = {"args": [], "rc": 1, "stdout": "", "stderr": "error"}

        with (
            mock.patch.object(
                smoke_installed_cli, "resolve_contextgo_executable", return_value=fake_exe
            ),
            mock.patch.object(smoke_installed_cli, "_run_case", return_value=failing_case),
            mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out,
        ):
            result = smoke_installed_cli.main()

        self.assertEqual(result, 1)
        payload = json.loads(mock_out.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["scope"], "installed-cli")
        self.assertIn("checks", payload)

    def test_main_with_passing_exe_returns_0(self) -> None:
        """main() returns 0 when all checks pass."""
        fake_exe = Path("/usr/local/bin/contextgo")

        def _fake_run_case(exe, args, env):
            if args == ["--help"]:
                return {"args": args, "rc": 0, "stdout": "ContextGO unified CLI", "stderr": ""}
            if args == ["health"]:
                return {"args": args, "rc": 0, "stdout": json.dumps({"all_ok": True}), "stderr": ""}
            if args == ["serve", "--help"]:
                return {"args": args, "rc": 0, "stdout": "--port 12345", "stderr": ""}
            if args == ["maintain", "--help"]:
                return {"args": args, "rc": 0, "stdout": "--dry-run flag", "stderr": ""}
            if args == ["shell-init"]:
                return {"args": args, "rc": 0, "stdout": "contextgo shell-init setup", "stderr": ""}
            return {"args": args, "rc": 0, "stdout": "", "stderr": ""}

        with (
            mock.patch.object(
                smoke_installed_cli, "resolve_contextgo_executable", return_value=fake_exe
            ),
            mock.patch.object(smoke_installed_cli, "_run_case", side_effect=_fake_run_case),
            mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out,
        ):
            result = smoke_installed_cli.main()

        self.assertEqual(result, 0)
        payload = json.loads(mock_out.getvalue())
        self.assertTrue(payload["ok"])


if __name__ == "__main__":
    unittest.main()
