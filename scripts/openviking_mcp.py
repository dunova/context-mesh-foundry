import atexit
from datetime import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import threading
from typing import Any

import httpx


def _stderr(msg: str) -> None:
    try:
        print(msg, file=sys.stderr, flush=True)
    except Exception:
        pass


def _try_reexec_with_openviking_python() -> bool:
    """If current interpreter misses MCP deps, re-exec with known working venv python."""
    if os.environ.get("OPENVIKING_MCP_REEXECED") == "1":
        return False

    target_py = os.path.expanduser(os.environ.get("OPENVIKING_PYTHON", "~/.openviking_env/bin/python"))
    if not os.path.exists(target_py):
        return False

    try:
        if os.path.realpath(sys.executable) == os.path.realpath(target_py):
            return False
    except Exception:
        pass

    env = os.environ.copy()
    env["OPENVIKING_MCP_REEXECED"] = "1"
    try:
        os.execve(target_py, [target_py, *sys.argv], env)
    except Exception as exc:
        _stderr(f"[openviking-mcp] re-exec failed: {exc}")
        return False
    return True


try:
    from mcp.server.fastmcp import FastMCP
except Exception as _import_exc:
    _stderr(f"[openviking-mcp] import mcp failed with {sys.executable}: {_import_exc}")
    _try_reexec_with_openviking_python()
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        FastMCP = None


class _NoopMCP:
    """Fallback MCP object used in test environments with mocked FastMCP."""

    @staticmethod
    def tool(*_args, **_kwargs):
        def _decorator(func):
            return func

        return _decorator

    @staticmethod
    def run(*_args, **_kwargs):
        return None


def _create_mcp_server():
    """Create MCP server; degrade to no-op when FastMCP is mocked/unavailable."""
    try:
        if FastMCP is None:
            return _NoopMCP()
        if getattr(FastMCP, "__module__", "").startswith("unittest.mock"):
            return _NoopMCP()
        server = FastMCP("OpenViking Global Memory Server")
        if getattr(type(server), "__module__", "").startswith("unittest.mock"):
            return _NoopMCP()
        return server
    except Exception:
        return _NoopMCP()


mcp = _create_mcp_server()

OPENVIKING_URL = os.environ.get("OPENVIKING_URL", "http://127.0.0.1:8090/api/v1")

# Security: require HTTPS for non-localhost URLs to prevent MITM
_ov_host = OPENVIKING_URL.split("://", 1)[-1].split("/", 1)[0].split(":")[0]
if _ov_host not in ("127.0.0.1", "localhost", "::1") and not OPENVIKING_URL.startswith("https://"):
    enforce_https = str(os.environ.get("OPENVIKING_ENFORCE_HTTPS", "0")).strip().lower() in {"1", "true", "yes"}
    if enforce_https:
        raise SystemExit(f"Remote OPENVIKING_URL must use https://. Got: {OPENVIKING_URL}")
    _stderr(f"[openviking-mcp] warning: insecure remote OPENVIKING_URL over http: {OPENVIKING_URL}")

LOCAL_STORAGE_ROOT = os.path.expanduser(
    os.environ.get("UNIFIED_CONTEXT_STORAGE_ROOT", os.environ.get("OPENVIKING_STORAGE_ROOT", "~/.unified_context_data"))
)


def _resolve_onecontext_db_path() -> str:
    candidates = [
        os.environ.get("ONECONTEXT_DB_PATH", "").strip(),
        "~/.aline/db/aline.db",
        "~/.onecontext/history.db",
    ]
    for c in candidates:
        if not c:
            continue
        p = os.path.expanduser(c)
        if os.path.isfile(p):
            return p
    # keep first valid-looking candidate for diagnostics even if not found
    for c in candidates:
        if c:
            return os.path.expanduser(c)
    return os.path.expanduser("~/.aline/db/aline.db")


