#!/usr/bin/env python3
"""R11 extended tests for memory_index module — targeting uncovered lines."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_index


class TestStripPrivateBlocks(unittest.TestCase):
    def test_removes_private_block(self) -> None:
        text = "before <private>secret</private> after"
        result = memory_index.strip_private_blocks(text)
        self.assertNotIn("secret", result)
        self.assertIn("before", result)
        self.assertIn("after", result)

    def test_removes_stray_private_tags(self) -> None:
        text = "content <private> stray tag"
        result = memory_index.strip_private_blocks(text)
        self.assertNotIn("<private>", result)

    def test_handles_empty_string(self) -> None:
        self.assertEqual(memory_index.strip_private_blocks(""), "")

    def test_multiline_private_block_removed(self) -> None:
        text = "start\n<private>\nline1\nline2\n</private>\nend"
        result = memory_index.strip_private_blocks(text)
        self.assertNotIn("line1", result)
        self.assertIn("start", result)
        self.assertIn("end", result)


class TestSanitizeText(unittest.TestCase):
    def test_redacts_github_token(self) -> None:
        text = "token ghp_" + "A" * 25
        result = memory_index._sanitize_text(text)
        self.assertIn("***REDACTED***", result)
        self.assertNotIn("ghp_", result)

    def test_redacts_openai_key(self) -> None:
        text = "key sk-" + "x" * 20
        result = memory_index._sanitize_text(text)
        self.assertIn("***REDACTED***", result)

    def test_preserves_normal_text(self) -> None:
        text = "normal text without secrets"
        result = memory_index._sanitize_text(text)
        self.assertEqual(result, text)

    def test_strips_private_blocks_before_redaction(self) -> None:
        text = "before <private>secret</private> after"
        result = memory_index._sanitize_text(text)
        self.assertNotIn("secret", result)


class TestToEpoch(unittest.TestCase):
    def test_parses_valid_iso_timestamp(self) -> None:
        epoch = memory_index._to_epoch("2026-03-25T10:00:00", fallback=0)
        self.assertGreater(epoch, 0)

    def test_returns_fallback_for_empty(self) -> None:
        self.assertEqual(memory_index._to_epoch("", fallback=42), 42)

    def test_returns_fallback_for_invalid(self) -> None:
        self.assertEqual(memory_index._to_epoch("not-a-date", fallback=99), 99)


class TestGetIndexDbPath(unittest.TestCase):
    def test_uses_env_override(self) -> None:
        with mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": "/tmp/custom_memory.db"}, clear=False):
            result = memory_index.get_index_db_path()
            self.assertEqual(str(result), "/tmp/custom_memory.db")

    def test_uses_storage_root_when_no_env(self) -> None:
        env_copy = {k: v for k, v in os.environ.items() if k != "MEMORY_INDEX_DB_PATH"}
        with mock.patch.dict(os.environ, env_copy, clear=True):
            result = memory_index.get_index_db_path()
            self.assertIsInstance(result, Path)
            self.assertTrue(str(result).endswith("memory_index.db"))


class TestEnsureIndexDb(unittest.TestCase):
    def test_creates_db_and_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "index" / "memory_index.db"
            with mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False):
                result = memory_index.ensure_index_db()
                self.assertTrue(result.exists())
                conn = sqlite3.connect(result)
                try:
                    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    self.assertIn("observations", tables)
                finally:
                    conn.close()


class TestParseMarkdown(unittest.TestCase):
    def test_parses_basic_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "memory.md"
            path.write_text(
                "# My Memory Title\nTags: ai, ml\nDate: 2026-03-25\n## Content\nThis is the content.\n",
                encoding="utf-8",
            )
            obs = memory_index._parse_markdown(path)
            self.assertIsNotNone(obs)
            assert obs is not None
            self.assertEqual(obs.title, "My Memory Title")
            self.assertIn("ai", json.loads(obs.tags_json))
            self.assertIn("This is the content.", obs.content)

    def test_returns_none_for_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.md"
            path.write_text("", encoding="utf-8")
            obs = memory_index._parse_markdown(path)
            self.assertIsNone(obs)

    def test_returns_none_for_whitespace_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "spaces.md"
            path.write_text("   \n\n  \n", encoding="utf-8")
            obs = memory_index._parse_markdown(path)
            self.assertIsNone(obs)

    def test_returns_none_on_oserror(self) -> None:
        path = Path("/nonexistent/dir/memory.md")
        obs = memory_index._parse_markdown(path)
        self.assertIsNone(obs)

    def test_uses_path_stem_when_no_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "my_memory_stem.md"
            path.write_text("Some content without a title header.\n", encoding="utf-8")
            obs = memory_index._parse_markdown(path)
            self.assertIsNotNone(obs)
            assert obs is not None
            self.assertEqual(obs.title, "my_memory_stem")

    def test_detects_conversation_source_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conv_dir = Path(tmpdir) / "conversations"
            conv_dir.mkdir()
            path = conv_dir / "session_abc.md"
            path.write_text("# Conv Memory\nContent here.\n", encoding="utf-8")
            obs = memory_index._parse_markdown(path)
            self.assertIsNotNone(obs)
            assert obs is not None
            self.assertEqual(obs.source_type, "conversation")

    def test_detects_history_source_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hist_dir = Path(tmpdir) / "history"
            hist_dir.mkdir()
            path = hist_dir / "session_xyz.md"
            path.write_text("# Hist Memory\nContent here.\n", encoding="utf-8")
            obs = memory_index._parse_markdown(path)
            self.assertIsNotNone(obs)
            assert obs is not None
            self.assertEqual(obs.source_type, "history")

    def test_strips_private_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "private_test.md"
            path.write_text(
                "# Title\n<private>secret</private>\nPublic content here.\n",
                encoding="utf-8",
            )
            obs = memory_index._parse_markdown(path)
            self.assertIsNotNone(obs)
            assert obs is not None
            self.assertNotIn("secret", obs.content)
            self.assertIn("Public content here.", obs.content)

    def test_returns_none_when_content_section_all_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "all_private.md"
            # Use ## Content section so body_lines only contains the private block
            path.write_text("# Title\n## Content\n<private>everything secret</private>\n", encoding="utf-8")
            obs = memory_index._parse_markdown(path)
            self.assertIsNone(obs)


class TestSyncIndexFromStorage(unittest.TestCase):
    def test_adds_new_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            (history_dir / "memory1.md").write_text(
                "# Memory One\nDate: 2026-03-25\n## Content\nFirst memory content.\n",
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                result = memory_index.sync_index_from_storage()
            self.assertGreaterEqual(result["added"], 1)

    def test_updates_changed_observation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            md_file = history_dir / "memory_update.md"
            md_file.write_text(
                "# Update Memory\nDate: 2026-03-25\n## Content\nOriginal content.\n",
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                result1 = memory_index.sync_index_from_storage()
                self.assertEqual(result1["added"], 1)
                md_file.write_text(
                    "# Update Memory\nDate: 2026-03-25\n## Content\nUpdated content.\n",
                    encoding="utf-8",
                )
                result2 = memory_index.sync_index_from_storage()
                self.assertEqual(result2["updated"], 1)

    def test_removes_deleted_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            md_file = history_dir / "memory_delete.md"
            md_file.write_text(
                "# Delete Memory\nDate: 2026-03-25\n## Content\nContent to delete.\n",
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                result1 = memory_index.sync_index_from_storage()
                self.assertEqual(result1["added"], 1)
                md_file.unlink()
                result2 = memory_index.sync_index_from_storage()
                self.assertGreaterEqual(result2["removed"], 1)

    def test_skips_nonexistent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            nonexistent = Path(tmpdir) / "nonexistent_dir"
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[nonexistent]),
            ):
                result = memory_index.sync_index_from_storage()
            self.assertEqual(result["scanned"], 0)

    def test_handles_duplicate_rows_same_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            md_file = history_dir / "dup_memory.md"
            md_file.write_text("# Dup Memory\n## Content\nDuplicate test content.\n", encoding="utf-8")
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                # First sync adds the observation
                memory_index.sync_index_from_storage()
                # Manually insert a duplicate row for the same file_path
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    obs = memory_index._parse_markdown(md_file)
                    assert obs is not None
                    fp2 = hashlib.sha256(b"different-fingerprint").hexdigest()
                    now_epoch = int(datetime.now().timestamp())
                    conn.execute(
                        """INSERT INTO observations(fingerprint, source_type, session_id, title, content,
                           tags_json, file_path, created_at, created_at_epoch, updated_at_epoch)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            fp2,
                            obs.source_type,
                            obs.session_id,
                            obs.title,
                            obs.content,
                            obs.tags_json,
                            obs.file_path,
                            obs.created_at,
                            obs.created_at_epoch,
                            now_epoch,
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
                # Second sync should clean up the duplicate
                result2 = memory_index.sync_index_from_storage()
                self.assertGreaterEqual(result2["removed"], 1)


