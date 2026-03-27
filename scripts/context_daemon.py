#!/usr/bin/env python3
"""
ContextGO real-time sync daemon.

Responsibilities
----------------
- Tail JSONL history files from Claude Code, Codex, OpenCode, Kilo
- Tail shell history (zsh / bash)
- Scan Codex session files and Claude transcript files
- Scan Antigravity brain directories
- Export idle sessions to local storage and optionally to a remote endpoint
- Retry queued (pending) exports when the remote becomes reachable again
- Run indefinitely with bounded memory, rotating logs, adaptive polling,
  and graceful shutdown on SIGTERM / SIGINT
"""

from __future__ import annotations

import atexit
import contextlib
import glob as _glob
import hashlib
import json
import logging
import logging.handlers
import os
import random
import re
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import resource as _resource_mod
except ImportError:
    _resource_mod = None  # type: ignore[assignment]

try:
    from context_config import env_bool, env_float, env_int, env_str, storage_root
    from memory_index import strip_private_blocks, sync_index_from_storage
except ImportError:  # pragma: no cover - alternate import path
    from .context_config import env_bool, env_float, env_int, env_str, storage_root  # type: ignore[import-not-found]
    from .memory_index import strip_private_blocks, sync_index_from_storage  # type: ignore[import-not-found]


# Env-var helpers — all daemon settings are prefixed CONTEXTGO_


def _cfg_bool(name: str, default: bool) -> bool:
    return env_bool(f"CONTEXTGO_{name}", default=default)


def _cfg_int(name: str, default: int, **kwargs: Any) -> int:
    return env_int(f"CONTEXTGO_{name}", default=default, **kwargs)


def _cfg_float(name: str, default: float, **kwargs: Any) -> float:
    return env_float(f"CONTEXTGO_{name}", default=default, **kwargs)


def _cfg_str(name: str, default: str) -> str:
    return env_str(f"CONTEXTGO_{name}", default=default)


# Remote-sync configuration

# Optional remote sync; the default (local-only) path never contacts a server.
REMOTE_SYNC_URL: str = _cfg_str("REMOTE_URL", default="http://127.0.0.1:8090/api/v1")
REMOTE_RESOURCE_ENDPOINT: str = f"{REMOTE_SYNC_URL.rstrip('/')}/resources"
REMOTE_HISTORY_TARGET: str = "contextgo://resources/shared/history"

# Security: non-localhost URLs must use HTTPS to prevent MITM attacks.
_remote_host = REMOTE_SYNC_URL.split("://", 1)[-1].split("/", 1)[0].split(":")[0]
if _remote_host not in ("127.0.0.1", "localhost", "::1") and not REMOTE_SYNC_URL.startswith("https://"):
    print(
        f"FATAL: CONTEXTGO_REMOTE_URL must use https:// for non-localhost targets. Got: {REMOTE_SYNC_URL}",
        file=sys.stderr,
    )
    raise SystemExit(1)

# Storage paths

LOCAL_STORAGE_ROOT: Path = storage_root().expanduser()

