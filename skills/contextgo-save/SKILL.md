---
name: contextgo-save
version: "0.9"
description: |
  Persist key conclusions, decisions, and lessons to ContextGO local memory.
  Activates when the user solves a hard problem, makes an architectural
  decision, finishes a task, or says "save this", "remember this",
  "note this down", "wrap up". Also activates proactively when you observe
  a significant decision that should survive across sessions.
  Requires: contextgo CLI.
---

# ContextGO Save

Write durable memory entries that persist across sessions and are
searchable via `contextgo semantic` and `contextgo search`.

## Command

```bash
contextgo save \
  --title "Concise title (what was decided or learned)" \
  --content "Full context: rationale, file paths, commands, gotchas, next steps" \
  --tags "project,topic,category"
```

Saved as Markdown files under `~/.contextgo/resources/shared/conversations/`.
Indexed automatically by the daemon for future recall.

## When to Save

| Situation | Example Title |
|-----------|--------------|
| Architectural decision | "Chose SQLite FTS5 over Elasticsearch for session index" |
| Bug root cause found | "OOM in daemon caused by unbounded cursor dict" |
| Config/env choice | "CONTEXTGO_POLL_INTERVAL_SEC=180 optimal for battery life" |
| Warning for future self | "Do not use mmap on NFS-mounted storage roots" |
| Session handoff | "Auth refactor: middleware done, token refresh next" |

## When NOT to Save

- Routine file edits (daemon captures these automatically)
- Temporary debug steps that failed
- Information the user explicitly stated they already know
- Anything containing secrets, tokens, or credentials

## Tag Conventions

Use lowercase, hyphenated tags:
- Project: `contextgo`, `my-app`, `infra`
- Topic: `auth`, `performance`, `ci-cd`
- Type: `decision`, `bug-fix`, `config`, `handoff`

## Proactive Behavior

After solving a non-trivial problem, suggest saving:
"This was a significant fix. Want me to save it for future reference?"

At session end, if decisions were made:
"You made 2 architectural decisions this session. Saving them for continuity."

## Export for Sharing

To share saved context with another agent or machine:

```bash
contextgo export "" /tmp/memories.json --limit 500
contextgo import /tmp/memories.json  # on the receiving end
```