class TestSearchIndex(unittest.TestCase):
    def test_returns_matching_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            (history_dir / "search_mem.md").write_text(
                "# Search Memory\n## Content\nNotebookLM integration result.\n",
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                memory_index.sync_index_from_storage()
                results = memory_index.search_index("NotebookLM")
            self.assertGreaterEqual(len(results), 1)
            self.assertIn("NotebookLM", results[0]["content"])

    def test_returns_empty_for_no_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            with mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False):
                results = memory_index.search_index("xyz_no_match_at_all_r11")
            self.assertEqual(results, [])

    def test_respects_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            for i in range(5):
                (history_dir / f"mem_{i}.md").write_text(
                    f"# Memory {i}\n## Content\nshared_token_{i} content.\n",
                    encoding="utf-8",
                )
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                memory_index.sync_index_from_storage()
                results = memory_index.search_index("", limit=2)
            self.assertLessEqual(len(results), 2)

    def test_filters_by_source_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            conv_dir = Path(tmpdir) / "conversations"
            history_dir.mkdir()
            conv_dir.mkdir()
            (history_dir / "hist_mem.md").write_text(
                "# Hist\n## Content\nhistory source content.\n",
                encoding="utf-8",
            )
            (conv_dir / "conv_mem.md").write_text(
                "# Conv\n## Content\nconversation source content.\n",
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir, conv_dir]),
            ):
                memory_index.sync_index_from_storage()
                results = memory_index.search_index("", source_type="history")
            for r in results:
                self.assertEqual(r["source_type"], "history")

    def test_filters_by_date_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            (history_dir / "date_mem.md").write_text(
                "# Date Memory\nDate: 2026-03-25\n## Content\nDate range test.\n",
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                memory_index.sync_index_from_storage()
                # Future date range — should find nothing
                results_empty = memory_index.search_index("", date_start_epoch=9999999999)
                self.assertEqual(results_empty, [])
                # Past date range — should find the observation
                results_found = memory_index.search_index("", date_end_epoch=9999999999)
                self.assertGreaterEqual(len(results_found), 1)


class TestTimelineIndex(unittest.TestCase):
    def test_returns_empty_for_nonexistent_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            with mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False):
                memory_index.ensure_index_db()
                result = memory_index.timeline_index(anchor_id=99999)
            self.assertEqual(result, [])

    def test_returns_timeline_around_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            for i in range(5):
                (history_dir / f"timeline_{i}.md").write_text(
                    f"# Timeline {i}\nDate: 2026-03-2{i + 1}\n## Content\nTimeline content {i}.\n",
                    encoding="utf-8",
                )
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                memory_index.sync_index_from_storage()
                results = memory_index.search_index("Timeline", limit=10)
                self.assertGreater(len(results), 0)
                anchor_id = results[len(results) // 2]["id"]
                timeline = memory_index.timeline_index(anchor_id=anchor_id, depth_before=2, depth_after=2)
            self.assertGreater(len(timeline), 0)


class TestGetObservationsByIds(unittest.TestCase):
    def test_returns_empty_for_empty_ids(self) -> None:
        result = memory_index.get_observations_by_ids([])
        self.assertEqual(result, [])

    def test_returns_matching_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            (history_dir / "byid_mem.md").write_text(
                "# By ID Memory\n## Content\nContent for by-id lookup.\n",
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                memory_index.sync_index_from_storage()
                results = memory_index.search_index("by-id lookup")
                self.assertGreater(len(results), 0)
                obs_id = results[0]["id"]
                fetched = memory_index.get_observations_by_ids([obs_id])
            self.assertEqual(len(fetched), 1)
            self.assertEqual(fetched[0]["id"], obs_id)


class TestIndexStats(unittest.TestCase):
    def test_returns_stats_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            with mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False):
                stats = memory_index.index_stats()
            self.assertIn("db_path", stats)
            self.assertIn("total_observations", stats)
            self.assertIn("latest_epoch", stats)
            self.assertIsInstance(stats["total_observations"], int)

    def test_counts_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            (history_dir / "stats_mem.md").write_text(
                "# Stats Memory\n## Content\nContent for stats test.\n",
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                memory_index.sync_index_from_storage()
                stats = memory_index.index_stats()
            self.assertGreaterEqual(stats["total_observations"], 1)


class TestExportObservationsPayload(unittest.TestCase):
    def test_returns_export_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[]),
            ):
                payload = memory_index.export_observations_payload()
            self.assertIn("exported_at", payload)
            self.assertIn("observations", payload)
            self.assertIn("total_observations", payload)
            self.assertIn("sync", payload)

    def test_export_with_query_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            (history_dir / "export_mem.md").write_text(
                "# Export Memory\n## Content\nExportable content.\n",
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                payload = memory_index.export_observations_payload(query="Exportable")
            self.assertGreaterEqual(payload["total_observations"], 1)


class TestImportObservationsPayload(unittest.TestCase):
    def test_raises_value_error_for_invalid_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[]),
            ):
                with self.assertRaises(ValueError):
                    memory_index.import_observations_payload({"observations": "not a list"})

    def test_imports_valid_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            payload = {
                "observations": [
                    {
                        "fingerprint": hashlib.sha256(b"import_test_r11").hexdigest(),
                        "source_type": "import",
                        "session_id": "import-session",
                        "title": "Import Test R11",
                        "content": "Imported memory content for R11 test.",
                        "tags": ["import", "test"],
                        "file_path": "import://json",
                        "created_at": "2026-03-25T10:00:00",
                        "created_at_epoch": 1700000000,
                    }
                ]
            }
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[]),
            ):
                result = memory_index.import_observations_payload(payload, sync_from_storage=False)
            self.assertEqual(result["inserted"], 1)
            self.assertEqual(result["skipped"], 0)

    def test_skips_duplicate_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            fp = hashlib.sha256(b"dup_import_r11").hexdigest()
            obs = {
                "fingerprint": fp,
                "source_type": "import",
                "session_id": "dup-session",
                "title": "Dup Import",
                "content": "Content for dup test.",
                "tags": [],
                "file_path": "import://json",
                "created_at": "2026-03-25T10:00:00",
                "created_at_epoch": 1700000000,
            }
            payload = {"observations": [obs]}
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[]),
            ):
                r1 = memory_index.import_observations_payload(payload, sync_from_storage=False)
                r2 = memory_index.import_observations_payload(payload, sync_from_storage=False)
            self.assertEqual(r1["inserted"], 1)
            self.assertEqual(r2["inserted"], 0)
            self.assertEqual(r2["skipped"], 1)

    def test_skips_empty_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            payload = {
                "observations": [
                    {
                        "fingerprint": "",
                        "source_type": "import",
                        "session_id": "empty",
                        "title": "Empty Content",
                        "content": "",
                        "tags": [],
                        "file_path": "import://json",
                        "created_at": "2026-03-25T10:00:00",
                        "created_at_epoch": 1700000000,
                    }
                ]
            }
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[]),
            ):
                result = memory_index.import_observations_payload(payload, sync_from_storage=False)
            self.assertEqual(result["inserted"], 0)
            self.assertEqual(result["skipped"], 1)

    def test_skips_non_dict_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            payload = {"observations": ["not a dict", 42, None]}
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[]),
            ):
                result = memory_index.import_observations_payload(payload, sync_from_storage=False)
            self.assertEqual(result["inserted"], 0)