# Security: storage root must be owned by the current user; symlinks are warned.
if LOCAL_STORAGE_ROOT.exists():
    _storage_stat = LOCAL_STORAGE_ROOT.lstat()
    if _storage_stat.st_uid != os.getuid():
        print(
            f"FATAL: {LOCAL_STORAGE_ROOT} is not owned by current user (uid={_storage_stat.st_uid})",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if LOCAL_STORAGE_ROOT.is_symlink():
        print(
            f"WARNING: {LOCAL_STORAGE_ROOT} is a symlink — following cautiously",
            file=sys.stderr,
        )

PENDING_DIR: Path = LOCAL_STORAGE_ROOT / "resources" / "shared" / "history" / ".pending"
LOG_DIR: Path = LOCAL_STORAGE_ROOT / "logs"

# Logging constants

_DAEMON_LOG_NAME = "contextgo_daemon.log"
_DAEMON_LOCK_NAME = "contextgo_daemon.lock"
_LOGGER_NAME = "contextgo.daemon"
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Logging setup (runs once at import; logger is module-scoped)

LOG_DIR.mkdir(parents=True, exist_ok=True)
with contextlib.suppress(OSError):
    os.chmod(LOG_DIR, 0o700)

logger = logging.getLogger(_LOGGER_NAME)
logger.setLevel(logging.INFO)

# Rotating file handler — 5 MB x 3 backups
_rfh = logging.handlers.RotatingFileHandler(
    LOG_DIR / _DAEMON_LOG_NAME,
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_rfh.setFormatter(logging.Formatter(_LOG_FORMAT))
logger.addHandler(_rfh)

# Console handler — warnings and above only (keeps launchd/systemd journals clean)
_sh = logging.StreamHandler(sys.stderr)
_sh.setLevel(logging.WARNING)
_sh.setFormatter(logging.Formatter(_LOG_FORMAT))
logger.addHandler(_sh)

LOCK_FILE: Path = LOG_DIR / _DAEMON_LOCK_NAME
_LOCK_FD: int | None = None

# Optional httpx (remote-sync transport)

try:
    import httpx as _httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _httpx = None  # type: ignore[assignment]
    _HTTPX_AVAILABLE = False
    logger.info("httpx not installed; remote sync disabled.")

# Poll / timing configuration
# Night-mode: off-hours (23:00-07:00) sleep is expanded to NIGHT_POLL_INTERVAL_SEC.
NIGHT_POLL_START_HOUR: int = _cfg_int("NIGHT_POLL_START_HOUR", default=23)
NIGHT_POLL_END_HOUR: int = _cfg_int("NIGHT_POLL_END_HOUR", default=7)
NIGHT_POLL_INTERVAL_SEC: int = _cfg_int("NIGHT_POLL_INTERVAL_SEC", default=600, minimum=1)
POLL_INTERVAL_SEC: int = _cfg_int("POLL_INTERVAL_SEC", default=30, minimum=1)
FAST_POLL_INTERVAL_SEC: int = _cfg_int("FAST_POLL_INTERVAL_SEC", default=3, minimum=1)
IDLE_SLEEP_CAP_SEC: int = max(POLL_INTERVAL_SEC, _cfg_int("IDLE_SLEEP_CAP_SEC", default=180))
IDLE_TIMEOUT_SEC: int = _cfg_int("IDLE_TIMEOUT_SEC", default=300)
PENDING_RETRY_INTERVAL_SEC: int = _cfg_int("PENDING_RETRY_INTERVAL_SEC", default=60, minimum=5)
HEARTBEAT_INTERVAL_SEC: int = _cfg_int("HEARTBEAT_INTERVAL_SEC", default=600, minimum=10)
CYCLE_BUDGET_SEC: int = _cfg_int("CYCLE_BUDGET_SEC", default=8, minimum=1)
ERROR_BACKOFF_MAX_SEC: int = _cfg_int("ERROR_BACKOFF_MAX_SEC", default=30, minimum=2)
LOOP_JITTER_SEC: float = _cfg_float("LOOP_JITTER_SEC", default=0.7, minimum=0.0)
INDEX_SYNC_MIN_INTERVAL_SEC: int = _cfg_int("INDEX_SYNC_MIN_INTERVAL_SEC", default=20, minimum=5)

# Capacity limits / HTTP timeouts
MAX_TRACKED_SESSIONS: int = _cfg_int("MAX_TRACKED_SESSIONS", default=240)
MAX_FILE_CURSORS: int = _cfg_int("MAX_FILE_CURSORS", default=800)
SESSION_TTL_SEC: int = _cfg_int("SESSION_TTL_SEC", default=7200)
MAX_MESSAGES_PER_SESSION: int = _cfg_int("MAX_MESSAGES_PER_SESSION", default=500)
MAX_PENDING_FILES: int = max(200, _cfg_int("MAX_PENDING_FILES", default=5000))
EXPORT_HTTP_TIMEOUT_SEC: int = _cfg_int("EXPORT_HTTP_TIMEOUT_SEC", default=30, minimum=5)
PENDING_HTTP_TIMEOUT_SEC: int = _cfg_int("PENDING_HTTP_TIMEOUT_SEC", default=15, minimum=5)

# Feature flags
ENABLE_REMOTE_SYNC: bool = _cfg_bool("ENABLE_REMOTE_SYNC", default=False)
ENABLE_SHELL_MONITOR: bool = _cfg_bool("ENABLE_SHELL_MONITOR", default=True)
ENABLE_CLAUDE_HISTORY_MONITOR: bool = _cfg_bool("ENABLE_CLAUDE_HISTORY_MONITOR", default=True)
ENABLE_CODEX_HISTORY_MONITOR: bool = _cfg_bool("ENABLE_CODEX_HISTORY_MONITOR", default=True)
ENABLE_OPENCODE_MONITOR: bool = _cfg_bool("ENABLE_OPENCODE_MONITOR", default=False)
ENABLE_KILO_MONITOR: bool = _cfg_bool("ENABLE_KILO_MONITOR", default=False)
ENABLE_CODEX_SESSION_MONITOR: bool = _cfg_bool("ENABLE_CODEX_SESSION_MONITOR", default=True)
ENABLE_CLAUDE_TRANSCRIPTS_MONITOR: bool = _cfg_bool("ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", default=True)
ENABLE_ANTIGRAVITY_MONITOR: bool = _cfg_bool("ENABLE_ANTIGRAVITY_MONITOR", default=True)

# Antigravity (Gemini) configuration
# ANTIGRAVITY_INGEST_MODE: "final_only" (wait for quiet) or "live" (export on every change).
ANTIGRAVITY_BRAIN: Path = Path.home() / ".gemini" / "antigravity" / "brain"
_raw_ingest_mode = _cfg_str("ANTIGRAVITY_INGEST_MODE", default="final_only").strip().lower()
ANTIGRAVITY_INGEST_MODE: str = _raw_ingest_mode if _raw_ingest_mode in {"final_only", "live"} else "final_only"
ANTIGRAVITY_QUIET_SEC: int = max(30, _cfg_int("ANTIGRAVITY_QUIET_SEC", default=180))
ANTIGRAVITY_MIN_DOC_BYTES: int = max(120, _cfg_int("ANTIGRAVITY_MIN_DOC_BYTES", default=400))
# Skip Antigravity polling while the Gemini language server is active (macOS ARM).
SUSPEND_ANTIGRAVITY_WHEN_BUSY: bool = _cfg_bool("SUSPEND_ANTIGRAVITY_WHEN_BUSY", default=True)
ANTIGRAVITY_BUSY_LS_THRESHOLD: int = max(2, _cfg_int("ANTIGRAVITY_BUSY_LS_THRESHOLD", default=3))
MAX_ANTIGRAVITY_SESSIONS: int = max(100, _cfg_int("MAX_ANTIGRAVITY_SESSIONS", default=500))
ANTIGRAVITY_SCAN_INTERVAL_SEC: int = max(15, _cfg_int("ANTIGRAVITY_SCAN_INTERVAL_SEC", default=120))
MAX_ANTIGRAVITY_DIRS_PER_SCAN: int = max(50, _cfg_int("MAX_ANTIGRAVITY_DIRS_PER_SCAN", default=400))

# Codex session / Claude transcript configuration
CODEX_SESSIONS: Path = Path.home() / ".codex" / "sessions"
CODEX_SESSION_SCAN_INTERVAL_SEC: int = max(10, _cfg_int("CODEX_SESSION_SCAN_INTERVAL_SEC", default=90))
MAX_CODEX_SESSION_FILES_PER_SCAN: int = max(100, _cfg_int("MAX_CODEX_SESSION_FILES_PER_SCAN", default=1200))
CLAUDE_TRANSCRIPTS_DIR: Path = Path.home() / ".claude" / "transcripts"
# Skip transcript files older than this many days on first startup (avoid history replay).
CLAUDE_TRANSCRIPTS_LOOKBACK_DAYS: int = _cfg_int("TRANSCRIPTS_LOOKBACK_DAYS", default=7)
CLAUDE_TRANSCRIPT_SCAN_INTERVAL_SEC: int = max(30, _cfg_int("CLAUDE_TRANSCRIPT_SCAN_INTERVAL_SEC", default=180))
MAX_CLAUDE_TRANSCRIPT_FILES_PER_POLL: int = max(50, _cfg_int("MAX_CLAUDE_TRANSCRIPT_FILES_PER_POLL", default=500))

# JSONL and shell source definitions
# Each entry maps a logical source name to one or more candidate Path objects.
# The first existing path wins; sources are re-evaluated every 120 seconds.
_HOME = Path.home()
_IPT_KEYS = ["input", "prompt", "text"]  # common sid/text keys for generic tools
_SID_IPT = ["session_id", "sessionId", "id"]


def _ipt_src(path: Path) -> dict[str, Any]:
    return {"path": path, "sid_keys": _SID_IPT, "text_keys": _IPT_KEYS}


JSONL_SOURCES: dict[str, list[dict[str, Any]]] = {
    "claude_code": [
        {
            "path": _HOME / ".claude" / "history.jsonl",
            "sid_keys": ["sessionId", "session_id"],
            "text_keys": ["display", "text", "input", "prompt"],
        },
    ],
    "codex_history": [
        {
            "path": _HOME / ".codex" / "history.jsonl",
            "sid_keys": ["session_id", "sessionId", "id"],
            "text_keys": ["text", "input", "prompt"],
        },
    ],
    "opencode": [
        _ipt_src(_HOME / ".local" / "state" / "opencode" / "prompt-history.jsonl"),
        _ipt_src(_HOME / ".config" / "opencode" / "prompt-history.jsonl"),
        _ipt_src(_HOME / ".opencode" / "prompt-history.jsonl"),
    ],
    "kilo": [
        _ipt_src(_HOME / ".local" / "state" / "kilo" / "prompt-history.jsonl"),
        _ipt_src(_HOME / ".config" / "kilo" / "prompt-history.jsonl"),
    ],
}

SHELL_SOURCES: dict[str, list[Path]] = {
    "shell_zsh": [_HOME / ".zsh_history"],
    "shell_bash": [_HOME / ".bash_history"],
}

# Maps each JSONL source name to its enable flag so refresh_sources() can skip
# disabled monitors without hardcoding names.
SOURCE_MONITOR_FLAGS: dict[str, bool] = {
    "claude_code": ENABLE_CLAUDE_HISTORY_MONITOR,
    "codex_history": ENABLE_CODEX_HISTORY_MONITOR,
    "opencode": ENABLE_OPENCODE_MONITOR,
    "kilo": ENABLE_KILO_MONITOR,
}

# Text sanitisation

# zsh extended_history format: ": <timestamp>:<elapsed>;<command>"
_SHELL_LINE_RE: re.Pattern[str] = re.compile(r"^:\s*(\d+):\d+;(.*)$")

# Commands that are noisy and carry no context value.
_IGNORE_SHELL_CMD_PREFIXES = ("history", "fc ")

# Ordered list of (pattern, replacement) pairs applied during sanitisation.
# Compiled once at startup for efficiency.
_SECRET_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(api[_-]?key\s*[=:]\s*)([^\s\"']+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(token\s*[=:]\s*)([^\s\"']+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(password\s*[=:]\s*)([^\s\"']+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(secret\s*[=:]\s*)([^\s\"']+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(--api-key\s+)([^\s]+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(--token\s+)([^\s]+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(Authorization\s*:\s*Bearer\s+)([^\s\"']+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "sk-***"),
    (re.compile(r"\bsk-proj-[A-Za-z0-9_-]{16,}\b"), "sk-proj-***"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "ghp_***"),
    (re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"), "gho_***"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"), "AIza***"),
    (re.compile(r"\bxox[bprs]-[A-Za-z0-9\-]{10,}\b"), "xox?-***"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{12,}\b"), "AKIA***"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "***PEM_KEY_REDACTED***",
    ),
]

# Graceful shutdown — shared flag set by signal handlers

_shutdown: bool = False


def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown
    logger.info("Received signal %s — initiating graceful shutdown.", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# Single-instance lock


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* names a live process on this host."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _release_single_instance_lock() -> None:
    """Remove the PID lock file; registered via atexit."""
    global _LOCK_FD
    try:
        if _LOCK_FD is not None:
            os.close(_LOCK_FD)
            _LOCK_FD = None
    except OSError:
        pass
    with contextlib.suppress(OSError):
        LOCK_FILE.unlink(missing_ok=True)


def _acquire_single_instance_lock() -> bool:
    """Atomically create the PID lock file; removes stale lock and retries once."""
    global _LOCK_FD
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    for _ in range(2):
        try:
            _LOCK_FD = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(_LOCK_FD, str(os.getpid()).encode("utf-8"))
            os.fsync(_LOCK_FD)
            atexit.register(_release_single_instance_lock)
            return True
        except FileExistsError:
            try:
                raw = LOCK_FILE.read_text(encoding="utf-8").strip()
                pid = int(raw) if raw else 0
            except (OSError, ValueError):
                pid = 0

            if pid > 0 and _pid_alive(pid):
                logger.error("Another ContextGO daemon instance is already running (pid=%s).", pid)
                return False

            # Stale lock — remove and retry.
            with contextlib.suppress(OSError):
                LOCK_FILE.unlink(missing_ok=True)
        except OSError as exc:
            logger.error("Failed to acquire daemon lock: %s", exc)
            return False

    logger.error("Failed to acquire daemon lock after stale-lock cleanup.")
    return False


# System helpers


def _count_antigravity_language_servers() -> int:
    """Return the number of running Gemini language-server processes (0 on error)."""
    try:
        proc = subprocess.run(
            ["pgrep", "-f", "language_server_macos_arm"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if proc.returncode not in (0, 1):
            return 0
        return sum(1 for ln in (proc.stdout or "").splitlines() if ln.strip())
    except (OSError, subprocess.TimeoutExpired):
        return 0


# Glob cache helpers


def _refresh_glob_cache(
    pattern: str,
    max_results: int,
    last_refresh: float,
    interval_sec: int,
    cached: list[Path],
    error_context: str,
) -> tuple[list[Path], float, bool]:
    """Refresh a glob result list if the interval has elapsed.

    Returns (file_list, new_last_refresh, had_error).  On OSError the previous
    cache is preserved and had_error is True.
    """
    now = time.time()
    if cached and now - last_refresh < interval_sec:
        return cached, last_refresh, False

    try:
        results = [Path(p) for p in _glob.glob(pattern, recursive=True)]
        if len(results) > max_results:
            results.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
            results = results[:max_results]
        logger.debug("%s cache refreshed: %d entries.", error_context, len(results))
        return results, now, False
    except OSError as exc:
        logger.error("glob %s: %s", error_context, exc)
        return cached, last_refresh, True


# SessionTracker


class SessionTracker:
    """Tracks open AI / shell sessions, manages file cursors, and exports idle
    sessions to local storage (and optionally a remote endpoint).
    """

    def __init__(self) -> None:
        # In-flight sessions: sid -> metadata dict
        self.sessions: dict[str, dict[str, Any]] = {}

        # File-read cursors: cursor_key -> (inode, byte_offset)
        self.file_cursors: dict[str, tuple[int, int]] = {}

        # Antigravity brain sessions: sid -> metadata dict
        self.antigravity_sessions: dict[str, dict[str, Any]] = {}

        # Active source descriptors discovered on disk
        self.active_jsonl: dict[str, dict[str, Any]] = {}
        self.active_shell: dict[str, Path] = {}

        # Internal timers and counters
        self._last_heartbeat: float = time.time()
        self._last_source_refresh: float = 0.0
        self._last_pending_retry: float = 0.0
        self._last_codex_scan: float = 0.0
        self._last_claude_transcript_scan: float = 0.0
        self._last_antigravity_scan: float = 0.0
        self._last_antigravity_busy_log: float = 0.0
        self._last_index_sync: float = 0.0
        self._last_activity_ts: float | None = None

        self._export_count: int = 0
        self._error_count: int = 0
        self._index_dirty: bool = False

        # Cached glob results (refreshed on interval to amortise filesystem cost)
        self._cached_codex_session_files: list[Path] = []
        self._cached_claude_transcript_files: list[Path] = []
        self._cached_antigravity_dirs: list[Path] = []

        # Optional HTTP client for remote sync
        self._http_client: Any = None
        if ENABLE_REMOTE_SYNC and _HTTPX_AVAILABLE:
            try:
                self._http_client = _httpx.Client(
                    timeout=EXPORT_HTTP_TIMEOUT_SEC,
                    trust_env=False,
                    follow_redirects=False,
                )
            except Exception as exc:
                logger.warning("Failed to initialise HTTP client: %s", exc)

        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(PENDING_DIR, 0o700)

        self.refresh_sources(force=True)

    # Source discovery

    def refresh_sources(self, force: bool = False) -> None:
        """Re-scan the filesystem for enabled JSONL and shell source files (throttled to 120s)."""
        now = time.time()
        if not force and now - self._last_source_refresh < 120:
            return
        self._last_source_refresh = now

        # JSONL AI sources — pick the first existing candidate for each source.
        for source_name, candidates in JSONL_SOURCES.items():
            if not SOURCE_MONITOR_FLAGS.get(source_name, True):
                if source_name in self.active_jsonl:
                    logger.info("Source disabled by env: %s", source_name)
                    del self.active_jsonl[source_name]
                continue

            picked: dict[str, Any] | None = None
            for candidate in candidates:
                if candidate["path"].exists():
                    picked = candidate
                    break

            prev = self.active_jsonl.get(source_name)
            if picked is not None:
                self.active_jsonl[source_name] = picked
                if not prev or prev["path"] != picked["path"]:
                    cursor_key = self._cursor_key("jsonl", source_name, picked["path"])
                    self._set_cursor(cursor_key, picked["path"], picked["path"].stat().st_size)
                    logger.info("Source active: %s -> %s", source_name, picked["path"])
            elif source_name in self.active_jsonl:
                logger.info("Source offline: %s", source_name)
                del self.active_jsonl[source_name]

        # Shell sources
        if ENABLE_SHELL_MONITOR:
            for source_name, paths in SHELL_SOURCES.items():
                picked_path: Path | None = next((p for p in paths if p.exists()), None)
                prev_path = self.active_shell.get(source_name)

                if picked_path is not None:
                    self.active_shell[source_name] = picked_path
                    if prev_path != picked_path:
                        cursor_key = self._cursor_key("shell", source_name, picked_path)
                        self._set_cursor(cursor_key, picked_path, picked_path.stat().st_size)
                        logger.info("Source active: %s -> %s", source_name, picked_path)
                elif source_name in self.active_shell:
                    logger.info("Source offline: %s", source_name)
                    del self.active_shell[source_name]

    # Cursor management

    def _cursor_key(self, kind: str, source_name: str, path: Path | str) -> str:
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:10]
        return f"{kind}:{source_name}:{digest}"

    def _get_cursor(self, cursor_key: str, path: Path) -> int:
        """Return the current read offset for *path* (0 on rotation/truncation, size on first encounter)."""
        try:
            st = path.stat()
        except OSError:
            return 0

        prev = self.file_cursors.get(cursor_key)
        if prev is None:
            # First encounter — start at end to avoid replaying existing content.
            return st.st_size

        prev_inode, prev_offset = prev
        if st.st_ino != prev_inode or st.st_size < prev_offset:
            # File was rotated/replaced or truncated — restart from beginning.
            return 0
        return prev_offset

    def _set_cursor(self, cursor_key: str, path: Path | str, offset: int) -> None:
        try:
            inode = Path(path).stat().st_ino
        except OSError:
            return
        self.file_cursors[cursor_key] = (inode, offset)

    @staticmethod
    def _is_safe_source(path: Path) -> bool:
        """Return True if *path* is a regular file owned by the current user."""
        try:
            st = path.lstat()
        except OSError:
            return False
        if path.is_symlink():
            logger.warning("Skipping symlinked source: %s", path)
            return False
        if st.st_uid != os.getuid():
            logger.warning("Skipping source not owned by current user: %s (uid=%d)", path, st.st_uid)
            return False
        if not stat.S_ISREG(st.st_mode):
            logger.warning("Skipping non-regular source: %s", path)
            return False
        return True

    # Shared incremental-read helper

    def _tail_file(self, cursor_key: str, path: Path, error_label: str) -> tuple[int, list[str]] | None:
        """Return (cur_size, new_lines) if the file has grown, else None."""
        if not self._is_safe_source(path):
            return None
        try:
            cur_size = path.stat().st_size
        except OSError:
            return None
        last = self._get_cursor(cursor_key, path)
        if cur_size <= last:
            self._set_cursor(cursor_key, path, cur_size)
            return None
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                fh.seek(last)
                lines = list(fh)
            self._set_cursor(cursor_key, path, cur_size)
            return cur_size, lines
        except (OSError, UnicodeDecodeError) as exc:
            self._error_count += 1
            logger.error("%s: %s", error_label, exc)
            return None

    # Polling — JSONL sources

    def poll_jsonl_sources(self) -> None:
        """Read new lines from all active JSONL history files."""
        now = time.time()
        for source_name, source in self.active_jsonl.items():
            path: Path = source["path"]
            cursor_key = self._cursor_key("jsonl", source_name, path)
            result = self._tail_file(cursor_key, path, f"poll_jsonl_sources({source_name})")
            if result is None:
                continue
            _, lines = result
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = self._extract_sid(data, source.get("sid_keys", []), source_name)
                text = self._sanitize_text(self._extract_text(data, source.get("text_keys", [])))
                if text:
                    self._upsert_session(sid, source_name, text, now)

    # Polling — shell history

    def poll_shell_sources(self) -> None:
        """Read new lines from active shell history files."""
        if not ENABLE_SHELL_MONITOR:
            return
        now = time.time()
        for source_name, path in self.active_shell.items():
            cursor_key = self._cursor_key("shell", source_name, path)
            result = self._tail_file(cursor_key, path, f"poll_shell_sources({source_name})")
            if result is None:
                continue
            _, lines = result
            for line in lines:
                parsed = self._parse_shell_line(source_name, line)
                if parsed is not None:
                    sid, text = parsed
                    self._upsert_session(sid, source_name, text, now)

    # Polling — Codex session files

    def poll_codex_sessions(self) -> None:
        """Tail Codex session JSONL files under ~/.codex/sessions/."""
        if not ENABLE_CODEX_SESSION_MONITOR or not CODEX_SESSIONS.is_dir():
            return

        now = time.time()
        self._cached_codex_session_files, self._last_codex_scan, _err = _refresh_glob_cache(
            pattern=str(CODEX_SESSIONS / "**" / "*.jsonl"),
            max_results=MAX_CODEX_SESSION_FILES_PER_SCAN,
            last_refresh=self._last_codex_scan,
            interval_sec=CODEX_SESSION_SCAN_INTERVAL_SEC,
            cached=self._cached_codex_session_files,
            error_context="codex_sessions",
        )
        if _err:
            self._error_count += 1

        for path in self._cached_codex_session_files:
            if not self._is_safe_source(path):
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            # Skip files that have not been touched in the last hour.
            if mtime < now - 3600:
                continue

            cursor_key = self._cursor_key("codex_session", "codex_session", path)
            result = self._tail_file(cursor_key, path, f"poll_codex_sessions({path})")
            if result is None:
                continue
            _, lines = result
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("type") != "response_item":
                    continue
                payload = data.get("payload", {})
                ptype = payload.get("type")
                text = ""
                if ptype == "message":
                    texts = [c.get("text", "") for c in payload.get("content", []) if c.get("type") == "output_text"]
                    text = "\n".join(t for t in texts if t)
                elif ptype == "reasoning":
                    text = payload.get("text", "")
                text = self._sanitize_text(text)
                if text:
                    self._upsert_session(path.name, "codex_session", text, now)

    # Polling — Claude transcript files

    def poll_claude_transcripts(self) -> None:
        """Scan ~/.claude/transcripts/ses_*.jsonl; indexes user/assistant/human messages."""
        if not ENABLE_CLAUDE_TRANSCRIPTS_MONITOR or not CLAUDE_TRANSCRIPTS_DIR.is_dir():
            return

        now = time.time()
        lookback_cutoff = now - CLAUDE_TRANSCRIPTS_LOOKBACK_DAYS * 86400

        self._cached_claude_transcript_files, self._last_claude_transcript_scan, _err = _refresh_glob_cache(
            pattern=str(CLAUDE_TRANSCRIPTS_DIR / "**" / "ses_*.jsonl"),
            max_results=MAX_CLAUDE_TRANSCRIPT_FILES_PER_POLL,
            last_refresh=self._last_claude_transcript_scan,
            interval_sec=CLAUDE_TRANSCRIPT_SCAN_INTERVAL_SEC,
            cached=self._cached_claude_transcript_files,
            error_context="claude_transcripts",
        )
        if _err:
            self._error_count += 1

        for path in self._cached_claude_transcript_files:
            if not self._is_safe_source(path):
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue

            cursor_key = self._cursor_key("claude_transcripts", "claude_transcripts", path)

            # First encounter: apply lookback window to avoid replaying old history.
            if cursor_key not in self.file_cursors:
                try:
                    fsize = path.stat().st_size
                except OSError:
                    fsize = 0
                if mtime < lookback_cutoff:
                    self._set_cursor(cursor_key, path, fsize)
                    continue
                try:
                    self.file_cursors[cursor_key] = (path.stat().st_ino, 0)
                except OSError:
                    continue

            result = self._tail_file(cursor_key, path, f"poll_claude_transcripts({path})")
            if result is None:
                continue
            _, lines = result
            messages_added = 0
            for raw in lines:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg_type = data.get("type", "")
                if msg_type not in ("user", "assistant", "human"):
                    continue
                content = data.get("content", "")
                if isinstance(content, str):
                    text = content.strip()
                elif isinstance(content, list):
                    parts = [
                        block.get("text", "").strip()
                        for block in content
                        if isinstance(block, dict)
                        and block.get("type") == "text"
                        and isinstance(block.get("text"), str)
                    ]
                    text = " ".join(p for p in parts if p)
                elif isinstance(content, dict):
                    raw_text = content.get("text", "")
                    text = raw_text.strip() if isinstance(raw_text, str) else ""
                else:
                    text = ""
                text = self._sanitize_text(text)
                if text:
                    sid = self._build_transcript_sid(path)
                    self._upsert_session(sid, "claude_transcripts", text, now)
                    messages_added += 1
            if messages_added:
                logger.debug("claude_transcripts: +%d msgs from %s", messages_added, path.name)

    # Polling — Antigravity (Gemini) brain

    def poll_antigravity(self) -> None:
        """Export Antigravity brain docs when stable (final_only) or on every change (live)."""
        if not ENABLE_ANTIGRAVITY_MONITOR:
            return

        if SUSPEND_ANTIGRAVITY_WHEN_BUSY:
            ls_count = _count_antigravity_language_servers()
            if ls_count >= ANTIGRAVITY_BUSY_LS_THRESHOLD:
                now = time.time()
                if now - self._last_antigravity_busy_log >= 180:
                    logger.info(
                        "poll_antigravity skipped: language_server_macos_arm=%s threshold=%s",
                        ls_count,
                        ANTIGRAVITY_BUSY_LS_THRESHOLD,
                    )
                    self._last_antigravity_busy_log = now
                return

        if not ANTIGRAVITY_BRAIN.is_dir():
            return

        now = time.time()
        self._cached_antigravity_dirs, self._last_antigravity_scan, _err = _refresh_glob_cache(
            pattern=str(ANTIGRAVITY_BRAIN / "*-*-*-*-*"),
            max_results=MAX_ANTIGRAVITY_DIRS_PER_SCAN,
            last_refresh=self._last_antigravity_scan,
            interval_sec=ANTIGRAVITY_SCAN_INTERVAL_SEC,
            cached=self._cached_antigravity_dirs,
            error_context="antigravity_dirs",
        )
        if _err:
            self._error_count += 1

        # Document types to consider, ordered by preference.
        brain_docs = (
            ["walkthrough.md", "task.md", "implementation_plan.md"]
            if ANTIGRAVITY_INGEST_MODE == "final_only"
            else ["walkthrough.md", "implementation_plan.md"]
        )

        seen_sids: set[str] = set()

        for sdir in self._cached_antigravity_dirs:
            sid = sdir.name
            seen_sids.add(sid)

            # Pick the most recently modified document in this session directory.
            wt: Path | None = None
            latest_mtime = 0.0
            for doc in brain_docs:
                candidate = sdir / doc
                if candidate.exists():
                    try:
                        m = candidate.stat().st_mtime
                    except OSError:
                        m = 0.0
                    if m > latest_mtime:
                        latest_mtime = m
                        wt = candidate
            if wt is None:
                continue

            try:
                mtime = wt.stat().st_mtime
            except OSError:
                continue

            if sid not in self.antigravity_sessions:
                self.antigravity_sessions[sid] = {
                    "mtime": mtime,
                    "path": wt,
                    "last_change": now,
                    "exported_mtime": mtime,
                }
                continue

            meta = self.antigravity_sessions[sid]
            prev_mtime = float(meta.get("mtime", 0.0))
            path_changed = wt != meta.get("path")

            if path_changed or mtime > prev_mtime:
                meta["mtime"] = mtime
                meta["path"] = wt
                meta["last_change"] = now
                if ANTIGRAVITY_INGEST_MODE == "final_only":
                    continue

            if ANTIGRAVITY_INGEST_MODE == "final_only":
                exported_mtime = float(meta.get("exported_mtime", 0.0))
                if mtime <= exported_mtime:
                    continue
                last_change = float(meta.get("last_change", now))
                if now - last_change < ANTIGRAVITY_QUIET_SEC:
                    continue
                try:
                    if wt.stat().st_size < ANTIGRAVITY_MIN_DOC_BYTES:
                        continue
                except OSError:
                    continue

            try:
                content = self._sanitize_text(wt.read_text(encoding="utf-8", errors="replace")[:50_000])
                if content:
                    export_data: dict[str, Any] = {
                        "source": "antigravity",
                        "messages": [content],
                        "last_seen": now,
                    }
                    self._export(sid, export_data, title_prefix="Antigravity Walkthrough")
                    meta["exported_mtime"] = mtime
            except (OSError, UnicodeDecodeError) as exc:
                self._error_count += 1
                logger.error("poll_antigravity(%s): %s", sid, exc)

        # Evict oldest unseen entries when the tracking map exceeds its limit.
        if len(self.antigravity_sessions) > MAX_ANTIGRAVITY_SESSIONS:
            stale = [
                (sid, meta.get("mtime", 0.0)) for sid, meta in self.antigravity_sessions.items() if sid not in seen_sids
            ]
            stale.sort(key=lambda x: x[1])
            remove_n = len(self.antigravity_sessions) - MAX_ANTIGRAVITY_SESSIONS
            for sid, _ in stale[:remove_n]:
                self.antigravity_sessions.pop(sid, None)

    # Parsing helpers

    def _extract_sid(self, data: dict[str, Any], sid_keys: list[str], source_name: str) -> str:
        for key in sid_keys:
            val = data.get(key)
            if isinstance(val, (str, int)) and str(val).strip():
                return str(val)
        return f"{source_name}_default"

    def _extract_text(self, data: dict[str, Any], text_keys: list[str]) -> str:
        for key in text_keys:
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

        # Fallback for structured part arrays (e.g., OpenCode format).
        parts = data.get("parts")
        if isinstance(parts, list):
            text_parts: list[str] = [
                part.get("text", "").strip()
                for part in parts
                if isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
                and part.get("text", "").strip()
            ]
            if text_parts:
                prefix = data.get("input")
                if isinstance(prefix, str) and prefix.strip():
                    return prefix.strip() + "\n" + "\n".join(text_parts)
                return "\n".join(text_parts)

        return ""

    def _parse_shell_line(self, source_name: str, raw_line: str) -> tuple[str, str] | None:
        """Parse one shell history line.

        Handles both plain commands and zsh extended_history format
        (": <timestamp>:<elapsed>;<command>").  Returns (session_id, command)
        or None if the line should be skipped.
        """
        line = raw_line.strip()
        if not line:
            return None

        ts = int(time.time())
        cmd = line

        match = _SHELL_LINE_RE.match(line)
        if match:
            ts = int(match.group(1))
            cmd = match.group(2).strip()

        if not cmd or cmd.lower().startswith(_IGNORE_SHELL_CMD_PREFIXES):
            return None

        cmd = self._sanitize_text(cmd)
        if not cmd:
            return None

        day = datetime.fromtimestamp(ts).strftime("%Y%m%d")
        return f"{source_name}_{day}", cmd

    def _sanitize_text(self, text: str) -> str:
        """Strip private blocks and redact secrets; truncate to 4000 characters."""
        if not text:
            return ""
        out = strip_private_blocks(text).strip()
        for pattern, repl in _SECRET_REPLACEMENTS:
            out = pattern.sub(repl, out)
        return out[:4000]

    @staticmethod
    def _sanitize_filename_part(raw: str, default: str = "session") -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", (raw or "").strip()).strip("._-")
        return safe[:64] if safe else default

    def _build_transcript_sid(self, path: Path) -> str:
        try:
            rel = path.relative_to(CLAUDE_TRANSCRIPTS_DIR).as_posix()
        except ValueError:
            rel = path.name
        base = self._sanitize_filename_part(path.stem.replace(".jsonl", ""))
        digest = hashlib.sha256(rel.encode("utf-8", errors="ignore")).hexdigest()[:10]
        return f"{base}_{digest}"

    # Session management

    def _upsert_session(self, sid: str, source: str, text: str, now: float) -> None:
        if sid not in self.sessions:
            if len(self.sessions) >= MAX_TRACKED_SESSIONS:
                self._evict_oldest()
            self.sessions[sid] = {
                "last_seen": now,
                "messages": [],
                "exported": False,
                "source": source,
                "created": now,
                "last_hash": "",
            }

        sess = self.sessions[sid]
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        if digest == sess.get("last_hash"):
            return

        sess["messages"].append(text)
        sess["last_hash"] = digest
        sess["last_seen"] = now
        self._last_activity_ts = now

        if len(sess["messages"]) > MAX_MESSAGES_PER_SESSION:
            sess["messages"] = sess["messages"][-200:]

    def _evict_oldest(self) -> None:
        """Remove the oldest session from the tracking map to make room."""
        exported = [(k, v) for k, v in self.sessions.items() if v["exported"]]
        if exported:
            del self.sessions[min(exported, key=lambda x: x[1]["last_seen"])[0]]
            return
        del self.sessions[min(self.sessions, key=lambda k: self.sessions[k]["last_seen"])]

    def check_and_export_idle(self) -> None:
        """Export idle sessions and evict exported ones past SESSION_TTL_SEC."""
        now = time.time()
        to_remove: list[str] = []

        for sid, data in self.sessions.items():
            if data["exported"]:
                if now - data["last_seen"] > SESSION_TTL_SEC:
                    to_remove.append(sid)
                continue

            if now - data["last_seen"] <= IDLE_TIMEOUT_SEC:
                continue

            source = data["source"]
            min_messages = 4 if source.startswith("shell_") else 2
            if len(data["messages"]) >= min_messages:
                self._export(sid, data)
                data["exported"] = True
            elif now - data.get("created", 0) > SESSION_TTL_SEC:
                data["exported"] = True

        for sid in to_remove:
            del self.sessions[sid]

    def cleanup_cursors(self) -> None:
        """Evict the oldest third of cursor entries when the map is over-full."""
        if len(self.file_cursors) <= MAX_FILE_CURSORS:
            return
        keys = sorted(self.file_cursors.keys())
        remove_n = max(1, len(keys) // 3)
        for key in keys[:remove_n]:
            del self.file_cursors[key]
        logger.info("Evicted %d stale file cursors.", remove_n)

    def maybe_sync_index(self, force: bool = False) -> None:
        """Flush the memory index to storage if dirty and the min interval has elapsed."""
        if not self._index_dirty and not force:
            return
        now = time.time()
        if not force and now - self._last_index_sync < INDEX_SYNC_MIN_INTERVAL_SEC:
            return
        try:
            sync_index_from_storage()
            self._index_dirty = False
            self._last_index_sync = now
        except OSError as exc:
            self._error_count += 1
            logger.warning("sync_index_from_storage failed: %s", exc)

    # Export — local write and optional remote push

    def _export(self, sid: str, data: dict[str, Any], title_prefix: str = "") -> bool:
        """Write session data locally and optionally push to remote (queues on failure)."""
        source = data["source"]
        messages = data["messages"]
        content = "\n- ".join(msg[:2000] for msg in messages[-60:])

        prefix = title_prefix or f"Live {source} Session"
        title = f"{prefix} {sid[:12]}"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        local_dir = LOCAL_STORAGE_ROOT / "resources" / "shared" / "history"
        local_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(local_dir, 0o700)

        source_safe = self._sanitize_filename_part(source, default="source")
        sid_safe = self._sanitize_filename_part(sid, default="sid")
        file_path = local_dir / f"{source_safe}_{ts}_{sid_safe[:24]}.md"

        formatted = (
            f"# {title}\n\n"
            f"Tags: {source}, live_sync, unified_context\n"
            f"Date: {datetime.now().isoformat()}\n\n"
            f"## Content\n- {content}\n"
        )

        try:
            fd = os.open(str(file_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, formatted.encode("utf-8"))
            finally:
                os.close(fd)
            self._index_dirty = True
            self.maybe_sync_index()
        except OSError as exc:
            logger.error("Failed to write local export %s: %s", file_path, exc)
            return False

        if not self._http_client:
            if not ENABLE_REMOTE_SYNC:
                self._export_count += 1
                return True
            self._queue_pending(file_path, formatted)
            return False

        payload = {
            "path": str(file_path),
            "target": REMOTE_HISTORY_TARGET,
            "reason": f"Real-time sync of {source} session",
            "instruction": f"Index real-time completed {source} conversation: {title}",
        }
        try:
            resp = self._http_client.post(
                REMOTE_RESOURCE_ENDPOINT,
                json=payload,
                timeout=EXPORT_HTTP_TIMEOUT_SEC,
            )
            if resp.status_code < 300:
                self._export_count += 1
                logger.info(
                    "Synced %s session %s to remote (HTTP %d).",
                    source,
                    sid[:12],
                    resp.status_code,
                )
                self._retry_pending()
                return True
            logger.warning("Remote sync returned HTTP %d for %s %s.", resp.status_code, source, sid[:12])
        except Exception as exc:
            logger.warning("Remote unreachable — queuing pending export: %s", exc)

        self._queue_pending(file_path, formatted)
        return False

    def _queue_pending(self, file_path: Path, formatted: str) -> None:
        """Write *formatted* to the pending queue for later retry."""
        pending_path = PENDING_DIR / file_path.name
        try:
            self._prune_pending_files()
            fd = os.open(str(pending_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, formatted.encode("utf-8"))
            finally:
                os.close(fd)
            logger.info("Queued pending export: %s", pending_path.name)
        except OSError as exc:
            logger.error("Failed to write pending export: %s", exc)

    @staticmethod
    def _pending_mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    def _retry_pending(self) -> None:
        """Attempt to flush the oldest pending exports to the remote.

        Processes at most 8 files per call to avoid blocking the main loop.
        Stops on the first HTTP failure to preserve ordering.
        """
        if not self._http_client:
            return

        pending = sorted(
            (p for p in PENDING_DIR.glob("*.md") if p.is_file()),
            key=self._pending_mtime,
        )
        if not pending:
            return

        self._last_pending_retry = time.time()
        for pf in pending[:8]:
            payload = {
                "path": str(pf),
                "target": REMOTE_HISTORY_TARGET,
                "reason": "Retry pending sync",
                "instruction": f"Index pending conversation: {pf.stem}",
            }
            try:
                resp = self._http_client.post(
                    REMOTE_RESOURCE_ENDPOINT,
                    json=payload,
                    timeout=PENDING_HTTP_TIMEOUT_SEC,
                )
                if resp.status_code < 300:
                    pf.unlink(missing_ok=True)
                    logger.info("Pending retry succeeded: %s", pf.name)
                else:
                    logger.warning(
                        "Pending retry got HTTP %d for %s — stopping batch.",
                        resp.status_code,
                        pf.name,
                    )
                    break
            except Exception as exc:
                logger.warning("Pending retry failed: %s — stopping batch.", exc)
                break

    def _prune_pending_files(self) -> None:
        """Remove the oldest pending files when the queue exceeds MAX_PENDING_FILES."""
        try:
            files = [p for p in PENDING_DIR.glob("*.md") if p.is_file()]
        except OSError:
            return
        if len(files) < MAX_PENDING_FILES:
            return
        files.sort(key=self._pending_mtime)
        for old in files[: len(files) - MAX_PENDING_FILES + 1]:
            with contextlib.suppress(OSError):
                old.unlink(missing_ok=True)

    def maybe_retry_pending(self) -> None:
        """Trigger a pending-queue flush if the retry interval has elapsed."""
        if not PENDING_DIR.exists():
            return
        try:
            has_pending = any(PENDING_DIR.glob("*.md"))
        except OSError:
            has_pending = False
        if not has_pending:
            return
        if time.time() - self._last_pending_retry < PENDING_RETRY_INTERVAL_SEC:
            return
        self._retry_pending()

    # Adaptive sleep interval

    def next_sleep_interval(self) -> int:
        """Return adaptive sleep seconds: night-mode expansion, idle cap, fast-poll reduction."""
        current_hour = datetime.now().hour
        start_h = NIGHT_POLL_START_HOUR % 24
        end_h = NIGHT_POLL_END_HOUR % 24

        if start_h > end_h:
            is_night = current_hour >= start_h or current_hour < end_h
        else:
            is_night = start_h <= current_hour < end_h

        has_pending_sessions = any(not v.get("exported") for v in self.sessions.values())
        try:
            has_pending_files = PENDING_DIR.exists() and any(PENDING_DIR.glob("*.md"))
        except OSError:
            has_pending_files = False

        if is_night and not has_pending_sessions and not has_pending_files:
            return max(1, NIGHT_POLL_INTERVAL_SEC)

        if not has_pending_sessions and not has_pending_files:
            return min(POLL_INTERVAL_SEC * 3, IDLE_SLEEP_CAP_SEC)

        sleep_s = max(1, POLL_INTERVAL_SEC)

        if has_pending_files:
            sleep_s = min(sleep_s, FAST_POLL_INTERVAL_SEC)

        now = time.time()
        nearest_due: float | None = None
        for data in self.sessions.values():
            if data.get("exported"):
                continue
            remaining = IDLE_TIMEOUT_SEC - (now - data.get("last_seen", now))
            if nearest_due is None or remaining < nearest_due:
                nearest_due = remaining

        if nearest_due is not None:
            if nearest_due <= FAST_POLL_INTERVAL_SEC:
                sleep_s = min(sleep_s, FAST_POLL_INTERVAL_SEC)
            elif nearest_due < sleep_s:
                sleep_s = min(sleep_s, max(1, int(nearest_due)))

        recent_activity_window = max(15, FAST_POLL_INTERVAL_SEC * 4)
        if self._last_activity_ts is not None and (now - self._last_activity_ts) < recent_activity_window:
            sleep_s = min(sleep_s, FAST_POLL_INTERVAL_SEC)

        return max(1, sleep_s)

    # Periodic heartbeat

    def heartbeat(self) -> None:
        """Log a structured status line at HEARTBEAT_INTERVAL_SEC intervals."""
        now = time.time()
        if now - self._last_heartbeat < HEARTBEAT_INTERVAL_SEC:
            return
        self._last_heartbeat = now

        mem_mb = -1.0
        if _resource_mod is not None:
            try:
                rss = _resource_mod.getrusage(_resource_mod.RUSAGE_SELF).ru_maxrss
                mem_mb = rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024
            except (OSError, ValueError):
                pass

        pending_count = sum(1 for _ in PENDING_DIR.glob("*.md")) if PENDING_DIR.exists() else 0
        active_sources = list(self.active_jsonl.keys()) + list(self.active_shell.keys())

        logger.info(
            "heartbeat sessions=%d cursors=%d exported=%d errors=%d pending=%d mem_mb=%.1f active_sources=%s",
            len(self.sessions),
            len(self.file_cursors),
            self._export_count,
            self._error_count,
            pending_count,
            mem_mb,
            ",".join(active_sources) if active_sources else "none",
        )


# Entry point


def main() -> None:
    """Start the ContextGO sync daemon."""
    os.umask(0o077)

    if not _acquire_single_instance_lock():
        raise SystemExit(1)

    def _on_off(flag: bool) -> str:
        return "on" if flag else "off"

    logger.info(
        "ContextGO daemon starting. remote_sync=%s remote_url=%s"
        " idle=%ds poll=%ds fast_poll=%ds heartbeat=%ds cycle_budget=%ds"
        " shell=%s claude_history=%s codex_history=%s opencode=%s kilo=%s"
        " codex_session=%s claude_transcripts=%s antigravity=%s"
        " ag_ingest=%s ag_quiet=%ds ag_min=%dB",
        _on_off(ENABLE_REMOTE_SYNC),
        REMOTE_SYNC_URL,
        IDLE_TIMEOUT_SEC,
        POLL_INTERVAL_SEC,
        FAST_POLL_INTERVAL_SEC,
        HEARTBEAT_INTERVAL_SEC,
        CYCLE_BUDGET_SEC,
        _on_off(ENABLE_SHELL_MONITOR),
        _on_off(ENABLE_CLAUDE_HISTORY_MONITOR),
        _on_off(ENABLE_CODEX_HISTORY_MONITOR),
        _on_off(ENABLE_OPENCODE_MONITOR),
        _on_off(ENABLE_KILO_MONITOR),
        _on_off(ENABLE_CODEX_SESSION_MONITOR),
        _on_off(ENABLE_CLAUDE_TRANSCRIPTS_MONITOR),
        _on_off(ENABLE_ANTIGRAVITY_MONITOR),
        ANTIGRAVITY_INGEST_MODE,
        ANTIGRAVITY_QUIET_SEC,
        ANTIGRAVITY_MIN_DOC_BYTES,
    )

    tracker = SessionTracker()
    cycle = 0
    consecutive_errors = 0

    while not _shutdown:
        had_error = False
        try:
            # Poll cycle
            # Each step checks whether it still has budget before proceeding.
            # Higher-priority monitors (JSONL, shell) always run; lower-priority
            # monitors (Codex sessions, Claude transcripts, Antigravity) are
            # skipped when the cycle has already consumed its budget.
            cycle_started = time.monotonic()
            budget_deadline = cycle_started + CYCLE_BUDGET_SEC

            tracker.refresh_sources()
            tracker.poll_jsonl_sources()
            tracker.poll_shell_sources()

            if time.monotonic() < budget_deadline:
                tracker.poll_codex_sessions()
            if time.monotonic() < budget_deadline:
                tracker.poll_claude_transcripts()
            if time.monotonic() < budget_deadline:
                tracker.poll_antigravity()

            tracker.check_and_export_idle()
            tracker.maybe_sync_index()
            tracker.maybe_retry_pending()
            tracker.heartbeat()

            cycle += 1

            if cycle % 60 == 0:
                tracker.cleanup_cursors()
                tracker.maybe_sync_index(force=True)
                tracker.maybe_retry_pending()

        except Exception as exc:
            had_error = True
            logger.exception("Unhandled error in main loop: %s", exc)

        # Adaptive sleep with exponential back-off on repeated errors
        consecutive_errors = consecutive_errors + 1 if had_error else 0

        sleep_s = float(tracker.next_sleep_interval())

        if consecutive_errors > 0:
            sleep_s += min(float(ERROR_BACKOFF_MAX_SEC), float(2 ** min(consecutive_errors, 6)))

        if LOOP_JITTER_SEC > 0:
            sleep_s += random.uniform(0.0, LOOP_JITTER_SEC)

        time.sleep(max(1.0, sleep_s))

    # Graceful shutdown
    tracker.maybe_sync_index(force=True)
    if tracker._http_client is not None:
        with contextlib.suppress(Exception):
            tracker._http_client.close()
    logger.info("ContextGO daemon stopped. Total sessions exported: %d.", tracker._export_count)


if __name__ == "__main__":
    main()
