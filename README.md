# Context Mesh Foundry

本地优先上下文基础设施，面向多 agent AI 编码团队的单体产品。
无 MCP、无 Docker、无分布式依赖，只有一个统一 CLI 和本地 runtime，帮助工程组在自己机器上完成调试、记忆、迁移和部署。

## 核心承诺

- **单体可控**：上下文采集、语义搜索、记忆存储、守护进程都由同一套 `contextmesh` 代码驱动，不再跳转多个桥接脚本。
- **本地优先**：默认路径 100% 在本地，远程同步默认关闭；部署目录、服务名、数据库都围绕单机运行优化。
- **无 MCP 依赖**：不需要 MCP 或其他云端服务即可完整运行，连接历史、终端和 agent 的唯一信任源是本地索引。
- **Benchmark 驱动**：自带 `benchmarks/` 验证真实热点，定期校准瓶颈，统计结果直接反馈到本地仪表盘。
- **Native 迁移路线**：在 Python monolith 上量化热点后，再逐步用 Rust/Go 取代，保持稳定性的同时提升速度。

## 商业定位与价值主张

Context Mesh Foundry 0.5.0 把本地上下文设施打磨成面向企业的产品型单体：在工程团队对本地可控性、可审计性与速度有苛刻要求时，只需一套命令就能部署、验证、迁移与升级。

- **确定性部署与运营**：统一的 `context_cli.py` 入口（`search`、`serve`、`health`、`maintain` 等）与 `bash scripts/unified_context_deploy.sh` 脚本，让运维只需记住一套流程即可复现环境。
- **本地治理与审计**：所有上下文数据、守护进程与 viewer 配置都在 SQLite + 本地目录里，可用 `python3 scripts/context_cli.py health`、`python3 scripts/context_cli.py smoke` 立刻确认状态。
- **可度量迁移计划**：借助 `python -m benchmarks` 收集 latency、throughput，再用 `cargo run --release` / `go run` 与 `python3 scripts/context_cli.py native-scan --backend auto --threads 4` 比较结果，量化 Native 迁移收益。
- **稳定演进不破坏体验**：Native 原型在 `native/session_scan`、`native/session_scan_go`，但 CLI 参数、守护进程入口、文档命令都保持不变，确保客户体验持续一致。

## 产品形态

- `python3 scripts/context_cli.py`：统一 CLI（`search`、`semantic`、`save`、`export`、`import`、`serve`、`maintain`、`health`、`smoke`、`native-scan`）覆盖搜索、维护、守护进程验证与 Native 扫描，所有命令都可在任何支持 Python 3.11 的平台复现。
- `scripts/context_daemon.py`：canonical 守护进程入口，可由 `bash scripts/unified_context_deploy.sh` 注册成 `com.contextmesh.daemon`，daemon 与 viewer 均可通过 `context_cli.py health` 验证。
- `scripts/session_index.py` / `scripts/memory_index.py`：本地 SQLite 索引，直连 Codex/Claude/shell 历史，结果可用 `python3 scripts/context_cli.py search` 或 `semantic` 命令察看。
- `benchmarks/`：指令化 benchmark 目录帮助工程师在切换 Native 代码前验证 throughput 与 latency。
- `native/session_scan/`：Rust hot-path 原型，绑定 `context_cli.py native-scan --backend auto --threads 4` 入口。
- `native/session_scan_go/`：Go 版扫描原型，配合 `native-scan --backend go` 观察轻量化替代方案。

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
python3 scripts/context_cli.py smoke
```

### 安装态烟测

```bash
python3 scripts/smoke_installed_runtime.py
# 或直接对当前工作副本运行
python3 scripts/context_cli.py smoke
```

## 部署与运维

- 默认安装目录：`~/.local/share/context-mesh-foundry`。
- 本地服务：`com.contextmesh.daemon`、`com.contextmesh.healthcheck`。
- `CONTEXT_MESH_*` 系列变量统一配置：`STORAGE_ROOT`、`REMOTE_URL`、`ENABLE_REMOTE_SYNC`、`VIEWER_HOST`、`VIEWER_PORT`、`SESSION_INDEX_DB_PATH`。
- 旧桥接（`recall-lite`、`openviking`、`aline`）可清理，部署流程仅需 `bash scripts/unified_context_deploy.sh`。
- 建议验证命令：`python3 scripts/context_cli.py health`，`python3 scripts/context_cli.py smoke`，`python3 scripts/context_cli.py native-scan --backend auto --threads 4`，必要时再配合 `cargo run --release` 或 `go run` 验证 Native 模块的性能。

## 安装矩阵

| 平台 | 先决条件 | 快速部署 | 说明 |
| --- | --- | --- | --- |
| Linux x86_64 / ARM64 | Python 3.11+、SQLite3、本地 shell、可选 Rust 工具链 | `git clone https://github.com/dunova/context-mesh-foundry.git && cd context-mesh-foundry && cp .env.example .env && bash scripts/unified_context_deploy.sh`，再用 `python3 scripts/context_cli.py health`、`python3 scripts/context_cli.py smoke` 或 `python3 scripts/context_cli.py native-scan --backend auto --threads 4` 验证 | Rust/cargo 仅在构建 `native/session_scan` 原型时必须，其他模块只要 Python 即可运行。 |
| macOS (Intel / Apple Silicon) | 同上，确保 `/opt/homebrew/bin` 在 PATH 中 | `git clone https://github.com/dunova/context-mesh-foundry.git && cd context-mesh-foundry && cp .env.example .env && bash scripts/unified_context_deploy.sh`，再用 `python3 scripts/context_cli.py health` 与 `python3 scripts/context_cli.py smoke` 复测 | `brew install sqlite` 仅在缺失时使用；`bash` 与 `cargo` 同样可用。 |
| Windows (WSL2 / PowerShell) | WSL 2 (Ubuntu 22.04+) / Git Bash + Windows Terminal，启用 Windows Subsystem for Linux | `git clone https://github.com/dunova/context-mesh-foundry.git` 后在 WSL 里 `cp .env.example .env && bash scripts/unified_context_deploy.sh`，再用 `python3 scripts/context_cli.py health` 与 `python3 scripts/context_cli.py native-scan --backend auto --threads 4` 检查 | 建议在 WSL 环境中运行，避免混合文件权限。WSL 内可用 `rustup` 安装 native 依赖。 |

