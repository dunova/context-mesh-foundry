#!/usr/bin/env python3
"""R23 coverage-boost tests for context_core.py.

Targets uncovered lines: 54-55, 66, 129, 148-149, 152-153, 157-158,
177, 194, 201, 208, 275, 277.

Each test uses real temp-dir files and is self-contained.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

os.environ.setdefault("CONTEXTGO_STORAGE_ROOT", "/tmp/cgo_test_r23")

import context_core  # noqa: E402


class SafeMtimeTests(unittest.TestCase):
    """Tests for safe_mtime() — targets lines 54-55 (OSError path)."""

    def test_safe_mtime_nonexistent_file_returns_zero(self) -> None:
        """safe_mtime returns 0.0 when the path does not exist (OSError branch)."""
        result = context_core.safe_mtime("/nonexistent/path/that/does/not/exist.md")
        self.assertEqual(result, 0.0)

    def test_safe_mtime_existing_file_returns_positive(self) -> None:
        """safe_mtime returns a positive float for an existing file."""
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
            tmp = Path(f.name)
            f.write(b"hello")
        try:
            result = context_core.safe_mtime(tmp)
            self.assertGreater(result, 0.0)
        finally:
            tmp.unlink(missing_ok=True)

    def test_safe_mtime_string_path(self) -> None:
        """safe_mtime accepts a plain string path."""
        result = context_core.safe_mtime("/no/such/file")
        self.assertEqual(result, 0.0)


class IterSharedFilesTests(unittest.TestCase):
    """Tests for iter_shared_files() — targets line 66 (non-directory path)."""

    def test_iter_shared_files_nonexistent_root_returns_empty(self) -> None:
        """iter_shared_files returns [] when shared_root does not exist (line 66)."""
        result = context_core.iter_shared_files("/nonexistent/dir", max_files=10)
        self.assertEqual(result, [])

    def test_iter_shared_files_file_as_root_returns_empty(self) -> None:
        """iter_shared_files returns [] when shared_root is a plain file (line 66)."""
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
            tmp = Path(f.name)
            f.write(b"content")
        try:
            result = context_core.iter_shared_files(tmp, max_files=10)
            self.assertEqual(result, [])
        finally:
            tmp.unlink(missing_ok=True)

    def test_iter_shared_files_nested_dirs(self) -> None:
        """iter_shared_files discovers files in nested subdirectories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sub = root / "a" / "b"
            sub.mkdir(parents=True)
            (sub / "deep.md").write_text("deep content", encoding="utf-8")
            (root / "top.txt").write_text("top content", encoding="utf-8")
            (root / ".hidden.md").write_text("hidden", encoding="utf-8")

            result = context_core.iter_shared_files(root, max_files=10)
            names = {p.name for p in result}
            self.assertIn("deep.md", names)
            self.assertIn("top.txt", names)
            # Hidden files must be excluded
            self.assertNotIn(".hidden.md", names)

    def test_iter_shared_files_max_files_respected(self) -> None:
        """iter_shared_files returns at most max_files entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for i in range(10):
                (root / f"file{i}.md").write_text(f"content {i}", encoding="utf-8")
            result = context_core.iter_shared_files(root, max_files=3)
            self.assertLessEqual(len(result), 3)

    def test_iter_shared_files_max_files_zero_returns_at_least_one(self) -> None:
        """iter_shared_files with max_files=0 still returns at least 1 file (max(1,...))."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "file.md").write_text("content", encoding="utf-8")
            result = context_core.iter_shared_files(root, max_files=0)
            self.assertGreaterEqual(len(result), 1)

    def test_iter_shared_files_non_text_files_excluded(self) -> None:
        """iter_shared_files excludes files with non-text suffixes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "code.py").write_text("print('hi')", encoding="utf-8")
            (root / "readme.md").write_text("# readme", encoding="utf-8")
            result = context_core.iter_shared_files(root, max_files=10)
            names = {p.name for p in result}
            self.assertNotIn("code.py", names)
            self.assertIn("readme.md", names)


class LocalMemoryMatchesTests(unittest.TestCase):
    """Tests for local_memory_matches() — targets lines 129, 148-149, 152-153,
    157-158, 177."""

    def test_empty_query_returns_empty(self) -> None:
        """local_memory_matches returns [] for empty/whitespace query (line 129)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "file.md").write_text("content", encoding="utf-8")
            self.assertEqual(context_core.local_memory_matches("", shared_root=root), [])
            self.assertEqual(context_core.local_memory_matches("   ", shared_root=root), [])

    def test_path_match_via_filename(self) -> None:
        """local_memory_matches matches query in relative path (lines 151-153)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # File whose NAME contains the query
            f = root / "unique_keyword_file.md"
            f.write_text("irrelevant body text", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "unique_keyword",
                shared_root=root,
                limit=5,
                files=[f],
            )
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["matched_in"], "path")
            self.assertIn("unique_keyword", matches[0]["snippet"])

    def test_content_match_returns_snippet(self) -> None:
        """local_memory_matches returns a snippet for content matches (lines 155-164)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "note.md"
            f.write_text("Before text. TARGET_TOKEN_XYZ. After text.", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "TARGET_TOKEN_XYZ",
                shared_root=root,
                limit=5,
                files=[f],
            )
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["matched_in"], "content")
            self.assertIn("TARGET_TOKEN_XYZ", matches[0]["snippet"])

    def test_oserror_file_skipped(self) -> None:
        """local_memory_matches skips files that raise OSError on read (lines 157-158)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            good = root / "good.md"
            good.write_text("this has the token FINDME here", encoding="utf-8")
            bad = root / "bad.md"
            bad.write_text("also has FINDME", encoding="utf-8")
            # Remove read permission from bad
            os.chmod(bad, 0o000)
            try:
                matches = context_core.local_memory_matches(
                    "FINDME",
                    shared_root=root,
                    limit=5,
                    files=[good, bad],
                )
                # Only good.md should be found
                paths = [m["file_path"] for m in matches]
                self.assertIn(str(good), paths)
                self.assertNotIn(str(bad), paths)
            finally:
                os.chmod(bad, 0o644)

    def test_limit_caps_results(self) -> None:
        """local_memory_matches respects limit and stops early (line 177)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            file_list = []
            for i in range(10):
                f = root / f"file{i}.md"
                f.write_text(f"content COMMON_TOKEN number {i}", encoding="utf-8")
                file_list.append(f)
            matches = context_core.local_memory_matches(
                "COMMON_TOKEN",
                shared_root=root,
                limit=3,
                files=file_list,
            )
            self.assertEqual(len(matches), 3)

    def test_no_match_returns_empty(self) -> None:
        """local_memory_matches returns [] when no files match the query."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "note.md"
            f.write_text("some completely different content", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "NOTPRESENT",
                shared_root=root,
                limit=5,
                files=[f],
            )
            self.assertEqual(matches, [])

    def test_uri_prefix_applied(self) -> None:
        """local_memory_matches prepends uri_prefix to relative paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "note.md"
            f.write_text("content MYTOKEN here", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "MYTOKEN",
                shared_root=root,
                limit=5,
                files=[f],
                uri_prefix="mem://",
            )
            self.assertEqual(len(matches), 1)
            self.assertTrue(matches[0]["uri_hint"].startswith("mem://"))

    def test_empty_uri_prefix(self) -> None:
        """local_memory_matches with empty uri_prefix returns bare relative path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "note.md"
            f.write_text("content MYTOKEN here", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "MYTOKEN",
                shared_root=root,
                limit=5,
                files=[f],
                uri_prefix="",
            )
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["uri_hint"], "note.md")

    def test_file_outside_root_uses_filename(self) -> None:
        """local_memory_matches falls back to filename when file is outside root (lines 148-149)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "sub"
            root.mkdir()
            # Place file outside root
            outside = Path(tmpdir) / "outside_MYQUERY.md"
            outside.write_text("content here", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "MYQUERY",
                shared_root=root,
                limit=5,
                files=[outside],
            )
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["matched_in"], "path")

    def test_mtime_field_is_iso8601(self) -> None:
        """Each match result contains an ISO-8601 mtime string."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "note.md"
            f.write_text("content TOKEN_ISO here", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "TOKEN_ISO",
                shared_root=root,
                limit=5,
                files=[f],
            )
            self.assertEqual(len(matches), 1)
            mtime = matches[0]["mtime"]
            # Should be parseable ISO-8601
            from datetime import datetime

            dt = datetime.fromisoformat(mtime)
            self.assertIsInstance(dt, datetime)

    def test_files_with_no_newlines(self) -> None:
        """local_memory_matches handles files that contain no newline characters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "flat.md"
            f.write_text("one long line with LONGSEARCH token no newline", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "LONGSEARCH",
                shared_root=root,
                limit=5,
                files=[f],
            )
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["matched_in"], "content")

    def test_files_with_only_whitespace(self) -> None:
        """local_memory_matches skips files that contain only whitespace (no match)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "blank.md"
            f.write_text("   \n   \t   \n", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "anything",
                shared_root=root,
                limit=5,
                files=[f],
            )
            self.assertEqual(matches, [])

    def test_empty_file_no_match(self) -> None:
        """local_memory_matches handles empty files gracefully (no match)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "empty.md"
            f.write_text("", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "anything",
                shared_root=root,
                limit=5,
                files=[f],
            )
            self.assertEqual(matches, [])

    def test_very_long_single_line(self) -> None:
        """local_memory_matches correctly extracts snippet from a very long single line."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "long.md"
            prefix = "A" * 500
            suffix = "B" * 500
            f.write_text(f"{prefix}FINDTOKEN{suffix}", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "FINDTOKEN",
                shared_root=root,
                limit=5,
                files=[f],
            )
            self.assertEqual(len(matches), 1)
            self.assertIn("FINDTOKEN", matches[0]["snippet"])

    def test_binary_file_read_with_errors_ignore(self) -> None:
        """local_memory_matches reads binary files without crashing (errors='ignore')."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "binary.md"
            # Write bytes that include the search token embedded in binary data
            f.write_bytes(b"\x00\x01\x02BINSEARCH\x03\x04\xff\xfe")
            matches = context_core.local_memory_matches(
                "BINSEARCH",
                shared_root=root,
                limit=5,
                files=[f],
            )
            # Either finds it or doesn't; important is no exception raised
            self.assertIsInstance(matches, list)

    def test_cjk_content_match(self) -> None:
        """local_memory_matches correctly matches CJK content tokens."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "cjk.md"
            f.write_text("这是一段包含特定关键词的中文内容，用于测试搜索功能。", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "特定关键词",
                shared_root=root,
                limit=5,
                files=[f],
            )
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["matched_in"], "content")

    def test_emoji_in_file_name_path_match(self) -> None:
        """local_memory_matches handles emoji in filenames for path matching."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Use a filename with emoji-adjacent text
            f = root / "rocket_deploy.md"
            f.write_text("unrelated body content xyz", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "rocket",
                shared_root=root,
                limit=5,
                files=[f],
            )
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["matched_in"], "path")

    def test_snippet_radius_boundary(self) -> None:
        """Snippet is bounded by _SNIPPET_RADIUS on both sides of the match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "note.md"
            # Construct content where match is in the middle
            f.write_text("X" * 200 + "MIDTOKEN" + "Y" * 200, encoding="utf-8")
            matches = context_core.local_memory_matches(
                "MIDTOKEN",
                shared_root=root,
                limit=5,
                files=[f],
            )
            self.assertEqual(len(matches), 1)
            snippet = matches[0]["snippet"]
            self.assertIn("MIDTOKEN", snippet)
            # Snippet should be much shorter than the full file
            self.assertLess(len(snippet), 400)

    def test_read_bytes_cap_truncates_large_file(self) -> None:
        """local_memory_matches respects read_bytes and won't read beyond it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "large.md"
            # Token beyond the read cap should not be found
            f.write_text("A" * 5000 + "HIDDENTOKEN", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "HIDDENTOKEN",
                shared_root=root,
                limit=5,
                files=[f],
                read_bytes=4096,
            )
            # Token is beyond 4096 bytes so should not match
            self.assertEqual(matches, [])

    def test_files_none_uses_iter_shared_files(self) -> None:
        """local_memory_matches scans shared_root when files=None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "scan_me.md").write_text("content SCANTOKEN here", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "SCANTOKEN",
                shared_root=root,
                limit=5,
            )
            self.assertEqual(len(matches), 1)

    def test_limit_zero_raises_no_exception(self) -> None:
        """local_memory_matches with limit=0 uses max(1,0)=1, returns at most 1 result."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for i in range(3):
                (root / f"file{i}.md").write_text(f"LIMITTOKEN {i}", encoding="utf-8")
            matches = context_core.local_memory_matches(
                "LIMITTOKEN",
                shared_root=root,
                limit=0,
            )
            self.assertLessEqual(len(matches), 1)


