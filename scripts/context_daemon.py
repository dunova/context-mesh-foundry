#!/usr/bin/env python3
"""
ContextGO real-time sync daemon.

Goals:
- Global terminal coverage on one machine (CLI tools + shell history)
- Zero-touch background operation via launchd
- Safe long-running behavior (bounded memory, rotating logs, retries)
"""

import atexit
import glob
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
    from memory_index import strip_private_blocks, sync_index_from_storage
    from context_config import env_bool, env_float, env_int, env_str, storage_root
except ImportError:  # pragma: no cover - module import path compatibility
    from .memory_index import strip_private_blocks, sync_index_from_storage  # type: ignore[import-not-found]
    from .context_config import env_bool, env_float, env_int, env_str, storage_root  # type: ignore[import-not-found]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# 可选远程同步；默认主链不依赖远程服务
REMOTE_SYNC_URL = env_str("CONTEXTGO_REMOTE_URL", default="http://127.0.0.1:8090/api/v1")
REMOTE_RESOURCE_ENDPOINT = f"{REMOTE_SYNC_URL.rstrip('/')}/resources"
REMOTE_HISTORY_TARGET = "contextgo://resources/shared/history"

def _mesh_env_names(name: str) -> tuple[str]:
    return (f"CONTEXTGO_{name}",)


def mesh_env_bool(name: str, default: bool) -> bool:
    return env_bool(*_mesh_env_names(name), default=default)


def mesh_env_int(name: str, default: int, **kwargs: Any) -> int:
    return env_int(*_mesh_env_names(name), default=default, **kwargs)


def mesh_env_float(name: str, default: float, **kwargs: Any) -> float:
    return env_float(*_mesh_env_names(name), default=default, **kwargs)


def mesh_env_str(name: str, default: str) -> str:
    return env_str(*_mesh_env_names(name), default=default)

# Security: require HTTPS for non-localhost URLs to prevent MITM
_ov_host = REMOTE_SYNC_URL.split("://", 1)[-1].split("/", 1)[0].split(":")[0]
if _ov_host not in ("127.0.0.1", "localhost", "::1") and not REMOTE_SYNC_URL.startswith("https://"):
    print(f"FATAL: Remote sync URL must use https://. Got: {REMOTE_SYNC_URL}", file=sys.stderr)
    raise SystemExit(1)

LOCAL_STORAGE_ROOT = storage_root().expanduser()
PENDING_DIR = LOCAL_STORAGE_ROOT / "resources" / "shared" / "history" / ".pending"

# Security: verify storage root is not a symlink and is owned by current user
if LOCAL_STORAGE_ROOT.exists():
    _storage_stat = LOCAL_STORAGE_ROOT.lstat()
    if _storage_stat.st_uid != os.getuid():
        print(f"FATAL: {LOCAL_STORAGE_ROOT} is not owned by current user (uid={_storage_stat.st_uid})", file=sys.stderr)
        raise SystemExit(1)
    if LOCAL_STORAGE_ROOT.is_symlink():
        print(f"WARNING: {LOCAL_STORAGE_ROOT} is a symlink – following cautiously", file=sys.stderr)

LOG_DIR = LOCAL_STORAGE_ROOT / "logs"
DAEMON_LOG_NAME = "contextgo_daemon.log"
DAEMON_LOCK_NAME = "contextgo_daemon.lock"
LOGGER_NAME = "contextgo.daemon"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

CODEX_SESSIONS = str(Path.home() / ".codex" / "sessions")
ANTIGRAVITY_BRAIN = str(Path.home() / ".gemini" / "antigravity" / "brain")

# Claude / Antigravity / OpenClaw full-session transcripts
CLAUDE_TRANSCRIPTS_DIR = str(Path.home() / ".claude" / "transcripts")
# How many days back to index on first startup (avoid replay storm for old files)
CLAUDE_TRANSCRIPTS_LOOKBACK_DAYS = mesh_env_int("TRANSCRIPTS_LOOKBACK_DAYS", default=7)
# Night-mode low-power: quiet hours where poll expands to NIGHT_POLL_INTERVAL_SEC
NIGHT_POLL_START_HOUR = mesh_env_int("NIGHT_POLL_START_HOUR", default=23)
NIGHT_POLL_END_HOUR = mesh_env_int("NIGHT_POLL_END_HOUR", default=7)
NIGHT_POLL_INTERVAL_SEC = mesh_env_int("NIGHT_POLL_INTERVAL_SEC", default=600, minimum=1)

