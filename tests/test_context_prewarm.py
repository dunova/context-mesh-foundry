"""Tests for context_prewarm — auto context prewarm engine."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contextgo import context_prewarm as pw


class TestExtractKeywords(unittest.TestCase):
    """Keyword extraction from user messages."""

    def test_basic_chinese(self) -> None:
        kws = pw.extract_keywords("帮我优化网页项目的性能和加载速度")
        # Should filter stop words (帮我, 的, 和) and keep meaningful tokens.
        self.assertTrue(len(kws) > 0)
        self.assertNotIn("帮我", kws)
        self.assertNotIn("的", kws)

    def test_basic_english(self) -> None:
        kws = pw.extract_keywords("how did we fix the auth bug in production?")
        self.assertIn("production", kws)
        self.assertIn("auth", kws)
        self.assertIn("bug", kws)
        self.assertIn("fix", kws)
        # Stop words removed.
        self.assertNotIn("the", kws)
        self.assertNotIn("how", kws)
        self.assertNotIn("did", kws)

    def test_mixed_language(self) -> None:
        kws = pw.extract_keywords("帮我检查 polymarket 跟单系统")
        self.assertIn("polymarket", kws)

    def test_empty_message(self) -> None:
        self.assertEqual(pw.extract_keywords(""), [])

    def test_only_stop_words(self) -> None:
        self.assertEqual(pw.extract_keywords("the a an is are"), [])

    def test_max_keywords(self) -> None:
        kws = pw.extract_keywords(
            "alpha beta gamma delta epsilon zeta eta theta iota kappa",
            max_keywords=3,
        )
        self.assertEqual(len(kws), 3)

    def test_dedup(self) -> None:
        kws = pw.extract_keywords("deploy deploy deploy pipeline pipeline")
        self.assertEqual(kws.count("deploy"), 1)
        self.assertEqual(kws.count("pipeline"), 1)

    def test_short_tokens_filtered(self) -> None:
        kws = pw.extract_keywords("a b c dd ee longer_token")
        self.assertIn("longer_token", kws)
        self.assertNotIn("a", kws)

    def test_sorted_by_length(self) -> None:
        kws = pw.extract_keywords("abc defgh ij klmnop")
        # Longest first.
        self.assertEqual(kws[0], "klmnop")

    def test_cjk_bigrams(self) -> None:
        """CJK character-level bigram extraction."""
        kws = pw.extract_keywords("数据库")
        # Should produce bigrams: 数据, 据库, and full run: 数据库
        found = set(kws)
        self.assertTrue({"数据库", "数据", "据库"} & found, f"Expected CJK bigrams, got {kws}")

    def test_cjk_two_chars(self) -> None:
        """Two CJK chars should produce the full run."""
        kws = pw.extract_keywords("优化")
        self.assertIn("优化", kws)

    def test_cjk_single_char_filtered(self) -> None:
        """Single CJK char should be filtered by min length."""
        kws = pw.extract_keywords("车")
        # Single char → len 1 < _MIN_KW_LEN=2
        self.assertEqual(kws, [])


class TestExtractMessageFromHook(unittest.TestCase):
    """Parsing Claude Code hook payloads."""

    def test_claude_code_format(self) -> None:
        payload = json.dumps({"prompt": {"content": "hello world"}})
        self.assertEqual(pw._extract_message_from_hook(payload), "hello world")

    def test_prompt_as_string(self) -> None:
        payload = json.dumps({"prompt": "direct string"})
        self.assertEqual(pw._extract_message_from_hook(payload), "direct string")

    def test_fallback_content(self) -> None:
        payload = json.dumps({"content": "fallback"})
        self.assertEqual(pw._extract_message_from_hook(payload), "fallback")

    def test_fallback_message(self) -> None:
        payload = json.dumps({"message": "msg fallback"})
        self.assertEqual(pw._extract_message_from_hook(payload), "msg fallback")

    def test_invalid_json(self) -> None:
        # Invalid JSON should return empty string (not echo raw input).
        self.assertEqual(pw._extract_message_from_hook("not json"), "")

    def test_empty(self) -> None:
        self.assertEqual(pw._extract_message_from_hook(""), "")

    def test_non_dict(self) -> None:
        self.assertEqual(pw._extract_message_from_hook("[1,2,3]"), "")


class TestFormatPrewarmOutput(unittest.TestCase):
    """Branded output formatting."""

    def test_memory_results(self) -> None:
        results = [
            {"title": "Auth fix", "date": "2025-01-01", "tags": "auth,jwt", "snippet": "Fixed JWT expiry"},
        ]
        out = pw._format_prewarm_output(results, "", 0.3, ["auth"])
        self.assertIn("[ContextGO]", out)
        self.assertIn("0.3s", out)
        self.assertIn("Auth fix", out)
        self.assertIn("auth,jwt", out)

    def test_session_fallback(self) -> None:
        session = "Found 2 sessions (local index):\n[1] 2025-01-01 | abc | claude\n[2] 2025-01-02 | def | codex"
        out = pw._format_prewarm_output([], session, 0.5, ["test"])
        self.assertIn("[ContextGO]", out)
        self.assertIn("2 条历史会话记录", out)

    def test_empty_results(self) -> None:
        out = pw._format_prewarm_output([], "", 0.1, ["test"])
        self.assertEqual(out, "")

    def test_no_matches_session(self) -> None:
        out = pw._format_prewarm_output([], "No matches found for: test", 0.1, ["test"])
        self.assertEqual(out, "")


class TestPrewarm(unittest.TestCase):
    """Core prewarm function."""

    def test_empty_message(self) -> None:
        self.assertEqual(pw.prewarm(""), "")

    def test_stop_words_only(self) -> None:
        # All stop words → no keywords → empty result.
        self.assertEqual(pw.prewarm("the is a"), "")

    @patch("contextgo.context_prewarm._logger")
    def test_no_crash_on_missing_modules(self, _mock_logger: object) -> None:
        """Prewarm should not crash even if search modules are unavailable."""
        result = pw.prewarm("some obscure query that matches nothing xyz123")
        # May return empty or results — just ensure no exception.
        self.assertIsInstance(result, str)


class TestPrewarmFromStdin(unittest.TestCase):
    """Hook entry point."""

    @patch("sys.stdin")
    def test_empty_stdin(self, mock_stdin: object) -> None:
        mock_stdin.read.return_value = ""  # type: ignore[union-attr]
        self.assertEqual(pw.prewarm_from_stdin(), 0)

    @patch("sys.stdin")
    def test_short_message(self, mock_stdin: object) -> None:
        mock_stdin.read.return_value = json.dumps({"prompt": {"content": "ok"}})  # type: ignore[union-attr]
        self.assertEqual(pw.prewarm_from_stdin(), 0)

    @patch("sys.stdin")
    @patch("contextgo.context_prewarm.prewarm", return_value="")
    def test_valid_message(self, mock_prewarm: object, mock_stdin: object) -> None:
        mock_stdin.read.return_value = json.dumps({"prompt": {"content": "deploy pipeline fix"}})  # type: ignore[union-attr]
        self.assertEqual(pw.prewarm_from_stdin(), 0)

    @patch("sys.stdin")
    def test_stdin_read_size_limit(self, mock_stdin: object) -> None:
        """Verify stdin.read is called with size limit."""
        mock_stdin.read.return_value = ""  # type: ignore[union-attr]
        pw.prewarm_from_stdin()
        mock_stdin.read.assert_called_with(pw._MAX_STDIN_BYTES)  # type: ignore[union-attr]


class TestSetupClaudeCode(unittest.TestCase):
    """Hook installation into settings.json."""

    def test_creates_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_claude = Path(tmp) / ".claude"
            fake_claude.mkdir()
            with patch.object(Path, "home", return_value=Path(tmp)):
                result = pw.setup_claude_code()
            self.assertTrue(result)
            settings = json.loads((fake_claude / "settings.json").read_text())
            hooks = settings["hooks"]["UserPromptSubmit"]
            self.assertTrue(any("contextgo prewarm" in h.get("command", "") for h in hooks))

    def test_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_claude = Path(tmp) / ".claude"
            fake_claude.mkdir()
            with patch.object(Path, "home", return_value=Path(tmp)):
                pw.setup_claude_code()
                pw.setup_claude_code()
            settings = json.loads((fake_claude / "settings.json").read_text())
            hooks = settings["hooks"]["UserPromptSubmit"]
            prewarm_hooks = [h for h in hooks if "contextgo prewarm" in h.get("command", "")]
            self.assertEqual(len(prewarm_hooks), 1)

    def test_preserves_existing_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_claude = Path(tmp) / ".claude"
            fake_claude.mkdir()
            (fake_claude / "settings.json").write_text(json.dumps({"env": {"FOO": "bar"}, "model": "test"}))
            with patch.object(Path, "home", return_value=Path(tmp)):
                pw.setup_claude_code()
            settings = json.loads((fake_claude / "settings.json").read_text())
            self.assertEqual(settings["env"]["FOO"], "bar")
            self.assertEqual(settings["model"], "test")


class TestTeardownClaudeCode(unittest.TestCase):
    """Hook removal from settings.json."""

    def test_removes_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_claude = Path(tmp) / ".claude"
            fake_claude.mkdir()
            with patch.object(Path, "home", return_value=Path(tmp)):
                pw.setup_claude_code()
                result = pw.teardown_claude_code()
            self.assertTrue(result)
            settings = json.loads((fake_claude / "settings.json").read_text())
            hooks = settings["hooks"]["UserPromptSubmit"]
            self.assertFalse(any("contextgo prewarm" in h.get("command", "") for h in hooks))

    def test_teardown_preserves_other_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_claude = Path(tmp) / ".claude"
            fake_claude.mkdir()
            settings = {
                "hooks": {
                    "UserPromptSubmit": [
                        {"matcher": "", "command": "contextgo prewarm"},
                        {"matcher": "", "command": "other-tool run"},
                    ]
                },
                "env": {"KEEP": "me"},
            }
            (fake_claude / "settings.json").write_text(json.dumps(settings))
            with patch.object(Path, "home", return_value=Path(tmp)):
                pw.teardown_claude_code()
            result = json.loads((fake_claude / "settings.json").read_text())
            self.assertEqual(len(result["hooks"]["UserPromptSubmit"]), 1)
            self.assertEqual(result["hooks"]["UserPromptSubmit"][0]["command"], "other-tool run")
            self.assertEqual(result["env"]["KEEP"], "me")

    def test_teardown_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "home", return_value=Path(tmp)):
                result = pw.teardown_claude_code()
            self.assertTrue(result)

    def test_teardown_already_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_claude = Path(tmp) / ".claude"
            fake_claude.mkdir()
            (fake_claude / "settings.json").write_text(json.dumps({"hooks": {"UserPromptSubmit": []}}))
            with patch.object(Path, "home", return_value=Path(tmp)):
                result = pw.teardown_claude_code()
            self.assertTrue(result)


class TestInjectScfPolicy(unittest.TestCase):
    """SCF policy injection into Markdown files."""

    def test_inject_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "AGENTS.md"
            target.write_text("# Existing content\n")
            result = pw._inject_scf_policy(target)
            self.assertTrue(result)
            content = target.read_text()
            self.assertIn("SCF:CONTEXT-FIRST:START", content)

    def test_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "AGENTS.md"
            target.write_text("# Existing\n")
            pw._inject_scf_policy(target)
            pw._inject_scf_policy(target)
            content = target.read_text()
            self.assertEqual(content.count("SCF:CONTEXT-FIRST:START"), 1)

    def test_missing_parent(self) -> None:
        result = pw._inject_scf_policy(Path("/nonexistent/dir/AGENTS.md"))
        self.assertFalse(result)


class TestRemoveScfPolicy(unittest.TestCase):
    """SCF policy removal from Markdown files."""

    def test_remove_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "AGENTS.md"
            target.write_text("# Header\n")
            pw._inject_scf_policy(target)
            self.assertIn("SCF:CONTEXT-FIRST:START", target.read_text())
            result = pw._remove_scf_policy(target)
            self.assertTrue(result)
            self.assertNotIn("SCF:CONTEXT-FIRST:START", target.read_text())

    def test_remove_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "AGENTS.md"
            target.write_text("# Header\n")
            result = pw._remove_scf_policy(target)
            self.assertTrue(result)

    def test_remove_no_file(self) -> None:
        result = pw._remove_scf_policy(Path("/nonexistent/file.md"))
        self.assertTrue(result)


class TestSetupAll(unittest.TestCase):
    """Full setup across all platforms."""

    def test_returns_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "home", return_value=Path(tmp)):
                (Path(tmp) / ".claude").mkdir()
                results = pw.setup_all()
            self.assertIsInstance(results, dict)
            self.assertIn("Claude Code (hook)", results)

    def test_all_tools_attempted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "home", return_value=Path(tmp)):
                (Path(tmp) / ".claude").mkdir()
                (Path(tmp) / ".codex").mkdir()
                results = pw.setup_all()
            self.assertTrue(results["Claude Code (hook)"])
            self.assertTrue(results["Codex CLI"])


class TestTeardownAll(unittest.TestCase):
    """Full teardown across all platforms."""

    def test_setup_then_teardown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "home", return_value=Path(tmp)):
                (Path(tmp) / ".claude").mkdir()
                (Path(tmp) / ".codex").mkdir()
                (Path(tmp) / ".codex" / "AGENTS.md").write_text("# Codex\n")
                pw.setup_all()
                results = pw.teardown_all()
            self.assertTrue(all(results.values()))

    def test_teardown_returns_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "home", return_value=Path(tmp)):
                results = pw.teardown_all()
            self.assertIsInstance(results, dict)
            self.assertIn("Claude Code (hook)", results)


class TestSetupCodex(unittest.TestCase):
    """Codex AGENTS.md policy injection."""

    def test_creates_agents_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = Path(tmp) / ".codex"
            codex.mkdir()
            (codex / "AGENTS.md").write_text("# Agents\n")
            with patch.object(Path, "home", return_value=Path(tmp)):
                result = pw.setup_codex()
            self.assertTrue(result)
            content = (codex / "AGENTS.md").read_text()
            self.assertIn("SCF:CONTEXT-FIRST:START", content)
            self.assertIn("contextgo semantic", content)

    def test_missing_codex_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "home", return_value=Path(tmp)):
                result = pw.setup_codex()
            self.assertFalse(result)


class TestPrewarmWithOutput(unittest.TestCase):
    """Test prewarm with mocked search results."""

    @patch("sys.stdin")
    @patch("contextgo.context_prewarm.prewarm", return_value="[ContextGO] test output")
    def test_prewarm_prints_output(self, mock_prewarm: object, mock_stdin: object) -> None:
        mock_stdin.read.return_value = json.dumps({"prompt": {"content": "test deploy pipeline"}})  # type: ignore[union-attr]
        self.assertEqual(pw.prewarm_from_stdin(), 0)

    @patch("sys.stdin")
    def test_stdin_exception(self, mock_stdin: object) -> None:
        mock_stdin.read.side_effect = OSError("stdin broken")  # type: ignore[union-attr]
        self.assertEqual(pw.prewarm_from_stdin(), 0)

    def test_format_memory_no_tags(self) -> None:
        results = [{"title": "Test", "date": "2025-01-01", "snippet": "hello"}]
        out = pw._format_prewarm_output(results, "", 0.1, ["test"])
        self.assertIn("Test", out)
        self.assertNotIn("tags:", out)

    def test_format_memory_with_content_fallback(self) -> None:
        results = [{"title": "Test", "date": "2025-01-01", "content": "body text here"}]
        out = pw._format_prewarm_output(results, "", 0.1, ["test"])
        self.assertIn("body text here", out)

    def test_format_session_single_result(self) -> None:
        session = "Found 1 sessions (local index):\nsome data here"
        out = pw._format_prewarm_output([], session, 0.2, ["deploy"])
        self.assertIn("1 条历史会话记录", out)


class TestSetupClaudeMd(unittest.TestCase):
    """CLAUDE.md policy injection."""

    def test_inject_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claude = Path(tmp) / ".claude"
            claude.mkdir()
            (claude / "CLAUDE.md").write_text("# Claude\n")
            with patch.object(Path, "home", return_value=Path(tmp)):
                result = pw.setup_claude_md()
            self.assertTrue(result)
            self.assertIn("SCF:CONTEXT-FIRST", (claude / "CLAUDE.md").read_text())


class TestSetupClaudeCodeCorruptJson(unittest.TestCase):
    """Handle corrupt settings.json gracefully."""

    def test_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claude = Path(tmp) / ".claude"
            claude.mkdir()
            (claude / "settings.json").write_text("{corrupt json!!!")
            with patch.object(Path, "home", return_value=Path(tmp)):
                result = pw.setup_claude_code()
            self.assertTrue(result)
            settings = json.loads((claude / "settings.json").read_text())
            self.assertIn("UserPromptSubmit", settings["hooks"])


class TestAtomicWrite(unittest.TestCase):
    """Atomic write helper."""

    def test_basic_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "test.json"
            pw._atomic_write(target, '{"key": "value"}\n')
            self.assertEqual(target.read_text(), '{"key": "value"}\n')

    def test_overwrites_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "test.json"
            target.write_text("old content")
            pw._atomic_write(target, "new content")
            self.assertEqual(target.read_text(), "new content")

    def test_resolves_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real_file = Path(tmp) / "real.json"
            real_file.write_text("original")
            link = Path(tmp) / "link.json"
            link.symlink_to(real_file)
            pw._atomic_write(link, "updated via symlink")
            self.assertEqual(real_file.read_text(), "updated via symlink")

    def test_no_temp_leftover_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "test.json"
            pw._atomic_write(target, "content")
            files = list(Path(tmp).iterdir())
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].name, "test.json")


class TestCLIIntegration(unittest.TestCase):
    """CLI dispatch and main() integration."""

    def test_commands_dict_contains_prewarm_setup_unsetup(self) -> None:
        from contextgo import context_cli

        self.assertIn("prewarm", context_cli.COMMANDS)
        self.assertIn("setup", context_cli.COMMANDS)
        self.assertIn("unsetup", context_cli.COMMANDS)

    def test_commands_point_to_correct_functions(self) -> None:
        from contextgo import context_cli

        self.assertIs(context_cli.COMMANDS["prewarm"], context_cli.cmd_prewarm)
        self.assertIs(context_cli.COMMANDS["setup"], context_cli.cmd_setup)
        self.assertIs(context_cli.COMMANDS["unsetup"], context_cli.cmd_unsetup)

    def test_build_parser_has_prewarm_setup_unsetup(self) -> None:
        from contextgo import context_cli

        parser = context_cli.build_parser()
        subs_actions = [a for a in parser._actions if hasattr(a, "_name_parser_map")]
        self.assertTrue(len(subs_actions) > 0)
        names = subs_actions[0]._name_parser_map
        self.assertIn("prewarm", names)
        self.assertIn("setup", names)
        self.assertIn("unsetup", names)

    @patch("contextgo.context_prewarm.prewarm_from_stdin", return_value=0)
    def test_cmd_prewarm_delegates(self, mock_pfstdin: object) -> None:
        import argparse

        from contextgo import context_cli

        args = argparse.Namespace(command="prewarm")
        rc = context_cli.cmd_prewarm(args)
        self.assertEqual(rc, 0)

    @patch("contextgo.context_prewarm.setup_all")
    def test_cmd_setup_calls_setup_all(self, mock_sa: object) -> None:
        import argparse

        from contextgo import context_cli

        mock_sa.return_value = {
            "Claude Code (hook)": True,
            "Codex CLI": False,
            "OpenClaw": False,
            "Claude Code (policy)": True,
        }  # type: ignore[union-attr]
        args = argparse.Namespace(command="setup")
        rc = context_cli.cmd_setup(args)
        self.assertEqual(rc, 0)

    @patch("contextgo.context_prewarm.teardown_all")
    def test_cmd_unsetup_calls_teardown_all(self, mock_ta: object) -> None:
        import argparse

        from contextgo import context_cli

        mock_ta.return_value = {
            "Claude Code (hook)": True,
            "Codex CLI": True,
            "OpenClaw": True,
            "Claude Code (policy)": True,
        }  # type: ignore[union-attr]
        args = argparse.Namespace(command="unsetup")
        rc = context_cli.cmd_unsetup(args)
        self.assertEqual(rc, 0)


class TestSetupAllKeys(unittest.TestCase):
    """Verify setup_all returns all expected keys."""

    def test_returns_all_expected_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "home", return_value=Path(tmp)):
                (Path(tmp) / ".claude").mkdir()
                results = pw.setup_all()
        expected_keys = {
            "Claude Code (hook)",
            "Claude Code (policy)",
            "Codex CLI",
            "OpenClaw",
            "Antigravity",
            "Accio",
            "GitHub Copilot",
        }
        self.assertEqual(set(results.keys()), expected_keys)

    def test_teardown_all_returns_all_expected_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "home", return_value=Path(tmp)):
                results = pw.teardown_all()
        expected_keys = {
            "Claude Code (hook)",
            "Claude Code (policy)",
            "Codex CLI",
            "OpenClaw",
            "Antigravity",
            "Accio",
            "GitHub Copilot",
        }
        self.assertEqual(set(results.keys()), expected_keys)


class TestSetupOpenclaw(unittest.TestCase):
    """OpenClaw setup tests."""

    def test_setup_with_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            oc = Path(tmp) / ".openclaw" / "workspace"
            oc.mkdir(parents=True)
            (oc / "AGENTS.md").write_text("# OpenClaw\n")
            with patch.object(Path, "home", return_value=Path(tmp)):
                result = pw.setup_openclaw()
            self.assertTrue(result)
            self.assertIn("SCF:CONTEXT-FIRST:START", (oc / "AGENTS.md").read_text())

    def test_setup_missing_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "home", return_value=Path(tmp)):
                result = pw.setup_openclaw()
            self.assertFalse(result)


class TestPrewarmStdoutCapture(unittest.TestCase):
    """Verify stdout output from prewarm_from_stdin."""

    @patch("sys.stdin")
    @patch("contextgo.context_prewarm.prewarm", return_value="[ContextGO] 上下文预热完成 — 找到 1 条")
    @patch("builtins.print")
    def test_output_printed_to_stdout(self, mock_print: object, mock_prewarm: object, mock_stdin: object) -> None:
        mock_stdin.read.return_value = json.dumps({"prompt": {"content": "deploy pipeline fix auth"}})  # type: ignore[union-attr]
        pw.prewarm_from_stdin()
        mock_print.assert_called_once()  # type: ignore[union-attr]
        printed = mock_print.call_args[0][0]  # type: ignore[union-attr]
        self.assertIn("[ContextGO]", printed)

    @patch("sys.stdin")
    @patch("contextgo.context_prewarm.prewarm", return_value="")
    @patch("builtins.print")
    def test_no_output_when_empty(self, mock_print: object, mock_prewarm: object, mock_stdin: object) -> None:
        mock_stdin.read.return_value = json.dumps({"prompt": {"content": "deploy pipeline fix auth"}})  # type: ignore[union-attr]
        pw.prewarm_from_stdin()
        mock_print.assert_not_called()  # type: ignore[union-attr]

    @patch("sys.stdin")
    @patch("contextgo.context_prewarm.prewarm", return_value="")
    def test_message_boundary_length_4(self, mock_prewarm: object, mock_stdin: object) -> None:
        """Message with exactly 4 chars after strip should be processed."""
        mock_stdin.read.return_value = json.dumps({"prompt": {"content": "abcd"}})  # type: ignore[union-attr]
        pw.prewarm_from_stdin()
        mock_prewarm.assert_called_once_with("abcd")  # type: ignore[union-attr]


class TestPrewarmTimeout(unittest.TestCase):
    """Timeout and concurrent execution paths."""

    def test_prewarm_respects_timeout(self) -> None:
        """Prewarm should not block beyond timeout even with slow modules."""
        import time as _t

        t0 = _t.monotonic()
        # With unavailable modules, should return quickly.
        result = pw.prewarm("some obscure query xyz999", timeout=0.5)
        elapsed = _t.monotonic() - t0
        self.assertLess(elapsed, 3.0)
        self.assertIsInstance(result, str)


if __name__ == "__main__":
    unittest.main()
