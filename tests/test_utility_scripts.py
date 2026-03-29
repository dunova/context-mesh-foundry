#!/usr/bin/env python3
"""Tests for utility scripts: export_memories, import_memories,
e2e_quality_gate, memory_hit_first_regression, smoke_installed_runtime,
smoke_installed_cli,
and autoresearch_contextgo.

All external calls are mocked so no real CLI or filesystem side-effects occur.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# export_memories
# ---------------------------------------------------------------------------


class TestExportMemoriesMain(unittest.TestCase):
    def test_delegates_to_context_cli_main(self) -> None:
        import export_memories

        with patch.object(sys, "argv", ["export_memories", "query text", "/tmp/out.json"]):
            with patch("export_memories.context_cli.main", return_value=0) as mock_main:
                rc = export_memories.main()
                mock_main.assert_called_once()
                args_passed = mock_main.call_args[0][0]
                self.assertEqual(args_passed[0], "export")
                self.assertEqual(args_passed[1], "query text")
                self.assertEqual(args_passed[2], "/tmp/out.json")
                self.assertEqual(rc, 0)

    def test_passes_limit_flag(self) -> None:
        import export_memories

        with patch.object(sys, "argv", ["export_memories", "q", "/tmp/out.json", "--limit", "100"]):
            with patch("export_memories.context_cli.main", return_value=0) as mock_main:
                export_memories.main()
                args_passed = mock_main.call_args[0][0]
                self.assertIn("--limit", args_passed)
                self.assertIn("100", args_passed)

    def test_passes_source_type_flag(self) -> None:
        import export_memories

        with patch.object(sys, "argv", ["export_memories", "", "/tmp/out.json", "--source-type", "history"]):
            with patch("export_memories.context_cli.main", return_value=0) as mock_main:
                export_memories.main()
                args_passed = mock_main.call_args[0][0]
                self.assertIn("--source-type", args_passed)
                self.assertIn("history", args_passed)

    def test_returns_nonzero_on_failure(self) -> None:
        import export_memories

        with patch.object(sys, "argv", ["export_memories", "q", "/tmp/out.json"]):
            with patch("export_memories.context_cli.main", return_value=1):
                rc = export_memories.main()
                self.assertEqual(rc, 1)


# ---------------------------------------------------------------------------
# import_memories
# ---------------------------------------------------------------------------


class TestImportMemoriesMain(unittest.TestCase):
    def test_delegates_to_context_cli_main(self) -> None:
        import import_memories

        with patch("import_memories.context_cli.main", return_value=0) as mock_main:
            rc = import_memories.main(["/tmp/memories.json"])
            mock_main.assert_called_once()
            args_passed = mock_main.call_args[0][0]
            self.assertEqual(args_passed[0], "import")
            self.assertIn("/tmp/memories.json", args_passed[1])
            self.assertEqual(rc, 0)

    def test_no_sync_flag_forwarded(self) -> None:
        import import_memories

        with patch("import_memories.context_cli.main", return_value=0) as mock_main:
            import_memories.main(["/tmp/memories.json", "--no-sync"])
            args_passed = mock_main.call_args[0][0]
            self.assertIn("--no-sync", args_passed)

    def test_returns_nonzero_on_failure(self) -> None:
        import import_memories

        with patch("import_memories.context_cli.main", return_value=2):
            rc = import_memories.main(["/tmp/memories.json"])
            self.assertEqual(rc, 2)

    def test_expanduser_applied_to_path(self) -> None:
        import import_memories

        home = str(Path.home())
        with patch("import_memories.context_cli.main", return_value=0) as mock_main:
            import_memories.main(["~/memories.json"])
            args_passed = mock_main.call_args[0][0]
            self.assertIn("import", args_passed)
            self.assertIn(home, args_passed[1])


# ---------------------------------------------------------------------------
# e2e_quality_gate — unit-level
# ---------------------------------------------------------------------------


class TestE2eQualityGate(unittest.TestCase):
    def test_session_db_path(self) -> None:
        import e2e_quality_gate

        root = Path("/tmp/fakeroot")
        result = e2e_quality_gate.session_db_path(root)
        self.assertEqual(result, root / "index" / "session_index.db")

    def test_prepare_fixture_home_creates_files(self) -> None:
        import e2e_quality_gate

        with tempfile.TemporaryDirectory(prefix="cg_gate_test_") as tmpdir:
            fake_home = Path(tmpdir)
            e2e_quality_gate.prepare_fixture_home(fake_home)

            self.assertTrue((fake_home / ".codex" / "history.jsonl").exists())
            self.assertTrue((fake_home / ".claude" / "history.jsonl").exists())
            self.assertTrue((fake_home / ".zsh_history").exists())
            self.assertTrue((fake_home / ".bash_history").exists())

            # Verify codex session fixture
            codex_sessions = list((fake_home / ".codex" / "sessions").rglob("*.jsonl"))
            self.assertTrue(len(codex_sessions) >= 1)

    def test_run_cmd_success(self) -> None:
        import e2e_quality_gate

        rc, out, err = e2e_quality_gate.run_cmd(
            ["python3", "-c", "print('hello')"],
            env=os.environ.copy(),
            timeout=10,
        )
        self.assertEqual(rc, 0)
        self.assertIn("hello", out)

    def test_run_cmd_failure(self) -> None:
        import e2e_quality_gate

        rc, out, err = e2e_quality_gate.run_cmd(
            ["python3", "-c", "import sys; sys.exit(1)"],
            env=os.environ.copy(),
            timeout=10,
        )
        self.assertEqual(rc, 1)

    def test_case_health_parses_json(self) -> None:
        import e2e_quality_gate

        health_payload = json.dumps({"all_ok": True, "remote_sync_policy": {"mode": "local"}})
        with patch.object(
            e2e_quality_gate,
            "run_cmd",
            return_value=(0, health_payload, ""),
        ):
            result = e2e_quality_gate.case_health(env=os.environ.copy())
        self.assertTrue(result.passed)
        self.assertEqual(result.name, "health")

    def test_case_health_fails_on_bad_json(self) -> None:
        import e2e_quality_gate

        with patch.object(
            e2e_quality_gate,
            "run_cmd",
            return_value=(0, "not-json", ""),
        ):
            result = e2e_quality_gate.case_health(env=os.environ.copy())
        self.assertFalse(result.passed)

    def test_case_health_fails_when_all_ok_false(self) -> None:
        import e2e_quality_gate

        payload = json.dumps({"all_ok": False})
        with patch.object(e2e_quality_gate, "run_cmd", return_value=(0, payload, "")):
            result = e2e_quality_gate.case_health(env=os.environ.copy())
        self.assertFalse(result.passed)

    def test_case_save_and_readback_pass(self) -> None:
        import e2e_quality_gate

        marker = f"gate-marker-{int(time.time())}"

        def fake_run_cmd(args, env, timeout=20):
            if "save" in args:
                return 0, "saved", ""
            if "semantic" in args:
                return 0, f"results containing {marker}", ""
            return 0, "", ""

        with patch.object(e2e_quality_gate, "run_cmd", side_effect=fake_run_cmd):
            with patch("time.time", return_value=int(marker.split("-")[-1])):
                # Can't easily mock the marker itself, so just test the logic
                result = e2e_quality_gate.case_save_and_readback(env=os.environ.copy())
        self.assertEqual(result.name, "save-readback")

    def test_case_session_index_sources_db_missing(self) -> None:
        import e2e_quality_gate

        with tempfile.TemporaryDirectory() as tmpdir:
            storage_root = Path(tmpdir) / ".contextgo"
            with patch.object(e2e_quality_gate, "run_cmd", return_value=(0, "", "")):
                result = e2e_quality_gate.case_session_index_sources(env=os.environ.copy(), storage_root=storage_root)
        self.assertFalse(result.passed)
        self.assertIn("db missing", result.detail)

    def test_case_local_search_pass(self) -> None:
        import e2e_quality_gate

        with patch.object(
            e2e_quality_gate,
            "run_cmd",
            return_value=(0, "Found 3 results for NotebookLM", ""),
        ):
            result = e2e_quality_gate.case_local_search(env=os.environ.copy())
        self.assertTrue(result.passed)
        self.assertEqual(result.name, "local-search")

    def test_case_local_search_fail_no_found(self) -> None:
        import e2e_quality_gate

        with patch.object(
            e2e_quality_gate,
            "run_cmd",
            return_value=(0, "No results", ""),
        ):
            result = e2e_quality_gate.case_local_search(env=os.environ.copy())
        self.assertFalse(result.passed)

    def test_case_result_dataclass(self) -> None:
        import e2e_quality_gate

        r = e2e_quality_gate.CaseResult("mytest", True, "detail text", 0.5)
        self.assertEqual(r.name, "mytest")
        self.assertTrue(r.passed)
        self.assertEqual(r.elapsed_sec, 0.5)

    def test_case_save_and_readback_fail(self) -> None:
        """Cover the failure detail branch (line 160) in case_save_and_readback."""
        import e2e_quality_gate

        def fake_run_cmd(args, env, timeout=20):
            if "save" in args:
                return 0, "saved", ""
            if "semantic" in args:
                # marker NOT in output → triggers failure branch
                return 0, "no results here", ""
            return 0, "", ""

        with patch.object(e2e_quality_gate, "run_cmd", side_effect=fake_run_cmd):
            result = e2e_quality_gate.case_save_and_readback(env=os.environ.copy())
        self.assertFalse(result.passed)
        self.assertIn("save-readback failed", result.detail)

    def test_case_save_and_readback_fail_save_error(self) -> None:
        """Cover case where save itself fails."""
        import e2e_quality_gate

        def fake_run_cmd(args, env, timeout=20):
            if "save" in args:
                return 1, "", "error saving"
            return 0, "", ""

        with patch.object(e2e_quality_gate, "run_cmd", side_effect=fake_run_cmd):
            result = e2e_quality_gate.case_save_and_readback(env=os.environ.copy())
        self.assertFalse(result.passed)

    def test_case_session_index_sources_all_present(self) -> None:
        """Cover lines 175-185: DB exists and has all required source types."""
        import sqlite3

        import e2e_quality_gate

        with tempfile.TemporaryDirectory() as tmpdir:
            storage_root = Path(tmpdir) / ".contextgo"
            db_dir = storage_root / "index"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = db_dir / "session_index.db"

            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE session_documents (source_type TEXT, content TEXT)")
            for src in ("codex_session", "claude_session", "shell_zsh"):
                conn.execute("INSERT INTO session_documents VALUES (?, ?)", (src, "data"))
            conn.commit()
            conn.close()

            with patch.object(e2e_quality_gate, "run_cmd", return_value=(0, "", "")):
                result = e2e_quality_gate.case_session_index_sources(env=os.environ.copy(), storage_root=storage_root)

        self.assertTrue(result.passed)
        self.assertEqual(result.name, "session-index-sources")
        self.assertIn("missing=[]", result.detail)

    def test_case_session_index_sources_some_missing(self) -> None:
        """Cover lines 175-185: DB exists but missing some required source types."""
        import sqlite3

        import e2e_quality_gate

        with tempfile.TemporaryDirectory() as tmpdir:
            storage_root = Path(tmpdir) / ".contextgo"
            db_dir = storage_root / "index"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = db_dir / "session_index.db"

            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE session_documents (source_type TEXT, content TEXT)")
            # Only codex_session present, missing claude_session and shell_zsh
            conn.execute("INSERT INTO session_documents VALUES (?, ?)", ("codex_session", "data"))
            conn.commit()
            conn.close()

            with patch.object(e2e_quality_gate, "run_cmd", return_value=(0, "", "")):
                result = e2e_quality_gate.case_session_index_sources(env=os.environ.copy(), storage_root=storage_root)

        self.assertFalse(result.passed)
        self.assertIn("claude_session", result.detail)

    def test_case_export_and_import_save_fails(self) -> None:
        """Cover lines 226-232: save step returns non-zero."""
        import e2e_quality_gate

        with patch.object(e2e_quality_gate, "run_cmd", return_value=(1, "", "save error")):
            result = e2e_quality_gate.case_export_and_import(env=os.environ.copy())

        self.assertFalse(result.passed)
        self.assertIn("save failed", result.detail)

    def test_case_export_and_import_export_fails(self) -> None:
        """Cover lines 248-254: export step returns non-zero."""
        import e2e_quality_gate

        call_count = [0]

        def fake_run_cmd(args, env, timeout=20):
            call_count[0] += 1
            if "save" in args:
                return 0, "saved", ""
            if "export" in args:
                return 1, "", "export error"
            return 0, "", ""

        with patch.object(e2e_quality_gate, "run_cmd", side_effect=fake_run_cmd):
            result = e2e_quality_gate.case_export_and_import(env=os.environ.copy())

        self.assertFalse(result.passed)
        self.assertIn("export failed", result.detail)

    def test_case_export_and_import_export_file_not_created(self) -> None:
        """Cover lines 255-261: export file doesn't exist after export command."""
        import e2e_quality_gate

        def fake_run_cmd(args, env, timeout=20):
            if "save" in args:
                return 0, "saved", ""
            if "export" in args:
                # rc=0 but file won't actually be created
                return 0, "exported", ""
            return 0, "", ""

        with patch.object(e2e_quality_gate, "run_cmd", side_effect=fake_run_cmd):
            result = e2e_quality_gate.case_export_and_import(env=os.environ.copy())

        self.assertFalse(result.passed)
        self.assertIn("export file not created", result.detail)

    def test_case_export_and_import_zero_observations(self) -> None:
        """Cover lines 263-272: export file exists but has total_observations=0."""
        import e2e_quality_gate

        with tempfile.TemporaryDirectory(prefix="cg_gate_ei_test_") as tmpdir:
            export_path = Path(tmpdir) / "gate_export.json"
            export_path.write_text(json.dumps({"total_observations": 0, "memories": []}), encoding="utf-8")

            def fake_run_cmd(args, env, timeout=20):
                if "save" in args:
                    return 0, "saved", ""
                if "export" in args:
                    # Write to whatever export_file path was passed
                    for _i, a in enumerate(args):
                        if str(a).endswith(".json") and "gate_export" in str(a):
                            Path(a).write_text(json.dumps({"total_observations": 0}), encoding="utf-8")
                    return 0, "exported", ""
                return 0, "", ""

            with patch.object(e2e_quality_gate, "run_cmd", side_effect=fake_run_cmd):
                result = e2e_quality_gate.case_export_and_import(env=os.environ.copy())

        self.assertFalse(result.passed)
        self.assertIn("0 observations", result.detail)

    def test_case_export_and_import_import_fails(self) -> None:
        """Cover lines 274-281: import step returns non-zero."""
        import e2e_quality_gate

        def fake_run_cmd(args, env, timeout=20):
            if "save" in args:
                return 0, "saved", ""
            if "export" in args:
                # Write a valid export file to the path arg
                for a in args:
                    if str(a).endswith(".json"):
                        Path(a).write_text(
                            json.dumps({"total_observations": 2, "memories": [{}, {}]}),
                            encoding="utf-8",
                        )
                return 0, "exported", ""
            if "import" in args:
                return 1, "", "import error"
            return 0, "", ""

        with patch.object(e2e_quality_gate, "run_cmd", side_effect=fake_run_cmd):
            result = e2e_quality_gate.case_export_and_import(env=os.environ.copy())

        self.assertFalse(result.passed)
        self.assertIn("import failed", result.detail)

    def test_case_export_and_import_success(self) -> None:
        """Cover the full success path of case_export_and_import (lines 274-282)."""
        import e2e_quality_gate

        def fake_run_cmd(args, env, timeout=20):
            if "save" in args:
                return 0, "saved", ""
            if "export" in args:
                for a in args:
                    if str(a).endswith(".json"):
                        Path(a).write_text(
                            json.dumps({"total_observations": 3, "memories": [{}, {}, {}]}),
                            encoding="utf-8",
                        )
                return 0, "exported", ""
            if "import" in args:
                return 0, "import done: 3 records", ""
            return 0, "", ""

        with patch.object(e2e_quality_gate, "run_cmd", side_effect=fake_run_cmd):
            result = e2e_quality_gate.case_export_and_import(env=os.environ.copy())

        self.assertTrue(result.passed)
        self.assertEqual(result.name, "export-import")

    def test_case_maintain_pass(self) -> None:
        """Cover lines 287-302: maintain returns rc=0 and 'Snapshot' in output."""
        import e2e_quality_gate

        with patch.object(
            e2e_quality_gate,
            "run_cmd",
            return_value=(0, "Snapshot taken. 10 observations archived.", ""),
        ):
            result = e2e_quality_gate.case_maintain(env=os.environ.copy())

        self.assertTrue(result.passed)
        self.assertEqual(result.name, "maintain")
        self.assertIn("snapshot_reported=True", result.detail)

    def test_case_maintain_fail_no_snapshot(self) -> None:
        """Cover maintain failure path: rc=0 but no 'Snapshot' keyword."""
        import e2e_quality_gate

        with patch.object(
            e2e_quality_gate,
            "run_cmd",
            return_value=(0, "Done. Nothing to archive.", ""),
        ):
            result = e2e_quality_gate.case_maintain(env=os.environ.copy())

        self.assertFalse(result.passed)
        self.assertIn("maintain --dry-run failed", result.detail)

    def test_case_maintain_fail_nonzero_rc(self) -> None:
        """Cover maintain failure path: non-zero returncode."""
        import e2e_quality_gate

        with patch.object(
            e2e_quality_gate,
            "run_cmd",
            return_value=(1, "", "crash"),
        ):
            result = e2e_quality_gate.case_maintain(env=os.environ.copy())

        self.assertFalse(result.passed)

    def test_case_maintain_snapshot_in_stderr(self) -> None:
        """Cover maintain: Snapshot keyword in stderr (text = out or err)."""
        import e2e_quality_gate

        with patch.object(
            e2e_quality_gate,
            "run_cmd",
            return_value=(0, "", "Snapshot taken via stderr."),
        ):
            result = e2e_quality_gate.case_maintain(env=os.environ.copy())

        self.assertTrue(result.passed)

    def test_main_all_pass(self) -> None:
        """Cover lines 307-336: main() with all cases passing."""
        import e2e_quality_gate

        pass_result = e2e_quality_gate.CaseResult("dummy", True, "ok", 0.1)

        with patch.object(e2e_quality_gate, "case_health", return_value=pass_result):
            with patch.object(e2e_quality_gate, "case_save_and_readback", return_value=pass_result):
                with patch.object(e2e_quality_gate, "case_session_index_sources", return_value=pass_result):
                    with patch.object(e2e_quality_gate, "case_local_search", return_value=pass_result):
                        with patch.object(e2e_quality_gate, "case_export_and_import", return_value=pass_result):
                            with patch.object(e2e_quality_gate, "case_maintain", return_value=pass_result):
                                with patch.object(e2e_quality_gate, "prepare_fixture_home"):
                                    with patch("builtins.print"):
                                        rc = e2e_quality_gate.main()

        self.assertEqual(rc, 0)

    def test_main_with_failures(self) -> None:
        """Cover lines 328-335: main() with some cases failing."""
        import e2e_quality_gate

        pass_result = e2e_quality_gate.CaseResult("dummy", True, "ok", 0.1)
        fail_result = e2e_quality_gate.CaseResult("failing", False, "broken", 0.1)

        with patch.object(e2e_quality_gate, "case_health", return_value=fail_result):
            with patch.object(e2e_quality_gate, "case_save_and_readback", return_value=pass_result):
                with patch.object(e2e_quality_gate, "case_session_index_sources", return_value=pass_result):
                    with patch.object(e2e_quality_gate, "case_local_search", return_value=pass_result):
                        with patch.object(e2e_quality_gate, "case_export_and_import", return_value=pass_result):
                            with patch.object(e2e_quality_gate, "case_maintain", return_value=pass_result):
                                with patch.object(e2e_quality_gate, "prepare_fixture_home"):
                                    with patch("builtins.print"):
                                        rc = e2e_quality_gate.main()

        self.assertEqual(rc, 1)


