# ContextGO 0.11.2

## Summary

ContextGO 0.11.2 is a performance, security, and reliability patch on top of 0.11.1.
No breaking changes. Users on 0.11.0 or 0.11.1 can upgrade in place.

## Highlights

- **[Go P0]** sync.Pool buffer aliasing fixed — pool buffer uses a fixed 32 MB allocation; scanner never grows, pool reuse now effective
- **[Rust P1]** Per-file `active_workdir` syscall eliminated — computed once at scan entry, passed to all parallel workers
- **[Rust P1]** ASCII fast-path heap allocation removed — replaced `Vec<u8>` lowercase copy with `eq_ignore_ascii_case` zero-alloc comparison
- **[Go P1]** Unconditional `lineStr` allocation deferred — string conversion delayed to JSON-parse-failure path only
- **[Go P1]** `IsNoiseLower` avoids `strings.Split` on single-line input — newline check prevents allocation in the common case
- **[Python]** Duplicate `scripts` package removed from wheel distribution
- **[Python]** `memory_viewer` health response no longer leaks internal `db_name` path
- **[CI]** Safety scan now covers full dependency closure (`pip freeze`) instead of `requirements.txt` only
- **[CI]** `release.yml` concurrency group added to prevent duplicate release runs

## Fixed

### [Go P0] sync.Pool buffer aliasing / 缓冲池别名

The Go session scanner was returning a grown buffer back to the pool after
`bufio.Scanner` expanded it beyond the initial allocation. Subsequent pool gets
would receive the large buffer by reference, causing data races on concurrent
scans. The fix allocates a fixed 32 MB buffer once per pool object so the
scanner never reallocates and pool reuse is safe.

### [Rust P1] active_workdir per-file syscall / 每文件重复 syscall

`active_workdir()` was called once per file inside the parallel worker closure.
It is now computed at scan entry and passed into all workers, reducing syscall
overhead on large directory trees.

### [Rust P1] ASCII fast-path heap allocation / ASCII 快路径堆分配

The keyword noise filter was allocating a `Vec<u8>` to lowercase-compare file
extension strings. Replaced with `eq_ignore_ascii_case`, which performs the
comparison in-place with no allocation.

### [Go P1] Unconditional lineStr allocation / 无条件字符串分配

`lineStr` was constructed unconditionally from `[]byte` on every scanned line.
The conversion is now deferred to the JSON-parse-failure branch, which is the
minority path in normal operation.

### [Go P1] IsNoiseLower single-line Split / 单行 Split

`IsNoiseLower` called `strings.Split(line, "\n")` even on inputs that contain no
newline. A newline presence check now gates the split, avoiding the allocation
and slice on the common single-line case.

### [Python] Wheel duplicate scripts package / wheel 重复打包

`pyproject.toml` contained a `force-include` entry that caused the `scripts`
package to appear twice in the wheel. The redundant entry has been removed.

### [Python] memory_viewer db_name information leak / 信息泄露

The `/api/health` endpoint and SSE event stream included the internal `db_name`
field, which could expose the on-disk database path to any client that can reach
the viewer. The field is now filtered from both response surfaces.

### [CI] safety scan scope / 安全扫描范围

The `safety` vulnerability scan was reading only `requirements.txt`, which does
not include transitive or dev dependencies installed by `pip`. Changed to
`pip freeze` to capture the full installed closure.

### [CI] release.yml concurrency / 发布并发保护

Added a `concurrency` group to `release.yml` so that a second release run
triggered on the same ref cancels rather than racing with the first.

## Changed

- **pyproject.toml:** Added `numpy>=1.24` to dev extras; removed incorrect `Libraries` PyPI classifier.
- **Makefile:** Updated `mypy` and `py_compile` targets to scan `src/contextgo/` instead of the legacy `scripts/` layout.
- **docs/TROUBLESHOOTING.md:** Fixed stale `context_cli serve` command reference to `contextgo serve`.

## Upgrade notes

No configuration or API changes. `pipx upgrade contextgo` is sufficient.
