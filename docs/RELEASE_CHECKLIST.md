# Release Checklist

- [ ] `rg` scan confirms no secrets / local absolute paths
- [ ] `bash -n scripts/*.sh`
- [ ] `python3 -m py_compile scripts/*.py`
- [ ] `python3 benchmarks/session_index_benchmark.py`
- [ ] README updated for new env vars / behavior
- [ ] healthcheck output reviewed on a real machine
- [ ] GitHub Actions verification passes
- [ ] tag + release notes created
