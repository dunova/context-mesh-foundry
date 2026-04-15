#!/usr/bin/env python3
"""ContextGO automatic context prewarm engine.

Zero-config, zero-understanding-cost: install → ``contextgo setup`` → done.
Every new conversation auto-recalls relevant memories before the AI starts work.

Architecture:
- ``prewarm()``  — core: extract keywords from user message, search memory, return
  branded summary suitable for injection as hook output.
- ``setup()``    — one-command configuration of all detected AI coding tools.
- ``unsetup()``  — remove all hooks and SCF policy blocks.
- Brand output   — all prewarm activity prefixed with ``[ContextGO]``.

Hook integration (Claude Code):
  ``UserPromptSubmit`` hook calls ``contextgo prewarm``.
  stdin receives JSON ``{"prompt": {"content": "..."}}``.
  stdout is injected as ``<user-prompt-submit-hook>`` into the conversation.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

_logger = logging.getLogger(__name__)

__all__ = [
    "extract_keywords",
    "prewarm",
    "prewarm_from_stdin",
    "setup_all",
    "setup_claude_code",
    "setup_cursor",
    "teardown_all",
    "teardown_claude_code",
    "teardown_cursor",
]

# ───────────────────────────────────────────────
# Brand constants
# ───────────────────────────────────────────────

BRAND = "ContextGO"
_PREWARM_START = f"[{BRAND}] 正在召回相关记忆..."
_PREWARM_DONE = f"[{BRAND}] 上下文预热完成"
_PREWARM_EMPTY = f"[{BRAND}] 上下文预热完成 — 记忆库暂无相关记录"
_SETUP_BANNER = f"[{BRAND}] 自动预热配置"

# Max stdin read size (1 MB) to prevent resource exhaustion.
_MAX_STDIN_BYTES = 1_048_576
_PREWARM_STATE_TTL_SEC = 20 * 60
_SAME_TOPIC_OVERLAP = 0.4
_NEW_TOPIC_OVERLAP = 0.25

_ACK_ONLY_RE = re.compile(
    r"^(ok|okay|kk|yes|yep|nope|好的|收到|明白|知道了|行|好|嗯|哦|谢谢|thanks|thank you)[!,. ]*$",
    re.IGNORECASE,
)
_CONTINUATION_RE = re.compile(
    r"(continue|resume|pick up|follow up|handoff|what was i doing|current status|"
    r"继续|接着|续上|上次|之前|刚才|交接|当前状态|我们做到哪了)",
    re.IGNORECASE,
)
_STRUCTURAL_RE = re.compile(
    r"(architecture|flow|dependency|blast radius|impact|call graph|caller|callee|refactor|"
    r"where is|which file|module|function|class|graph|架构|流程|依赖|影响|调用链|重构|"
    r"模块|函数|类|在哪个文件|哪个文件|哪个模块)",
    re.IGNORECASE,
)
_IDENTIFIER_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]{2,}|[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)")

# Chinese + common programming stop words to skip when extracting keywords.
_STOP_WORDS: frozenset[str] = frozenset(
    [
        # Chinese
        "的",
        "了",
        "在",
        "是",
        "我",
        "有",
        "和",
        "就",
        "不",
        "人",
        "都",
        "一",
        "一个",
        "上",
        "也",
        "很",
        "到",
        "说",
        "要",
        "去",
        "你",
        "会",
        "着",
        "没有",
        "看",
        "好",
        "自己",
        "这",
        "他",
        "她",
        "它",
        "们",
        "么",
        "那",
        "被",
        "它们",
        "些",
        "呢",
        "吗",
        "啊",
        "嗯",
        "哦",
        "吧",
        "哈",
        "嘛",
        "帮",
        "帮我",
        "请",
        "请帮",
        "一下",
        "现在",
        "然后",
        "还有",
        "看看",
        "可以",
        "能不能",
        "怎么",
        "如何",
        "什么",
        # English
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "may",
        "might",
        "must",
        "can",
        "could",
        "need",
        "dare",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "he",
        "him",
        "his",
        "she",
        "her",
        "it",
        "its",
        "they",
        "them",
        "their",
        "this",
        "that",
        "these",
        "those",
        "what",
        "which",
        "who",
        "whom",
        "how",
        "when",
        "where",
        "why",
        "and",
        "or",
        "but",
        "not",
        "so",
        "if",
        "then",
        "else",
        "for",
        "at",
        "by",
        "from",
        "in",
        "on",
        "to",
        "with",
        "as",
        "of",
    ]
)

# CJK Unicode ranges for character-level splitting.
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

# Minimum keyword length after stripping.
_MIN_KW_LEN = 2


# ───────────────────────────────────────────────
# Keyword extraction
# ───────────────────────────────────────────────


def extract_keywords(text: str, *, max_keywords: int = 6) -> list[str]:
    """Extract meaningful search keywords from a user message.

    Strategy: split on whitespace and punctuation, with CJK characters split
    into bigrams for better Chinese keyword extraction.  Remove stop words,
    keep the longest (most specific) tokens.  Simple and fast — no NLP needed.
    """
    # Split into raw tokens: word chars + CJK.
    raw_tokens = re.findall(r"[\w\u4e00-\u9fff\u3400-\u4dbf]+", text.lower())

    # Expand CJK-only tokens into bigrams for better recall.
    tokens: list[str] = []
    for t in raw_tokens:
        if _CJK_RE.fullmatch(t):
            # Single CJK character — keep as-is (will be filtered by length).
            tokens.append(t)
        elif _CJK_RE.search(t) and len(t) > 1:
            # Mixed or pure CJK multi-char: extract CJK bigrams + non-CJK parts.
            cjk_chars = _CJK_RE.findall(t)
            non_cjk = _CJK_RE.sub("", t)
            # CJK bigrams.
            for i in range(len(cjk_chars) - 1):
                tokens.append(cjk_chars[i] + cjk_chars[i + 1])
            # Keep full CJK run if ≥ 2 chars.
            cjk_run = "".join(cjk_chars)
            if len(cjk_run) >= _MIN_KW_LEN:
                tokens.append(cjk_run)
            # Non-CJK part.
            if non_cjk and len(non_cjk) >= _MIN_KW_LEN:
                tokens.append(non_cjk)
        else:
            tokens.append(t)

    seen: set[str] = set()
    keywords: list[str] = []
    for t in tokens:
        if len(t) < _MIN_KW_LEN:
            continue
        if t in _STOP_WORDS:
            continue
        if t in seen:
            continue
        seen.add(t)
        keywords.append(t)

    # Prefer longer (more specific) keywords.
    keywords.sort(key=len, reverse=True)
    return keywords[:max_keywords]


def _safe_cwd() -> Path:
    with contextlib.suppress(OSError):
        return Path.cwd().resolve()
    return Path.home()


def _storage_root_path() -> Path | None:
    try:
        try:
            from context_config import storage_root as _sr  # type: ignore[import-not-found]
        except ImportError:
            from contextgo.context_config import storage_root as _sr  # type: ignore[import-not-found]

        return Path(_sr())
    except Exception:
        _logger.debug("Prewarm state path unavailable", exc_info=True)
        return None


def _prewarm_state_path(cwd: Path | None = None) -> Path | None:
    root = _storage_root_path()
    if root is None:
        return None
    workspace = str((cwd or _safe_cwd()).resolve())
    digest = hashlib.sha256(workspace.encode("utf-8")).hexdigest()[:12]
    state_dir = root / "state" / "prewarm"
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return state_dir / f"{digest}.json"


def _load_prewarm_state(cwd: Path | None = None) -> dict[str, Any]:
    state_path = _prewarm_state_path(cwd)
    if state_path is None or not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_prewarm_state(message: str, keywords: list[str], reason: str, cwd: Path | None = None) -> None:
    state_path = _prewarm_state_path(cwd)
    if state_path is None:
        return
    payload = {
        "message": message[:500],
        "keywords": keywords[:8],
        "reason": reason,
        "timestamp": int(time.time()),
        "cwd": str((cwd or _safe_cwd()).resolve()),
    }
    try:
        _atomic_write(state_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        _logger.debug("Failed to persist prewarm state", exc_info=True)


def _keyword_overlap(current: list[str], previous: list[str]) -> float:
    current_set = {item for item in current if item}
    previous_set = {item for item in previous if item}
    if not current_set or not previous_set:
        return 0.0
    union = current_set | previous_set
    if not union:
        return 0.0
    return len(current_set & previous_set) / len(union)


def _should_skip_trivial_message(message: str, keywords: list[str]) -> bool:
    normalized = re.sub(r"\s+", " ", message.strip().lower())
    if not normalized:
        return True
    if len(normalized) <= 24 and _ACK_ONLY_RE.fullmatch(normalized):
        return True
    return len(keywords) < 2 and len(normalized) < 12 and not _CONTINUATION_RE.search(normalized)


def _classify_prewarm(
    message: str,
    keywords: list[str],
    *,
    cwd: Path | None = None,
) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", message.strip().lower())
    if _should_skip_trivial_message(message, keywords):
        return {"trigger": False, "reason": "trivial", "graph_hint": False, "limit": 0}

    state = _load_prewarm_state(cwd)
    previous_keywords = state.get("keywords", [])
    if not isinstance(previous_keywords, list):
        previous_keywords = []
    previous_ts = int(state.get("timestamp", 0) or 0)
    is_recent = (int(time.time()) - previous_ts) < _PREWARM_STATE_TTL_SEC
    overlap = _keyword_overlap(keywords, [str(item) for item in previous_keywords])
    continuation = bool(_CONTINUATION_RE.search(normalized))
    structural = bool(_STRUCTURAL_RE.search(normalized))
    identifier_heavy = bool(_IDENTIFIER_RE.search(message))

    if continuation:
        return {"trigger": True, "reason": "continuation", "graph_hint": structural, "limit": 3}

    if structural:
        if is_recent and overlap >= _SAME_TOPIC_OVERLAP:
            _save_prewarm_state(message, keywords, "same-topic-structural", cwd)
            return {"trigger": False, "reason": "same-topic", "graph_hint": True, "limit": 0}
        return {"trigger": True, "reason": "structural", "graph_hint": True, "limit": 2}

    if not state:
        return {"trigger": True, "reason": "cold-start", "graph_hint": False, "limit": 3}

    if is_recent and overlap >= _SAME_TOPIC_OVERLAP:
        _save_prewarm_state(message, keywords, "same-topic", cwd)
        return {"trigger": False, "reason": "same-topic", "graph_hint": False, "limit": 0}

    if identifier_heavy or overlap <= _NEW_TOPIC_OVERLAP:
        return {"trigger": True, "reason": "new-topic", "graph_hint": False, "limit": 3}

    if not is_recent:
        return {"trigger": True, "reason": "stale-context", "graph_hint": False, "limit": 3}

    _save_prewarm_state(message, keywords, "same-topic", cwd)
    return {"trigger": False, "reason": "same-topic", "graph_hint": False, "limit": 0}


def _reason_label(reason: str) -> str:
    return {
        "continuation": "续做/历史任务",
        "structural": "结构化问题",
        "cold-start": "新窗口/冷启动",
        "new-topic": "检测到新主题",
        "stale-context": "上下文已过期",
    }.get(reason, "相关任务")


def _trim_session_results(session_text: str, *, max_items: int = 3) -> list[str]:
    lines = session_text.splitlines()
    kept: list[str] = []
    current_block: list[str] = []
    blocks: list[list[str]] = []
    for line in lines:
        if re.match(r"^\[\d+\]\s", line):
            if current_block:
                blocks.append(current_block)
            current_block = [line]
            continue
        if not current_block:
            continue
        if line.lstrip().startswith("File:"):
            continue
        if line.lstrip().startswith(">") or line.startswith("    >"):
            current_block.append(line.strip())
    if current_block:
        blocks.append(current_block)
    for block in blocks[:max_items]:
        kept.extend(block)
    return kept


def _session_query_terms(message: str) -> list[str]:
    try:
        try:
            import session_index as _si  # type: ignore[import-not-found]
        except ImportError:
            from contextgo import session_index as _si  # type: ignore[import-not-found]
        return _si.build_query_terms(message)
    except Exception:
        _logger.debug("Session query term extraction unavailable", exc_info=True)
        return []


def _term_priority(term: str) -> tuple[int, int]:
    identifier_like = bool(_IDENTIFIER_RE.fullmatch(term) or "/" in term or "_" in term or "." in term)
    cjk = bool(_CJK_RE.search(term))
    signal = 3 if identifier_like else 2 if cjk else 1
    return (signal, len(term))


def _build_recall_queries(message: str, keywords: list[str], *, max_queries: int = 4) -> list[str]:
    pool = _session_query_terms(message)
    for kw in keywords:
        if kw not in pool:
            pool.append(kw)

    deduped: list[str] = []
    seen: set[str] = set()
    for term in sorted(pool, key=_term_priority, reverse=True):
        clean = term.strip()
        lower = clean.lower()
        if len(clean) < 2 or lower in seen:
            continue
        seen.add(lower)
        deduped.append(clean)

    if not deduped:
        return []

    queries: list[str] = []

    def add(query: str) -> None:
        q = query.strip()
        if not q or q in queries:
            return
        queries.append(q)

    if len(deduped) >= 2:
        add(f"{deduped[0]} {deduped[1]}")
    add(deduped[0])
    if len(deduped) >= 2:
        add(deduped[1])
    if len(deduped) >= 3:
        add(deduped[2])
    return queries[:max_queries]


def _search_memory_candidates(query_candidates: list[str], *, limit: int, shared_root: Path) -> list[dict[str, Any]]:
    try:
        try:
            import context_core as _core  # type: ignore[import-not-found]
        except ImportError:
            from contextgo import context_core as _core  # type: ignore[import-not-found]
    except Exception:
        _logger.debug("Memory search path unavailable", exc_info=True)
        return []

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in query_candidates:
        try:
            matches = _core.local_memory_matches(
                query,
                shared_root=shared_root,
                limit=max(limit * 2, 3),
                max_files=200,
                read_bytes=8192,
                uri_prefix="local://",
            )
        except Exception:
            _logger.debug("Memory candidate search failed: %s", query, exc_info=True)
            continue
        for item in matches:
            key = str(item.get("file_path") or item.get("uri_hint") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            results.append(item)
            if len(results) >= limit:
                return results
    return results


def _search_session_candidates(query_candidates: list[str], *, limit: int) -> list[dict[str, Any]]:
    try:
        try:
            import session_index as _si  # type: ignore[import-not-found]
        except ImportError:
            from contextgo import session_index as _si  # type: ignore[import-not-found]
    except Exception:
        _logger.debug("Session index path unavailable", exc_info=True)
        return []

    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for query in query_candidates:
        try:
            rows = _si._search_rows(query, limit=max(limit * 3, 6), literal=True)  # type: ignore[attr-defined]
        except Exception:
            _logger.debug("Session candidate search failed: %s", query, exc_info=True)
            continue
        for row in rows:
            key = (
                str(row.get("source_type", "")),
                str(row.get("session_id", "")),
                str(row.get("file_path", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            results.append(row)
            if len(results) >= limit:
                return results
    return results


def _format_session_rows(session_results: list[dict[str, Any]]) -> str:
    if not session_results:
        return ""
    lines = [f"Found {len(session_results)} sessions (local index):"]
    for idx, row in enumerate(session_results, 1):
        lines.append(f"[{idx}] {str(row.get('created_at', ''))[:10]} | {row.get('session_id', '')} | {row.get('source_type', '')}")
        lines.append(f"    {row.get('title', '')}")
        lines.append(f"    File: {row.get('file_path', '')}")
        lines.append(f"    > {row.get('snippet', '')}")
    return "\n".join(lines)


# ───────────────────────────────────────────────
# Core prewarm
# ───────────────────────────────────────────────


def prewarm(message: str, *, limit: int = 5, timeout: float = 2.0, cwd: Path | None = None) -> str:
    """Run context prewarm for a user message.  Returns branded output.

    Searches memory files first (fast path), then falls back to session index.
    Total wall time is bounded by *timeout* seconds.

    Returns empty string if nothing relevant is found (silent to user).
    """
    cwd = cwd or _safe_cwd()
    keywords = extract_keywords(message)
    if not keywords:
        return ""

    decision = _classify_prewarm(message, keywords, cwd=cwd)
    if not decision["trigger"]:
        return ""

    limit = min(limit, int(decision["limit"]) or limit)

    query_candidates = _build_recall_queries(message, keywords)
    if not query_candidates:
        return ""
    t0 = time.monotonic()

    # ── Search paths (parallel, bounded by timeout) ──────────────
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: list[dict[str, Any]] = []
    session_results: list[dict[str, Any]] = []

    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cg-prewarm")
    try:
        futures: dict[str, Any] = {}

        # Path 1: local memory files (preferred).
        try:
            try:
                from context_config import storage_root as _sr  # type: ignore[import-not-found]
            except ImportError:
                from contextgo.context_config import storage_root as _sr  # type: ignore[import-not-found]

            shared_root = _sr() / "resources" / "shared"
            futures["memory"] = pool.submit(
                _search_memory_candidates,
                query_candidates,
                limit=limit,
                shared_root=shared_root,
            )
        except Exception:
            _logger.debug("Memory search path unavailable", exc_info=True)

        # Path 2: session index FTS.
        try:
            futures["session"] = pool.submit(
                _search_session_candidates,
                query_candidates,
                limit=min(limit, 10),
            )
        except Exception:
            _logger.debug("Session index path unavailable", exc_info=True)

        remaining = max(0.1, timeout - (time.monotonic() - t0))
        try:
            for f in as_completed(futures.values(), timeout=remaining):
                key = next(k for k, v in futures.items() if v is f)
                try:
                    val = f.result(timeout=0.1)
                    if key == "memory" and isinstance(val, list):
                        results = val
                    elif key == "session" and isinstance(val, list):
                        session_results = val
                except Exception:  # noqa: BLE001
                    _logger.debug("Prewarm future %s failed", key, exc_info=True)
        except TimeoutError:
            _logger.debug("Prewarm search timed out after %.1fs", remaining)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    elapsed = time.monotonic() - t0

    # ── Format output ────────────────────────────────────────────
    output = _format_prewarm_output(
        results,
        _format_session_rows(session_results),
        elapsed,
        keywords,
        reason=str(decision["reason"]),
        graph_hint=bool(decision["graph_hint"]),
    )
    if output:
        _save_prewarm_state(message, keywords, str(decision["reason"]), cwd)
    return output


def _format_prewarm_output(
    memory_results: list[dict[str, Any]],
    session_text: str,
    elapsed: float,
    keywords: list[str],
    *,
    reason: str = "related-task",
    graph_hint: bool = False,
) -> str:
    """Format branded prewarm output."""
    lines: list[str] = []
    label = _reason_label(reason)

    if memory_results:
        trimmed = memory_results[:3]
        lines.append(f"{_PREWARM_DONE} ({elapsed:.1f}s) — {label}")
        lines.append(f"关键词: {', '.join(keywords[:4])}")
        if graph_hint:
            lines.append("建议: 这是结构类问题；若当前环境有 graph，先用 graph 看架构/影响半径，再用 ContextGO 查历史决策。")
        for item in trimmed:
            title = item.get("title", "Untitled")
            tags = item.get("tags", "")
            date = item.get("date", "")
            snippet = str(item.get("snippet", item.get("content", "")))[:90]
            line = f"- {date} | {title}"
            if tags:
                line += f" (tags: {tags})"
            lines.append(line)
            if snippet:
                lines.append(f"  > {snippet}")
        return "\n".join(lines)

    if session_text and not session_text.startswith("No matches found"):
        trimmed_lines = _trim_session_results(session_text, max_items=3)
        count = max(1, len([line for line in trimmed_lines if re.match(r"^\[\d+\]\s", line)]))
        lines.append(f"{_PREWARM_DONE} ({elapsed:.1f}s) — {label}，命中 {count} 条历史会话")
        lines.append(f"关键词: {', '.join(keywords[:4])}")
        if graph_hint:
            lines.append("建议: 若当前环境提供 graph，先用 graph 查调用链/影响半径，再回看下面的历史会话。")
        lines.extend(trimmed_lines)
        return "\n".join(lines)

    # Nothing found — stay silent (return empty string).
    return ""


# ───────────────────────────────────────────────
# Hook entry point (stdin → stdout)
# ───────────────────────────────────────────────


def prewarm_from_stdin() -> int:
    """Read Claude Code hook JSON from stdin, run prewarm, print results.

    Called by: ``contextgo prewarm`` (which is registered as a
    ``UserPromptSubmit`` hook in ``~/.claude/settings.json``).

    Returns 0 always (prewarm is advisory, never blocks the user message).
    """
    try:
        raw = sys.stdin.read(_MAX_STDIN_BYTES)
    except Exception:
        return 0

    if not raw.strip():
        return 0

    # Parse the hook payload — try several possible shapes.
    message = _extract_message_from_hook(raw)
    if not message or len(message.strip()) < 4:
        return 0

    output = prewarm(message, cwd=_safe_cwd())
    if output:
        print(output)
    return 0


def _extract_message_from_hook(raw: str) -> str:
    """Extract user message text from a Claude Code hook JSON payload."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Invalid JSON — return empty (don't echo raw input back).
        return ""

    # Claude Code format: {"prompt": {"content": "..."}}
    if isinstance(data, dict):
        prompt = data.get("prompt")
        if isinstance(prompt, dict):
            return prompt.get("content", "")
        if isinstance(prompt, str):
            return prompt
        # Fallback: top-level "content" or "message".
        return data.get("content", data.get("message", ""))
    return ""


