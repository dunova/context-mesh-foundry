#!/usr/bin/env python3
"""R9 AutoResearch batch-1 tests — closing top coverage gaps.

Covers:
1. _search_noise_penalty short-token-line density branch (session_index)
2. _search_noise_penalty ls -l output pattern (session_index)
3. _is_current_repo_meta_result empty-compact early-return (session_index)
4. _remote_process_count TimeoutExpired (context_cli)
5. _remote_process_count OSError (context_cli)
6. _load_health_cache non-dict envelope (context_native)
7. _store_health_cache mkdir OSError (context_native)
8. search_index limit clamping — 0 and 201 (memory_index)
9. CJK export-import round-trip (memory_index)
10. Concurrent sync_index_from_storage (memory_index)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Must be set BEFORE any contextgo imports
os.environ.setdefault("CONTEXTGO_STORAGE_ROOT", "/tmp/cgo_test_r9")

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import context_cli  # noqa: E402  # pylint: disable=wrong-import-position
import context_native  # noqa: E402
import memory_index  # noqa: E402
import session_index  # noqa: E402

# ---------------------------------------------------------------------------
# 1. _search_noise_penalty — short-token-line density branch
# ---------------------------------------------------------------------------


class TestSearchNoisePenaltyShortTokenLines(unittest.TestCase):
    """Penalty is applied when 8+ lines each have <= 40 chars and no spaces."""

    def test_short_token_line_density_triggers_penalty(self) -> None:
        # Build 10 lines of short tokens with no spaces (like variable names)
        lines = "\n".join(["abcdefghij"] * 10)
        penalty = session_index._search_noise_penalty("", lines, "")
        self.assertGreaterEqual(penalty, 200, "Expected penalty >= 200 for dense short-token lines")

    def test_short_token_lines_below_threshold_no_penalty(self) -> None:
        # Only 5 lines — below the threshold of 8
        lines = "\n".join(["abcdefghij"] * 5)
        penalty = session_index._search_noise_penalty("", lines, "")
        # Short-token-line penalty should NOT be applied
        self.assertLess(penalty, 200, "Expected no short-token density penalty with only 5 lines")

    def test_lines_with_spaces_excluded_from_short_token_count(self) -> None:
        # 10 lines but each has a space — should NOT count as short token lines
        lines = "\n".join(["hello world"] * 10)
        penalty = session_index._search_noise_penalty("", lines, "")
        self.assertLess(penalty, 200, "Lines with spaces should not count toward short-token penalty")

    def test_long_lines_excluded_from_short_token_count(self) -> None:
        # 10 lines each > 40 chars — should not trigger penalty
        lines = "\n".join(["x" * 50] * 10)
        penalty = session_index._search_noise_penalty("", lines, "")
        self.assertLess(penalty, 200, "Long lines should not trigger short-token density penalty")


# ---------------------------------------------------------------------------
# 2. _search_noise_penalty — ls -l output pattern
# ---------------------------------------------------------------------------


class TestSearchNoisePenaltyLsOutput(unittest.TestCase):
    """Penalty is applied when text contains ls -l style permission strings."""

    def test_drwx_pattern_triggers_penalty(self) -> None:
        text = "drwxr-xr-x  5 user group 160 Mar 27 10:00 somedir"
        penalty = session_index._search_noise_penalty("", text, "")
        self.assertGreaterEqual(penalty, 200, "Expected penalty for drwxr-xr-x pattern")

    def test_rwxr_xr_x_pattern_triggers_penalty(self) -> None:
        text = "rwxr-xr-x  1 user group  80 Mar 27 10:00 file.txt"
        penalty = session_index._search_noise_penalty("", text, "")
        self.assertGreaterEqual(penalty, 200, "Expected penalty for rwxr-xr-x pattern")

    def test_newline_total_pattern_triggers_penalty(self) -> None:
        text = "\ntotal 48\n-rw-r--r-- 1 user group 1234 file.py"
        penalty = session_index._search_noise_penalty("", text, "")
        self.assertGreaterEqual(penalty, 200, "Expected penalty for \\ntotal pattern")

    def test_clean_text_no_ls_penalty(self) -> None:
        text = "This is a normal sentence about code review findings."
        penalty = session_index._search_noise_penalty("", text, "")
        # Should not trigger the ls-output penalty (200)
        self.assertLess(penalty, 200, "Clean text should not trigger ls-output penalty")


# ---------------------------------------------------------------------------
# 3. _is_current_repo_meta_result — empty-compact early-return
# ---------------------------------------------------------------------------


class TestIsCurrentRepoMetaResultEmptyCompact(unittest.TestCase):
    """Empty compact content returns True when title matches CWD."""

    def test_empty_content_returns_true_when_title_matches_cwd(self) -> None:
        cwd = str(Path.cwd().resolve())
        # title == cwd AND content is empty — should return True (early-return branch)
        result = session_index._is_current_repo_meta_result(cwd, "", "somefile.md")
        self.assertTrue(result, "Empty content with CWD title should return True")

    def test_whitespace_only_content_returns_true_when_title_matches_cwd(self) -> None:
        cwd = str(Path.cwd().resolve())
        result = session_index._is_current_repo_meta_result(cwd, "   \n\t  ", "somefile.md")
        self.assertTrue(result, "Whitespace-only content with CWD title should return True")

    def test_non_matching_title_returns_false(self) -> None:
        # title does NOT match cwd — should return False regardless
        result = session_index._is_current_repo_meta_result("/some/other/path", "", "somefile.md")
        self.assertFalse(result, "Non-matching title should return False")

    def test_non_empty_content_without_markers_returns_false(self) -> None:
        cwd = str(Path.cwd().resolve())
        # title matches but content has no meta markers
        result = session_index._is_current_repo_meta_result(cwd, "just normal content here", "somefile.md")
        self.assertFalse(result, "Non-meta content should not be flagged as meta result")


# ---------------------------------------------------------------------------
# 4. _remote_process_count — TimeoutExpired
# ---------------------------------------------------------------------------


class TestRemoteProcessCountTimeoutExpired(unittest.TestCase):
    """Returns 0 when subprocess.run raises TimeoutExpired."""

    def test_timeout_expired_returns_zero(self) -> None:
        import subprocess

        # _remote_process_count does a deferred `import subprocess` inside the
        # function body, so we patch the subprocess module directly in sys.modules
        # to intercept the call.
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="pgrep", timeout=3)):
            result = context_cli._remote_process_count()
        self.assertEqual(result, 0, "TimeoutExpired should cause _remote_process_count to return 0")

    def test_timeout_expired_is_silent(self) -> None:
        """No exception should propagate to the caller."""
        import subprocess

        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="pgrep", timeout=3)):
            # Must not raise
            try:
                context_cli._remote_process_count()
            except Exception as exc:
                self.fail(f"_remote_process_count raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# 5. _remote_process_count — OSError
# ---------------------------------------------------------------------------


class TestRemoteProcessCountOSError(unittest.TestCase):
    """Returns 0 when subprocess.run raises OSError."""

    def test_oserror_returns_zero(self) -> None:
        import subprocess

        with patch.object(subprocess, "run", side_effect=OSError("pgrep not found")):
            result = context_cli._remote_process_count()
        self.assertEqual(result, 0, "OSError should cause _remote_process_count to return 0")

    def test_oserror_is_silent(self) -> None:
        import subprocess

        with patch.object(subprocess, "run", side_effect=OSError("no such file")):
            try:
                context_cli._remote_process_count()
            except Exception as exc:
                self.fail(f"_remote_process_count raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# 6. _load_health_cache — non-dict envelope
# ---------------------------------------------------------------------------


class TestLoadHealthCacheNonDictEnvelope(unittest.TestCase):
    """Returns None when the JSON envelope is not a dict (e.g. a list)."""

    def test_list_envelope_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "native_health_cache.json"
            # Write a valid JSON list (not dict) as the envelope
            cache_path.write_text(json.dumps([{"cached_at": time.time(), "payload": {}}]), encoding="utf-8")
            with (
                patch.object(context_native, "NATIVE_HEALTH_CACHE_PATH", cache_path),
                patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 60),
            ):
                result = context_native._load_health_cache()
        self.assertIsNone(result, "Non-dict envelope should cause _load_health_cache to return None")

    def test_string_envelope_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "native_health_cache.json"
            cache_path.write_text(json.dumps("not a dict"), encoding="utf-8")
            with (
                patch.object(context_native, "NATIVE_HEALTH_CACHE_PATH", cache_path),
                patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 60),
            ):
                result = context_native._load_health_cache()
        self.assertIsNone(result, "String envelope should cause _load_health_cache to return None")

    def test_number_envelope_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "native_health_cache.json"
            cache_path.write_text("42", encoding="utf-8")
            with (
                patch.object(context_native, "NATIVE_HEALTH_CACHE_PATH", cache_path),
                patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 60),
            ):
                result = context_native._load_health_cache()
        self.assertIsNone(result, "Number envelope should cause _load_health_cache to return None")


# ---------------------------------------------------------------------------
# 7. _store_health_cache — mkdir OSError
# ---------------------------------------------------------------------------


class TestStoreHealthCacheMkdirOSError(unittest.TestCase):
    """Silently skips write when Path.mkdir raises OSError."""

    def test_mkdir_oserror_silent_skip(self) -> None:
        payload = {"available_backends": [], "probe_mode": "disabled"}
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "nonexistent_dir" / "native_health_cache.json"
            with (
                patch.object(context_native, "NATIVE_HEALTH_CACHE_PATH", cache_path),
                patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 60),
                patch("pathlib.Path.mkdir", side_effect=OSError("permission denied")),
            ):
                # Must not raise
                try:
                    context_native._store_health_cache(payload)
                except Exception as exc:
                    self.fail(f"_store_health_cache raised unexpectedly: {exc}")
        # Cache file must NOT have been written
        self.assertFalse(cache_path.exists(), "Cache file should not exist after mkdir OSError")

    def test_mkdir_oserror_does_not_raise(self) -> None:
        with (
            patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 60),
            patch("pathlib.Path.mkdir", side_effect=OSError("read-only filesystem")),
        ):
            # No exception should propagate
            context_native._store_health_cache({"key": "value"})


# ---------------------------------------------------------------------------
# 8. search_index limit clamping
# ---------------------------------------------------------------------------


class TestSearchIndexLimitClamping(unittest.TestCase):
    """search_index clamps limit to the range [1, 200]."""

    def _run_search(self, limit: int) -> list:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "mem.db"
            # Create minimal DB with the correct schema
            import sqlite3 as _sqlite3

            conn = _sqlite3.connect(str(db_path))
            conn.execute(
                """CREATE TABLE IF NOT EXISTS observations (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint      TEXT    UNIQUE NOT NULL,
                    source_type      TEXT    NOT NULL,
                    session_id       TEXT    NOT NULL,
                    title            TEXT    NOT NULL,
                    content          TEXT    NOT NULL,
                    tags_json        TEXT    NOT NULL,
                    file_path        TEXT    NOT NULL,
                    created_at       TEXT    NOT NULL,
                    created_at_epoch INTEGER NOT NULL,
                    updated_at_epoch INTEGER NOT NULL
                )"""
            )
            conn.commit()
            conn.close()
            # Point memory_index at an isolated DB for this test
            with patch.object(memory_index, "ensure_index_db", return_value=db_path):
                # Disable result cache for this test
                with patch.object(memory_index, "_SEARCH_CACHE_TTL", 0):
                    results = memory_index.search_index("test query", limit=limit)
            return results

    def test_limit_zero_clamped_to_one(self) -> None:
        # With limit=0 the function should clamp to max(1, 0)=1; no exception
        try:
            results = self._run_search(0)
            self.assertIsInstance(results, list)
        except Exception as exc:
            self.fail(f"search_index(limit=0) raised: {exc}")

    def test_limit_201_clamped_to_200(self) -> None:
        # With limit=201 the function should clamp to min(201, 200)=200; no exception
        try:
            results = self._run_search(201)
            self.assertIsInstance(results, list)
        except Exception as exc:
            self.fail(f"search_index(limit=201) raised: {exc}")

    def test_limit_negative_clamped_to_one(self) -> None:
        try:
            results = self._run_search(-5)
            self.assertIsInstance(results, list)
        except Exception as exc:
            self.fail(f"search_index(limit=-5) raised: {exc}")


# ---------------------------------------------------------------------------
# 9. CJK export-import round-trip
# ---------------------------------------------------------------------------


class TestCJKExportImportRoundTrip(unittest.TestCase):
    """Save a memory with CJK title, export, verify the JSON contains the CJK content."""

    def test_cjk_title_survives_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Path(tmpdir) / ".contextgo"
            storage.mkdir(parents=True, exist_ok=True)
            conversations = storage / "resources" / "shared" / "conversations"
            conversations.mkdir(parents=True, exist_ok=True)

            cjk_title = "多智能体协同测试记录"
            cjk_content = "这是一段测试内容，包含CJK字符。测试通过后记录结果。"

            # Write memory markdown
            import context_core

            md_path = context_core.write_memory_markdown(
                cjk_title,
                cjk_content,
                ["测试", "CJK"],
                conversations_root=conversations,
            )
            self.assertTrue(md_path.exists(), "Memory markdown file should be created")

            # Verify content
            md_text = md_path.read_text(encoding="utf-8")
            self.assertIn(cjk_title, md_text, "CJK title should appear in markdown")
            self.assertIn(cjk_content, md_text, "CJK content should appear in markdown")

    def test_cjk_observations_export_json_preserves_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "mem.db"

            import sqlite3 as _sqlite3

            conn = _sqlite3.connect(str(db_path))
            conn.execute(
                """CREATE TABLE IF NOT EXISTS observations (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint      TEXT    UNIQUE NOT NULL,
                    source_type      TEXT    NOT NULL,
                    session_id       TEXT    NOT NULL,
                    title            TEXT    NOT NULL,
                    content          TEXT    NOT NULL,
                    tags_json        TEXT    NOT NULL,
                    file_path        TEXT    NOT NULL,
                    created_at       TEXT    NOT NULL,
                    created_at_epoch INTEGER NOT NULL,
                    updated_at_epoch INTEGER NOT NULL
                )"""
            )
            cjk_title = "中文记忆标题"
            cjk_content = "包含汉字、日文ひらがな和한국어的内容。"
            conn.execute(
                "INSERT INTO observations "
                "(fingerprint, source_type, session_id, title, content, tags_json, file_path, "
                "created_at, created_at_epoch, updated_at_epoch) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "fp_cjk_test",
                    "history",
                    "",
                    cjk_title,
                    cjk_content,
                    "[]",
                    "test://cjk",
                    "2026-01-01T00:00:00",
                    1700000000,
                    1700000000,
                ),
            )
            conn.commit()
            conn.close()

            with (
                patch.object(memory_index, "ensure_index_db", return_value=db_path),
                patch.object(
                    memory_index,
                    "sync_index_from_storage",
                    return_value={"added": 0, "updated": 0, "removed": 0, "scanned": 0},
                ),
                patch.object(memory_index, "_SEARCH_CACHE_TTL", 0),
            ):
                results = memory_index.search_index(cjk_title, limit=10)

            # Verify CJK chars appear in exported JSON
            exported = json.dumps(results, ensure_ascii=False)
            self.assertIn(cjk_title, exported, "CJK title must survive JSON serialization")
            self.assertIn("汉字", exported, "CJK characters must be preserved (not escaped)")

    def test_cjk_roundtrip_export_import_payload(self) -> None:
        """Export payload and re-import; CJK chars must survive the round-trip."""
        cjk_title = "语义记忆导出测试"
        cjk_content = "验证export_observations_payload和import_observations_payload的CJK兼容性。"

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "mem.db"

            import sqlite3 as _sqlite3

            conn = _sqlite3.connect(str(db_path))
            conn.execute(
                """CREATE TABLE IF NOT EXISTS observations (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint      TEXT    UNIQUE NOT NULL,
                    source_type      TEXT    NOT NULL,
                    session_id       TEXT    NOT NULL,
                    title            TEXT    NOT NULL,
                    content          TEXT    NOT NULL,
                    tags_json        TEXT    NOT NULL,
                    file_path        TEXT    NOT NULL,
                    created_at       TEXT    NOT NULL,
                    created_at_epoch INTEGER NOT NULL,
                    updated_at_epoch INTEGER NOT NULL
                )"""
            )
            conn.commit()
            conn.close()

            with (
                patch.object(memory_index, "ensure_index_db", return_value=db_path),
                patch.object(
                    memory_index,
                    "sync_index_from_storage",
                    return_value={"added": 0, "updated": 0, "removed": 0, "scanned": 0},
                ),
                patch.object(memory_index, "_SEARCH_CACHE_TTL", 0),
            ):
                # Insert a CJK observation directly via import
                payload_in = {
                    "observations": [
                        {
                            "fingerprint": "fp_cjk_roundtrip",
                            "title": cjk_title,
                            "content": cjk_content,
                            "source_type": "history",
                            "file_path": "import://test",
                            "tags": ["CJK", "测试"],
                            "created_at": "2026-01-01T00:00:00",
                            "created_at_epoch": 1704067200,
                        }
                    ]
                }
                result = memory_index.import_observations_payload(payload_in, sync_from_storage=False)
                self.assertGreaterEqual(result["inserted"] + result["skipped"], 1)

                # Now export and verify CJK content
                export = memory_index.export_observations_payload(limit=10)
                exported_json = json.dumps(export, ensure_ascii=False)
                # The exported JSON must contain the CJK title
                self.assertIn(cjk_title, exported_json, "CJK title must appear in export payload JSON")


# ---------------------------------------------------------------------------
# 10. Concurrent sync_index_from_storage
# ---------------------------------------------------------------------------


class TestConcurrentSyncIndexFromStorage(unittest.TestCase):
    """Two threads calling sync_index_from_storage simultaneously should not raise OperationalError."""

    def test_concurrent_sync_no_operational_error(self) -> None:
        import sqlite3 as _sqlite3

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "mem.db"

            # Pre-create the DB with WAL mode and correct schema.
            conn = _sqlite3.connect(str(db_path), timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS observations (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint      TEXT    UNIQUE NOT NULL,
                    source_type      TEXT    NOT NULL,
                    session_id       TEXT    NOT NULL,
                    title            TEXT    NOT NULL,
                    content          TEXT    NOT NULL,
                    tags_json        TEXT    NOT NULL,
                    file_path        TEXT    NOT NULL,
                    created_at       TEXT    NOT NULL,
                    created_at_epoch INTEGER NOT NULL,
                    updated_at_epoch INTEGER NOT NULL
                )"""
            )
            conn.commit()
            conn.close()

            errors: list[Exception] = []

            def _sync_worker() -> None:
                try:
                    memory_index.sync_index_from_storage()
                except Exception as exc:
                    errors.append(exc)

            # Patch at the outer scope (not inside threads) to avoid
            # race conditions where concurrent patch/unpatch leaves the
            # module attribute in a corrupted state.
            with (
                patch.object(memory_index, "ensure_index_db", return_value=db_path),
                patch.object(memory_index, "_history_dirs", return_value=[]),
            ):
                t1 = threading.Thread(target=_sync_worker)
                t2 = threading.Thread(target=_sync_worker)
                t1.start()
                t2.start()
                t1.join(timeout=10)
                t2.join(timeout=10)

            operational_errors = [e for e in errors if isinstance(e, _sqlite3.OperationalError)]
            self.assertEqual(
                operational_errors,
                [],
                f"Concurrent sync raised OperationalError: {operational_errors}",
            )


if __name__ == "__main__":
    unittest.main()
