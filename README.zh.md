<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/media/logo-dark.svg">
    <img src="docs/media/logo.svg" alt="ContextGO" width="360">
  </picture>
</p>

<p align="center">
  <strong>为多 Agent AI 编码团队打造的本地优先上下文与记忆引擎。</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/contextgo/"><img src="https://img.shields.io/pypi/v/contextgo?color=2563eb&style=flat" alt="PyPI"></a>
  <a href="https://pypi.org/project/contextgo/"><img src="https://img.shields.io/pypi/dm/contextgo?color=0d9488&label=installs&style=flat" alt="Monthly Installs"></a>
  <a href="https://pypi.org/project/contextgo/"><img src="https://img.shields.io/pypi/pyversions/contextgo?color=3776ab&style=flat" alt="Python"></a>
  <a href="https://github.com/dunova/ContextGO/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-6d28d9?style=flat" alt="License"></a>
  <a href="https://github.com/dunova/ContextGO/actions/workflows/verify.yml"><img src="https://github.com/dunova/ContextGO/actions/workflows/verify.yml/badge.svg" alt="CI"></a>
  <a href="https://codecov.io/gh/dunova/ContextGO"><img src="https://codecov.io/gh/dunova/ContextGO/branch/main/graph/badge.svg" alt="Coverage"></a>
  <a href="https://github.com/dunova/ContextGO/actions/workflows/codeql.yml"><img src="https://github.com/dunova/ContextGO/actions/workflows/codeql.yml/badge.svg" alt="CodeQL"></a>
</p>

<p align="center">
  <a href="README.md">English</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="#快速上手">快速上手</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="#混合语义搜索">混合搜索</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="#面向-ai-agent">AI Agent 配置</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="docs/">文档</a>
</p>

---

> **你的 AI Agent 每次对话都从零开始。它忘记了昨天的决策、那个方案为什么被放弃、团队已经试过什么。**
>
> ContextGO 解决这个问题。它在本地索引所有 Codex、Claude 和 Shell 会话历史——无需 Docker，
> 无需 MCP 代理，无需外部向量数据库，无需云端依赖。一行命令安装：`pipx install contextgo`。
> 下一次 `contextgo search` 查询即可跨越数周历史、跨越所有 AI 工具，返回完整结果。
>
> 混合语义搜索（model2vec + BM25）。Rust/Go 原生扫描引擎保障速度。
> 任何 AI 编码 Agent 无需集成代码即可直接查询跨会话持久记忆。

---

## 快速上手

```bash
# 1. 安装
pipx install "contextgo[vector]"
eval "$(contextgo shell-init)"

# 2. 初始化索引
contextgo health
contextgo sources

# 3. 验证
contextgo search "authentication" --limit 5
```