# ───────────────────────────────────────────────
# Atomic file write helper
# ───────────────────────────────────────────────


def _atomic_write(filepath: Path, content: str) -> None:
    """Write *content* to *filepath* atomically via tmp + rename.

    Prevents TOCTOU race conditions and partial-write corruption.
    Raises OSError on failure.
    """
    # Resolve symlinks to write to the real target.
    real_path = filepath.resolve()
    fd, tmp_path = tempfile.mkstemp(
        dir=str(real_path.parent),
        prefix=".contextgo_",
        suffix=".tmp",
    )
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
        Path(tmp_path).replace(real_path)
    except BaseException:
        # Clean up temp file on any failure.
        with contextlib.suppress(OSError):
            Path(tmp_path).unlink(missing_ok=True)
        raise


# ───────────────────────────────────────────────
# One-command setup
# ───────────────────────────────────────────────

# Claude Code settings.json hook configuration.
_CLAUDE_CODE_HOOK = {
    "hooks": {
        "UserPromptSubmit": [
            {
                "matcher": "",
                "command": "contextgo prewarm",
            }
        ]
    }
}

# Codex AGENTS.md SCF policy block.
_SCF_MARKER_START = "<!-- SCF:CONTEXT-FIRST:START -->"
_SCF_MARKER_END = "<!-- SCF:CONTEXT-FIRST:END -->"
_SCF_POLICY_BLOCK = f"""{_SCF_MARKER_START}
## ⚠️ ContextGO Smart Recall Policy (HIGHEST PRIORITY)

目标：只在真正需要时调用 ContextGO；默认保持静默，避免每轮对话都灌上下文和浪费 token。

### 应当调用
- 新窗口 / 冷启动：第一次进入一个仓库或新任务
- 续做任务：`continue` / `接着做` / `上次做到哪` / `当前状态`
- 交接与历史：用户提到之前的决定、根因、方案、另一个 agent 的工作
- 主题切换：和当前主题重叠很低，明显进入新问题
- 结构问题：架构、依赖、调用链、影响半径、重构落点、`哪个文件/模块/函数`

### 不应调用
- 同一主题里的连续追问、澄清、微调
- 简短确认：`好的`、`收到`、`ok`、`谢谢`
- 纯闲聊、翻译、润色、礼貌回复
- 已经在当前窗口刚做过召回，且问题仍是同一主题

### 调用顺序
1. 若问题包含明确标识符、文件名、报错串、函数/类名：先 `contextgo search "<query>" --limit 5 --literal`
2. 若问题是续做、历史、主题级问题：用 `contextgo semantic "<topic>" --limit 3`
3. 若当前环境有 code graph，且问题是架构/调用链/影响半径/重构定位：
   先用 graph，看结构；再用 ContextGO 补历史决策与过往根因
4. 结果必须压缩成 2-3 句；禁止粘贴原始长输出
5. 在没有命中时静默继续，不要为了“显得勤奋”重复检索

### 检索启发
- 用户说“昨天 / 今天 / 上次 / 前天”时，先转成绝对日期再检索
- 若当前工作区明确，给查询补一个工作区锚点（如 `ContextGO`、`QuantX` 或当前目录名）
- 先试 2-3 条紧凑查询，不要一上来堆很多词：
  - `contextgo search "2026-04-14 ContextGO codex" --limit 5 --literal`
  - `contextgo search "2026-04-14 127.0.0.1:5050 monitoring codex" --limit 5 --literal`
  - `contextgo semantic "2026-04-14 Codex progress in ContextGO" --limit 3`
- 只有这些紧凑查询都无效时，才考虑回退到平台原生 session 查询或原始会话文件

### 强约束
- 禁止每次聊天都跑 ContextGO
- 禁止在没有历史需求时先扫全仓再说“我去查一下”
- 禁止盲扫 `~/`、`/Volumes/*` 等大目录

### 持久记忆
仅在以下情况保存：
- 已确认的根因
- 已拍板的架构决策
- 下一窗口高概率会复用的交接信息

保存命令：
`~/.local/bin/contextgo save --title "..." --content "..." --tags "..."`
{_SCF_MARKER_END}"""


