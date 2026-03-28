#!/usr/bin/env python3
"""R33 coverage-boost tests for context_core.py.

Targets UNCOVERED lines:
  78->72  -- _scandir_files OSError on entry.is_dir()/is_file() calls
  84-89   -- _scandir_files OSError on entry.stat() and os.scandir()
  151-152 -- _mmap_contains OSError on path.stat()
  165-170 -- _mmap_contains mmap fallback (OSError on mmap.mmap())
  202-203 -- _mmap_snippet OSError on path.stat()
  206     -- _mmap_snippet empty file (size == 0) returns ""
  214-216 -- _mmap_snippet mmap fallback (OSError on mmap.mmap())
  222-223 -- _mmap_snippet OSError on open()
  229     -- _mmap_snippet query not found returns ""

Each test is self-contained and uses tmp_path or mocking to trigger
the relevant code path.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_SCRIPTS_DIR = str(Path(__file__).parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

os.environ.setdefault("CONTEXTGO_STORAGE_ROOT", "/tmp/cgo_test_r33")

import context_core  # noqa: E402

# ---------------------------------------------------------------------------
# _scandir_files -- OSError branches (lines 78->72, 84-89)
# ---------------------------------------------------------------------------


class ScandirFilesOSErrorTests(unittest.TestCase):
    """Exercise the OSError fallback branches inside _scandir_files."""

    def test_scandir_oserror_on_subdir_is_skipped(self) -> None:
        """_scandir_files continues when os.scandir() raises OSError on a subdir.

        This covers lines 88-89: the outer try/except OSError around os.scandir().
        We achieve this by providing a root that itself cannot be scanned (no
        execute permission), which triggers the OSError at the top-level stack
        entry so that _scandir_files simply returns [].
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            locked = Path(tmpdir) / "locked"
            locked.mkdir()
            (locked / "file.md").write_text("content", encoding="utf-8")
            # Remove execute/read permission so scandir raises OSError
            os.chmod(locked, 0o000)
            try:
                result = context_core._scandir_files(str(locked))
                # Should return [] without crashing
                self.assertEqual(result, [])
            finally:
                os.chmod(locked, 0o755)

    def test_scandir_oserror_on_entry_stat_skips_file(self) -> None:
        """_scandir_files skips a file when entry.stat() raises OSError (lines 84-85).

        We mock os.scandir to produce an entry whose .stat() raises OSError.
        """
        fake_entry = mock.MagicMock()
        fake_entry.name = "test.md"
        fake_entry.path = "/fake/test.md"
        fake_entry.is_dir.return_value = False
        fake_entry.is_file.return_value = True
        fake_entry.stat.side_effect = OSError("stat failed")

        mock_it = mock.MagicMock()
        mock_it.__enter__ = mock.Mock(return_value=iter([fake_entry]))
        mock_it.__exit__ = mock.Mock(return_value=False)

        with mock.patch("os.scandir", return_value=mock_it):
            result = context_core._scandir_files("/fake/root")
        # File with failed stat is silently skipped
        self.assertEqual(result, [])

    def test_scandir_oserror_on_is_file_skips_entry(self) -> None:
        """_scandir_files skips an entry when is_file() raises OSError (lines 86-87).

        We mock an entry where both is_dir() and is_file() raise OSError.
        """
        fake_entry = mock.MagicMock()
        fake_entry.name = "test.md"
        fake_entry.path = "/fake/test.md"
        fake_entry.is_dir.side_effect = OSError("is_dir failed")
        fake_entry.is_file.side_effect = OSError("is_file failed")

        mock_it = mock.MagicMock()
        mock_it.__enter__ = mock.Mock(return_value=iter([fake_entry]))
        mock_it.__exit__ = mock.Mock(return_value=False)

        with mock.patch("os.scandir", return_value=mock_it):
            result = context_core._scandir_files("/fake/root")
        self.assertEqual(result, [])

    def test_scandir_oserror_on_subdir_scandir_continues(self) -> None:
        """_scandir_files continues processing when a nested scandir raises OSError.

        The outer scandir call returns a dir entry, but scanning that subdir
        raises OSError (lines 88-89 path). The root-level file should still be
        returned.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "visible.md").write_text("hello", encoding="utf-8")
            locked = root / "locked_sub"
            locked.mkdir()
            (locked / "hidden.md").write_text("secret", encoding="utf-8")
            os.chmod(locked, 0o000)
            try:
                result = context_core._scandir_files(str(root))
                names = {p.name for p in [pair[1] for pair in result]}
                self.assertIn("visible.md", names)
                # hidden.md should be inaccessible — not required to be absent but
                # no exception should have been raised
            finally:
                os.chmod(locked, 0o755)


# ---------------------------------------------------------------------------
# _mmap_contains -- OSError branches (lines 151-152, 165-170)
# ---------------------------------------------------------------------------


class MmapContainsOSErrorTests(unittest.TestCase):
    """Exercise _mmap_contains error branches."""

    def test_mmap_contains_stat_oserror_returns_false(self) -> None:
        """_mmap_contains returns False when path.stat() raises OSError (lines 151-152)."""
        nonexistent = Path("/nonexistent/file/that/cannot/exist.md")
        result = context_core._mmap_contains(nonexistent, b"hello", 4096)
        self.assertFalse(result)

    def test_mmap_contains_mmap_oserror_falls_back_to_read_found(self, tmp_path: Path = None) -> None:
        """_mmap_contains falls back to regular read when mmap.mmap() raises OSError (lines 165-170).

        When the fallback read finds the query, _mmap_contains returns True.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "note.md"
            f.write_text("hello world FINDME content here", encoding="utf-8")

            def mmap_oserror(*args, **kwargs):
                raise OSError("mmap not supported")

            with mock.patch("mmap.mmap", side_effect=mmap_oserror):
                result = context_core._mmap_contains(f, b"findme", 4096)

            self.assertTrue(result)

    def test_mmap_contains_mmap_oserror_falls_back_to_read_not_found(self) -> None:
        """_mmap_contains falls back to regular read and returns False when not found (lines 165-170)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "note.md"
            f.write_text("hello world content here", encoding="utf-8")

            with mock.patch("mmap.mmap", side_effect=OSError("mmap not supported")):
                result = context_core._mmap_contains(f, b"notpresent", 4096)

            self.assertFalse(result)

    def test_mmap_contains_open_oserror_returns_false(self) -> None:
        """_mmap_contains returns False when open() raises OSError (line 177 path)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "note.md"
            f.write_text("content here FINDME", encoding="utf-8")

            original_open = open

            def open_oserror(path, mode="r", **kwargs):
                if "rb" in str(mode):
                    raise OSError("permission denied")
                return original_open(path, mode, **kwargs)

            with mock.patch("builtins.open", side_effect=open_oserror):
                result = context_core._mmap_contains(f, b"findme", 4096)

            self.assertFalse(result)


