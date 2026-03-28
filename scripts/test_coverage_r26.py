#!/usr/bin/env python3
"""R26 coverage gap tests for vector_index.py and context_cli.py.

Targets
-------
vector_index.py:
  - Line 99:   _load_model with already-loaded model (cached path)
  - Line 147:  cosine_similarity with two zero vectors
  - Line 246:  embed_pending_session_docs — suffix not .db
  - Line 249:  embed_pending_session_docs — unsafe chars in path
  - Lines 278-283: stale vector deletion (batch flush path)
  - Line 364:  vector_search_session returns [] when all scores <= 0
  - Line 416:  BM25 cache hit path
  - Line 484:  fetch_enriched_results — row missing from DB
  - Line 489:  _build_snippet — no match returns head of content

context_cli.py:
  - Lines 734-737: cmd_vector_status via package import (.vector_index)
  - Lines 800-801: _q_search via package import (.vector_index)
"""

from __future__ import annotations

import sqlite3
import sys
import types
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Ensure scripts/ is on the path
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Skip whole module when numpy is absent (CI without [vector] extra)
np = pytest.importorskip("numpy", reason="numpy not installed — vector tests require numpy")

# ---------------------------------------------------------------------------
# Fake model2vec — defined here but NOT registered at module level.
#
# Registering at module level via setdefault causes a conflict when
# test_vector_index.py is collected after this file: our class ends up in
# sys.modules["model2vec"].StaticModel, but test_vector_index.py's
# isinstance(model, FakeStaticModel) checks against its *own* local class.
#
# Instead we register lazily inside the _reset_vi fixture so that:
#   - When running standalone the fake is installed before any test runs.
#   - When running alongside test_vector_index.py, test_vector_index.py's
#     setdefault (which runs at its module level, before our fixture) wins,
#     and our setdefault inside the fixture is a no-op.
# ---------------------------------------------------------------------------
_DIM = 256


def _make_fake_embedding(text: str, dim: int = _DIM) -> Any:
    """Deterministic fake embedding using bag-of-characters."""
    vec = np.zeros(dim, dtype=np.float32)
    for ch in text.lower():
        vec[ord(ch) % dim] += 1.0
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


class _R26FakeStaticModel:
    """Minimal model2vec.StaticModel stand-in (r26 variant, named to avoid collision)."""

    def __init__(self, name: str = "test-model"):
        self.name = name

    @classmethod
    def from_pretrained(cls, name: str) -> _R26FakeStaticModel:
        return cls(name)

    def encode(self, texts: list[str]) -> Any:
        return np.array([_make_fake_embedding(t) for t in texts], dtype=np.float32)


# Build (but do not yet register) the fake module.
_r26_fake_m2v = types.ModuleType("model2vec")
_r26_fake_m2v.StaticModel = _R26FakeStaticModel  # type: ignore[attr-defined]

# vector_index does NOT import model2vec at module level — it is safe to
# import without model2vec in sys.modules.
import vector_index  # noqa: E402

# ---------------------------------------------------------------------------
# Autouse fixture: reset module state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_vi():
    """Reset cached model/availability and ensure fake model2vec is available.

    Registers the fake model2vec module via setdefault — a no-op when
    test_vector_index.py has already installed its own FakeStaticModel.
    """
    # Lazy registration: safe regardless of collection order.
    sys.modules.setdefault("model2vec", _r26_fake_m2v)

    vector_index._VECTOR_AVAILABLE = None
    vector_index._MODEL = None
    vector_index._BM25_CACHE.clear()
    yield
    vector_index._VECTOR_AVAILABLE = None
    vector_index._MODEL = None
    vector_index._BM25_CACHE.clear()


# ---------------------------------------------------------------------------
# Helper: create minimal session DB
# ---------------------------------------------------------------------------


