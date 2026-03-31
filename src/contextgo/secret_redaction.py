#!/usr/bin/env python3
"""Secret / credential redaction utilities for ContextGO.

This module is the single source-of-truth for all regex-based secret
scrubbing performed before text is persisted to the local index or
exported to a remote endpoint.

Public API
----------
_SECRET_REPLACEMENTS : list[tuple[re.Pattern, str]]
    Ordered list of ``(compiled_pattern, replacement)`` pairs.  Each
    pattern is applied sequentially; the replacement may contain
    back-references (e.g. ``r"\\1***"``).

sanitize_text(text) -> str
    Apply all replacements to *text* and return the cleaned string.
    Does **not** truncate; callers are responsible for length limits.
"""

from __future__ import annotations

__all__ = ["_SECRET_REPLACEMENTS", "sanitize_text"]

import re

# ---------------------------------------------------------------------------
# Ordered list of (pattern, replacement) pairs applied during sanitisation.
# Compiled once at import time for efficiency.
#
# ORDERING MATTERS: specific token patterns MUST come before generic
# key=value patterns, otherwise "API_KEY=sk-ant-..." would have the
# value replaced by *** before the sk-ant- pattern gets a chance to
# produce the more informative "sk-ant-***" replacement.
# ---------------------------------------------------------------------------
_SECRET_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    # ===== SPECIFIC TOKEN PATTERNS (run first) =====
    # --- OpenAI / Anthropic keys ---
    (re.compile(r"\bsk-proj-[A-Za-z0-9_-]{16,}\b"), "sk-proj-***"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b"), "sk-ant-***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "sk-***"),
    # --- GitHub tokens ---
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "github_pat_***"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "ghp_***"),
    (re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"), "gho_***"),
    (re.compile(r"\bghs_[A-Za-z0-9]{20,}\b"), "ghs_***"),
    (re.compile(r"\bghr_[A-Za-z0-9]{20,}\b"), "ghr_***"),
    # --- GitLab PATs ---
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}\b"), "glpat-***"),
    # --- Google API keys ---
    (re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"), "AIza***"),
    # --- npm tokens ---
    (re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b"), "npm_***"),
    # --- HuggingFace tokens ---
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"), "hf_***"),
    # --- Slack tokens (xoxb-, xoxp-, xoxa-, xoxs-, xoxr-) ---
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"), "xox?-***"),
    # --- AWS access key IDs ---
    (re.compile(r"\b(?:AKIA|ASIA|AROA|AIPA|ANPA|ANVA|APKA)[A-Z0-9]{12,}\b"), "AKIA***"),
    # --- Stripe keys ---
    (re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{24,}\b"), "sk_***"),
    (re.compile(r"\brk_(?:live|test)_[A-Za-z0-9]{24,}\b"), "rk_***"),
    # --- SendGrid API keys ---
    (re.compile(r"\bSG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"), "SG.***"),
    # --- Twilio Account SID / Auth Token ---
    (re.compile(r"\bAC[a-f0-9]{32}\b"), "AC_twilio_***"),
    (re.compile(r"\bSK[a-f0-9]{32}\b"), "SK_twilio_***"),
    # --- PEM private keys ---
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "***PEM_KEY_REDACTED***",
    ),
    # --- JWT tokens ---
    (re.compile(r"\beyJhb[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"), "***JWT_REDACTED***"),
    # --- Azure SAS tokens ---
    (re.compile(r"([\?&]sig=)[A-Za-z0-9%+/=]{20,}", re.IGNORECASE), r"\1***"),
    # --- HashiCorp Vault tokens ---
    (re.compile(r"\bhvs\.[A-Za-z0-9_-]{20,}\b"), "hvs.***"),
    # --- Docker PATs ---
    (re.compile(r"\bdckr_pat_[A-Za-z0-9_-]{20,}\b"), "dckr_pat_***"),
    # --- Database connection strings ---
    (re.compile(r"((?:postgres|mysql|mongodb|redis)(?:ql)?://[^:]+:)[^\s@]+(@)", re.IGNORECASE), r"\1***@"),
    # ===== GENERIC KEY=VALUE PATTERNS (run last) =====
    # These catch remaining secrets not matched by specific patterns above.
    # The negative lookahead (?!\S*\*\*\*) prevents re-redacting values
    # that were already replaced by a specific pattern above (e.g. sk-ant-***).
    (re.compile(r"(api[_-]?key\s*[=:]\s*)(?!\S*\*\*\*)([^\s\"']+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(token\s*[=:]\s*)(?!\S*\*\*\*)([^\s\"']+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(password\s*[=:]\s*)(?!\S*\*\*\*)([^\s\"']+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(secret\s*[=:]\s*)(?!\S*\*\*\*)([^\s\"']+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(--api-key\s+)(?!\S*\*\*\*)([^\s]+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(--token\s+)(?!\S*\*\*\*)([^\s]+)", re.IGNORECASE), r"\1***"),
    # --- Authorization headers ---
    (re.compile(r"(Authorization\s*:\s*Bearer\s+)(?!\S*\*\*\*)([^\s\"']+)", re.IGNORECASE), r"\1***"),
]


def sanitize_text(text: str) -> str:
    """Redact known secret patterns from *text* and return the result.

    Each entry in :data:`_SECRET_REPLACEMENTS` is applied in order.
    The function is deliberately free of side-effects and does not
    truncate the output — length limiting is the caller's responsibility.

    Parameters
    ----------
    text:
        Raw string that may contain secrets.

    Returns
    -------
    str
        A copy of *text* with all recognised secret patterns replaced
        by a type-specific placeholder (e.g. ``sk-***``, ``ghp_***``).
    """
    out = text
    for pattern, repl in _SECRET_REPLACEMENTS:
        out = pattern.sub(repl, out)
    return out
