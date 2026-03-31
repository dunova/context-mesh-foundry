# ContextGO 0.11.1

## Summary

ContextGO 0.11.1 is a reliability, CI hardening, and documentation alignment patch
on top of 0.11.0. No breaking changes. Users on 0.11.0 can upgrade in place.

## Highlights

- Adapter fault isolation extended — `sync_all_adapters()` and `source_freshness_snapshot()` now catch per-source exceptions so a single broken adapter cannot block the entire sync pipeline
- Search/sync decoupled — `sync_all_adapters()` only runs after the throttle check in `sync_session_index()`, preventing frequent search calls from triggering full adapter filesystem scans
- `smoke --sandbox` path resolution fixed — uses `Path.resolve()` to handle symlinked pipx/pip installations correctly
- Release gate hardened — `release.yml` now includes the same Go/Rust verification steps as `verify.yml`; `safety` check is now a blocking gate (removed `|| true`)
- Stale `scripts/` path references updated to `src/contextgo/` across AGENTS.md, CONTRIBUTING.md, ARCHITECTURE.md, RELEASE_CHECKLIST.md, native/README.md, and .claude/CLAUDE.md

## Fixed

### Adapter fault isolation / 适配器容错隔离

`sync_all_adapters()` and `source_freshness_snapshot()` now wrap each source in a
`try/except` block. Previously, a single broken or unavailable adapter could raise
an unhandled exception and abort the entire sync pipeline for all other adapters.

### Search/sync decoupling / 搜索与同步解耦

`sync_all_adapters()` call was moved to run only after the throttle check inside
`sync_session_index()`. The previous position meant that every `contextgo search`
invocation would trigger a full filesystem scan of all registered adapter sources,
regardless of how recently a sync had been performed.

### Smoke test path resolution / 冒烟测试路径解析

`smoke --sandbox` now resolves the package root via `Path.resolve()` before
constructing sandboxed paths. This correctly handles installations where the
`contextgo` entry point is a symlink (e.g. pipx on macOS).

### CI release gate / CI 发布门禁

`release.yml` now runs the same Go and Rust verification steps (`go vet`, `cargo clippy`)
that `verify.yml` already required. The `safety` vulnerability scan no longer uses
`|| true`; a finding now blocks the release.

### Test compatibility / 测试兼容

`test_sync_rechecks_immediately_when_adapter_dirty` now marks the adapter dirty
*after* writing test data, matching the production code ordering.

## Changed

### Documentation path alignment / 文档路径对齐

All references to the old `scripts/` layout were updated to `src/contextgo/` across
six documentation and configuration files to reflect the package layout introduced
in 0.10.x.

## Upgrade notes

No configuration or API changes. `pip install --upgrade contextgo` is sufficient.