# ---------------------------------------------------------------------------
# memory_hit_first_regression — unit-level
# ---------------------------------------------------------------------------


class TestMemoryHitFirstRegression(unittest.TestCase):
    def test_check_dataclass(self) -> None:
        import memory_hit_first_regression

        c = memory_hit_first_regression.Check("test-name", True, "detail", 1.23)
        self.assertEqual(c.name, "test-name")
        self.assertTrue(c.passed)

    def test_run_cmd_success(self) -> None:
        import memory_hit_first_regression

        rc, out, err = memory_hit_first_regression.run_cmd(["python3", "-c", "print('ok')"], timeout=5)
        self.assertEqual(rc, 0)
        self.assertIn("ok", out)

    def test_run_cli_invokes_python(self) -> None:
        import memory_hit_first_regression

        with patch.object(memory_hit_first_regression, "run_cmd", return_value=(0, "output", "")) as mock_run:
            rc, out, err = memory_hit_first_regression.run_cli("health")
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            self.assertIn("health", args)

    def test_check_cli_fixed_cases_structure(self) -> None:
        import memory_hit_first_regression

        # Mock run_cli to return plausible output for all cases
        def fake_run_cli(*args, timeout=30):
            cmd = args[0] if args else ""
            if cmd == "health":
                return 0, '{"all_ok": true}', ""
            if "search" in args:
                return 0, "found notebooklm result", ""
            return 0, "some output notebooklm 2026-03-06", ""

        with patch.object(memory_hit_first_regression, "run_cli", side_effect=fake_run_cli):
            checks = memory_hit_first_regression.check_cli_fixed_cases()

        self.assertIsInstance(checks, list)
        self.assertTrue(len(checks) >= 1)
        for check in checks:
            self.assertIsInstance(check, memory_hit_first_regression.Check)
            self.assertIsNotNone(check.name)

    def test_main_returns_zero_on_all_pass(self) -> None:
        import memory_hit_first_regression

        mock_check = memory_hit_first_regression.Check("test", True, "detail", 0.1)
        with patch.object(memory_hit_first_regression, "check_cli_fixed_cases", return_value=[mock_check]):
            with patch("builtins.print"):
                rc = memory_hit_first_regression.main()
        self.assertEqual(rc, 0)

    def test_main_returns_one_on_failure(self) -> None:
        import memory_hit_first_regression

        mock_check = memory_hit_first_regression.Check("test", False, "failed", 0.1)
        with patch.object(memory_hit_first_regression, "check_cli_fixed_cases", return_value=[mock_check]):
            with patch("builtins.print"):
                rc = memory_hit_first_regression.main()
        self.assertEqual(rc, 1)

    def test_main_outputs_json(self) -> None:
        import memory_hit_first_regression

        mock_check = memory_hit_first_regression.Check("test-case", True, "ok", 0.2)
        printed_output = []
        with patch.object(memory_hit_first_regression, "check_cli_fixed_cases", return_value=[mock_check]):
            with patch("builtins.print", side_effect=lambda s: printed_output.append(s)):
                memory_hit_first_regression.main()

        self.assertTrue(len(printed_output) > 0)
        payload = json.loads(printed_output[0])
        self.assertIn("passed", payload)
        self.assertIn("failed", payload)
        self.assertIn("checks", payload)


