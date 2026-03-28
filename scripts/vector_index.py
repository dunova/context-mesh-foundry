#!/usr/bin/env python3
"""Vector semantic search for ContextGO.

Provides hybrid search combining model2vec static embeddings with bm25s
keyword scoring via Reciprocal Rank Fusion (RRF).  All heavy dependencies
(model2vec, bm25s, numpy) are optional — the module degrades gracefully
when they are absent.

Public surface
--------------
vector_available        -- True when model2vec + numpy are importable
embed_pending_session_docs  -- incremental embedding of session documents
hybrid_search_session   -- combined vector + BM25 search
vector_search_session   -- pure vector cosine search
bm25s_search_session    -- pure BM25 keyword search
fetch_enriched_results  -- convert file_path+score → full result dicts
vector_status           -- index statistics
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

from context_config import env_int, env_str

# ---------------------------------------------------------------------------
# Module logger
# ---------------------------------------------------------------------------

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (env-based, matching ContextGO conventions)
# ---------------------------------------------------------------------------

VECTOR_MODEL_NAME: str = env_str("CONTEXTGO_VECTOR_MODEL", default="minishlab/potion-base-8M")
VECTOR_DIM: int = env_int("CONTEXTGO_VECTOR_DIM", default=256, minimum=64)
VECTOR_BATCH_SIZE: int = env_int("CONTEXTGO_VECTOR_EMBED_BATCH", default=64, minimum=1)
VECTOR_HYBRID_K: int = env_int("CONTEXTGO_VECTOR_HYBRID_K", default=60, minimum=1)
VECTOR_SEARCH_MULT: int = env_int("CONTEXTGO_VECTOR_SEARCH_MULT", default=5, minimum=2)
VECTOR_TEXT_CAP: int = env_int("CONTEXTGO_VECTOR_TEXT_CAP", default=2000, minimum=200)

# ---------------------------------------------------------------------------
# Availability probe (cached)
# ---------------------------------------------------------------------------

_VECTOR_AVAILABLE: bool | None = None


def vector_available() -> bool:
    """Return True if model2vec and numpy are importable.

    The result is cached at module level after the first call.
    """
    global _VECTOR_AVAILABLE  # noqa: PLW0603
    if _VECTOR_AVAILABLE is not None:
        return _VECTOR_AVAILABLE
    try:
        import model2vec  # noqa: F401
        import numpy  # noqa: F401

        _VECTOR_AVAILABLE = True
    except ImportError:
        _VECTOR_AVAILABLE = False
    return _VECTOR_AVAILABLE


# ---------------------------------------------------------------------------
# Model loading (lazy, thread-safe)
# ---------------------------------------------------------------------------

_MODEL: Any = None
_MODEL_LOCK = threading.Lock()


def _load_model() -> Any:
    """Load and cache the model2vec StaticModel.  Thread-safe."""
    global _MODEL  # noqa: PLW0603
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            from model2vec import StaticModel  # noqa: PLC0415

            print(
                f"Loading vector model ({VECTOR_MODEL_NAME})… / 正在加载向量模型…",
                file=sys.stderr,
            )
            _MODEL = StaticModel.from_pretrained(VECTOR_MODEL_NAME)
    return _MODEL


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------


def embed_texts(texts: list[str]) -> Any:
    """Embed a list of texts, returning shape ``(N, dim)`` float32 ndarray."""
    import numpy as np  # noqa: PLC0415

    if not texts:
        return np.empty((0, VECTOR_DIM), dtype=np.float32)
    model = _load_model()
    embeddings = model.encode(texts)
    # Ensure float32
    if hasattr(embeddings, "astype"):
        embeddings = embeddings.astype(np.float32)
    return embeddings


def embed_single(text: str) -> Any:
    """Embed one text; returns shape ``(dim,)`` float32 array."""
    return embed_texts([text])[0]


def _pack_vector(vec: Any) -> bytes:
    """Serialize float32 ndarray to bytes for SQLite BLOB storage."""
    import numpy as np  # noqa: PLC0415

    return np.asarray(vec, dtype=np.float32).tobytes()


def _unpack_vector(blob: bytes, dim: int | None = None) -> Any:
    """Deserialize bytes from SQLite BLOB to float32 ndarray."""
    import numpy as np  # noqa: PLC0415

    return np.frombuffer(blob, dtype=np.float32).copy()


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------


def cosine_similarity(a: Any, b: Any) -> float:
    """Compute cosine similarity between two 1-D float32 vectors."""
    import numpy as np  # noqa: PLC0415

    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# ---------------------------------------------------------------------------
# Vector database management
# ---------------------------------------------------------------------------

_SQL_CREATE_SESSION_VECTORS = """
CREATE TABLE IF NOT EXISTS session_vectors (
    file_path        TEXT PRIMARY KEY,
    embedding        BLOB NOT NULL,
    model_name       TEXT NOT NULL,
    vector_dim       INTEGER NOT NULL,
    indexed_at_epoch INTEGER NOT NULL
)
"""

_SQL_CREATE_OBSERVATION_VECTORS = """
CREATE TABLE IF NOT EXISTS observation_vectors (
    fingerprint      TEXT PRIMARY KEY,
    embedding        BLOB NOT NULL,
    model_name       TEXT NOT NULL,
    vector_dim       INTEGER NOT NULL,
    indexed_at_epoch INTEGER NOT NULL
)
"""


def get_vector_db_path(session_db_path: Path | str) -> Path:
    """Return the vector DB path alongside the session DB."""
    custom = env_str("CONTEXTGO_VECTOR_DB_PATH", default="").strip()
    if custom:
        return Path(custom).expanduser().resolve()
    return Path(session_db_path).parent / "vector_index.db"


def ensure_vector_db(vector_db_path: Path | str) -> Path:
    """Create the vector database with required tables."""
    vdb = Path(vector_db_path)
    vdb.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(str(vdb), timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(_SQL_CREATE_SESSION_VECTORS)
        conn.execute(_SQL_CREATE_OBSERVATION_VECTORS)
        conn.commit()
    finally:
        conn.close()
    return vdb


# ---------------------------------------------------------------------------
# Incremental embedding
# ---------------------------------------------------------------------------


def embed_pending_session_docs(
    session_db_path: Path | str,
    vector_db_path: Path | str,
    *,
    force: bool = False,
) -> dict[str, int]:
    """Compute and store embeddings for new/updated session documents.

    Returns ``{"embedded": int, "skipped": int, "deleted": int}``.
    """
    sdb = str(Path(session_db_path).resolve())
    vdb = ensure_vector_db(vector_db_path)
    now_epoch = int(time.time())
    embedded = 0
    skipped = 0
    deleted = 0

    conn = sqlite3.connect(str(vdb), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(f"ATTACH DATABASE '{sdb}' AS sessions")

        # Find pending documents
        if force:
            pending = conn.execute(
                "SELECT sd.file_path, sd.title, sd.content, sd.updated_at_epoch "
                "FROM sessions.session_documents sd"
            ).fetchall()
        else:
            pending = conn.execute(
                "SELECT sd.file_path, sd.title, sd.content, sd.updated_at_epoch "
                "FROM sessions.session_documents sd "
                "LEFT JOIN session_vectors sv ON sd.file_path = sv.file_path "
                "WHERE sv.file_path IS NULL "
                "OR sd.updated_at_epoch > sv.indexed_at_epoch"
            ).fetchall()

        # Batch embed
        batch_texts: list[str] = []
        batch_paths: list[str] = []
        batch_epochs: list[int] = []

        for row in pending:
            text = f"{row['title']} {row['content'][:VECTOR_TEXT_CAP]}"
            batch_texts.append(text)
            batch_paths.append(row["file_path"])
            batch_epochs.append(row["updated_at_epoch"])

            if len(batch_texts) >= VECTOR_BATCH_SIZE:
                vectors = embed_texts(batch_texts)
                _store_batch(conn, batch_paths, vectors, batch_epochs, now_epoch)
                embedded += len(batch_texts)
                batch_texts.clear()
                batch_paths.clear()
                batch_epochs.clear()

        # Flush remaining
        if batch_texts:
            vectors = embed_texts(batch_texts)
            _store_batch(conn, batch_paths, vectors, batch_epochs, now_epoch)
            embedded += len(batch_texts)

        skipped = max(0, conn.execute(
            "SELECT COUNT(*) FROM session_vectors"
        ).fetchone()[0] - embedded)

        # Delete stale vectors
        cur = conn.execute(
            "DELETE FROM session_vectors "
            "WHERE file_path NOT IN (SELECT file_path FROM sessions.session_documents)"
        )
        deleted = cur.rowcount

        conn.commit()
        conn.execute("DETACH DATABASE sessions")
    finally:
        conn.close()

    return {"embedded": embedded, "skipped": skipped, "deleted": deleted}


def _store_batch(
    conn: sqlite3.Connection,
    paths: list[str],
    vectors: Any,
    epochs: list[int],
    now_epoch: int,
) -> None:
    """Insert or replace a batch of vectors."""
    for i, path in enumerate(paths):
        conn.execute(
            "INSERT OR REPLACE INTO session_vectors "
            "(file_path, embedding, model_name, vector_dim, indexed_at_epoch) "
            "VALUES (?, ?, ?, ?, ?)",
            (path, _pack_vector(vectors[i]), VECTOR_MODEL_NAME, VECTOR_DIM, now_epoch),
        )


# ---------------------------------------------------------------------------
# Vector search
# ---------------------------------------------------------------------------


def vector_search_session(
    query: str,
    session_db_path: Path | str,
    vector_db_path: Path | str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Pure vector cosine search over session_vectors.

    Returns ``[{"file_path": str, "score": float, "rank": int}, ...]``.
    """
    import numpy as np  # noqa: PLC0415

    vdb = str(Path(vector_db_path).resolve())
    if not Path(vdb).exists():
        return []

    query_vec = embed_single(query)

    conn = sqlite3.connect(vdb, timeout=30)
    try:
        rows = conn.execute(
            "SELECT file_path, embedding FROM session_vectors"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    paths = [r[0] for r in rows]
    matrix = np.array([_unpack_vector(r[1]) for r in rows], dtype=np.float32)

    # Batch cosine similarity
    norms = np.linalg.norm(matrix, axis=1)
    q_norm = np.linalg.norm(query_vec)
    if q_norm == 0.0:
        return []
    scores = (matrix @ query_vec) / (norms * q_norm + 1e-10)

    # Rank by score descending
    top_k = min(limit * VECTOR_SEARCH_MULT, len(scores))
    top_indices = np.argsort(scores)[::-1][:top_k]

    return [
        {"file_path": paths[i], "score": float(scores[i]), "rank": rank + 1}
        for rank, i in enumerate(top_indices)
        if scores[i] > 0.0
    ]


# ---------------------------------------------------------------------------
# BM25 search
# ---------------------------------------------------------------------------


def bm25s_search_session(
    query: str,
    session_db_path: Path | str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """BM25 keyword search over session_documents.

    Returns ``[{"file_path": str, "score": float, "rank": int}, ...]``.
    """
    sdb = str(Path(session_db_path).resolve())
    if not Path(sdb).exists():
        return []

    conn = sqlite3.connect(sdb, timeout=30)
    try:
        rows = conn.execute(
            "SELECT file_path, title, content FROM session_documents"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    paths = [r[0] for r in rows]
    corpus = [f"{r[1]} {r[2][:VECTOR_TEXT_CAP]}" for r in rows]

    try:
        import bm25s  # noqa: PLC0415

        tokenized_corpus = bm25s.tokenize(corpus, show_progress=False)
        retriever = bm25s.BM25()
        retriever.index(tokenized_corpus, show_progress=False)

        tokenized_query = bm25s.tokenize([query], show_progress=False)
        results, scores = retriever.retrieve(tokenized_query, corpus=paths, k=min(limit * VECTOR_SEARCH_MULT, len(paths)))

        output: list[dict[str, Any]] = []
        for rank_idx in range(results.shape[1]):
            fp = results[0, rank_idx]
            sc = float(scores[0, rank_idx])
            if sc > 0.0:
                output.append({"file_path": fp, "score": sc, "rank": rank_idx + 1})
        return output
    except ImportError:
        _logger.debug("bm25s not available, skipping BM25 search")
        return []


# ---------------------------------------------------------------------------
# Hybrid RRF merge
# ---------------------------------------------------------------------------


def _rrf_merge(
    vector_results: list[dict[str, Any]],
    bm25_results: list[dict[str, Any]],
    k: int = 60,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Merge vector + BM25 results via Reciprocal Rank Fusion."""
    PENALTY = 9999
    v_rank = {r["file_path"]: r["rank"] for r in vector_results}
    b_rank = {r["file_path"]: r["rank"] for r in bm25_results}
    all_paths = set(v_rank) | set(b_rank)

    scored = [
        {
            "file_path": fp,
            "rrf_score": 1.0 / (k + v_rank.get(fp, PENALTY)) + 1.0 / (k + b_rank.get(fp, PENALTY)),
        }
        for fp in all_paths
    ]
    return sorted(scored, key=lambda x: x["rrf_score"], reverse=True)[:limit]


def hybrid_search_session(
    query: str,
    session_db_path: Path | str,
    vector_db_path: Path | str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Combined vector + BM25 search via RRF.

    Returns ``[{"file_path": str, "rrf_score": float}, ...]``.
    """
    vec_results = vector_search_session(query, session_db_path, vector_db_path, limit=limit)
    bm25_results = bm25s_search_session(query, session_db_path, limit=limit)

    if not vec_results and not bm25_results:
        return []
    if not vec_results:
        return [{"file_path": r["file_path"], "rrf_score": 1.0 / (VECTOR_HYBRID_K + r["rank"])} for r in bm25_results[:limit]]
    if not bm25_results:
        return [{"file_path": r["file_path"], "rrf_score": 1.0 / (VECTOR_HYBRID_K + r["rank"])} for r in vec_results[:limit]]

    return _rrf_merge(vec_results, bm25_results, k=VECTOR_HYBRID_K, limit=limit)


# ---------------------------------------------------------------------------
# Result enrichment
# ---------------------------------------------------------------------------


def fetch_enriched_results(
    ranked_paths: list[dict[str, Any]],
    session_db_path: Path | str,
    query: str,
) -> list[dict[str, Any]]:
    """Fetch full session_documents rows for ranked paths.

    Returns dicts matching the ``_search_rows`` output contract:
    ``source_type, session_id, title, file_path, created_at, created_at_epoch, snippet``.
    """
    if not ranked_paths:
        return []

    sdb = str(Path(session_db_path).resolve())
    file_paths = [r["file_path"] for r in ranked_paths]
    placeholders = ",".join("?" for _ in file_paths)

    conn = sqlite3.connect(sdb, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"SELECT source_type, session_id, title, file_path, created_at, "  # noqa: S608
            f"created_at_epoch, content FROM session_documents "
            f"WHERE file_path IN ({placeholders})",
            file_paths,
        ).fetchall()
    finally:
        conn.close()

    # Build lookup by file_path
    row_map = {r["file_path"]: r for r in rows}
    ql = query.lower()

    results: list[dict[str, Any]] = []
    for entry in ranked_paths:
        fp = entry["file_path"]
        row = row_map.get(fp)
        if row is None:
            continue

        # Build snippet around query match
        content = row["content"] or ""
        snippet = _build_snippet(content, ql)

        results.append({
            "source_type": row["source_type"],
            "session_id": row["session_id"],
            "title": row["title"],
            "file_path": row["file_path"],
            "created_at": row["created_at"],
            "created_at_epoch": row["created_at_epoch"],
            "snippet": snippet,
        })

    return results


def _build_snippet(content: str, query_lower: str, radius: int = 120) -> str:
    """Extract a snippet centred on the first query match."""
    idx = content.lower().find(query_lower)
    if idx < 0:
        return content[:radius * 2].strip() if content else ""
    start = max(0, idx - radius)
    end = min(len(content), idx + len(query_lower) + radius)
    return content[start:end].strip()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def vector_status(
    session_db_path: Path | str,
    vector_db_path: Path | str,
) -> dict[str, Any]:
    """Return vector index statistics."""
    vdb = Path(vector_db_path)
    result: dict[str, Any] = {
        "available": vector_available(),
        "model": VECTOR_MODEL_NAME,
        "dim": VECTOR_DIM,
        "vector_db_path": str(vdb),
        "vector_db_exists": vdb.exists(),
        "indexed_sessions": 0,
        "indexed_observations": 0,
    }

    if not vdb.exists():
        return result

    conn = sqlite3.connect(str(vdb), timeout=10)
    try:
        with contextlib.suppress(sqlite3.OperationalError):
            result["indexed_sessions"] = conn.execute(
                "SELECT COUNT(*) FROM session_vectors"
            ).fetchone()[0]
        with contextlib.suppress(sqlite3.OperationalError):
            result["indexed_observations"] = conn.execute(
                "SELECT COUNT(*) FROM observation_vectors"
            ).fetchone()[0]
    finally:
        conn.close()

    return result
