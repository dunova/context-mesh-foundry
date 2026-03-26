# CLAUDE.md — Project Instructions for Claude Code

## Project

ContextGO — local-first context and memory runtime for AI coding teams.
Entry point: `python3 scripts/context_cli.py` (or `contextgo` if pip-installed)

## Architecture

- `scripts/` — Python core: CLI, daemon, indexer, viewer, search, smoke, maintenance
  - `context_cli.py` — single operator entry point for all commands
  - `context_config.py` — env var resolution and storage root
  - `session_index.py` — SQLite FTS5-backed session index and retrieval
  - `memory_index.py` — memory and observation index, export/import
  - `context_daemon.py` — session capture and sanitization
  - `context_server.py` — local viewer API server
  - `context_core.py` — shared helpers: file scan, memory write, safe_mtime
  - `context_native.py` — Rust/Go backend orchestration
  - `context_smoke.py` — smoke test suite
  - `context_maintenance.py` — index cleanup and repair
- `native/session_scan/` — Rust hot-path binary for file scanning
- `native/session_scan_go/` — Go hot-path binary for parallel scanning
- `docs/` — full documentation suite (ARCHITECTURE, CONFIGURATION, TROUBLESHOOTING, API, CONTRIBUTING)
- `benchmarks/` — Python vs. native-wrapper performance harness
- `templates/` — systemd/launchd service templates
- `artifacts/` — autoresearch outputs (do not edit)
- `patches/` — compatibility notes (do not edit)

## Test Commands

```bash
# Syntax checks
bash -n scripts/*.sh
python3 -m py_compile scripts/*.py

# Unit and integration tests
python3 -m pytest scripts/test_context_cli.py scripts/test_context_core.py scripts/test_session_index.py scripts/test_context_native.py scripts/test_context_smoke.py scripts/test_autoresearch_contextgo.py

# End-to-end quality gate
python3 scripts/e2e_quality_gate.py

# Smoke tests (sandboxed — does not write to ~/.contextgo)
python3 scripts/context_cli.py smoke --sandbox
python3 scripts/smoke_installed_runtime.py

# Health check
bash scripts/context_healthcheck.sh
```

## Style Rules

- **Python:** ruff-compatible, type hints required on all new functions and public interfaces, English docstrings, target Python 3.10+
- **Rust:** `cargo clippy` clean before commit
- **Go:** `go vet` clean before commit
- **Shell:** `shellcheck` clean, always start with `#!/usr/bin/env bash` and `set -euo pipefail`

## Important

- All user-facing text: bilingual (English primary, Chinese secondary)
- Never commit to `artifacts/` or `patches/` without an explicit request
- Run the full test suite before any commit
- No hardcoded absolute paths in committed code — use `~` or environment variables
- No secrets, tokens, or API keys in any committed file
- Default storage root is `~/.contextgo`; override with `CONTEXTGO_STORAGE_ROOT`
- Remote sync is disabled by default; enabled only via `CONTEXTGO_ENABLE_REMOTE_MEMORY_HTTP=true`