ENABLE_SHELL_MONITOR = mesh_env_bool("ENABLE_SHELL_MONITOR", default=True)
ENABLE_CLAUDE_HISTORY_MONITOR = mesh_env_bool("ENABLE_CLAUDE_HISTORY_MONITOR", default=True)
ENABLE_CODEX_HISTORY_MONITOR = mesh_env_bool("ENABLE_CODEX_HISTORY_MONITOR", default=True)
ENABLE_OPENCODE_MONITOR = mesh_env_bool("ENABLE_OPENCODE_MONITOR", default=False)
ENABLE_KILO_MONITOR = mesh_env_bool("ENABLE_KILO_MONITOR", default=False)
ENABLE_REMOTE_SYNC = mesh_env_bool("ENABLE_REMOTE_SYNC", default=False)
ENABLE_CODEX_SESSION_MONITOR = mesh_env_bool("ENABLE_CODEX_SESSION_MONITOR", default=True)
ENABLE_CLAUDE_TRANSCRIPTS_MONITOR = mesh_env_bool("ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", default=True)
ENABLE_ANTIGRAVITY_MONITOR = mesh_env_bool("ENABLE_ANTIGRAVITY_MONITOR", default=True)
IDLE_TIMEOUT_SEC = mesh_env_int("IDLE_TIMEOUT_SEC", default=300)
POLL_INTERVAL_SEC = mesh_env_int("POLL_INTERVAL_SEC", default=30, minimum=1)
IDLE_SLEEP_CAP_SEC = max(POLL_INTERVAL_SEC, mesh_env_int("IDLE_SLEEP_CAP_SEC", default=180))
HEARTBEAT_INTERVAL_SEC = mesh_env_int("HEARTBEAT_INTERVAL_SEC", default=600, minimum=10)
FAST_POLL_INTERVAL_SEC = mesh_env_int("FAST_POLL_INTERVAL_SEC", default=3, minimum=1)
PENDING_RETRY_INTERVAL_SEC = mesh_env_int("PENDING_RETRY_INTERVAL_SEC", default=60, minimum=5)
CYCLE_BUDGET_SEC = mesh_env_int("CYCLE_BUDGET_SEC", default=8, minimum=1)
ERROR_BACKOFF_MAX_SEC = mesh_env_int("ERROR_BACKOFF_MAX_SEC", default=30, minimum=2)
LOOP_JITTER_SEC = mesh_env_float("LOOP_JITTER_SEC", default=0.7, minimum=0.0)
INDEX_SYNC_MIN_INTERVAL_SEC = mesh_env_int("INDEX_SYNC_MIN_INTERVAL_SEC", default=20, minimum=5)
MAX_TRACKED_SESSIONS = mesh_env_int("MAX_TRACKED_SESSIONS", default=240)
MAX_FILE_CURSORS = mesh_env_int("MAX_FILE_CURSORS", default=800)
SESSION_TTL_SEC = mesh_env_int("SESSION_TTL_SEC", default=7200)
MAX_MESSAGES_PER_SESSION = mesh_env_int("MAX_MESSAGES_PER_SESSION", default=500)
EXPORT_HTTP_TIMEOUT_SEC = mesh_env_int("EXPORT_HTTP_TIMEOUT_SEC", default=30, minimum=5)
PENDING_HTTP_TIMEOUT_SEC = mesh_env_int("PENDING_HTTP_TIMEOUT_SEC", default=15, minimum=5)
MAX_CLAUDE_TRANSCRIPT_FILES_PER_POLL = max(
    50,
    mesh_env_int("MAX_CLAUDE_TRANSCRIPT_FILES_PER_POLL", default=500),
)
MAX_PENDING_FILES = max(200, mesh_env_int("MAX_PENDING_FILES", default=5000))
MAX_ANTIGRAVITY_SESSIONS = max(100, mesh_env_int("MAX_ANTIGRAVITY_SESSIONS", default=500))
CODEX_SESSION_SCAN_INTERVAL_SEC = max(10, mesh_env_int("CODEX_SESSION_SCAN_INTERVAL_SEC", default=90))
CLAUDE_TRANSCRIPT_SCAN_INTERVAL_SEC = max(
    30,
    mesh_env_int("CLAUDE_TRANSCRIPT_SCAN_INTERVAL_SEC", default=180),
)
ANTIGRAVITY_SCAN_INTERVAL_SEC = max(15, mesh_env_int("ANTIGRAVITY_SCAN_INTERVAL_SEC", default=120))
MAX_CODEX_SESSION_FILES_PER_SCAN = max(
    100,
    mesh_env_int("MAX_CODEX_SESSION_FILES_PER_SCAN", default=1200),
)
MAX_ANTIGRAVITY_DIRS_PER_SCAN = max(
    50,
    mesh_env_int("MAX_ANTIGRAVITY_DIRS_PER_SCAN", default=400),
)
SUSPEND_ANTIGRAVITY_WHEN_BUSY = mesh_env_bool("SUSPEND_ANTIGRAVITY_WHEN_BUSY", default=True)
ANTIGRAVITY_BUSY_LS_THRESHOLD = max(2, mesh_env_int("ANTIGRAVITY_BUSY_LS_THRESHOLD", default=3))
ANTIGRAVITY_INGEST_MODE = mesh_env_str("ANTIGRAVITY_INGEST_MODE", default="final_only").strip().lower()
if ANTIGRAVITY_INGEST_MODE not in {"final_only", "live"}:
    ANTIGRAVITY_INGEST_MODE = "final_only"
ANTIGRAVITY_QUIET_SEC = max(30, mesh_env_int("ANTIGRAVITY_QUIET_SEC", default=180))
ANTIGRAVITY_MIN_DOC_BYTES = max(120, mesh_env_int("ANTIGRAVITY_MIN_DOC_BYTES", default=400))

JSONL_SOURCES: dict[str, list[dict[str, Any]]] = {
    "claude_code": [
        {
            "path": str(Path.home() / ".claude" / "history.jsonl"),
            "sid_keys": ["sessionId", "session_id"],
            "text_keys": ["display", "text", "input", "prompt"],
        }
    ],
    "codex_history": [
        {
            "path": str(Path.home() / ".codex" / "history.jsonl"),
            "sid_keys": ["session_id", "sessionId", "id"],
            "text_keys": ["text", "input", "prompt"],
        }
    ],
    "opencode": [
        {
            "path": str(Path.home() / ".local" / "state" / "opencode" / "prompt-history.jsonl"),
            "sid_keys": ["session_id", "sessionId", "id"],
            "text_keys": ["input", "prompt", "text"],
        },
        {
            "path": str(Path.home() / ".config" / "opencode" / "prompt-history.jsonl"),
            "sid_keys": ["session_id", "sessionId", "id"],
            "text_keys": ["input", "prompt", "text"],
        },
        {
            "path": str(Path.home() / ".opencode" / "prompt-history.jsonl"),
            "sid_keys": ["session_id", "sessionId", "id"],
            "text_keys": ["input", "prompt", "text"],
        },
    ],
    "kilo": [
        {
            "path": str(Path.home() / ".local" / "state" / "kilo" / "prompt-history.jsonl"),
            "sid_keys": ["session_id", "sessionId", "id"],
            "text_keys": ["input", "prompt", "text"],
        },
        {
            "path": str(Path.home() / ".config" / "kilo" / "prompt-history.jsonl"),
            "sid_keys": ["session_id", "sessionId", "id"],
            "text_keys": ["input", "prompt", "text"],
        },
    ],
}