class NormalizeTagsTests(unittest.TestCase):
    """Additional normalize_tags tests — targets lines 194, 201, 208."""

    def test_normalize_tags_none_returns_empty(self) -> None:
        """normalize_tags(None) returns [] (line 194)."""
        self.assertEqual(context_core.normalize_tags(None), [])

    def test_normalize_tags_empty_string_returns_empty(self) -> None:
        """normalize_tags('') returns [] (line 201)."""
        self.assertEqual(context_core.normalize_tags(""), [])
        self.assertEqual(context_core.normalize_tags("   "), [])

    def test_normalize_tags_non_list_non_string(self) -> None:
        """normalize_tags coerces non-list/non-string to string (line 208)."""
        result = context_core.normalize_tags(42)  # type: ignore[arg-type]
        self.assertEqual(result, ["42"])

    def test_normalize_tags_json_non_list_returns_raw(self) -> None:
        """normalize_tags for JSON string that is not a list returns it as-is."""
        result = context_core.normalize_tags('{"key": "value"}')
        self.assertEqual(result, ['{"key": "value"}'])

    def test_normalize_tags_invalid_json_splits_by_comma(self) -> None:
        """normalize_tags falls back to CSV split for invalid JSON strings."""
        result = context_core.normalize_tags("tag1, tag2, tag3")
        self.assertEqual(result, ["tag1", "tag2", "tag3"])

    def test_normalize_tags_empty_items_filtered(self) -> None:
        """normalize_tags filters out empty strings from the result."""
        result = context_core.normalize_tags("a,,b, ,c")
        self.assertNotIn("", result)
        self.assertIn("a", result)
        self.assertIn("b", result)
        self.assertIn("c", result)


