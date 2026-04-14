# Release Notes — ContextGO 0.12.2 / 发布说明

## Highlights / 亮点

- **Smart recall instead of noisy recall**: automatic prewarm now runs only on cold starts, continuations, topic shifts, and structural prompts. Same-topic back-and-forth stays silent to reduce token waste. / 自动召回从“逢问必查”升级为“按需触发”
- **Higher hit rate on real prompts**: prewarm no longer pushes long keyword strings into strict search paths. It now plans short anchor queries and single-term fallbacks, which materially improves recall on natural-language prompts. / 长关键词串导致零命中的问题已修正
- **Graph-first structural workflow**: architecture, dependency, call-chain, and blast-radius questions now steer users toward code graph tools first, then ContextGO for historical decisions. / 结构类问题优先 graph，再补历史记忆
- **Cursor coverage improvement**: adapter extraction now recognizes newer Cursor generation/composer fields so recent sessions are easier to recall. / Cursor 新版字段提取增强

## Breaking Changes / 破坏性变更

None. Existing `contextgo setup` users can upgrade in place. The new policy block replaces the old one automatically on re-run. / 无破坏性变更，重新执行 `contextgo setup` 即可升级策略块。

## New Features / 新增功能

- Smart recall trigger classification in `context_prewarm.py`:
  - cold start / new window
  - continuation / handoff
  - new topic detection
  - structural-question graph hint
- Compact recall state persisted per workspace so same-topic follow-ups can stay silent.
- Short-query planning for prewarm recall:
  - 2-term anchor query first
  - single-term fallback queries next
  - de-duplicated aggregation across candidates

## Improvements / 改进

- `contextgo prewarm --help` and `contextgo setup --help` now describe the actual smart-recall behavior instead of the old always-search framing.
- Injected policy blocks now align across Claude Code, Codex, Cursor, Accio, Antigravity, Copilot, and OpenClaw.
- Session fallback output is trimmed to a compact form for lower hook token cost.
- Cursor adapter extraction now reads `composer`, `generation`, `textDescription`, and related fields from newer workspace storage records.

## Bug Fixes / 修复

- Fixed low-hit recall behavior caused by concatenating too many keywords into a single strict query.
- Fixed stale policy persistence: `setup` now replaces older SCF policy blocks with the new smart-recall policy instead of leaving outdated instructions in place.

## Performance / 性能

- Lower token overhead for hook output by capping recall to a concise summary and up to 3 hits.
- Reduced wasted searches by suppressing same-topic repeated recalls inside a short workspace-local TTL window.

## Documentation / 文档

- Updated README AI-agent behavior sections to explain selective recall.
- Added this release notes file and refreshed the docs index release table.
- Updated shell policy injector copy to the new smart-recall wording.

## Contributors / 贡献者

- Dunova
- Codex

## Verification / 验证

- `python3 -m py_compile src/contextgo/context_prewarm.py src/contextgo/context_cli.py src/contextgo/source_adapters.py`
- `python3 -m pytest -o addopts='' tests/test_context_prewarm.py`
- `python3 -m pytest -o addopts='' tests/test_source_adapters.py`
- `bash -n scripts/apply_context_first_policy.sh`
- `PYTHONPATH=src python3 -m contextgo.context_cli setup --help`
- `PYTHONPATH=src python3 -m contextgo.context_cli prewarm --help`

## Upgrade Path / 升级路径

```bash
pipx upgrade "contextgo[vector]"
contextgo setup
contextgo health
```

If you manage instructions manually, copy the refreshed smart-recall policy into your tool entrypoint and re-run your normal smoke checks. / 若你手工维护指令文件，请同步新版 smart-recall 策略后再执行常规冒烟验证。
