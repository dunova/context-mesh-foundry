# ContextGO Documentation / 文档索引

> Local-first context & memory engine for multi-agent AI coding teams.
> 为多 Agent AI 编码团队打造的本地优先上下文与记忆引擎。

---

## Pages / 文档页面

| Document | Description / 说明 |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, component layers, data flow / 系统设计、组件层次、数据流 |
| [CONFIGURATION.md](CONFIGURATION.md) | All environment variables with defaults / 所有环境变量及默认值 |
| [API.md](API.md) | Local viewer HTTP API reference / 本地 viewer HTTP API 参考 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Common failure modes and fixes / 常见故障与排查步骤 |
| [FAQ.md](FAQ.md) | Frequently asked questions / 常见问题解答 |
| [MIGRATION.md](MIGRATION.md) | Version migration guide (0.10 → 0.11) / 版本迁移指南 |
| [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) | Pre-release validation steps / 发布前验证步骤 |

---

## Quick links / 快速链接

- **Install:** `pipx install "contextgo[vector]"` then `eval "$(contextgo shell-init)"`
- **Health check:** `contextgo health`
- **Hybrid search setup:** `export CONTEXTGO_EXPERIMENTAL_SEARCH_BACKEND=vector && contextgo vector-sync`
- **AI agent setup:** Copy [AGENTS.md](../AGENTS.md) into your tool's instruction file

安装：`pipx install "contextgo[vector]"` 然后 `eval "$(contextgo shell-init)"`
健康检查：`contextgo health`
向量搜索：`export CONTEXTGO_EXPERIMENTAL_SEARCH_BACKEND=vector && contextgo vector-sync`
AI Agent 配置：将 [AGENTS.md](../AGENTS.md) 内容复制到你的工具指令文件中

---

## Release notes / 发布说明

| Version | File |
|---|---|
| 0.11.5 | [CHANGELOG.md](../CHANGELOG.md) |
| 0.11.4 | [RELEASE_NOTES_0.11.4.md](RELEASE_NOTES_0.11.4.md) |
| 0.11.3 | [RELEASE_NOTES_0.11.3.md](RELEASE_NOTES_0.11.3.md) |
| 0.11.2 | [RELEASE_NOTES_0.11.2.md](RELEASE_NOTES_0.11.2.md) |
| 0.11.1 | [RELEASE_NOTES_0.11.1.md](RELEASE_NOTES_0.11.1.md) |
| 0.11.0 | [RELEASE_NOTES_0.11.0.md](RELEASE_NOTES_0.11.0.md) |
| 0.10.0 | [RELEASE_NOTES_0.10.0.md](RELEASE_NOTES_0.10.0.md) |
| Older | [archive/](archive/) |

---

Copyright 2025-2026 [Dunova](https://github.com/dunova). AGPL-3.0.
