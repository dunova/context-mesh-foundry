#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_index


class SessionIndexTests(unittest.TestCase):
    def test_build_query_terms_extracts_anchor(self) -> None:
        terms = session_index.build_query_terms("继续搜索 GitHub 和 X 研究 notebookLM 的终端调用方案")
        lowered = {t.lower() for t in terms}
        self.assertIn("github", lowered)
        self.assertIn("notebooklm", lowered)

    def test_sync_and_search_local_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_root = root / ".codex" / "sessions" / "2026" / "03" / "25"
            codex_root.mkdir(parents=True)
            session_file = codex_root / "sample.jsonl"
            session_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {
                                    "id": "sample-session",
                                    "cwd": "/tmp/notebooklm-project",
                                    "timestamp": "2026-03-25T00:00:00Z",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {"type": "user_message", "message": "research NotebookLM integration"},
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            db_path = root / "session_index.db"
            with mock.patch.object(session_index, "_home", return_value=root):
                with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                    payload = session_index.health_payload()
                    self.assertTrue(payload["session_index_db_exists"])
                    self.assertGreaterEqual(payload["total_sessions"], 1)
                    text = session_index.format_search_results("NotebookLM", limit=5)
                    self.assertIn("sample-session", text)
                    self.assertIn("NotebookLM", text)

    def test_recent_sync_skips_rescan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_root = root / ".codex" / "sessions" / "2026" / "03" / "25"
            codex_root.mkdir(parents=True)
            (codex_root / "sample.jsonl").write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "sample-session",
                            "cwd": "/tmp/project",
                            "timestamp": "2026-03-25T00:00:00Z",
                        },
                    }
                ),
                encoding="utf-8",
            )
            db_path = root / "session_index.db"
            with mock.patch.object(session_index, "_home", return_value=root):
                with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                    first = session_index.sync_session_index(force=True)
                    second = session_index.sync_session_index(force=False)
                    self.assertGreaterEqual(first["scanned"], 1)
                    self.assertEqual(second["skipped_recent"], 1)

    def test_sync_handles_missing_cached_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "session_index.db"
            missing_path = root / "missing.jsonl"
            with mock.patch.object(session_index, "_home", return_value=root):
                with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                    with mock.patch.object(session_index, "_iter_sources", return_value=[("codex_session", missing_path)]):
                        stats = session_index.sync_session_index(force=True)
                        self.assertGreaterEqual(stats["scanned"], 1)
                        self.assertEqual(stats["added"], 0)

    def test_native_search_rows_when_enabled(self) -> None:
        mock_result = mock.Mock()
        mock_result.returncode = 0
        with mock.patch.object(session_index, "EXPERIMENTAL_SEARCH_BACKEND", "go"):
            with mock.patch.object(
                session_index.context_native,
                "run_native_scan",
                return_value=mock_result,
            ) as mock_run:
                with mock.patch.object(
                    session_index.context_native,
                    "extract_matches",
                    return_value=[
                        {
                            "source": "codex_session",
                            "session_id": "abc",
                            "path": "/tmp/a.jsonl",
                            "snippet": "NotebookLM match",
                        }
                    ],
                ):
                    rows = session_index._native_search_rows("NotebookLM", limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session_id"], "abc")
        mock_run.assert_called_once()

    def test_native_search_rows_filters_agents_noise(self) -> None:
        mock_result = mock.Mock()
        mock_result.returncode = 0
        with mock.patch.object(session_index, "EXPERIMENTAL_SEARCH_BACKEND", "go"):
            with mock.patch.object(session_index.context_native, "run_native_scan", return_value=mock_result):
                with mock.patch.object(
                    session_index.context_native,
                    "extract_matches",
                    return_value=[
                        {
                            "source": "codex_session",
                            "session_id": "noise",
                            "path": "/tmp/noise.jsonl",
                            "snippet": "# AGENTS.md instructions for /tmp NotebookLM",
                        },
                        {
                            "source": "codex_session",
                            "session_id": "clean",
                            "path": "/tmp/clean.jsonl",
                            "snippet": "NotebookLM integration decision",
                        },
                    ],
                ):
                    rows = session_index._native_search_rows("NotebookLM", limit=5)
        self.assertEqual([row["session_id"] for row in rows], ["clean"])

    def test_iter_sources_can_use_native_inventory(self) -> None:
        mock_result = mock.Mock()
        mock_result.returncode = 0
        with mock.patch.object(session_index, "EXPERIMENTAL_SYNC_BACKEND", "go"):
            with mock.patch.object(session_index.context_native, "run_native_scan", return_value=mock_result) as mock_run:
                with mock.patch.object(
                    session_index.context_native,
                    "inventory_items",
                    return_value=[("codex_session", Path("/tmp/native.jsonl"))],
                ):
                    items = session_index._iter_sources()
        self.assertEqual(items, [("codex_session", Path("/tmp/native.jsonl"))])
        mock_run.assert_called_once()

    def test_fetch_session_docs_by_paths_skips_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "session_index.db"
            canonical = session_index._normalize_file_path(Path("/tmp/dedup.jsonl"))
            with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                session_index.ensure_session_db()
                conn = sqlite3.connect(db_path)
                try:
                    conn.execute(
                        """
                        INSERT INTO session_documents(
                            file_path, source_type, session_id, title, content,
                            created_at, created_at_epoch, file_mtime, file_size, updated_at_epoch
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            canonical,
                            "codex_session",
                            "dedup",
                            "Dedup Session",
                            "Dedup NotebookLM content",
                            "2026-03-25T00:00:00Z",
                            1700000000,
                            123,
                            456,
                            1700000000,
                        ),
                    )
                    conn.commit()
                    conn.row_factory = sqlite3.Row
                    docs = session_index._fetch_session_docs_by_paths(
                        conn, ["/tmp/dedup.jsonl", "/tmp/dedup.jsonl"]
                    )
                    self.assertIn(canonical, docs)
                    self.assertEqual(docs[canonical]["session_id"], "dedup")
                finally:
                    conn.close()

    def test_enrich_native_rows_uses_index_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "session_index.db"
            canonical = session_index._normalize_file_path(Path("/tmp/native.jsonl"))
            with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                session_index.ensure_session_db()
                conn = sqlite3.connect(db_path)
                try:
                    conn.execute(
                        """
                        INSERT INTO session_documents(
                            file_path, source_type, session_id, title, content,
                            created_at, created_at_epoch, file_mtime, file_size, updated_at_epoch
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            canonical,
                            "codex_session",
                            "native-sample",
                            "Native Session Title",
                            "NotebookLM idea and decisions for the project",
                            "2026-03-25T00:00:00Z",
                            1700000000,
                            123,
                            456,
                            1700000000,
                        ),
                    )
                    conn.commit()
                    conn.row_factory = sqlite3.Row
                    rows = [
                        {"file_path": canonical, "snippet": "fallback snippet", "source_type": "native_session"}
                    ]
                    enriched = session_index._enrich_native_rows(rows, conn, ["NotebookLM"], limit=5)
                    self.assertEqual(enriched[0]["session_id"], "native-sample")
                    self.assertIn("NotebookLM", enriched[0]["snippet"])
                finally:
                    conn.close()

    def test_sync_session_index_canonicalizes_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "session_index.db"
            fake_dir = Path(tmpdir) / "src"
            fake_dir.mkdir(parents=True, exist_ok=True)
            real_file = fake_dir / "sample.jsonl"
            real_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {
                                    "id": "canonical-session",
                                    "cwd": "/tmp/canonical",
                                    "timestamp": "2026-03-25T00:00:00Z",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {"type": "user_message", "message": "canonical NotebookLM content"},
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            alias = fake_dir / "alias.jsonl"
            alias.symlink_to(real_file)
            with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                original_iter = session_index._iter_sources
                try:
                    session_index._iter_sources = lambda: [
                        ("codex_session", alias),
                        ("codex_session", real_file),
                    ]
                    stats = session_index.sync_session_index(force=True)
                finally:
                    session_index._iter_sources = original_iter
            self.assertEqual(stats["added"], 1)
            self.assertEqual(stats["updated"], 0)
            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute("SELECT file_path FROM session_documents").fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0][0], str(real_file.resolve()))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
