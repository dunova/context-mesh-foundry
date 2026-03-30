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
# ---------------------------------------------------------------------------
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
    # Anthropic API keys (sk-ant-api03-…)
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b"), "sk-ant-***"),
    # GitHub tokens: Personal (ghp_), OAuth (gho_), Server (ghs_), Actions refresh (ghr_)
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "ghp_***"),
    (re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"), "gho_***"),
    (re.compile(r"\bghs_[A-Za-z0-9]{20,}\b"), "ghs_***"),
    (re.compile(r"\bghr_[A-Za-z0-9]{20,}\b"), "ghr_***"),
    # GitLab personal/project/group access tokens
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}\b"), "glpat-***"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"), "AIza***"),
    # npm automation / publish tokens
    (re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b"), "npm_***"),
    # Slack tokens: bot (xoxb-), user (xoxp-), workspace (xoxs-), app-level (xoxa-), refresh (xoxr-)
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"), "xox?-***"),
    # AWS access key IDs (real keys are prefix + 16 uppercase alphanums, min 12 to catch test fixtures)
    (re.compile(r"\b(?:AKIA|ASIA|AROA|AIPA|ANPA|ANVA|APKA)[A-Z0-9]{12,}\b"), "AKIA***"),
    # Stripe secret/restricted keys
    (re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{24,}\b"), "sk_stripe_***"),
    (re.compile(r"\brk_(?:live|test)_[A-Za-z0-9]{24,}\b"), "rk_stripe_***"),
    # HuggingFace API tokens
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"), "hf_***"),
    # SendGrid API keys
    (re.compile(r"\bSG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"), "SG.***"),
    # Twilio Account SID / Auth Token patterns
    (re.compile(r"\bAC[a-f0-9]{32}\b"), "AC_twilio_***"),
    (re.compile(r"\bSK[a-f0-9]{32}\b"), "SK_twilio_***"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "***PEM_KEY_REDACTED***",
    ),
    # JWT tokens (three base64url segments separated by dots)
    (re.compile(r"\beyJhb[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"), "***JWT_REDACTED***"),
    # GitHub fine-grained PAT (github_pat_)
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "github_pat_***"),
    # Azure SAS tokens
    (re.compile(r"([\?&]sig=)[A-Za-z0-9%+/=]{20,}", re.IGNORECASE), r"\1***"),
    # HashiCorp Vault tokens
    (re.compile(r"\bhvs\.[A-Za-z0-9_-]{20,}\b"), "hvs.***"),
    # Docker PAT (dckr_pat_)
    (re.compile(r"\bdckr_pat_[A-Za-z0-9_-]{20,}\b"), "dckr_pat_***"),
    # Database connection strings (postgres://, mysql://, mongodb:// with embedded credentials)
    (re.compile(r"((?:postgres|mysql|mongodb|redis)(?:ql)?://[^:]+:)[^\s@]+(@)", re.IGNORECASE), r"\1***\2"),
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
        A copy of *text* with all recognised secret patterns replaced by
        their corresponding redaction markers (e.g. ``***``, ``sk-***``).
    """
    out = text
    for pattern, repl in _SECRET_REPLACEMENTS:
        out = pattern.sub(repl, out)
    return out
