---
name: contextgo-recall
version: "0.9"
description: |
  Search and recall context from ContextGO local session index.
  Activates when the user asks about past work: "what did I do with X",
  "find that thing", "search history", "recall", "context for Y",
  or any question about previous coding sessions, decisions, commands.
  Requires: contextgo CLI.
---

# ContextGO Recall

Two search modes against the local session index.

## Semantic Search

Checks local memory files first (saved conclusions), then falls back to
session history FTS5 content search.

```bash
contextgo semantic "<natural language query>" --limit 5
```

Use for: broad questions, topic exploration, "what was the plan for X".

## Keyword Search

Direct FTS5 query against indexed session transcripts.

```bash
contextgo search "<keywords>" --limit 10
```

Flags:
- `--type all|event|session|turn|content` -- filter by source type (default: all)
- `--limit N` -- max results (default: 10)
- `--literal` -- exact phrase match, no tokenization

Use for: specific function names, file paths, error messages, CLI commands.

## Search Strategy

1. Start with `semantic` for broad context
2. If too noisy, switch to `search` with specific keywords
3. If exact match needed, add `--literal`
4. For native-backend speed on large indexes: `contextgo native-scan --query "X" --json`

## Output Handling

- Summarize results for the user in 2-3 sentences
- Include the session date and key finding
- If no results: suggest `contextgo health` to verify index status
- Never paste raw JSON output unless the user asks for it
