---
name: contextgo-recall
version: "0.9"
description: |
  Search and recall context from ContextGO local session index.
  Activates when the user asks about past work: "what did I do with X",
  "find that thing", "search history", "recall", "context for Y",
  or any question about previous sessions, decisions, or commands.
  Requires: contextgo CLI.
---

# ContextGO Recall
# ContextGO 上下文召回

Two search modes against the local session index.
两种检索模式，针对本地会话索引。

---

## Semantic Search / 语义搜索

Checks saved memory files first, then falls back to session history index.
Best for broad questions and topic exploration.

优先检查已保存记忆文件，再回退到会话历史索引。
适合宽泛问题和主题探索。

```bash
contextgo semantic "<natural language query>" --limit 5
```

---

## Keyword Search / 关键词搜索

Direct keyword query against indexed session transcripts.
Best for function names, file paths, error messages, CLI commands.

直接对会话索引执行关键词查询。
适合函数名、文件路径、错误信息、CLI 命令。

```bash
contextgo search "<keywords>" --limit 10
```

| Flag / 参数 | Effect / 效果 |
|---|---|
| `--type all\|event\|session\|turn\|content` | Filter by source type (default: all) / 按来源类型过滤 |
| `--limit N` | Max results (default: 10) / 最大结果数 |
| `--literal` | Exact phrase match, no tokenization / 精确短语匹配 |

---

## Search Strategy / 检索策略

1. Start with `semantic` for broad context / 先用 `semantic` 获取宽泛上下文
2. If too noisy, switch to `search` with specific keywords / 结果过多时换 `search` 加精确词
3. If exact match needed, add `--literal` / 需要精确匹配时加 `--literal`
4. For high-speed scan on large indexes / 大索引高速扫描：
   ```bash
   contextgo native-scan --query "<keyword>" --json
   ```

---

## Output Rules / 输出规则

- Summarize in 2–3 sentences; include session date and key finding / 2–3 句总结，含会话日期与核心发现
- Never paste raw JSON unless the user explicitly asks / 除非用户明确要求，否则禁止粘贴原始 JSON
- If no results: suggest `contextgo health` to verify index status / 无结果时建议运行 `contextgo health` 检查索引