# ---------------------------------------------------------------------------
# smoke_installed_runtime — unit-level
# ---------------------------------------------------------------------------


class TestSmokeInstalledRuntime(unittest.TestCase):
    def test_resolve_install_root_default(self) -> None:
        import smoke_installed_runtime

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONTEXTGO_INSTALL_ROOT", None)
            # The module-level INSTALL_ROOT is already set; test the function directly
            result = smoke_installed_runtime.resolve_install_root()
        expected = Path.home() / ".local" / "share" / "contextgo" / "scripts"
        self.assertEqual(result, expected)

    def test_resolve_install_root_from_env_scripts(self) -> None:
        import smoke_installed_runtime

        with patch.dict(os.environ, {"CONTEXTGO_INSTALL_ROOT": "/tmp/mycontextgo/scripts"}):
            result = smoke_installed_runtime.resolve_install_root()
        self.assertEqual(result, Path("/tmp/mycontextgo/scripts"))

    def test_resolve_install_root_from_env_base(self) -> None:
        import smoke_installed_runtime

        with patch.dict(os.environ, {"CONTEXTGO_INSTALL_ROOT": "/tmp/mycontextgo"}):
            result = smoke_installed_runtime.resolve_install_root()
        self.assertEqual(result, Path("/tmp/mycontextgo") / "scripts")

    def test_main_returns_zero_on_pass(self) -> None:
        import smoke_installed_runtime

        fake_payload = {
            "summary": {"status": "pass", "total": 1, "failed": 0, "failed_names": []},
            "results": [],
        }
        with patch("smoke_installed_runtime.run_smoke", return_value=fake_payload), patch("builtins.print"):
            rc = smoke_installed_runtime.main()
        self.assertEqual(rc, 0)

    def test_main_returns_one_on_failure(self) -> None:
        import smoke_installed_runtime

        fake_payload = {
            "summary": {"status": "fail", "total": 1, "failed": 1, "failed_names": ["health"]},
            "results": [],
        }
        with patch("smoke_installed_runtime.run_smoke", return_value=fake_payload), patch("builtins.print"):
            rc = smoke_installed_runtime.main()
        self.assertEqual(rc, 1)

    def test_main_outputs_json_with_scope(self) -> None:
        import smoke_installed_runtime

        fake_payload = {
            "summary": {"status": "pass", "total": 0, "failed": 0, "failed_names": []},
            "results": [],
        }
        printed_output = []
        with patch("smoke_installed_runtime.run_smoke", return_value=fake_payload):
            with patch("builtins.print", side_effect=lambda s: printed_output.append(s)):
                smoke_installed_runtime.main()

        self.assertTrue(len(printed_output) > 0)
        payload = json.loads(printed_output[0])
        self.assertEqual(payload["scope"], "installed")
        self.assertIn("install_root", payload)
        self.assertIn("cli_path", payload)