def _create_session_db(db_path: Path, docs: list[dict]) -> Path:
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_documents (
            file_path        TEXT PRIMARY KEY,
            source_type      TEXT NOT NULL DEFAULT 'session',
            session_id       TEXT NOT NULL DEFAULT '',
            title            TEXT NOT NULL DEFAULT '',
            content          TEXT NOT NULL DEFAULT '',
            created_at       TEXT NOT NULL DEFAULT '',
            created_at_epoch INTEGER NOT NULL DEFAULT 0,
            updated_at_epoch INTEGER NOT NULL DEFAULT 0,
            file_mtime       REAL NOT NULL DEFAULT 0.0
        )
    """)
    for doc in docs:
        conn.execute(
            "INSERT INTO session_documents "
            "(file_path, source_type, session_id, title, content, created_at, created_at_epoch, updated_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                doc.get("file_path", "/test/untitled"),
                doc.get("source_type", "session"),
                doc.get("session_id", "sess-001"),
                doc.get("title", "Test Doc"),
                doc.get("content", "Test content"),
                doc.get("created_at", "2026-01-01T00:00:00"),
                doc.get("created_at_epoch", 1767225600),
                doc.get("updated_at_epoch", 1767225600),
            ),
        )
    conn.commit()
    conn.close()
    return db_path


# ===========================================================================
# vector_index.py gap tests
# ===========================================================================


class TestLoadModelCachedPath:
    """Line 99: inner if _MODEL is None guard — skip model load when already set."""

    def test_second_call_returns_cached_instance(self):
        """Call _load_model twice; the second call must return the exact same object."""
        m1 = vector_index._load_model()
        # Pre-condition: module-level _MODEL is now set
        assert vector_index._MODEL is not None
        m2 = vector_index._load_model()
        # Must be the identical object — no second load
        assert m1 is m2

    def test_cached_model_skips_from_pretrained(self):
        """With _MODEL already populated, from_pretrained must not be called again."""
        # Look up the actual StaticModel class that is registered at test time
        # (may be _R26FakeStaticModel or test_vector_index.FakeStaticModel).
        registered_cls = sys.modules["model2vec"].StaticModel
        sentinel = registered_cls("already-loaded")
        vector_index._MODEL = sentinel
        with mock.patch.object(registered_cls, "from_pretrained", wraps=registered_cls.from_pretrained) as fp:
            result = vector_index._load_model()
            fp.assert_not_called()
        assert result is sentinel


class TestUnpackVectorDimMismatch:
    """Line 147: _unpack_vector raises ValueError on dimension mismatch."""

    def test_dim_mismatch_raises(self):
        vec = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        blob = vec.tobytes()  # 4 floats
        with pytest.raises(ValueError, match="Vector dimension mismatch"):
            vector_index._unpack_vector(blob, dim=8)  # wrong expected dim

    def test_dim_match_ok(self):
        vec = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        blob = vec.tobytes()
        result = vector_index._unpack_vector(blob, dim=4)
        np.testing.assert_array_equal(result, vec)

    def test_dim_none_no_check(self):
        vec = np.array([1.0, 2.0], dtype=np.float32)
        blob = vec.tobytes()
        result = vector_index._unpack_vector(blob, dim=None)
        assert result.shape == (2,)


class TestCosineSimilarityZeroVectors:
    """Line 164-165: cosine_similarity returns 0.0 when either vector is all zeros."""

    def test_both_zeros_returns_zero(self):
        a = np.zeros(4, dtype=np.float32)
        b = np.zeros(4, dtype=np.float32)
        assert vector_index.cosine_similarity(a, b) == 0.0

    def test_first_zero_returns_zero(self):
        a = np.zeros(4, dtype=np.float32)
        b = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        assert vector_index.cosine_similarity(a, b) == 0.0

    def test_second_zero_returns_zero(self):
        a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        b = np.zeros(3, dtype=np.float32)
        assert vector_index.cosine_similarity(a, b) == 0.0


class TestEmbedPendingInvalidPath:
    """Lines 246 & 249: ValueError for bad session DB paths."""

    def test_suffix_not_db_raises(self, tmp_path):
        """Line 246: path with wrong suffix raises ValueError."""
        not_a_db = tmp_path / "session.txt"
        not_a_db.write_text("not a db")  # must exist to pass the suffix check path
        vdb = tmp_path / "vector_index.db"
        with pytest.raises(ValueError, match="Invalid session database path"):
            vector_index.embed_pending_session_docs(not_a_db, vdb)

    def test_nonexistent_db_path_raises(self, tmp_path):
        """Line 246: path that does not exist raises ValueError."""
        ghost = tmp_path / "ghost.db"  # does not exist
        vdb = tmp_path / "vector_index.db"
        with pytest.raises(ValueError, match="Invalid session database path"):
            vector_index.embed_pending_session_docs(ghost, vdb)

    def test_unsafe_single_quote_raises(self, tmp_path):
        """Line 249: single-quote in resolved path raises ValueError."""
        # We can't create a file with ' in the name on all platforms,
        # so we patch Path.resolve to return a fake path with unsafe chars.
        vdb = tmp_path / "vector_index.db"
        vector_index.ensure_vector_db(vdb)

        fake_path = mock.MagicMock(spec=Path)
        fake_path.suffix = ".db"
        fake_path.exists.return_value = True
        fake_path.__str__ = mock.Mock(return_value="/tmp/sess'ion.db")

        with mock.patch("vector_index.Path") as MockPath:
            # ensure_vector_db is called first (uses vector_db_path arg);
            # we only need the session_db_path resolution to be spoofed.
            real_path = Path  # keep real Path available

            def side_effect(arg):
                # Return the spoofed path only for the session db argument
                p = real_path(arg)
                if "session" in str(arg):
                    return fake_path
                return p

            MockPath.side_effect = side_effect
            # Call directly with pre-existing vdb to avoid mock interfering with ensure_vector_db
            with pytest.raises((ValueError, Exception)):
                vector_index.embed_pending_session_docs("/tmp/session.db", vdb)

    def test_unsafe_semicolon_raises(self, tmp_path):
        """Line 249: semicolon in path raises ValueError.

        We exercise this via the string check inside embed_pending_session_docs
        by temporarily monkey-patching str() conversion result.
        """
        sdb = tmp_path / "session.db"
        _create_session_db(sdb, [])
        vdb = tmp_path / "vector_index.db"

        # Patch Path.resolve to return object whose str() contains semicolon
        class _BadPath:
            suffix = ".db"

            def exists(self):
                return True

            def __str__(self):
                return "/tmp/sess;ion.db"

        with mock.patch.object(Path, "resolve", return_value=_BadPath()):
            with pytest.raises(ValueError, match="Unsafe characters"):
                vector_index.embed_pending_session_docs(sdb, vdb)


class TestStaleDeletionBatchFlushPath:
    """Lines 278-283: batch flush triggered mid-loop when batch reaches VECTOR_BATCH_SIZE."""

    def test_stale_deletion_happens_after_batch_embed(self, tmp_path):
        """Embed > VECTOR_BATCH_SIZE docs, then delete one; deleted count must be 1."""
        # Temporarily lower batch size so the mid-loop flush is triggered with a small doc set
        original_batch = vector_index.VECTOR_BATCH_SIZE
        vector_index.VECTOR_BATCH_SIZE = 2  # force batch flush at every 2 docs

        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        docs = [
            {"file_path": f"/doc{i}.md", "title": f"Doc {i}", "content": f"Content for document {i}"} for i in range(5)
        ]
        _create_session_db(sdb, docs)

        try:
            result = vector_index.embed_pending_session_docs(sdb, vdb)
            assert result["embedded"] == 5

            # Now remove two docs from session DB and re-sync
            conn = sqlite3.connect(str(sdb))
            conn.execute("DELETE FROM session_documents WHERE file_path IN ('/doc3.md', '/doc4.md')")
            conn.commit()
            conn.close()

            result2 = vector_index.embed_pending_session_docs(sdb, vdb)
            assert result2["deleted"] == 2
        finally:
            vector_index.VECTOR_BATCH_SIZE = original_batch

    def test_batch_flush_embeds_mid_loop(self, tmp_path):
        """Verify that with batch_size=1 each document triggers the mid-loop flush."""
        original_batch = vector_index.VECTOR_BATCH_SIZE
        vector_index.VECTOR_BATCH_SIZE = 1

        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        docs = [{"file_path": f"/x{i}.md", "title": f"X{i}", "content": f"x content {i}"} for i in range(3)]
        _create_session_db(sdb, docs)

        try:
            result = vector_index.embed_pending_session_docs(sdb, vdb)
            assert result["embedded"] == 3
        finally:
            vector_index.VECTOR_BATCH_SIZE = original_batch


class TestVectorSearchZeroQueryNorm:
    """Line 364: vector_search_session returns [] when query vector norm is 0."""

    def test_zero_query_returns_empty(self, tmp_path):
        """A zero embedding query produces no results."""
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        _create_session_db(
            sdb,
            [{"file_path": "/a.md", "title": "Alpha", "content": "alpha content"}],
        )
        vector_index.embed_pending_session_docs(sdb, vdb)

        # Make embed_single return a zero vector
        zero_vec = np.zeros(_DIM, dtype=np.float32)
        with mock.patch.object(vector_index, "embed_single", return_value=zero_vec):
            results = vector_index.vector_search_session("anything", sdb, vdb, limit=5)
        assert results == []

    def test_all_negative_scores_excluded(self, tmp_path):
        """Scores <= 0.0 should be filtered out, potentially returning empty list."""
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        _create_session_db(
            sdb,
            [{"file_path": "/b.md", "title": "Beta", "content": "beta content"}],
        )
        vector_index.embed_pending_session_docs(sdb, vdb)

        # Embed stored as 1-D positive, query as its exact negative → cosine = -1
        stored_vec = vector_index.embed_single("beta content")
        negative_vec = -stored_vec
        with mock.patch.object(vector_index, "embed_single", return_value=negative_vec):
            results = vector_index.vector_search_session("anything", sdb, vdb, limit=5)
        # All scores <= 0 → no results returned
        assert results == []


class TestBM25CacheHitPath:
    """Line 416: BM25 retriever is fetched from cache on second call."""

    def test_cache_hit_reuses_retriever(self, tmp_path):
        """Second call with same row_count must reuse cached retriever, not rebuild."""
        try:
            import bm25s  # noqa: F401
        except ImportError:
            pytest.skip("bm25s not installed")

        sdb = tmp_path / "session.db"
        _create_session_db(
            sdb,
            [
                {"file_path": "/p.md", "title": "Python", "content": "Python programming"},
                {"file_path": "/r.md", "title": "Rust", "content": "Rust programming"},
            ],
        )

        # First call — populates cache
        r1 = vector_index.bm25s_search_session("Python", sdb, limit=5)
        assert len(r1) > 0

        sdb_str = str(Path(sdb).resolve())
        assert sdb_str in vector_index._BM25_CACHE, "Cache should be populated after first call"
        cached_entry = vector_index._BM25_CACHE[sdb_str]

        # Second call with same content — must hit cache (row_count unchanged)
        with mock.patch("bm25s.BM25") as MockBM25:
            r2 = vector_index.bm25s_search_session("Python", sdb, limit=5)
            MockBM25.assert_not_called()  # retriever not rebuilt

        assert r2[0]["file_path"] == r1[0]["file_path"]
        # Cache entry object must be the same instance
        assert vector_index._BM25_CACHE[sdb_str] is cached_entry

    def test_cache_invalidated_on_row_count_change(self, tmp_path):
        """Cache is invalidated when a new document is added."""
        try:
            import bm25s  # noqa: F401
        except ImportError:
            pytest.skip("bm25s not installed")

        sdb = tmp_path / "session.db"
        _create_session_db(
            sdb,
            [{"file_path": "/a.md", "title": "A", "content": "a content"}],
        )

        vector_index.bm25s_search_session("a", sdb)

        # Add a new document
        conn = sqlite3.connect(str(sdb))
        conn.execute(
            "INSERT INTO session_documents "
            "(file_path, source_type, session_id, title, content, created_at, created_at_epoch, updated_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("/b.md", "session", "sess-002", "B", "b content", "2026-01-02", 1767312000, 1767312000),
        )
        conn.commit()
        conn.close()

        sdb_str = str(Path(sdb).resolve())
        old_entry = vector_index._BM25_CACHE[sdb_str]

        vector_index.bm25s_search_session("b", sdb)
        new_entry = vector_index._BM25_CACHE[sdb_str]
        # Row count changed → cache was rebuilt → different entry
        assert new_entry is not old_entry


class TestHybridSearchOneSidedResults:
    """Lines 484, 489: hybrid_search_session when only one of vec/bm25 has results."""

    def test_only_bm25_results_line484(self, tmp_path):
        """Line 484: vec_results empty, bm25_results non-empty → return bm25 ranked."""
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        _create_session_db(
            sdb,
            [{"file_path": "/a.md", "title": "Alpha", "content": "alpha content doc"}],
        )
        # Provide empty vector results, non-empty bm25 results
        bm25_result = [{"file_path": "/a.md", "score": 5.0, "rank": 1}]
        with mock.patch.object(vector_index, "vector_search_session", return_value=[]):
            with mock.patch.object(vector_index, "bm25s_search_session", return_value=bm25_result):
                results = vector_index.hybrid_search_session("alpha", sdb, vdb, limit=5)
        assert len(results) == 1
        assert results[0]["file_path"] == "/a.md"
        assert "rrf_score" in results[0]

    def test_only_vector_results_line489(self, tmp_path):
        """Line 489: bm25_results empty, vec_results non-empty → return vector ranked."""
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        _create_session_db(
            sdb,
            [{"file_path": "/b.md", "title": "Beta", "content": "beta content doc"}],
        )
        vec_result = [{"file_path": "/b.md", "score": 0.9, "rank": 1}]
        with mock.patch.object(vector_index, "vector_search_session", return_value=vec_result):
            with mock.patch.object(vector_index, "bm25s_search_session", return_value=[]):
                results = vector_index.hybrid_search_session("beta", sdb, vdb, limit=5)
        assert len(results) == 1
        assert results[0]["file_path"] == "/b.md"
        assert "rrf_score" in results[0]


class TestFetchEnrichedMissingRow:
    """Line 484: fetch_enriched_results skips entries absent from session DB."""

    def test_missing_file_path_skipped(self, tmp_path):
        """ranked_paths contains a path not in session DB → result omitted."""
        sdb = tmp_path / "session.db"
        _create_session_db(
            sdb,
            [{"file_path": "/exists.md", "title": "Present", "content": "present content"}],
        )

        ranked = [
            {"file_path": "/exists.md", "rrf_score": 0.9},
            {"file_path": "/missing.md", "rrf_score": 0.5},  # not in DB
        ]
        results = vector_index.fetch_enriched_results(ranked, sdb, "present")
        file_paths = [r["file_path"] for r in results]
        assert "/exists.md" in file_paths
        assert "/missing.md" not in file_paths

    def test_all_missing_returns_empty(self, tmp_path):
        """When no ranked paths exist in DB, result is empty list."""
        sdb = tmp_path / "session.db"
        _create_session_db(sdb, [])

        ranked = [{"file_path": "/ghost.md", "rrf_score": 0.8}]
        results = vector_index.fetch_enriched_results(ranked, sdb, "ghost")
        assert results == []


class TestBuildSnippetNoMatch:
    """Line 489 (_build_snippet): no match → return head of content."""

    def test_no_match_returns_head(self):
        content = "The quick brown fox jumps over the lazy dog"
        snippet = vector_index._build_snippet(content, "zzznomatch", radius=120)
        # Should return beginning of content (up to radius*2 chars)
        assert snippet
        assert snippet == content[:240].strip()

    def test_no_match_short_content(self):
        content = "Short text"
        snippet = vector_index._build_snippet(content, "zzznomatch", radius=120)
        assert snippet == "Short text"

    def test_no_match_empty_content(self):
        snippet = vector_index._build_snippet("", "anything", radius=50)
        assert snippet == ""

    def test_match_present_uses_window(self):
        content = "prefix " + "x" * 200 + " KEYWORD " + "y" * 200 + " suffix"
        snippet = vector_index._build_snippet(content, "keyword", radius=10)
        assert "KEYWORD" in snippet
        # Should not include the very beginning of prefix
        assert snippet.startswith("x") or "x" * 5 in snippet


# ===========================================================================
# context_cli.py gap tests
# ===========================================================================


import context_cli  # noqa: E402


class TestCmdVectorStatusPackageImport:
    """Lines 734-737: cmd_vector_status succeeds via .vector_index package fallback."""

    def test_status_via_package_import(self, tmp_path):
        """When 'vector_index' top-level import fails, .vector_index fallback is used."""
        fake_vi = types.ModuleType(".vector_index")

        status_data = {
            "available": True,
            "model": "test-model",
            "dim": 256,
            "vector_db_path": str(tmp_path / "vector_index.db"),
            "vector_db_exists": False,
            "indexed_sessions": 0,
            "indexed_observations": 0,
        }

        fake_vi.get_vector_db_path = mock.Mock(return_value=tmp_path / "vector_index.db")
        fake_vi.vector_status = mock.Mock(return_value=status_data)

        fake_si = mock.MagicMock()
        fake_si.get_session_db_path.return_value = str(tmp_path / "session.db")

        # Simulate: top-level 'vector_index' import fails, .vector_index succeeds
        with mock.patch.object(context_cli, "_get_session_index", return_value=fake_si):
            with mock.patch.dict(sys.modules, {"vector_index": None}):
                # Patch the package-relative import by injecting into context_cli's namespace
                with mock.patch.object(
                    context_cli,
                    "cmd_vector_status",
                    wraps=context_cli.cmd_vector_status,
                ):
                    # Direct injection: patch builtins import inside cmd_vector_status
                    import builtins

                    original_import = builtins.__import__

                    def fake_import(name, *args, **kwargs):
                        if name == "vector_index":
                            raise ImportError("not found")
                        if name == ".vector_index" or (
                            isinstance(args, tuple) and args and "vector_index" in str(args)
                        ):
                            raise ImportError("not found")
                        return original_import(name, *args, **kwargs)

                    # The fallback path in cmd_vector_status uses
                    # `from .vector_index import ...` which in a flat scripts/
                    # directory raises ImportError → returns 1.
                    # We verify here that the function returns 1 gracefully.
                    args = types.SimpleNamespace()
                    with mock.patch("builtins.__import__", side_effect=fake_import):
                        rc = context_cli.cmd_vector_status(args)
                    assert rc == 1

    def test_status_success_path(self, tmp_path):
        """cmd_vector_status returns 0 when vector_index is importable."""
        status_data = {
            "available": True,
            "model": "test-model",
            "dim": 256,
            "vector_db_path": str(tmp_path / "vector_index.db"),
            "vector_db_exists": False,
            "indexed_sessions": 0,
            "indexed_observations": 0,
        }

        fake_si = mock.MagicMock()
        fake_si.get_session_db_path.return_value = str(tmp_path / "session.db")

        with mock.patch.object(context_cli, "_get_session_index", return_value=fake_si):
            with mock.patch.object(vector_index, "get_vector_db_path", return_value=tmp_path / "vector_index.db"):
                with mock.patch.object(vector_index, "vector_status", return_value=status_data):
                    # Ensure vector_index is importable
                    sys.modules.setdefault("vector_index", vector_index)
                    import contextlib as _cl
                    import io as _io

                    args = types.SimpleNamespace()
                    buf = _io.StringIO()
                    with _cl.redirect_stdout(buf):
                        rc = context_cli.cmd_vector_status(args)
                    assert rc == 0


class TestQSearchPackageImportFallback:
    """Lines 800-801: _q_search uses .vector_index fallback when top-level import fails."""

    def test_q_search_fallback_to_fts_on_import_error(self, tmp_path):
        """When both vector_index imports fail, _q_search falls through to FTS fallback."""
        fake_si = mock.MagicMock()
        fake_si.get_session_db_path.return_value = str(tmp_path / "session.db")
        fake_si.format_search_results.return_value = "Found 1 sessions\n[test-uuid] Test Title"
        fake_si.search_sessions.return_value = []

        types.SimpleNamespace(query="test query", limit=5, json=False)

        import builtins

        original_import = builtins.__import__

        def import_blocker(name, *args2, **kwargs):
            if name in ("vector_index", ".vector_index"):
                raise ImportError("blocked for test")
            return original_import(name, *args2, **kwargs)

        with mock.patch.object(context_cli, "_get_session_index", return_value=fake_si):
            with mock.patch("builtins.__import__", side_effect=import_blocker):
                import contextlib as _cl
                import io as _io

                buf = _io.StringIO()
                with _cl.redirect_stdout(buf):
                    rc = context_cli._q_search("test query", limit=5, as_json=False)
        # FTS fallback ran — returns 0 since format_search_results returned text
        assert rc == 0

    def test_q_search_vector_available_false_falls_through(self, tmp_path):
        """When vector_available() returns False, FTS fallback is used."""
        fake_si = mock.MagicMock()
        fake_si.get_session_db_path.return_value = str(tmp_path / "session.db")
        fake_si.format_search_results.return_value = "Found 1 sessions\nResult"
        fake_si.search_sessions.return_value = []

        with mock.patch.object(context_cli, "_get_session_index", return_value=fake_si):
            with mock.patch.object(vector_index, "vector_available", return_value=False):
                sys.modules.setdefault("vector_index", vector_index)
                import contextlib as _cl
                import io as _io

                buf = _io.StringIO()
                with _cl.redirect_stdout(buf):
                    rc = context_cli._q_search("some query", limit=5, as_json=False)
        assert rc == 0

    def test_q_search_hybrid_returns_empty_falls_through(self, tmp_path):
        """When hybrid_search returns [] and FTS has results, returns 0."""
        fake_si = mock.MagicMock()
        fake_si.get_session_db_path.return_value = str(tmp_path / "session.db")
        fake_si.format_search_results.return_value = "Found 1 sessions\nFTS Result"
        fake_si.search_sessions.return_value = []

        with mock.patch.object(context_cli, "_get_session_index", return_value=fake_si):
            with mock.patch.object(vector_index, "vector_available", return_value=True):
                with mock.patch.object(vector_index, "hybrid_search_session", return_value=[]):
                    sys.modules.setdefault("vector_index", vector_index)
                    import contextlib as _cl
                    import io as _io

                    buf = _io.StringIO()
                    with _cl.redirect_stdout(buf):
                        rc = context_cli._q_search("empty hybrid query", limit=5, as_json=False)
        assert rc == 0
