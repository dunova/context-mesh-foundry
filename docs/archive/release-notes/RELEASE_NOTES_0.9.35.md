## v0.9.35 -- Lightweight, Stable, Fast / 轻稳快

> A focused optimization sprint across every layer of the global memory runtime.
> Bounded daemon I/O. SQLite PRAGMA tuning. Search caching. 151 new Python tests + 8 new Go tests.

---

### Highlights

- **CLI cold-start reduced** -- `json`, `datetime`, and `types.ModuleType` imports deferred to point of use
- **Daemon memory bounded** -- chunked file reads (`_TAIL_CHUNK_BYTES` = 1 MB), cursor eviction policy, `gc.collect()` hints after cleanup
- **SQLite throughput improved** -- `executemany()` batch inserts, secondary indexes on `source_type` / `session_id`, PRAGMA tuning (`synchronous=NORMAL`, `cache_size=-8000`, `mmap_size=256MB`, `temp_store=MEMORY`)
- **Search result caching** -- TTL-based cache in both `memory_index` and `session_index` eliminates redundant scans
- **Retry-on-busy resilience** -- exponential backoff helpers (`_retry_sqlite`, `_retry_sqlite_many`, `_retry_commit`) for WAL contention
- **Go scanner improved** -- `sync.Pool` for buffer reuse, `filepath.WalkDir` replacing `filepath.Walk`, parallel walks with bounded goroutine pool
- **Test coverage expanded** -- 1,131 to 1,282 Python tests (+151), 8 new Go tests, coverage 97.9% to 98.1%

---

### Performance

| Area | Change | Impact |
|------|--------|--------|
| CLI startup | Defer 3 stdlib imports | Faster cold-start |
| SQLite writes | `synchronous=NORMAL` + WAL | Reduced fsync overhead (per SQLite docs) |
| SQLite reads | Secondary indexes on `source_type`, `session_id` | Faster filtered queries |
| Memory index | `executemany()` batch inserts | Fewer Python-SQLite round-trips |
| Session index | Batch upserts + path normalization cache | Faster sync |
| Daemon I/O | 1 MB chunked reads | Bounded peak memory |
| Go scanner | `sync.Pool` + parallel walks | Lower GC pressure |

---

### Added

- SQLite retry helpers with exponential backoff (0.1s / 0.5s / 2s)
- Secondary indexes on `source_type`, `session_id`, `created_at_epoch`, `updated_at_epoch`, `filepath`
- Search result cache with configurable TTL (default 5s) in both indexes
- Daemon cursor eviction: oldest third evicted when over `MAX_FILE_CURSORS`
- Go: 8 new edge-case tests (BOM, Latin-1 fallback, large files, deep nesting, symlink cycles, parallel pool reuse)
- Python: 151 new tests across `test_coverage_boost_r10`, `test_context_smoke`, `test_memory_viewer`, `test_context_maintenance`, `test_session_index` (CJK)

### Changed

- `MAX_TRACKED_SESSIONS` default raised from 240 to 500
- `MAX_PENDING_FILES` hard-capped at 50 to bound disk scan cost
- Daemon `_tail_file()` reads binary with decode, avoiding `UnicodeDecodeError`
- Daemon glob cache uses generator for large result sets, avoiding full materialization
- Go scanner: pre-allocated rune slices, parallel directory walks with bounded goroutine pool
- `COLLATE NOCASE` replaces `lower()` in LIKE queries for both indexes
- Docs: `CONFIGURATION.md` updated with new env vars (`TAIL_CHUNK_BYTES`, `PENDING_HARD_LIMIT`, `SESSION_SEARCH_CACHE_TTL`, `MEMORY_INDEX_SEARCH_CACHE_TTL`)

### Fixed

- Daemon `maybe_sync_index()` catches `sqlite3.OperationalError` (database locked) gracefully
- `MAX_PENDING_FILES` unbounded growth under heavy file watch load

---

### Stats

| Metric | Before (v0.9.32) | After (v0.9.35) |
|--------|-------------------|------------------|
| Python tests | 1,131 | 1,282 |
| Coverage | 97.9% | 98.1% |
| Go tests | Pass | Pass |
| Ruff | Clean | Clean |
| Go vet | Clean | Clean |

> Note: v0.9.33 and v0.9.34 were skipped; this release follows directly from v0.9.32.

---

### Upgrade

```bash
pip install --upgrade contextgo
contextgo health
```

No breaking changes. No migration steps required. If you run the daemon as a service, restart it after upgrading to pick up the new I/O and caching improvements.

---

**Full Changelog**: https://github.com/dunova/ContextGO/compare/v0.9.32...v0.9.35