class TestNormalizeImportObservation(unittest.TestCase):
    def test_redacts_absolute_file_path(self) -> None:
        raw = {
            "title": "Test",
            "content": "Content",
            "file_path": "/absolute/local/path.md",
            "source_type": "history",
            "session_id": "s1",
        }
        result = memory_index._normalize_import_observation(raw)
        self.assertEqual(result["file_path"], "import://local-path-redacted")

    def test_redacts_tilde_path(self) -> None:
        raw = {
            "title": "Test",
            "content": "Content",
            "file_path": "~/local/path.md",
        }
        result = memory_index._normalize_import_observation(raw)
        self.assertEqual(result["file_path"], "import://local-path-redacted")

    def test_derives_fingerprint_when_absent(self) -> None:
        raw = {
            "title": "Test",
            "content": "Unique content for fingerprint derivation.",
            "fingerprint": "",
        }
        result = memory_index._normalize_import_observation(raw)
        self.assertNotEqual(result["fingerprint"], "")
        self.assertEqual(len(result["fingerprint"]), 64)  # SHA-256 hex

    def test_preserves_existing_fingerprint(self) -> None:
        fp = hashlib.sha256(b"existing").hexdigest()
        raw = {
            "fingerprint": fp,
            "title": "Test",
            "content": "Content",
        }
        result = memory_index._normalize_import_observation(raw)
        self.assertEqual(result["fingerprint"], fp)

    def test_handles_list_tags(self) -> None:
        raw = {
            "title": "Test",
            "content": "Content",
            "tags": ["ai", "ml", "python"],
        }
        result = memory_index._normalize_import_observation(raw)
        tags = json.loads(result["tags_json"])
        self.assertIn("ai", tags)
        self.assertIn("ml", tags)

    def test_handles_non_list_tags(self) -> None:
        raw = {
            "title": "Test",
            "content": "Content",
            "tags": "single-tag",
        }
        result = memory_index._normalize_import_observation(raw)
        tags = json.loads(result["tags_json"])
        self.assertIn("single-tag", tags)


