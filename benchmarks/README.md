# Benchmarks

A reproducible Python benchmark suite for measuring the performance of the ContextGO core paths:

- `context_cli health` (includes session index sync)
- `context_cli search` (exact search)
- `session_index.sync_session_index` (forced full index rebuild)

The harness creates temporary sample data (`~/.codex`, `~/.claude`, `~/.zsh_history`, etc.) in an isolated `HOME` environment so it does not depend on the actual user directory.

## Usage

```bash
python3 -m benchmarks [--mode python|native|both] [--format text|json]
# or equivalently:
python3 -m benchmarks.run [--mode python|native|both] [--format text|json]
```

Optional flags:

| Flag | Default | Description |
|---|---|---|
| `--mode` | `python` | Backend to benchmark: `python`, `native`, or `both`. |
| `--format` | `text` | Output format: `text` (human-readable summary) or `json`. |
| `--iterations` | `3` | Number of timed iterations per benchmark. |
| `--warmup` | `1` | Number of warmup iterations (not counted in results). |
| `--query` | `benchmark` | Search query string used in the search benchmark. |
| `--search-limit` | `5` | Result limit used in the search benchmark. |

All flags can also be set via environment variables with the `CONTEXTGO_BENCH_` prefix:

| Environment variable | Corresponding flag |
|---|---|
| `CONTEXTGO_BENCH_QUERY` | `--query` |
| `CONTEXTGO_BENCH_ITERATIONS` | `--iterations` |
| `CONTEXTGO_BENCH_SEARCH_LIMIT` | `--search-limit` |

## Comparing Python and native paths

Run both backends and compare:

```bash
python3 -m benchmarks --mode python --format json > python.json
python3 -m benchmarks --mode native --format json > native.json
diff python.json native.json
```

Or run both in a single pass with `--mode both`:

```bash
python3 -m benchmarks --mode both
python3 -m benchmarks --mode both --format json > both.json
```

When `--mode both` is used, the JSON output is a dictionary keyed by mode name, with an additional `comparison` array containing `python_mean_ms`, `native_mean_ms`, `mean_diff_ms`, and `mean_ratio` fields.

## Backward compatibility

`python3 benchmarks/session_index_benchmark.py` still works and is equivalent to `--mode native --format json`.

## Quick pre-release run

```bash
python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark
```

This is the minimum benchmark run required before tagging a release. See [docs/TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md) for interpretation guidance.
