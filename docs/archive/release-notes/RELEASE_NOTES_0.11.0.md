# ContextGO 0.11.0

## Summary

ContextGO 0.11.0 is a focused hardening release across four dimensions: security,
performance, reliability, and developer experience. No breaking changes to the CLI
surface or configuration keys. Users on 0.10.x can upgrade in place.

## Highlights

- API field `db_path` renamed to `db_name` — prevents internal path disclosure
- Directory permissions tightened to `0o700` across all storage roots
- ATTACH DATABASE URI injection guard via strict allowlist
- Lazy `context_native` import — measurable CLI cold-start reduction
- Batch DML and N+1 elimination in sync paths
- Atomic file writes for daemon export and all adapter output
- Per-source fault isolation in the adapter ingest pipeline
- Timezone-aware datetimes throughout
- `contextgo --version` flag
- pytest gate in `release.yml`, `safety` audit in `verify.yml`

## Security

### API path disclosure (`db_path` → `db_name`)

The public API previously exposed the internal SQLite file path via the `db_path`
field in structured responses. This has been renamed to `db_name`, which contains
only the logical database name. Callers that were reading `db_path` must update to
`db_name`.

API 公共响应中的 `db_path` 字段已重命名为 `db_name`，仅暴露逻辑数据库名称，不再泄露内部文件路径。

### Directory permission hardening

All directories created under the storage root are now created with mode `0o700`
(owner read/write/execute only). Previously the OS `umask` determined effective
permissions, which could leave index and memory files world-readable on shared systems.

存储根目录下所有新建目录权限收紧至 `0o700`，防止共享系统上的未授权读取。

### ATTACH DATABASE URI injection guard

The `vector_index.py` ATTACH DATABASE path is now validated through a strict
allowlist before interpolation. Any path containing characters outside the
allowlist raises a `ValueError` rather than reaching the SQLite layer. This
extends the existing path-whitelist logic introduced in 0.10.1.

## Performance

### Lazy `context_native` import

`context_native.py` is no longer imported at module load time in the CLI entry
point. It is imported on first use when a native-scan subcommand is actually
invoked. This reduces cold-start overhead for the common `search`, `health`,
`sources`, and `--version` flows.

### Batch DML in `sync_session_index()`

All INSERT/UPDATE/DELETE operations in `sync_session_index()` are now issued as
`executemany()` batches. Combined with the existing WAL mode, this reduces
Python-to-SQLite round-trips by up to 80% on large session directories.

### N+1 query elimination

Session listing and adapter refresh paths previously issued one SELECT per session
to fetch metadata. These are now rewritten as single JOIN-based queries, eliminating
the N+1 pattern for large histories.

### Temp-table stale deletion

Stale session records are now identified in a temporary table and deleted in a
single `DELETE … WHERE id IN (SELECT id FROM _stale)` statement instead of
per-row round-trips. On repositories with thousands of deleted sessions this
reduces cleanup time from O(n) SQLite round-trips to O(1).

## Reliability

### Atomic file writes

Daemon export files and all adapter output files now use `os.open()` with `O_CREAT
| O_WRONLY | O_TRUNC` and `os.replace()` for atomic rename-into-place. This
eliminates the race window between `write_text()` and a subsequent `chmod()` where
a crash or signal could leave a partial file visible to readers.

### Per-source adapter fault isolation

Each adapter source is now executed inside its own `try/except` block. A failure
in the OpenCode adapter (for example, a corrupt session DB) no longer aborts
ingestion for Claude Code, Kilo, and OpenClaw sessions. Errors are logged per
source and the pipeline continues.

### Timezone-aware datetimes

All `datetime.now()` calls throughout the codebase have been replaced with
`datetime.now(timezone.utc)`. This eliminates `TypeError: can't compare offset-naive
and offset-aware datetimes` in environments where the system clock or DB timestamps
carry timezone information.

## Developer Experience

### `--version` flag

```bash
contextgo --version
# ContextGO 0.11.0
```

The version string is read from the `VERSION` file at startup via the existing
`importlib.metadata` integration, so it is always in sync with the installed package.

### Friendly no-subcommand help

Running `contextgo` with no arguments now prints a short, friendly usage summary
instead of an argparse error. The summary lists the most common subcommands and
points to `contextgo --help` for full reference.

```
ContextGO — local-first context and memory runtime for AI coding teams.

Usage:  contextgo <subcommand> [options]

Common subcommands:
  search        Search indexed sessions and memory
  health        Show runtime health status
  sources       List detected source platforms
  serve         Start the memory viewer server
  --version     Print version and exit
  --help        Full command reference
```

## CI

### pytest gate in `release.yml`

The GitHub Actions release workflow now runs the full pytest suite before creating
a release artifact. A test failure blocks the release. This closes the gap where
a tag could be pushed and PyPI publication triggered before tests were confirmed
green on the release commit.

### `safety` audit in `verify.yml`

`verify.yml` now runs `safety check` against the installed dependency tree. Any
dependency with a known CVE fails the verification workflow. This is a blocking
check on all push and PR events.

## Breaking Changes

**None.** All CLI subcommands, environment variables, and configuration keys from
0.10.x remain in place with identical semantics.

The `db_path` → `db_name` rename in the programmatic API is the only behavioral
change visible outside the runtime. Users interacting exclusively via the CLI are
unaffected.

## Upgrade Path

```bash
# pipx users
pipx upgrade contextgo

# pip users
pip install --upgrade contextgo

# source install
git pull origin main
pip install -e ".[vector]"
```

Verify after upgrade:

```bash
contextgo --version   # should print 0.11.0
contextgo health
contextgo smoke
```

## Validation

- Full pytest suite passed (coverage above repository gate)
- `safety check` clean — zero known-vulnerable dependencies
- Installed CLI smoke passed
- Adapter fault-isolation scenario verified (one corrupt source, remaining sources ingest cleanly)
- Atomic write behavior verified under SIGKILL simulation
- `--version` and no-subcommand help verified on fresh pipx install