SHELL_SOURCES: dict[str, list[str]] = {
    "shell_zsh": [
        str(Path.home() / ".zsh_history"),
    ],
    "shell_bash": [
        str(Path.home() / ".bash_history"),
    ],
}

SOURCE_MONITOR_FLAGS: dict[str, bool] = {
    "claude_code": ENABLE_CLAUDE_HISTORY_MONITOR,
    "codex_history": ENABLE_CODEX_HISTORY_MONITOR,
    "opencode": ENABLE_OPENCODE_MONITOR,
    "kilo": ENABLE_KILO_MONITOR,
}

SHELL_LINE_RE = re.compile(r"^:\s*(\d+):\d+;(.*)$")
SECRET_REPLACEMENTS = [
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
    # Slack tokens
    (re.compile(r"\bxox[bprs]-[A-Za-z0-9\-]{10,}\b"), "xox?-***"),
    # AWS access keys
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{12,}\b"), "AKIA***"),
    # PEM private key blocks
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "***PEM_KEY_REDACTED***"),
]

IGNORE_SHELL_CMD_PREFIXES = (
    "history",
    "fc ",
)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
LOG_DIR.mkdir(parents=True, exist_ok=True)
try:
    os.chmod(LOG_DIR, 0o700)
except OSError:
    pass
log_file = LOG_DIR / DAEMON_LOG_NAME
logger = logging.getLogger(LOGGER_NAME)
logger.setLevel(logging.INFO)

