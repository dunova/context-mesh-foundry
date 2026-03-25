# Changelog

## 0.5.0 - 2026-03-25

### Added

- standalone `contextmesh` runtime with unified CLI:
  - `search`
  - `semantic`
  - `save`
  - `export`
  - `import`
  - `serve`
  - `maintain`
  - `health`
- built-in session index backed by local SQLite
- benchmark harness under [`benchmarks/`](/Volumes/AI/GitHub/context-mesh-foundry/benchmarks)
- Rust session-scan prototype under [`native/session_scan/`](/Volumes/AI/GitHub/context-mesh-foundry/native/session_scan)

### Changed

- converged the mainline into a local-first monolith
- switched deployment defaults to `contextmesh` service names
- disabled remote sync by default for lower overhead and more predictable local behavior
- isolated legacy bridges under [`scripts/legacy/`](/Volumes/AI/GitHub/context-mesh-foundry/scripts/legacy)
- normalized package-safe imports so the runtime works in both script mode and package mode

### Fixed

- viewer runtime config propagation through the canonical server entrypoint
- session index rescan behavior with short sync windows
- several wrapper/path assumptions left from earlier incremental refactors
