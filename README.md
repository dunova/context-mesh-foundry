<p align="center">
  <img src="docs/media/logo.svg" alt="ContextGO" width="360">
</p>

<p align="center">
  <strong>Local-first context & memory engine for multi-agent AI coding teams.</strong><br>
  <em>为多 Agent AI 编码团队打造的本地优先上下文与记忆引擎。</em>
</p>

<p align="center">
  <a href="https://pypi.org/project/contextgo/"><img src="https://img.shields.io/pypi/v/contextgo?color=2563eb&style=flat" alt="PyPI"></a>
  <a href="https://pypi.org/project/contextgo/"><img src="https://img.shields.io/pypi/dm/contextgo?color=0d9488&label=installs&style=flat" alt="Monthly Installs"></a>
  <a href="https://pypi.org/project/contextgo/"><img src="https://img.shields.io/pypi/pyversions/contextgo?color=3776ab&style=flat" alt="Python"></a>
  <a href="https://github.com/dunova/ContextGO/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-6d28d9?style=flat" alt="License"></a>
  <a href="https://github.com/dunova/ContextGO/actions/workflows/verify.yml"><img src="https://github.com/dunova/ContextGO/actions/workflows/verify.yml/badge.svg" alt="CI"></a>
  <a href="https://codecov.io/gh/dunova/ContextGO"><img src="https://codecov.io/gh/dunova/ContextGO/branch/main/graph/badge.svg" alt="Coverage"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="#hybrid-semantic-search">Hybrid Search</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="#for-ai-agents">AI Agent Setup</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="docs/">Docs</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="#%E4%B8%AD%E6%96%87%E7%89%88">中文</a>
</p>

---

> **No Docker. No MCP broker. No external vector database. Install with `pipx`, run one health check, and ContextGO starts discovering local AI chat history immediately.**
>
> ContextGO unifies Codex, Claude, and shell session histories into one searchable,
> auditable index stored entirely on your machine. Hybrid semantic search (model2vec + BM25).
> Native Rust/Go scanning. Persistent cross-session memory that any AI coding agent can query.

---

## Quick Start

```bash
# 1) Install pipx once (skip if you already have it)
brew install pipx              # macOS
# sudo apt install pipx        # Debian/Ubuntu
pipx ensurepath

# Open a new shell if pipx was just installed, then:
pipx install "contextgo[vector]"
eval "$(contextgo shell-init)"

# Verify the runtime on a brand-new machine
contextgo health
contextgo sources
contextgo search "authentication" --limit 5
```

**Prefer the zero-dependency core install?**

```bash
pipx install contextgo
eval "$(contextgo shell-init)"
contextgo health
```