def setup_claude_code() -> bool:
    """Configure Claude Code's ``~/.claude/settings.json`` with the prewarm hook.

    Merges the hook into existing settings without overwriting other config.
    Uses atomic write to prevent corruption.
    Returns True if the hook was installed or already present.
    """
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    existing: dict[str, Any] = {}
    if settings_path.exists():
        try:
            raw = settings_path.read_text(encoding="utf-8")
            existing = json.loads(raw)
            if not isinstance(existing, dict):
                _logger.warning("settings.json is not a dict, resetting")
                existing = {}
        except (json.JSONDecodeError, OSError) as exc:
            _logger.warning("Corrupt settings.json (%s), resetting to empty", exc)
            existing = {}

    hooks = existing.setdefault("hooks", {})
    upsub = hooks.setdefault("UserPromptSubmit", [])

    # Check if our hook is already registered.
    for entry in upsub:
        if isinstance(entry, dict) and "contextgo prewarm" in entry.get("command", ""):
            return True  # Already installed.

    upsub.append({"matcher": "", "command": "contextgo prewarm"})
    _atomic_write(
        settings_path,
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
    )
    return True


def teardown_claude_code() -> bool:
    """Remove the ContextGO prewarm hook from Claude Code settings.

    Returns True if the hook was removed or was already absent.
    """
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return True

    try:
        existing = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    if not isinstance(existing, dict):
        return True

    hooks = existing.get("hooks", {})
    upsub = hooks.get("UserPromptSubmit", [])

    original_len = len(upsub)
    upsub = [e for e in upsub if not (isinstance(e, dict) and "contextgo prewarm" in e.get("command", ""))]
    if len(upsub) == original_len:
        return True  # Was already absent.

    hooks["UserPromptSubmit"] = upsub
    _atomic_write(
        settings_path,
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
    )
    return True