ALINE_DB_PATH = _resolve_onecontext_db_path()
VALID_SEARCH_TYPES = {"all", "event", "session", "turn", "content"}
HTTP_TIMEOUT_SEC = max(int(os.environ.get("OPENVIKING_HTTP_TIMEOUT_SEC", "20")), 3)
HTTP_CLIENT = httpx.Client(timeout=HTTP_TIMEOUT_SEC, trust_env=False, follow_redirects=False)
atexit.register(HTTP_CLIENT.close)
OPENVIKING_ROOT_URL = OPENVIKING_URL.split("/api/", 1)[0].rstrip("/")
ONECONTEXT_CLI_TIMEOUT_SEC = max(2, int(os.environ.get("OPENVIKING_ONECONTEXT_CLI_TIMEOUT_SEC", "8")))
ONECONTEXT_SEARCH_BUDGET_SEC = max(4, int(os.environ.get("OPENVIKING_ONECONTEXT_SEARCH_BUDGET_SEC", "18")))
SQLITE_CONNECT_TIMEOUT_SEC = max(0.5, float(os.environ.get("OPENVIKING_SQLITE_CONNECT_TIMEOUT_SEC", "1.5")))
OPENVIKING_ENABLE_SEMANTIC_QUERY = str(
    os.environ.get("OPENVIKING_ENABLE_SEMANTIC_QUERY", "0")
).strip().lower() in {"1", "true", "yes", "on"}
OPENVIKING_LOCAL_SCAN_CACHE_TTL_SEC = max(
    5, int(os.environ.get("OPENVIKING_LOCAL_SCAN_CACHE_TTL_SEC", "30"))
)
OPENVIKING_LOCAL_SCAN_MAX_FILES = max(
    50, int(os.environ.get("OPENVIKING_LOCAL_SCAN_MAX_FILES", "400"))
)
OPENVIKING_LOCAL_SCAN_HARD_CAP = max(
    OPENVIKING_LOCAL_SCAN_MAX_FILES, int(os.environ.get("OPENVIKING_LOCAL_SCAN_HARD_CAP", "2000"))
)
OPENVIKING_LOCAL_SCAN_READ_BYTES = max(
    4096, int(os.environ.get("OPENVIKING_LOCAL_SCAN_READ_BYTES", "120000"))
)
OPENVIKING_HEALTH_CACHE_TTL_SEC = max(
    10, int(os.environ.get("OPENVIKING_HEALTH_CACHE_TTL_SEC", "120"))
)
QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "how",
    "into",
    "of",
    "or",
    "please",
    "search",
    "session",
    "terminal",
    "the",
    "this",
    "to",
    "use",
    "with",
    "x",
    "继续",
    "刚才",
    "功能",
    "命令",
    "方案",
    "搜索",
    "终端",
    "调用",
    "这个",
    "那个",
    "研究",
    "任务",
    "问题",
}


def _resolve_recall_script() -> str:
    candidates = [
        os.path.expanduser("~/.agents/skills/recall/scripts/recall.py"),
        os.path.expanduser("~/.codex/skills/recall/scripts/recall.py"),
        os.path.expanduser("~/.claude/skills/recall/scripts/recall.py"),
    ]
    for script in candidates:
        if os.path.exists(script):
            return script
    return ""


RECALL_SCRIPT_PATH = _resolve_recall_script()
_LOCAL_SCAN_CACHE: dict[str, Any] = {"expires_at": 0.0, "files": [], "root_mtime": 0.0}
_HEALTH_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}
_CACHE_LOCK = threading.Lock()

# ─── Intent Pre-filter (memU-inspired, zero network dependency) ───────────────
# Exact no-retrieve token set – kept deliberately tight so we never drop a
# real query; false-positives here mean wasted DB calls, not missing data.
_NO_RETRIEVE_EXACT: frozenset[str] = frozenset({
    "hi", "hello", "hey", "yo", "ok", "okay", "k", "kk", "sure",
    "thanks", "thank you", "thx", "ty", "np", "nice", "lgtm",
    "got it", "understood", "noted", "great", "good", "cool", "👍",
    "bye", "goodbye", "see you", "later", "exit", "quit", "stop",
    "yes", "no", "yep", "nope", "nah", "uh huh", "uh uh",
})
# Prefix patterns for social openers (do not immediately skip if meaningful tail exists)
_NO_RETRIEVE_PREFIXES = re.compile(
    r"^(hi\b|hello\b|hey\b|yo\b|good\s*(morning|afternoon|evening|night)\b"
    r"|morning\b|evening\b|午安|早安|晚安|你好|嗨|哈喽|谢谢|thanks?\b|thank you\b|再见|拜拜)",
    re.IGNORECASE
)
_SOCIAL_PREFIXES = re.compile(
    r"^(hi|hello|hey|yo|你好|嗨|哈喽|您好|谢谢|thanks?|thank you|ok|okay|好的|收到|明白|请问|麻烦)[,\s，。.!！？?、]*",
    re.IGNORECASE,
)
_RETRIEVE_INTENT_HINTS = re.compile(
    r"(\?|？|\b(what|which|who|when|where|why|how|recap|review|remember|history|decision|decisions|project|projects|preference|preferences|context|summary|worked|did we|yesterday|last week)\b"
    r"|回顾|总结|检索|查询|历史|记录|决策|偏好|项目|上下文|记忆|昨天|上周|之前|为什么|怎么|如何|哪些|什么|谁|请帮)",
    re.IGNORECASE,
)
_SOCIAL_ONLY_TAIL = re.compile(
    r"^(good\s*(morning|afternoon|evening|night)|morning|afternoon|evening|night|你好|嗨|哈喽|thanks?|thank you|谢谢|再见|拜拜|好的|ok|okay)[!,.，。！？?\s]*$",
    re.IGNORECASE,
)


