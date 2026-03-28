#!/usr/bin/env python3
"""R26 tests for context_cli — cmd_q subsystem and related functions.

Targets:
- _uuid_prefix_pattern: regex matching for valid/invalid UUID prefixes
- cmd_q: empty query guard, routing to _q_session_lookup vs _q_search
- _q_session_lookup: found and not-found paths
- _q_search: vector available path, FTS fallback, JSON output
- _print_q_results: text and JSON output format
- cmd_shell_init: prints shell integration script, returns 0
- cmd_vector_sync: ImportError path, vector_available() False, success path
- cmd_vector_status: ImportError path
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import context_cli  # noqa: E402

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_si_mock(
    *,
    lookup_rows: list[dict] | None = None,
    search_text: str = "Found 2 sessions\nResult line",
    search_rows: list[dict] | None = None,
    db_path: str = "/tmp/test_session.db",
) -> mock.MagicMock:
    """Return a MagicMock shaped like session_index module."""
    m = mock.MagicMock()
    m.get_session_db_path.return_value = db_path
    m.lookup_session_by_id.return_value = lookup_rows if lookup_rows is not None else []
    m.format_search_results.return_value = search_text
    m._search_rows.return_value = search_rows if search_rows is not None else []
    return m


def _make_result_row(
    *,
    session_id: str = "abcd1234-0000-0000-0000-000000000000",
    created_at: str = "2025-01-15",
    source_type: str = "shell_zsh",
    title: str = "Test Session",
    snippet: str = "This is a test snippet.",
) -> dict:
    """Return a minimal result row dict."""
    return {
        "session_id": session_id,
        "created_at": created_at,
        "source_type": source_type,
        "title": title,
        "snippet": snippet,
    }


# ---------------------------------------------------------------------------
# 1. _uuid_prefix_pattern — valid UUID prefixes match
# ---------------------------------------------------------------------------


class TestUuidPrefixPatternMatch(unittest.TestCase):
    """_uuid_prefix_pattern() must match valid UUID-style hex prefixes."""

    def _pat(self) -> object:
        # Reset the cached regex so each test gets a fresh compile path.
        context_cli._UUID_PREFIX_RE = None
        return context_cli._uuid_prefix_pattern()

    def test_eight_hex_chars_lowercase(self) -> None:
        """Exactly 8 lowercase hex characters must match."""
        self.assertIsNotNone(self._pat().match("abcd1234"))

    def test_eight_hex_chars_uppercase(self) -> None:
        """Exactly 8 uppercase hex characters must match (IGNORECASE flag)."""
        self.assertIsNotNone(self._pat().match("ABCD1234"))

    def test_eight_hex_chars_mixed_case(self) -> None:
        """Mixed case 8-char hex string must match."""
        self.assertIsNotNone(self._pat().match("AbCd1234"))

    def test_full_uuid_with_dashes(self) -> None:
        """A full UUID with dashes must match."""
        self.assertIsNotNone(self._pat().match("abcd1234-1234-5678-abcd-000000000000"))

    def test_partial_uuid_nine_hex_no_dash(self) -> None:
        """9 hex chars (no dash) must match."""
        self.assertIsNotNone(self._pat().match("abcd12345"))

    def test_partial_uuid_with_trailing_dash(self) -> None:
        """8 hex chars followed by a dash must match."""
        self.assertIsNotNone(self._pat().match("abcd1234-"))

    def test_sixteen_hex_chars(self) -> None:
        """16 consecutive hex characters must match."""
        self.assertIsNotNone(self._pat().match("deadbeefcafe0011"))

    def test_pattern_is_cached_after_first_call(self) -> None:
        """Second call must return the exact same compiled object (cached)."""
        context_cli._UUID_PREFIX_RE = None
        pat1 = context_cli._uuid_prefix_pattern()
        pat2 = context_cli._uuid_prefix_pattern()
        self.assertIs(pat1, pat2)


# ---------------------------------------------------------------------------
# 2. _uuid_prefix_pattern — non-UUID strings do not match
# ---------------------------------------------------------------------------


class TestUuidPrefixPatternNoMatch(unittest.TestCase):
    """_uuid_prefix_pattern() must NOT match non-UUID strings."""

    def _pat(self) -> object:
        context_cli._UUID_PREFIX_RE = None
        return context_cli._uuid_prefix_pattern()

    def test_plain_word_no_match(self) -> None:
        """A plain English word must not match."""
        self.assertIsNone(self._pat().match("hello"))

    def test_auth_token_no_match(self) -> None:
        """The string 'auth token' must not match."""
        self.assertIsNone(self._pat().match("auth token"))

    def test_seven_hex_chars_no_match(self) -> None:
        """Only 7 hex chars (below minimum) must not match."""
        self.assertIsNone(self._pat().match("abcd123"))

    def test_empty_string_no_match(self) -> None:
        """An empty string must not match."""
        self.assertIsNone(self._pat().match(""))

    def test_starts_with_non_hex_no_match(self) -> None:
        """A string starting with a non-hex character must not match."""
        self.assertIsNone(self._pat().match("ghi12345"))

    def test_natural_language_query_no_match(self) -> None:
        """A typical natural language query must not match."""
        self.assertIsNone(self._pat().match("what did I work on yesterday"))

    def test_uuid_with_leading_space_no_match(self) -> None:
        """A UUID prefixed by a space must not match (anchored at ^)."""
        self.assertIsNone(self._pat().match(" abcd1234"))


# ---------------------------------------------------------------------------
# 3. cmd_q — empty query returns 2
# ---------------------------------------------------------------------------


class TestCmdQEmptyQuery(unittest.TestCase):
    """cmd_q must return exit code 2 when the query list is empty."""

    def test_empty_list_returns_2(self) -> None:
        """args.query=[] must cause cmd_q to return 2."""
        args = SimpleNamespace(query=[], json=False, limit=5)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = context_cli.cmd_q(args)
        self.assertEqual(rc, 2)
        self.assertIn("Usage", buf.getvalue())

    def test_whitespace_only_list_returns_2(self) -> None:
        """args.query=['   '] (whitespace only) must return 2."""
        args = SimpleNamespace(query=["   "], json=False, limit=5)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = context_cli.cmd_q(args)
        self.assertEqual(rc, 2)


# ---------------------------------------------------------------------------
# 4. cmd_q — UUID prefix routes to _q_session_lookup
# ---------------------------------------------------------------------------


class TestCmdQRoutesToSessionLookup(unittest.TestCase):
    """cmd_q must call _q_session_lookup when the query looks like a UUID prefix."""

    def test_uuid_prefix_routes_to_session_lookup(self) -> None:
        """An 8-hex-char query must be dispatched to _q_session_lookup."""
        args = SimpleNamespace(query=["abcd1234"], json=False, limit=5)
        with (
            mock.patch.object(context_cli, "_q_session_lookup", return_value=0) as mock_lookup,
            mock.patch.object(context_cli, "_q_search") as mock_search,
        ):
            rc = context_cli.cmd_q(args)
        mock_lookup.assert_called_once_with("abcd1234", 5, False)
        mock_search.assert_not_called()
        self.assertEqual(rc, 0)

    def test_full_uuid_routes_to_session_lookup(self) -> None:
        """A full UUID string must be dispatched to _q_session_lookup."""
        full_uuid = "abcd1234-1234-5678-abcd-000000000000"
        args = SimpleNamespace(query=[full_uuid], json=False, limit=10)
        with (
            mock.patch.object(context_cli, "_q_session_lookup", return_value=0) as mock_lookup,
            mock.patch.object(context_cli, "_q_search") as mock_search,
        ):
            rc = context_cli.cmd_q(args)
        mock_lookup.assert_called_once_with(full_uuid, 10, False)
        mock_search.assert_not_called()
        self.assertEqual(rc, 0)

    def test_json_flag_propagated_to_session_lookup(self) -> None:
        """The --json flag must be passed through to _q_session_lookup."""
        args = SimpleNamespace(query=["deadbeef"], json=True, limit=3)
        with mock.patch.object(context_cli, "_q_session_lookup", return_value=0) as mock_lookup:
            context_cli.cmd_q(args)
        mock_lookup.assert_called_once_with("deadbeef", 3, True)


# ---------------------------------------------------------------------------
# 5. cmd_q — non-UUID routes to _q_search
# ---------------------------------------------------------------------------


class TestCmdQRoutesToSearch(unittest.TestCase):
    """cmd_q must call _q_search when the query does not look like a UUID prefix."""

    def test_natural_language_routes_to_search(self) -> None:
        """A natural language query must be dispatched to _q_search."""
        args = SimpleNamespace(query=["what", "did", "I", "work", "on"], json=False, limit=5)
        with (
            mock.patch.object(context_cli, "_q_search", return_value=0) as mock_search,
            mock.patch.object(context_cli, "_q_session_lookup") as mock_lookup,
        ):
            rc = context_cli.cmd_q(args)
        mock_search.assert_called_once_with("what did I work on", 5, False)
        mock_lookup.assert_not_called()
        self.assertEqual(rc, 0)

    def test_short_hex_routes_to_search(self) -> None:
        """A 7-hex-char string (below UUID threshold) must go to _q_search."""
        args = SimpleNamespace(query=["abcdef1"], json=False, limit=5)
        with (
            mock.patch.object(context_cli, "_q_search", return_value=0) as mock_search,
            mock.patch.object(context_cli, "_q_session_lookup") as mock_lookup,
        ):
            context_cli.cmd_q(args)
        mock_search.assert_called_once()
        mock_lookup.assert_not_called()

    def test_json_flag_propagated_to_search(self) -> None:
        """The --json flag must be passed through to _q_search."""
        args = SimpleNamespace(query=["rust", "performance"], json=True, limit=7)
        with mock.patch.object(context_cli, "_q_search", return_value=0) as mock_search:
            context_cli.cmd_q(args)
        mock_search.assert_called_once_with("rust performance", 7, True)


# ---------------------------------------------------------------------------
# 6. _q_session_lookup — found path
# ---------------------------------------------------------------------------


class TestQSessionLookupFound(unittest.TestCase):
    """_q_session_lookup must print results and return 0 when sessions are found."""

    def test_found_returns_zero(self) -> None:
        """lookup_session_by_id returning rows must yield return code 0."""
        rows = [_make_result_row()]
        si_mock = _make_si_mock(lookup_rows=rows)
        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            rc = context_cli._q_session_lookup("abcd1234", limit=5, as_json=False)
        self.assertEqual(rc, 0)

    def test_found_calls_lookup_with_correct_args(self) -> None:
        """lookup_session_by_id must be called with the session_id and limit."""
        rows = [_make_result_row()]
        si_mock = _make_si_mock(lookup_rows=rows)
        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            context_cli._q_session_lookup("abcd1234", limit=10, as_json=False)
        si_mock.lookup_session_by_id.assert_called_once_with("abcd1234", limit=10)

    def test_found_calls_print_q_results(self) -> None:
        """_print_q_results must be called with the returned rows."""
        rows = [_make_result_row(), _make_result_row(title="Second")]
        si_mock = _make_si_mock(lookup_rows=rows)
        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.object(context_cli, "_print_q_results") as mock_print,
        ):
            context_cli._q_session_lookup("abcd1234", limit=5, as_json=True)
        mock_print.assert_called_once_with(rows, as_json=True)

    def test_found_with_multiple_rows(self) -> None:
        """Return code must be 0 even when multiple rows are returned."""
        rows = [_make_result_row(session_id=f"abcd{i:04x}-0000-0000-0000-000000000000") for i in range(5)]
        si_mock = _make_si_mock(lookup_rows=rows)
        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            rc = context_cli._q_session_lookup("abcd", limit=5, as_json=False)
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# 7. _q_session_lookup — not found path
# ---------------------------------------------------------------------------


class TestQSessionLookupNotFound(unittest.TestCase):
    """_q_session_lookup must print an error to stderr and return 1 when no sessions found."""

    def test_not_found_returns_one(self) -> None:
        """An empty lookup result must yield return code 1."""
        si_mock = _make_si_mock(lookup_rows=[])
        with mock.patch.object(context_cli, "_get_session_index", return_value=si_mock):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                rc = context_cli._q_session_lookup("deadbeef", limit=5, as_json=False)
        self.assertEqual(rc, 1)

    def test_not_found_prints_message_to_stderr(self) -> None:
        """A 'No session found' message must be written to stderr."""
        si_mock = _make_si_mock(lookup_rows=[])
        with mock.patch.object(context_cli, "_get_session_index", return_value=si_mock):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                context_cli._q_session_lookup("deadbeef", limit=5, as_json=False)
        self.assertIn("deadbeef", buf.getvalue())

    def test_not_found_does_not_call_print_q_results(self) -> None:
        """_print_q_results must NOT be called when no rows are found."""
        si_mock = _make_si_mock(lookup_rows=[])
        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.object(context_cli, "_print_q_results") as mock_print,
        ):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                context_cli._q_session_lookup("deadbeef", limit=5, as_json=False)
        mock_print.assert_not_called()


# ---------------------------------------------------------------------------
# 8. _q_search — vector available path
# ---------------------------------------------------------------------------


class TestQSearchVectorAvailable(unittest.TestCase):
    """_q_search must use hybrid vector search when vector_index is available."""

    def test_vector_path_returns_zero_on_results(self) -> None:
        """When hybrid_search_session returns ranked results, return code must be 0."""
        si_mock = _make_si_mock()
        rows = [_make_result_row()]
        ranked = [{"doc_id": 1, "score": 0.95}]

        vector_mod = types.ModuleType("vector_index")
        vector_mod.vector_available = lambda: True
        vector_mod.get_vector_db_path = lambda db: "/tmp/vector.db"
        vector_mod.hybrid_search_session = mock.MagicMock(return_value=ranked)
        vector_mod.fetch_enriched_results = mock.MagicMock(return_value=rows)

        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": vector_mod}),
            mock.patch.object(context_cli, "_print_q_results") as mock_print,
        ):
            rc = context_cli._q_search("rust performance", limit=5, as_json=False)

        self.assertEqual(rc, 0)
        mock_print.assert_called_once_with(rows, as_json=False)

    def test_vector_path_calls_hybrid_search_with_query(self) -> None:
        """hybrid_search_session must be invoked with the correct query."""
        si_mock = _make_si_mock()
        ranked = [{"doc_id": 1, "score": 0.9}]
        rows = [_make_result_row()]

        hybrid_mock = mock.MagicMock(return_value=ranked)
        enrich_mock = mock.MagicMock(return_value=rows)

        vector_mod = types.ModuleType("vector_index")
        vector_mod.vector_available = lambda: True
        vector_mod.get_vector_db_path = lambda db: "/tmp/vector.db"
        vector_mod.hybrid_search_session = hybrid_mock
        vector_mod.fetch_enriched_results = enrich_mock

        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": vector_mod}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            context_cli._q_search("go channels", limit=3, as_json=False)

        hybrid_mock.assert_called_once()
        call_args = hybrid_mock.call_args
        self.assertEqual(call_args[0][0], "go channels")

    def test_vector_path_falls_back_when_ranked_empty(self) -> None:
        """When hybrid_search returns no ranked results, FTS fallback must be used."""
        si_mock = _make_si_mock(search_text="Found 1 sessions\nsome result")

        vector_mod = types.ModuleType("vector_index")
        vector_mod.vector_available = lambda: True
        vector_mod.get_vector_db_path = lambda db: "/tmp/vector.db"
        vector_mod.hybrid_search_session = mock.MagicMock(return_value=[])
        vector_mod.fetch_enriched_results = mock.MagicMock(return_value=[])

        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": vector_mod}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            context_cli._q_search("some query", limit=5, as_json=False)

        # FTS fallback should have been used
        si_mock.format_search_results.assert_called_once()

    def test_vector_exception_falls_back_to_fts(self) -> None:
        """An exception inside the vector path must silently fall back to FTS."""
        si_mock = _make_si_mock(search_text="Found 1 sessions\nsome result")

        vector_mod = types.ModuleType("vector_index")
        vector_mod.vector_available = lambda: True
        vector_mod.get_vector_db_path = lambda db: "/tmp/vector.db"
        vector_mod.hybrid_search_session = mock.MagicMock(side_effect=RuntimeError("model error"))
        vector_mod.fetch_enriched_results = mock.MagicMock()

        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": vector_mod}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            # Must not raise
            context_cli._q_search("query after vector error", limit=5, as_json=False)

        si_mock.format_search_results.assert_called_once()


# ---------------------------------------------------------------------------
# 9. _q_search — FTS fallback path (no vector available)
# ---------------------------------------------------------------------------


class TestQSearchFallbackFts(unittest.TestCase):
    """_q_search must fall back to FTS5/LIKE when vector is not available."""

    def _run_fts_fallback(
        self,
        search_text: str = "Found 2 sessions\nResult line",
        as_json: bool = False,
    ) -> tuple[int, str]:
        si_mock = _make_si_mock(search_text=search_text)

        # Simulate vector_index raising ImportError
        vector_mod = types.ModuleType("vector_index")
        vector_mod.vector_available = lambda: False
        vector_mod.get_vector_db_path = lambda db: "/tmp/vector.db"
        vector_mod.hybrid_search_session = mock.MagicMock(return_value=[])
        vector_mod.fetch_enriched_results = mock.MagicMock(return_value=[])

        buf = io.StringIO()
        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": vector_mod}),
            contextlib.redirect_stdout(buf),
        ):
            rc = context_cli._q_search("fallback query", limit=5, as_json=as_json)
        return rc, buf.getvalue()

    def test_fts_fallback_calls_format_search_results(self) -> None:
        """format_search_results must be called during FTS fallback."""
        si_mock = _make_si_mock(search_text="Found 1 sessions\nResult")
        vector_mod = types.ModuleType("vector_index")
        vector_mod.vector_available = lambda: False
        vector_mod.get_vector_db_path = lambda db: "/tmp/vector.db"
        vector_mod.hybrid_search_session = mock.MagicMock(return_value=[])
        vector_mod.fetch_enriched_results = mock.MagicMock(return_value=[])

        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": vector_mod}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            context_cli._q_search("fallback query", limit=7, as_json=False)

        si_mock.format_search_results.assert_called_once_with("fallback query", limit=7)

    def test_fts_fallback_returns_zero_when_results_found(self) -> None:
        """Return code must be 0 when FTS produces results."""
        rc, _ = self._run_fts_fallback(search_text="Found 3 sessions\nSome result text")
        self.assertEqual(rc, 0)

    def test_fts_fallback_returns_one_when_no_matches(self) -> None:
        """Return code must be 1 when FTS returns 'No matches found'."""
        rc, _ = self._run_fts_fallback(search_text="No matches found")
        self.assertEqual(rc, 1)

    def test_fts_fallback_returns_one_when_empty_text(self) -> None:
        """Return code must be 1 when FTS returns empty string."""
        rc, _ = self._run_fts_fallback(search_text="")
        self.assertEqual(rc, 1)

    def test_fts_fallback_prints_text_output(self) -> None:
        """The FTS text output must be printed to stdout."""
        rc, output = self._run_fts_fallback(search_text="Found 2 sessions\nSome result")
        self.assertIn("Found 2 sessions", output)


# ---------------------------------------------------------------------------
# 10. _q_search — JSON output flag
# ---------------------------------------------------------------------------


class TestQSearchJsonOutput(unittest.TestCase):
    """_q_search must emit valid JSON when as_json=True is set."""

    def test_json_output_is_valid_json(self) -> None:
        """Output when as_json=True must be parseable JSON."""
        search_rows = [_make_result_row()]
        si_mock = _make_si_mock(
            search_text="No matches found",
            search_rows=search_rows,
        )
        # Ensure _search_rows is accessible on the mock
        si_mock._search_rows.return_value = search_rows

        vector_mod = types.ModuleType("vector_index")
        vector_mod.vector_available = lambda: False
        vector_mod.get_vector_db_path = lambda db: "/tmp/vector.db"
        vector_mod.hybrid_search_session = mock.MagicMock(return_value=[])
        vector_mod.fetch_enriched_results = mock.MagicMock(return_value=[])

        buf = io.StringIO()
        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": vector_mod}),
            contextlib.redirect_stdout(buf),
        ):
            context_cli._q_search("any query", limit=5, as_json=True)

        output = buf.getvalue().strip()
        # Should be valid JSON (either a list or the search text printed directly)
        if output:
            try:
                json.loads(output)
            except json.JSONDecodeError:
                self.fail(f"Output is not valid JSON: {output!r}")

    def test_json_output_from_vector_path_is_valid(self) -> None:
        """When vector returns results and as_json=True, _print_q_results is called with as_json=True."""
        si_mock = _make_si_mock()
        rows = [_make_result_row()]
        ranked = [{"doc_id": 1, "score": 0.95}]

        vector_mod = types.ModuleType("vector_index")
        vector_mod.vector_available = lambda: True
        vector_mod.get_vector_db_path = lambda db: "/tmp/vector.db"
        vector_mod.hybrid_search_session = mock.MagicMock(return_value=ranked)
        vector_mod.fetch_enriched_results = mock.MagicMock(return_value=rows)

        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": vector_mod}),
            mock.patch.object(context_cli, "_print_q_results") as mock_print,
        ):
            context_cli._q_search("test query", limit=5, as_json=True)

        mock_print.assert_called_once_with(rows, as_json=True)


# ---------------------------------------------------------------------------
# 11. _print_q_results — text output format
# ---------------------------------------------------------------------------


class TestPrintQResultsText(unittest.TestCase):
    """_print_q_results must produce compact text output with correct fields."""

    def _capture_text(self, results: list[dict]) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            context_cli._print_q_results(results, as_json=False)
        return buf.getvalue()

    def test_text_output_contains_index(self) -> None:
        """Each result line must start with '[N]'."""
        row = _make_result_row()
        output = self._capture_text([row])
        self.assertIn("[1]", output)

    def test_text_output_contains_session_id_prefix(self) -> None:
        """Output must contain the first 8 chars of session_id."""
        row = _make_result_row(session_id="abcd1234-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
        output = self._capture_text([row])
        self.assertIn("abcd1234", output)

    def test_text_output_contains_created_at(self) -> None:
        """Output must contain the first 10 chars of created_at (date part)."""
        row = _make_result_row(created_at="2025-01-15T10:30:00")
        output = self._capture_text([row])
        self.assertIn("2025-01-15", output)

    def test_text_output_contains_source_type(self) -> None:
        """Output must contain the source_type field."""
        row = _make_result_row(source_type="shell_bash")
        output = self._capture_text([row])
        self.assertIn("shell_bash", output)

    def test_text_output_contains_title(self) -> None:
        """Output must contain the title field."""
        row = _make_result_row(title="My Important Task")
        output = self._capture_text([row])
        self.assertIn("My Important Task", output)

    def test_text_output_contains_snippet(self) -> None:
        """Output must contain the snippet when present."""
        row = _make_result_row(snippet="This is a meaningful snippet")
        output = self._capture_text([row])
        self.assertIn("This is a meaningful snippet", output)

    def test_text_output_skips_snippet_prefix_when_empty(self) -> None:
        """When snippet is empty, the '> ' prefix line must not appear."""
        row = _make_result_row(snippet="")
        output = self._capture_text([row])
        self.assertNotIn("> ", output)

    def test_text_output_multiple_results_numbered(self) -> None:
        """Multiple results must be numbered sequentially."""
        rows = [_make_result_row(title=f"Result {i}") for i in range(3)]
        output = self._capture_text(rows)
        self.assertIn("[1]", output)
        self.assertIn("[2]", output)
        self.assertIn("[3]", output)

    def test_text_output_empty_list_produces_no_output(self) -> None:
        """An empty results list must produce no output."""
        output = self._capture_text([])
        self.assertEqual(output, "")

    def test_snippet_newlines_replaced_by_space(self) -> None:
        """Newlines in snippets must be replaced with spaces."""
        row = _make_result_row(snippet="line one\nline two\nline three")
        output = self._capture_text([row])
        self.assertNotIn("\nline", output)
        self.assertIn("line one", output)

    def test_snippet_truncated_at_200_chars(self) -> None:
        """Snippets longer than 200 characters must be truncated."""
        long_snippet = "x" * 300
        row = _make_result_row(snippet=long_snippet)
        output = self._capture_text([row])
        # The snippet line with "> " prefix should not exceed 200 visible chars of snippet
        snippet_line = [ln for ln in output.splitlines() if ln.strip().startswith(">")]
        self.assertTrue(len(snippet_line) > 0)
        # Snippet content after "> " should not be longer than 200 chars
        content = snippet_line[0].lstrip().lstrip(">").strip()
        self.assertLessEqual(len(content), 200)


# ---------------------------------------------------------------------------
# 12. _print_q_results — JSON output
# ---------------------------------------------------------------------------


class TestPrintQResultsJson(unittest.TestCase):
    """_print_q_results must produce valid JSON when as_json=True."""

    def _capture_json(self, results: list[dict]) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            context_cli._print_q_results(results, as_json=True)
        return buf.getvalue()

    def test_json_output_is_parseable(self) -> None:
        """JSON output must be parseable."""
        rows = [_make_result_row()]
        output = self._capture_json(rows)
        parsed = json.loads(output)
        self.assertIsInstance(parsed, list)

    def test_json_output_contains_all_fields(self) -> None:
        """Parsed JSON must contain all original fields."""
        row = _make_result_row(
            session_id="abcd1234-0000-0000-0000-000000000000",
            title="Test Title",
            snippet="Test snippet",
        )
        output = self._capture_json([row])
        parsed = json.loads(output)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["session_id"], row["session_id"])
        self.assertEqual(parsed[0]["title"], row["title"])

    def test_json_output_multiple_items(self) -> None:
        """Multiple rows must all appear in the JSON array."""
        rows = [_make_result_row(title=f"Item {i}") for i in range(4)]
        output = self._capture_json(rows)
        parsed = json.loads(output)
        self.assertEqual(len(parsed), 4)

    def test_json_output_empty_list(self) -> None:
        """An empty list must produce a JSON empty array."""
        output = self._capture_json([])
        parsed = json.loads(output)
        self.assertEqual(parsed, [])

    def test_json_output_uses_ensure_ascii_false(self) -> None:
        """CJK characters in snippets must appear as literals, not as \\uXXXX escapes."""
        row = _make_result_row(snippet="测试内容 test content")
        output = self._capture_json([row])
        self.assertIn("测试内容", output)
        self.assertNotIn("\\u6d4b", output)


# ---------------------------------------------------------------------------
# 13. cmd_shell_init — prints shell integration script, returns 0
# ---------------------------------------------------------------------------


class TestCmdShellInit(unittest.TestCase):
    """cmd_shell_init must print the shell integration script and return 0."""

    def _run(self) -> tuple[int, str]:
        args = SimpleNamespace()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = context_cli.cmd_shell_init(args)
        return rc, buf.getvalue()

    def test_returns_zero(self) -> None:
        """cmd_shell_init must always return 0."""
        rc, _ = self._run()
        self.assertEqual(rc, 0)

    def test_output_contains_contextgo_q(self) -> None:
        """Output must contain 'contextgo q' (the quick recall alias)."""
        _, output = self._run()
        self.assertIn("contextgo q", output)

    def test_output_contains_cg_function(self) -> None:
        """Output must define the 'cg()' shell function."""
        _, output = self._run()
        self.assertIn("cg()", output)

    def test_output_contains_shell_integration_comment(self) -> None:
        """Output must include a ContextGO shell integration comment."""
        _, output = self._run()
        self.assertIn("ContextGO shell integration", output)

    def test_output_contains_cgs_alias(self) -> None:
        """Output must contain the 'cgs' search shorthand alias."""
        _, output = self._run()
        self.assertIn("cgs", output)

    def test_output_contains_eval_instruction(self) -> None:
        """Output must include eval instruction for shell initialization."""
        _, output = self._run()
        self.assertIn("eval", output)

    def test_no_args_needed(self) -> None:
        """cmd_shell_init must work with any args object, including an empty namespace."""
        for args in [SimpleNamespace(), SimpleNamespace(extra="ignored"), object()]:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = context_cli.cmd_shell_init(args)
            self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# 14. cmd_vector_sync — ImportError for vector_index
# ---------------------------------------------------------------------------


class TestCmdVectorSyncNoDeps(unittest.TestCase):
    """cmd_vector_sync must return 1 and print an error when vector_index is missing."""

    def test_import_error_returns_one(self) -> None:
        """ImportError for vector_index must cause cmd_vector_sync to return 1."""
        args = SimpleNamespace(force=False)
        si_mock = _make_si_mock()

        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": None}),
        ):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                rc = context_cli.cmd_vector_sync(args)

        self.assertEqual(rc, 1)

    def test_import_error_prints_error_message(self) -> None:
        """An ImportError must produce an error message on stderr."""
        args = SimpleNamespace(force=False)
        si_mock = _make_si_mock()

        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": None}),
        ):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                context_cli.cmd_vector_sync(args)

        self.assertIn("Error", buf.getvalue())


# ---------------------------------------------------------------------------
# 15. cmd_vector_sync — vector_available() returns False
# ---------------------------------------------------------------------------


class TestCmdVectorSyncNotAvailable(unittest.TestCase):
    """cmd_vector_sync must return 1 when vector_available() returns False."""

    def test_not_available_returns_one(self) -> None:
        """When vector_available() is False, cmd_vector_sync must return 1."""
        args = SimpleNamespace(force=False)
        si_mock = _make_si_mock()

        vector_mod = types.ModuleType("vector_index")
        vector_mod.vector_available = lambda: False
        vector_mod.get_vector_db_path = lambda db: "/tmp/vector.db"
        vector_mod.embed_pending_session_docs = mock.MagicMock()

        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": vector_mod}),
        ):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                rc = context_cli.cmd_vector_sync(args)

        self.assertEqual(rc, 1)

    def test_not_available_prints_error_to_stderr(self) -> None:
        """An unavailability error message must be written to stderr."""
        args = SimpleNamespace(force=False)
        si_mock = _make_si_mock()

        vector_mod = types.ModuleType("vector_index")
        vector_mod.vector_available = lambda: False
        vector_mod.get_vector_db_path = lambda db: "/tmp/vector.db"
        vector_mod.embed_pending_session_docs = mock.MagicMock()

        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": vector_mod}),
        ):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                context_cli.cmd_vector_sync(args)

        self.assertIn("Error", buf.getvalue())

    def test_not_available_does_not_call_embed(self) -> None:
        """embed_pending_session_docs must NOT be called when vector is unavailable."""
        args = SimpleNamespace(force=False)
        si_mock = _make_si_mock()
        embed_mock = mock.MagicMock()

        vector_mod = types.ModuleType("vector_index")
        vector_mod.vector_available = lambda: False
        vector_mod.get_vector_db_path = lambda db: "/tmp/vector.db"
        vector_mod.embed_pending_session_docs = embed_mock

        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": vector_mod}),
        ):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                context_cli.cmd_vector_sync(args)

        embed_mock.assert_not_called()


# ---------------------------------------------------------------------------
# 16. cmd_vector_status — ImportError for vector_index
# ---------------------------------------------------------------------------


class TestCmdVectorStatusNoDeps(unittest.TestCase):
    """cmd_vector_status must return 1 and print an error when vector_index is missing."""

    def test_import_error_returns_one(self) -> None:
        """ImportError for vector_index in cmd_vector_status must return 1."""
        args = SimpleNamespace()
        si_mock = _make_si_mock()

        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": None}),
        ):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                rc = context_cli.cmd_vector_status(args)

        self.assertEqual(rc, 1)

    def test_import_error_prints_error_to_stderr(self) -> None:
        """An ImportError must produce an error message on stderr for cmd_vector_status."""
        args = SimpleNamespace()
        si_mock = _make_si_mock()

        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": None}),
        ):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                context_cli.cmd_vector_status(args)

        self.assertIn("Error", buf.getvalue())


# ---------------------------------------------------------------------------
# 17. cmd_vector_sync — success path
# ---------------------------------------------------------------------------


class TestCmdVectorSyncSuccess(unittest.TestCase):
    """cmd_vector_sync must return 0 and emit a JSON payload on success."""

    def _run_sync(self, *, force: bool = False) -> tuple[int, str]:
        args = SimpleNamespace(force=force)
        si_mock = _make_si_mock()

        embed_result = {"embedded": 5, "skipped": 2, "deleted": 0}
        embed_mock = mock.MagicMock(return_value=embed_result)

        vector_mod = types.ModuleType("vector_index")
        vector_mod.vector_available = lambda: True
        vector_mod.get_vector_db_path = lambda db: "/tmp/vector.db"
        vector_mod.embed_pending_session_docs = embed_mock

        buf = io.StringIO()
        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": vector_mod}),
            contextlib.redirect_stdout(buf),
        ):
            rc = context_cli.cmd_vector_sync(args)
        return rc, buf.getvalue()

    def test_success_returns_zero(self) -> None:
        """cmd_vector_sync must return 0 on success."""
        rc, _ = self._run_sync()
        self.assertEqual(rc, 0)

    def test_success_output_is_valid_json(self) -> None:
        """Success output must be valid JSON."""
        _, output = self._run_sync()
        parsed = json.loads(output)
        self.assertIsInstance(parsed, dict)

    def test_success_output_contains_embedded_count(self) -> None:
        """Success JSON must contain the 'embedded' count."""
        _, output = self._run_sync()
        parsed = json.loads(output)
        self.assertIn("embedded", parsed)
        self.assertEqual(parsed["embedded"], 5)

    def test_success_output_contains_skipped_count(self) -> None:
        """Success JSON must contain the 'skipped' count."""
        _, output = self._run_sync()
        parsed = json.loads(output)
        self.assertIn("skipped", parsed)
        self.assertEqual(parsed["skipped"], 2)

    def test_success_output_contains_elapsed_sec(self) -> None:
        """Success JSON must contain 'elapsed_sec' field."""
        _, output = self._run_sync()
        parsed = json.loads(output)
        self.assertIn("elapsed_sec", parsed)
        self.assertIsInstance(parsed["elapsed_sec"], (int, float))

    def test_success_output_contains_vector_db_path(self) -> None:
        """Success JSON must contain 'vector_db' path."""
        _, output = self._run_sync()
        parsed = json.loads(output)
        self.assertIn("vector_db", parsed)

    def test_success_with_force_flag_calls_embed_with_force_true(self) -> None:
        """When force=True, embed_pending_session_docs must be called with force=True."""
        args = SimpleNamespace(force=True)
        si_mock = _make_si_mock()

        embed_result = {"embedded": 10, "skipped": 0, "deleted": 1}
        embed_mock = mock.MagicMock(return_value=embed_result)

        vector_mod = types.ModuleType("vector_index")
        vector_mod.vector_available = lambda: True
        vector_mod.get_vector_db_path = lambda db: "/tmp/vector.db"
        vector_mod.embed_pending_session_docs = embed_mock

        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.dict(sys.modules, {"vector_index": vector_mod}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            context_cli.cmd_vector_sync(args)

        embed_mock.assert_called_once()
        call_kwargs = embed_mock.call_args[1]
        self.assertTrue(call_kwargs.get("force", False))


if __name__ == "__main__":
    unittest.main()