_rfh = logging.handlers.RotatingFileHandler(
    str(log_file), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_rfh.setFormatter(logging.Formatter(LOG_FORMAT))
logger.addHandler(_rfh)

_sh = logging.StreamHandler(sys.stderr)
_sh.setLevel(logging.WARNING)
_sh.setFormatter(logging.Formatter(LOG_FORMAT))
logger.addHandler(_sh)

LOCK_FILE = LOG_DIR / DAEMON_LOCK_NAME
_LOCK_FD = None

# ---------------------------------------------------------------------------
# Lazy httpx import
# ---------------------------------------------------------------------------
try:
    import httpx

    _HTTPX_OK = True
except ImportError:
    _HTTPX_OK = False
    logger.warning("httpx not installed; will only write local files.")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_shutdown = False


def _handle_signal(signum, _frame):
    global _shutdown
    logger.info("Received signal %s, shutting down.", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _release_single_instance_lock() -> None:
    global _LOCK_FD
    try:
        if _LOCK_FD is not None:
            os.close(_LOCK_FD)
            _LOCK_FD = None
    except OSError:
        pass
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except OSError:
        pass


def _acquire_single_instance_lock() -> bool:
    global _LOCK_FD
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            _LOCK_FD = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
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
                logger.error("Another ContextGO daemon instance is running (pid=%s), exiting.", pid)
                return False
            try:
                LOCK_FILE.unlink()
            except OSError:
                pass
        except OSError as exc:
            logger.error("Failed to acquire daemon lock: %s", exc)
            return False
    logger.error("Failed to acquire daemon lock after stale cleanup.")
    return False


def _count_antigravity_language_servers() -> int:
    try:
        proc = subprocess.run(
            ["pgrep", "-f", "language_server_macos_arm"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode not in (0, 1):
            return 0
        return sum(1 for ln in (proc.stdout or "").splitlines() if ln.strip())
    except (OSError, subprocess.TimeoutExpired):
        return 0


class SessionTracker:
    def __init__(self):
        self.sessions: dict[str, dict[str, Any]] = {}
        self.file_cursors: dict[str, tuple[int, int]] = {}  # cursor_key -> (inode, offset)
        self.antigravity_sessions: dict[str, dict[str, Any]] = {}
        self.active_jsonl: dict[str, dict[str, Any]] = {}
        self.active_shell: dict[str, str] = {}

        self._last_heartbeat = time.time()
        self._last_source_refresh = 0.0
        self._last_pending_retry = 0.0
        self._export_count = 0
        self._error_count = 0
        self._last_activity_ts = 0.0
        self._http_client = None
        self._last_codex_scan = 0.0
        self._last_claude_transcript_scan = 0.0
        self._last_antigravity_scan = 0.0
        self._last_antigravity_busy_log = 0.0
        self._last_index_sync = 0.0
        self._index_dirty = False
        self._cached_codex_session_files: list[str] = []
        self._cached_claude_transcript_files: list[str] = []
        self._cached_antigravity_dirs: list[str] = []

        if ENABLE_REMOTE_SYNC and _HTTPX_OK:
            try:
                self._http_client = httpx.Client(
                    timeout=EXPORT_HTTP_TIMEOUT_SEC, trust_env=False, follow_redirects=False
                )
            except Exception as exc:
                logger.warning("Failed to initialize httpx client: %s", exc)
                self._http_client = None

        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(PENDING_DIR, 0o700)
        except OSError:
            pass
        self.refresh_sources(force=True)

    # -- source discovery -------------------------------------------------
    def refresh_sources(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_source_refresh < 120:
            return
        self._last_source_refresh = now

        # JSONL AI sources: pick first existing candidate per source.
        for source_name, candidates in JSONL_SOURCES.items():
            if not SOURCE_MONITOR_FLAGS.get(source_name, True):
                if source_name in self.active_jsonl:
                    logger.info("Source disabled by env: %s", source_name)
                    del self.active_jsonl[source_name]
                continue
            picked = None
            for candidate in candidates:
                p = candidate["path"]
                if os.path.exists(p):
                    picked = candidate
                    break
            prev = self.active_jsonl.get(source_name)
            if picked:
                self.active_jsonl[source_name] = picked
                if not prev or prev["path"] != picked["path"]:
                    cursor_key = self._cursor_key("jsonl", source_name, picked["path"])
                    self._set_cursor(cursor_key, picked["path"], os.path.getsize(picked["path"]))
                    logger.info("Source active: %s -> %s", source_name, picked["path"])
            elif source_name in self.active_jsonl:
                logger.info("Source offline: %s", source_name)
                del self.active_jsonl[source_name]

        # Shell sources
        if ENABLE_SHELL_MONITOR:
            for source_name, paths in SHELL_SOURCES.items():
                picked_path = ""
                for p in paths:
                    if os.path.exists(p):
                        picked_path = p
                        break
                prev = self.active_shell.get(source_name, "")
                if picked_path:
                    self.active_shell[source_name] = picked_path
                    if prev != picked_path:
                        cursor_key = self._cursor_key("shell", source_name, picked_path)
                        self._set_cursor(cursor_key, picked_path, os.path.getsize(picked_path))
                        logger.info("Source active: %s -> %s", source_name, picked_path)
                elif source_name in self.active_shell:
                    logger.info("Source offline: %s", source_name)
                    del self.active_shell[source_name]

    def _cursor_key(self, kind: str, source_name: str, path: str) -> str:
        digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:10]
        return f"{kind}:{source_name}:{digest}"

    def _get_cursor(self, cursor_key: str, path: str) -> int:
        """Return the byte offset for path, resetting to 0 if the file was rotated (inode changed)."""
        try:
            st = os.stat(path)
        except OSError:
            return 0
        cur_inode = st.st_ino
        prev = self.file_cursors.get(cursor_key)
        if prev is None:
            # First time: start at current size to skip existing content
            return st.st_size
        prev_inode, prev_offset = prev
        if cur_inode != prev_inode:
            # File was rotated/replaced
            return 0
        if st.st_size < prev_offset:
            # File was truncated
            return 0
        return prev_offset

    def _set_cursor(self, cursor_key: str, path: str, offset: int) -> None:
        try:
            inode = os.stat(path).st_ino
        except OSError:
            return
        self.file_cursors[cursor_key] = (inode, offset)

    @staticmethod
    def _is_safe_source(path: str) -> bool:
        """Verify source file is a regular file owned by the current user (not a symlink)."""
        try:
            st = os.lstat(path)
        except OSError:
            return False
        if os.path.islink(path):
            logger.warning("Skipping symlinked source: %s", path)
            return False
        if st.st_uid != os.getuid():
            logger.warning("Skipping source not owned by current user: %s (uid=%d)", path, st.st_uid)
            return False
        if not stat.S_ISREG(st.st_mode):
            logger.warning("Skipping non-regular source: %s", path)
            return False
        return True

    # -- polling ----------------------------------------------------------
    def poll_jsonl_sources(self):
        now = time.time()
        for source_name, source in self.active_jsonl.items():
            path = source["path"]
            cursor_key = self._cursor_key("jsonl", source_name, path)
            self._poll_jsonl_file(source_name, path, source, cursor_key, now)

    def _poll_jsonl_file(self, source_name: str, path: str, source: dict[str, Any], cursor_key: str, now: float):
        if not self._is_safe_source(path):
            return
        try:
            cur_size = os.path.getsize(path)
        except OSError:
            return

        last = self._get_cursor(cursor_key, path)
        if cur_size <= last:
            self._set_cursor(cursor_key, path, cur_size)
            return

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(last)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    sid = self._extract_sid(data, source.get("sid_keys", []), source_name)
                    text = self._extract_text(data, source.get("text_keys", []))
                    text = self._sanitize_text(text)
                    if not text:
                        continue

                    self._upsert_session(sid, source_name, text, now)

            self._set_cursor(cursor_key, path, cur_size)
        except Exception as exc:
            self._error_count += 1
            logger.error("poll_jsonl_sources(%s): %s", source_name, exc)

    def poll_shell_sources(self):
        if not ENABLE_SHELL_MONITOR:
            return

        now = time.time()
        for source_name, path in self.active_shell.items():
            if not self._is_safe_source(path):
                continue
            cursor_key = self._cursor_key("shell", source_name, path)
            try:
                cur_size = os.path.getsize(path)
            except OSError:
                continue

            last = self._get_cursor(cursor_key, path)
            if cur_size <= last:
                self._set_cursor(cursor_key, path, cur_size)
                continue

            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(last)
                    for line in f:
                        parsed = self._parse_shell_line(source_name, line)
                        if not parsed:
                            continue
                        sid, text = parsed
                        self._upsert_session(sid, source_name, text, now)
                self._set_cursor(cursor_key, path, cur_size)
            except Exception as exc:
                self._error_count += 1
                logger.error("poll_shell_sources(%s): %s", source_name, exc)

    def poll_codex_sessions(self):
        if not ENABLE_CODEX_SESSION_MONITOR:
            return
        if not os.path.isdir(CODEX_SESSIONS):
            return

        now = time.time()
        if now - self._last_codex_scan >= CODEX_SESSION_SCAN_INTERVAL_SEC or not self._cached_codex_session_files:
            try:
                session_files = glob.glob(os.path.join(CODEX_SESSIONS, "**", "*.jsonl"), recursive=True)
                if len(session_files) > MAX_CODEX_SESSION_FILES_PER_SCAN:
                    session_files = sorted(
                        session_files,
                        key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0.0,
                        reverse=True,
                    )[:MAX_CODEX_SESSION_FILES_PER_SCAN]
                self._cached_codex_session_files = session_files
                self._last_codex_scan = now
                logger.debug("codex sessions cache refreshed: %d files", len(session_files))
            except OSError as exc:
                self._error_count += 1
                logger.error("glob codex sessions: %s", exc)
                session_files = self._cached_codex_session_files
        else:
            session_files = self._cached_codex_session_files

        for path in session_files:
            if not self._is_safe_source(path):
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime < now - 3600:
                continue

            cursor_key = self._cursor_key("codex_session", "codex_session", path)
            try:
                cur_size = os.path.getsize(path)
            except OSError:
                continue

            last = self._get_cursor(cursor_key, path)
            if cur_size <= last:
                self._set_cursor(cursor_key, path, cur_size)
                continue

            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(last)
                    for line in f:
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
                            texts = [
                                c.get("text", "")
                                for c in payload.get("content", [])
                                if c.get("type") == "output_text"
                            ]
                            text = "\n".join(t for t in texts if t)
                        elif ptype == "reasoning":
                            text = payload.get("text", "")

                        text = self._sanitize_text(text)
                        if text:
                            sid = os.path.basename(path)
                            self._upsert_session(sid, "codex_session", text, now)

                self._set_cursor(cursor_key, path, cur_size)
            except Exception as exc:
                self._error_count += 1
                logger.error("poll_codex_sessions(%s): %s", path, exc)

    def poll_claude_transcripts(self):
        """Scan ~/.claude/transcripts/ses_*.jsonl for full AI conversation text.

        Each transcript file is one conversation session.  The JSONL schema per line:
          {"type": "user"|"assistant"|"tool_use"|"tool_result"|..., ...}
        We only extract 'user' and 'assistant' text lines, skipping tool noise.
        We honour CLAUDE_TRANSCRIPTS_LOOKBACK_DAYS on first run to avoid a
        historical replay storm.
        """
        if not ENABLE_CLAUDE_TRANSCRIPTS_MONITOR:
            return
        if not os.path.isdir(CLAUDE_TRANSCRIPTS_DIR):
            return

        now = time.time()
        lookback_cutoff = now - CLAUDE_TRANSCRIPTS_LOOKBACK_DAYS * 86400

        if (
            now - self._last_claude_transcript_scan >= CLAUDE_TRANSCRIPT_SCAN_INTERVAL_SEC
            or not self._cached_claude_transcript_files
        ):
            try:
                session_files = glob.glob(
                    os.path.join(CLAUDE_TRANSCRIPTS_DIR, "**", "ses_*.jsonl"), recursive=True
                )
                if len(session_files) > MAX_CLAUDE_TRANSCRIPT_FILES_PER_POLL:
                    session_files = sorted(
                        session_files,
                        key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0.0,
                        reverse=True,
                    )[:MAX_CLAUDE_TRANSCRIPT_FILES_PER_POLL]
                self._cached_claude_transcript_files = session_files
                self._last_claude_transcript_scan = now
                logger.debug("claude transcript cache refreshed: %d files", len(session_files))
            except OSError as exc:
                self._error_count += 1
                logger.error("glob claude_transcripts: %s", exc)
                session_files = self._cached_claude_transcript_files
        else:
            session_files = self._cached_claude_transcript_files

        for path in session_files:
            if not self._is_safe_source(path):
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue

            # On very first encounter: if the file is older than the lookback
            # window, skip it to avoid replaying months of history.
            cursor_key = self._cursor_key("claude_transcripts", "claude_transcripts", path)
            if cursor_key not in self.file_cursors:
                try:
                    fsize = os.path.getsize(path)
                except OSError:
                    fsize = 0
                if mtime < lookback_cutoff:
                    # Establish baseline at end-of-file; never re-read old content.
                    self._set_cursor(cursor_key, path, fsize)
                    continue
                # New file within lookback window: start from beginning.
                try:
                    self.file_cursors[cursor_key] = (os.stat(path).st_ino, 0)
                except OSError:
                    continue

            try:
                cur_size = os.path.getsize(path)
            except OSError:
                continue

            last = self._get_cursor(cursor_key, path)
            if cur_size <= last:
                self._set_cursor(cursor_key, path, cur_size)
                continue

            messages_added = 0
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(last)
                    for raw in f:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        msg_type = data.get("type", "")
                        # Only index human and AI text; skip tool noise
                        if msg_type not in ("user", "assistant", "human"):
                            continue

                        # Extract text — handles plain string or content-block lists
                        content = data.get("content", "")
                        if isinstance(content, str):
                            text = content.strip()
                        elif isinstance(content, list):
                            parts = []
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    t = block.get("text", "")
                                    if isinstance(t, str) and t.strip():
                                        parts.append(t.strip())
                            text = " ".join(parts)
                        elif isinstance(content, dict):
                            t = content.get("text", "")
                            text = t.strip() if isinstance(t, str) else ""
                        else:
                            text = ""

                        text = self._sanitize_text(text)
                        if not text:
                            continue

                        sid = self._build_transcript_sid(path)
                        self._upsert_session(sid, "claude_transcripts", text, now)
                        messages_added += 1

                self._set_cursor(cursor_key, path, cur_size)
                if messages_added:
                    logger.debug("claude_transcripts: +%d msgs from %s", messages_added, os.path.basename(path))

            except Exception as exc:
                self._error_count += 1
                logger.error("poll_claude_transcripts(%s): %s", path, exc)

    def poll_antigravity(self):
        if not ENABLE_ANTIGRAVITY_MONITOR:
            return
        if SUSPEND_ANTIGRAVITY_WHEN_BUSY:
            ls_count = _count_antigravity_language_servers()
            if ls_count >= ANTIGRAVITY_BUSY_LS_THRESHOLD:
                now = time.time()
                # Throttle busy logs to avoid noisy stderr in long-running sessions.
                if now - self._last_antigravity_busy_log >= 180:
                    logger.info(
                        "poll_antigravity skipped: language_server_macos_arm=%s threshold=%s",
                        ls_count,
                        ANTIGRAVITY_BUSY_LS_THRESHOLD,
                    )
                    self._last_antigravity_busy_log = now
                return

        if not os.path.isdir(ANTIGRAVITY_BRAIN):
            return

        now = time.time()
        if now - self._last_antigravity_scan >= ANTIGRAVITY_SCAN_INTERVAL_SEC or not self._cached_antigravity_dirs:
            try:
                dirs = glob.glob(os.path.join(ANTIGRAVITY_BRAIN, "*-*-*-*-*"))
                if len(dirs) > MAX_ANTIGRAVITY_DIRS_PER_SCAN:
                    dirs = sorted(
                        dirs,
                        key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0.0,
                        reverse=True,
                    )[:MAX_ANTIGRAVITY_DIRS_PER_SCAN]
                self._cached_antigravity_dirs = dirs
                self._last_antigravity_scan = now
                logger.debug("antigravity dirs cache refreshed: %d dirs", len(dirs))
            except OSError as exc:
                self._error_count += 1
                logger.error("glob antigravity dirs: %s", exc)
                dirs = self._cached_antigravity_dirs
        else:
            dirs = self._cached_antigravity_dirs

        # final_only: collect near-complete summaries only; live: keep old behavior.
        if ANTIGRAVITY_INGEST_MODE == "final_only":
            brain_docs = ["walkthrough.md", "task.md", "implementation_plan.md"]
        else:
            brain_docs = ["walkthrough.md", "implementation_plan.md"]
        seen_sids = set()

        for sdir in dirs:
            sid = os.path.basename(sdir)
            seen_sids.add(sid)
            # Try each doc type; use the most recently modified one that exists
            wt = None
            latest_mtime = 0.0
            for doc in brain_docs:
                candidate = os.path.join(sdir, doc)
                if os.path.exists(candidate):
                    try:
                        m = os.path.getmtime(candidate)
                    except OSError:
                        m = 0.0
                    if m > latest_mtime:
                        latest_mtime = m
                        wt = candidate
            if not wt:
                continue

            try:
                mtime = os.path.getmtime(wt)
            except OSError:
                continue

            if sid not in self.antigravity_sessions:
                # First sighting: establish baseline and skip to avoid replay storm.
                self.antigravity_sessions[sid] = {
                    "mtime": mtime,
                    "path": wt,
                    "last_change": now,
                    "exported_mtime": mtime,
                }
                continue

            meta = self.antigravity_sessions[sid]
            prev = float(meta.get("mtime", 0.0))
            path_changed = wt != meta.get("path")
            if path_changed or mtime > prev:
                meta["mtime"] = mtime
                meta["path"] = wt
                meta["last_change"] = now
                # final_only mode delays export until file is quiet for N seconds.
                if ANTIGRAVITY_INGEST_MODE == "final_only":
                    continue
            elif ANTIGRAVITY_INGEST_MODE != "final_only":
                continue

            if ANTIGRAVITY_INGEST_MODE == "final_only":
                exported_mtime = float(meta.get("exported_mtime", 0.0))
                if mtime <= exported_mtime:
                    continue
                last_change = float(meta.get("last_change", now))
                if now - last_change < ANTIGRAVITY_QUIET_SEC:
                    continue
                try:
                    if os.path.getsize(wt) < ANTIGRAVITY_MIN_DOC_BYTES:
                        continue
                except OSError:
                    continue

            try:
                with open(wt, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(50_000)
                content = self._sanitize_text(content)
                if content:
                    data = {
                        "source": "antigravity",
                        "messages": [content],
                        "last_seen": now,
                    }
                    self._export(sid, data, title_prefix="Antigravity Walkthrough")
                    meta["exported_mtime"] = mtime
            except Exception as exc:
                self._error_count += 1
                logger.error("poll_antigravity(%s): %s", sid, exc)

        if len(self.antigravity_sessions) > MAX_ANTIGRAVITY_SESSIONS:
            stale = [
                (sid, meta.get("mtime", 0.0))
                for sid, meta in self.antigravity_sessions.items()
                if sid not in seen_sids
            ]
            stale.sort(key=lambda x: x[1])
            remove_n = len(self.antigravity_sessions) - MAX_ANTIGRAVITY_SESSIONS
            for sid, _ in stale[:remove_n]:
                self.antigravity_sessions.pop(sid, None)

    # -- parsing helpers ---------------------------------------------------
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

        # fallback for structures like opencode parts
        parts = data.get("parts")
        if isinstance(parts, list):
            text_parts: list[str] = []
            for part in parts:
                if isinstance(part, dict) and part.get("type") == "text":
                    ptext = part.get("text")
                    if isinstance(ptext, str) and ptext.strip():
                        text_parts.append(ptext.strip())
            if text_parts:
                prefix = data.get("input") if isinstance(data.get("input"), str) else ""
                if prefix.strip():
                    return prefix.strip() + "\n" + "\n".join(text_parts)
                return "\n".join(text_parts)
        return ""

    def _parse_shell_line(self, source_name: str, raw_line: str):
        line = raw_line.strip()
        if not line:
            return None

        ts = int(time.time())
        cmd = line

        match = SHELL_LINE_RE.match(line)
        if match:
            ts = int(match.group(1))
            cmd = match.group(2).strip()

        if not cmd:
            return None

        low = cmd.lower()
        if low.startswith(IGNORE_SHELL_CMD_PREFIXES):
            return None

        cmd = self._sanitize_text(cmd)
        if not cmd:
            return None

        day = datetime.fromtimestamp(ts).strftime("%Y%m%d")
        sid = f"{source_name}_{day}"
        return sid, cmd

    def _sanitize_text(self, text: str) -> str:
        if not text:
            return ""
        out = strip_private_blocks(text).strip()
        for pattern, repl in SECRET_REPLACEMENTS:
            out = pattern.sub(repl, out)
        if len(out) > 4000:
            out = out[:4000]
        return out

    @staticmethod
    def _sanitize_filename_part(raw: str, default: str = "session") -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", (raw or "").strip())
        safe = safe.strip("._-")
        return safe[:64] if safe else default

    def _build_transcript_sid(self, path: str) -> str:
        try:
            rel = os.path.relpath(path, CLAUDE_TRANSCRIPTS_DIR)
        except ValueError:
            rel = os.path.basename(path)
        base = self._sanitize_filename_part(os.path.basename(path).replace(".jsonl", ""))
        digest = hashlib.sha256(rel.encode("utf-8", errors="ignore")).hexdigest()[:10]
        return f"{base}_{digest}"

    # -- session management -----------------------------------------------
    def _upsert_session(self, sid: str, source: str, text: str, now: float):
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

    def _evict_oldest(self):
        exported = [(k, v) for k, v in self.sessions.items() if v["exported"]]
        if exported:
            oldest_k = min(exported, key=lambda x: x[1]["last_seen"])[0]
            del self.sessions[oldest_k]
            return
        oldest_k = min(self.sessions, key=lambda k: self.sessions[k]["last_seen"])
        del self.sessions[oldest_k]

    def check_and_export_idle(self):
        now = time.time()
        to_remove = []

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
                # Discard stale sessions with insufficient messages
                data["exported"] = True

        for sid in to_remove:
            del self.sessions[sid]

    def cleanup_cursors(self):
        if len(self.file_cursors) <= MAX_FILE_CURSORS:
            return
        keys = sorted(self.file_cursors.keys())
        remove_n = max(1, len(keys) // 3)
        for key in keys[:remove_n]:
            del self.file_cursors[key]
        logger.info("Cleaned %d file cursors.", remove_n)

    def maybe_sync_index(self, force: bool = False) -> None:
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

    # -- export -----------------------------------------------------------
    def _export(self, sid: str, data: dict[str, Any], title_prefix: str = ""):
        source = data["source"]
        messages = data["messages"]
        content = "\n- ".join(msg[:2000] for msg in messages[-60:])

        prefix = title_prefix or f"Live {source} Session"
        title = f"{prefix} {sid[:12]}"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        local_dir = LOCAL_STORAGE_ROOT / "resources" / "shared" / "history"
        local_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(local_dir, 0o700)
        except OSError:
            pass
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
            file_path.write_text(formatted, encoding="utf-8")
            os.chmod(file_path, 0o600)
            self._index_dirty = True
            self.maybe_sync_index()
        except OSError as exc:
            logger.error("Failed to write local file %s: %s", file_path, exc)
            return False

        if self._http_client:
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
                    logger.info("Synced %s session %s to remote history.", source, sid[:12])
                    self._retry_pending()
                    return True
                logger.warning("Remote sync HTTP %d for %s %s", resp.status_code, source, sid[:12])
            except Exception as exc:
                logger.warning("Remote sync offline, queue pending: %s", exc)
        elif not ENABLE_REMOTE_SYNC:
            self._export_count += 1
            return True

        pending_path = PENDING_DIR / file_path.name
        try:
            self._prune_pending_files()
            pending_path.write_text(formatted, encoding="utf-8")
            os.chmod(pending_path, 0o600)
            logger.info("Queued pending sync: %s", pending_path.name)
        except OSError as exc:
            logger.error("Failed pending write: %s", exc)
        return False

    @staticmethod
    def _pending_mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    def _retry_pending(self) -> None:
        if not self._http_client:
            return

        pending_candidates = list(PENDING_DIR.glob("*.md"))
        pending: list[Path] = [p for p in pending_candidates if p.is_file()]
        pending.sort(key=self._pending_mtime)
        if not pending:
            return

        self._last_pending_retry = time.time()
        for pf in pending[:8]:
            try:
                payload = {
                    "path": str(pf),
                    "target": REMOTE_HISTORY_TARGET,
                    "reason": "Retry pending sync",
                    "instruction": f"Index pending conversation: {pf.stem}",
                }
                resp = self._http_client.post(
                    REMOTE_RESOURCE_ENDPOINT,
                    json=payload,
                    timeout=PENDING_HTTP_TIMEOUT_SEC,
                )
                if resp.status_code < 300:
                    pf.unlink(missing_ok=True)
                    logger.info("Retried pending OK: %s", pf.name)
            except Exception:
                break

    def _prune_pending_files(self) -> None:
        try:
            files = [p for p in PENDING_DIR.glob("*.md") if p.is_file()]
        except OSError:
            return
        if len(files) < MAX_PENDING_FILES:
            return
        files.sort(key=self._pending_mtime)
        for old in files[: len(files) - MAX_PENDING_FILES + 1]:
            try:
                old.unlink(missing_ok=True)
            except OSError:
                continue

    def maybe_retry_pending(self) -> None:
        if not PENDING_DIR.exists():
            return
        try:
            has_pending = any(PENDING_DIR.glob("*.md"))
        except OSError:
            has_pending = False
        if not has_pending:
            return
        now = time.time()
        if now - self._last_pending_retry < PENDING_RETRY_INTERVAL_SEC:
            return
        self._retry_pending()

    def next_sleep_interval(self) -> int:
        """Adaptive polling: faster near idle-export boundary, quiet when idle.

        Night-mode: during NIGHT_POLL_START_HOUR – NIGHT_POLL_END_HOUR (local)
        and when there are no active sessions, expand interval to
        NIGHT_POLL_INTERVAL_SEC to preserve battery / CPU on always-on machines.
        """
        current_hour = datetime.now().hour
        start_hour = NIGHT_POLL_START_HOUR % 24
        end_hour = NIGHT_POLL_END_HOUR % 24
        is_night = (
            start_hour > end_hour
            and (current_hour >= start_hour or current_hour < end_hour)
        ) or (
            start_hour <= end_hour
            and start_hour <= current_hour < end_hour
        )

        # Night mode: only throttle if no sessions are actively pending export
        has_pending_sessions = any(
            not v.get("exported") for v in self.sessions.values()
        )
        try:
            has_pending_files = PENDING_DIR.exists() and any(PENDING_DIR.glob("*.md"))
        except Exception:
            has_pending_files = False

        if is_night and not has_pending_sessions and not has_pending_files:
            return max(1, NIGHT_POLL_INTERVAL_SEC)

        # Normal mode: no active sessions → slow down by 3×
        if not has_pending_sessions and not has_pending_files:
            return min(POLL_INTERVAL_SEC * 3, IDLE_SLEEP_CAP_SEC)

        sleep_s = max(1, POLL_INTERVAL_SEC)

        try:
            if has_pending_files:
                sleep_s = min(sleep_s, FAST_POLL_INTERVAL_SEC)
        except Exception:
            pass

        now = time.time()
        nearest_due = None
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

        if self._last_activity_ts and (now - self._last_activity_ts) < max(15, FAST_POLL_INTERVAL_SEC * 4):
            sleep_s = min(sleep_s, FAST_POLL_INTERVAL_SEC)

        return max(1, sleep_s)

    # -- heartbeat --------------------------------------------------------
    def heartbeat(self):
        now = time.time()
        if now - self._last_heartbeat < HEARTBEAT_INTERVAL_SEC:
            return
        self._last_heartbeat = now

        try:
            if _resource_mod is not None:
                rss = _resource_mod.getrusage(_resource_mod.RUSAGE_SELF).ru_maxrss
                # macOS reports bytes; Linux reports kilobytes
                if sys.platform == "darwin":
                    mem_mb = rss / (1024 * 1024)
                else:
                    mem_mb = rss / 1024
            else:
                mem_mb = -1
        except Exception:
            mem_mb = -1

        pending_count = len(list(PENDING_DIR.glob("*.md"))) if PENDING_DIR.exists() else 0

        active_sources = list(self.active_jsonl.keys()) + list(self.active_shell.keys())
        logger.info(
            "♥ sessions=%d cursors=%d exported=%d errors=%d pending=%d mem=%.1fMB active_sources=%s",
            len(self.sessions),
            len(self.file_cursors),
            self._export_count,
            self._error_count,
            pending_count,
            mem_mb,
            ",".join(active_sources) if active_sources else "none",
        )


def main():
    os.umask(0o077)
    if not _acquire_single_instance_lock():
        raise SystemExit(1)
    logger.info("Starting ContextGO daemon")
    logger.info("Remote sync: %s", "on" if ENABLE_REMOTE_SYNC else "off")
    logger.info("Remote sync URL: %s", REMOTE_SYNC_URL)
    logger.info("Codex sessions path: %s", CODEX_SESSIONS)
    logger.info("Antigravity brain path: %s", ANTIGRAVITY_BRAIN)
    logger.info(
        "Idle=%ds Poll=%ds FastPoll=%ds PendingRetry=%ds Heartbeat=%ds ShellMonitor=%s"
        " CodexScan=%ds ClaudeScan=%ds AntigravityScan=%ds"
        " AGIngest=%s AGQuiet=%ds AGMinDoc=%dB AGSuspendBusy=%s AGBusyThreshold=%d"
        " Monitors={claude_history:%s,codex_history:%s,opencode:%s,kilo:%s,codex_session:%s,claude_transcripts:%s,antigravity:%s}"
        " RemoteSync=%s"
        " CycleBudget=%ss IndexSyncMin=%ss BackoffMax=%ss Jitter=%ss",
        IDLE_TIMEOUT_SEC,
        POLL_INTERVAL_SEC,
        FAST_POLL_INTERVAL_SEC,
        PENDING_RETRY_INTERVAL_SEC,
        HEARTBEAT_INTERVAL_SEC,
        "on" if ENABLE_SHELL_MONITOR else "off",
        CODEX_SESSION_SCAN_INTERVAL_SEC,
        CLAUDE_TRANSCRIPT_SCAN_INTERVAL_SEC,
        ANTIGRAVITY_SCAN_INTERVAL_SEC,
        ANTIGRAVITY_INGEST_MODE,
        ANTIGRAVITY_QUIET_SEC,
        ANTIGRAVITY_MIN_DOC_BYTES,
        "on" if SUSPEND_ANTIGRAVITY_WHEN_BUSY else "off",
        ANTIGRAVITY_BUSY_LS_THRESHOLD,
        "on" if ENABLE_CLAUDE_HISTORY_MONITOR else "off",
        "on" if ENABLE_CODEX_HISTORY_MONITOR else "off",
        "on" if ENABLE_OPENCODE_MONITOR else "off",
        "on" if ENABLE_KILO_MONITOR else "off",
        "on" if ENABLE_CODEX_SESSION_MONITOR else "off",
        "on" if ENABLE_CLAUDE_TRANSCRIPTS_MONITOR else "off",
        "on" if ENABLE_ANTIGRAVITY_MONITOR else "off",
        "on" if ENABLE_REMOTE_SYNC else "off",
        CYCLE_BUDGET_SEC,
        INDEX_SYNC_MIN_INTERVAL_SEC,
        ERROR_BACKOFF_MAX_SEC,
        LOOP_JITTER_SEC,
    )

    tracker = SessionTracker()
    cycle = 0
    consecutive_errors = 0

    while not _shutdown:
        had_error = False
        try:
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

        if had_error:
            consecutive_errors += 1
        else:
            consecutive_errors = 0

        sleep_s = float(tracker.next_sleep_interval())
        if consecutive_errors > 0:
            sleep_s += min(float(ERROR_BACKOFF_MAX_SEC), float(2 ** min(consecutive_errors, 6)))
        if LOOP_JITTER_SEC > 0:
            sleep_s += random.uniform(0.0, LOOP_JITTER_SEC)
        time.sleep(max(1.0, sleep_s))

    tracker.maybe_sync_index(force=True)
    if tracker._http_client:
        try:
            tracker._http_client.close()
        except OSError:
            pass
    logger.info("Daemon shutdown complete. Exported %d sessions total.", tracker._export_count)


if __name__ == "__main__":
    main()
