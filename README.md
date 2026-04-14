<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/media/logo-dark.svg">
    <img src="docs/media/logo.svg" alt="ContextGO" width="360">
  </picture>
</p>

<p align="center">
  <strong>Local-first context &amp; memory engine for multi-agent AI coding teams.</strong>
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
  <a href="#quick-start">Quick Start</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="#hybrid-semantic-search">Hybrid Search</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="#for-ai-agents">AI Agent Setup</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="docs/">Docs</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="README.zh.md">中文</a>
</p>

---

> **Your AI agent starts from zero every conversation. It forgets what was decided yesterday, why that approach was abandoned, and what the team already tried.**
>
> ContextGO fixes this. It indexes every Codex, Claude, and shell session locally — no Docker,
> no MCP broker, no external vector database, no cloud dependency. Install in one line with
> `pipx install contextgo`. The next `contextgo search` query returns results across your entire
> coding history, including sessions from weeks ago, across all your AI tools at once.
>
> Hybrid semantic search (model2vec + BM25). Native Rust/Go scanning for speed. Persistent
> cross-session memory that any AI coding agent can query without any integration code.

---

## Quick Start

```bash
# 1. Install
pipx install "contextgo[vector]"
eval "$(contextgo shell-init)"

# 2. Initialize index
contextgo health
contextgo sources

# 3. Verify
contextgo search "authentication" --limit 5
```