def _strip_social_prefixes(query: str) -> str:
    current = (query or "").strip()
    for _ in range(3):
        nxt = _SOCIAL_PREFIXES.sub("", current, count=1).strip()
        if nxt == current:
            break
        current = nxt
    return current

def _decide_retrieval_intent(query: str) -> bool:
    """Zero-latency, zero-network intent gate (memU-inspired).

    Returns False (skip retrieval) only when the query is demonstrably a greeting
    or pure social noise. Falls back to True (allow retrieval) for anything else,
    including ambiguous cases.
    """
    q = (query or "").strip()
    if not q:
        return False   # empty query – nothing to retrieve
    q_lower = q.lower().rstrip("!.,。？?！")
    # 1. Exact membership check (O(1))
    if q_lower in _NO_RETRIEVE_EXACT:
        return False
    # 2. Always keep identifier-like queries retrievable
    if _looks_like_identifier_query(q):
        return True
    # 3. Obvious retrieval intent (question marks, history/decision keywords)
    if _RETRIEVE_INTENT_HINTS.search(q):
        return True
    # 4. Pure social opener without meaningful tail should be skipped
    if _NO_RETRIEVE_PREFIXES.match(q_lower):
        tail = _strip_social_prefixes(q)
        if not tail:
            return False
        if _looks_like_identifier_query(tail) or _RETRIEVE_INTENT_HINTS.search(tail):
            return True
        if _SOCIAL_ONLY_TAIL.match(tail):
            return False
        token_count = len(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", tail))
        if len(tail) <= 8 and token_count <= 3:
            return False
        return True
    # 5. Ambiguous cases default to retrieve (safe by design)
    return True


def _resolve_search_type(search_type: str) -> str:

    if search_type in VALID_SEARCH_TYPES:
        return search_type
    return "all"


def _onecontext_no_match(result_text: str) -> bool:
    if not result_text:
        return True
    markers = [
        "Found 0 matches",
        "No matches found",
        "No matching messages found",
        "Search Results for:",
        "Regex Search:",
        "Error searching:",
        "no such column:",
    ]
    text = result_text.strip()
    if any(marker in text for marker in markers):
        # "Search Results for: ..." or "Regex Search: ..." alone should be treated as no-match.
        # Otherwise the caller may stop early and skip useful fallback routes.
        if "Found 0 matches" in text or "No matches found" in text:
            return True
        compact_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(compact_lines) <= 3:
            return True
    return False


def _looks_like_identifier_query(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    if q.startswith("ctx-"):
        return True
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", q, re.IGNORECASE):
        return True
    if re.search(r"\d{8}", q) and ("-" in q or "_" in q):
        return True
    # opaque IDs / tags are often dominated by digits + separators and are poor semantic queries
    alnum = sum(ch.isalnum() for ch in q)
    punct = sum(ch in "-_:" for ch in q)
    return len(q) >= 12 and punct >= 2 and alnum >= 6


def _build_query_variants(query: str) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []

    def _add(out: list[str], seen: set[str], value: str) -> None:
        item = (value or "").strip()
        if item and item not in seen:
            out.append(item)
            seen.add(item)

    def _expand_anchor(anchor: str) -> list[str]:
        expanded = [anchor]
        if "/" in anchor:
            basename = os.path.basename(anchor)
            if basename and basename not in expanded:
                expanded.append(basename)
            stem = os.path.splitext(basename)[0]
            if stem and stem not in expanded:
                expanded.append(stem)
        if any(ch in anchor for ch in "._-"):
            parts = [part for part in re.split(r"[._/-]+", anchor) if len(part) >= 3]
            for part in parts[:4]:
                if part not in expanded:
                    expanded.append(part)
        return expanded

    def _latin_tokens(text: str) -> list[str]:
        items = re.findall(r"[A-Za-z][A-Za-z0-9._/-]{1,}", text)
        return [item for item in items if item.lower() not in QUERY_STOPWORDS]

    def _cjk_terms(text: str) -> list[str]:
        parts = re.findall(r"[\u4e00-\u9fff]{2,12}", text)
        return [part for part in parts if part not in QUERY_STOPWORDS]

    def _anchor_score(token: str) -> tuple[int, int, str]:
        has_path = 1 if "/" in token or token.startswith("~") else 0
        has_date = 1 if re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", token) else 0
        looks_identifier = 1 if re.search(r"[A-Z]", token) or re.search(r"\d", token) or "_" in token else 0
        return (has_path + has_date + looks_identifier, len(token), token.lower())

    variants: list[str] = []
    seen: set[str] = set()
    compact = re.sub(r"\s+", " ", q).strip()

    m = re.fullmatch(r"\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*", compact)
    if m:
        y, mm, dd = m.groups()
        _add(variants, seen, f"{y}-{int(mm):02d}-{int(dd):02d}")
        _add(variants, seen, f"{y}{int(mm):02d}{int(dd):02d}")

    anchors: list[str] = []
    anchors.extend(re.findall(r"(?:~?/[A-Za-z0-9._/-]+)", q))
    anchors.extend(_latin_tokens(q))
    anchors.extend(_cjk_terms(q))
    anchors = sorted(set(anchors), key=_anchor_score, reverse=True)
    for anchor in anchors[:8]:
        for item in _expand_anchor(anchor):
            _add(variants, seen, item)
            lowered = item.lower()
            if lowered != item:
                _add(variants, seen, lowered)

    _add(variants, seen, compact)
    return variants


def _normalize_tags(tags: list[str] | str | None) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, list):
        return [str(t).strip() for t in tags if str(t).strip()]
    if isinstance(tags, str):
        raw = tags.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(t).strip() for t in parsed if str(t).strip()]
        except Exception:
            pass
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(tags).strip()]


