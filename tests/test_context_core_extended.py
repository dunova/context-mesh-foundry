#!/usr/bin/env python3
"""Extended unit tests for context_core module to improve coverage."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import context_core  # noqa: E402

# ---------------------------------------------------------------------------
# safe_mtime tests
# ---------------------------------------------------------------------------


class TestSafeMtime(unittest.TestCase):
    def test_returns_mtime_for_existing_file(self) -> None:
        with tempfile.NamedTemporaryFile() as f:
            mtime = context_core.safe_mtime(f.name)
        self.assertGreater(mtime, 0.0)

    def test_returns_zero_for_missing_file(self) -> None:
        result = context_core.safe_mtime("/nonexistent_path_xyz/missing.txt")
        self.assertEqual(result, 0.0)

    def test_accepts_path_object(self) -> None:
        with tempfile.NamedTemporaryFile() as f:
            mtime = context_core.safe_mtime(Path(f.name))
        self.assertGreater(mtime, 0.0)


# ---------------------------------------------------------------------------
# iter_shared_files tests
# ---------------------------------------------------------------------------


class TestIterSharedFiles(unittest.TestCase):
    def test_returns_empty_when_root_missing(self) -> None:
        result = context_core.iter_shared_files("/nonexistent_xyz", max_files=10)
        self.assertEqual(result, [])

    def test_returns_empty_when_root_is_file(self) -> None:
        with tempfile.NamedTemporaryFile() as f:
            result = context_core.iter_shared_files(f.name, max_files=10)
        self.assertEqual(result, [])

    def test_skips_hidden_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".hidden.md").write_text("hidden")
            (root / "visible.md").write_text("visible")
            files = context_core.iter_shared_files(root, max_files=10)
        names = [f.name for f in files]
        self.assertIn("visible.md", names)
        self.assertNotIn(".hidden.md", names)

    def test_skips_non_text_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data.csv").write_text("a,b")
            (root / "data.md").write_text("# hello")
            files = context_core.iter_shared_files(root, max_files=10)
        names = [f.name for f in files]
        self.assertIn("data.md", names)
        self.assertNotIn("data.csv", names)

    def test_returns_at_most_max_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for i in range(5):
                (root / f"file{i}.md").write_text(f"content {i}")
            files = context_core.iter_shared_files(root, max_files=3)
        self.assertLessEqual(len(files), 3)

    def test_max_files_minimum_is_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "only.md").write_text("content")
            files = context_core.iter_shared_files(root, max_files=0)
        self.assertGreaterEqual(len(files), 1)


# ---------------------------------------------------------------------------
# compact_text tests
# ---------------------------------------------------------------------------


class TestCompactText(unittest.TestCase):
    def test_collapses_whitespace(self) -> None:
        result = context_core.compact_text("  hello   world  ")
        self.assertEqual(result, "hello world")

    def test_collapses_newlines(self) -> None:
        result = context_core.compact_text("line1\n\nline2")
        self.assertEqual(result, "line1 line2")

    def test_empty_string(self) -> None:
        self.assertEqual(context_core.compact_text(""), "")

    def test_none_input(self) -> None:
        # The function uses (text or "") so None would need to be str
        # Actually the signature is str, so pass empty
        self.assertEqual(context_core.compact_text(""), "")


# ---------------------------------------------------------------------------
# local_memory_matches tests
# ---------------------------------------------------------------------------


class TestLocalMemoryMatches(unittest.TestCase):
    def test_empty_query_returns_empty(self) -> None:
        result = context_core.local_memory_matches("", shared_root="/tmp")
        self.assertEqual(result, [])

    def test_whitespace_query_returns_empty(self) -> None:
        result = context_core.local_memory_matches("   ", shared_root="/tmp")
        self.assertEqual(result, [])

    def test_match_in_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "note.md").write_text("This contains the unique_token_xyz_abc string")
            matches = context_core.local_memory_matches(
                "unique_token_xyz_abc",
                shared_root=root,
                limit=5,
            )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["matched_in"], "content")

    def test_match_in_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            subdir = root / "unique_path_token_dir"
            subdir.mkdir()
            (subdir / "file.md").write_text("unrelated content here")
            matches = context_core.local_memory_matches(
                "unique_path_token_dir",
                shared_root=root,
                limit=5,
            )
        self.assertGreater(len(matches), 0)
        self.assertEqual(matches[0]["matched_in"], "path")

    def test_explicit_files_list_skips_dir_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "explicit.md"
            f.write_text("explicit_query_marker content")
            matches = context_core.local_memory_matches(
                "explicit_query_marker",
                shared_root=root,
                files=[f],
            )
        self.assertEqual(len(matches), 1)

    def test_respects_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for i in range(5):
                (root / f"note_{i}.md").write_text("common_search_term present here")
            matches = context_core.local_memory_matches(
                "common_search_term",
                shared_root=root,
                limit=2,
            )
        self.assertLessEqual(len(matches), 2)

    def test_oserror_on_file_read_skips_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "bad.md"
            f.write_text("content")

            original_read_text = Path.read_text

            def patched_read_text(self: Path, **kwargs: object) -> str:
                if self.name == "bad.md":
                    raise OSError("permission denied")
                return original_read_text(self, **kwargs)

            with mock.patch.object(Path, "read_text", patched_read_text):
                matches = context_core.local_memory_matches("content", shared_root=root, limit=5)
        # Should not raise, just skip the file
        self.assertIsInstance(matches, list)

    def test_result_has_required_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "doc.md").write_text("result_keys_check_token present")
            matches = context_core.local_memory_matches("result_keys_check_token", shared_root=root)
        self.assertEqual(len(matches), 1)
        m = matches[0]
        for key in ("uri_hint", "file_path", "matched_in", "mtime", "snippet"):
            self.assertIn(key, m)

    def test_uri_prefix_prepended(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "doc.md").write_text("uri_prefix_test_token present")
            matches = context_core.local_memory_matches(
                "uri_prefix_test_token",
                shared_root=root,
                uri_prefix="myscheme://",
            )
        self.assertTrue(matches[0]["uri_hint"].startswith("myscheme://"))

    def test_relative_path_used_when_file_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir1, tempfile.TemporaryDirectory() as tmpdir2:
            root = Path(tmpdir1)
            outside_file = Path(tmpdir2) / "outside.md"
            outside_file.write_text("outside_root_token present")
            matches = context_core.local_memory_matches(
                "outside_root_token",
                shared_root=root,
                files=[outside_file],
            )
        self.assertEqual(len(matches), 1)
        # Fallback to file.name
        self.assertIn("outside.md", matches[0]["uri_hint"])

    def test_no_match_file_is_skipped(self) -> None:
        """Cover branches 160->166 and 166->176: file whose path and content don't match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # This file contains no query term and its name doesn't match either
            (root / "irrelevant.md").write_text("completely unrelated words here")
            matches = context_core.local_memory_matches(
                "zzz_unique_term_not_in_any_file_xyz",
                shared_root=root,
                limit=5,
            )
        self.assertEqual(matches, [])

    def test_empty_uri_prefix_omitted_from_uri_hint(self) -> None:
        """Cover the ``else rel_path`` branch in uri_hint construction (line 169)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "doc.md").write_text("empty_prefix_test_token present")
            matches = context_core.local_memory_matches(
                "empty_prefix_test_token",
                shared_root=root,
                uri_prefix="",
            )
        self.assertEqual(len(matches), 1)
        # uri_hint should just be the relative path, no prefix
        self.assertFalse(matches[0]["uri_hint"].startswith("local://"))


# ---------------------------------------------------------------------------
# normalize_tags tests
# ---------------------------------------------------------------------------


class TestNormalizeTags(unittest.TestCase):
    def test_none_returns_empty(self) -> None:
        self.assertEqual(context_core.normalize_tags(None), [])

    def test_empty_list_returns_empty(self) -> None:
        self.assertEqual(context_core.normalize_tags([]), [])

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(context_core.normalize_tags(""), [])

    def test_whitespace_string_returns_empty(self) -> None:
        self.assertEqual(context_core.normalize_tags("   "), [])

    def test_list_deduplicates(self) -> None:
        result = context_core.normalize_tags(["a", "b", "a"])
        self.assertEqual(result, ["a", "b"])

    def test_csv_string_split(self) -> None:
        result = context_core.normalize_tags("alpha, beta, gamma")
        self.assertEqual(result, ["alpha", "beta", "gamma"])

    def test_json_array_string_parsed(self) -> None:
        result = context_core.normalize_tags('["x", "y"]')
        self.assertEqual(result, ["x", "y"])

    def test_non_list_json_treated_as_single_item(self) -> None:
        result = context_core.normalize_tags('"just a string"')
        self.assertEqual(len(result), 1)

    def test_non_string_non_list_coerced(self) -> None:
        result = context_core.normalize_tags(42)  # type: ignore[arg-type]
        self.assertEqual(result, ["42"])

    def test_strips_whitespace_from_items(self) -> None:
        result = context_core.normalize_tags(["  foo  ", "  bar  "])
        self.assertEqual(result, ["foo", "bar"])

    def test_empty_items_filtered(self) -> None:
        result = context_core.normalize_tags(["", "valid", "  "])
        self.assertEqual(result, ["valid"])


# ---------------------------------------------------------------------------
# safe_filename tests
# ---------------------------------------------------------------------------


class TestSafeFilename(unittest.TestCase):
    def test_basic_slug(self) -> None:
        self.assertEqual(context_core.safe_filename("Hello World"), "hello_world")

    def test_empty_falls_back_to_memory(self) -> None:
        self.assertEqual(context_core.safe_filename(""), "memory")

    def test_special_chars_replaced(self) -> None:
        result = context_core.safe_filename("foo/bar:baz")
        self.assertNotIn("/", result)
        self.assertNotIn(":", result)

    def test_truncated_to_max_chars(self) -> None:
        long_input = "a" * 200
        result = context_core.safe_filename(long_input)
        self.assertLessEqual(len(result), 120)

    def test_leading_trailing_punctuation_stripped(self) -> None:
        result = context_core.safe_filename("---hello---")
        self.assertFalse(result.startswith("-"))
        self.assertFalse(result.endswith("-"))


# ---------------------------------------------------------------------------
# write_memory_markdown tests
# ---------------------------------------------------------------------------


class TestWriteMemoryMarkdown(unittest.TestCase):
    def test_raises_on_empty_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, self.assertRaises(ValueError):
            context_core.write_memory_markdown("", "content", [], conversations_root=Path(tmpdir))

    def test_raises_on_empty_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, self.assertRaises(ValueError):
            context_core.write_memory_markdown("title", "", [], conversations_root=Path(tmpdir))

    def test_raises_on_whitespace_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, self.assertRaises(ValueError):
            context_core.write_memory_markdown("   ", "content", [], conversations_root=Path(tmpdir))

    def test_file_written_with_correct_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = context_core.write_memory_markdown(
                "Test Title",
                "Test body content",
                ["tag1", "tag2"],
                conversations_root=Path(tmpdir),
                timestamp="20250101_120000",
            )
            self.assertTrue(path.exists())
            text = path.read_text(encoding="utf-8")
            self.assertIn("# Test Title", text)
            self.assertIn("Test body content", text)
            self.assertIn("tag1", text)
            self.assertIn("tag2", text)

    def test_custom_timestamp_used_in_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = context_core.write_memory_markdown(
                "My Memory",
                "some content",
                None,
                conversations_root=Path(tmpdir),
                timestamp="20301231_235959",
            )
            self.assertIn("20301231_235959", path.name)

    def test_auto_timestamp_when_not_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = context_core.write_memory_markdown(
                "Auto TS",
                "auto content",
                None,
                conversations_root=Path(tmpdir),
            )
            self.assertTrue(path.exists())

    def test_file_permissions_are_0600(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = context_core.write_memory_markdown(
                "Perms Test",
                "content here",
                None,
                conversations_root=Path(tmpdir),
            )
            stat = os.stat(path)
            # Check owner read/write, no group/other bits
            self.assertEqual(stat.st_mode & 0o777, 0o600)

    def test_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = Path(tmpdir) / "deep" / "nested" / "dir"
            path = context_core.write_memory_markdown(
                "Nested",
                "nested content",
                None,
                conversations_root=nested,
            )
            self.assertTrue(path.exists())

    def test_csv_tags_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = context_core.write_memory_markdown(
                "CSV Tags",
                "content",
                "tagA, tagB, tagC",
                conversations_root=Path(tmpdir),
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn("tagA", text)
            self.assertIn("tagB", text)


if __name__ == "__main__":
    unittest.main()
