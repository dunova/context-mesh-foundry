#!/usr/bin/env python3
"""Tests for source_adapters.py."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import source_adapters  # noqa: E402


class SourceAdaptersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="cg_sources_")
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

    def _create_opencode_db(self) -> Path:
        db_path = self.home / ".local" / "share" / "opencode" / "opencode.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT, time_created INTEGER, time_updated INTEGER)"
        )
        conn.execute("CREATE TABLE part (session_id TEXT, id TEXT PRIMARY KEY, data TEXT, time_created INTEGER)")
        conn.execute(
            "INSERT INTO session VALUES (?, ?, ?, ?, ?)",
            ("ses_open_1", "OpenCode Session", "/work/opencode", 1700000000000, 1700000005000),
        )
        conn.execute(
            "INSERT INTO part VALUES (?, ?, ?, ?)",
            ("ses_open_1", "prt_1", json.dumps({"type": "text", "text": "OpenCode says hello"}), 1700000001000),
        )
        conn.commit()
        conn.close()
        return db_path

    def _create_kilo_storage(self) -> Path:
        storage_root = self.home / ".local" / "share" / "kilo" / "storage"
        session_path = storage_root / "session" / "global" / "ses_kilo_1.json"
        part_path = storage_root / "part" / "msg_1" / "prt_1.json"
        part_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(
            json.dumps(
                {
                    "id": "ses_kilo_1",
                    "directory": "/work/kilo",
                    "title": "Kilo Session",
                    "time": {"created": 1700000010000, "updated": 1700000019000},
                }
            ),
            encoding="utf-8",
        )
        part_path.write_text(
            json.dumps({"id": "prt_1", "sessionID": "ses_kilo_1", "type": "text", "text": '"Kilo asks hi"'}),
            encoding="utf-8",
        )
        return storage_root

    def _create_openclaw_session(self) -> Path:
        session_path = self.home / ".openclaw" / "agents" / "agent-a" / "sessions" / "ses_claw_1.jsonl"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(
            "\n".join(
                [
                    json.dumps({"type": "message", "text": "OpenClaw hello"}, ensure_ascii=False),
                    json.dumps({"payload": {"content": [{"type": "text", "text": "OpenClaw followup"}]}}),
                ]
            ),
            encoding="utf-8",
        )
        return session_path

    def test_sync_all_adapters_writes_all_supported_outputs(self) -> None:
        self._create_opencode_db()
        self._create_kilo_storage()
        self._create_openclaw_session()

        with mock.patch.object(source_adapters, "_home", return_value=self.home):
            result = source_adapters.sync_all_adapters()

        self.assertTrue(result["opencode_session"]["detected"])
        self.assertTrue(result["kilo_session"]["detected"])
        self.assertTrue(result["openclaw_session"]["detected"])

        adapter_root = source_adapters._adapter_root(self.home)
        self.assertTrue(any((adapter_root / "opencode_session").glob("*.jsonl")))
        self.assertTrue(any((adapter_root / "kilo_session").glob("*.jsonl")))
        self.assertTrue(any((adapter_root / "openclaw_session").glob("*.jsonl")))

    def test_discover_index_sources_includes_adapter_sessions_and_histories(self) -> None:
        self._create_opencode_db()
        self._create_kilo_storage()
        self._create_openclaw_session()
        codex_history = self.home / ".codex" / "history.jsonl"
        codex_history.parent.mkdir(parents=True, exist_ok=True)
        codex_history.write_text(json.dumps({"text": "codex history"}), encoding="utf-8")

        with mock.patch.object(source_adapters, "_home", return_value=self.home):
            discovered = source_adapters.discover_index_sources()

        discovered_types = {source_type for source_type, _ in discovered}
        self.assertIn("codex_history", discovered_types)
        self.assertIn("opencode_session", discovered_types)
        self.assertIn("kilo_session", discovered_types)
        self.assertIn("openclaw_session", discovered_types)

    def test_source_inventory_reports_detected_platforms(self) -> None:
        self._create_opencode_db()
        self._create_kilo_storage()
        self._create_openclaw_session()

        with mock.patch.object(source_adapters, "_home", return_value=self.home):
            inventory = source_adapters.source_inventory()

        platforms = {item["platform"]: item for item in inventory["platforms"]}
        self.assertTrue(platforms["opencode"]["detected"])
        self.assertTrue(platforms["kilo"]["detected"])
        self.assertTrue(platforms["openclaw"]["detected"])

    def test_source_freshness_snapshot_includes_adapter_and_new_platforms(self) -> None:
        opencode_db = self._create_opencode_db()
        self._create_kilo_storage()
        self._create_openclaw_session()

        with mock.patch.object(source_adapters, "_home", return_value=self.home):
            snapshot = source_adapters.source_freshness_snapshot()

        self.assertTrue(snapshot["opencode_db"]["exists"])
        self.assertEqual(snapshot["opencode_db"]["path"], str(opencode_db))
        self.assertTrue(snapshot["kilo_storage"]["exists"])
        self.assertTrue(snapshot["openclaw_sessions_root"]["exists"])
        self.assertIn("opencode_session_count", snapshot["adapter_sessions"])

    def test_sync_all_adapters_handles_missing_sources_and_prunes_stale(self) -> None:
        stale_root = source_adapters._adapter_root(self.home) / "opencode_session"
        stale_root.mkdir(parents=True, exist_ok=True)
        stale_file = stale_root / "stale.jsonl"
        stale_file.write_text('{"text":"stale"}', encoding="utf-8")

        with mock.patch.object(source_adapters, "_home", return_value=self.home):
            result = source_adapters.sync_all_adapters()

        self.assertFalse(result["opencode_session"]["detected"])
        self.assertFalse(result["kilo_session"]["detected"])
        self.assertFalse(result["openclaw_session"]["detected"])
        self.assertFalse(stale_file.exists())

    def test_opencode_sync_falls_back_to_message_rows_when_parts_missing(self) -> None:
        db_path = self.home / ".local" / "share" / "opencode" / "opencode.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT, time_created INTEGER, time_updated INTEGER)"
        )
        conn.execute("CREATE TABLE part (session_id TEXT, id TEXT PRIMARY KEY, data TEXT, time_created INTEGER)")
        conn.execute("CREATE TABLE message (session_id TEXT, id TEXT PRIMARY KEY, data TEXT, time_created INTEGER)")
        conn.execute(
            "INSERT INTO session VALUES (?, ?, ?, ?, ?)",
            ("ses_open_2", "Fallback Session", "/work/fallback", 1700000000000, 1700000005000),
        )
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("ses_open_2", "msg_1", json.dumps({"text": "Message fallback text"}), 1700000001000),
        )
        conn.commit()
        conn.close()

        with mock.patch.object(source_adapters, "_home", return_value=self.home):
            result = source_adapters.sync_all_adapters()

        self.assertTrue(result["opencode_session"]["detected"])
        written = next((source_adapters._adapter_root(self.home) / "opencode_session").glob("*.jsonl"))
        self.assertIn("Message fallback text", written.read_text(encoding="utf-8"))

    def test_helper_text_extractors_cover_edge_cases(self) -> None:
        self.assertIsInstance(source_adapters._home(), Path)
        self.assertIsNone(source_adapters._iso_or_none(None))
        self.assertIsInstance(source_adapters._iso_or_none(1700000000), str)
        self.assertEqual(source_adapters._normalize_text_value(None), None)
        self.assertEqual(source_adapters._normalize_text_value("  "), None)
        self.assertEqual(source_adapters._normalize_text_value('"quoted value"'), "quoted value")
        self.assertEqual(source_adapters._normalize_text_value('"unterminated'), '"unterminated')
        self.assertFalse(source_adapters._write_adapter_file(self.storage / "empty.jsonl", [], 1700000000, meta=None))

        texts = source_adapters._extract_text_fragments(
            {
                "type": "reasoning",
                "text": "hello",
                "payload": {"content": [{"type": "text", "text": "world"}, {"text": "world"}]},
            }
        )
        self.assertEqual(texts, ["hello", "world"])
        self.assertEqual(source_adapters._extract_text_fragments(None), [])
        self.assertEqual(source_adapters._extract_text_fragments(["a", {"text": "b"}, 3]), ["a", "b"])
        self.assertEqual(source_adapters._extract_text_fragments(3), [])

    def test_discover_index_sources_dedupes_duplicate_glob_results(self) -> None:
        codex_root = self.home / ".codex" / "sessions"
        codex_root.mkdir(parents=True)
        session_file = codex_root / "dup.jsonl"
        session_file.write_text(json.dumps({"text": "dup"}), encoding="utf-8")

        real_rglob = Path.rglob

        def fake_rglob(self_path: Path, pattern: str):  # noqa: ANN001
            results = list(real_rglob(self_path, pattern))
            if self_path == codex_root:
                return results + results
            return results

        with (
            mock.patch.object(source_adapters, "_home", return_value=self.home),
            mock.patch.object(source_adapters, "sync_all_adapters", return_value={}),
            mock.patch.object(Path, "rglob", fake_rglob),
        ):
            discovered = source_adapters.discover_index_sources(self.home)

        codex_entries = [(source_type, path) for source_type, path in discovered if source_type == "codex_session"]
        self.assertEqual(len(codex_entries), 1)

    def test_kilo_sync_handles_sparse_session_metadata(self) -> None:
        storage_root = self.home / ".local" / "share" / "kilo" / "storage"
        session_path = storage_root / "session" / "global" / "ses_kilo_sparse.json"
        part_path = storage_root / "part" / "msg_2" / "prt_2.json"
        part_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(
            json.dumps({"id": "ses_kilo_sparse", "time": {"updated": 1700000020000}}), encoding="utf-8"
        )
        part_path.write_text(
            json.dumps({"id": "prt_2", "sessionID": "ses_kilo_sparse", "type": "text", "text": '"Sparse text"'}),
            encoding="utf-8",
        )
        ignored_part = storage_root / "part" / "msg_ignored" / "prt_ignored.json"
        ignored_part.parent.mkdir(parents=True, exist_ok=True)
        ignored_part.write_text(json.dumps({"id": "prt_bad", "sessionID": "", "text": "ignored"}), encoding="utf-8")

        with mock.patch.object(source_adapters, "_home", return_value=self.home):
            result = source_adapters.sync_all_adapters()

        self.assertTrue(result["kilo_session"]["detected"])
        written = next(
            p
            for p in (source_adapters._adapter_root(self.home) / "kilo_session").glob("*.jsonl")
            if "ses_kilo_sparse" in p.name
        )
        content = written.read_text(encoding="utf-8")
        self.assertIn("Sparse text", content)
        self.assertIn("ses_kilo_sparse", content)

    def test_openclaw_sync_skips_blank_and_invalid_lines(self) -> None:
        session_path = self.home / ".openclaw" / "agents" / "agent-b" / "sessions" / "ses_claw_2.jsonl"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text('\nnot-json\n{"text":"claw text"}\n', encoding="utf-8")

        with mock.patch.object(source_adapters, "_home", return_value=self.home):
            result = source_adapters.sync_all_adapters()

        self.assertTrue(result["openclaw_session"]["detected"])
        written = next((source_adapters._adapter_root(self.home) / "openclaw_session").glob("*.jsonl"))
        self.assertIn("claw text", written.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
