#!/usr/bin/env python3
"""ContextGO maintenance utility.

Repairs stale processing jobs and enqueues local session files that are not
yet tracked in the ContextGO SQLite database.

Typical usage::

    python3 scripts/context_maintenance.py --repair-queue --enqueue-missing
    python3 scripts/context_maintenance.py --enqueue-missing --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sqlite3
import sys
import uuid
from pathlib import Path

__all__ = [
    "collect_local_session_files",
    "enqueue_missing",
    "fetch_existing_session_paths",
    "main",
    "parse_args",
    "print_snapshot",
    "repair_queue",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

SessionItem = tuple[str, Path, str]  # (source_type, file_path, session_id)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SOURCE_CODEX = "codex"
_SOURCE_CLAUDE = "claude"
_SUBAGENT_FRAGMENT = "/subagents/"
_TERMINAL_STATUSES = frozenset({"done", "failed", "error"})

# Default DB path — derived from CONTEXTGO_STORAGE_ROOT or ~/.contextgo.
# Individual callers override this via --db when needed (e.g. aline integration).
_DEFAULT_DB = "~/.contextgo/db/contextgo.db"

# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------

_SQL_FETCH_EXISTING_PATHS = "SELECT session_file_path FROM sessions WHERE session_file_path IS NOT NULL"

_SQL_COUNT_STALE = """
    SELECT count(*) FROM jobs
    WHERE status = 'processing'
      AND (
        locked_until IS NULL
        OR datetime(locked_until) < datetime('now')
        OR datetime(updated_at) < datetime(?)
      )
"""

_SQL_RELEASE_STALE = """
    UPDATE jobs
    SET status       = 'queued',
        locked_until = NULL,
        locked_by    = NULL,
        updated_at   = datetime('now')
    WHERE status = 'processing'
      AND (
        locked_until IS NULL
        OR datetime(locked_until) < datetime('now')
        OR datetime(updated_at) < datetime(?)
      )
"""

_SQL_LOOKUP_JOB = "SELECT id, status FROM jobs WHERE dedupe_key = ? LIMIT 1"

_SQL_REVIVE_JOB = """
    UPDATE jobs
    SET status      = 'queued',
        attempts    = 0,
        next_run_at = datetime('now'),
        payload     = ?,
        last_error  = NULL,
        updated_at  = datetime('now')
    WHERE id = ?
"""

_SQL_INSERT_JOB = """
    INSERT INTO jobs
        (id, kind, dedupe_key, payload, status, priority, attempts,
         next_run_at, reschedule, created_at, updated_at)
    VALUES
        (?, 'session_process', ?, ?, 'queued', 100, 0,
         datetime('now'), 0, datetime('now'), datetime('now'))
"""

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the maintenance utility."""
    parser = argparse.ArgumentParser(
        description="Maintain ContextGO backlog and coverage",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--db",
        default=_DEFAULT_DB,
        help="Path to the ContextGO SQLite database",
    )
    parser.add_argument(
        "--codex-root",
        default="~/.codex/sessions",
        help="Codex sessions root directory",
    )
    parser.add_argument(
        "--claude-root",
        default="~/.claude/projects",
        help="Claude projects root directory",
    )
    parser.add_argument(
        "--include-subagents",
        action="store_true",
        help="Include .jsonl files inside subagents/ subdirectories",
    )
    parser.add_argument(
        "--repair-queue",
        action="store_true",
        help="Release stale processing jobs back to queued",
    )
    parser.add_argument(
        "--enqueue-missing",
        action="store_true",
        help="Queue local session files not yet present in the database",
    )
    parser.add_argument(
        "--max-enqueue",
        type=int,
        default=2000,
        help="Maximum number of sessions to enqueue per run",
    )
    parser.add_argument(
        "--stale-minutes",
        type=int,
        default=15,
        help="Age threshold (minutes) for treating processing jobs as stale",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to the database",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def collect_local_session_files(
    codex_root: Path,
    claude_root: Path,
    include_subagents: bool,
) -> list[SessionItem]:
    """Return all local session JSONL files from Codex and Claude roots.

    Args:
        codex_root: Directory that contains Codex session JSONL files.
        claude_root: Directory that contains Claude project JSONL files.
        include_subagents: When ``False``, skip files whose resolved path
            contains ``/subagents/``.

    Returns:
        A list of ``(source_type, path, session_id)`` tuples where
        *session_id* is the file stem.
    """
    items: list[SessionItem] = []

    if codex_root.is_dir():
        for p in sorted(codex_root.rglob("*.jsonl")):
            items.append((_SOURCE_CODEX, p, p.stem))

    if claude_root.is_dir():
        for p in sorted(claude_root.rglob("*.jsonl")):
            if not include_subagents and _SUBAGENT_FRAGMENT in str(p):
                continue
            items.append((_SOURCE_CLAUDE, p, p.stem))

    return items


