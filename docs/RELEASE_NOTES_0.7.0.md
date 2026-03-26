# ContextGO 0.7.0 Release Notes

**Release date:** 2026-03-26
**Release type:** Quality and stability — commercial-grade polish

---

## Highlights

0.7 is the commercial-grade polish release.

Where 0.6.1 closed the gap between experimental codebase and deployable product, 0.7.0 hardens every layer of the stack against the standards a production engineering team would apply before trusting a tool in a daily workflow: comprehensive test coverage, structured CI/CD validation, tightened native code paths, documented security posture, and a fully verified end-to-end quality gate.

No new runtime features are introduced. The focus is correctness, auditability, and confidence: every path that ships has a test, every test has a deterministic pass condition, and every operator-facing document reflects the current behavior of the system.

This release is suitable for deployment in multi-developer teams where the context runtime is a shared infrastructure dependency, not just a personal productivity script.

---

## Breaking Changes

None. All CLI commands, environment variables, configuration keys, install paths, and service labels from 0.6.1 remain unchanged.

Operators upgrading from 0.6.1 do not need to modify any deployment configuration.

---

## New Features

### Autoresearch module and test suite

- `scripts/autoresearch_contextgo.py`: new module providing structured multi-step research workflows over the local context index, enabling agents to chain context lookups without manual query construction.
- `scripts/test_autoresearch_contextgo.py`: full unit and integration test coverage for the autoresearch module, including fixture-driven cases for query chaining, deduplication, and empty-result handling.

### Extended e2e quality gate

- `scripts/e2e_quality_gate.py` expanded with additional gate stages covering session index schema migration, native backend contract validation, and benchmark regression detection.
- Quality gate now emits structured JSON results, making it suitable as a CI artifact for pass/fail determination in automated pipelines.

### Session index benchmark

- `benchmarks/session_index_benchmark.py`: standalone benchmark for the SQLite-backed session index covering write throughput, read latency under concurrent load, and rescan convergence time.
- Results are comparable across versions to track index performance across releases.

---

## Improvements

### Code quality

- All Python modules in `scripts/` now pass `pylint` and `flake8` with zero errors at the configured threshold.
- Import ordering normalized across all scripts to stdlib, then third-party, then local.
- Dead code and unreachable branches removed from `context_core.py`, `context_native.py`, and `session_index.py`.
- Type annotations added to all public functions in `context_config.py`, `context_core.py`, and `session_index.py`.

### Test coverage

- Unit test coverage across `scripts/test_context_cli.py`, `scripts/test_context_core.py`, `scripts/test_context_native.py`, `scripts/test_context_smoke.py`, and `scripts/test_session_index.py` raised to cover previously untested edge cases:
  - `context_native.py`: health probe cache invalidation, JSON fallback parse with malformed input, backend selection when both Rust and Go binaries are absent.
  - `session_index.py`: canonical path normalization for symlinked roots, schema migration on a populated database, and concurrent index writes.
  - `context_smoke.py`: native contract smoke with missing fixture directory, and smoke with a storage root that is unwritable.
- All tests are deterministic: no reliance on real filesystem paths, network access, or wall-clock timing.

### CI/CD pipeline

- GitHub Actions workflow updated to run the full validation chain on every push and pull request:
  - Shell syntax check: `bash -n scripts/*.sh`
  - Python compile check: `python3 -m py_compile scripts/*.py benchmarks/*.py`
  - Unit tests: `python3 -m pytest` across all test modules
  - Go tests: `cd native/session_scan_go && go test ./...`
  - Rust tests: `cd native/session_scan && CARGO_INCREMENTAL=0 cargo test`
  - e2e quality gate: `python3 scripts/e2e_quality_gate.py`
  - Smoke: `python3 scripts/context_cli.py smoke`
- Workflow outputs are retained as artifacts for post-run forensics.
- Failure in any stage blocks merge.

### Native code hardening

- `native/session_scan_go/scanner.go`: error handling tightened around file read failures during directory walk; previously silently skipped files now emit structured warnings to stderr for operator visibility.
- `native/session_scan_go/scanner_test.go`: test coverage expanded to include directory walk over a fixture tree with intentionally unreadable files, verifying graceful degradation.
- `native/session_scan/src/`: Rust session scanner bounds-checked all slice indexing operations, eliminating the remaining `unwrap()` calls on path operations that could panic on unusual filesystem layouts.