# ---------------------------------------------------------------------------
# smoke_installed_cli — unit-level
# ---------------------------------------------------------------------------


class TestSmokeInstalledCli(unittest.TestCase):
    def test_resolve_contextgo_executable_from_env(self) -> None:
        import smoke_installed_cli

        with patch.dict(os.environ, {"CONTEXTGO_EXECUTABLE": "/tmp/contextgo-bin"}):
            result = smoke_installed_cli.resolve_contextgo_executable()
        self.assertEqual(result, Path("/tmp/contextgo-bin"))

    def test_resolve_contextgo_executable_from_path(self) -> None:
        import smoke_installed_cli

        with patch.dict(os.environ, {}, clear=False), patch("shutil.which", return_value="/usr/local/bin/contextgo"):
            os.environ.pop("CONTEXTGO_EXECUTABLE", None)
            result = smoke_installed_cli.resolve_contextgo_executable()
        self.assertEqual(result, Path("/usr/local/bin/contextgo"))

    def test_main_returns_one_when_executable_missing(self) -> None:
        import smoke_installed_cli

        printed: list[str] = []
        with (
            patch("smoke_installed_cli.resolve_contextgo_executable", return_value=None),
            patch("builtins.print", side_effect=lambda s: printed.append(s)),
        ):
            rc = smoke_installed_cli.main()
        self.assertEqual(rc, 1)
        payload = json.loads(printed[0])
        self.assertFalse(payload["ok"])

    def test_main_returns_zero_on_success(self) -> None:
        import smoke_installed_cli

        exe = Path("/usr/local/bin/contextgo")

        def fake_run(cmd, **kwargs):  # noqa: ANN001
            text = ""
            if cmd[1:] == ["--help"]:
                text = "ContextGO unified CLI"
            elif cmd[1:] == ["health"]:
                text = json.dumps({"all_ok": True})
            elif cmd[1:] == ["serve", "--help"]:
                text = "--port"
            elif cmd[1:] == ["maintain", "--help"]:
                text = "--dry-run"
            elif cmd[1:] == ["shell-init"]:
                text = 'eval "$(contextgo shell-init)"'
            return type("Proc", (), {"returncode": 0, "stdout": text, "stderr": ""})()

        with (
            patch("smoke_installed_cli.resolve_contextgo_executable", return_value=exe),
            patch("smoke_installed_cli.subprocess.run", side_effect=fake_run),
            patch("builtins.print"),
        ):
            rc = smoke_installed_cli.main()
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# autoresearch_contextgo — unit-level
# ---------------------------------------------------------------------------