def _inject_scf_policy(filepath: Path) -> bool:
    """Inject the SCF context-first policy block into a Markdown file.

    Idempotent: skips injection if the marker is already present with the
    absolute path. If an old version (bare 'contextgo' command) exists,
    it is replaced with the new absolute-path version.
    Uses atomic write to prevent corruption.
    Returns True if the file was modified or already has the policy.
    """
    real_path = filepath.resolve()
    if not real_path.parent.exists():
        return False

    content = ""
    if real_path.exists():
        try:
            content = real_path.read_text(encoding="utf-8")
        except OSError:
            return False

    if _SCF_MARKER_START in content:
        start_idx = content.index(_SCF_MARKER_START)
        end_marker = content.index(_SCF_MARKER_END, start_idx)
        if end_marker > start_idx:
            end_idx = end_marker + len(_SCF_MARKER_END)
            old_block = content[start_idx:end_idx]
            if old_block != _SCF_POLICY_BLOCK:
                updated = content[:start_idx] + _SCF_POLICY_BLOCK + content[end_idx:]
                try:
                    _atomic_write(filepath, updated)
                except OSError:
                    return False
                return True
        return True

    # Prepend policy block at file top for maximum priority.
    updated = _SCF_POLICY_BLOCK + "\n\n" + content.lstrip()
    try:
        _atomic_write(filepath, updated)
    except OSError:
        return False
    return True


