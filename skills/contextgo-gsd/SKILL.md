---
name: contextgo-gsd
version: "0.9"
description: |
  ContextGO GSD (Get Stuff Done) workflow. Activates when the user starts a
  coding session, says "GSD", "continue", "pick up where I left off", "what
  was I doing", or any task that benefits from cross-session continuity.
  Also activates on session wrap-up: "done", "wrap up", "save progress".
  Requires: contextgo CLI (pip install contextgo or python3 scripts/context_cli.py).
---

# ContextGO GSD Workflow

GSD is a three-phase loop that runs across every coding session.
ContextGO provides the memory layer -- a local SQLite FTS5 index of all
your Codex, Claude, and shell sessions. No cloud. No MCP. Just CLI.

## Phase 1 -- Recall

Run at session start or when switching tasks.

```bash
# Broad recall: what was I working on?
contextgo semantic "<current task or project name>" --limit 3

# Targeted recall: find a specific decision or fix
contextgo search "<keyword>" --limit 5

# Exact match: find an error message or log line
contextgo search "<exact phrase>" --limit 5 --literal
```

`semantic` checks local memory files first (saved conclusions), then falls
back to session history FTS5 search. `search` goes directly to the index.

Read the output. Synthesize it into a 2-3 sentence status for the user:
"You were working on X. Last session you decided Y. Next step was Z."

Do NOT paste raw CLI output. Summarize.

## Phase 2 -- Execute

Work normally. The ContextGO daemon captures session transcripts, shell
commands, and file changes in the background. No action needed from you.

During work, use recall whenever you hit a question that might have been
answered before:

```bash
contextgo search "that auth bug" --limit 3
```

## Phase 3 -- Persist

When a milestone is reached, a hard problem is solved, or the session ends:

```bash
contextgo save \
  --title "Brief title of what was decided or learned" \
  --content "Full explanation. Include file paths, rationale, gotchas, next steps." \
  --tags "project,topic,type"
```

### What to persist

- Architectural decisions with rationale
- Bug root causes and the actual fix
- "Do not do X because Y" warnings
- Environment/config choices
- Cross-session handoff notes: what to do next

### What NOT to persist

- Routine edits (the daemon already indexes these)
- Temporary debug attempts
- Obvious context the user already knows

## Health Check

If recall returns empty or stale results:

```bash
contextgo health --verbose
```

This shows index size, source freshness, native backend status, and daemon
state. Use it to diagnose why context might be missing.

## Cross-Tool Handoff

Export context for another agent or team member:

```bash
contextgo export "<query>" /tmp/handoff.json --limit 100
```

Import on the receiving side:

```bash
contextgo import /tmp/handoff.json
```

## Install

```bash
pip install contextgo
# or from source:
git clone https://github.com/dunova/ContextGO && cd ContextGO && pip install -e .
```

Verify: `contextgo health`
