# Native components

High-performance session scanners that accelerate the hot path of ContextGO's
session search.  The Python layer remains responsible for the CLI, deployment,
and compatibility surface; these binaries handle file I/O and text matching.

## Components

### session_scan (Rust)

Parallel `.json` / `.jsonl` session scanner built with Rayon.  Recommended for
throughput-sensitive workloads where the file count is large.

- Source: `session_scan/`
- Output: structured JSON consumed by `scripts/context_cli.py native-scan`
- License: MIT OR Apache-2.0

### session_scan_go (Go)

Single-binary session scanner with no external dependencies.  Easier to deploy
on systems without a Rust toolchain.

- Source: `session_scan_go/`
- Output: same JSON schema as the Rust scanner
- License: MIT

## Build

### Rust

```bash
cd native/session_scan
cargo build --release
# binary: target/release/session_scan
```

On file systems with locking restrictions:

```bash
CARGO_TARGET_DIR=/tmp/session_scan_target cargo build --release
```

### Go

```bash
cd native/session_scan_go
go build -o session_scan_go .
# binary: ./session_scan_go
```

## Quick start

```bash
# Rust
./native/session_scan/target/release/session_scan \
  --query "agent" --limit 20 --json

# Go
./native/session_scan_go/session_scan_go \
  --query "agent" --limit 20 --json
```

Both binaries accept the same flags:

| Flag | Default | Description |
|---|---|---|
| `--codex-root` | `~/.codex/sessions` | Codex session directory |
| `--claude-root` | `~/.claude/projects` | Claude session directory |
| `--threads` | `4` | Parallel worker count |
| `--query` | _(empty)_ | Substring filter; empty returns all sessions |
| `--limit` | `20` | Maximum results |
| `--json` | `false` | Emit JSON |

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
        "path": "...",
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

The Go binary omits `aggregates` / `duration_ms` at the top level; those are
present inside the `ScanOutput` struct methods and accessible via
`payload.Aggregates()` when used as a library.

## Environment variables

| Variable | Description |
|---|---|
| `CONTEXTGO_ACTIVE_WORKDIR` | Override the active working directory used to skip the current session from results. |

## Tests

```bash
# Rust
cd native/session_scan
CARGO_INCREMENTAL=0 cargo test

# Go
cd native/session_scan_go
go test ./...
```

## Performance notes

- Both scanners allocate a buffered reader per file and bound the per-line
  buffer to 32 MB, preventing unbounded memory growth on large sessions.
- Setting `--threads` above the physical core count rarely helps for
  I/O-bound workloads.
- The noise filter operates on already-lowercased strings to avoid redundant
  allocations in the matching hot path.
- Rayon (Rust) uses work-stealing; Go uses a fixed goroutine pool with a
  buffered channel — both approaches keep all workers busy without a global lock.

## Extending

To add a new session source directory, append a `SourceRoot` (Rust) or
`WorkItem` (Go) entry to the scanner initialisation in `main`.  Aggregation
and JSON output pick up the new source automatically.
