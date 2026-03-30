# Contributing to ContextGO
# 参与贡献 ContextGO

Thank you for contributing. ContextGO is a local-first runtime — every change
must preserve that guarantee. This guide covers everything you need from first
clone to merged PR.

感谢贡献。ContextGO 是本地优先运行时——每次变更必须保持这一核心保证。本指南覆盖从首次克隆到 PR 合并的完整流程。

---

## Contents / 目录

- [Development setup](#development-setup)
- [Project principles](#project-principles)
- [Code style](#code-style)
- [Testing](#testing)
- [Pull request process](#pull-request-process)
- [Pre-submission checklist](#pre-submission-checklist)

---

## Development setup

**Prerequisites:** Python 3.10+, Bash, Git.
Rust and Go are optional — required only when touching native hot paths.

**前置条件：** Python 3.10+、Bash、Git。
Rust 与 Go 为可选项，仅修改 native 热路径时需要。

```bash
git clone https://github.com/dunova/ContextGO.git
cd ContextGO

# Install package + dev tools (pytest, ruff, pytest-cov, mypy)
make install

# Initialize local environment
bash scripts/unified_context_deploy.sh

# Verify the setup
make health
make smoke
```

### Makefile reference

| Target | Description |
|---|---|
| `make install` | `pip install -e ".[dev]"` |
| `make lint` | ruff check + format-check |
| `make format` | ruff format + auto-fix |
| `make dev-check` | lint + syntax checks |
| `make test` | full pytest suite with coverage |
| `make test-fast` | tests without coverage (faster) |
| `make smoke` | smoke suite, sandboxed |
| `make health` | contextgo health probe |
| `make e2e` | end-to-end quality gate |
| `make bench` | Python vs. native-wrapper benchmark |
| `make build` | build sdist + wheel |
| `make clean` | remove bytecode and caches |

### Configuration

The storage root defaults to `~/.contextgo`. Override with `CONTEXTGO_STORAGE_ROOT`.
See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for all options.

存储根目录默认为 `~/.contextgo`，可通过 `CONTEXTGO_STORAGE_ROOT` 覆盖。
完整配置项见 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)。

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

## Project principles

1. **All changes serve the core entry points.**
   Contributions must relate clearly to `context_cli.py`, `context_daemon.py`,
   `session_index.py`, `memory_index.py`, or the validation chain.
   Changes to legacy or bridge paths require explicit justification.

2. **Local-first by default.**
   The default code path must not introduce external service calls, network
   connections, or cloud dependencies. Remote features belong behind explicit
   feature flags with documentation.

3. **Smoke and benchmark are health thresholds.**
   If a change degrades `context_smoke.py` or the benchmark harness, it must
   be addressed before merging.

4. **No secrets or machine-specific paths in commits.**
   Replace local paths with `~` or environment variable references.

5. **Prefer fast recovery over clever optimizations.**
   Silent failures, extra dependencies, and multi-host synchronization logic
   in the default code path are not acceptable.

---

## 项目原则

1. **所有变更服务于核心入口点。** 贡献必须与 `context_cli.py`、`context_daemon.py`、`session_index.py`、`memory_index.py` 或验证链有明确关联。修改旧桥接路径须附理由。

2. **默认本地优先。** 默认代码路径不得引入外部服务调用、网络连接或云依赖。远程功能必须放在显式特性标志后面并配套文档。

3. **Smoke 和 benchmark 是健康门槛。** 变更导致 `context_smoke.py` 或 benchmark 退化，必须在合并前修复。

4. **禁止提交密钥或机器绝对路径。** 用 `~` 或环境变量替代本地路径。

5. **优先快速恢复，而非聪明优化。** 默认路径中的静默失败、额外依赖和多主机同步逻辑不可接受。

---

## Code style

### Python

- Target Python 3.10+.
- Prefer the standard library. Third-party additions must go in `pyproject.toml`
  and pass CI.
- Type hints required on all new functions and public interfaces.
- Comments explain *why*, not *what*. Let the code speak.
- `ruff` is the enforced formatter and linter. Run `make format` before
  committing; `make lint` to verify. CI blocks on ruff violations.

### Shell scripts

- Begin with `#!/usr/bin/env bash` and `set -euo pipefail`.
- Use lightweight, portable POSIX-compatible commands.
- Must pass `shellcheck` at error level.

### Rust

- Keep native modules focused on a single hot-path function.
- `cargo clippy` must be clean before commit.
- Every change must maintain passing benchmarks and tests.

### Go

- `go vet` must be clean before commit.
- String operations on session content must use `[]rune` slices (not byte
  offsets) to handle CJK codepoints safely.

---

## Testing

Run the following before every PR. All steps must pass.

在每次 PR 前执行以下所有步骤，全部必须通过。

```bash
# Lint and syntax
make dev-check

# Unit and integration tests
make test

# End-to-end quality gate
make e2e

# Smoke and health
make smoke
make health

# Benchmark baseline
make bench
```

For documentation-only or configuration-only changes, `make e2e` and
`make bench` may be skipped — state the reason explicitly in the PR
description.

Test dependencies:

- `CONTEXTGO_STORAGE_ROOT` defaults to `~/.contextgo`; confirm
  `scripts/context_config.py` resolves correctly before running.
- The installed-runtime smoke (`smoke_installed_runtime.py`) expects
  `context_cli.py` at `~/.local/share/contextgo/scripts` (or
  `CONTEXTGO_INSTALL_ROOT`).

---

## Pull request process

1. **Target `main`.** All work goes to `main`. Do not target legacy or bridge
   branches.

2. **Keep your branch current.** Run `git pull --rebase` before opening a PR
   and resolve conflicts before requesting review.

3. **Small changes merge faster.** Single-module fixes can go to PR immediately.
   Cross-module changes should start as a discussion issue.

4. **Include verification output.** For changes touching core entry points
   (`context_cli`, `context_daemon`, `session_index`, `memory_index`), paste
   the relevant test and smoke output into the PR description.

5. **Update documentation.** New environment variables, changed defaults, or
   storage layout changes must update
   `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`,
   and `docs/TROUBLESHOOTING.md` in the same PR.

6. **Notify on release-pipeline changes.** If your change affects the release
   workflow or deployment steps, mention `@release-owner` in the PR.

---

## PR 流程

1. **目标分支 `main`。** 所有工作合并到 `main`，不要指向旧桥接分支。
2. **保持分支最新。** 开 PR 前执行 `git pull --rebase`，提前解决冲突。
3. **小改动更快合并。** 单模块修复可直接提 PR；跨模块变更先开 issue 对齐影响。
4. **附上验证输出。** 触及核心入口点的变更必须在 PR 描述中粘贴相关测试和 smoke 输出。
5. **同步更新文档。** 新增环境变量、修改默认行为或存储布局必须在同一 PR 中更新相关 docs。
6. **通知发布流程负责人。** 影响发布流程或部署步骤时，在 PR 中 `@release-owner`。

---

## Pre-submission checklist

- [ ] No secrets, API keys, tokens, or passwords in any committed file.
- [ ] No hardcoded absolute paths (e.g. `/Users/name/`, `/home/name/`). Use `~`
      or environment variables.
- [ ] Secret scan passes:
      `rg -i 'AKIA|password|secret|token' scripts/ docs/`
- [ ] `make lint` passes with zero errors.
- [ ] `make test` passes (all tests green, coverage ≥ 97%).
- [ ] `make smoke` passes.
- [ ] PR description includes test output or justification for skipping.
- [ ] Documentation updated for any new environment variables or behavior
      changes.