def _safe_filename(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", (value or "").strip().lower())
    s = s.strip("._-")
    return (s or "memory")[:120]


def _secure_write_text(path: str, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except Exception:
        return 0.0


def _list_shared_files_cached(root: str) -> list[str]:
    now = time.monotonic()
    root_mtime = _safe_mtime(root)
    with _CACHE_LOCK:
        cached_files = _LOCAL_SCAN_CACHE.get("files", [])
        if (
            _LOCAL_SCAN_CACHE.get("expires_at", 0.0) > now
            and float(_LOCAL_SCAN_CACHE.get("root_mtime", 0.0)) == float(root_mtime)
            and cached_files
        ):
            return list(cached_files)

    files: list[str] = []
    reached_cap = False
    for base, _, names in os.walk(root):
        for name in names:
            if name.startswith("."):
                continue
            if not name.lower().endswith((".md", ".txt", ".json", ".jsonl", ".log")):
                continue
            files.append(os.path.join(base, name))
            if len(files) >= OPENVIKING_LOCAL_SCAN_HARD_CAP:
                reached_cap = True
                break
        if reached_cap:
            break

    if files:
        files.sort(key=_safe_mtime, reverse=True)
        files = files[:OPENVIKING_LOCAL_SCAN_MAX_FILES]

    with _CACHE_LOCK:
        _LOCAL_SCAN_CACHE["files"] = list(files)
        _LOCAL_SCAN_CACHE["root_mtime"] = float(root_mtime)
        _LOCAL_SCAN_CACHE["expires_at"] = now + OPENVIKING_LOCAL_SCAN_CACHE_TTL_SEC
    return files


def _local_exact_resource_matches(query: str, limit: int = 3) -> list[dict[str, Any]]:
    root = os.path.join(LOCAL_STORAGE_ROOT, "resources", "shared")
    if not os.path.isdir(root):
        return []

    files = _list_shared_files_cached(root)
    if not files:
        return []

    matches: list[dict[str, Any]] = []
    ql = query.lower()

    for path in files:
        hit_in = None
        snippet = ""
        rel_path = os.path.relpath(path, root)
        if ql in rel_path.lower():
            hit_in = "path"
            snippet = rel_path
        else:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read(OPENVIKING_LOCAL_SCAN_READ_BYTES)
            except Exception:
                continue
            idx = text.lower().find(ql)
            if idx >= 0:
                hit_in = "content"
                start = max(0, idx - 120)
                end = min(len(text), idx + len(query) + 120)
                snippet = re.sub(r"\s+", " ", text[start:end]).strip()

        if hit_in:
            matches.append(
                {
                    "uri_hint": f"viking://resources/{rel_path.replace(os.sep, '/')}",
                    "file_path": path,
                    "matched_in": hit_in,
                    "mtime": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(),
                    "snippet": snippet,
                }
            )
        if len(matches) >= limit:
            break

    return matches


def _build_snippet(text: str, query: str, use_regex: bool, radius: int = 80) -> str:
    if not text:
        return ""
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return ""

    if use_regex and len(query) <= 200:
        try:
            pattern = re.compile(query, re.IGNORECASE)
            match = pattern.search(compact)
        except re.error:
            match = None
    else:
        idx = compact.lower().find(query.lower())
        if idx < 0:
            match = None
        else:
            _s, _e = idx, idx + len(query)
            match = type("_Span", (), {"start": staticmethod(lambda _s=_s: _s), "end": staticmethod(lambda _e=_e: _e)})()

    if not match:
        return compact[: radius * 2]

    start = max(0, match.start() - radius)
    end = min(len(compact), match.end() + radius)
    return compact[start:end]


def _try_cli_search(query: str, search_type: str, limit: int, no_regex: bool) -> str:
    candidates = [
        os.environ.get("ONECONTEXT_BIN", ""),
        "onecontext",
        os.path.expanduser("~/.local/bin/onecontext"),
    ]

    for candidate in candidates:
        if not candidate:
            continue
        if "/" in candidate:
            cmd_path = candidate if os.path.exists(candidate) else None
        else:
            cmd_path = shutil.which(candidate)
        if not cmd_path:
            continue

        cmd = [cmd_path, "search", query, "-t", search_type, "-l", str(limit)]
        if no_regex:
            cmd.append("--no-regex")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=ONECONTEXT_CLI_TIMEOUT_SEC,
            )
        except Exception:
            continue

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        # newer aline help may hide subcommands but search still works; non-zero with no useful output -> try fallback
        if result.returncode == 0 and stdout:
            return f"--- ONECONTEXT SEARCH RESULTS (cli: {os.path.basename(cmd_path)}) ---\n{stdout}"

        unknown_cmd_markers = [
            "No such command",
            "Unknown command",
            "Usage:",
        ]
        if any(m in stderr for m in unknown_cmd_markers):
            continue

        if stdout:
            return f"--- ONECONTEXT SEARCH RESULTS (cli: {os.path.basename(cmd_path)}) ---\n{stdout}"

    if RECALL_SCRIPT_PATH:
        cmd = [
            sys.executable,
            RECALL_SCRIPT_PATH,
            query,
            "--backend",
            "recall",
            "--type",
            search_type,
            "--limit",
            str(limit),
        ]
        if no_regex:
            cmd.append("--no-regex")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=ONECONTEXT_CLI_TIMEOUT_SEC,
            )
        except Exception:
            return ""
        stdout = (result.stdout or "").strip()
        if result.returncode == 0 and stdout and "No matches found" not in stdout:
            return f"--- ONECONTEXT SEARCH RESULTS (recall fallback) ---\n{stdout}"

    return ""


