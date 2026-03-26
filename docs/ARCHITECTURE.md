# ContextGO 架构 / ContextGO Architecture

> Related: [CONFIGURATION.md](CONFIGURATION.md) · [API.md](API.md) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md) · [../CONTRIBUTING.md](../CONTRIBUTING.md)

## 中文版

### 架构图

```mermaid
flowchart LR
    A["Codex / Claude / Shell<br/>会话与历史源"] --> B["ContextGO Daemon<br/>采集与脱敏"]
    B --> C["Session Index + Memory Index<br/>本地 SQLite / Files"]
    C --> D["ContextGO CLI<br/>Search / Semantic / Save / Export / Import"]
    D --> E["Viewer API<br/>本地可视化与查询"]
    D --> F["Health / Smoke / Benchmark<br/>交付验证链"]
    C --> G["Rust / Go Hot Paths<br/>渐进式提速"]
```

### 架构树

```text
ContextGO/
├── docs/                      # 架构、发布、故障排查、商业交付文档
├── scripts/                   # 单体主链：CLI / daemon / server / smoke / health / deploy
│   ├── context_cli.py         # 搜索、语义、记忆、viewer、smoke 的唯一入口
│   ├── context_daemon.py      # 会话采集与脱敏写盘
│   ├── session_index.py       # 会话索引与检索排序
│   ├── memory_index.py        # 记忆 / observation 索引
│   ├── context_server.py      # viewer 服务入口
│   ├── context_maintenance.py # 清理、修复、维护
│   ├── context_smoke.py       # 工作副本 smoke
│   ├── context_healthcheck.sh # 健康检查
│   └── unified_context_deploy.sh
├── native/
│   ├── session_scan/          # Rust 热路径
│   └── session_scan_go/       # Go 热路径
├── benchmarks/                # Python / native-wrapper 基准
├── config/                    # 运行时配置文件（noise_markers.json）
├── integrations/gsd/          # GSD / gstack 对接
├── artifacts/                 # autoresearch 结果、测试集、QA 报告
├── templates/                 # launchd / systemd-user 模板
├── examples/                  # 配置模板
└── patches/                   # 兼容补丁说明
```

### 组件概览

1. **采集层**  
   `scripts/context_daemon.py` 负责收集终端会话、shell 历史并在写入前完成 `<private>` 过滤。

2. **索引层**  
   `scripts/session_index.py` 与 `scripts/memory_index.py` 负责本地 SQLite / 文件索引。

3. **检索与服务层**  
   `scripts/context_cli.py` 是唯一 canonical CLI，承载 `health / search / semantic / save / export / import / serve / maintain`。

4. **运维验证层**  
   `scripts/context_healthcheck.sh`、`scripts/context_smoke.py`、`scripts/smoke_installed_runtime.py` 与 `benchmarks/run.py` 组成统一验证链。

### 数据流

1. 终端与 agent 历史由 `context_daemon` 采集并脱敏。  
2. 数据写入本地 storage root（默认 `~/.contextgo`）。  
3. `session_index` 与 `memory_index` 构建索引。  
4. `context_cli` 统一执行检索、导入导出、health、smoke 与 native 调用。  
5. `context_server` 提供本地 viewer API。  

### 设计原则

- **本地优先**：默认无外部桥接、无 Docker、无远程 recall 依赖  
- **统一入口**：用户始终只面对一套 CLI  
- **默认单体**：复杂度尽量收在内部，而不是拆散到多服务  
- **验证前置**：任何变更都要过 `health / smoke / benchmark`  
- **渐进提速**：Python 保稳，Rust / Go 替换热点  

## English Version

### Architecture Diagram

```mermaid
flowchart LR
    A["Codex / Claude / Shell<br/>session and history sources"] --> B["ContextGO Daemon<br/>capture and sanitization"]
    B --> C["Session Index + Memory Index<br/>local SQLite / files"]
    C --> D["ContextGO CLI<br/>Search / Semantic / Save / Export / Import"]
    D --> E["Viewer API<br/>local visualization and query"]
    D --> F["Health / Smoke / Benchmark<br/>delivery validation chain"]
    C --> G["Rust / Go Hot Paths<br/>incremental acceleration"]
```

### Architecture Tree

```text
ContextGO/
├── docs/                      # architecture, release, troubleshooting, and commercial delivery docs
├── scripts/                   # unified monolith mainline: CLI / daemon / server / smoke / health / deploy
│   ├── context_cli.py         # single canonical entry point for search, semantic, memory, viewer, smoke
│   ├── context_daemon.py      # session capture and sanitized write-through
│   ├── session_index.py       # session index and ranked retrieval
│   ├── memory_index.py        # memory / observation index
│   ├── context_server.py      # viewer service entry point
│   ├── context_maintenance.py # cleanup, repair, and maintenance operations
│   ├── context_smoke.py       # working-copy smoke tests
│   ├── context_healthcheck.sh # shell health check
│   └── unified_context_deploy.sh
├── native/
│   ├── session_scan/          # Rust hot path
│   └── session_scan_go/       # Go hot path
├── benchmarks/                # Python / native-wrapper performance baselines
├── config/                    # runtime configuration files (noise_markers.json)
├── integrations/gsd/          # GSD / gstack workflow integration
├── artifacts/                 # autoresearch outputs, test sets, QA reports
├── templates/                 # launchd / systemd-user service templates
├── examples/                  # configuration templates
└── patches/                   # compatibility patch notes
```

### Component Overview

1. **Capture layer**
   `scripts/context_daemon.py` collects terminal sessions and shell history, applying `<private>` redaction before writing to disk.

2. **Index layer**
   `scripts/session_index.py` and `scripts/memory_index.py` maintain local SQLite and file-based indexes used for all retrieval operations.

3. **Retrieval and service layer**
   `scripts/context_cli.py` is the single canonical CLI, exposing `health`, `search`, `semantic`, `save`, `export`, `import`, `serve`, and `maintain` subcommands.

4. **Operational validation layer**
   `scripts/context_healthcheck.sh`, `scripts/context_smoke.py`, `scripts/smoke_installed_runtime.py`, and `benchmarks/run.py` form the unified delivery validation chain.

### Data Flow

1. Terminal and agent histories are captured and sanitized by `context_daemon`.
2. Data is written to the local storage root (default: `~/.contextgo`).
3. `session_index` and `memory_index` build indexes on top of SQLite and local files.
4. `context_cli` handles retrieval, import/export, health checks, smoke tests, and native backend invocations.
5. `context_server` exposes the local viewer API for interactive inspection.

### Design Principles

- **Local-first**: no external bridges, no Docker, no remote recall dependency by default.
- **Single entry point**: operators always interact with one CLI surface.
- **Monolith by default**: complexity is contained internally rather than split across multiple services.
- **Validation-gated changes**: every change must pass `health`, `smoke`, and `benchmark` before merging.
- **Gradual native acceleration**: Python provides stability; Rust and Go replace hot paths incrementally without modifying the CLI contract.