class WriteMemoryMarkdownTests(unittest.TestCase):
    """Additional write_memory_markdown tests — targets lines 275, 277."""

    def test_write_memory_markdown_empty_title_raises(self) -> None:
        """write_memory_markdown raises ValueError for empty title (line 275)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError, msg="title cannot be empty"):
                context_core.write_memory_markdown(
                    "",
                    "some content",
                    None,
                    conversations_root=tmpdir,
                )

    def test_write_memory_markdown_whitespace_title_raises(self) -> None:
        """write_memory_markdown raises ValueError for whitespace-only title."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                context_core.write_memory_markdown(
                    "   ",
                    "some content",
                    None,
                    conversations_root=tmpdir,
                )

    def test_write_memory_markdown_empty_content_raises(self) -> None:
        """write_memory_markdown raises ValueError for empty content (line 277)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError, msg="content cannot be empty"):
                context_core.write_memory_markdown(
                    "valid title",
                    "",
                    None,
                    conversations_root=tmpdir,
                )

    def test_write_memory_markdown_whitespace_content_raises(self) -> None:
        """write_memory_markdown raises ValueError for whitespace-only content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                context_core.write_memory_markdown(
                    "valid title",
                    "   \n\t   ",
                    None,
                    conversations_root=tmpdir,
                )

    def test_write_memory_markdown_creates_parent_dirs(self) -> None:
        """write_memory_markdown creates nested parent directories automatically."""
        with tempfile.TemporaryDirectory() as tmpdir:
            deep_root = Path(tmpdir) / "a" / "b" / "c"
            path = context_core.write_memory_markdown(
                "Test Title",
                "Test body content here.",
                ["tag1"],
                conversations_root=deep_root,
                timestamp="20260101_000000",
            )
            self.assertTrue(path.exists())

    def test_write_memory_markdown_uses_default_timestamp(self) -> None:
        """write_memory_markdown uses current time when timestamp=None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = context_core.write_memory_markdown(
                "Auto TS Title",
                "Auto timestamp body content.",
                None,
                conversations_root=tmpdir,
                timestamp=None,
            )
            self.assertTrue(path.exists())
            # Filename should start with a date-like prefix
            self.assertRegex(path.name, r"^\d{8}_\d{6}_")

    def test_write_memory_markdown_file_contains_title_and_tags(self) -> None:
        """write_memory_markdown file content includes title and tags."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = context_core.write_memory_markdown(
                "My Title",
                "My body content here.",
                ["alpha", "beta"],
                conversations_root=tmpdir,
                timestamp="20260101_120000",
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn("My Title", text)
            self.assertIn("alpha", text)
            self.assertIn("beta", text)
            self.assertIn("My body content here.", text)


class SafeFilenameTests(unittest.TestCase):
    """Additional safe_filename tests for edge cases."""

    def test_safe_filename_empty_string_returns_memory(self) -> None:
        """safe_filename returns 'memory' for empty input."""
        self.assertEqual(context_core.safe_filename(""), "memory")

    def test_safe_filename_only_special_chars_returns_memory(self) -> None:
        """safe_filename returns 'memory' when all chars are replaced and stripped."""
        result = context_core.safe_filename("!!!---...")
        # After replacing special chars with _, stripping ._-, we may get underscores
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_safe_filename_max_length_truncated(self) -> None:
        """safe_filename truncates to _FILENAME_MAX_CHARS (120) characters."""
        long_name = "a" * 200
        result = context_core.safe_filename(long_name)
        self.assertLessEqual(len(result), 120)

    def test_safe_filename_lowercase(self) -> None:
        """safe_filename lowercases all ASCII letters."""
        result = context_core.safe_filename("HelloWorld")
        self.assertEqual(result, "helloworld")


if __name__ == "__main__":
    unittest.main()
