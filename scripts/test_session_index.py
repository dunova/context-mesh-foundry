#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()
