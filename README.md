# Silent Context Foundry

[English](#the-problem) | [中文](#问题是什么)

---

## The Problem

Modern developers use multiple AI coding assistants -- Claude Code, Codex, OpenCode, Kilo, Gemini Antigravity -- sometimes switching between them within the same project. **Each tool maintains its own isolated memory.** Context built in one session is invisible to the others. You explain the same architecture, the same debugging history, the same project conventions over and over again.

Shell history is also a goldmine of context (commands tried, paths explored, debugging sequences), but it sits in flat files that no AI tool reads.

**The result:** your AI tools have amnesia. Every new session starts from zero, even though the answers already exist somewhere on your machine.

## What This Does

Silent Context Foundry gives all your AI terminals a **shared, persistent, searchable memory**.

As of 2026-03, the default runtime is **recall-lite** (low-power, low-blocking):

1. `recall` indexes local histories incrementally (Codex/Claude/OpenCode/Kilo/Shell/Antigravity).
2. `onecontext` command is kept as a compatibility shim (backed by `recall`, no upstream dependency).
3. `openviking_mcp.py` is **recall-first**. OpenViking semantic search is optional and non-blocking.
4. `context_healthcheck.sh` defaults to non-intrusive checks; deep legacy checks are opt-in.

Legacy heavy stack (OneContext/Aline daemon chain) is no longer required for normal operation.

**Net effect:** when you ask "what did I try last week to fix the auth bug?", MCP can still retrieve cross-terminal context, but with much lower background overhead and less risk of multi-agent stalls.

## Optional 4th Layer: Agent Orchestrator (AO)

SCF gives your AI tools shared memory and process discipline (with GSD), but it does not manage multiple coding-agent sessions for you. If your bottleneck is now "babysitting agents" (tabs, branches, CI failures, review comments), add **Agent Orchestrator (AO)** as a manager layer:

- **SCF (Recall + OpenViking(optional) + GSD)** = memory + context retrieval + execution discipline
- **AO** = parallel session orchestration + PR/CI/review plumbing automation

This repo includes an AO integration pack under `integrations/agent-orchestrator/` (templates, bridge scripts, and progressive-disclosure skills).

## 4-Layer Architecture (Recommended)

Use SCF + AO as a layered system instead of a single monolith:

1. **Memory Layer (Recall + OpenViking optional)**
- Recall = exact/local history lookup (session/turn/content)
- OpenViking = optional semantic recall across terminals and shell history

2. **Discipline Layer (GSD)**
- Forces discuss -> plan -> execute -> verify
- Requires context warmup before execution
- Requires evidence before completion

3. **Execution Layer (Coding Agents)**
- Codex / Claude Code / Aider / others implement code changes

4. **Manager Layer (AO, optional)**
- Spawns and isolates parallel sessions
- Tracks status, PRs, CI, reviews
- Routes routine feedback back to the right worker
- Escalates only when human judgment is needed

**Rule of thumb:** SCF tells agents what the past says and how to work; AO helps them run in parallel without you doing the plumbing.

## Module Map (What Lives Where)

### Core SCF runtime

- `scripts/viking_daemon.py`
  - Watches terminal histories and exports sanitized markdown
- `scripts/openviking_mcp.py`
  - MCP bridge exposing unified search/save/health tools
- `scripts/start_openviking.sh`
  - Starts OpenViking safely (ports, config, retries)
- `scripts/context_healthcheck.sh`
  - Health checks for the whole stack
- `scripts/unified_context_deploy.sh`
  - Syncs scripts/skills and patches runtime services

### GSD integration

- `integrations/gsd/workflows/`
  - GSD workflow snippets (health and process hooks)

### AO manager-layer integration (optional)

- `integrations/agent-orchestrator/templates/`
  - AO config templates for SCF-managed environments
- `integrations/agent-orchestrator/skills/`
  - Progressive-disclosure skills (L1-L4)
- `scripts/install_agent_orchestrator.sh`
  - Installs `ao` + `pnpm` and checks prerequisites
- `scripts/scf_context_prewarm.sh`
  - Shell helper for context warmup before GSD/AO actions
- `scripts/scf_ao_spawn_from_plan.sh`
  - Bridges GSD task lists into AO worker sessions

## What Are the Upstream Projects?

| Project | What it does | Repository |
|---------|-------------|------------|
| **OpenViking** | Local vector database + semantic search engine. Stores files, vectorizes them, and provides a search API. | [volcengine/OpenViking](https://github.com/volcengine/OpenViking) |
| **Recall** | Lightweight local history index/search, now used as the default exact retrieval backend. | [arjunkmrm/recall](https://github.com/arjunkmrm/recall) |
| **GSD** | "Get Shit Done" -- an execution discipline framework. Forces AI agents to follow discuss → plan → execute → verify instead of ad-hoc problem solving. Requires context warmup (check OneContext + OpenViking first), evidence-based verification, and clear role separation in multi-agent collaboration. | [gsd-build/get-shit-done](https://github.com/gsd-build/get-shit-done) |
| **Agent Orchestrator (optional)** | Orchestrates parallel coding agents and automates session/PR/CI/review workflows. | [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) |

This repo does **not** ship upstream source code. It provides the **integration layer** that makes them work together as a unified system: the daemon that watches and sanitizes, the MCP bridge, the deployment scripts, the health checks, and (optionally) an AO manager-layer integration pack.

**Why GSD matters in this stack:** Recall/OpenViking give your AI tools memory. GSD gives them *discipline*. Without it, an AI that can search past sessions will still skip verification, ignore old decisions, and jump straight to answers without evidence. GSD enforces the workflow: warm up context first, plan before executing, verify with proof before claiming done.

## What Problems Does the Integration Solve?

The upstream tools are strong individually but don't work together out of the box. This repo handles:

| Problem | Solution |
|---------|----------|
| Each AI tool stores history in a different format/location | Daemon has parsers for all of them (JSONL, shell history, Codex sessions, Antigravity walkthroughs) |
| Raw terminal history contains secrets | Regex-based scrubbing of API keys, tokens, passwords, PEM blocks, AWS keys, Slack tokens before any export |
| OpenViking might be offline when daemon wants to export | Local pending queue with automatic retry |
| Service startup race conditions (port conflicts) | Port-busy detection, health-wait loops, ordered reload |
| No unified search across exact + semantic layers | MCP server bridges both: recall exact search + optional semantic search |
| Config generator scripts on NAS/network paths can hang | Timeout protection, ownership validation |
| Log files grow unbounded | Rotating file handlers, healthcheck-triggered truncation |
| File permission leaks | chmod 700 on data dirs, chmod 600 on exported files, ownership checks on source files |

## Requirements

- **OS**: macOS or Linux
- **Python**: 3.11+
- **OpenViking**: installed or installable via pip (`pip install openviking`)
- **Optional**: OpenViking runtime, `sqlite3`, `rsync`, `gh`
- **API Key**: Gemini API key (for OpenViking's embedding model)

## Quick Start

### 1. Clone

```bash
git clone https://github.com/dunova/silent-context-foundry.git
cd silent-context-foundry
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env
```

`GEMINI_API_KEY` is only required when you enable OpenViking semantic search.

```bash

# Optional: configure OpenViking (semantic mode only)
mkdir -p ~/.openviking_data
cp examples/ov.conf.template.json ~/.openviking_data/ov.conf
# Replace ${OPENVIKING_DATA_DIR} with ~/.openviking_data
# Replace ${GEMINI_API_KEY} with your actual key
```

The launcher also supports a local secrets file (`~/.antigravity_secrets`) with either format:
```
GEMINI_API_KEY=your-key-here
export OPENAI_API_KEY=your-key-here
```

### 3a. Deploy (macOS)

```bash
# This syncs scripts, patches launchd plists, reloads services
bash scripts/unified_context_deploy.sh
```

### 3b. Deploy (Linux systemd)

```bash
# Copy systemd units
cp templates/systemd-user/*.service ~/.config/systemd/user/
cp templates/systemd-user/*.timer ~/.config/systemd/user/

# Enable and start
systemctl --user daemon-reload
systemctl --user enable --now openviking-server.service
systemctl --user enable --now viking-daemon.service
systemctl --user enable --now context-healthcheck.timer
```

### 4. Verify

```bash
bash scripts/context_healthcheck.sh --deep
```

A healthy output (lite mode) looks like:
```
Core:
  ✅ launchd com.context.recall-lite 已加载
  ✅ onecontext 兼容入口可用
  ✅ recall 健康检查通过

🟢 Context Lite checks passed.
```

### 5. Connect MCP to your AI tool

Add the MCP server to your AI client's config. For Claude Code (`~/.claude/mcp_servers.json`):

```json
{
  "openviking-memory": {
    "command": "python3",
    "args": ["/path/to/silent-context-foundry/scripts/openviking_mcp.py"],
    "env": {
      "OPENVIKING_URL": "http://127.0.0.1:8090/api/v1",
      "OPENVIKING_ENABLE_SEMANTIC_QUERY": "0"
    }
  }
}
```

The MCP server runs over stdio and exposes 4 tools. Once connected, your AI can search across all past sessions automatically.

## Troubleshooting Index

See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) for known failures and fixes. Start with:

1. OpenViking crash loops caused by `litellm` version drift (semantic mode).
2. Legacy `realign` import failures in old watcher/worker daemons.
3. MCP script path drift across clients (`~/.claude/settings.json`, `~/.claude.json`, `~/.codex/config.toml`, `~/.config/opencode/opencode.json`, `~/.openclaw/workspace/config/mcporter.json`).

Latest upstream sync notes: [`docs/UPSTREAM_SYNC_2026-03-05.md`](docs/UPSTREAM_SYNC_2026-03-05.md).

## Repository Layout

```
silent-context-foundry/
├── scripts/
│   ├── viking_daemon.py          # Background daemon: watch, sanitize, export
│   ├── openviking_mcp.py         # MCP server: 4 tools for search & save
│   ├── start_openviking.sh       # OpenViking launcher with safety checks
│   ├── context_healthcheck.sh    # Comprehensive health check
│   ├── unified_context_deploy.sh # Deploy: sync scripts/skills, patch launchd, reload
│   ├── install_agent_orchestrator.sh # Install ao + pnpm (optional manager layer)
│   ├── scf_context_prewarm.sh    # Shell helper for context prewarm
│   ├── scf_ao_spawn_from_plan.sh # Bridge GSD task list -> AO execution
│   └── patch_openviking_semantic_processor.py  # Optional VLM quiet patch
├── templates/
│   ├── launchd/                  # macOS LaunchAgent plists
│   └── systemd-user/             # Linux systemd user services & timers
├── integrations/
│   ├── gsd/workflows/            # GSD health workflow
│   └── agent-orchestrator/       # AO manager-layer templates, skills, examples
├── examples/
│   └── ov.conf.template.json     # OpenViking config template
├── docs/
│   ├── ARCHITECTURE.md
│   ├── TROUBLESHOOTING.md        # Known issues & deployment gotchas
│   └── RELEASE_CHECKLIST.md
├── .env.example                  # All environment variables documented
├── SECURITY.md                   # Threat model and local secret hygiene
└── CONTRIBUTING.md               # Contribution guidelines
```

## How the Daemon Works

The daemon (`viking_daemon.py`) runs in the background and:

1. **Discovers sources** -- scans for history files from Claude Code, Codex, OpenCode, Kilo, and shell histories (zsh/bash). Also watches Codex session directories and Gemini Antigravity brain walkthroughs.

2. **Tails new content** -- uses inode-aware file cursors to detect new lines, file rotation, and truncation without re-reading entire files.

3. **Sanitizes** -- applies 15+ regex patterns to strip API keys (`sk-*`, `ghp_*`, `AIza*`), tokens, passwords, AWS keys, Slack tokens, and PEM blocks.

4. **Exports on idle** -- when a session has been idle for 5 minutes (configurable) and has enough messages, it writes a Markdown summary to local storage and POSTs it to OpenViking for vectorization.

5. **Queues failures** -- if OpenViking is offline, files go to a `.pending/` directory and are retried automatically on the next successful export.

6. **Adaptive polling** -- poll interval speeds up near idle-export boundaries and slows down during quiet periods, saving CPU.

## Security

- **No secrets in this repo.** CI scans for common key patterns on every push.
- **Secret scrubbing:** The daemon redacts API keys, tokens, passwords, PEM private keys, AWS access keys, and Slack tokens before exporting any content.
- **Safe secrets parsing:** `start_openviking.sh` parses `KEY=VALUE` files without `source`, preventing shell injection.
- **File permissions:** Data directories are chmod 700, exported files are chmod 600. Source files are ownership-checked before reading.
- **TLS enforcement:** Remote OpenViking URLs must use HTTPS (localhost is exempt).
- **HTTP safety:** `trust_env=False` prevents proxy credential leaks. `follow_redirects=False` prevents open redirect attacks.
- **Config generator validation:** Generator scripts must be owned by the current user. NAS paths are blocked by default.

See [SECURITY.md](SECURITY.md) for the full threat model.

## Environment Variables

All behavior is configurable via environment variables. See [`.env.example`](.env.example) for the complete list with defaults.

Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | (required) | API key for OpenViking embeddings |
| `OPENVIKING_URL` | `http://127.0.0.1:8090/api/v1` | OpenViking API endpoint |
| `VIKING_IDLE_TIMEOUT_SEC` | `300` | Seconds of inactivity before session export |
| `VIKING_POLL_INTERVAL_SEC` | `180` | Base polling interval (low-power default) |
| `VIKING_FAST_POLL_INTERVAL_SEC` | `20` | Fast path interval near active boundaries |
| `VIKING_ENABLE_SHELL_MONITOR` | `0` | Shell history monitoring (disabled by default) |
| `VIKING_ENABLE_OPENCODE_MONITOR` | `0` | OpenCode prompt-history monitoring |
| `VIKING_ANTIGRAVITY_INGEST_MODE` | `final_only` | Antigravity capture mode (`final_only` or `live`) |
| `OPENVIKING_ENABLE_SEMANTIC_QUERY` | `0` | MCP semantic query switch (OneContext-first by default) |
| `ONECONTEXT_DB_PATH` | auto-detect | OneContext sqlite path override (fallbacks supported) |

## Known Caveats

- **OpenViking VLM provider support**: Some OpenViking builds only support `openai`/`volcengine` in VLMFactory. If your config uses `gemini`, semantic summary generation may spam logs. Use `scripts/patch_openviking_semantic_processor.py` to enable quiet fallback.
- **macOS vs Linux**: The deploy script generates launchd plists on macOS. On Linux, use the provided systemd templates instead.
- **AO is optional**: SCF is still useful without AO. Add AO when your bottleneck becomes parallel agent execution management.

## Upstream & Forks

| Project | Upstream | Fork |
|---------|----------|------|
| OneContext | (original upstream unavailable as of 2026-03-05) | [dunova/OneContext](https://github.com/dunova/OneContext) |
| OpenViking | [volcengine/OpenViking](https://github.com/volcengine/OpenViking) | [dunova/OpenViking](https://github.com/dunova/OpenViking) |
| GSD | [gsd-build/get-shit-done](https://github.com/gsd-build/get-shit-done) | [dunova/get-shit-done](https://github.com/dunova/get-shit-done) |

## License

MIT -- see [LICENSE](LICENSE). Upstream tools remain under their own licenses.

---

## 问题是什么

现代开发者同时使用多个 AI 编程助手 -- Claude Code、Codex、OpenCode、Kilo、Gemini Antigravity -- 有时在同一个项目中来回切换。**但每个工具都维护着独立的记忆。** 在一个会话中建立的上下文，对其他工具完全不可见。你不得不反复解释同样的架构、同样的调试历史、同样的项目约定。

Shell 历史也是上下文的金矿（尝试过的命令、探索过的路径、调试序列），但它以纯文本文件形式存在，没有 AI 工具会去读取。

**结果是：** 你的 AI 工具患有失忆症。每个新会话都从零开始，即使答案已经存在于你机器上的某个地方。

## 这个项目做了什么

Silent Context Foundry 让你所有的 AI 终端共享**持久化、可搜索的记忆**。它通过连接三个开源系统实现单一管道：

```
┌─────────────────────────────────────────────────────────────────────┐
│                          你的机器                                    │
│                                                                     │
│  Claude Code ──┐                                                    │
│  Codex ────────┤                                                    │
│  OpenCode ─────┤    ┌──────────────┐    ┌────────────┐              │
│  Kilo ─────────┼───>│  守护进程     │───>│ OpenViking │              │
│  Antigravity ──┤    │ (监听+清洗)   │    │ (向量化)    │              │
│  Shell (.zsh/  │    └──────────────┘    └─────┬──────┘              │
│   .bash) ──────┘           │                  │                     │
│                       本地 MD 文件        语义搜索                    │
│                            │                  │                     │
│                            │           ┌──────┴──────┐              │
│                            │           │  MCP 服务器   │              │
│                            │           │  (4 个工具)   │              │
│                            │           └──────┬──────┘              │
│                            │                  │                     │
│                      ┌─────┴──────┐     任意 MCP 客户端              │
│                      │ OneContext  │    (Claude Code,                │
│                      │ (时间线)    │     Cursor 等)                   │
│                      └────────────┘                                 │
└─────────────────────────────────────────────────────────────────────┘
```

**通俗地说：**

1. 后台**守护进程**实时监听你的终端历史（Claude Code、Codex、OpenCode、Kilo、Gemini Antigravity、zsh/bash）。当会话空闲后，守护进程清洗内容（去除 API 密钥、Token、密码），保存为本地 Markdown 文件。

2. **OpenViking** 获取这些文件并向量化 -- 将原始文本转化为可搜索的语义嵌入。

3. **MCP 服务器**暴露 4 个工具，任何支持 MCP 协议的 AI 客户端都可以调用：
   - `query_viking_memory` -- 跨所有历史会话的语义搜索
   - `search_onecontext_history` -- 搜索 OneContext 的结构化时间线（事件、会话、对话轮次）
   - `save_conversation_memory` -- 主动保存重要结论或摘要
   - `context_system_health` -- 检查所有组件运行状态

4. **OneContext**（可选）提供所有 AI 交互的结构化时间线数据库，支持按事件、会话、对话轮次搜索。

**最终效果：** 当你打开一个新的 Claude Code 会话问"上周修 auth bug 的时候我试了什么方法？"，MCP 服务器会跨所有历史终端会话搜索 -- 无论当时用的是哪个 AI 工具 -- 并返回相关上下文。

## 可选第 4 层：Agent Orchestrator（AO，经理层）

SCF 解决的是共享记忆、上下文检索和流程纪律（GSD），但它不负责替你管理多个 coding agent 的并行执行。如果你的瓶颈已经变成“盯终端、盯分支、盯 CI、盯 review 评论”，可以把 **Agent Orchestrator (AO)** 接到 SCF 之上：

- **SCF（OneContext + OpenViking + GSD）**：记忆 + 检索 + 纪律
- **AO**：并行会话编排 + PR/CI/review 流程自动化

本仓库已提供 AO 集成包：`integrations/agent-orchestrator/`（模板、桥接脚本、渐进式 skill）。

## 四层架构（推荐）

把 SCF + AO 当成分层系统来用，而不是一个“大工具”：

1. **记忆层（OneContext + OpenViking）**
- OneContext：精确、结构化历史检索（event/session/turn）
- OpenViking：跨终端与 shell 历史的语义召回

2. **纪律层（GSD）**
- 强制 `discuss -> plan -> execute -> verify`
- 执行前必须做上下文预热
- 完成前必须给证据

3. **执行层（Coding Agents）**
- Codex / Claude Code / Aider 等实际写代码

4. **经理层（AO，可选）**
- 并行 spawn worker sessions
- 隔离 workspace/branch/tmux 会话
- 跟踪 PR/CI/review 状态并自动回灌
- 只在需要人判断时升级给你

**一句话：** SCF 负责“记忆 + 方法论”，AO 负责“并行执行编排”。

## 模块地图（各目录/脚本做什么）

### SCF 核心运行层

- `scripts/viking_daemon.py`
  - 监听终端历史并清洗、导出 markdown
- `scripts/openviking_mcp.py`
  - MCP 桥接（统一搜索/保存/健康检查）
- `scripts/start_openviking.sh`
  - 安全启动 OpenViking（端口、配置、重试）
- `scripts/context_healthcheck.sh`
  - 全栈健康检查
- `scripts/unified_context_deploy.sh`
  - 同步脚本/skills，并修补运行时服务配置

### GSD 集成层

- `integrations/gsd/workflows/`
  - GSD 工作流片段（健康检查与流程钩子）

### AO 经理层集成（可选）

- `integrations/agent-orchestrator/templates/`
  - SCF 场景 AO 配置模板
- `integrations/agent-orchestrator/skills/`
  - 渐进式披露 skill（L1-L4）
- `scripts/install_agent_orchestrator.sh`
  - 安装 `ao` + `pnpm` 并检查前置依赖
- `scripts/scf_context_prewarm.sh`
  - GSD/AO 操作前的上下文预热辅助脚本
- `scripts/scf_ao_spawn_from_plan.sh`
  - 把 GSD 任务清单批量转成 AO worker sessions

## 上游项目是什么？

| 项目 | 功能 | 仓库 |
|------|------|------|
| **OpenViking** | 本地向量数据库 + 语义搜索引擎。存储文件、向量化、提供搜索 API。 | [volcengine/OpenViking](https://github.com/volcengine/OpenViking) |
| **OneContext** | AI 交互的时间线结构化数据库。记录事件、会话和对话轮次。 | [dunova/OneContext（当前可用镜像）](https://github.com/dunova/OneContext) |
| **GSD** | "Get Shit Done" -- 执行纪律框架。强制 AI 按 discuss → plan → execute → verify 流程执行，杜绝跳步和返工。要求执行前先做上下文预热（查 OneContext + OpenViking），验收时提供证据和产物，多终端协作时明确角色分工。 | [gsd-build/get-shit-done](https://github.com/gsd-build/get-shit-done) |

本仓库**不包含**上游源码。它提供的是让三者协同工作的**集成层**：监听和清洗的守护进程、MCP 桥接、部署脚本、健康检查。

**为什么需要 GSD：** OneContext 和 OpenViking 给你的 AI 工具提供记忆，GSD 给它们提供*纪律*。没有它，AI 即使能搜索历史会话，仍然会跳过验证、忽略旧决策、不提供证据就声称"搞定了"。GSD 强制执行流程：先预热上下文，规划后再执行，用证据验收后才算完成。

## 集成解决了什么问题？

上游工具各自强大，但开箱并不能协同工作。本仓库处理的问题：

| 问题 | 解决方案 |
|------|----------|
| 每个 AI 工具的历史格式/位置不同 | 守护进程内置了所有格式的解析器（JSONL、Shell history、Codex sessions、Antigravity walkthroughs） |
| 原始终端历史包含密钥 | 导出前自动清洗 API 密钥、Token、密码、PEM 私钥、AWS 密钥、Slack Token |
| 导出时 OpenViking 可能离线 | 本地待处理队列 + 自动重试 |
| 服务启动竞态（端口冲突） | 端口占用检测、健康等待循环、有序重载 |
| OneContext 和 OpenViking 没有统一搜索 | MCP 服务器桥接两者：结构化时间线 + 语义搜索 |
| NAS 上的配置生成脚本可能挂起 | 超时保护、所有权验证 |
| 日志文件无限增长 | 滚动日志处理器、健康检查触发截断 |
| 文件权限泄露 | 数据目录 chmod 700、导出文件 chmod 600、源文件所有权检查 |

## 系统要求

- **操作系统**: macOS 或 Linux
- **Python**: 3.11+
- **OpenViking**: 已安装或可通过 pip 安装 (`pip install openviking`)
- **可选**: OneContext/Aline CLI、`sqlite3`、`rsync`、`gh`
- **API 密钥**: Gemini API key（用于 OpenViking 的嵌入模型）

## 快速开始

### 1. 克隆

```bash
git clone https://github.com/dunova/silent-context-foundry.git
cd silent-context-foundry
```

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env -- 至少设置 GEMINI_API_KEY

# 配置 OpenViking
mkdir -p ~/.openviking_data
cp examples/ov.conf.template.json ~/.openviking_data/ov.conf
# 将 ${OPENVIKING_DATA_DIR} 替换为 ~/.openviking_data
# 将 ${GEMINI_API_KEY} 替换为你的实际密钥
```

启动脚本也支持本地密钥文件（`~/.antigravity_secrets`）：
```
GEMINI_API_KEY=your-key-here
export OPENAI_API_KEY=your-key-here
```

### 3a. 部署（macOS）

```bash
# 同步脚本、修补 launchd、重载服务
bash scripts/unified_context_deploy.sh
```

### 3b. 部署（Linux systemd）

```bash
# 复制 systemd 单元文件
cp templates/systemd-user/*.service ~/.config/systemd/user/
cp templates/systemd-user/*.timer ~/.config/systemd/user/

# 启用并启动
systemctl --user daemon-reload
systemctl --user enable --now openviking-server.service
systemctl --user enable --now viking-daemon.service
systemctl --user enable --now context-healthcheck.timer
```

### 4. 验证

```bash
bash scripts/context_healthcheck.sh --deep
```

### 5. 将 MCP 连接到你的 AI 工具

在 AI 客户端配置中添加 MCP 服务器。以 Claude Code 为例（`~/.claude/mcp_servers.json`）：

```json
{
  "openviking-memory": {
    "command": "python3",
    "args": ["/path/to/silent-context-foundry/scripts/openviking_mcp.py"],
    "env": {
      "OPENVIKING_URL": "http://127.0.0.1:8090/api/v1",
      "OPENVIKING_ENABLE_SEMANTIC_QUERY": "0"
    }
  }
}
```

MCP 服务器通过 stdio 运行，暴露 4 个工具。连接后，你的 AI 可以自动搜索所有历史会话。

## 安全

- **仓库中不包含密钥。** 每次推送 CI 自动扫描常见密钥模式。
- **密钥清洗：** 守护进程在导出前自动清洗 API 密钥、Token、密码、PEM 私钥、AWS 密钥、Slack Token。
- **安全的密钥文件解析：** `start_openviking.sh` 解析 `KEY=VALUE` 而非 `source`，防止 Shell 注入。
- **文件权限：** 数据目录 chmod 700，导出文件 chmod 600。读取前检查源文件所有权。
- **TLS 强制：** 远程 OpenViking URL 必须使用 HTTPS（localhost 豁免）。
- **HTTP 安全：** `trust_env=False` 防止代理凭据泄露；`follow_redirects=False` 防止重定向攻击。

详见 [SECURITY.md](SECURITY.md)。

## 许可证

MIT -- 详见 [LICENSE](LICENSE)。上游工具保持各自的许可证。