def _probe_recall_health() -> dict[str, Any]:
    now = time.monotonic()
    with _CACHE_LOCK:
        if _HEALTH_CACHE.get("expires_at", 0.0) > now and _HEALTH_CACHE.get("payload") is not None:
            return dict(_HEALTH_CACHE["payload"])

    if not RECALL_SCRIPT_PATH:
        payload = {"ok": False, "error": "recall.py not found"}
        with _CACHE_LOCK:
            _HEALTH_CACHE["payload"] = payload
            _HEALTH_CACHE["expires_at"] = now + OPENVIKING_HEALTH_CACHE_TTL_SEC
        return payload
    try:
        result = subprocess.run(
            [sys.executable, RECALL_SCRIPT_PATH, "--health"],
            capture_output=True,
            text=True,
            timeout=12,
        )
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
        with _CACHE_LOCK:
            _HEALTH_CACHE["payload"] = payload
            _HEALTH_CACHE["expires_at"] = now + OPENVIKING_HEALTH_CACHE_TTL_SEC
        return payload

    output = (result.stdout or "").strip()
    if result.returncode != 0:
        payload = {"ok": False, "error": output or (result.stderr or "").strip()}
        with _CACHE_LOCK:
            _HEALTH_CACHE["payload"] = payload
            _HEALTH_CACHE["expires_at"] = now + OPENVIKING_HEALTH_CACHE_TTL_SEC
        return payload

    start = output.find("{")
    end = output.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(output[start : end + 1])
            result_payload = {
                "ok": bool(payload.get("recall_db_exists")),
                "sessions": payload.get("total_sessions", 0),
                "messages": payload.get("total_messages", 0),
                "indexed_this_run": payload.get("indexed_this_run", 0),
                "db": payload.get("recall_db"),
            }
            with _CACHE_LOCK:
                _HEALTH_CACHE["payload"] = result_payload
                _HEALTH_CACHE["expires_at"] = now + OPENVIKING_HEALTH_CACHE_TTL_SEC
            return result_payload
        except Exception:
            pass
    payload = {"ok": "Indexed" in output or "total_sessions" in output, "raw": output[:300]}
    with _CACHE_LOCK:
        _HEALTH_CACHE["payload"] = payload
        _HEALTH_CACHE["expires_at"] = now + OPENVIKING_HEALTH_CACHE_TTL_SEC
    return payload


