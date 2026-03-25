# Context Mesh Foundry

Local-first context infrastructure for AI coding workflows.

[中文版](#中文版) | [English](#english)

## Why

Modern AI development happens across many parallel surfaces:

- Claude Code
- Codex CLI
- OpenCode
- shell history
- ad-hoc one-off terminals

Each session tends to start with partial memory. Teams then waste time re-feeding context, re-deriving earlier decisions, and re-debugging already solved problems.

Context Mesh Foundry turns that into a local product:

- one runtime
- one CLI
- one local index
- one daemon
- no MCP requirement
- no Docker requirement

## What You Get

- `context_cli.py`
  Unified entrypoint for search, semantic fallback, save, import/export, viewer, maintenance, and health.
- `session_index.py`
  Built-in local session index over Codex, Claude, shell, and related histories.
- `memory_index.py`
  Local memory/document index for saved artifacts and exported summaries.
- `context_daemon.py`
  Canonical long-running daemon entrypoint.

The default path is fully local and self-contained. Optional remote sync remains available, but it is not required and is disabled by default.

## Status

Current version: `0.5.0`

This branch contains:

- standalone mainline runtime
- package-safe Python modules
- local benchmark harness
- first Rust performance prototype

## Quick Start

```bash
git clone https://github.com/dunova/context-mesh-foundry.git
cd context-mesh-foundry
cp .env.example .env
bash scripts/unified_context_deploy.sh
python3 scripts/context_cli.py health
```

## Core Commands

```bash
# exact local session search
python3 scripts/context_cli.py search "auth bug" --limit 10 --literal

# semantic fallback over local memory
python3 scripts/context_cli.py semantic "database decisions" --limit 5

# save an important conclusion
python3 scripts/context_cli.py save --title "Auth fix" --content "Root cause..." --tags auth,bugfix

# export / import local indexed observations
python3 scripts/context_cli.py export "" /tmp/contextmesh-export.json --limit 1000
python3 scripts/context_cli.py import /tmp/contextmesh-export.json

# start local viewer
python3 scripts/context_cli.py serve --host 127.0.0.1 --port 37677

# local maintenance / dry run
python3 scripts/context_cli.py maintain --dry-run

# health snapshot
python3 scripts/context_cli.py health
```

## Product Shape

The project is now organized around a clean mainline:

- `scripts/context_cli.py`
- `scripts/context_daemon.py`
- `scripts/context_server.py`
- `scripts/context_maintenance.py`
- `scripts/session_index.py`
- `scripts/memory_index.py`
- `scripts/context_config.py`

Legacy compatibility code has been pushed behind thin wrappers and archived under `scripts/legacy/`.

## Performance

Python benchmarks:

```bash
python3 -m benchmarks --iterations 3 --warmup 1 --query benchmark
```

Rust prototype:

```bash
cd native/session_scan
CARGO_TARGET_DIR=/tmp/context_mesh_target cargo run --release -- --threads 4
```

Strategy:

1. converge Python into a clean monolith
2. benchmark real hotspots
3. replace hot paths incrementally in Rust or Go
4. keep the product stable while speeding up internals

## Deployment

The deploy script installs the canonical runtime to:

- `/Users/<you>/.local/share/context-mesh-foundry`

And manages local services:

- `com.contextmesh.daemon`
- `com.contextmesh.healthcheck`

Old local service traces such as `recall-lite`, `openviking`, `aline`, and older daemon logs can be removed safely once the current `contextmesh` services are healthy.

## Environment

The preferred config namespace is now `CONTEXT_MESH_*`.

Older variables remain supported where needed for compatibility, but new setups should prefer:

- `CONTEXT_MESH_STORAGE_ROOT`
- `CONTEXT_MESH_REMOTE_URL`
- `CONTEXT_MESH_ENABLE_REMOTE_SYNC`
- `CONTEXT_MESH_VIEWER_HOST`
- `CONTEXT_MESH_VIEWER_PORT`
- `CONTEXT_MESH_SESSION_INDEX_DB_PATH`

See [`.env.example`](/Volumes/AI/GitHub/context-mesh-foundry/.env.example).

## Release Checklist

See [docs/RELEASE_CHECKLIST.md](/Volumes/AI/GitHub/context-mesh-foundry/docs/RELEASE_CHECKLIST.md).

## Changelog

See [CHANGELOG.md](/Volumes/AI/GitHub/context-mesh-foundry/CHANGELOG.md).

## Architecture

See [docs/ARCHITECTURE.md](/Volumes/AI/GitHub/context-mesh-foundry/docs/ARCHITECTURE.md).

## Troubleshooting

See [docs/TROUBLESHOOTING.md](/Volumes/AI/GitHub/context-mesh-foundry/docs/TROUBLESHOOTING.md).

## License

[GPL-3.0](/Volumes/AI/GitHub/context-mesh-foundry/LICENSE)

---

## 中文版

### 项目定位

Context Mesh Foundry 是一套面向 AI 编程工作流的本地上下文基础设施。

它解决的问题很直接：

- 多终端、多 agent、多会话并行工作
- 上下文在不同工具之间丢失
- 旧决策、旧排障、旧约束无法被稳定复用

现在的主链已经收敛成一个本地单体产品：

- 一个 CLI
- 一个本机会话索引
- 一个本地记忆索引
- 一个后台守护进程
- 不依赖 MCP
- 不依赖 Docker

### 当前版本

`0.5.0`

### 核心能力

- `context_cli.py`
  统一入口，负责 `search / semantic / save / export / import / serve / maintain / health`
- `session_index.py`
  本机会话索引，直接扫描 Codex、Claude、shell 等历史
- `memory_index.py`
  本地记忆/文档索引
- `context_daemon.py`
  canonical 守护进程入口

### 快速开始

```bash
git clone https://github.com/dunova/context-mesh-foundry.git
cd context-mesh-foundry
cp .env.example .env
bash scripts/unified_context_deploy.sh
python3 scripts/context_cli.py health
```

### 常用命令

```bash
# 精确检索本地会话
python3 scripts/context_cli.py search "身份验证 bug" --limit 10 --literal

# 本地语义补洞
python3 scripts/context_cli.py semantic "数据库配置决策" --limit 5

# 保存关键结论
python3 scripts/context_cli.py save --title "Auth fix" --content "Root cause..." --tags auth,bugfix

# 导出 / 导入
python3 scripts/context_cli.py export "" /tmp/contextmesh-export.json --limit 1000
python3 scripts/context_cli.py import /tmp/contextmesh-export.json

# 启动本地 viewer
python3 scripts/context_cli.py serve --host 127.0.0.1 --port 37677

# 本地维护
python3 scripts/context_cli.py maintain --dry-run

# 健康检查
python3 scripts/context_cli.py health
```

### 当前产品形态

当前仓库的主线已经集中到这些模块：

- `scripts/context_cli.py`
- `scripts/context_daemon.py`
- `scripts/context_server.py`
- `scripts/context_maintenance.py`
- `scripts/session_index.py`
- `scripts/memory_index.py`
- `scripts/context_config.py`

历史兼容实现已下沉到 `scripts/legacy/`。

### 性能与渐进重写

Python 基准：

```bash
python3 -m benchmarks --iterations 3 --warmup 1 --query benchmark
```

Rust 原型：

```bash
cd native/session_scan
CARGO_TARGET_DIR=/tmp/context_mesh_target cargo run --release -- --threads 4
```

路线不是“一上来全量重写”，而是：

1. 先把 Python 主链收敛成干净单体
2. 对真实热点做 benchmark
3. 用 Rust/Go 渐进替换热路径
4. 在保持稳定的前提下持续提速

### 部署与运行

部署脚本会把 canonical runtime 安装到：

- `/Users/<你>/.local/share/context-mesh-foundry`

并管理本地服务：

- `com.contextmesh.daemon`
- `com.contextmesh.healthcheck`

### 配置

新配置建议统一使用 `CONTEXT_MESH_*` 前缀。

常见变量：

- `CONTEXT_MESH_STORAGE_ROOT`
- `CONTEXT_MESH_REMOTE_URL`
- `CONTEXT_MESH_ENABLE_REMOTE_SYNC`
- `CONTEXT_MESH_VIEWER_HOST`
- `CONTEXT_MESH_VIEWER_PORT`
- `CONTEXT_MESH_SESSION_INDEX_DB_PATH`

详见 [`.env.example`](/Volumes/AI/GitHub/context-mesh-foundry/.env.example)。

### 其他文档

- [CHANGELOG.md](/Volumes/AI/GitHub/context-mesh-foundry/CHANGELOG.md)
- [docs/ARCHITECTURE.md](/Volumes/AI/GitHub/context-mesh-foundry/docs/ARCHITECTURE.md)
- [docs/TROUBLESHOOTING.md](/Volumes/AI/GitHub/context-mesh-foundry/docs/TROUBLESHOOTING.md)
- [docs/RELEASE_CHECKLIST.md](/Volumes/AI/GitHub/context-mesh-foundry/docs/RELEASE_CHECKLIST.md)
