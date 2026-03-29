#!/usr/bin/env python3
"""Tests for vector_index.py — model2vec + bm25s hybrid semantic search.

All tests use mocked models (no real 30MB model download in CI).
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
import types
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

# Ensure scripts/ is on sys.path (same pattern as other test files)
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Skip entire module when numpy is not installed (CI without [vector] extra)
np = pytest.importorskip("numpy", reason="numpy not installed — vector tests require numpy")

# ---------------------------------------------------------------------------
# Helpers: mock model2vec + numpy
# ---------------------------------------------------------------------------

_DIM = 256


def _fake_numpy():
    """Return real numpy module."""
    return np


def _make_fake_embedding(text: str, dim: int = _DIM) -> Any:
    """Deterministic fake embedding based on text content.

    Uses a simple bag-of-characters approach so texts sharing common substrings
    produce similar (positive cosine) embeddings — essential for search tests.
    """
    np = _fake_numpy()
    vec = np.zeros(dim, dtype=np.float32)
    for ch in text.lower():
        vec[ord(ch) % dim] += 1.0
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


class FakeStaticModel:
    """Mock model2vec.StaticModel for testing."""

    def __init__(self, name: str = "test-model"):
        self.name = name

    @classmethod
    def from_pretrained(cls, name: str) -> FakeStaticModel:
        return cls(name)

    def encode(self, texts: list[str]) -> Any:
        np = _fake_numpy()
        return np.array([_make_fake_embedding(t) for t in texts], dtype=np.float32)


# Install the fake model2vec module once at import time (NOT in a fixture that
# would use mock.patch.dict and remove numpy sub-modules on teardown).
_FAKE_MODEL2VEC = types.ModuleType("model2vec")
_FAKE_MODEL2VEC.StaticModel = FakeStaticModel  # type: ignore[attr-defined]
sys.modules.setdefault("model2vec", _FAKE_MODEL2VEC)

import vector_index  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_vector_module():
    """Reset vector_index module state between tests."""
    vector_index._VECTOR_AVAILABLE = None
    vector_index._MODEL = None
    yield
    vector_index._VECTOR_AVAILABLE = None
    vector_index._MODEL = None


@pytest.fixture
def vi():
    """Return vector_index module with reset state."""
    vector_index._VECTOR_AVAILABLE = None
    vector_index._MODEL = None
    return vector_index


# ---------------------------------------------------------------------------
# Helper: create session DB with test documents
# ---------------------------------------------------------------------------


def _create_session_db(db_path: Path, docs: list[dict[str, Any]]) -> Path:
    """Create a minimal session_documents table for testing."""
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
                doc.get("file_path", f"/test/{doc.get('title', 'untitled')}"),
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


# ---------------------------------------------------------------------------
# Test: vector_available
# ---------------------------------------------------------------------------


class TestVectorAvailable:
    def test_available_when_deps_present(self, vi):
        assert vi.vector_available() is True

    def test_cached_result(self, vi):
        result1 = vi.vector_available()
        result2 = vi.vector_available()
        assert result1 == result2

    def test_not_available_when_no_model2vec(self, vi):
        vi._VECTOR_AVAILABLE = None  # force re-probe
        # Temporarily remove model2vec from sys.modules and install a
        # sentinel that raises ImportError on import.
        old = sys.modules.get("model2vec")
        sys.modules["model2vec"] = None  # type: ignore[assignment]
        try:
            assert vi.vector_available() is False
        finally:
            if old is not None:
                sys.modules["model2vec"] = old
            # Reset so next test picks up the real fake module
            vi._VECTOR_AVAILABLE = None


# ---------------------------------------------------------------------------
# Test: model loading
# ---------------------------------------------------------------------------


class TestModelLoading:
    def test_load_model_returns_fake(self, vi):
        model = vi._load_model()
        assert isinstance(model, FakeStaticModel)

    def test_load_model_cached(self, vi):
        m1 = vi._load_model()
        m2 = vi._load_model()
        assert m1 is m2


# ---------------------------------------------------------------------------
# Test: embedding helpers
# ---------------------------------------------------------------------------


class TestEmbedding:
    def test_embed_texts_empty(self, vi):

        result = vi.embed_texts([])
        assert result.shape == (0, _DIM)
        assert result.dtype == np.float32

    def test_embed_texts_single(self, vi):

        result = vi.embed_texts(["hello world"])
        assert result.shape == (1, _DIM)
        assert result.dtype == np.float32

    def test_embed_texts_batch(self, vi):
        texts = ["hello", "world", "test"]
        result = vi.embed_texts(texts)
        assert result.shape == (3, _DIM)

    def test_embed_single(self, vi):
        result = vi.embed_single("hello world")
        assert result.shape == (_DIM,)

    def test_embed_deterministic(self, vi):

        r1 = vi.embed_single("same text")
        r2 = vi.embed_single("same text")
        np.testing.assert_array_equal(r1, r2)

    def test_embed_different_texts_differ(self, vi):

        r1 = vi.embed_single("hello")
        r2 = vi.embed_single("completely different text")
        assert not np.array_equal(r1, r2)


# ---------------------------------------------------------------------------
# Test: vector pack/unpack
# ---------------------------------------------------------------------------


class TestPackUnpack:
    def test_roundtrip(self, vi):

        vec = np.random.randn(_DIM).astype(np.float32)
        packed = vi._pack_vector(vec)
        assert isinstance(packed, bytes)
        unpacked = vi._unpack_vector(packed)
        np.testing.assert_array_almost_equal(vec, unpacked)

    def test_packed_size(self, vi):

        vec = np.zeros(_DIM, dtype=np.float32)
        packed = vi._pack_vector(vec)
        assert len(packed) == _DIM * 4  # float32 = 4 bytes


# ---------------------------------------------------------------------------
# Test: cosine similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self, vi):

        v = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        assert vi.cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self, vi):

        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 1.0], dtype=np.float32)
        assert vi.cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self, vi):

        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([-1.0, 0.0], dtype=np.float32)
        assert vi.cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector(self, vi):

        a = np.zeros(3, dtype=np.float32)
        b = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        assert vi.cosine_similarity(a, b) == 0.0


# ---------------------------------------------------------------------------
# Test: vector DB management
# ---------------------------------------------------------------------------


class TestVectorDB:
    def test_get_vector_db_path(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        vdb = vi.get_vector_db_path(sdb)
        assert vdb == tmp_path / "vector_index.db"

    def test_get_vector_db_path_custom(self, vi, tmp_path):
        custom = tmp_path / "custom_vector.db"
        with mock.patch.dict("os.environ", {"CONTEXTGO_VECTOR_DB_PATH": str(custom)}):
            result = vi.get_vector_db_path(tmp_path / "session.db")
            assert result == custom

    def test_ensure_vector_db_creates_file(self, vi, tmp_path):
        vdb = tmp_path / "subdir" / "vector_index.db"
        result = vi.ensure_vector_db(vdb)
        assert result.exists()
        # Verify tables exist
        conn = sqlite3.connect(str(vdb))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "session_vectors" in tables
        assert "observation_vectors" in tables

    def test_ensure_vector_db_idempotent(self, vi, tmp_path):
        vdb = tmp_path / "vector_index.db"
        vi.ensure_vector_db(vdb)
        vi.ensure_vector_db(vdb)  # should not raise
        assert vdb.exists()


# ---------------------------------------------------------------------------
# Test: embed_pending_session_docs
# ---------------------------------------------------------------------------


class TestEmbedPending:
    def test_embed_new_docs(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        _create_session_db(
            sdb,
            [
                {"file_path": "/a.md", "title": "Alpha", "content": "Alpha content"},
                {"file_path": "/b.md", "title": "Beta", "content": "Beta content"},
            ],
        )

        result = vi.embed_pending_session_docs(sdb, vdb)
        assert result["embedded"] == 2
        assert result["deleted"] == 0

    def test_skip_already_embedded(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        _create_session_db(
            sdb,
            [
                {"file_path": "/a.md", "title": "Alpha", "content": "Alpha content"},
            ],
        )

        # First embed
        r1 = vi.embed_pending_session_docs(sdb, vdb)
        assert r1["embedded"] == 1

        # Second embed — should skip
        r2 = vi.embed_pending_session_docs(sdb, vdb)
        assert r2["embedded"] == 0
        assert r2["skipped"] == 1

    def test_force_reembed(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        _create_session_db(
            sdb,
            [
                {"file_path": "/a.md", "title": "Alpha", "content": "Alpha content"},
            ],
        )

        vi.embed_pending_session_docs(sdb, vdb)
        r2 = vi.embed_pending_session_docs(sdb, vdb, force=True)
        assert r2["embedded"] == 1

    def test_delete_stale_vectors(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        _create_session_db(
            sdb,
            [
                {"file_path": "/a.md", "title": "Alpha", "content": "Alpha content"},
                {"file_path": "/b.md", "title": "Beta", "content": "Beta content"},
            ],
        )
        vi.embed_pending_session_docs(sdb, vdb)

        # Remove one doc from session DB
        conn = sqlite3.connect(str(sdb))
        conn.execute("DELETE FROM session_documents WHERE file_path = '/b.md'")
        conn.commit()
        conn.close()

        r2 = vi.embed_pending_session_docs(sdb, vdb)
        assert r2["deleted"] == 1

    def test_empty_session_db(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        _create_session_db(sdb, [])

        result = vi.embed_pending_session_docs(sdb, vdb)
        assert result["embedded"] == 0


# ---------------------------------------------------------------------------
# Test: vector_search_session
# ---------------------------------------------------------------------------


class TestVectorSearch:
    def test_basic_search(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        _create_session_db(
            sdb,
            [
                {"file_path": "/python.md", "title": "Python Guide", "content": "Python programming language tutorial"},
                {"file_path": "/rust.md", "title": "Rust Guide", "content": "Rust systems programming language"},
                {"file_path": "/cooking.md", "title": "Cooking Recipes", "content": "How to make pasta and pizza"},
            ],
        )
        vi.embed_pending_session_docs(sdb, vdb)

        results = vi.vector_search_session("Python", sdb, vdb, limit=3)
        assert len(results) > 0
        assert all("file_path" in r and "score" in r and "rank" in r for r in results)

    def test_search_empty_db(self, vi, tmp_path):
        vdb = tmp_path / "vector_index.db"
        sdb = tmp_path / "session.db"
        results = vi.vector_search_session("test", sdb, vdb)
        assert results == []

    def test_search_nonexistent_db(self, vi, tmp_path):
        vdb = tmp_path / "nonexistent.db"
        sdb = tmp_path / "session.db"
        results = vi.vector_search_session("test", sdb, vdb)
        assert results == []

    def test_search_returns_ranked(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        docs = [{"file_path": f"/doc{i}.md", "title": f"Doc {i}", "content": f"Content {i}"} for i in range(5)]
        _create_session_db(sdb, docs)
        vi.embed_pending_session_docs(sdb, vdb)

        results = vi.vector_search_session("Doc 0", sdb, vdb, limit=3)
        # Results should be ranked (rank 1, 2, 3, ...)
        for i, r in enumerate(results):
            assert r["rank"] == i + 1


# ---------------------------------------------------------------------------
# Test: bm25s_search_session
# ---------------------------------------------------------------------------


class TestBM25Search:
    def test_basic_bm25(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        _create_session_db(
            sdb,
            [
                {"file_path": "/python.md", "title": "Python Guide", "content": "Python programming language tutorial"},
                {"file_path": "/rust.md", "title": "Rust Guide", "content": "Rust systems programming language"},
            ],
        )

        try:
            import bm25s  # noqa: F401
        except ImportError:
            pytest.skip("bm25s not installed")

        results = vi.bm25s_search_session("Python programming", sdb, limit=5)
        assert len(results) > 0
        # Python doc should rank higher
        assert results[0]["file_path"] == "/python.md"

    def test_bm25_empty_db(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        _create_session_db(sdb, [])
        results = vi.bm25s_search_session("test", sdb)
        assert results == []

    def test_bm25_nonexistent_db(self, vi, tmp_path):
        sdb = tmp_path / "nonexistent.db"
        results = vi.bm25s_search_session("test", sdb)
        assert results == []

    def test_bm25_no_bm25s_module(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        _create_session_db(
            sdb,
            [
                {"file_path": "/a.md", "title": "Test", "content": "Test content"},
            ],
        )
        with mock.patch.dict(sys.modules, {"bm25s": None}):
            results = vi.bm25s_search_session("test", sdb)
            assert results == []


# ---------------------------------------------------------------------------
# Test: RRF merge
# ---------------------------------------------------------------------------


class TestRRFMerge:
    def test_merge_basic(self, vi):
        vec = [
            {"file_path": "/a.md", "score": 0.9, "rank": 1},
            {"file_path": "/b.md", "score": 0.7, "rank": 2},
        ]
        bm25 = [
            {"file_path": "/b.md", "score": 5.0, "rank": 1},
            {"file_path": "/c.md", "score": 3.0, "rank": 2},
        ]
        merged = vi._rrf_merge(vec, bm25, k=60, limit=10)
        assert len(merged) == 3
        # b.md appears in both, should score highest
        assert merged[0]["file_path"] == "/b.md"

    def test_merge_empty_inputs(self, vi):
        assert vi._rrf_merge([], [], k=60, limit=10) == []

    def test_merge_one_empty(self, vi):
        vec = [{"file_path": "/a.md", "score": 0.9, "rank": 1}]
        merged = vi._rrf_merge(vec, [], k=60, limit=10)
        assert len(merged) == 1
        assert merged[0]["file_path"] == "/a.md"

    def test_merge_limit(self, vi):
        vec = [{"file_path": f"/{i}.md", "score": 1.0, "rank": i + 1} for i in range(20)]
        merged = vi._rrf_merge(vec, [], k=60, limit=5)
        assert len(merged) == 5

    def test_merge_rrf_scores_positive(self, vi):
        vec = [{"file_path": "/a.md", "score": 0.5, "rank": 1}]
        bm25 = [{"file_path": "/b.md", "score": 2.0, "rank": 1}]
        merged = vi._rrf_merge(vec, bm25, k=60, limit=10)
        for r in merged:
            assert r["rrf_score"] > 0


# ---------------------------------------------------------------------------
# Test: hybrid_search_session
# ---------------------------------------------------------------------------


class TestHybridSearch:
    def test_hybrid_returns_results(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        _create_session_db(
            sdb,
            [
                {"file_path": "/python.md", "title": "Python Guide", "content": "Python programming language tutorial"},
                {"file_path": "/rust.md", "title": "Rust Guide", "content": "Rust systems programming"},
            ],
        )
        vi.embed_pending_session_docs(sdb, vdb)

        results = vi.hybrid_search_session("Python", sdb, vdb, limit=5)
        assert len(results) > 0
        assert all("file_path" in r and "rrf_score" in r for r in results)

    def test_hybrid_empty(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        _create_session_db(sdb, [])
        vi.ensure_vector_db(vdb)
        results = vi.hybrid_search_session("test", sdb, vdb)
        assert results == []


# ---------------------------------------------------------------------------
# Test: fetch_enriched_results
# ---------------------------------------------------------------------------


class TestFetchEnriched:
    def test_enrich_basic(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        _create_session_db(
            sdb,
            [
                {"file_path": "/a.md", "title": "Alpha", "content": "Alpha content about testing"},
            ],
        )
        ranked = [{"file_path": "/a.md", "rrf_score": 0.5}]
        results = vi.fetch_enriched_results(ranked, sdb, "testing")
        assert len(results) == 1
        assert results[0]["file_path"] == "/a.md"
        assert results[0]["title"] == "Alpha"
        assert "snippet" in results[0]
        assert "source_type" in results[0]

    def test_enrich_empty(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        _create_session_db(sdb, [])
        assert vi.fetch_enriched_results([], sdb, "test") == []

    def test_enrich_missing_path(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        _create_session_db(sdb, [])
        ranked = [{"file_path": "/nonexistent.md", "rrf_score": 0.5}]
        results = vi.fetch_enriched_results(ranked, sdb, "test")
        assert results == []


# ---------------------------------------------------------------------------
# Test: _build_snippet
# ---------------------------------------------------------------------------


class TestBuildSnippet:
    def test_snippet_with_match(self, vi):
        content = "A" * 200 + "TARGET" + "B" * 200
        snippet = vi._build_snippet(content, "target", radius=50)
        assert "TARGET" in snippet
        assert len(snippet) <= 106 + 10  # radius*2 + len("TARGET") + slack

    def test_snippet_no_match(self, vi):
        content = "Some content without the search term"
        snippet = vi._build_snippet(content, "nonexistent", radius=120)
        assert snippet  # should return beginning of content

    def test_snippet_empty_content(self, vi):
        assert vi._build_snippet("", "test") == ""


# ---------------------------------------------------------------------------
# Test: vector_status
# ---------------------------------------------------------------------------


class TestVectorStatus:
    def test_status_no_db(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "nonexistent.db"
        status = vi.vector_status(sdb, vdb)
        assert status["available"] is True
        assert status["vector_db_exists"] is False
        assert status["indexed_sessions"] == 0

    def test_status_with_data(self, vi, tmp_path):
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"
        _create_session_db(
            sdb,
            [
                {"file_path": "/a.md", "title": "Alpha", "content": "Content A"},
                {"file_path": "/b.md", "title": "Beta", "content": "Content B"},
            ],
        )
        vi.embed_pending_session_docs(sdb, vdb)

        status = vi.vector_status(sdb, vdb)
        assert status["available"] is True
        assert status["vector_db_exists"] is True
        assert status["indexed_sessions"] == 2
        assert status["model"] == vi.VECTOR_MODEL_NAME
        assert status["dim"] == vi.VECTOR_DIM


# ---------------------------------------------------------------------------
# Test: CLI commands (cmd_vector_sync, cmd_vector_status)
# ---------------------------------------------------------------------------


class TestCLIVectorCommands:
    def test_cmd_vector_sync_no_deps(self, tmp_path):
        """vector-sync returns 1 when vector deps missing."""
        with mock.patch.dict(sys.modules, {"vector_index": None}):
            if "context_cli" in sys.modules:
                importlib.reload(sys.modules["context_cli"])
            try:
                import context_cli
            except ImportError:
                pytest.skip("context_cli not directly importable")

            args = types.SimpleNamespace(force=False)
            # The ImportError inside cmd_vector_sync should be caught
            rc = context_cli.cmd_vector_sync(args)
            assert rc == 1

    def test_cmd_vector_status_no_deps(self, tmp_path):
        """vector-status returns 1 when vector deps missing."""
        with mock.patch.dict(sys.modules, {"vector_index": None}):
            try:
                import context_cli
            except ImportError:
                pytest.skip("context_cli not directly importable")

            args = types.SimpleNamespace()
            rc = context_cli.cmd_vector_status(args)
            assert rc == 1


# ---------------------------------------------------------------------------
# Test: integration — full pipeline
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_full_pipeline(self, vi, tmp_path):
        """End-to-end: create docs, embed, search, enrich."""
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"

        docs = [
            {
                "file_path": "/proj/auth.py",
                "title": "Authentication Module",
                "content": "OAuth2 token validation and JWT parsing for user authentication",
            },
            {
                "file_path": "/proj/db.py",
                "title": "Database Layer",
                "content": "SQLite connection pool and query builder for data persistence",
            },
            {
                "file_path": "/proj/api.py",
                "title": "REST API",
                "content": "FastAPI routes for user management and session handling",
            },
            {
                "file_path": "/proj/cache.py",
                "title": "Cache Service",
                "content": "Redis-backed LRU cache for frequently accessed data",
            },
            {
                "file_path": "/proj/logs.py",
                "title": "Logging Setup",
                "content": "Structured logging with JSON formatter and rotation",
            },
        ]
        _create_session_db(sdb, docs)

        # Embed
        embed_result = vi.embed_pending_session_docs(sdb, vdb)
        assert embed_result["embedded"] == 5

        # Vector search
        vec_results = vi.vector_search_session("authentication", sdb, vdb, limit=3)
        assert len(vec_results) > 0

        # Hybrid search
        hybrid_results = vi.hybrid_search_session("database query", sdb, vdb, limit=3)
        assert len(hybrid_results) > 0

        # Enrich
        enriched = vi.fetch_enriched_results(hybrid_results, sdb, "database")
        assert len(enriched) > 0
        assert all(k in enriched[0] for k in ["source_type", "title", "file_path", "snippet"])

        # Status
        status = vi.vector_status(sdb, vdb)
        assert status["indexed_sessions"] == 5

    def test_incremental_update(self, vi, tmp_path):
        """Test that new documents are incrementally embedded."""
        sdb = tmp_path / "session.db"
        vdb = tmp_path / "vector_index.db"

        # Initial docs
        _create_session_db(
            sdb,
            [
                {"file_path": "/a.md", "title": "A", "content": "Content A"},
            ],
        )
        r1 = vi.embed_pending_session_docs(sdb, vdb)
        assert r1["embedded"] == 1

        # Add another doc
        conn = sqlite3.connect(str(sdb))
        conn.execute(
            "INSERT INTO session_documents "
            "(file_path, source_type, session_id, title, content, created_at, created_at_epoch, updated_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("/b.md", "session", "sess-002", "B", "Content B", "2026-01-02", 1767312000, 1767312000),
        )
        conn.commit()
        conn.close()

        r2 = vi.embed_pending_session_docs(sdb, vdb)
        assert r2["embedded"] == 1  # only the new doc
        assert r2["skipped"] == 1  # the old one was skipped

        status = vi.vector_status(sdb, vdb)
        assert status["indexed_sessions"] == 2