def _sqlite_search(query: str, search_type: str, limit: int, no_regex: bool) -> str:
    if not os.path.exists(ALINE_DB_PATH):
        return f"OneContext fallback failed: DB not found at {ALINE_DB_PATH}"

    use_regex = not no_regex
    regex_obj = None
    if use_regex:
        # Guard against ReDoS: reject overly complex patterns
        if len(query) > 200:
            use_regex = False
        else:
            try:
                regex_obj = re.compile(query, re.IGNORECASE)
            except re.error as exc:
                return f"OneContext fallback failed: invalid regex `{query}` ({exc})"

    def _matched(text: str) -> bool:
        if not text:
            return False
        if use_regex:
            return bool(regex_obj.search(text))
        return query.lower() in text.lower()

    results: list[dict[str, Any]] = []
    hard_limit = max(limit, 1) * 8

    conn = None
    try:
        conn = sqlite3.connect(ALINE_DB_PATH, timeout=SQLITE_CONNECT_TIMEOUT_SEC)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Read-only query path: reduce lock contention with active writer processes.
        cur.execute("PRAGMA query_only=1")

        if search_type in ("all", "event"):
            rows = cur.execute(
                """
                SELECT id, title, description, created_at
                FROM events
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (hard_limit,),
            ).fetchall()
            for r in rows:
                text = f"{r['id'] or ''}\n{r['title'] or ''}\n{r['description'] or ''}"
                if _matched(text):
                    results.append(
                        {
                            "type": "event",
                            "id": r["id"],
                            "title": r["title"] or "",
                            "snippet": _build_snippet(text, query, use_regex),
                        }
                    )

        if search_type in ("all", "session"):
            rows = cur.execute(
                """
                SELECT id, session_type, session_title, session_summary, created_at
                FROM sessions
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (hard_limit,),
            ).fetchall()
            for r in rows:
                text = f"{r['id'] or ''}\n{r['session_title'] or ''}\n{r['session_summary'] or ''}"
                if _matched(text):
                    results.append(
                        {
                            "type": "session",
                            "id": r["id"],
                            "title": f"{r['session_type']} | {(r['session_title'] or '').strip()}",
                            "snippet": _build_snippet(text, query, use_regex),
                        }
                    )

        if search_type in ("all", "turn"):
            rows = cur.execute(
                """
                SELECT t.id, t.session_id, t.turn_number, t.llm_title, t.user_message, t.assistant_summary
                FROM turns t
                ORDER BY t.created_at DESC
                LIMIT ?
                """,
                (hard_limit * 2,),
            ).fetchall()
            for r in rows:
                text = (
                    f"{r['id'] or ''}\n{r['session_id'] or ''}\n"
                    f"{r['llm_title'] or ''}\n{r['user_message'] or ''}\n{r['assistant_summary'] or ''}"
                )
                if _matched(text):
                    results.append(
                        {
                            "type": "turn",
                            "id": r["id"],
                            "title": f"session={r['session_id']} turn={r['turn_number']}",
                            "snippet": _build_snippet(text, query, use_regex),
                        }
                    )

        if search_type in ("all", "content"):
            rows = cur.execute(
                """
                SELECT tc.turn_id, t.session_id, t.turn_number, substr(tc.content, 1, 60000) AS content_excerpt
                FROM turn_content tc
                JOIN turns t ON t.id = tc.turn_id
                ORDER BY t.created_at DESC
                LIMIT ?
                """,
                (max(hard_limit, 40),),
            ).fetchall()
            for r in rows:
                text = f"{r['turn_id'] or ''}\n{r['session_id'] or ''}\n{r['content_excerpt'] or ''}"
                if _matched(text):
                    results.append(
                        {
                            "type": "content",
                            "id": r["turn_id"],
                            "title": f"session={r['session_id']} turn={r['turn_number']}",
                            "snippet": _build_snippet(text, query, use_regex, radius=120),
                        }
                    )
    except Exception as exc:
        return f"OneContext fallback failed: sqlite query error ({exc})"
    finally:
        if conn:
            conn.close()

    if not results:
        return "No matches found in OneContext history (sqlite fallback)."

    lines = [f"--- ONECONTEXT SEARCH RESULTS (sqlite fallback: {ALINE_DB_PATH}) ---"]
    for item in results[:limit]:
        lines.append(f"[{item['type']}] {item['id']} | {item['title']}")
        lines.append(f"  {item['snippet']}")
    lines.append(f"\nFound {len(results)} matches (showing up to {limit}).")
    return "\n".join(lines)