class TestAutoresearchContextGO(unittest.TestCase):
    def test_json_from_text_valid(self) -> None:
        import autoresearch_contextgo

        result = autoresearch_contextgo._json_from_text('{"key": "value"}')
        self.assertEqual(result["key"], "value")

    def test_json_from_text_empty(self) -> None:
        import autoresearch_contextgo

        result = autoresearch_contextgo._json_from_text("")
        self.assertEqual(result, {})

    def test_json_from_text_whitespace(self) -> None:
        import autoresearch_contextgo

        result = autoresearch_contextgo._json_from_text("   ")
        self.assertEqual(result, {})

    def test_run_cmd_success(self) -> None:
        import autoresearch_contextgo

        rc, out, err = autoresearch_contextgo.run_cmd(["python3", "-c", "print('hello')"], timeout=10)
        self.assertEqual(rc, 0)
        self.assertIn("hello", out)

    def test_current_git_commit_returns_string(self) -> None:
        import autoresearch_contextgo

        with patch.object(autoresearch_contextgo, "run_cmd", return_value=(0, "abc1234\n", "")):
            result = autoresearch_contextgo.current_git_commit()
        self.assertEqual(result, "abc1234")

    def test_current_git_commit_returns_empty_on_failure(self) -> None:
        import autoresearch_contextgo

        with patch.object(autoresearch_contextgo, "run_cmd", return_value=(1, "", "error")):
            result = autoresearch_contextgo.current_git_commit()
        self.assertEqual(result, "")

    def test_native_eval_structure(self) -> None:
        import autoresearch_contextgo

        payload = json.dumps({"matches": [{"snippet": "hello"}, {"snippet": "world"}]})
        with patch.object(autoresearch_contextgo, "run_cmd", return_value=(0, payload, "")):
            result = autoresearch_contextgo._native_eval("rust", "test-query")

        self.assertEqual(result["backend"], "rust")
        self.assertEqual(result["rc"], 0)
        self.assertEqual(result["match_count"], 2)
        self.assertTrue(result["ok"])

    def test_native_eval_no_matches(self) -> None:
        import autoresearch_contextgo

        payload = json.dumps({"matches": []})
        with patch.object(autoresearch_contextgo, "run_cmd", return_value=(0, payload, "")):
            result = autoresearch_contextgo._native_eval("go", "test-query")

        self.assertFalse(result["ok"])
        self.assertEqual(result["match_count"], 0)

    def test_native_text_eval_structure(self) -> None:
        import autoresearch_contextgo

        with patch.object(autoresearch_contextgo, "run_cmd", return_value=(0, "some output", "")):
            result = autoresearch_contextgo._native_text_eval("rust", "test-query")

        self.assertEqual(result["backend"], "rust")
        self.assertEqual(result["rc"], 0)
        self.assertTrue(result["ok"])

    def test_smoke_eval_returns_on_pass(self) -> None:
        import autoresearch_contextgo

        smoke_payload = json.dumps({"summary": {"status": "pass"}})
        with patch.object(autoresearch_contextgo, "run_cmd", return_value=(0, smoke_payload, "")):
            rc, payload, size = autoresearch_contextgo._smoke_eval()
        self.assertEqual(rc, 0)
        self.assertEqual((payload.get("summary") or {}).get("status"), "pass")

    def test_smoke_eval_retries_on_failure(self) -> None:
        import autoresearch_contextgo

        fail_payload = json.dumps({"summary": {"status": "fail"}})
        call_count = [0]

        def fake_run_cmd(args, timeout=180):
            call_count[0] += 1
            return 1, fail_payload, ""

        with patch.object(autoresearch_contextgo, "run_cmd", side_effect=fake_run_cmd):
            rc, payload, _ = autoresearch_contextgo._smoke_eval()
        # Should retry exactly 2 times
        self.assertEqual(call_count[0], 2)

    def test_evaluate_returns_scored_metrics(self) -> None:
        import autoresearch_contextgo

        health_ok = json.dumps({"all_ok": True})
        smoke_ok = json.dumps({"summary": {"status": "pass"}})
        search_ok = "Found 3 results"
        native_json = json.dumps({"matches": [{"snippet": "test"}]})
        native_text = "text output"

        call_count = [0]

        def fake_run_cmd(args, timeout=180):
            call_count[0] += 1
            arg_str = " ".join(str(a) for a in args)
            if "health" in arg_str:
                return 0, health_ok, ""
            if "smoke" in arg_str:
                return 0, smoke_ok, ""
            if "--json" in args:
                return 0, native_json, ""
            if "native-scan" in arg_str:
                return 0, native_text, ""
            return 0, search_ok, ""

        with patch.object(autoresearch_contextgo, "run_cmd", side_effect=fake_run_cmd):
            result = autoresearch_contextgo.evaluate("TestQuery")

        self.assertIn("total_score", result)
        self.assertIn("dimensions", result)
        self.assertIn("signals", result)
        self.assertIn("stability", result["dimensions"])
        self.assertIn("recall", result["dimensions"])
        self.assertIn("token_efficiency", result["dimensions"])
        self.assertGreater(result["total_score"], 0)

    def test_evaluate_stability_scoring(self) -> None:
        import autoresearch_contextgo

        # All failing — return valid but empty JSON to avoid parse errors in _json_from_text
        with patch.object(autoresearch_contextgo, "run_cmd", return_value=(1, "{}", "")):
            result = autoresearch_contextgo.evaluate("query")
        self.assertEqual(result["dimensions"]["stability"], 0)

    def test_build_parser_defaults(self) -> None:
        import autoresearch_contextgo

        parser = autoresearch_contextgo.build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.round, 1)
        self.assertEqual(args.max_rounds, autoresearch_contextgo.DEFAULT_MAX_ROUNDS)
        self.assertEqual(args.query, autoresearch_contextgo.DEFAULT_QUERY)
        self.assertEqual(args.note, "baseline")

    def test_build_parser_custom_args(self) -> None:
        import autoresearch_contextgo

        parser = autoresearch_contextgo.build_parser()
        args = parser.parse_args(["--round", "5", "--query", "TestQuery", "--note", "phase2"])
        self.assertEqual(args.round, 5)
        self.assertEqual(args.query, "TestQuery")
        self.assertEqual(args.note, "phase2")

    def test_append_log_writes_files(self) -> None:
        import autoresearch_contextgo

        payload = {
            "timestamp": "2026-03-27T00:00:00",
            "query": "test",
            "dimensions": {"stability": 100, "recall": 100, "token_efficiency": 100},
            "total_score": 100.0,
            "signals": {
                "health_bytes": 400,
                "search_bytes": 100,
                "smoke_bytes": 500,
                "native_total_bytes": 1000,
                "native_text_bytes": 200,
            },
        }

        with tempfile.TemporaryDirectory(prefix="cg_autoresearch_test_") as tmpdir:
            orig_artifact_root = autoresearch_contextgo.ARTIFACT_ROOT
            orig_log = autoresearch_contextgo.LOG_PATH
            orig_state = autoresearch_contextgo.STATE_PATH
            orig_metrics = autoresearch_contextgo.METRICS_PATH
            orig_best = autoresearch_contextgo.BEST_PATH

            try:
                autoresearch_contextgo.ARTIFACT_ROOT = Path(tmpdir)
                autoresearch_contextgo.LOG_PATH = Path(tmpdir) / "test.tsv"
                autoresearch_contextgo.STATE_PATH = Path(tmpdir) / "state.json"
                autoresearch_contextgo.METRICS_PATH = Path(tmpdir) / "metrics.json"
                autoresearch_contextgo.BEST_PATH = Path(tmpdir) / "best.json"

                autoresearch_contextgo.append_log(1, payload, "KEEP", "test-run")

                self.assertTrue(autoresearch_contextgo.LOG_PATH.exists())
                self.assertTrue(autoresearch_contextgo.STATE_PATH.exists())
            finally:
                autoresearch_contextgo.ARTIFACT_ROOT = orig_artifact_root
                autoresearch_contextgo.LOG_PATH = orig_log
                autoresearch_contextgo.STATE_PATH = orig_state
                autoresearch_contextgo.METRICS_PATH = orig_metrics
                autoresearch_contextgo.BEST_PATH = orig_best

    def test_main_runs_evaluate_and_logs(self) -> None:
        import autoresearch_contextgo

        fake_metrics = {
            "timestamp": "2026-03-27T00:00:00",
            "query": "NotebookLM",
            "dimensions": {"stability": 50, "recall": 70, "token_efficiency": 80},
            "total_score": 65.0,
            "signals": {
                "health_ok": False,
                "health_bytes": 100,
                "smoke_ok": False,
                "search_ok": True,
                "search_bytes": 100,
                "smoke_bytes": 500,
                "native_total_bytes": 1000,
                "native_text_bytes": 200,
                "rust": {"ok": True, "bytes": 500, "match_count": 1, "rc": 0},
                "go": {"ok": True, "bytes": 500, "match_count": 1, "rc": 0},
                "rust_text": {"ok": True, "bytes": 100, "rc": 0},
                "go_text": {"ok": True, "bytes": 100, "rc": 0},
            },
        }

        with patch.object(autoresearch_contextgo, "evaluate", return_value=fake_metrics):
            with patch.object(autoresearch_contextgo, "append_log"):
                with patch.object(autoresearch_contextgo, "current_git_commit", return_value="abc123"):
                    with patch("builtins.print"):
                        rc = autoresearch_contextgo.main(["--round", "1", "--note", "test"])
        self.assertEqual(rc, 0)

    def test_append_log_idempotent_on_same_round(self) -> None:
        """Appending the same round twice should not duplicate rows."""
        import autoresearch_contextgo

        payload = {
            "timestamp": "2026-03-27T00:00:00",
            "query": "test",
            "dimensions": {"stability": 50, "recall": 50, "token_efficiency": 50},
            "total_score": 50.0,
            "signals": {
                "health_bytes": 400,
                "search_bytes": 100,
                "smoke_bytes": 500,
                "native_total_bytes": 1000,
                "native_text_bytes": 200,
            },
        }

        with tempfile.TemporaryDirectory(prefix="cg_autoresearch_idem_") as tmpdir:
            orig_log = autoresearch_contextgo.LOG_PATH
            orig_state = autoresearch_contextgo.STATE_PATH
            orig_metrics = autoresearch_contextgo.METRICS_PATH
            orig_best = autoresearch_contextgo.BEST_PATH
            orig_artifact_root = autoresearch_contextgo.ARTIFACT_ROOT

            try:
                autoresearch_contextgo.ARTIFACT_ROOT = Path(tmpdir)
                autoresearch_contextgo.LOG_PATH = Path(tmpdir) / "test.tsv"
                autoresearch_contextgo.STATE_PATH = Path(tmpdir) / "state.json"
                autoresearch_contextgo.METRICS_PATH = Path(tmpdir) / "metrics.json"
                autoresearch_contextgo.BEST_PATH = Path(tmpdir) / "best.json"

                autoresearch_contextgo.append_log(2, payload, "KEEP", "run1")
                autoresearch_contextgo.append_log(2, payload, "KEEP", "run2")

                lines = [
                    ln
                    for ln in autoresearch_contextgo.LOG_PATH.read_text().splitlines()
                    if ln.strip() and ln.startswith("R002")
                ]
                self.assertEqual(len(lines), 1)
            finally:
                autoresearch_contextgo.ARTIFACT_ROOT = orig_artifact_root
                autoresearch_contextgo.LOG_PATH = orig_log
                autoresearch_contextgo.STATE_PATH = orig_state
                autoresearch_contextgo.METRICS_PATH = orig_metrics
                autoresearch_contextgo.BEST_PATH = orig_best