> **Note:** Use `pipx` rather than `pip install` — required on macOS (Homebrew Python 3.12+)
> and most Linux distros due to [PEP 668](https://peps.python.org/pep-0668/).
> Install pipx: `brew install pipx` (macOS) or `apt install pipx` (Debian/Ubuntu).

ContextGO auto-discovers all supported local sources with no configuration:
`Codex` · `Claude Code` · `Cursor` · `Accio Work` · `Gemini/Antigravity` · `OpenCode` · `Kilo` · `OpenClaw` · `zsh/bash shell history`

**Enable hybrid search after you have existing history:**

```bash
export CONTEXTGO_EXPERIMENTAL_SEARCH_BACKEND=vector
contextgo vector-sync
contextgo vector-status
```

<details>
<summary><strong>Source install for contributors</strong></summary>

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

## Why ContextGO

| Capability | ContextGO | Cursor Context | Continue.dev | Mem0 |
|---|:---:|:---:|:---:|:---:|
| Local-first by default | **Yes** | Partial | Partial | No |
| Docker-free | **Yes** | Yes | Partial | No |
| Multi-agent session index | **Yes** | No | No | Partial |
| Cross-tool history (Codex + Claude + shell) | **Yes** | No | No | No |
| Cross-tool history (Codex + Claude + Accio + Gemini) | **Yes** | No | No | No |
| Hybrid semantic search | **Yes** | No | No | Partial |
| Native Rust/Go scan | **Yes** | No | No | No |
| MCP-free by default | **Yes** | Yes | No | No |
| Built-in delivery validation | **Yes** | No | No | No |
| CJK / Unicode full support | **Yes** | Partial | No | No |
| One-line install, zero config | **Yes** | No | No | No |

**Key numbers:** 2,183 tests &nbsp;|&nbsp; 97.1% coverage &nbsp;|&nbsp; Python 3.10+ &nbsp;|&nbsp; Hybrid search &lt; 5 ms (warm) &nbsp;|&nbsp; 8 AI tools + shell

---

## Hybrid Semantic Search

ContextGO includes an optional hybrid search engine combining **vector similarity** and **BM25 keyword scoring** via Reciprocal Rank Fusion (RRF).

| Component | Technology | Size | Latency |
|---|---|---|---|
| Vector embeddings | [model2vec](https://github.com/MinishLab/model2vec) (potion-base-8M) | 30 MB model | 0.2 ms/query |
| Keyword scoring | [bm25s](https://github.com/xhluca/bm25s) | numpy only | ~80 ms |
| Fusion | Reciprocal Rank Fusion (k=60) | zero overhead | rank-based |
| Storage | SQLite BLOB (`vector_index.db`) | 1.6 MB / 1K docs | — |

**Benchmarks (Mac mini, 1,085 indexed sessions):**

| Operation | Latency |
|---|---|
| Single embedding | **0.2 ms** |
| Pure vector search | **3 ms** (p50), 14 ms (p99) |
| Hybrid search (vector + BM25) | **79 ms** (p50), 92 ms (p99) |
| Full pipeline (search + enrich) | **82 ms** |
| Model cold load (first run) | ~6 s |
| Incremental sync (no changes) | **6 ms** |

All vector dependencies are optional — ContextGO degrades gracefully to FTS5/LIKE search when `model2vec` is absent.

---

## Architecture

```mermaid
flowchart LR
    subgraph Sources
        A1[Codex]
        A2[Claude]
        A3[Accio]
        A4[Gemini]
        A5[OpenCode]
        A6[Kilo]
        A7[OpenClaw]
        A8[Shell]
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
contextgo q "auth"                                # quick recall — search or session ID lookup
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
contextgo health                     # verify installation and storage integrity
contextgo maintain --enqueue-missing # index all existing sessions
contextgo maintain --dry-run         # clean and repair local index
contextgo serve --port 37677         # start local viewer at 127.0.0.1:37677
contextgo smoke                      # maintainer smoke suite
```

### Daemon Mode

```bash
contextgo daemon start
contextgo daemon status
contextgo daemon stop
```

### Shell Integration

```bash
eval "$(contextgo shell-init)"       # activate cg alias + shell aliases
```

For tab completion setup (bash/zsh/fish), see [docs/SHELL_COMPLETION.md](docs/SHELL_COMPLETION.md).

---

## For AI Agents

> **If you are an AI agent** (Claude, Codex, Cursor, Windsurf, Copilot, or any LLM tool), follow the steps below. After setup, you will have persistent cross-session memory across all your conversations.

### Step 1 — Install

```bash
pipx ensurepath
pipx install "contextgo[vector]"
eval "$(contextgo shell-init)"
contextgo health
contextgo sources
```

### Step 2 — Build the initial index

```bash
export CONTEXTGO_EXPERIMENTAL_SEARCH_BACKEND=vector
contextgo vector-sync
contextgo vector-status
```

### Step 3 — Add to your AI tool's instructions

Copy the contents of [`AGENTS.md`](AGENTS.md) into your tool's persistent instructions:

| AI Tool | Where to add |
|---------|-------------|
| Claude Code | `~/.claude/CLAUDE.md` (append AGENTS.md content) |
| Codex | `AGENTS.md` in project root (already works if repo is cloned) |
| Cursor | `.cursorrules` in project root |
| Windsurf | `.windsurfrules` in project root |
| Other | Your tool's system prompt or instruction file |

### Step 4 — Verify automatic behaviors

After setup, these behaviors activate automatically:

- **Cold start / new window** → recall only once, then stay quiet unless the topic changes
- **User says "continue" or "what was I doing"** → `contextgo semantic "topic" --limit 3` and summarize
- **User asks about past decisions** → search and summarize in 2–3 sentences
- **Structural question (architecture, call graph, blast radius)** → prefer code graph first, then ContextGO for historical decisions
- **Same-topic follow-up** → skip recall to reduce token cost
- **Hard problem solved** → suggest `contextgo save` to persist the conclusion

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
└── docs/                      # Architecture, config, benchmarks, templates
```

---

## Contributing

See [CONTRIBUTING.md](.github/CONTRIBUTING.md) for local dev setup, test commands, and PR quality gates.

```bash
git clone https://github.com/dunova/ContextGO.git
cd ContextGO
bash scripts/unified_context_deploy.sh
export PATH="$HOME/.local/bin:$PATH"
contextgo health
```

| Resource | |
|---|---|
| Security | [SECURITY.md](.github/SECURITY.md) — threat model and responsible disclosure |
| Changelog | [CHANGELOG.md](.github/CHANGELOG.md) — full version history |
| Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — design principles |
| Troubleshooting | [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — common failure modes |

---

## License

Licensed under [AGPL-3.0](LICENSE). You may use, modify, and distribute ContextGO freely — any modifications distributed as a service must also be open-sourced under AGPL-3.0. Commercial licensing available; contact the maintainers.

Copyright 2025–2026 [Dunova](https://github.com/dunova).