def _remove_scf_policy(filepath: Path) -> bool:
    """Remove the SCF context-first policy block from a Markdown file.

    Returns True if the policy was removed or was already absent.
    """
    real_path = filepath.resolve()
    if not real_path.exists():
        return True

    try:
        content = real_path.read_text(encoding="utf-8")
    except OSError:
        return False

    if _SCF_MARKER_START not in content:
        return True  # Already absent.

    # Remove the block (marker start → marker end, plus surrounding blank lines).
    start_idx = content.index(_SCF_MARKER_START)
    end_idx = content.index(_SCF_MARKER_END) + len(_SCF_MARKER_END)
    # Also consume trailing newline.
    if end_idx < len(content) and content[end_idx] == "\n":
        end_idx += 1
    # Consume one preceding blank line.
    if start_idx >= 2 and content[start_idx - 2 : start_idx] == "\n\n":
        start_idx -= 1

    updated = content[:start_idx] + content[end_idx:]
    try:
        _atomic_write(filepath, updated)
    except OSError:
        return False
    return True


def _upsert_json_string_field(filepath: Path, field: str, content: str) -> bool:
    """Set a top-level JSON string field, preserving unrelated config."""
    real_path = filepath.resolve()
    if not real_path.parent.exists():
        return False
    data: dict[str, Any] = {}
    if real_path.exists():
        try:
            loaded = json.loads(real_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            return False
    if data.get(field) == content:
        return True
    data[field] = content
    try:
        _atomic_write(real_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        return False
    return True


def _remove_json_string_field(filepath: Path, field: str) -> bool:
    real_path = filepath.resolve()
    if not real_path.exists():
        return True
    try:
        loaded = json.loads(real_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(loaded, dict):
        return True
    if field not in loaded:
        return True
    loaded.pop(field, None)
    try:
        _atomic_write(real_path, json.dumps(loaded, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        return False
    return True


def setup_codex() -> bool:
    """Inject SCF policy into ``~/.codex/AGENTS.md``."""
    return _inject_scf_policy(Path.home() / ".codex" / "AGENTS.md")


def setup_openclaw() -> bool:
    """Inject SCF policy into ``~/.openclaw/workspace/AGENTS.md``."""
    return _inject_scf_policy(Path.home() / ".openclaw" / "workspace" / "AGENTS.md")


def setup_claude_md() -> bool:
    """Inject SCF policy into ``~/.claude/CLAUDE.md``."""
    return _inject_scf_policy(Path.home() / ".claude" / "CLAUDE.md")


def teardown_codex() -> bool:
    """Remove SCF policy from ``~/.codex/AGENTS.md``."""
    return _remove_scf_policy(Path.home() / ".codex" / "AGENTS.md")


def teardown_openclaw() -> bool:
    """Remove SCF policy from ``~/.openclaw/workspace/AGENTS.md``."""
    return _remove_scf_policy(Path.home() / ".openclaw" / "workspace" / "AGENTS.md")


def teardown_claude_md() -> bool:
    """Remove SCF policy from ``~/.claude/CLAUDE.md``."""
    return _remove_scf_policy(Path.home() / ".claude" / "CLAUDE.md")


def _find_all_agents_md(home: Path, base: Path) -> list[Path]:
    """Find all AGENTS.md files under a base directory."""
    results: list[Path] = []
    if not base.is_dir():
        return results
    for p in base.rglob("AGENTS.md"):
        if p.is_file():
            results.append(p)
    return results


def _setup_scf_all_agents(home: Path, base: Path, tool_name: str) -> bool:
    """Inject SCF policy into all AGENTS.md files under base. Returns True if any modified or all already had it."""
    agents_files = _find_all_agents_md(home, base)
    if not agents_files:
        return False
    all_ok = True
    for af in agents_files:
        try:
            ok = _inject_scf_policy(af)
            if not ok:
                all_ok = False
        except OSError:
            all_ok = False
    return all_ok


def _teardown_scf_all_agents(home: Path, base: Path) -> bool:
    """Remove SCF policy from all AGENTS.md files under base."""
    agents_files = _find_all_agents_md(home, base)
    all_ok = True
    for af in agents_files:
        try:
            _remove_scf_policy(af)
        except OSError:
            all_ok = False
    return all_ok


def setup_accio() -> bool:
    """Inject SCF policy into all Accio agent AGENTS.md files."""
    accio_base = Path.home() / ".accio" / "accounts"
    return _setup_scf_all_agents(Path.home(), accio_base, "Accio")


def teardown_accio() -> bool:
    """Remove SCF policy from all Accio agent AGENTS.md files."""
    accio_base = Path.home() / ".accio" / "accounts"
    return _teardown_scf_all_agents(Path.home(), accio_base)


def setup_antigravity() -> bool:
    """Inject SCF policy into Antigravity GEMINI.md."""
    return _inject_scf_policy(Path.home() / ".gemini" / "GEMINI.md")


def teardown_antigravity() -> bool:
    """Remove SCF policy from Antigravity GEMINI.md."""
    return _remove_scf_policy(Path.home() / ".gemini" / "GEMINI.md")


def setup_opencode() -> bool:
    """Inject smart-recall instructions into OpenCode config files."""
    content = _SCF_POLICY_BLOCK
    candidates = [
        Path.home() / ".opencode" / "opencode.json",
        Path.home() / ".config" / "opencode" / "opencode.json",
    ]
    touched = False
    for config_file in candidates:
        if _upsert_json_string_field(config_file, "instructions", content):
            touched = True
    return touched


def teardown_opencode() -> bool:
    """Remove smart-recall instructions from OpenCode config files."""
    candidates = [
        Path.home() / ".opencode" / "opencode.json",
        Path.home() / ".config" / "opencode" / "opencode.json",
    ]
    ok = True
    for config_file in candidates:
        if not _remove_json_string_field(config_file, "instructions"):
            ok = False
    return ok


def setup_hermes() -> bool:
    """Inject smart-recall policy into Hermes global SOUL.md."""
    targets = [
        Path.home() / ".hermes" / "SOUL.md",
        Path.home() / ".hermes" / "hermes-agent" / "AGENTS.md",
    ]
    touched = False
    for target in targets:
        if _inject_scf_policy(target):
            touched = True
    return touched


def teardown_hermes() -> bool:
    """Remove smart-recall policy from Hermes global prompt files."""
    targets = [
        Path.home() / ".hermes" / "SOUL.md",
        Path.home() / ".hermes" / "hermes-agent" / "AGENTS.md",
    ]
    ok = True
    for target in targets:
        if not _remove_scf_policy(target):
            ok = False
    return ok


def setup_factory() -> bool:
    """Inject smart-recall policy into Factory/Droid prompt files."""
    factory_root = Path.home() / ".factory"
    touched = False
    if _inject_scf_policy(factory_root / "AGENTS.md"):
        touched = True
    droids_dir = factory_root / "droids"
    if droids_dir.is_dir():
        for md in droids_dir.glob("*.md"):
            if _inject_scf_policy(md):
                touched = True
    return touched


def teardown_factory() -> bool:
    """Remove smart-recall policy from Factory/Droid prompt files."""
    factory_root = Path.home() / ".factory"
    ok = _remove_scf_policy(factory_root / "AGENTS.md")
    droids_dir = factory_root / "droids"
    if droids_dir.is_dir():
        for md in droids_dir.glob("*.md"):
            if not _remove_scf_policy(md):
                ok = False
    return ok


def setup_copilot() -> bool:
    """Inject SCF policy into GitHub Copilot project-level instructions.

    Copilot reads .github/copilot-instructions.md from the project root.
    We inject into the most common project roots the user works with.
    """
    injected = False
    # Inject into common project roots
    for project_root in [Path.home() / "ContextGO", Path.home() / "QuantX", Path.home() / "happycapy" / "QuantX"]:
        instructions_file = project_root / ".github" / "copilot-instructions.md"
        if project_root.exists():
            instructions_file.parent.mkdir(parents=True, exist_ok=True)
            if _inject_scf_policy(instructions_file):
                injected = True
    return injected


def teardown_copilot() -> bool:
    """Remove SCF policy from GitHub Copilot project-level instructions."""
    removed = True
    for project_root in [Path.home() / "ContextGO", Path.home() / "QuantX", Path.home() / "happycapy" / "QuantX"]:
        instructions_file = project_root / ".github" / "copilot-instructions.md"
        if instructions_file.exists():
            if not _remove_scf_policy(instructions_file):
                removed = False
    return removed


def setup_cursor() -> bool:
    """Inject SCF policy into Cursor project-level .cursorrules files.

    Scans common project roots for .cursorrules files and injects the
    ContextGO context-first policy block.
    """
    injected = False
    # Common project roots - inject into each project's .cursorrules
    project_roots = [
        Path.home() / "ContextGO",
        Path.home() / "QuantX",
    ]
    # Add any other projects under ~/ that have .cursorrules
    try:
        for p in Path.home().iterdir():
            if p.is_dir() and not p.name.startswith("."):
                cursor_rules = p / ".cursorrules"
                if cursor_rules.exists() or p.name in ["happycapy", "workspace"]:
                    project_roots.append(p)
    except OSError:
        pass

    for project_root in project_roots:
        if not project_root.exists():
            continue
        rules_file = project_root / ".cursorrules"
        rules_file.parent.mkdir(parents=True, exist_ok=True)
        if _inject_scf_policy(rules_file):
            injected = True

    return injected


def teardown_cursor() -> bool:
    """Remove SCF policy from Cursor .cursorrules files."""
    removed = True
    project_roots = [
        Path.home() / "ContextGO",
        Path.home() / "QuantX",
    ]
    try:
        for p in Path.home().iterdir():
            if p.is_dir() and not p.name.startswith("."):
                project_roots.append(p)
    except OSError:
        pass

    for project_root in project_roots:
        if not project_root.exists():
            continue
        rules_file = project_root / ".cursorrules"
        if rules_file.exists():
            if not _remove_scf_policy(rules_file):
                removed = False

    return removed


def setup_all() -> dict[str, bool]:
    """Detect and configure all supported AI coding tools.

    Returns a dict mapping tool name → success boolean.
    """
    results: dict[str, bool] = {}

    # Claude Code — hook-based (strongest: system-enforced prewarm).
    results["Claude Code (hook)"] = setup_claude_code()

    # Claude Code — CLAUDE.md policy (fallback for tools that ignore hooks).
    results["Claude Code (policy)"] = setup_claude_md()

    # Codex CLI.
    results["Codex CLI"] = setup_codex()

    # OpenClaw.
    results["OpenClaw"] = setup_openclaw()

    # Accio Work — SCF policy into all agent AGENTS.md files.
    results["Accio"] = setup_accio()

    # OpenCode — smart-recall instructions in opencode.json.
    results["OpenCode"] = setup_opencode()

    # Antigravity (Gemini) — SCF policy into GEMINI.md.
    results["Antigravity"] = setup_antigravity()

    # Hermes — global SOUL.md and local agent AGENTS.md.
    results["Hermes"] = setup_hermes()

    # Factory / Droid — global and droid prompt markdowns.
    results["Factory Droid"] = setup_factory()

    # GitHub Copilot — SCF policy into project-level .github/copilot-instructions.md.
    results["GitHub Copilot"] = setup_copilot()

    # Cursor IDE — SCF policy into project-level .cursorrules.
    results["Cursor"] = setup_cursor()

    return results


def teardown_all() -> dict[str, bool]:
    """Remove all ContextGO hooks and SCF policy blocks.

    Returns a dict mapping tool name → success boolean.
    """
    results: dict[str, bool] = {}

    results["Claude Code (hook)"] = teardown_claude_code()
    results["Claude Code (policy)"] = teardown_claude_md()
    results["Codex CLI"] = teardown_codex()
    results["OpenClaw"] = teardown_openclaw()
    results["Accio"] = teardown_accio()
    results["OpenCode"] = teardown_opencode()
    results["Antigravity"] = teardown_antigravity()
    results["Hermes"] = teardown_hermes()
    results["Factory Droid"] = teardown_factory()
    results["GitHub Copilot"] = teardown_copilot()

    results["Cursor"] = teardown_cursor()

    return results