# ---------------------------------------------------------------------------
# Database queries
# ---------------------------------------------------------------------------


def fetch_existing_session_paths(cur: sqlite3.Cursor) -> set[str]:
    """Return the set of session file paths already recorded in the database."""
    return {row[0] for row in cur.execute(_SQL_FETCH_EXISTING_PATHS)}


def print_snapshot(
    cur: sqlite3.Cursor,
    local_total: int,
    missing_codex: int,
    missing_claude: int,
) -> None:
    """Print a concise snapshot of database and local-file counts to stdout."""
    sessions: int = cur.execute("SELECT count(*) FROM sessions").fetchone()[0]
    turns: int = cur.execute("SELECT count(*) FROM turns").fetchone()[0]
    turn_content: int = cur.execute("SELECT count(*) FROM turn_content").fetchone()[0]
    events: int = cur.execute("SELECT count(*) FROM events").fetchone()[0]

    queued_sp: int = cur.execute(
        "SELECT count(*) FROM jobs WHERE kind='session_process' AND status='queued'"
    ).fetchone()[0]
    processing_sp: int = cur.execute(
        "SELECT count(*) FROM jobs WHERE kind='session_process' AND status='processing'"
    ).fetchone()[0]
    done_sp: int = cur.execute("SELECT count(*) FROM jobs WHERE kind='session_process' AND status='done'").fetchone()[0]
    llm_err_sessions: int = cur.execute(
        "SELECT count(*) FROM sessions WHERE session_title LIKE '\u26a0 LLM API Error%'"
    ).fetchone()[0]

    print("=== Snapshot ===")
    print(f"sessions={sessions} turns={turns} turn_content={turn_content} events={events}")
    print(f"session_process jobs: queued={queued_sp} processing={processing_sp} done={done_sp}")
    print(f"llm_error_sessions={llm_err_sessions}")
    print(f"local_files={local_total} missing_codex={missing_codex} missing_claude_main={missing_claude}")


# ---------------------------------------------------------------------------
# Queue repair
# ---------------------------------------------------------------------------


def repair_queue(
    cur: sqlite3.Cursor,
    stale_minutes: int,
    dry_run: bool,
) -> int:
    """Release stale ``processing`` jobs back to ``queued``.

    A job is considered stale when its ``updated_at`` timestamp is older than
    *stale_minutes* ago (UTC), or when its ``locked_until`` value has already
    passed.

    Args:
        cur: An open SQLite cursor for the ContextGO database.
        stale_minutes: Age threshold in minutes.
        dry_run: When ``True``, count affected rows without mutating the DB.

    Returns:
        Number of jobs affected (or that *would* be affected in dry-run mode).
    """
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=stale_minutes)).strftime("%Y-%m-%d %H:%M:%S")

    if dry_run:
        return cur.execute(_SQL_COUNT_STALE, (cutoff,)).fetchone()[0]

    cur.execute(_SQL_RELEASE_STALE, (cutoff,))
    return cur.rowcount


# ---------------------------------------------------------------------------
# Missing-session enqueue
# ---------------------------------------------------------------------------