> **Note:** On macOS (especially Homebrew Python 3.12+) and many Linux distros, direct `pip install`
> is not a supported end-user install path because of [PEP 668](https://peps.python.org/pep-0668/)
> and system Python restrictions. Use `pipx` instead. Install pipx with `brew install pipx` (macOS)
> or `apt install pipx` (Debian/Ubuntu).

**See what ContextGO auto-detected**

```bash
contextgo sources
```

ContextGO automatically discovers and normalizes supported local sources including:

- `Codex`
- `Claude Code`
- `OpenCode`
- `Kilo`
- `OpenClaw`
- `zsh` / `bash` shell history

If you install a new supported tool later, you do not need to reconfigure ContextGO.
The next `contextgo health`, `contextgo sources`, or `contextgo search ...` run will
rescan local source registries and absorb the new history.

**Enable hybrid search once you already have history**

```bash
contextgo sources
export CONTEXTGO_EXPERIMENTAL_SEARCH_BACKEND=vector
contextgo health
contextgo vector-sync
contextgo vector-status
```

`contextgo vector-sync` now initializes a fresh local index cleanly, including a brand-new install
that has no `session_index.db` yet.

**Upgrade cleanly**

```bash
pipx upgrade contextgo || pipx install "contextgo[vector]"
eval "$(contextgo shell-init)"
contextgo health
contextgo sources
```

If you are upgrading from a local checkout instead of PyPI:

```bash
bash scripts/upgrade_contextgo.sh
```

ContextGO now versions its adapter cache and automatically refreshes normalized
source mirrors when the adapter schema changes, so upgrades do not leave stale
OpenCode / Kilo / OpenClaw adapter artifacts behind.

**One-command uninstall**

```bash
# Keep indexed data and memories
bash scripts/uninstall_contextgo.sh

# Remove everything, including ~/.contextgo data
bash scripts/uninstall_contextgo.sh --purge-data
```

<details>
<summary><strong>Source install for contributors</strong></summary>

```bash
# From source (recommended for contributors and local repo work)
git clone https://github.com/dunova/ContextGO.git
cd ContextGO
bash scripts/unified_context_deploy.sh
export PATH="$HOME/.local/bin:$PATH"
eval "$(contextgo shell-init)"
contextgo health
```

```bash
# Optional maintainer validation gate
contextgo smoke --sandbox
```

</details>

---

## Why ContextGO

| Capability | ContextGO | Cursor Context | Continue.dev | Mem0 |
|---|:---:|:---:|:---:|:---:|
| Local-first by default | **Yes** | Partial | Partial | No |
| Docker-free | **Yes** | Yes | Partial | No |
| Multi-agent session index | **Yes** | No | No | Partial |
| Hybrid semantic search | **Yes** | No | No | Partial |
| Native Rust/Go scan | **Yes** | No | No | No |
| MCP-free by default | **Yes** | Yes | No | No |
| Built-in delivery validation | **Yes** | No | No | No |
| CJK / Unicode full support | **Yes** | Partial | No | No |

**Key numbers:** 2,041 tests | 97.1% coverage | Python 3.10+ | Hybrid search < 5ms (warm)

---

## Hybrid Semantic Search

ContextGO includes an optional hybrid search engine combining **vector similarity** and **BM25 keyword scoring** via Reciprocal Rank Fusion (RRF).

| Component | Technology | Size | Latency |
|---|---|---|---|
| Vector embeddings | [model2vec](https://github.com/MinishLab/model2vec) (potion-base-8M) | 30 MB model | 0.2 ms/query |
| Keyword scoring | [bm25s](https://github.com/xhluca/bm25s) | numpy only | ~80 ms |
| Fusion | Reciprocal Rank Fusion (k=60) | zero overhead | rank-based |
| Storage | SQLite BLOB (`vector_index.db`) | 1.6 MB / 1K docs | -- |

**Benchmarks (Mac mini, 1,085 indexed sessions):**

| Operation | Latency |
|---|---|
| Single embedding | **0.2 ms** |
| Pure vector search | **3 ms** (p50), 14 ms (p99) |
| Hybrid search (vector + BM25) | **79 ms** (p50), 92 ms (p99) |
| Full pipeline (search + enrich) | **82 ms** |
| Model cold load (first run) | ~6 s |
| Incremental sync (no changes) | **6 ms** |

All vector dependencies are optional -- ContextGO degrades gracefully to FTS5/LIKE search when `model2vec` is absent.

---

## Architecture

```mermaid
flowchart LR
    subgraph Sources
        A1[Codex]
        A2[Claude]
        A3[Shell]
        A4[OpenCode]
        A5[Kilo]
        A6[OpenClaw]
    end

    subgraph Core
        B[Daemon\nCapture &middot; Sanitize]
        C[(SQLite WAL\n+ Files)]
        F[Native Backends\nRust &middot; Go]
        V[Vector Index\nmodel2vec &middot; BM25]
    end

    subgraph Interface
        D[CLI\nsearch / memory / export]
        E[Viewer API\n127.0.0.1:37677]
    end

    Sources --> B
    B --> C
    C --> F
    C --> V
    C --> D
    D --> E
```

**Stack:** Python (control plane) | Rust (`native/session_scan/`) | Go (`native/session_scan_go/`) | SQLite WAL (index) | model2vec + bm25s (optional vector search)

---

## Commands

### Search & Recall

```bash
contextgo sources                                 # show detected platforms and adapter status
contextgo search "schema migration" --limit 10    # full-text keyword search
contextgo semantic "database design" --limit 5    # memory-first search with keyword fallback
contextgo native-scan --backend auto --threads 4  # Rust/Go scanner directly
```

### Vector Search

```bash
contextgo vector-sync                             # embed all pending session documents
contextgo vector-sync --force                     # re-embed everything
contextgo vector-status                           # show vector index statistics
```

### Memory

```bash
contextgo save --title "Auth fix" --content "..." --tags auth,bug
contextgo export "" /tmp/export.json --limit 1000
contextgo import /tmp/export.json
```

### Operations

```bash
contextgo health                    # verify installation and storage integrity
contextgo maintain --enqueue-missing # index all existing sessions
contextgo smoke                     # maintainer smoke suite
contextgo maintain --dry-run        # clean and repair local index
contextgo serve --port 37677        # start local viewer at 127.0.0.1:37677
```

---

## For AI Agents

> **If you are an AI agent reading this** (Claude, Codex, Cursor, Windsurf, Copilot, or any LLM tool), follow the four steps below. After setup, you will have persistent cross-session memory across all your conversations.

### Step 1 -- Install

```bash
pipx ensurepath
pipx install "contextgo[vector]"
eval "$(contextgo shell-init)"
contextgo health
contextgo sources
```

### Step 2 -- Build the initial index

```bash
contextgo sources
export CONTEXTGO_EXPERIMENTAL_SEARCH_BACKEND=vector
contextgo health
contextgo vector-sync
contextgo vector-status
```

### Step 3 -- Add to your AI tool's instructions

Copy the contents of [`AGENTS.md`](AGENTS.md) into your tool's persistent instructions:

| AI Tool | Where to add |
|---------|-------------|
| Claude Code | `~/.claude/CLAUDE.md` (append AGENTS.md content) |
| Codex | `AGENTS.md` in project root (already works if repo is cloned) |
| Cursor | `.cursorrules` in project root |
| Windsurf | `.windsurfrules` in project root |
| Other | Your tool's system prompt or instruction file |

Tell your user: *"I need to add ContextGO instructions to [config file]. This lets me automatically search your session history when relevant. OK?"*

### Step 4 -- Verify automatic behaviors

After setup, these behaviors activate automatically:

- **You don't know the project history** &rarr; `contextgo search "topic" --limit 5` before answering
- **User says "continue" or "what was I doing"** &rarr; `contextgo semantic "topic" --limit 3` and summarize
- **User asks about past decisions** &rarr; search and summarize in 2--3 sentences
- **Hard problem solved** &rarr; suggest `contextgo save` to persist the conclusion
- **Session ends** &rarr; save handoff notes for the next session

Full behavioral spec: [AGENTS.md](AGENTS.md)

---

## Configuration

All configuration is via environment variables. Defaults work out of the box.

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_STORAGE_ROOT` | `~/.contextgo` | Root directory for all data |
| `CONTEXTGO_SESSION_INDEX_DB_PATH` | `$ROOT/index/session_index.db` | Session index SQLite path |
| `MEMORY_INDEX_DB_PATH` | `$ROOT/index/memory_index.db` | Memory index SQLite path |
| `CONTEXTGO_EXPERIMENTAL_SEARCH_BACKEND` | _(empty)_ | Set to `vector` for hybrid search |
| `CONTEXTGO_VECTOR_MODEL` | `minishlab/potion-base-8M` | model2vec model name |
| `CONTEXTGO_VECTOR_DIM` | `256` | Vector dimension |
| `CONTEXTGO_VIEWER_HOST` | `127.0.0.1` | Viewer bind address |
| `CONTEXTGO_VIEWER_PORT` | `37677` | Viewer TCP port |
| `CONTEXTGO_VIEWER_TOKEN` | _(empty)_ | Bearer token for non-loopback binding |
| `CONTEXTGO_ENABLE_REMOTE_MEMORY_HTTP` | `0` | Enable remote sync (disabled by default) |

Full reference: [docs/CONFIGURATION.md](docs/CONFIGURATION.md)

---

## Project Structure

```
ContextGO/
├── src/contextgo/             # Runtime package
│   ├── context_cli.py         # Unified CLI entry point
│   ├── session_index.py       # SQLite session index + hybrid search
│   ├── memory_index.py        # Memory and observation index
│   ├── source_adapters.py     # Auto-discovery for tool-specific local storage
│   └── ...
├── tests/                     # Full automated test suite
├── scripts/                   # Thin wrappers + operational shell scripts
├── native/
│   ├── session_scan/          # Rust hot-path binary
│   └── session_scan_go/       # Go parallel-scan binary
├── docs/                      # Architecture, config, release notes
├── benchmarks/                # Python vs. native performance harness
└── templates/                 # launchd / systemd-user service templates
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for local dev setup, test commands, and PR quality gates.

First-time contributor setup from a fresh machine:

```bash
git clone https://github.com/dunova/ContextGO.git
cd ContextGO
bash scripts/unified_context_deploy.sh
export PATH="$HOME/.local/bin:$PATH"
contextgo health
```

Repository layout principles:

- Runtime code lives in `src/contextgo/`
- Tests live in `tests/`
- `scripts/` is reserved for wrappers and operational entrypoints
- Old release notes are archived under `docs/archive/`

| Resource | |
|---|---|
| Security | [SECURITY.md](SECURITY.md) -- threat model and responsible disclosure |
| Changelog | [CHANGELOG.md](CHANGELOG.md) -- full version history |
| Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) -- design principles |
| Troubleshooting | [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) -- common failure modes |

---

## License

Licensed under [AGPL-3.0](LICENSE). You may use, modify, and distribute ContextGO freely -- any modifications distributed as a service must also be open-sourced under AGPL-3.0. Commercial licensing available; contact the maintainers.

Copyright 2025--2026 [Dunova](https://github.com/dunova).

---

<h1 id="中文版">中文版</h1>

<p align="center">
  <img src="docs/media/logo.svg" alt="ContextGO" width="360">
</p>

<p align="center">
  <strong>为多 Agent AI 编码团队打造的本地优先上下文与记忆引擎。</strong><br>
  <em>Local-first context & memory engine for multi-agent AI coding teams.</em>
</p>

<p align="center">
  <a href="#quick-start">English Version</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="docs/">文档</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="#混合语义搜索">混合搜索</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="#面向-ai-agent">AI Agent 配置</a>
</p>

---

> **无需 Docker，无需 MCP 代理，无需外部向量数据库。使用 `pipx` 安装，跑一次 health，ContextGO 就会开始自动发现本地 AI 聊天历史。**
>
> ContextGO 将 Codex、Claude 和 Shell 会话历史统一为一条可检索、可追溯的索引，
> 全部存储在本机。内置混合语义搜索（model2vec + BM25）。Rust/Go 原生扫描引擎。
> 为每个 AI 编码 Agent 提供跨会话记忆。

---

## 快速上手

```bash
# 1) 首次安装 pipx（已安装可跳过）
brew install pipx              # macOS
# sudo apt install pipx        # Debian/Ubuntu
pipx ensurepath

# 如果刚安装完 pipx，请重新打开一个 shell，然后执行：
pipx install "contextgo[vector]"
eval "$(contextgo shell-init)"

# 在一台全新机器上验证运行时
contextgo health
contextgo sources
contextgo search "authentication" --limit 5
```

**如果你只想安装零依赖核心版：**

```bash
pipx install contextgo
eval "$(contextgo shell-init)"
contextgo health
```

> **提示：** macOS（尤其 Homebrew Python 3.12+）和部分 Linux 发行版不再适合把 `pip install`
> 作为终端用户安装路径（见 [PEP 668](https://peps.python.org/pep-0668/)）。请使用 `pipx`。
> 安装 pipx：`brew install pipx`（macOS）或 `apt install pipx`（Debian/Ubuntu）。

**先看看 ContextGO 自动发现了哪些平台**

```bash
contextgo sources
```

默认会自动探测并吸收这些本地来源：

- `Codex`
- `Claude Code`
- `OpenCode`
- `Kilo`
- `OpenClaw`
- `zsh` / `bash` 终端历史

以后如果你又安装了新的受支持工具，不需要重新配置。下一次执行
`contextgo health`、`contextgo sources` 或 `contextgo search ...` 时，
ContextGO 会重新扫描并自动吸收这些新增来源。

**已有历史会话后，再启用混合搜索：**

```bash
contextgo sources
export CONTEXTGO_EXPERIMENTAL_SEARCH_BACKEND=vector
contextgo health
contextgo vector-sync
contextgo vector-status
```

`contextgo vector-sync` 现在会在全新环境中自动初始化本地索引，即使本地还没有
`session_index.db` 也能正常运行。

**升级到最新版本**

```bash
pipx upgrade contextgo || pipx install "contextgo[vector]"
eval "$(contextgo shell-init)"
contextgo health
contextgo sources
```

如果你是从本地仓库升级而不是从 PyPI 升级：

```bash
bash scripts/upgrade_contextgo.sh
```

ContextGO 现在会对 adapter 缓存做 schema 版本管理。升级后如果 OpenCode / Kilo /
OpenClaw 的规范化缓存格式发生变化，会自动刷新，不会残留旧版本的 adapter 产物。

**一键卸载**

```bash
# 保留 ~/.contextgo 数据
bash scripts/uninstall_contextgo.sh

# 连同索引与记忆数据一起彻底删除
bash scripts/uninstall_contextgo.sh --purge-data
```

<details>
<summary><strong>贡献者源码安装</strong></summary>

```bash
# 从源码安装（推荐给贡献者与仓库维护者）
git clone https://github.com/dunova/ContextGO.git
cd ContextGO
bash scripts/unified_context_deploy.sh
export PATH="$HOME/.local/bin:$PATH"
eval "$(contextgo shell-init)"
contextgo health
```

```bash
# 可选的维护者验证门禁
contextgo smoke --sandbox
```

</details>

---

## 为什么选择 ContextGO

| 能力 | ContextGO | Cursor Context | Continue.dev | Mem0 |
|---|:---:|:---:|:---:|:---:|
| 默认本地优先 | **是** | 部分 | 部分 | 否 |
| 无需 Docker | **是** | 是 | 部分 | 否 |
| 多 Agent 会话索引 | **是** | 否 | 否 | 部分 |
| 混合语义搜索 | **是** | 否 | 否 | 部分 |
| Rust/Go 原生扫描 | **是** | 否 | 否 | 否 |
| 默认无 MCP | **是** | 是 | 否 | 否 |
| 内置交付验证链 | **是** | 否 | 否 | 否 |
| CJK / Unicode 全面支持 | **是** | 部分 | 否 | 否 |

**关键数据：** 2,041 项测试 | 97.14% 覆盖率 | Python 3.10+ | 混合搜索 < 5ms（热状态）

---

## 混合语义搜索

ContextGO 内置可选的混合搜索引擎，结合 **向量语义相似度** 和 **BM25 关键词评分**，通过倒数排名融合（RRF）合并结果。

| 组件 | 技术 | 体积 | 延迟 |
|---|---|---|---|
| 向量嵌入 | [model2vec](https://github.com/MinishLab/model2vec) (potion-base-8M) | 30 MB 模型 | 0.2 ms/查询 |
| 关键词评分 | [bm25s](https://github.com/xhluca/bm25s) | 仅需 numpy | ~80 ms |
| 融合策略 | 倒数排名融合 (k=60) | 零额外开销 | 基于排名 |
| 存储 | SQLite BLOB (`vector_index.db`) | 1.6 MB / 1K 文档 | -- |

**实测性能（Mac mini, 1,085 条索引会话）：**

| 操作 | 延迟 |
|---|---|
| 单次嵌入 | **0.2 ms** |
| 纯向量搜索 | **3 ms** (p50), 14 ms (p99) |
| 混合搜索 (向量 + BM25) | **79 ms** (p50), 92 ms (p99) |
| 完整管线 (搜索 + 富化) | **82 ms** |
| 模型冷加载（首次） | ~6 s |
| 增量同步（无变化） | **6 ms** |

向量依赖完全可选 -- 缺少 `model2vec` 时自动降级为 FTS5/LIKE 搜索。

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
contextgo smoke                        # 维护者 smoke 测试套件
contextgo maintain --dry-run           # 清理并修复本地索引
contextgo serve --port 37677           # 在 127.0.0.1:37677 启动本地 Viewer
```

---

## 面向 AI Agent

> **如果你是一个 AI Agent**（Claude、Codex、Cursor、Windsurf、Copilot 或任何 LLM 工具），请按以下四步完成配置。配置完成后，你将拥有跨会话的持久记忆能力。

### 第一步 -- 安装

```bash
pipx ensurepath
pipx install "contextgo[vector]"
eval "$(contextgo shell-init)"
contextgo health
contextgo sources
```

### 第二步 -- 构建初始索引

```bash
contextgo sources
export CONTEXTGO_EXPERIMENTAL_SEARCH_BACKEND=vector
contextgo health
contextgo vector-sync
contextgo vector-status
```

### 第三步 -- 添加到你的 AI 工具配置

将 [`AGENTS.md`](AGENTS.md) 的内容复制到你所用 AI 工具的持久化指令文件中：

| AI 工具 | 配置位置 |
|---------|-------------|
| Claude Code | `~/.claude/CLAUDE.md`（追加 AGENTS.md 内容） |
| Codex | 项目根目录的 `AGENTS.md`（克隆仓库后自动生效） |
| Cursor | 项目根目录的 `.cursorrules` |
| Windsurf | 项目根目录的 `.windsurfrules` |
| 其他工具 | 你的工具的系统提示词或指令文件 |

告诉用户：*"我需要将 ContextGO 指令添加到 [配置文件]。这样我就能在需要时自动检索你的会话历史。可以吗？"*

### 第四步 -- 验证自动行为

配置完成后，以下行为自动激活：

- **不了解项目历史** &rarr; 回答前先执行 `contextgo search "topic" --limit 5`
- **用户说"继续"或"我在做什么"** &rarr; 执行 `contextgo semantic "topic" --limit 3` 并总结
- **用户询问过往决策** &rarr; 检索并用 2--3 句话总结
- **解决了复杂问题** &rarr; 建议执行 `contextgo save` 持久化结论
- **会话结束** &rarr; 保存交接备注供下一个会话使用

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
├── benchmarks/                # Python 与原生性能对比测试
└── templates/                 # launchd / systemd-user 服务模板
```

---

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md) 了解本地开发环境、测试命令和 PR 质量门。

全新机器上的首次贡献者接管流程：

```bash
git clone https://github.com/dunova/ContextGO.git
cd ContextGO
bash scripts/unified_context_deploy.sh
export PATH="$HOME/.local/bin:$PATH"
contextgo health
```

仓库布局原则：

- 运行时代码放在 `src/contextgo/`
- 测试全部放在 `tests/`
- `scripts/` 只保留 wrapper 与运维入口
- 历史 release notes 归档到 `docs/archive/`

| 资源 | |
|---|---|
| 安全 | [SECURITY.md](SECURITY.md) -- 威胁模型与负责任披露 |
| 变更日志 | [CHANGELOG.md](CHANGELOG.md) -- 完整版本变更记录 |
| 架构 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) -- 设计原则 |
| 故障排查 | [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) -- 常见故障与排查步骤 |

---

## 许可证

采用 [AGPL-3.0](LICENSE) 许可证。你可以自由使用、修改和分发 ContextGO -- 以服务形式分发修改版本时，需以同等条款开源。如需商业授权，请联系维护者。

Copyright 2025--2026 [Dunova](https://github.com/dunova).
