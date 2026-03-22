# Context Mesh Foundry

[English](#the-problem) | [中文](#问题是什么)

---

## The Problem

Modern AI-assisted development spawns many parallel sessions — Claude Code, Codex CLI, OpenCode, terminal shells… Each starts with zero context. Decisions, debugging history, and architectural constraints from one session are invisible to the next.

## What This Does

Context Mesh Foundry (CMF) is a **local-first, MCP-free** context persistence layer. It weaves together three subsystems into a unified memory mesh:

1. **recall.py** — Hybrid search across all AI session histories (SQLite index + regex)
2. **context_cli.py** — Lightweight CLI for search, semantic query, save, and health check — the **default entry point**
3. **viking_daemon.py** — Background daemon that watches terminal/AI histories and exports sanitized markdown to OpenViking for vectorization

### Hit-First Retrieval Protocol

Every AI session must follow this order before doing any work:

```
1. Recall exact search   (mandatory)
2. Local semantic search  (only if recall misses)
3. Codebase scan          (only as last resort)
```

Blind whole-disk scans (`~/`, `/Volumes/*`) without prior recall are **forbidden**.

## Architecture

```
┌─────────────────────────────────────────────┐
│              AI Terminal / Agent             │
│     (Claude Code, Codex CLI, OpenCode…)     │
└──────────────┬──────────────────────────────┘
               │  python3 context_cli.py search/semantic/save/health
               ▼
┌─────────────────────────────────────────────┐
│           context_cli.py (CLI Layer)        │
│   • search: recall.py → local file scan    │
│   • semantic: embeddings via OpenViking     │
│   • save: persist decisions & constraints   │
│   • health: stack-wide diagnostics          │
└──────────────┬──────────────────────────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
┌────────────┐  ┌─────────────────┐
│  recall.py │  │  OpenViking API │
│  (SQLite   │  │  (vectorized    │
│   hybrid)  │  │   search)       │
└────────────┘  └─────────────────┘
       ▲
       │  auto-export on idle
┌──────┴──────────────────────────────────────┐
│           viking_daemon.py (Daemon)         │
│   • Watches: Claude, Codex, OpenCode,       │
│     Kilo, zsh/bash, Gemini walkthroughs     │
│   • Sanitizes: 15+ redaction patterns       │
│   • Exports: markdown → OpenViking POST     │
│   • Queues failures to .pending/            │
└─────────────────────────────────────────────┘
```

### GSD Integration

When used with the [GSD workflow](https://github.com/dunova/get-shit-done) (`discuss → plan → execute → verify`), each phase auto-preheats context via `context_cli.py`:

- **discuss-phase**: mandatory recall search
- **plan-phase**: recall + optional semantic backfill
- **health**: stack-wide diagnostics via `context_healthcheck.sh`

## Module Map

### Core Runtime

| Script | Purpose |
|--------|---------|
| `context_cli.py` | **Default CLI entry point** — search, semantic, save, health |
| `viking_daemon.py` | Background daemon: watch → sanitize → export |
| `openviking_mcp.py` | Legacy MCP bridge (kept for reference, not the default path) |
| `context_healthcheck.sh` | Comprehensive health checks for the whole stack |
| `start_openviking.sh` | Start OpenViking safely (ports, config, retries) |
| `unified_context_deploy.sh` | Deploy: sync scripts/skills, patch launchd, reload |
| `scf_context_prewarm.sh` | Shell helper for context warmup before GSD actions |

### Memory Tools

| Script | Purpose |
|--------|---------|
| `memory_index.py` | Local memory indexing and deduplication |
| `memory_viewer.py` | Browse and inspect stored memories |
| `memory_hit_first_regression.py` | Regression suite for retrieval quality |
| `export_memories.py` | Export memories to portable format |
| `import_memories.py` | Import memories from backup |
| `start_memory_viewer.sh` | Launch memory viewer |

### Context-First Policy

| Script | Purpose |
|--------|---------|
| `apply_context_first_policy.sh` | Apply Context-First protocol to AI tool configs |
| `verify_context_first_policy.sh` | Verify all terminals follow the protocol |
| `e2e_quality_gate.py` | End-to-end quality gate for context pipeline |
| `test_context_cli.py` | Unit tests for context_cli.py |

### Utilities

| Script | Purpose |
|--------|---------|
| `onecontext_maintenance.py` | OneContext data maintenance |
| `run_onecontext_maintenance.sh` | Wrapper for above |
| `patch_openviking_semantic_processor.py` | Optional VLM quiet patch |

## Requirements

- Python 3.10+
- [OpenViking](https://github.com/Open-Wise/OpenViking) running locally (default: `http://127.0.0.1:8090`)
- macOS (launchd) or Linux (systemd) for daemon management
- [recall.py](https://github.com/dunova/get-shit-done) (from the GSD skill ecosystem)

## Quick Start

### 1. Clone

```bash
git clone https://github.com/dunova/context-mesh-foundry.git
cd context-mesh-foundry
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — set OPENVIKING_URL, OPENVIKING_API_KEY, storage paths
```

### 3a. Deploy (macOS)

```bash
bash scripts/unified_context_deploy.sh
```

This will:
- Copy scripts to `~/.codex/skills/openviking-memory-sync/scripts/`
- Install LaunchAgent plists for daemon, server, and health check
- Reload services

### 3b. Deploy (Linux systemd)

```bash
cp templates/systemd-user/*.service ~/.config/systemd/user/
cp templates/systemd-user/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now viking-daemon.service openviking-server.service context-healthcheck.timer
```

### 4. Verify

```bash
python3 scripts/context_cli.py health
```

### 5. Use

```bash
# Search across all AI session histories
python3 scripts/context_cli.py search "authentication bug" --type all --limit 20 --literal

# Semantic search (via OpenViking embeddings)
python3 scripts/context_cli.py semantic "database migration decisions" --limit 5

# Save a key decision
python3 scripts/context_cli.py save --title "DB choice" --content "Chose SQLite for local store" --tags "architecture,db"

# Health check
python3 scripts/context_cli.py health
```

## How the Daemon Works

The daemon (`viking_daemon.py`) runs in the background and:

1. **Discovers sources** — scans for history files from Claude Code, Codex, OpenCode, Kilo, and shell histories (zsh/bash). Also watches Codex session directories and Gemini Antigravity brain walkthroughs.

2. **Tails new content** — uses inode-aware file cursors to detect new lines, file rotation, and truncation without re-reading entire files.

3. **Sanitizes** — applies 15+ regex patterns to strip API keys (`sk-*`, `ghp_*`, `AIza*`), tokens, passwords, AWS keys, Slack tokens, and PEM blocks.

4. **Exports on idle** — when a session has been idle for 5 minutes (configurable) and has enough messages, it writes a Markdown summary to local storage and POSTs it to OpenViking for vectorization.

5. **Queues failures** — if OpenViking is offline, files go to a `.pending/` directory and are retried automatically on the next successful export.

6. **Adaptive polling** — poll interval speeds up near idle-export boundaries and slows down during quiet periods, saving CPU.

## Repository Layout

```
context-mesh-foundry/
├── scripts/
│   ├── context_cli.py                # Default CLI entry point
│   ├── viking_daemon.py              # Background daemon
│   ├── openviking_mcp.py             # Legacy MCP bridge (reference only)
│   ├── context_healthcheck.sh        # Health checks
│   ├── start_openviking.sh           # OpenViking launcher
│   ├── unified_context_deploy.sh     # Deploy & sync
│   ├── scf_context_prewarm.sh        # Context prewarm helper
│   ├── memory_index.py               # Memory indexing
│   ├── memory_viewer.py              # Memory browser
│   ├── memory_hit_first_regression.py # Regression suite
│   ├── export_memories.py            # Memory export
│   ├── import_memories.py            # Memory import
│   ├── start_memory_viewer.sh        # Memory viewer launcher
│   ├── apply_context_first_policy.sh # Apply Context-First policy
│   ├── verify_context_first_policy.sh # Verify policy compliance
│   ├── e2e_quality_gate.py           # E2E quality gate
│   ├── test_context_cli.py           # Unit tests
│   ├── onecontext_maintenance.py     # OneContext maintenance
│   ├── run_onecontext_maintenance.sh # Maintenance wrapper
│   └── patch_openviking_semantic_processor.py # VLM patch
├── templates/
│   ├── launchd/                      # macOS LaunchAgent plists
│   └── systemd-user/                 # Linux systemd user services
├── integrations/
│   └── gsd/workflows/                # GSD health workflow
├── examples/
│   └── ov.conf.template.json         # OpenViking config template
├── docs/
│   ├── ARCHITECTURE.md
│   ├── TROUBLESHOOTING.md
│   └── ...
├── .env.example
├── SECURITY.md
└── CONTRIBUTING.md
```

## Security

- **No secrets in this repo.** CI scans for common key patterns on every push.
- **Secret scrubbing:** The daemon redacts API keys, tokens, passwords, PEM private keys, AWS access keys, and Slack tokens before exporting any content.
- **Safe secrets parsing:** `start_openviking.sh` parses `KEY=VALUE` files without `source`, preventing shell injection.
- **File permissions:** Data directories are chmod 700, exported files are chmod 600.
- **TLS enforcement:** Remote OpenViking URLs must use HTTPS (localhost is exempt).

See [SECURITY.md](SECURITY.md) for the full threat model.

## Environment Variables

See [`.env.example`](.env.example) for all configurable environment variables.

## Troubleshooting

See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) for known failures and fixes.

## License

[GPL-3.0](LICENSE)

---

## 问题是什么

现代 AI 辅助开发会产生很多并行会话 — Claude Code、Codex CLI、OpenCode、终端 Shell… 每个都从零上下文开始，一个会话中的决策、调试历史和架构约束对下一个会话不可见。

## 这个项目做了什么

Context Mesh Foundry (CMF) 是一个**本地优先、无 MCP 依赖**的上下文持久层。它将三个子系统缝合成统一的记忆网格：

1. **recall.py** — 跨所有 AI 会话历史的混合搜索（SQLite 索引 + 正则）
2. **context_cli.py** — 轻量 CLI，支持 search / semantic / save / health — **默认入口**
3. **viking_daemon.py** — 后台守护进程，监控终端/AI 历史并导出清洗后的 markdown 到 OpenViking 做向量化

### 三段式预热协议（强制执行）

每个 AI 会话在执行任何工作前，必须按以下顺序：

```
1. Recall 精确检索       （必做）
2. 本地语义检索          （仅 recall 未命中时）
3. 代码库扫描            （最后手段）
```

未经 recall 预热就做全盘穷举扫描（`~/`、`/Volumes/*`）是**被禁止的**。

### GSD 集成

与 [GSD 工作流](https://github.com/dunova/get-shit-done)（`discuss → plan → execute → verify`）配合使用时，每个阶段自动通过 `context_cli.py` 预热上下文。

## 快速开始

```bash
# 克隆
git clone https://github.com/dunova/context-mesh-foundry.git
cd context-mesh-foundry

# 配置
cp .env.example .env
# 编辑 .env — 设置 OPENVIKING_URL、OPENVIKING_API_KEY、存储路径

# 部署（macOS）
bash scripts/unified_context_deploy.sh

# 验证
python3 scripts/context_cli.py health
```

## 使用

```bash
# 跨 AI 会话搜索
python3 scripts/context_cli.py search "认证 bug" --type all --limit 20 --literal

# 语义搜索
python3 scripts/context_cli.py semantic "数据库迁移决策" --limit 5

# 保存关键决策
python3 scripts/context_cli.py save --title "DB" --content "选了 SQLite" --tags "架构,数据库"

# 健康检查
python3 scripts/context_cli.py health
```

## 许可证

[GPL-3.0](LICENSE)
