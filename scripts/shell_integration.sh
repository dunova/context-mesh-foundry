#!/usr/bin/env bash
# ContextGO shell integration
# ──────────────────────────────────────────────────────────────────────────────
# INSTALLATION
#   Option 1 (recommended) — add to your shell rc file:
#     echo 'eval "$(contextgo shell-init)"' >> ~/.bashrc   # or ~/.zshrc
#
#   Option 2 — source this file directly:
#     source /path/to/shell_integration.sh
#
#   Option 3 — one-time activation in current session:
#     eval "$(contextgo shell-init)"
#
# ALIASES ADDED
#   cg   — contextgo q        (quick recall: hybrid search or session ID lookup)
#   cgs  — contextgo search   (full-text search)
#   cgse — contextgo semantic  (semantic search with memory fallback)
#   cgvs — contextgo vector-sync  (embed new sessions into vector index)
# ──────────────────────────────────────────────────────────────────────────────

# Quick recall — search or session ID lookup
# Usage: cg 'how did we fix the auth bug?'
#        cg 3f2a1b8c
cg() { contextgo q "$@"; }

# Shorthand aliases
alias cgs='contextgo search'
alias cgse='contextgo semantic'
alias cgvs='contextgo vector-sync'
