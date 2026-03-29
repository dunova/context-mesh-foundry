# ContextGO 0.10.0

## Summary

ContextGO 0.10.0 turns the project from a fixed-source local memory CLI into a
multi-platform, auto-discovering memory runtime with release-grade onboarding.

This release is focused on one product promise:

> install in under a minute, see real value immediately, and keep working even
> after new terminal AI tools are installed later.

## Highlights

- Automatic platform discovery with `contextgo sources`
- Normalized adapter ingestion for OpenCode, Kilo, and OpenClaw
- Immediate indexing of newly installed tools without waiting for sync TTL
- Upgrade-safe adapter schema versioning
- One-command upgrade and uninstall scripts
- README rewritten for investor/demo-grade onboarding

## User-visible improvements

### 1. Install and prove value immediately

The recommended flow is now:

```bash
pipx install "contextgo[vector]"
eval "$(contextgo shell-init)"
contextgo health
contextgo sources
contextgo search "authentication" --limit 5
```

Users and AI agents can now confirm, within the first minute, which platforms
ContextGO has already detected locally.

### 2. Multi-platform local source absorption

ContextGO now auto-discovers and normalizes local histories from:

- Codex
- Claude Code
- OpenCode
- Kilo
- OpenClaw
- zsh / bash shell history

OpenCode and Kilo are no longer treated as prompt-history-only sources when
richer local session storage is available.

### 3. Incremental adoption solved

If a user installs OpenCode, Kilo, or OpenClaw after ContextGO is already
installed, they do not need to reconfigure anything.

The next run of:

- `contextgo health`
- `contextgo sources`
- `contextgo search ...`

will refresh adapters and absorb the newly available local sessions.

### 4. Upgrade and uninstall flows

ContextGO now ships:

- `scripts/upgrade_contextgo.sh`
- `scripts/uninstall_contextgo.sh`

This makes lifecycle management explicit and reduces trust-damaging ambiguity
for evaluators and first-time users.

## Engineering notes

- Added `scripts/source_adapters.py`
- Added adapter cache schema versioning
- Added home-scoped adapter roots to avoid cross-environment collisions
- Refreshed session index behavior so adapter changes bypass the normal sync TTL
- Added new regression coverage for source adapters and incremental ingestion

## Validation

- Full pytest suite passed
- Coverage remains above the repository gate
- Installed CLI smoke passed
- Installed runtime smoke passed
- Fresh install plus incremental OpenCode / Kilo / OpenClaw adoption scenarios verified