def enqueue_missing(
    cur: sqlite3.Cursor,
    missing: list[SessionItem],
    max_enqueue: int,
    dry_run: bool,
) -> tuple[int, int]:
    """Insert or revive jobs for local session files absent from the database.

    Args:
        cur: An open SQLite cursor for the ContextGO database.
        missing: Session items to process, as returned by
            :func:`collect_local_session_files`.
        max_enqueue: Maximum number of sessions to process in this call.
            Values <= 0 result in no operations.
        dry_run: When ``True``, simulate inserts/updates without mutating the DB.

    Returns:
        A ``(inserted, revived)`` tuple: *inserted* counts new job rows;
        *revived* counts terminal jobs reset to ``queued``.
    """
    inserted = 0
    revived = 0
    limit = max(0, max_enqueue)

    for stype, path, sid in missing[:limit]:
        dedupe_key = f"session_process:{sid}"
        payload = json.dumps(
            {
                "session_id": sid,
                "session_file_path": str(path),
                "session_type": stype,
                "source_event": "manual_full_backfill",
            },
            ensure_ascii=False,
        )

        existing = cur.execute(_SQL_LOOKUP_JOB, (dedupe_key,)).fetchone()
        if existing:
            job_id, status = existing
            if status in _TERMINAL_STATUSES:
                if not dry_run:
                    cur.execute(_SQL_REVIVE_JOB, (payload, job_id))
                revived += 1
            continue

        if not dry_run:
            cur.execute(_SQL_INSERT_JOB, (str(uuid.uuid4()), dedupe_key, payload))
        inserted += 1

    return inserted, revived


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the maintenance utility.

    Returns:
        ``0`` on success, ``1`` on error.
    """
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    args = parse_args(argv)
    db_path = Path(args.db).expanduser().resolve()
    codex_root = Path(args.codex_root).expanduser().resolve()
    claude_root = Path(args.claude_root).expanduser().resolve()

    if not db_path.exists():
        if args.dry_run:
            local_items = collect_local_session_files(codex_root, claude_root, args.include_subagents)
            missing_codex = sum(1 for stype, _, _ in local_items if stype == _SOURCE_CODEX)
            missing_claude = sum(1 for stype, _, _ in local_items if stype == _SOURCE_CLAUDE)
            print("=== Snapshot ===")
            print("sessions=0 turns=0 turn_content=0 events=0")
            print("session_process jobs: queued=0 processing=0 done=0")
            print("llm_error_sessions=0")
            print(f"local_files={len(local_items)} missing_codex={missing_codex} missing_claude_main={missing_claude}")
            print("dry_run: no DB changes applied (database missing, treated as empty)")
            return 0
        print(f"ERROR: cannot open database {db_path}: database file does not exist", file=sys.stderr)
        return 1

    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.OperationalError as exc:
        print(f"ERROR: cannot open database {db_path}: {exc}", file=sys.stderr)
        return 1

    try:
        cur = conn.cursor()

        local_items = collect_local_session_files(codex_root, claude_root, args.include_subagents)
        existing_paths = fetch_existing_session_paths(cur)

        missing: list[SessionItem] = []
        missing_codex = 0
        missing_claude = 0
        for stype, path, sid in local_items:
            if str(path) in existing_paths:
                continue
            missing.append((stype, path, sid))
            if stype == _SOURCE_CODEX:
                missing_codex += 1
            elif stype == _SOURCE_CLAUDE:
                missing_claude += 1

        print_snapshot(cur, len(local_items), missing_codex, missing_claude)

        if args.repair_queue:
            released = repair_queue(cur, args.stale_minutes, args.dry_run)
            print(f"repair_queue: released_stale_processing={released}")

        if args.enqueue_missing:
            inserted, revived = enqueue_missing(cur, missing, args.max_enqueue, args.dry_run)
            print(f"enqueue_missing: inserted={inserted} revived={revived} (max={args.max_enqueue})")

        if args.dry_run:
            conn.rollback()
            print("dry_run: no DB changes applied")
        else:
            conn.commit()
            print("db_commit: done")

    except sqlite3.DatabaseError as exc:
        print(f"ERROR: database error: {exc}", file=sys.stderr)
        conn.rollback()
        return 1
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