### Documentation

- `docs/ARCHITECTURE.md`: updated to reflect the current module dependency graph, storage layout under `~/.local/share/contextgo`, and the native acceleration decision tree.
- `docs/TROUBLESHOOTING.md`: expanded with new sections covering native binary not found, session index schema migration failures, and health probe cache stale reads.
- `CONTRIBUTING.md`: updated with the full local development setup, test execution instructions, and the definition of done required for a PR to pass the quality gate.
- `SECURITY.md`: updated with current threat model, trust boundary description, and guidance on responsible disclosure.
- `docs/RELEASE_CHECKLIST.md`: fully rewritten as a structured pre- and post-release checklist with explicit pass conditions for each step.

---

## Bug Fixes

- Fixed: `session_index.py` canonical path logic incorrectly resolved symlinked storage roots, causing duplicate entries in the index when the install path was accessed via both the canonical and symlink paths. The index now resolves all paths through `Path.resolve()` before insertion and lookup.
- Fixed: `context_native.py` health probe cache could return a stale `healthy` result after the native binary was removed or became unexecutable between restarts. Cache is now invalidated when the binary mtime changes.
- Fixed: `context_smoke.py` native contract check raised an unhandled `FileNotFoundError` when the fixture directory did not exist instead of reporting a structured failure. Now catches and reports as a named failure case.
- Fixed: `benchmarks/run.py` would silently skip the `native-wrapper` timing column in text-format output if the native backend returned a non-zero exit code; now marks the column as `FAIL` with the exit code for operator visibility.
- Fixed: `e2e_quality_gate.py` did not flush stdout between gate stages, causing buffered output to appear out of order when run inside a CI pty. Now explicitly flushes after each stage result line.

---

## Performance

- `session_index.py` rescan: batch write commit interval tuned from per-row to per-100-row, reducing SQLite write amplification by approximately 80% on large directory trees. Observed rescan time on a 10,000-session corpus reduced from ~8s to ~1.5s on a local NVMe device.
- `context_native.py` health probe: TTL cache introduced in 0.6.1 confirmed stable across the full test matrix; default TTL remains 30 seconds.
- Go scanner (`native/session_scan_go`): query-window snippet extraction now avoids redundant string allocation on the hot path by operating on byte slices directly; throughput improvement measured at approximately 12% on the synthetic benchmark corpus.

---

## Documentation

- `docs/ARCHITECTURE.md`: reflects current system layout and module boundaries.
- `docs/TROUBLESHOOTING.md`: new sections for native binary lifecycle and index migration.
- `docs/RELEASE_CHECKLIST.md`: rewritten with structured pre- and post-release steps.
- `CONTRIBUTING.md`: full development workflow including test, lint, and quality gate execution.
- `SECURITY.md`: current threat model and disclosure process.
- All doc cross-links verified against the current file layout.

---

## Contributors

This release was produced by the ContextGO core team. Contributions in the form of benchmark data, bug reports, and real-world deployment feedback from early operators informed the prioritization of the stability and coverage work in this cycle.

---

## Verification

The following commands constitute the full verification chain for this release:

```bash
bash -n scripts/*.sh
python3 -m py_compile scripts/*.py benchmarks/*.py
python3 -m pytest scripts/test_context_cli.py scripts/test_context_core.py scripts/test_context_native.py scripts/test_context_smoke.py scripts/test_session_index.py scripts/test_autoresearch_contextgo.py
python3 scripts/e2e_quality_gate.py
python3 scripts/context_cli.py health
python3 scripts/context_cli.py smoke
python3 scripts/smoke_installed_runtime.py
python3 -m benchmarks --mode both --iterations 1 --warmup 0 --query benchmark --format text
cd native/session_scan_go && go test ./...
cd native/session_scan && CARGO_INCREMENTAL=0 cargo test
```

---

## Upgrade Path

No migration steps are required when upgrading from 0.6.1 to 0.7.0.

After replacing scripts and binaries in the install root (`~/.local/share/contextgo/scripts`), run `python3 scripts/context_cli.py health` to confirm the runtime is healthy. The session index schema version is unchanged; no rescan is required.

To verify the installed runtime after deployment:

```bash
python3 scripts/smoke_installed_runtime.py
```
