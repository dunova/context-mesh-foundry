#!/usr/bin/env python3
"""OneContext maintenance utility.

Goals:
1) Show coverage/status snapshot.
2) Repair stale processing jobs.
3) Enqueue missing local sessions (codex + claude main sessions) for full backfill.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import uuid
from pathlib import Path


def expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maintain OneContext backlog and coverage")
    parser.add_argument("--db", default="~/.aline/db/aline.db", help="Path to aline.db")
    parser.add_argument("--codex-root", default="~/.codex/sessions", help="Codex sessions root")
    parser.add_argument("--claude-root", default="~/.claude/projects", help="Claude projects root")
    parser.add_argument("--include-subagents", action="store_true", help="Include claude subagents jsonl")
    parser.add_argument("--repair-queue", action="store_true", help="Release stale processing jobs")
    parser.add_argument("--enqueue-missing", action="store_true", help="Queue missing local sessions")
    parser.add_argument("--max-enqueue", type=int, default=2000, help="Max queued sessions per run")
    parser.add_argument(
        "--stale-minutes",
        type=int,
        default=15,
        help="Treat processing jobs older than this as stale when --repair-queue is set",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def collect_local_session_files(codex_root: Path, claude_root: Path, include_subagents: bool) -> list[tuple[str, Path, str]]:
    items: list[tuple[str, Path, str]] = []
    if codex_root.is_dir():
        for p in codex_root.rglob("*.jsonl"):
            items.append(("codex", p, p.stem))
    if claude_root.is_dir():
        for p in claude_root.rglob("*.jsonl"):
            sp = str(p)
            if not include_subagents and "/subagents/" in sp:
                continue
            items.append(("claude", p, p.stem))
    return items


def fetch_existing_session_paths(cur: sqlite3.Cursor) -> set[str]:
    return {r[0] for r in cur.execute("SELECT session_file_path FROM sessions WHERE session_file_path IS NOT NULL")}


def print_snapshot(cur: sqlite3.Cursor, local_total: int, missing_codex: int, missing_claude: int) -> None:
    sessions = cur.execute("SELECT count(*) FROM sessions").fetchone()[0]
    turns = cur.execute("SELECT count(*) FROM turns").fetchone()[0]
    turn_content = cur.execute("SELECT count(*) FROM turn_content").fetchone()[0]
    events = cur.execute("SELECT count(*) FROM events").fetchone()[0]

    queued_sp = cur.execute(
        "SELECT count(*) FROM jobs WHERE kind='session_process' AND status='queued'"
    ).fetchone()[0]
    processing_sp = cur.execute(
        "SELECT count(*) FROM jobs WHERE kind='session_process' AND status='processing'"
    ).fetchone()[0]
    done_sp = cur.execute(
        "SELECT count(*) FROM jobs WHERE kind='session_process' AND status='done'"
    ).fetchone()[0]
    llm_err_sessions = cur.execute(
        "SELECT count(*) FROM sessions WHERE session_title LIKE '⚠ LLM API Error%'"
    ).fetchone()[0]

    print("=== Snapshot ===")
    print(f"sessions={sessions} turns={turns} turn_content={turn_content} events={events}")
    print(
        f"session_process jobs: queued={queued_sp} processing={processing_sp} done={done_sp}"
    )
    print(f"llm_error_sessions={llm_err_sessions}")
    print(
        f"local_files={local_total} missing_codex={missing_codex} missing_claude_main={missing_claude}"
    )


def repair_queue(cur: sqlite3.Cursor, stale_minutes: int, dry_run: bool) -> int:
    cutoff = (dt.datetime.now() - dt.timedelta(minutes=stale_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    sql = """
    UPDATE jobs
    SET status='queued',
        locked_until=NULL,
        locked_by=NULL,
        updated_at=datetime('now')
    WHERE status='processing'
      AND (
        locked_until IS NULL
        OR datetime(locked_until) < datetime('now')
        OR datetime(updated_at) < datetime(?)
      )
    """
    if dry_run:
        return cur.execute(
            """
            SELECT count(*) FROM jobs
            WHERE status='processing'
              AND (
                locked_until IS NULL
                OR datetime(locked_until) < datetime('now')
                OR datetime(updated_at) < datetime(?)
              )
            """,
            (cutoff,),
        ).fetchone()[0]
    cur.execute(sql, (cutoff,))
    return cur.rowcount


def enqueue_missing(
    cur: sqlite3.Cursor,
    missing: list[tuple[str, Path, str]],
    max_enqueue: int,
    dry_run: bool,
) -> tuple[int, int]:
    inserted = 0
    revived = 0
    for stype, path, sid in missing[: max(0, max_enqueue)]:
        dedupe_key = f"session_process:{sid}"
        payload_obj = {
            "session_id": sid,
            "session_file_path": str(path),
            "session_type": stype,
            "source_event": "manual_full_backfill",
        }
        payload = json.dumps(payload_obj, ensure_ascii=False)

        existing = cur.execute(
            "SELECT id, status FROM jobs WHERE dedupe_key=? LIMIT 1",
            (dedupe_key,),
        ).fetchone()
        if existing:
            job_id, status = existing
            # If dedupe exists but session is still missing, revive done/failed jobs.
            if status in {"done", "failed", "error"}:
                if not dry_run:
                    cur.execute(
                        """
                        UPDATE jobs
                        SET status='queued',
                            attempts=0,
                            next_run_at=datetime('now'),
                            payload=?,
                            last_error=NULL,
                            updated_at=datetime('now')
                        WHERE id=?
                        """,
                        (payload, job_id),
                    )
                revived += 1
            continue

        if dry_run:
            inserted += 1
            continue

        cur.execute(
            """
            INSERT INTO jobs
            (id, kind, dedupe_key, payload, status, priority, attempts, next_run_at, reschedule, created_at, updated_at)
            VALUES (?, 'session_process', ?, ?, 'queued', 100, 0, datetime('now'), 0, datetime('now'), datetime('now'))
            """,
            (str(uuid.uuid4()), dedupe_key, payload),
        )
        inserted += 1
    return inserted, revived


def main() -> int:
    args = parse_args()
    db_path = expand(args.db)
    codex_root = expand(args.codex_root)
    claude_root = expand(args.claude_root)

    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}")
        return 1

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    local_items = collect_local_session_files(codex_root, claude_root, args.include_subagents)
    existing_paths = fetch_existing_session_paths(cur)
    missing: list[tuple[str, Path, str]] = []
    missing_codex = 0
    missing_claude = 0
    for stype, path, sid in local_items:
        if str(path) in existing_paths:
            continue
        missing.append((stype, path, sid))
        if stype == "codex":
            missing_codex += 1
        elif stype == "claude":
            missing_claude += 1

    print_snapshot(cur, len(local_items), missing_codex, missing_claude)

    released = 0
    if args.repair_queue:
        released = repair_queue(cur, args.stale_minutes, args.dry_run)
        print(f"repair_queue: released_stale_processing={released}")

    inserted = revived = 0
    if args.enqueue_missing:
        inserted, revived = enqueue_missing(cur, missing, args.max_enqueue, args.dry_run)
        print(f"enqueue_missing: inserted={inserted} revived={revived} (max={args.max_enqueue})")

    if args.dry_run:
        conn.rollback()
        print("dry_run: no DB changes applied")
    else:
        conn.commit()
        print("db_commit: done")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
