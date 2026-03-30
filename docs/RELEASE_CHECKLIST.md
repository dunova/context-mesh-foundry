# ContextGO Release Checklist

This checklist must be completed in order for every release. Each step has a defined pass condition. Do not proceed to the next phase until the current phase is fully green.

---

## Phase 1: Pre-release — Code and Configuration Audit

- [ ] **Version file updated.** `VERSION` contains exactly the new version string (e.g. `0.9.0`) with no trailing whitespace or extra lines.
- [ ] **VERSION references updated in README.** `README.md` version badge and the `## Version` section reference the new version and link to the correct release notes file.
- [ ] **CHANGELOG entry added.** `CHANGELOG.md` contains a new section at the top for the new version with date, Story, Added, Changed, Fixed, and Removed subsections as applicable.
- [ ] **Release notes file created.** `docs/RELEASE_NOTES_<version>.md` exists and contains Highlights, Breaking Changes, New Features, Improvements, Bug Fixes, Performance, Documentation, Contributors, Verification, and Upgrade Path sections.
- [ ] **No secrets or personal paths in code.** Run `grep -r "REDACTED\|password\|secret\|token\|/Users/\|/home/[a-z]\+" scripts/ native/ benchmarks/` and confirm zero matches on sensitive patterns. Confirm no hardcoded absolute local paths remain.
- [ ] **Storage root configuration verified.** `src/contextgo/context_config.py` `storage_root()` defaults to `~/.contextgo` (or the value of `CONTEXTGO_STORAGE_ROOT`). Confirm the path is readable and writable under the deploying user on the target machine.
- [ ] **Environment variable inventory reviewed.** All `CONTEXTGO_*` environment variables referenced in code are documented in `docs/ARCHITECTURE.md` or `CONTRIBUTING.md`.

---

## Phase 2: Pre-release — Static Analysis and Compilation

- [ ] **Shell script syntax check passes.**
  ```bash
  bash -n scripts/*.sh
  ```
  Expected: no output, exit code 0.

- [ ] **Python compile check passes.**
  ```bash
  python3 -m py_compile src/contextgo/*.py benchmarks/*.py
  ```
  Expected: no output, exit code 0.

- [ ] **Go build check passes.**
  ```bash
  cd native/session_scan_go && go build ./...
  ```
  Expected: no output, exit code 0.

- [ ] **Rust build check passes.**
  ```bash
  cd native/session_scan && cargo build
  ```
  Expected: build completes, exit code 0.

---

## Phase 3: Pre-release — Test Suite

- [ ] **Python unit tests pass.**
  ```bash
  python3 -m pytest tests/ -v
  ```
  Expected: all tests pass, no errors, no unexpected skips.

- [ ] **Go unit tests pass.**
  ```bash
  cd native/session_scan_go && go test ./...
  ```
  Expected: `ok` for all packages, exit code 0.

- [ ] **Rust unit tests pass.**
  ```bash
  cd native/session_scan && CARGO_INCREMENTAL=0 cargo test
  ```
  Expected: all tests pass, exit code 0.

- [ ] **e2e quality gate passes.**
  ```bash
  python3 scripts/e2e_quality_gate.py
  ```
  Expected: all gate stages report pass, structured JSON result contains no failures, exit code 0.

---

## Phase 4: Pre-release — Runtime Validation

- [ ] **Health check passes.**
  ```bash
  contextgo health
  ```
  or
  ```bash
  bash scripts/context_healthcheck.sh --deep
  ```
  Expected: all health probes green, exit code 0.

- [ ] **Smoke test passes.**
  ```bash
  contextgo smoke
  ```
  Expected: native contract checks for both Rust and Go backends pass, all smoke cases reported as passed, exit code 0.

- [ ] **Installed runtime smoke passes.**
  ```bash
  python3 scripts/smoke_installed_runtime.py
  ```
  Expected: `INSTALL_ROOT` (`~/.local/share/contextgo` by default) contains `src/contextgo/context_cli.py`, `scripts/e2e_quality_gate.py`, `scripts/context_healthcheck.sh`, and `benchmarks/run.py`; all smoke cases pass; exit code 0.

- [ ] **Native scan produces non-empty results.**
  ```bash
  contextgo native-scan --backend auto --threads 4
  ```
  Expected: at least one result returned, no errors, exit code 0.

- [ ] **Benchmark runs without error.**
  ```bash
  python3 -m benchmarks --mode both --iterations 1 --warmup 0 --query benchmark --format text
  ```
  Expected: both `python` and `native-wrapper` columns present in output, no `FAIL` entries, exit code 0.

---

## Phase 5: Pre-release — Documentation and CI Verification

- [ ] **CI pipeline is green.** All GitHub Actions checks pass on the release branch or tag. Workflow run artifacts are retained.
- [ ] **Architecture doc is current.** `docs/ARCHITECTURE.md` reflects the module dependency graph, storage layout, and native acceleration decision tree for this version.
- [ ] **Troubleshooting doc is current.** `docs/TROUBLESHOOTING.md` covers failure modes introduced or modified in this release.
- [ ] **CONTRIBUTING.md is current.** Reflects the current test execution commands and quality gate definition of done.
- [ ] **SECURITY.md is current.** Threat model, trust boundary, and disclosure instructions reflect the current release.
- [ ] **All doc cross-links verified.** Run a link-check pass or manually verify that all relative links in `README.md`, `docs/ARCHITECTURE.md`, and release notes resolve to existing files.

---

## Phase 6: Release

- [ ] **Git tag created.** Tag the release commit: `git tag -a v0.9.0 -m "ContextGO 0.9.0"`.
- [ ] **Tag pushed to remote.** `git push origin v0.9.0`.
- [ ] **GitHub Release created.** Release created from the tag with the contents of `docs/RELEASE_NOTES_<version>.md` as the body.
- [ ] **Release artifacts attached** (if applicable): compiled native binaries for supported platforms, or a note confirming source-only release.

---

## Phase 7: Post-release Verification

- [ ] **GitHub Release page renders correctly.** Release notes display without formatting errors. All links in the release body resolve.
- [ ] **Tag is visible on the repository.** `git ls-remote --tags origin` shows the new tag.
- [ ] **README version reflects the new release.** The repository homepage shows the correct version after the release commit is merged to the default branch.
- [ ] **Installed runtime re-validated on a clean environment.** On a machine that has not previously run the quality gate for this version, repeat Phase 3 through Phase 4 using the released artifacts to confirm reproducibility.
- [ ] **Rollback path confirmed.** The previous release (`0.9.32`) artifacts are still available on GitHub Releases and the rollback procedure in `docs/TROUBLESHOOTING.md` has been verified to work.

---

## Notes

- A release is not complete until Phase 7 is fully checked.
- If any step in Phases 2 through 4 fails, stop, fix the root cause, re-run from the beginning of the failing phase.
- Do not skip the installed runtime smoke (Phase 4). It is the only validation that catches deployment packaging errors that unit tests cannot detect.
- The quality gate in Phase 3 is the authoritative pass/fail signal for code correctness. CI is a secondary confirmation.