@mcp.tool()
def save_conversation_memory(title: str, content: str, tags: list[str] | str | None = None) -> str:
    """
    Save a generalized conversation summary or key conclusions to OpenViking.
    """
    title = (title or "").strip()
    content = (content or "").strip()
    if not title:
        return "Failed to save memory: title cannot be empty."
    if not content:
        return "Failed to save memory: content cannot be empty."
    tags = _normalize_tags(tags)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename_title = _safe_filename(title)
    uri = f"viking://resources/shared/conversations/{timestamp}_{filename_title}.md"

    local_path = os.path.join(LOCAL_STORAGE_ROOT, "resources", "shared", "conversations")
    os.makedirs(local_path, exist_ok=True)
    try:
        os.chmod(local_path, 0o700)
    except OSError:
        pass
    file_path = os.path.join(local_path, f"{timestamp}_{filename_title}.md")

    formatted_content = f"# {title}\n\nTags: {', '.join(tags)}\nDate: {datetime.now().isoformat()}\n\n{content}\n"
    _secure_write_text(file_path, formatted_content)
    with _CACHE_LOCK:
        _LOCAL_SCAN_CACHE["expires_at"] = 0.0
        _LOCAL_SCAN_CACHE["files"] = []
        _LOCAL_SCAN_CACHE["root_mtime"] = 0.0

    target = uri.rsplit("/", 1)[0]
    payload = {
        "path": file_path,
        "target": target,
        "reason": "save_conversation",
        "instruction": f"Index global conversation memory: {title}",
    }

    try:
        response = HTTP_CLIENT.post(f"{OPENVIKING_URL}/resources", json=payload)
        response.raise_for_status()
        return f"Successfully saved memory to OpenViking: {uri}"
    except Exception as e:
        return f"Saved to local file {file_path}, but failed to index in OpenViking: {str(e)}"


@mcp.tool()
def query_viking_memory(query: str, limit: int = 3) -> str:
    """
    Search OpenViking global memory for relevant context.
    """
    if not _decide_retrieval_intent(query):
        return "Intent Check: Query categorized as common affirmation/chat. Skipped memory retrieval to save context."

    safe_limit = max(1, min(int(limit), 50))
    output = []

    # Lightweight local-first retrieval.
    exact_matches = _local_exact_resource_matches(query, limit=max(1, safe_limit))
    if exact_matches:
        output.append("--- LOCAL MEMORY MATCHES ---")
        for item in exact_matches:
            output.append(json.dumps(item, ensure_ascii=False, indent=2))
        return "\n".join(output)

    # Keep previous semantic behavior optional; do not require it.
    if OPENVIKING_ENABLE_SEMANTIC_QUERY:
        payload = {
            "query": query,
            "target_uri": "viking://resources",
            "limit": safe_limit,
        }
        try:
            response = HTTP_CLIENT.post(f"{OPENVIKING_URL}/search/find", json=payload)
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "ok":
                resources = data.get("result", {}).get("resources", [])
                memories = data.get("result", {}).get("memories", [])
                if resources:
                    output.append("--- FOUND RESOURCES ---")
                    for r in resources:
                        output.append(json.dumps(r, ensure_ascii=False, indent=2))
                if memories:
                    output.append("--- FOUND MEMORIES ---")
                    for m in memories:
                        output.append(json.dumps(m, ensure_ascii=False, indent=2))
                if output:
                    return "\n".join(output)
        except Exception:
            pass

    # Final fallback: search session history via recall/onecontext-compatible chain.
    history = search_onecontext_history(query=query, search_type="content", limit=min(safe_limit, 10), no_regex=True)
    if not _onecontext_no_match(history):
        return "--- HISTORY CONTENT FALLBACK ---\n" + history
    return "No relevant context found in local memory and history."


