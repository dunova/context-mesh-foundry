# Contributing to ContextGO
# 参与贡献 ContextGO

Thank you for considering a contribution to ContextGO. This guide covers the development setup, code style, testing requirements, and pull request process.

感谢你考虑为 ContextGO 做贡献。本指南涵盖开发环境搭建、代码风格、测试要求和 PR 流程。

## Contents / 目录

- [Development setup / 开发环境](#development-setup)
- [Project principles / 项目原则](#project-principles)
- [Code style / 代码风格](#code-style)
- [Testing requirements / 测试要求](#testing-requirements)
- [Pull request process / PR 流程](#pull-request-process)
- [Pre-submission checklist / 提交前检查清单](#pre-submission-checklist)

---

## Development setup

**Prerequisites:** Python 3.10+, Bash, Git. Rust and Go are optional and only required if working on native hot paths.

**前置条件：** Python 3.10+、Bash、Git。Rust 和 Go 为可选项，仅在修改 native 热路径时需要。

```bash
git clone https://github.com/dunova/ContextGO.git
cd ContextGO

# Install Python dependencies (including dev tools: pytest, ruff)
pip install -e ".[dev]"

# Run the deploy script to initialize the local environment
bash scripts/unified_context_deploy.sh

# Verify the setup
python3 scripts/context_cli.py health
python3 scripts/context_cli.py smoke --sandbox
```

A `Makefile` is provided for common operations:

```bash
make install   # pip install -e ".[dev]"
make test      # run the full pytest suite
make lint      # ruff check + format check
make format    # ruff format + auto-fix
make smoke     # contextgo smoke --sandbox via context_cli.py
make health    # contextgo health via context_cli.py
make bench     # run the benchmark harness
make clean     # remove __pycache__ and .pyc files
```

The storage root defaults to `~/.contextgo`. This can be overridden with the `CONTEXTGO_STORAGE_ROOT` environment variable. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for all configuration options.

### Optional: native hot paths

```bash
# Rust
cd native/session_scan
CARGO_TARGET_DIR=/tmp/contextgo_target cargo build --release

# Go
cd native/session_scan_go
go build .
```

---

## Project principles / 项目原则

- **All changes serve the core entry points.** Contributions must have a clear relationship to `context_cli.py`, `context_daemon.py`, `session_index.py`, `memory_index.py`, or the validation chain. Changes to legacy or bridge paths require justification.

- **Local-first by default.** The default code path must not introduce external service calls, network connections, or cloud dependencies. Remote paths belong behind explicit feature flags with documentation.

- **Smoke and benchmark are health thresholds.** If a change causes `context_smoke.py` or the benchmark harness to degrade, it must be addressed before merging.

- **No secrets or machine-specific paths in commits.** Replace any local paths with `~` or environment variable references. See [Pre-submission checklist](#pre-submission-checklist).

- **Prefer fast recovery over clever optimizations.** Silent failures, extra dependencies, and multi-host synchronization logic in the default path are not acceptable.

---

## Code style / 代码风格

### Shell scripts

- Begin with `#!/usr/bin/env bash` and `set -euo pipefail`.
- Use lightweight, portable POSIX-compatible commands.
- Avoid non-standard tools unless they are declared as dependencies.

### Python

- Target Python 3.10+.
- Prefer the standard library. When a third-party package is required, add it to `requirements.txt` and ensure it passes CI.
- Use type hints for new functions and public interfaces.
- Write comments only to explain non-obvious runtime or operational logic. Let the code be self-explanatory where possible.
- **ruff** is the enforced formatter and linter. Run `make format` before committing and `make lint` to check. CI will fail on ruff violations.

### Rust and Go

- Keep native modules focused on a single hot-path function.
- Validate with small prototypes before expanding scope.
- Every native change must maintain passing benchmarks and tests.

### Benchmarks

- Place new benchmarks in `benchmarks/`.
- For any hot-path or performance-sensitive change, include a before/after baseline comparison in the same directory.

---

## Testing requirements / 测试要求

Run the following before submitting a pull request. All steps must pass.

The Makefile shortcuts cover the most common checks:

```bash
make test    # full pytest suite
make lint    # ruff check + format check
make smoke   # contextgo smoke --sandbox
make health  # contextgo health
make bench   # benchmark baseline
```

Full manual sequence for pre-PR validation:

```bash
# Syntax checks
bash -n scripts/*.sh
python3 -m py_compile scripts/*.py

# Unit and integration tests
python3 -m pytest scripts/test_context_cli.py scripts/test_context_core.py \
    scripts/test_session_index.py scripts/test_context_native.py \
    scripts/test_context_smoke.py scripts/test_autoresearch_contextgo.py -v

# End-to-end quality gate
python3 scripts/e2e_quality_gate.py

# Performance baseline
python3 -m benchmarks --mode both --iterations 1 --warmup 0 --query benchmark --format text

# Smoke and health via the contextgo CLI
python3 scripts/context_cli.py smoke --sandbox
python3 scripts/context_cli.py health
python3 scripts/smoke_installed_runtime.py

# Shell health check
bash scripts/context_healthcheck.sh
```

For changes that only affect documentation or configuration, the smoke suite may be skipped. State the reason explicitly in the pull request description.

The smoke tests depend on `CONTEXTGO_STORAGE_ROOT` defaulting to `~/.contextgo`. Confirm `scripts/context_config.py` resolves to the correct path for your user before running.

The installed runtime smoke (`smoke_installed_runtime.py`) expects `context_cli.py` and `e2e_quality_gate.py` at `~/.local/share/contextgo/scripts` (or the path in `CONTEXTGO_INSTALL_ROOT`).

---

## Pull request process / PR 流程

1. **Target `main`.** All work goes to the `main` branch. Do not target `scripts/legacy` or historical bridge paths.

2. **Keep your branch up to date.** Run `git pull` before opening a PR and resolve any conflicts before requesting review.

3. **Small, focused changes merge faster.** Single-module fixes can be submitted as a PR immediately. Cross-module changes should start as a discussion issue to align on impact.

4. **Include verification output.** For any change affecting core entry points (`context_cli`, `context_daemon`, `session_index`, `memory_index`), include the output of the relevant test and smoke commands in the PR description.

5. **Update documentation.** If you add an environment variable, change a default behavior, or modify the storage layout, update the relevant docs (`docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, `docs/TROUBLESHOOTING.md`) in the same PR.

6. **Notify the release owner.** If your change affects the release pipeline or deployment steps, mention `@release-owner` in the PR.

---

## Pre-submission checklist / 提交前检查清单

- [ ] No secrets, API keys, tokens, or passwords are present in any committed file.
- [ ] No hardcoded absolute paths (e.g., `/Users/name/`, `/home/name/`). Use `~` or environment variables.
- [ ] `rg` scan for common secret patterns passes: `rg -i 'AKIA|password|secret|token' scripts/ docs/`
- [ ] All test and smoke commands pass locally.
- [ ] Documentation is updated for any new environment variables or behavior changes.
- [ ] PR description includes the test commands run and their output (or explains why they were skipped).