## Native 路线

1. 在 Python 单体内用 `python -m benchmarks --query <真实业务场景>` 按主权路径收集 latency/throughput 数据，产出可对比的凭证。
2. 识别 CPU、IO 或内存重度热点后，把路径抽象成 `native/session_scan`（Rust）或 `native/session_scan_go`（Go）原型，复用 `context_cli.py native-scan --backend <auto|rust|go>` 入口。
3. 每次 Native 替换都保持相同 CLI 参数，先在 Python 侧用 `python3 scripts/context_cli.py native-scan --backend auto --threads 4` 复测，再用 `cargo run --release` 或 `go run` 校验性能，最后运行 `python3 scripts/context_cli.py health` 与 `python3 scripts/context_cli.py smoke`、`python3 scripts/context_cli.py native-scan --backend auto --threads 4` 确认整体守护进程与扫描链路稳定。
4. 发布前用 `python3 -m benchmarks --iterations 1 --warmup 0 --query perf` 记录对比数据，把结果写入 release notes 目录，并在 README/CHANGELOG 中注明审核命令与差异。

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

### 如何在不同平台选择安装流程？

参考上面 `安装矩阵` 表格，所有平台都可以从 `git clone https://github.com/dunova/context-mesh-foundry.git` 开始，依赖同一套 `bash scripts/unified_context_deploy.sh` 和 `python3 scripts/context_cli.py health` 验证。Mac/Windows 需要先确认 Python 3.11 与 SQLite 可用，Linux 则额外可以直接在 shell 里按步骤运行脚本。

### Native 迁移路线会影响操作体验吗？

不会。每次用 `native/session_scan` 或 `native/session_scan_go` 替换热点时，仍然通过 `python3 scripts/context_cli.py native-scan` 触发，CLI 参数与守护进程入口一致。工程师只要在 `benchmarks/` 跑一次对比，确认 `cargo run --release` 与 `go run` 输出与 `python -m benchmarks` 的 latency 信息相当，就可以安全切换。

### 如何验证部署及 Native 迁移后的状态？

每一次部署或 Native 替换后，应连续运行 `python3 scripts/context_cli.py health` 与 `python3 scripts/context_cli.py smoke`，再用 `python3 scripts/context_cli.py native-scan --backend auto --threads 4` 检查扫描链路与 Native backend 配合是否有异常；必要时对比 `cargo run --release` 或 `go run` 的输出，提高迁移前后的可比性，并把 benchmark 结果写入 `docs/RELEASE_NOTES_0.5.0.md` 与 `CHANGELOG.md` 供商业审计。

## 版本与发布

- 当前版本：`0.5.0`，详见本地 [`VERSION`](./VERSION)。
- 发布纪要：[`CHANGELOG.md`](./CHANGELOG.md) 与 [`docs/RELEASE_NOTES_0.5.0.md`](./docs/RELEASE_NOTES_0.5.0.md)。
***
## English Snapshot

Context Mesh Foundry is a local-context monolith built for AI coding teams. No MCP, no Docker, fully self-hosted CLI and runtime. Start with the unified `contextmesh` CLI, benchmark real workloads with `benchmarks/`, and migrate hot paths into the `native/session_scan` prototype without touching the operator experience.

For detailed steps, refer to the same sections above.