@mcp.tool()
def search_onecontext_history(query: str, search_type: str = "all", limit: int = 10, no_regex: bool = False) -> str:
    """
    Search OneContext history. CLI-first, sqlite fallback for compatibility.
    """
    if not _decide_retrieval_intent(query):
        return "Intent Check: Query categorized as common affirmation/chat. Skipped sqlite DB retrieval to save context."

    normalized_type = _resolve_search_type(search_type)
    safe_limit = max(1, min(int(limit), 100))
    query_variants = _build_query_variants(query)
    if not query_variants:
        return "Empty query."
    started_at = time.monotonic()

    # For date-like inputs, literal search tends to be significantly more stable.
    date_like = any(re.fullmatch(r"\d{8}", v) for v in query_variants)
    prefer_literal_first = date_like and not no_regex

    def _try_cli_many(qs: list[str], stype: str, literal: bool) -> str:
        for q in qs:
            if time.monotonic() - started_at >= ONECONTEXT_SEARCH_BUDGET_SEC:
                break
            r = _try_cli_search(q, stype, safe_limit, literal)
            if r and not _onecontext_no_match(r):
                return r
        return ""

    def _try_sqlite_many(qs: list[str], stype: str, literal: bool) -> str:
        for q in qs:
            if time.monotonic() - started_at >= ONECONTEXT_SEARCH_BUDGET_SEC:
                break
            r = _sqlite_search(q, stype, safe_limit, literal)
            if not _onecontext_no_match(r):
                return r
        return ""

    # Stage 1: CLI with caller-specified regex mode
    cli_result = _try_cli_many(query_variants, normalized_type, True if prefer_literal_first else no_regex)
    if cli_result:
        if prefer_literal_first:
            return "Note: auto-switched to literal search for date-like query.\n" + cli_result
        return cli_result

    # Stage 2: CLI no-regex fallback when caller did not force it
    if not no_regex and not prefer_literal_first:
        cli_literal = _try_cli_many(query_variants, normalized_type, True)
        if cli_literal:
            return (
                "Note: auto-fallback to OneContext literal search (--no-regex).\n"
                + cli_literal
            )

    # Stage 3: CLI deep content fallback for broad/noisy queries
    if normalized_type == "all":
        cli_content = _try_cli_many(query_variants, "content", True if prefer_literal_first else no_regex)
        if cli_content:
            if prefer_literal_first:
                return (
                    "Note: auto-switched to content literal search for date-like query.\n"
                    + cli_content
                )
            return (
                "Note: auto-fallback to OneContext content search after `all` returned no matches.\n"
                + cli_content
            )
        if not no_regex and not prefer_literal_first:
            cli_content_literal = _try_cli_many(query_variants, "content", True)
            if cli_content_literal:
                return (
                    "Note: auto-fallback to OneContext content literal search (--no-regex).\n"
                    + cli_content_literal
                )

    # Stage 4: sqlite fallback with caller-specified regex mode
    sqlite_result = _try_sqlite_many(query_variants, normalized_type, True if prefer_literal_first else no_regex)
    if sqlite_result:
        if prefer_literal_first:
            return "Note: auto-switched to sqlite literal search for date-like query.\n" + sqlite_result
        return sqlite_result

    # Stage 5: sqlite no-regex fallback
    if not no_regex and not prefer_literal_first:
        sqlite_literal = _try_sqlite_many(query_variants, normalized_type, True)
        if sqlite_literal:
            return (
                "Note: auto-fallback to sqlite literal search (--no-regex).\n"
                + sqlite_literal
            )

    # Stage 6: sqlite content fallback
    if normalized_type == "all":
        sqlite_content = _try_sqlite_many(query_variants, "content", True if prefer_literal_first else no_regex)
        if sqlite_content:
            if prefer_literal_first:
                return (
                    "Note: auto-switched to sqlite content literal search for date-like query.\n"
                    + sqlite_content
                )
            return (
                "Note: auto-fallback to sqlite content search after `all` returned no matches.\n"
                + sqlite_content
            )
        if not no_regex and not prefer_literal_first:
            sqlite_content_literal = _try_sqlite_many(query_variants, "content", True)
            if sqlite_content_literal:
                return (
                    "Note: auto-fallback to sqlite content literal search (--no-regex).\n"
                    + sqlite_content_literal
                )

    return (
        f"No matches found after fallback chain (budget={ONECONTEXT_SEARCH_BUDGET_SEC}s). "
        "Try a shorter keyword, or search by `-t content --no-regex`."
    )


@mcp.tool()
def context_system_health() -> str:
    """
    Unified health snapshot for recall-lite + onecontext compatibility.
    """
    report: dict[str, Any] = {
        "checked_at": datetime.now().isoformat(),
        "recall_lite": {"ok": False},
        "onecontext_compat": {"ok": False},
        "openviking_optional": {"ok": False},
    }

    report["recall_lite"] = _probe_recall_health()

    onecontext_bin = shutil.which("onecontext") or os.path.expanduser("~/.local/bin/onecontext")
    onecontext_ok = bool(onecontext_bin and os.path.exists(onecontext_bin))
    report["onecontext_compat"] = {"ok": onecontext_ok, "bin": onecontext_bin if onecontext_ok else None}

    # Optional probe only; do not fail whole health if old stack is down.
    try:
        resp = HTTP_CLIENT.get(f"{OPENVIKING_ROOT_URL}/health", timeout=3)
        report["openviking_optional"] = {"ok": resp.status_code == 200, "status_code": resp.status_code}
    except Exception as exc:
        report["openviking_optional"] = {"ok": False, "error": str(exc)}

    report["all_ok"] = bool(report["recall_lite"].get("ok") and report["onecontext_compat"].get("ok"))
    return json.dumps(report, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