> **提示：** 请使用 `pipx` 而非 `pip install`——macOS（Homebrew Python 3.12+）和大多数 Linux
> 发行版因 [PEP 668](https://peps.python.org/pep-0668/) 要求如此。
> 安装 pipx：`brew install pipx`（macOS）或 `apt install pipx`（Debian/Ubuntu）。

ContextGO 无需任何配置，自动发现所有受支持的本地来源：
`Codex` · `Claude Code` · `Cursor` · `Accio Work` · `Gemini/Antigravity` · `OpenCode` · `Kilo` · `OpenClaw` · `zsh/bash 终端历史`

**已有历史会话后，启用混合搜索：**

```bash
export CONTEXTGO_EXPERIMENTAL_SEARCH_BACKEND=vector
contextgo vector-sync
contextgo vector-status
```

<details>
<summary><strong>贡献者源码安装</strong></summary>

```bash
git clone https://github.com/dunova/ContextGO.git
cd ContextGO
bash scripts/unified_context_deploy.sh
export PATH="$HOME/.local/bin:$PATH"
eval "$(contextgo shell-init)"
contextgo health
```

</details>

---

## 为什么选择 ContextGO

| 能力 | ContextGO | Cursor Context | Continue.dev | Mem0 |
|---|:---:|:---:|:---:|:---:|
| 默认本地优先 | **是** | 部分 | 部分 | 否 |
| 无需 Docker | **是** | 是 | 部分 | 否 |
| 多 Agent 会话索引 | **是** | 否 | 否 | 部分 |
| 跨工具历史（Codex + Claude + Shell） | **是** | 否 | 否 | 否 |
| 混合语义搜索 | **是** | 否 | 否 | 部分 |
| Rust/Go 原生扫描 | **是** | 否 | 否 | 否 |
| 默认无 MCP | **是** | 是 | 否 | 否 |
| 内置交付验证链 | **是** | 否 | 否 | 否 |
| CJK / Unicode 全面支持 | **是** | 部分 | 否 | 否 |
| 一行安装，零配置 | **是** | 否 | 否 | 否 |

**关键数据：** 2,183 项测试 &nbsp;|&nbsp; 97.1% 覆盖率 &nbsp;|&nbsp; Python 3.10+ &nbsp;|&nbsp; 混合搜索 &lt; 5ms（热状态）&nbsp;|&nbsp; 8 个 AI 工具 + shell

---

## 混合语义搜索

ContextGO 内置可选的混合搜索引擎，结合**向量语义相似度**和 **BM25 关键词评分**，通过倒数排名融合（RRF）合并结果。

| 组件 | 技术 | 体积 | 延迟 |
|---|---|---|---|
| 向量嵌入 | [model2vec](https://github.com/MinishLab/model2vec) (potion-base-8M) | 30 MB 模型 | 0.2 ms/查询 |
| 关键词评分 | [bm25s](https://github.com/xhluca/bm25s) | 仅需 numpy | ~80 ms |
| 融合策略 | 倒数排名融合 (k=60) | 零额外开销 | 基于排名 |
| 存储 | SQLite BLOB (`vector_index.db`) | 1.6 MB / 1K 文档 | — |

**实测性能（Mac mini，1,085 条索引会话）：**

| 操作 | 延迟 |
|---|---|
| 单次嵌入 | **0.2 ms** |
| 纯向量搜索 | **3 ms** (p50)，14 ms (p99) |
| 混合搜索（向量 + BM25） | **79 ms** (p50)，92 ms (p99) |
| 完整管线（搜索 + 富化） | **82 ms** |
| 模型冷加载（首次） | ~6 s |
| 增量同步（无变化） | **6 ms** |

向量依赖完全可选——缺少 `model2vec` 时自动降级为 FTS5/LIKE 搜索。

---

## 架构

```mermaid
flowchart LR
    subgraph 数据源
        A1[Codex]
        A2[Claude]
        A3[Shell]
        A4[OpenCode]
        A5[Kilo]
        A6[OpenClaw]
    end

    subgraph 核心层
        B[守护进程\n采集 · 脱敏]
        C[(SQLite WAL\n+ 文件)]
        F[原生后端\nRust · Go]
        V[向量索引\nmodel2vec · BM25]
    end

    subgraph 接口层
        D[CLI\n检索 / 记忆 / 导出]
        E[Viewer API\n127.0.0.1:37677]
    end

    数据源 --> B
    B --> C
    C --> F
    C --> V
    C --> D
    D --> E
```

**技术栈：** Python（控制层）| Rust（`native/session_scan/`）| Go（`native/session_scan_go/`）| SQLite WAL（索引）| model2vec + bm25s（可选向量搜索）

---

## 命令参考

### 检索与召回

```bash
contextgo sources                                 # 查看已探测的平台与 adapter 状态
contextgo search "schema 迁移" --limit 10         # 全文关键词检索
contextgo semantic "数据库设计决策" --limit 5       # 记忆优先检索，关键词兜底
contextgo q "认证"                                 # 快速召回
contextgo native-scan --backend auto --threads 4  # 直接调用原生扫描器
```

### 向量搜索

```bash
contextgo vector-sync                             # 嵌入所有待处理会话文档
contextgo vector-sync --force                     # 强制重新嵌入所有文档
contextgo vector-status                           # 显示向量索引统计
```

### 记忆

```bash
contextgo save --title "认证修复" --content "..." --tags auth,bug
contextgo export "" /tmp/export.json --limit 1000
contextgo import /tmp/export.json
```

### 运维

```bash
contextgo health                       # 验证安装状态与存储完整性
contextgo maintain --enqueue-missing   # 索引所有已有会话
contextgo maintain --dry-run           # 清理并修复本地索引
contextgo serve --port 37677           # 在 127.0.0.1:37677 启动本地 Viewer
contextgo smoke                        # 维护者 smoke 测试套件
```

### Daemon 模式

```bash
contextgo daemon start
contextgo daemon status
contextgo daemon stop
```

### Shell 集成

```bash
eval "$(contextgo shell-init)"         # 激活 cg 别名
```

Tab 补全配置（bash/zsh/fish）见 [docs/SHELL_COMPLETION.md](docs/SHELL_COMPLETION.md)。

---

## 面向 AI Agent

> **如果你是一个 AI Agent**（Claude、Codex、Cursor、Windsurf、Copilot 或任何 LLM 工具），请按以下步骤完成配置，获得跨会话持久记忆能力。

### 第一步 — 安装

```bash
pipx ensurepath
pipx install "contextgo[vector]"
eval "$(contextgo shell-init)"
contextgo health
contextgo sources
```

### 第二步 — 构建初始索引

```bash
export CONTEXTGO_EXPERIMENTAL_SEARCH_BACKEND=vector
contextgo vector-sync
contextgo vector-status
```

### 第三步 — 添加到 AI 工具配置

将 [`AGENTS.md`](AGENTS.md) 的内容复制到对应工具的持久化指令文件：

| AI 工具 | 配置位置 |
|---------|-------------|
| Claude Code | `~/.claude/CLAUDE.md`（追加 AGENTS.md 内容） |
| Codex | 项目根目录的 `AGENTS.md`（克隆仓库后自动生效） |
| Cursor | 项目根目录的 `.cursorrules` |
| Windsurf | 项目根目录的 `.windsurfrules` |
| 其他工具 | 你的工具的系统提示词或指令文件 |

### 第四步 — 验证自动行为

配置完成后，以下行为自动激活：

- **冷启动 / 新窗口** → 只在起始阶段召回一次；同主题内默认保持静默
- **用户说“继续”或“我在做什么”** → `contextgo semantic "topic" --limit 3` 并总结
- **用户询问过往决策** → 检索并用 2–3 句话总结
- **结构化问题（架构、调用链、影响半径）** → 先看 code graph，再用 ContextGO 补历史决策
- **同主题追问** → 默认跳过召回，降低 token 成本
- **解决了复杂问题** → 建议执行 `contextgo save` 持久化结论

完整行为规范：[AGENTS.md](AGENTS.md)

---

## 配置

所有配置均通过环境变量完成，默认值开箱即用。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CONTEXTGO_STORAGE_ROOT` | `~/.contextgo` | 所有数据的根目录 |
| `CONTEXTGO_SESSION_INDEX_DB_PATH` | `$ROOT/index/session_index.db` | 会话索引 SQLite 路径 |
| `MEMORY_INDEX_DB_PATH` | `$ROOT/index/memory_index.db` | 记忆索引 SQLite 路径 |
| `CONTEXTGO_EXPERIMENTAL_SEARCH_BACKEND` | _（空）_ | 设为 `vector` 启用混合搜索 |
| `CONTEXTGO_VECTOR_MODEL` | `minishlab/potion-base-8M` | model2vec 模型名称 |
| `CONTEXTGO_VECTOR_DIM` | `256` | 向量维度 |
| `CONTEXTGO_VIEWER_HOST` | `127.0.0.1` | Viewer 绑定地址 |
| `CONTEXTGO_VIEWER_PORT` | `37677` | Viewer TCP 端口 |
| `CONTEXTGO_VIEWER_TOKEN` | _（空）_ | 非回环地址绑定时的 Bearer token |
| `CONTEXTGO_ENABLE_REMOTE_MEMORY_HTTP` | `0` | 启用远程同步（默认关闭） |

完整参考：[docs/CONFIGURATION.md](docs/CONFIGURATION.md)

---

## 项目结构

```
ContextGO/
├── src/contextgo/             # 运行时主包
│   ├── context_cli.py         # 统一 CLI 入口
│   ├── session_index.py       # SQLite 会话索引 + 混合搜索
│   ├── memory_index.py        # 记忆与观察索引
│   ├── source_adapters.py     # 多平台本地来源自动发现
│   └── ...
├── tests/                     # 完整自动化测试套件
├── scripts/                   # wrapper 与运维入口脚本
├── native/
│   ├── session_scan/          # Rust 热路径二进制
│   └── session_scan_go/       # Go 并行扫描二进制
├── docs/                      # 架构、配置、发布说明
├── docs/benchmarks/           # Python 与原生性能对比测试
└── docs/templates/            # launchd / systemd-user 服务模板
```

---

## 参与贡献

见 [CONTRIBUTING.md](.github/CONTRIBUTING.md) 了解本地开发环境、测试命令和 PR 质量门。

```bash
git clone https://github.com/dunova/ContextGO.git
cd ContextGO
bash scripts/unified_context_deploy.sh
export PATH="$HOME/.local/bin:$PATH"
contextgo health
```

| 资源 | |
|---|---|
| 安全 | [SECURITY.md](.github/SECURITY.md) — 威胁模型与负责任披露 |
| 变更日志 | [CHANGELOG.md](.github/CHANGELOG.md) — 完整版本变更记录 |
| 架构 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 设计原则 |
| 故障排查 | [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — 常见故障与排查步骤 |

---

## 许可证

采用 [AGPL-3.0](LICENSE) 许可证。你可以自由使用、修改和分发 ContextGO——以服务形式分发修改版本时，需以同等条款开源。如需商业授权，请联系维护者。

Copyright 2025–2026 [Dunova](https://github.com/dunova).