class TestObsWhereClause(unittest.TestCase):
    def test_empty_query_all_source_type(self) -> None:
        where_clause, args = memory_index._obs_where_clause("", "all")
        self.assertEqual(where_clause, "")
        self.assertEqual(args, [])

    def test_with_query(self) -> None:
        where_clause, args = memory_index._obs_where_clause("notebooklm", "all")
        self.assertIn("LIKE", where_clause)
        self.assertIn("%notebooklm%", args)

    def test_with_source_type(self) -> None:
        where_clause, args = memory_index._obs_where_clause("", "history")
        self.assertIn("source_type", where_clause)
        self.assertIn("history", args)

    def test_with_query_and_source_type(self) -> None:
        where_clause, args = memory_index._obs_where_clause("test", "conversation")
        self.assertIn("LIKE", where_clause)
        self.assertIn("source_type", where_clause)


class TestRowToDict(unittest.TestCase):
    def test_converts_row_to_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            (history_dir / "row_dict.md").write_text(
                "# Row Dict\nTags: a, b\n## Content\nRow dict content.\n",
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                memory_index.sync_index_from_storage()
                results = memory_index.search_index("Row Dict")
            self.assertGreater(len(results), 0)
            r = results[0]
            self.assertIn("id", r)
            self.assertIn("source_type", r)
            self.assertIn("tags", r)
            self.assertIsInstance(r["tags"], list)


# ---------------------------------------------------------------------------
# R16: target remaining uncovered lines in memory_index
# ---------------------------------------------------------------------------


class TestSyncIndexParsesNoneMarkdown(unittest.TestCase):
    """Line 319: _parse_markdown returns None → continue path in sync_index."""

    def test_sync_skips_unreadable_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            # Create a file that will produce empty content after strip
            (history_dir / "empty_file.md").write_text("   \n   \n", encoding="utf-8")
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                result = memory_index.sync_index_from_storage()
            # scanned = 1, added = 0 (skipped due to None parse)
            self.assertEqual(result["added"], 0)
            self.assertEqual(result["scanned"], 1)


class TestSyncIndexRenameByFingerprint(unittest.TestCase):
    """Lines 352-354: reconcile by fingerprint when file was renamed."""

    def test_sync_updates_path_on_rename(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()

            # First sync with original file name
            content = "# Rename Test\n\n## Content\nContent for rename detection.\n"
            orig = history_dir / "original_name.md"
            orig.write_text(content, encoding="utf-8")

            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                r1 = memory_index.sync_index_from_storage()

            self.assertEqual(r1["added"], 1)

            # Rename the file
            renamed = history_dir / "renamed_file.md"
            orig.rename(renamed)

            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                r2 = memory_index.sync_index_from_storage()

            # The renamed file should have been recognized via fingerprint match
            self.assertEqual(r2["updated"], 1)


class TestRowToDictMalformedTags(unittest.TestCase):
    """Lines 408->412, 410-411: _row_to_dict with malformed tags_json."""

    def test_row_to_dict_with_malformed_tags_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            (history_dir / "good_file.md").write_text("# Good\n\n## Content\nSome content here.\n", encoding="utf-8")
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                memory_index.sync_index_from_storage()

            # Manually corrupt the tags_json in the DB to trigger the except branch
            import sqlite3 as _sqlite3

            with _sqlite3.connect(str(db_path)) as conn:
                conn.execute("UPDATE observations SET tags_json = 'not valid json{{'")

            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
            ):
                results = memory_index.search_index("Some content")
            # Should still return results even with corrupt tags
            self.assertGreater(len(results), 0)
            self.assertEqual(results[0]["tags"], [])


class TestExportObservationsPayloadPagination(unittest.TestCase):
    """Lines 574->585, 583: pagination loop (offset += len(batch) path)."""

    def test_export_uses_pagination_when_many_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()

            # Create more than 200 files to trigger the pagination offset path
            for i in range(205):
                (history_dir / f"mem_{i:03d}.md").write_text(
                    f"# Memory {i}\n\n## Content\nContent number {i}.\n",
                    encoding="utf-8",
                )

            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                # Export with a limit larger than one page (200)
                payload = memory_index.export_observations_payload(limit=205)

            self.assertGreaterEqual(payload["total_observations"], 205)

    def test_export_pagination_stops_at_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory_index.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()

            for i in range(205):
                (history_dir / f"item_{i:03d}.md").write_text(
                    f"# Item {i}\n\n## Content\nContent item {i}.\n",
                    encoding="utf-8",
                )

            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                payload = memory_index.export_observations_payload(limit=10)

            self.assertEqual(payload["total_observations"], 10)


if __name__ == "__main__":
    unittest.main()
