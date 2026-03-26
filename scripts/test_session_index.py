#!/usr/bin/env python3
"""Unit tests for session_index module."""
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
        self.assertIn("终端调用", terms)
        self.assertIn("调用方案", terms)

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

    def test_sync_and_search_archived_codex_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archived_root = root / ".codex" / "archived_sessions"
            archived_root.mkdir(parents=True)
            session_file = archived_root / "archived.jsonl"
            session_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {
                                    "id": "archived-session",
                                    "cwd": "/tmp/old-project",
                                    "timestamp": "2026-03-06T00:00:00Z",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {
                                    "type": "user_message",
                                    "message": "先做 onecontext 预热，再继续 NotebookLM 方案调研",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "payload": {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [
                                        {"type": "output_text", "text": "NotebookLM 的真实历史结论已经确认。"}
                                    ],
                                },
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            db_path = root / "session_index.db"
            with mock.patch.object(session_index, "_home", return_value=root):
                with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                    session_index.sync_session_index(force=True)
                    text = session_index.format_search_results("NotebookLM", limit=5)
                    self.assertIn("archived-session", text)
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

    def test_search_noise_penalty_demotes_prompt_like_content(self) -> None:
        noisy = session_index._search_noise_penalty(
            "skills-repo",
            "Current Skill Name: notebooklm\nCurrent Description:\nQuery AND UPLOAD to Google NotebookLM",
            "/tmp/skills/file.jsonl",
        )
        clean = session_index._search_noise_penalty(
            "product-notes",
            "NotebookLM integration decision for local runtime",
            "/tmp/contextgo/notes.jsonl",
        )
        self.assertGreater(noisy, clean)

    def test_current_repo_meta_result_is_excluded(self) -> None:
        repo = str(Path("/workspace/ContextGO").resolve())
        with mock.patch("pathlib.Path.cwd", return_value=Path(repo)):
            self.assertTrue(
                session_index._is_current_repo_meta_result(
                    repo,
                    "已收到任务。写集仅限 scripts/session_index.py。建议验证命令：python3 scripts/context_cli.py search NotebookLM",
                    "/tmp/session.jsonl",
                )
            )
            self.assertTrue(
                session_index._is_current_repo_meta_result(
                    repo,
                    "职责只限测试，不要改文件。测试集使用 artifacts/testsets/dataset_2026-03-25.json。",
                    "/tmp/session.jsonl",
                )
            )
            self.assertTrue(
                session_index._is_current_repo_meta_result(
                    repo,
                    "仓库：/workspace/ContextGO。你负责 `benchmarks/**`。改动文件: benchmarks/run.py",
                    "/tmp/session.jsonl",
                )
            )
            self.assertFalse(
                session_index._is_current_repo_meta_result(
                    "/tmp/other",
                    "NotebookLM product decision note",
                    "/tmp/session.jsonl",
                )
            )

    def test_build_snippet_prefers_conclusion_window(self) -> None:
        text = (
            "NotebookLM 过程说明，先做预热。"
            " 这里还是过程段。"
            " 最终交付：NotebookLM 的真实结论已经确认，并已完成验证。"
        )
        snippet = session_index._build_snippet(text, ["NotebookLM"])
        self.assertIn("最终交付", snippet)

    def test_build_snippet_prefers_summary_marker_without_term_hit(self) -> None:
        text = (
            "/workspace/ContextGO "
            "一些过程说明。 "
            "变更概览：统一默认安装目录与服务标签。 "
            "后面还有更多细节。"
        )
        snippet = session_index._build_snippet(text, ["2026-03-25"])
        self.assertIn("变更概览", snippet)

    def test_format_search_results_compacts_long_snippet(self) -> None:
        with mock.patch.object(
            session_index,
            "_search_rows",
            return_value=[
                {
                    "source_type": "codex_session",
                    "session_id": "s1",
                    "title": "/tmp/project",
                    "file_path": "/tmp/file.jsonl",
                    "created_at": "2026-03-26T00:00:00Z",
                    "snippet": "A" * 300,
                }
            ],
        ):
            text = session_index.format_search_results("x", limit=1)
        self.assertIn("A" * 50, text)
        self.assertIn("…", text)
        self.assertLess(len(text.split("> ", 1)[1]), 140)

    def test_path_only_content_is_demoted(self) -> None:
        self.assertTrue(
            session_index._looks_like_path_only_content(
                "/workspace/ContextGO",
                "/workspace/ContextGO",
            )
        )
        self.assertFalse(
            session_index._looks_like_path_only_content(
                "/workspace/ContextGO",
                "变更概览：统一默认安装目录。",
            )
        )

    def test_literal_long_query_falls_back_to_anchor_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archived_root = root / ".codex" / "archived_sessions"
            archived_root.mkdir(parents=True)
            session_file = archived_root / "archived.jsonl"
            session_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {
                                    "id": "long-query-session",
                                    "cwd": "/tmp/github-research",
                                    "timestamp": "2026-03-06T00:00:00Z",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {
                                    "type": "user_message",
                                    "message": "继续搜索 GitHub 和 X，研究 notebookLM 的终端调用方案",
                                },
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            db_path = root / "session_index.db"
            query = "继续搜索 GitHub 和 X 研究 notebookLM 的终端调用方案"
            with mock.patch.object(session_index, "_home", return_value=root):
                with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                    session_index.sync_session_index(force=True)
                    rows = session_index._search_rows(query, limit=5, literal=True)
            self.assertEqual(rows[0]["session_id"], "long-query-session")

    def test_literal_long_query_fallback_skips_current_repo_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archived_root = root / ".codex" / "archived_sessions"
            session_root = root / ".codex" / "sessions" / "2026" / "03" / "26"
            archived_root.mkdir(parents=True)
            session_root.mkdir(parents=True)
            (session_root / "current.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {
                                    "id": "current-session",
                                    "cwd": "/workspace/ContextGO",
                                    "timestamp": "2026-03-26T00:00:00Z",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {
                                    "type": "user_message",
                                    "message": "仓库：/workspace/ContextGO。你负责 GitHub notebookLM 测试。",
                                },
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            (archived_root / "archived.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {
                                    "id": "archived-session",
                                    "cwd": "/tmp/github-research",
                                    "timestamp": "2026-03-06T00:00:00Z",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {
                                    "type": "user_message",
                                    "message": "继续搜索 GitHub 和 X，研究 notebookLM 的终端调用方案",
                                },
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            db_path = root / "session_index.db"
            query = "继续搜索 GitHub 和 X 研究 notebookLM 的终端调用方案"
            with mock.patch.object(session_index, "_home", return_value=root):
                with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                    with mock.patch("pathlib.Path.cwd", return_value=Path("/workspace/ContextGO")):
                        session_index.sync_session_index(force=True)
                        rows = session_index._search_rows(query, limit=5, literal=True)
            self.assertEqual(rows[0]["session_id"], "archived-session")


if __name__ == "__main__":
    unittest.main()
