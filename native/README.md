# Native session scanners

High-performance binaries that accelerate the hot path of ContextGO session
search.  The Python layer remains responsible for CLI orchestration, deployment,
and compatibility; these binaries handle parallel file I/O and text matching.

Two implementations are provided with identical command-line interfaces and JSON
output schemas.  Choose based on the toolchain available on your target system.

| Implementation | Language | Parallelism | External deps |
|---|---|---|---|
| `session_scan` | Rust | Rayon work-stealing | `cargo` required to build |
| `session_scan_go` | Go | Fixed goroutine pool | None (single static binary) |

---

## Components

### session_scan (Rust)

Parallel `.json` / `.jsonl` session scanner built with Rayon.  Recommended for
throughput-sensitive workloads on systems with a Rust toolchain.

- Source: `session_scan/`
- Binary output: `session_scan/target/release/session_scan`
- License: MIT OR Apache-2.0

### session_scan_go (Go)

Single-binary session scanner with zero external runtime dependencies.  Easier
to deploy on systems without a Rust toolchain; the binary is fully self-contained.

- Source: `session_scan_go/`
- Binary output: `session_scan_go/session_scan_go`
- License: MIT

---

## Build

### Rust

Requirements: Rust stable toolchain (1.70+), available at <https://rustup.rs>.

```bash
cd native/session_scan
cargo build --release
# Binary: target/release/session_scan
```

On file systems with locking restrictions (network mounts, some CI environments):

```bash
CARGO_TARGET_DIR=/tmp/session_scan_target cargo build --release
```

The release profile is pre-configured with `lto = true` and `codegen-units = 1`
for maximum optimisation at the cost of longer compile times.

### Go

Requirements: Go 1.22+, available at <https://go.dev/dl>.

```bash
cd native/session_scan_go
go build -o session_scan_go .
# Binary: ./session_scan_go
```

To produce a statically linked binary suitable for Alpine Linux or scratch
containers:

```bash
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o session_scan_go .
```

---

## Quick start

```bash
# Rust
./native/session_scan/target/release/session_scan \
  --query "agent" --limit 20 --json

# Go
./native/session_scan_go/session_scan_go \
  --query "agent" --limit 20 --json
```

Both binaries accept identical flags:

| Flag | Default | Description |
|---|---|---|
| `--codex-root` | `~/.codex/sessions` | Root directory for Codex session files |
| `--claude-root` | `~/.claude/projects` | Root directory for Claude session files |
| `--threads` | `4` | Parallel worker count |
| `--query` | _(empty)_ | Substring filter; empty returns all sessions |
| `--limit` | `20` | Maximum results returned |
| `--json` | `false` | Emit machine-readable JSON instead of a human summary |

When `--query` is empty all discovered sessions are returned (up to `--limit`).

---

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
        "path": "/home/user/.codex/sessions/abc123.jsonl",
        "first_timestamp": "2025-03-24T12:00:00Z",
        "last_timestamp": "2025-03-24T12:05:00Z",
        "snippet": "...agent executed the plan...",
        "match_field": "message.content.text"
      }
    }
  ],
  "matches": [],
  "errors": []
}
```

`aggregates` lists per-source statistics in root-encounter order.
`matches` contains the full summary records sorted by score (descending), then
by last/first timestamp (descending).
`errors` collects non-fatal parse failures as strings; the scan continues even
when individual files cannot be parsed.

The Go binary omits `duration_ms` at the top level; timing is available via
`time.Since(start)` in `main.go` and can be added to the output if needed.

---

## Environment variables

| Variable | Description |
|---|---|
| `CONTEXTGO_ACTIVE_WORKDIR` | Canonical path of the active working directory. Sessions whose recorded `cwd` matches this value are excluded from results, preventing the current session from polluting search output. When unset, the process working directory (`$PWD`) is used. |

---

## Tests

### Rust

```bash
cd native/session_scan

# Run all unit tests
CARGO_INCREMENTAL=0 cargo test

# Run with output visible
CARGO_INCREMENTAL=0 cargo test -- --nocapture

# Lint (must be clean before commit)
cargo clippy -- -D warnings
```

`CARGO_INCREMENTAL=0` prevents stale incremental artefacts on network file systems.

### Go

```bash
cd native/session_scan_go

# Run all tests
go test ./...

# Run with verbose output
go test -v ./...

# Lint (must be clean before commit)
go vet ./...
```

---

## Benchmarks

### Rust

```bash
cd native/session_scan

# Run built-in test benchmarks (if any)
cargo bench

# Time a real scan against your session directories
time ./target/release/session_scan --query "agent" --limit 100 --json > /dev/null
```

### Go

```bash
cd native/session_scan_go

# Run benchmarks with memory allocation statistics
go test -bench=. -benchmem ./...

# Increase iteration count for stable numbers
go test -bench=. -benchmem -benchtime=5s ./...

# Profile CPU usage
go test -bench=BenchmarkProcessFile -cpuprofile=cpu.prof ./...
go tool pprof cpu.prof
```

The `-benchmem` flag is recommended for all Go benchmark runs: it reports
allocations per operation (`allocs/op`) and bytes allocated per operation
(`B/op`), which are the primary tuning targets for the hot path.

---

## Performance notes

- Both scanners allocate a buffered reader per file and bound the per-line
  buffer to 32 MB, preventing unbounded memory growth on large session files.
- Setting `--threads` above the physical core count rarely helps for I/O-bound
  workloads.  For NVMe-backed storage, 4–8 threads is typically optimal.
- The noise filter operates on already-lowercased strings to avoid redundant
  allocations in the matching hot path.
- Rayon (Rust) uses work-stealing to keep all workers busy without a global
  lock.  Go uses a fixed goroutine pool fed by a buffered channel — both
  approaches are lock-contention-free under typical workloads.
- The Rust release profile enables `lto = true` and `codegen-units = 1`, giving
  the compiler maximum visibility for inlining across the Rayon boundary.
- The three hot-path functions `is_noise_line`, `should_skip_meta_text`, and
  `clip_snippet` are annotated with `#[inline]` to encourage inlining into the
  per-file processing loop.

---

## Noise filter synchronisation

Both binaries share the same noise marker list, which is the authoritative
source of truth defined in `config/noise_markers.json` (project root).  After
updating that file, run:

```bash
python3 scripts/check_noise_sync.py
```

This verifies that the Rust (`src/main.rs`) and Go (`scanner.go`) copies are
in sync with the Python backend and each other.

---

## Deployment

### System-wide installation

```bash
# Rust
install -m 755 native/session_scan/target/release/session_scan \
  /usr/local/bin/session_scan

# Go
install -m 755 native/session_scan_go/session_scan_go \
  /usr/local/bin/session_scan_go
```

### ContextGO Python integration

The Python layer selects the binary automatically via `scripts/context_native.py`.
The binary is invoked as a subprocess; its stdout JSON is parsed and merged into
the session search results.  No additional configuration is required beyond
placing the binary on `PATH` or in the standard build output location.

To force a specific backend:

```bash
CONTEXTGO_NATIVE_BACKEND=rust  python3 scripts/context_cli.py native-scan --query "agent"
CONTEXTGO_NATIVE_BACKEND=go    python3 scripts/context_cli.py native-scan --query "agent"
```

---

## Extending

To add a new session source directory, append a `SourceRoot` (Rust) or
`WorkItem` (Go) entry to the scanner initialisation in `main`.  Aggregation
and JSON output pick up the new source automatically without further changes.