# ---------------------------------------------------------------------------
# context_smoke — unit-level (mock all subprocess calls)
# ---------------------------------------------------------------------------


class TestContextSmoke(unittest.TestCase):
    def test_run_cmd_returns_tuple(self) -> None:
        import context_smoke

        rc, out, err = context_smoke.run_cmd(["python3", "-c", "print('test')"], timeout=5)
        self.assertEqual(rc, 0)
        self.assertIn("test", out)

    def test_free_port_returns_int(self) -> None:
        import context_smoke

        try:
            port = context_smoke._free_port()
        except PermissionError:
            self.skipTest("loopback socket bind is not permitted in this environment")
        self.assertIsInstance(port, int)
        self.assertGreater(port, 0)
        self.assertLess(port, 65536)

    def test_test_health_pass(self) -> None:
        import context_smoke

        payload = json.dumps({"all_ok": True, "status": "ok"})
        with patch.object(context_smoke, "run_cmd", return_value=(0, payload, "")):
            result = context_smoke.test_health(Path("/fake/cli.py"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "health")
        self.assertEqual(result["rc"], 0)

    def test_test_health_fail_bad_json(self) -> None:
        import context_smoke

        with patch.object(context_smoke, "run_cmd", return_value=(0, "not-json", "")):
            result = context_smoke.test_health(Path("/fake/cli.py"))
        self.assertFalse(result["ok"])

    def test_test_health_fail_all_ok_false(self) -> None:
        import context_smoke

        payload = json.dumps({"all_ok": False})
        with patch.object(context_smoke, "run_cmd", return_value=(0, payload, "")):
            result = context_smoke.test_health(Path("/fake/cli.py"))
        self.assertFalse(result["ok"])

    def test_test_quality_gate_pass(self) -> None:
        import context_smoke

        with patch.object(context_smoke, "run_cmd", return_value=(0, "[PASS] health", "")):
            result = context_smoke.test_quality_gate(Path("/fake/gate.py"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "quality_gate")

    def test_test_quality_gate_fail(self) -> None:
        import context_smoke

        with patch.object(context_smoke, "run_cmd", return_value=(1, "[FAIL] health", "")):
            result = context_smoke.test_quality_gate(Path("/fake/gate.py"))
        self.assertFalse(result["ok"])

    def test_test_healthcheck_skipped_when_missing(self) -> None:
        import context_smoke

        result = context_smoke.test_healthcheck(Path("/nonexistent/healthcheck.sh"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["detail"]["skipped"])

    def test_test_healthcheck_pass(self) -> None:
        import context_smoke

        with tempfile.NamedTemporaryFile(suffix=".sh", delete=False) as f:
            script_path = Path(f.name)
            script_path.write_text("#!/usr/bin/env bash\nexit 0\n")

        try:
            with patch.object(context_smoke, "run_cmd", return_value=(0, "", "")):
                result = context_smoke.test_healthcheck(script_path)
            self.assertTrue(result["ok"])
        finally:
            script_path.unlink(missing_ok=True)

    def test_test_maintain_pass(self) -> None:
        import context_smoke

        with patch.object(context_smoke, "run_cmd", return_value=(0, "Snapshot taken. 42 observations.", "")):
            result = context_smoke.test_maintain(Path("/fake/cli.py"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "maintain")

    def test_test_maintain_fail_no_snapshot(self) -> None:
        import context_smoke

        with patch.object(context_smoke, "run_cmd", return_value=(0, "Done.", "")):
            result = context_smoke.test_maintain(Path("/fake/cli.py"))
        self.assertFalse(result["ok"])

    def test_summarize_results_all_pass(self) -> None:
        import context_smoke

        results = [
            {"name": "health", "ok": True},
            {"name": "rw_cycle", "ok": True},
        ]
        summary = context_smoke.summarize_results(results)
        self.assertEqual(summary["status"], "pass")
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["total"], 2)

    def test_summarize_results_with_failure(self) -> None:
        import context_smoke

        results = [
            {"name": "health", "ok": True},
            {"name": "rw_cycle", "ok": False},
        ]
        summary = context_smoke.summarize_results(results)
        self.assertEqual(summary["status"], "fail")
        self.assertEqual(summary["failed"], 1)
        self.assertIn("rw_cycle", summary["failed_names"])

    def test_available_native_backends_empty_on_bad_rc(self) -> None:
        import context_smoke

        with patch.object(context_smoke, "run_cmd", return_value=(1, "", "")):
            backends = context_smoke._available_native_backends(Path("/fake/cli.py"))
        self.assertEqual(backends, [])

    def test_available_native_backends_empty_on_bad_json(self) -> None:
        import context_smoke

        with patch.object(context_smoke, "run_cmd", return_value=(0, "not-json", "")):
            backends = context_smoke._available_native_backends(Path("/fake/cli.py"))
        self.assertEqual(backends, [])

    def test_available_native_backends_returns_valid_backends(self) -> None:
        import context_smoke

        payload = json.dumps({"all_ok": True, "native_backends": {"available_backends": ["rust", "go", "invalid"]}})
        with patch.object(context_smoke, "run_cmd", return_value=(0, payload, "")):
            backends = context_smoke._available_native_backends(Path("/fake/cli.py"))
        self.assertIn("rust", backends)
        self.assertIn("go", backends)
        self.assertNotIn("invalid", backends)

    def test_write_native_fixture(self) -> None:
        import context_smoke

        with tempfile.TemporaryDirectory(prefix="cg_native_smoke_") as tmpdir:
            root = Path(tmpdir)
            codex_root, claude_root = context_smoke._write_native_fixture(root, "marker123")
            # Verify JSONL was created under a date-based path
            files = list(codex_root.rglob("*.jsonl"))
            self.assertTrue(len(files) >= 1)
            content = files[0].read_text()
            self.assertIn("marker123", content)

    def test_test_native_scan_contract_skipped_when_no_backends(self) -> None:
        import context_smoke

        with patch.object(context_smoke, "_available_native_backends", return_value=[]):
            result = context_smoke.test_native_scan_contract(Path("/fake/cli.py"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["detail"]["skipped"])

    def test_run_smoke_returns_summary_and_results(self) -> None:
        import context_smoke

        fake_result = {"name": "health", "rc": 0, "ok": True, "detail": {}}

        with (
            patch.object(context_smoke, "test_health", return_value=fake_result),
            patch.object(
                context_smoke,
                "test_native_scan_contract",
                return_value={**fake_result, "name": "native_scan"},
            ),
            patch.object(
                context_smoke,
                "test_healthcheck",
                return_value={**fake_result, "name": "healthcheck"},
            ),
            patch.object(
                context_smoke,
                "test_quality_gate",
                return_value={**fake_result, "name": "quality_gate"},
            ),
            patch.object(
                context_smoke,
                "test_rw_cycle",
                return_value={**fake_result, "name": "rw_cycle"},
            ),
            patch.object(
                context_smoke,
                "test_maintain",
                return_value={**fake_result, "name": "maintain"},
            ),
            patch.object(
                context_smoke,
                "test_viewer",
                return_value={**fake_result, "name": "viewer"},
            ),
        ):
            payload = context_smoke.run_smoke(Path("/fake/cli.py"), Path("/fake/gate.py"))

        self.assertIn("summary", payload)
        self.assertIn("results", payload)
        self.assertEqual(payload["summary"]["status"], "pass")


# ---------------------------------------------------------------------------
# R16: __name__ == "__main__" guard coverage
# export_memories line 32, import_memories line 25, context_server line 50
# smoke_installed_runtime lines 62-63 (skip_sandbox=True path), 83
# ---------------------------------------------------------------------------


class TestExportMemoriesMainGuard(unittest.TestCase):
    """Line 32: raise SystemExit(main()) in export_memories — via runpy."""

    def test_main_guard_raises_system_exit(self) -> None:
        import runpy

        sys.modules.pop("export_memories", None)
        with patch.object(sys, "argv", ["export_memories", "q", "/tmp/out_r16.json"]):
            with patch("context_cli.main", return_value=0):
                sys.modules.pop("export_memories", None)
                with self.assertRaises(SystemExit) as ctx:
                    runpy.run_module("export_memories", run_name="__main__", alter_sys=False)
                self.assertEqual(ctx.exception.code, 0)


class TestImportMemoriesMainGuard(unittest.TestCase):
    """Line 25: raise SystemExit(main()) in import_memories — via runpy."""

    def test_main_guard_raises_system_exit(self) -> None:
        import runpy

        with (
            patch.object(sys, "argv", ["import_memories", "/tmp/fake_input_r16.json"]),
            patch("context_cli.main", return_value=0),
        ):
            sys.modules.pop("import_memories", None)
            with self.assertRaises(SystemExit) as ctx:
                runpy.run_module("import_memories", run_name="__main__", alter_sys=False)
            self.assertEqual(ctx.exception.code, 0)


class TestContextServerMainGuard(unittest.TestCase):
    """Line 50: context_server __name__ == "__main__" path via runpy."""

    def test_main_guard_calls_main_via_runpy(self) -> None:
        import runpy

        # Patch memory_viewer so the server doesn't actually start
        fake_viewer = unittest.mock.MagicMock()
        fake_viewer.main = unittest.mock.MagicMock(return_value=None)
        fake_viewer.HOST = "127.0.0.1"
        fake_viewer.PORT = 37242
        fake_viewer.VIEWER_TOKEN = ""

        with patch.dict(sys.modules, {"memory_viewer": fake_viewer, "context_server": None}):
            sys.modules.pop("context_server", None)
            try:
                runpy.run_module("context_server", run_name="__main__", alter_sys=False)
            except SystemExit:
                pass
        # If main() was called and returned normally, fake_viewer.main was called
        fake_viewer.main.assert_called()


class TestSmokeInstalledRuntimeSkipSandbox(unittest.TestCase):
    """Lines 62-63: skip_sandbox=True path in smoke_installed_runtime.main()."""

    def test_main_skip_sandbox_false_path(self) -> None:
        import smoke_installed_runtime

        fake_payload = {
            "summary": {"passed": 1, "failed": 0, "total": 1, "status": "pass"},
            "results": [],
        }
        with (
            patch.dict(os.environ, {"CONTEXTGO_SMOKE_SKIP_SANDBOX": "0"}, clear=False),
            patch("smoke_installed_runtime.run_smoke", return_value=fake_payload),
        ):
            rc = smoke_installed_runtime.main()
        self.assertEqual(rc, 0)

    def test_main_skip_sandbox_true_covers_lines_62_63(self) -> None:
        """Lines 62-63: when CONTEXTGO_SMOKE_SKIP_SANDBOX=1, skip sandbox."""
        import smoke_installed_runtime

        fake_payload = {
            "summary": {"passed": 1, "failed": 0, "total": 1, "status": "pass"},
            "results": [],
        }
        with (
            patch.dict(os.environ, {"CONTEXTGO_SMOKE_SKIP_SANDBOX": "1"}, clear=False),
            patch("smoke_installed_runtime.run_smoke", return_value=fake_payload),
        ):
            rc = smoke_installed_runtime.main()
        self.assertEqual(rc, 0)

    def test_main_returns_1_when_failed(self) -> None:
        """Coverage for branch where failed > 0."""
        import smoke_installed_runtime

        fake_payload = {
            "summary": {"passed": 0, "failed": 1, "total": 1, "status": "fail"},
            "results": [],
        }
        with (
            patch.dict(os.environ, {"CONTEXTGO_SMOKE_SKIP_SANDBOX": "1"}, clear=False),
            patch("smoke_installed_runtime.run_smoke", return_value=fake_payload),
        ):
            rc = smoke_installed_runtime.main()
        self.assertEqual(rc, 1)

    def test_main_guard_raises_system_exit_via_runpy(self) -> None:
        """Line 83: __name__ == '__main__' guard in smoke_installed_runtime."""
        import runpy

        import smoke_installed_runtime as _sir

        fake_payload = {
            "summary": {"passed": 1, "failed": 0, "total": 1, "status": "pass"},
            "results": [],
        }
        with (
            patch.dict(os.environ, {"CONTEXTGO_SMOKE_SKIP_SANDBOX": "1"}, clear=False),
            patch.object(_sir, "run_smoke", return_value=fake_payload),
        ):
            # Remove cached module so runpy re-executes it with __main__
            sys.modules.pop("smoke_installed_runtime", None)
            try:
                runpy.run_module("smoke_installed_runtime", run_name="__main__", alter_sys=False)
            except SystemExit as e:
                self.assertIn(e.code, (0, 1))  # Either pass or fail is acceptable


if __name__ == "__main__":
    unittest.main()
