# AGENTS.md — AI Agent Onboarding for ContextGO
# AI 智能体入门指南

> If you are an AI agent (Claude, Codex, Cursor, Windsurf, or any LLM tool),
> this file tells you everything you need to install and use ContextGO in under 60 seconds.
>
> 如果你是 AI 智能体（Claude、Codex、Cursor、Windsurf 等），
> 本文档可让你在 60 秒内完成 ContextGO 的安装与上手。

---

## Quick Start / 快速接管

**Requirements / 依赖：** Python 3.10+, Bash, Git

```bash
pipx install "contextgo[vector]" || pipx upgrade "contextgo[vector]"
contextgo setup       # one-command auto-prewarm for Claude Code / Codex / OpenClaw
contextgo health
eval "$(contextgo shell-init)"
```

macOS and Linux. No Docker. No MCP. No external services.
适用于 macOS 和 Linux。无需 Docker、无需 MCP、无外部服务依赖。

Do not present `pip install` as the default install path for macOS users.
不要把 `pip install` 作为 macOS 用户的默认安装路径。

For repository maintenance or local development:
```bash
git clone https://github.com/dunova/ContextGO.git
cd ContextGO
bash scripts/unified_context_deploy.sh
contextgo smoke --sandbox
```

---

## What is ContextGO? / 项目简介

ContextGO is a local-first context and memory runtime. It unifies Codex, Claude, and shell session histories into one searchable SQLite index, giving AI agents persistent cross-session memory and semantic search over past decisions — all via a single CLI.

ContextGO 是本地优先的上下文与记忆运行时，将 Codex、Claude 及 shell 历史统一索引到 SQLite，为 AI 智能体提供跨会话持久记忆和语义检索，无需任何外部基础设施。

Storage root: `~/.contextgo` (override: `CONTEXTGO_STORAGE_ROOT`)
存储根目录：`~/.contextgo`（可通过 `CONTEXTGO_STORAGE_ROOT` 覆盖）

---

## Key Commands / 核心命令

```bash
# Auto-prewarm setup (one-time, configures hooks for all AI tools) / 自动预热配置
contextgo setup

# Health check / 健康检查
contextgo health

# Smoke gate (sandboxed, does not touch ~/.contextgo) / 冒烟测试（沙箱，不写入真实数据）
contextgo smoke --sandbox

# Semantic search over memories and session history / 语义搜索记忆与历史
contextgo semantic "database design decision" --limit 5

# Keyword search / 关键词全文搜索
contextgo search "auth root cause" --limit 10

# Save a durable memory / 保存持久记忆
contextgo save \
  --title "Fix: JWT expiry" \
  --content "Set exp to 3600 in auth.py line 42" \
  --tags auth,jwt,fix

# Cross-agent handoff / 跨智能体交接
contextgo export "" /tmp/snapshot.json --limit 5000
contextgo import /tmp/snapshot.json
```

---

## Automatic Behaviors / 自动行为

As an AI agent, execute these behaviors **proactively** — do not wait to be asked.
作为 AI 智能体，以下行为**主动执行**，无需用户提示。

### Default Search Policy / 默认检索策略

| Trigger / 触发场景 | Action / 行动 |
|---|---|
| Session starts with a continuation task / 会话以延续任务开始 | `contextgo semantic "<topic>" --limit 3` |
| Uncertain about project history / 不确定项目历史 | `contextgo search "<keyword>" --limit 5` |
| User asks about past work / 用户询问过去工作 | `contextgo semantic "<question>"` |
| Before an architectural decision / 架构决策前 | `contextgo search "<topic>" --limit 3` |

**Output rule / 输出规则：** Summarize in 2–3 sentences. Never paste raw output. If search returns nothing, proceed silently.
结果用 2–3 句话总结，禁止粘贴原始输出；无结果时静默继续。

### Default Save Policy / 默认保存策略

| Trigger / 触发场景 | Title Pattern / 标题格式 |
|---|---|
| Hard bug root cause found / 发现疑难 bug 根因 | `Bug: <short description>` |
| Architectural decision made / 架构决策完成 | `Decision: <what and why>` |
| Session wrap-up ("done", "wrap up") / 会话结束 | `Handoff: <next steps>` |

```bash
contextgo save \
  --title "Decision: chose SQLite over Elasticsearch" \
  --content "Rationale, file paths, gotchas, next steps" \
  --tags "project,topic,decision"
```

Save only **durable knowledge** (decisions, root causes, warnings). The daemon already captures routine edits.
只保存**持久知识**（决策、根因、警告），日常编辑由 daemon 自动索引。

---

## Project Map / 项目结构

| Path | Role / 说明 |
|---|---|
| `src/contextgo/context_cli.py` | CLI entry point / 命令入口 |
| `src/contextgo/context_config.py` | Storage root, env resolution / 存储配置 |
| `src/contextgo/session_index.py` | SQLite session index / 会话索引 |
| `src/contextgo/memory_index.py` | Memory index, export/import / 记忆索引 |
| `src/contextgo/context_daemon.py` | Session capture daemon / 会话捕获守护进程 |
| `src/contextgo/context_server.py` | Local viewer API / 本地查看器 |
| `src/contextgo/context_core.py` | Shared helpers / 共享工具函数 |
| `src/contextgo/context_native.py` | Rust/Go backend orchestration / 原生后端调度 |
| `src/contextgo/context_smoke.py` | Smoke test suite / 冒烟测试套件 |
| `native/session_scan/` | Rust hot-path scanner / Rust 扫描器 |
| `native/session_scan_go/` | Go parallel scanner / Go 并行扫描器 |
| `artifacts/` | Autoresearch outputs — **do not edit** / 自动研究产物，勿修改 |
| `patches/` | Compatibility notes — **do not edit** / 兼容性说明，勿修改 |

---

## Before Any Commit / 提交前检查

All steps must pass. / 所有步骤必须通过。

```bash
# Syntax checks / 语法检查
bash -n scripts/*.sh
python3 -m py_compile src/contextgo/*.py

# Targeted unit + integration tests / 单元与集成测试
python3 -m pytest \
  tests/test_context_cli.py \
  tests/test_context_core.py \
  tests/test_session_index.py \
  tests/test_context_native.py \
  tests/test_context_smoke.py \
  tests/test_autoresearch_contextgo.py

# End-to-end quality gate / 端到端质量门控
python3 scripts/e2e_quality_gate.py

# Installed-runtime validation / 已安装运行时验证
contextgo smoke --sandbox
contextgo health
```

---

## Skills (Claude Code only) / 技能（仅 Claude Code）

```bash
bash docs/skills/install.sh
```

| Skill | Purpose / 用途 |
|---|---|
| `contextgo-gsd` | Full GSD workflow reference / GSD 工作流完整参考 |
| `contextgo-recall` | Search strategy and flags / 检索策略与参数 |
| `contextgo-save` | Save rules, tags, proactive prompts / 保存规则与标签约定 |

Skills complement the auto-behaviors above. Auto-behaviors work without skills installed.
技能是对上述自动行为的补充。不安装技能，自动行为同样生效。

---

## References / 参考文档

- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) — full config options / 完整配置项
- [docs/API.md](docs/API.md) — HTTP API reference / HTTP API 参考
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design / 系统设计