# ---------------------------------------------------------------------------
# _mmap_snippet -- OSError and empty-file branches (lines 202-203, 206, 214-216, 222-223, 229)
# ---------------------------------------------------------------------------


class MmapSnippetTests(unittest.TestCase):
    """Exercise _mmap_snippet error and edge-case branches."""

    def test_mmap_snippet_stat_oserror_returns_empty(self) -> None:
        """_mmap_snippet returns '' when path.stat() raises OSError (lines 202-203)."""
        nonexistent = Path("/nonexistent/path/that/cannot/exist.md")
        result = context_core._mmap_snippet(nonexistent, b"hello", "hello", 4096)
        self.assertEqual(result, "")

    def test_mmap_snippet_empty_file_returns_empty(self) -> None:
        """_mmap_snippet returns '' when the file is empty (line 206, size == 0)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "empty.md"
            f.write_bytes(b"")
            result = context_core._mmap_snippet(f, b"hello", "hello", 4096)
            self.assertEqual(result, "")

    def test_mmap_snippet_mmap_oserror_falls_back_to_read(self) -> None:
        """_mmap_snippet falls back to regular read when mmap.mmap() raises OSError (lines 214-216)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "note.md"
            f.write_text("before FINDTOKEN after", encoding="utf-8")

            with mock.patch("mmap.mmap", side_effect=OSError("mmap not supported")):
                result = context_core._mmap_snippet(f, b"findtoken", "findtoken", 4096)

            # Should still extract snippet using fallback read
            self.assertIn("FINDTOKEN", result)

    def test_mmap_snippet_open_oserror_returns_empty(self) -> None:
        """_mmap_snippet returns '' when open() raises OSError (lines 222-223)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "note.md"
            f.write_text("content FINDME here", encoding="utf-8")

            original_open = open

            def open_oserror(path, mode="r", **kwargs):
                if "rb" in str(mode):
                    raise OSError("permission denied")
                return original_open(path, mode, **kwargs)

            with mock.patch("builtins.open", side_effect=open_oserror):
                result = context_core._mmap_snippet(f, b"findme", "findme", 4096)

            self.assertEqual(result, "")

    def test_mmap_snippet_query_not_found_returns_empty(self) -> None:
        """_mmap_snippet returns '' when query_str is not found in file text (line 229)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "note.md"
            f.write_text("hello world no match here", encoding="utf-8")
            result = context_core._mmap_snippet(f, b"notpresent", "notpresent", 4096)
            self.assertEqual(result, "")

    def test_mmap_snippet_found_returns_nonempty(self) -> None:
        """_mmap_snippet returns a non-empty snippet when the query is found."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "note.md"
            f.write_text("prefix content MYTOKEN suffix content", encoding="utf-8")
            result = context_core._mmap_snippet(f, b"mytoken", "mytoken", 4096)
            self.assertIn("MYTOKEN", result)

    def test_mmap_snippet_query_at_start_of_file(self) -> None:
        """_mmap_snippet works when the query appears at the very start of the file."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "note.md"
            f.write_text("STARTTOKEN middle end", encoding="utf-8")
            result = context_core._mmap_snippet(f, b"starttoken", "starttoken", 4096)
            self.assertIn("STARTTOKEN", result)

    def test_mmap_snippet_query_at_end_of_file(self) -> None:
        """_mmap_snippet works when the query appears at the very end of the file."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "note.md"
            f.write_text("beginning middle ENDTOKEN", encoding="utf-8")
            result = context_core._mmap_snippet(f, b"endtoken", "endtoken", 4096)
            self.assertIn("ENDTOKEN", result)


# ---------------------------------------------------------------------------
# Integration: verify mmap fallback path through local_memory_matches
# ---------------------------------------------------------------------------


class MmapFallbackIntegrationTests(unittest.TestCase):
    """Verify that local_memory_matches still works when mmap is patched to fail.

    This exercises lines 165-170 (_mmap_contains fallback) and
    214-216 (_mmap_snippet fallback) via the public API.
    """

    def test_local_memory_matches_with_mmap_disabled(self) -> None:
        """local_memory_matches finds content matches even when mmap raises OSError."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "data.md"
            f.write_text("Some content with MMAP_FALLBACK_TOKEN here.", encoding="utf-8")

            with mock.patch("mmap.mmap", side_effect=OSError("mmap not supported")):
                matches = context_core.local_memory_matches(
                    "MMAP_FALLBACK_TOKEN",
                    shared_root=root,
                    limit=5,
                    files=[f],
                )

            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["matched_in"], "content")
            self.assertIn("MMAP_FALLBACK_TOKEN", matches[0]["snippet"])

    def test_local_memory_matches_mmap_disabled_no_match(self) -> None:
        """local_memory_matches returns [] when mmap fails and content doesn't match."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            f = root / "data.md"
            f.write_text("Some content without the token.", encoding="utf-8")

            with mock.patch("mmap.mmap", side_effect=OSError("mmap not supported")):
                matches = context_core.local_memory_matches(
                    "NOTPRESENT",
                    shared_root=root,
                    limit=5,
                    files=[f],
                )

            self.assertEqual(matches, [])


# ---------------------------------------------------------------------------
# _scandir_files -- ensure non-text files and hidden files also skip cleanly
# in all OSError-adjacent paths
# ---------------------------------------------------------------------------


class ScandirFilesEdgeCaseTests(unittest.TestCase):
    """Additional edge cases for _scandir_files to cover line 78->72 branch."""

    def test_scandir_files_skips_neither_file_nor_dir(self) -> None:
        """_scandir_files skips entries that are neither file nor dir (e.g. symlinks or sockets)."""
        fake_entry = mock.MagicMock()
        fake_entry.name = "special.md"
        fake_entry.path = "/fake/special.md"
        fake_entry.is_dir.return_value = False
        fake_entry.is_file.return_value = False  # not a regular file

        mock_it = mock.MagicMock()
        mock_it.__enter__ = mock.Mock(return_value=iter([fake_entry]))
        mock_it.__exit__ = mock.Mock(return_value=False)

        with mock.patch("os.scandir", return_value=mock_it):
            result = context_core._scandir_files("/fake/root")
        # Neither file nor dir entry is ignored
        self.assertEqual(result, [])

    def test_scandir_files_dir_entry_added_to_stack(self) -> None:
        """_scandir_files appends directory entries to the stack (line 77 / 78->72 loop branch)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sub = root / "subdir"
            sub.mkdir()
            (sub / "nested.md").write_text("nested content", encoding="utf-8")

            result = context_core._scandir_files(str(root))
            names = {pair[1].name for pair in result}
            self.assertIn("nested.md", names)


if __name__ == "__main__":
    unittest.main()
