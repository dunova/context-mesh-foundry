# Context Mesh Foundry

本地优先上下文基础设施，面向多 agent AI 编码团队的单体产品。
无 MCP、无 Docker、无分布式依赖，只有一个统一 CLI 和本地 runtime，帮助工程组在自己机器上完成调试、记忆、迁移和部署。

## 核心承诺

- **单体可控**：上下文采集、语义搜索、记忆存储、守护进程都由同一套 `contextmesh` 代码驱动，不再跳转多个桥接脚本。
- **本地优先**：默认路径 100% 在本地，远程同步默认关闭；部署目录、服务名、数据库都围绕单机运行优化。
- **无 MCP 依赖**：不需要 MCP 或其他云端服务即可完整运行，连接历史、终端和 agent 的唯一信任源是本地索引。
- **Benchmark 驱动**：自带 `benchmarks/` 验证真实热点，定期校准瓶颈，统计结果直接反馈到本地仪表盘。
- **Native 迁移路线**：在 Python monolith 上量化热点后，再逐步用 Rust/Go 取代，保持稳定性的同时提升速度。

## 产品形态

- `python scripts/context_cli.py`：统一入口，提供 `search`、`semantic`、`save`、`export/import`、`serve`、`maintain`、`health` 等操作。
- `scripts/context_daemon.py`：canonical 守护进程，可由 `bash scripts/unified_context_deploy.sh` 注册为 `com.contextmesh.daemon`。
- `scripts/session_index.py` / `scripts/memory_index.py`：本地 SQLite 索引，直连 Codex/Claude/shell 历史，无同步延迟。
- `benchmarks/`：精确定位热路径，量化本地运行速度，为 Rust/Go 替换提供数据上下文。
- `native/session_scan/`：首个 Rust hot-path 原型，示例如何逐步迁移关键子系统。
- `native/session_scan_go/`：Go 版扫描原型，用于评估更轻的一体化二进制路径。

## 绩效与本地迁移路线

1. 在 Python 单体中保持业务稳定，确保部署脚本只需一条命令安装本地 runtime。
2. 利用 `python -m benchmarks` 对话串、存储、语义检索等真实场景打桩，生成可复现结果。
3. 标定瓶颈后，针对性将数据密集的功能抽象成 `native/session_scan` 等模块，保持单体 shell 无感知。
4. 每次 native 迁移都保持 CLI 不变，并通过 `cargo run --release`、`benchmarks` 复测，确保性能优于旧路径。

## 入门快线

```bash
git clone https://github.com/dunova/context-mesh-foundry.git
cd context-mesh-foundry
cp .env.example .env
bash scripts/unified_context_deploy.sh
python3 scripts/context_cli.py health
```

### 核心命令

```
python3 scripts/context_cli.py search "auth root cause" --limit 10 --literal
python3 scripts/context_cli.py semantic "数据库 schema 决策" --limit 5
python3 scripts/context_cli.py save --title "Auth fix" --content "..." --tags auth,bug
python3 scripts/context_cli.py export "" /tmp/contextmesh-export.json --limit 1000
python3 scripts/context_cli.py import /tmp/contextmesh-export.json
python3 scripts/context_cli.py serve --host 127.0.0.1 --port 37677
python3 scripts/context_cli.py maintain --dry-run
python3 scripts/context_cli.py health
python3 scripts/context_cli.py native-scan --backend auto --threads 4
```

### 安装态烟测

```bash
python3 scripts/smoke_installed_runtime.py
```

## 部署与运维

- 默认安装目录：`~/.local/share/context-mesh-foundry`。
- 本地服务：`com.contextmesh.daemon`、`com.contextmesh.healthcheck`。
- `CONTEXT_MESH_*` 系列变量统一配置：`STORAGE_ROOT`、`REMOTE_URL`、`ENABLE_REMOTE_SYNC`、`VIEWER_HOST`、`VIEWER_PORT`、`SESSION_INDEX_DB_PATH`。
- 旧桥接（`recall-lite`、`openviking`、`aline`）可清理，部署流程仅需 `bash scripts/unified_context_deploy.sh`。

## FAQ

### 这是一个库、一个工具，还是一套本地服务？

三者都是，但对使用者来说它首先是一套本地产品：

- CLI：`context_cli.py`
- daemon：`context_daemon.py`
- viewer：`context_server.py`
- health/deploy：`context_healthcheck.sh` / `unified_context_deploy.sh`

你可以只把它当命令行工具用，也可以把它部署成常驻本地上下文基础设施。

### 为什么默认不启用远程同步？

因为默认目标是：

- 最少依赖
- 最低 surprise
- 最稳定本地链路
- 最低 token / 网络开销

远程同步是可选增强，不是默认主路径。

### 为什么不直接全部用 Rust/Go 重写？

因为当前最优路线是：

1. 先把 Python 主链收敛成稳定单体
2. 用 benchmark 找真实热点
3. 只把热点模块逐步替换成 Rust/Go

这样能同时兼顾速度、稳定性和迁移成本。

## 版本与发布

- 当前版本：`0.5.0`，详见本地 [`VERSION`](./VERSION)。
- 发布纪要：[`CHANGELOG.md`](./CHANGELOG.md) 与 [`docs/RELEASE_NOTES_0.5.0.md`](./docs/RELEASE_NOTES_0.5.0.md)。
***
## English Snapshot

Context Mesh Foundry is a local-context monolith built for AI coding teams. No MCP, no Docker, fully self-hosted CLI and runtime. Start with the unified `contextmesh` CLI, benchmark real workloads with `benchmarks/`, and migrate hot paths into the `native/session_scan` prototype without touching the operator experience.

For detailed steps, refer to the same sections above.
