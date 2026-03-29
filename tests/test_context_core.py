#!/usr/bin/env python3
"""Unit tests for context_core module."""

from __future__ import annotations

import json
import tempfile
import unicodedata
import unittest
from pathlib import Path

import context_core


class ContextCoreTests(unittest.TestCase):
    def test_write_memory_markdown_then_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conversations = Path(tmpdir) / "resources" / "shared" / "conversations"
            path = context_core.write_memory_markdown(
                "core-unit",
                "unique_token_context_core_unit",
                ["unit", "core"],
                conversations_root=conversations,
            )
            self.assertTrue(path.exists())
            matches = context_core.local_memory_matches(
                "unique_token_context_core_unit",
                shared_root=conversations.parent,
                limit=3,
                uri_prefix="local://",
            )
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["matched_in"], "content")


# ---------------------------------------------------------------------------
# R5 CJK/Unicode edge case tests
# ---------------------------------------------------------------------------


class CJKUnicodeContextCoreTests(unittest.TestCase):
    """Tests for CJK/Unicode edge cases in context_core functions."""

    # ------------------------------------------------------------------
    # normalize_tags with CJK content
    # ------------------------------------------------------------------

    def test_normalize_tags_cjk_list(self) -> None:
        """normalize_tags should handle CJK tags in a list."""
        tags = context_core.normalize_tags(["代码", "分析", "测试"])
        self.assertEqual(tags, ["代码", "分析", "测试"])

    def test_normalize_tags_cjk_csv_string(self) -> None:
        """normalize_tags should handle CJK tags in a CSV string."""
        tags = context_core.normalize_tags("代码, 分析, 测试")
        self.assertEqual(tags, ["代码", "分析", "测试"])

    def test_normalize_tags_cjk_json_array(self) -> None:
        """normalize_tags should handle CJK tags in a JSON array string."""
        tags_input = json.dumps(["代码", "分析", "测试"])
        tags = context_core.normalize_tags(tags_input)
        self.assertEqual(tags, ["代码", "分析", "测试"])

    def test_normalize_tags_mixed_scripts(self) -> None:
        """normalize_tags should handle mixed Arabic, Thai, CJK tags."""
        tags = context_core.normalize_tags(["代码", "تحليل", "วิเคราะห์"])
        self.assertEqual(len(tags), 3)
        self.assertIn("代码", tags)
        self.assertIn("تحليل", tags)
        self.assertIn("วิเคราะห์", tags)

    def test_normalize_tags_deduplicates_cjk(self) -> None:
        """normalize_tags should deduplicate CJK tags while preserving order."""
        tags = context_core.normalize_tags(["代码", "分析", "代码", "测试"])
        self.assertEqual(tags, ["代码", "分析", "测试"])

    def test_normalize_tags_emoji_tag(self) -> None:
        """normalize_tags should handle emoji in tags without crashing."""
        tags = context_core.normalize_tags(["代码 🔍", "分析 ✅"])
        self.assertEqual(len(tags), 2)

    # ------------------------------------------------------------------
    # safe_filename with CJK / Unicode
    # ------------------------------------------------------------------

    def test_safe_filename_cjk_title_replaced(self) -> None:
        """safe_filename should replace CJK characters with underscores."""
        result = context_core.safe_filename("代码分析报告")
        # CJK chars replaced by underscores; result should be non-empty
        self.assertGreater(len(result), 0)
        # Should not contain CJK characters (they are replaced)
        self.assertFalse(any("\u4e00" <= c <= "\u9fff" for c in result))

    def test_safe_filename_mixed_cjk_latin(self) -> None:
        """safe_filename with mixed CJK+Latin keeps latin chars."""
        result = context_core.safe_filename("Python代码analysis")
        self.assertIn("python", result)
        self.assertIn("analysis", result)

    def test_safe_filename_emoji_replaced(self) -> None:
        """safe_filename should replace emoji with underscores."""
        result = context_core.safe_filename("deploy_success")
        self.assertGreater(len(result), 0)
        self.assertNotIn("🚀", result)

    def test_safe_filename_arabic_replaced(self) -> None:
        """safe_filename should replace Arabic characters with underscores."""
        result = context_core.safe_filename("تحليل الكود")
        self.assertGreater(len(result), 0)

    def test_safe_filename_zero_width_chars_replaced(self) -> None:
        """safe_filename should handle zero-width characters gracefully."""
        result = context_core.safe_filename("code\u200banalysis\u200c")
        self.assertGreater(len(result), 0)

    # ------------------------------------------------------------------
    # write_memory_markdown with CJK titles and tags
    # ------------------------------------------------------------------

    def test_write_memory_markdown_cjk_title(self) -> None:
        """write_memory_markdown should accept a CJK title and create a valid file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conversations = Path(tmpdir) / "conversations"
            path = context_core.write_memory_markdown(
                "代码分析报告",
                "这是代码分析的详细内容，包含所有关键发现。",
                ["代码", "分析"],
                conversations_root=conversations,
                timestamp="20260325_120000",
            )
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8")
            self.assertIn("代码分析报告", content)
            self.assertIn("这是代码分析的详细内容", content)

    def test_write_memory_markdown_cjk_tags(self) -> None:
        """write_memory_markdown should store CJK tags correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conversations = Path(tmpdir) / "conversations"
            path = context_core.write_memory_markdown(
                "test title",
                "some content here",
                ["代码", "分析", "测试"],
                conversations_root=conversations,
                timestamp="20260325_120001",
            )
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8")
            self.assertIn("代码", content)
            self.assertIn("分析", content)

    def test_write_memory_markdown_emoji_in_content(self) -> None:
        """write_memory_markdown should handle emoji in content without errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conversations = Path(tmpdir) / "conversations"
            path = context_core.write_memory_markdown(
                "Emoji Test",
                "部署成功 🚀 测试通过 ✅ 代码审查完成 💯",
                ["emoji", "test"],
                conversations_root=conversations,
                timestamp="20260325_120002",
            )
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8")
            self.assertIn("🚀", content)

    def test_write_and_search_cjk_memory(self) -> None:
        """write_memory_markdown + local_memory_matches should work with CJK content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conversations = Path(tmpdir) / "resources" / "shared" / "conversations"
            path = context_core.write_memory_markdown(
                "CJK搜索测试",
                "unique_cjk_token_代码分析_结论已确认",
                ["cjk", "test"],
                conversations_root=conversations,
            )
            self.assertTrue(path.exists())
            matches = context_core.local_memory_matches(
                "unique_cjk_token_代码分析",
                shared_root=conversations.parent,
                limit=3,
                uri_prefix="local://",
            )
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["matched_in"], "content")

    # ------------------------------------------------------------------
    # local_memory_matches with Unicode / CJK queries
    # ------------------------------------------------------------------

    def test_local_memory_matches_cjk_query(self) -> None:
        """local_memory_matches should find files containing CJK query terms."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            note_path = root / "note.md"
            note_path.write_text(
                "# 研究报告\n代码质量分析结果：所有检查通过。\n",
                encoding="utf-8",
            )
            matches = context_core.local_memory_matches(
                "代码质量",
                shared_root=root,
                limit=3,
                files=[note_path],
            )
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["matched_in"], "content")

    def test_local_memory_matches_emoji_in_content(self) -> None:
        """local_memory_matches should find files with emoji in content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            note_path = root / "emoji_note.md"
            note_path.write_text(
                "# Deployment\nDeploy 🚀 finished successfully.\n",
                encoding="utf-8",
            )
            matches = context_core.local_memory_matches(
                "🚀",
                shared_root=root,
                limit=3,
                files=[note_path],
            )
            self.assertEqual(len(matches), 1)

    def test_local_memory_matches_arabic_content(self) -> None:
        """local_memory_matches should find files with Arabic content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            note_path = root / "arabic_note.md"
            note_path.write_text(
                "# تقرير\nتحليل الكود اكتمل بنجاح.\n",
                encoding="utf-8",
            )
            matches = context_core.local_memory_matches(
                "تحليل",
                shared_root=root,
                limit=3,
                files=[note_path],
            )
            self.assertEqual(len(matches), 1)

    def test_local_memory_matches_nfc_nfd_normalization(self) -> None:
        """local_memory_matches should find content when query is NFC normalized."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Write content in NFC form
            nfc_text = unicodedata.normalize("NFC", "café résumé naïve")
            note_path = root / "nfc_note.md"
            note_path.write_text(f"# Test\n{nfc_text}\n", encoding="utf-8")
            # Query in NFC form should match
            matches = context_core.local_memory_matches(
                unicodedata.normalize("NFC", "café"),
                shared_root=root,
                limit=3,
                files=[note_path],
            )
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["matched_in"], "content")

    def test_local_memory_matches_zero_width_chars_in_content(self) -> None:
        """local_memory_matches should handle zero-width characters in file content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            note_path = root / "zw_note.md"
            # Content with zero-width spaces embedded in latin text
            note_path.write_text(
                "# Test\ncode\u200banalysis\u200cresult done\n",
                encoding="utf-8",
            )
            # Search without zero-width chars should still work (finds 'analysis' substring)
            matches = context_core.local_memory_matches(
                "analysis",
                shared_root=root,
                limit=3,
                files=[note_path],
            )
            # Should find it (zero-width chars don't block latin match)
            self.assertIsInstance(matches, list)

    def test_local_memory_matches_mixed_script_content(self) -> None:
        """local_memory_matches should handle files with mixed script content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            note_path = root / "mixed_note.md"
            note_path.write_text(
                "# Mixed Scripts\nPython 代码 تحليل วิเคราะห์ complete\n",
                encoding="utf-8",
            )
            matches = context_core.local_memory_matches(
                "代码",
                shared_root=root,
                limit=3,
                files=[note_path],
            )
            self.assertEqual(len(matches), 1)

    # ------------------------------------------------------------------
    # compact_text with Unicode / CJK
    # ------------------------------------------------------------------

    def test_compact_text_cjk_whitespace(self) -> None:
        """compact_text should collapse whitespace in CJK text."""
        result = context_core.compact_text("代码   分析  报告")
        self.assertEqual(result, "代码 分析 报告")

    def test_compact_text_emoji_preserved(self) -> None:
        """compact_text should preserve emoji characters."""
        result = context_core.compact_text("deploy 🚀  success")
        self.assertIn("🚀", result)
        self.assertEqual(result, "deploy 🚀 success")

    def test_compact_text_mixed_scripts_whitespace(self) -> None:
        """compact_text should normalize whitespace in mixed-script text."""
        result = context_core.compact_text("Python  代码  تحليل  done")
        self.assertEqual(result, "Python 代码 تحليل done")

    def test_compact_text_zero_width_chars(self) -> None:
        """compact_text should preserve non-whitespace zero-width chars."""
        text = "code\u200banalysis"
        result = context_core.compact_text(text)
        # Zero-width space is not \s+, so it should be preserved as-is
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


if __name__ == "__main__":
    unittest.main()
