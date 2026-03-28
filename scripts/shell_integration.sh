#!/usr/bin/env bash
# ContextGO shell integration
# Add to ~/.bashrc or ~/.zshrc, or run: eval "$(contextgo shell-init)"

# Quick recall — search or session ID lookup
cg() { contextgo q "$@"; }

# Shorthand aliases
alias cgs='contextgo search'
alias cgse='contextgo semantic'
alias cgvs='contextgo vector-sync'
