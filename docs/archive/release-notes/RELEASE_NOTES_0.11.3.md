# ContextGO 0.11.3

## Summary

ContextGO 0.11.3 is a packaging, documentation, and CI hardening patch on top of 0.11.2.
No breaking changes. Users on any 0.11.x release can upgrade in place.

## Highlights

- `__init__.py` now exports `__all__` and `__version__` for a clean public package API
- Module docstrings added to `context_core`, `session_index`, and `context_daemon`
- Missing release notes for v0.11.1 added as `docs/RELEASE_NOTES_0.11.1.md`
- CI wheel validation: `verify.yml` now builds and installs the wheel to catch packaging regressions before release
- CI smoke test: `--sandbox` flag added to the smoke step in `verify.yml`
- `.gitignore` extended to cover `.mypy_cache/`, `htmlcov/`, `.coverage`, and common cache directories

## Added

### `__init__.py` exports / 包导出定义

`src/contextgo/__init__.py` now declares `__all__` with the public API surface and
re-exports `__version__` from `VERSION`. This enables `from contextgo import __version__`
and static analysis tools that depend on `__all__` for completeness checks.

### Module docstrings / 模块文档字符串

`context_core.py`, `session_index.py`, and `context_daemon.py` each received a
top-level module docstring describing their responsibility, inputs, and outputs.
This improves `help()` output and IDE hover documentation for contributors.

### docs/RELEASE_NOTES_0.11.1.md

The release notes file for v0.11.1 was missing from the repository. It has been
added to `docs/` to complete the release notes history.

### CI wheel validation / CI wheel 验证

`verify.yml` now includes a step that builds the wheel with `hatch build` and
installs it into a temporary virtual environment, then runs `contextgo --version`
to confirm the installed entry point is functional. This catches packaging errors
(missing files, wrong include patterns) that unit tests running from source cannot detect.

## Fixed

### CI smoke test sandbox flag / CI 冒烟测试沙箱标志

The smoke step in `verify.yml` was invoking `contextgo smoke` without `--sandbox`.
Without the flag the smoke test writes to the user's real `~/.contextgo` directory,
which is unavailable in CI runners. The `--sandbox` flag is now passed, directing
all smoke I/O to a temporary directory.

### .gitignore completeness / .gitignore 完整性

Several common build and analysis cache directories were missing from `.gitignore`,
causing them to appear as untracked files after running `mypy`, `coverage`, or
`pytest --html`. Added: `.mypy_cache/`, `htmlcov/`, `.coverage`, `.pytest_cache/`,
`dist/`, `*.egg-info/`.

## Upgrade notes

No configuration or API changes. `pipx upgrade contextgo` is sufficient.
