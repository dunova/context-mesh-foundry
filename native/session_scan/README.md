# session_scan

High-performance scanner for Codex/Claude session files written in Rust.

Walks `.json` and `.jsonl` session files in parallel (Rayon), extracts structured
metadata (timestamps, session IDs, text snippets), suppresses noise, and emits
either a human-readable summary or a machine-readable JSON report consumed by the
Python layer of ContextGO.

## Features

- Parallel file traversal with configurable thread count
- Substring query matching with centred snippet extraction
- Noise filter: known markers, line prefixes, and heuristic patterns
- Skips sessions whose working directory matches the caller's active directory
- Clean JSON output aligned with the Python consumer's expected schema

## Build

```bash
cd native/session_scan
cargo build --release
```

On file systems with locking restrictions (network mounts, some CI environments):

```bash
CARGO_TARGET_DIR=/tmp/session_scan_target cargo build --release
```

The release binary is written to `target/release/session_scan`.

## Run

```bash
./target/release/session_scan \
  --codex-root ~/.codex/sessions \
  --claude-root ~/.claude/projects \
  --threads 4 \
  --query "agent" \
  --limit 50 \
  --json
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `--codex-root` | `~/.codex/sessions` | Root directory for Codex session files |
| `--claude-root` | `~/.claude/projects` | Root directory for Claude session files |
| `--threads` | `4` | Rayon worker thread count |
| `--query` | _(empty)_ | Substring filter; empty returns all sessions |
| `--limit` | `20` | Maximum results returned |
| `--json` | `false` | Emit JSON instead of a human summary |

When `--query` is empty all discovered sessions are returned (up to `--limit`).

## JSON output schema

```json
{
  "files_scanned": 42,
  "query": "agent",
  "duration_ms": 1530,
  "aggregates": [
    {
      "label": "codex_session",
      "session_count": 4,
      "total_lines": 320,
      "total_bytes": 102400,
      "sample": {
        "session_id": "abc123",
        "path": "/home/.codex/sessions/abc123.jsonl",
        "first_timestamp": "2025-03-24T12:00:00Z",
        "last_timestamp": "2025-03-24T12:05:00Z",
        "snippet": "...",
        "match_field": "message.content.text"
      }
    }
  ],
  "matches": [ ],
  "errors": []
}
```

`aggregates` lists per-source statistics in root-encounter order.
`matches` contains the full `SerializableSummary` records sorted by score
(descending), then by last/first timestamp (descending).
`errors` collects non-fatal parse failures as strings.

## Environment variables

| Variable | Description |
|---|---|
| `CONTEXTGO_ACTIVE_WORKDIR` | Override the active working directory used to skip the current session. Useful when the caller cannot rely on `$PWD`. |

## Tests

```bash
cd native/session_scan
CARGO_INCREMENTAL=0 cargo test
```

`CARGO_INCREMENTAL=0` avoids lock-file issues on network file systems.

## Performance notes

- The scanner allocates one `BufReader` per file and processes lines
  sequentially within each worker, keeping memory usage proportional to the
  largest single line (capped at 32 MB by the bufio buffer).
- Rayon distributes files across workers dynamically; setting `--threads`
  above the number of physical cores rarely helps for I/O-bound workloads.
- Noise filtering operates on already-lower-cased strings to avoid redundant
  allocations in the hot path.

## Extending

To add a new session source, push an additional `SourceRoot` entry in
`Scanner::from_args`.  The aggregation and JSON report logic pick it up
automatically.
