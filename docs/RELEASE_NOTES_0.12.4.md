# Release Notes — ContextGO 0.12.4 / 发布说明

## Highlights / 亮点

- **Recall heuristics tightened for real-world prompts**: agents are now guided to normalize relative dates into absolute dates, add one workspace anchor, and prefer 2-3 compact ContextGO queries before any broader fallback. / 检索启发进一步收紧，更贴近真实使用
- **Skills updated to match the new policy**: both `contextgo-recall` and `contextgo-gsd` now teach the same compact-query pattern. / 技能文档与新策略对齐
- **Rolled back out to all connected local platforms**: the refreshed smart-recall policy has been pushed through `contextgo setup` again. / 已重新推送到所有本机已接入平台

## Breaking Changes / 破坏性变更

None. / 无。

## New Features / 新增功能

- Policy-level guidance for:
  - absolute-date conversion
  - workspace-anchor query planning
  - compact exact-first ContextGO recall

## Improvements / 改进

- Better default behavior for prompts like “昨天我和 Codex 的聊天”.
- Less chance of falling back too early into broad session/file scans.
- Lower token waste from oversized keyword strings.

## Bug Fixes / 修复

- Fixed the policy gap where agents could still choose broad session-native searches before trying strong ContextGO anchors.

## Performance / 性能

- No runtime dependency cost added.
- Lower recall token cost through tighter query planning.

## Documentation / 文档

- Updated `docs/skills/contextgo-recall/SKILL.md`
- Updated `docs/skills/contextgo-gsd/SKILL.md`
- Added this release notes file and updated the changelog

## Contributors / 贡献者

- Dunova
- Codex

## Verification / 验证

- `python3 -m py_compile src/contextgo/context_prewarm.py`
- `python3 -m pytest -o addopts='' tests/test_context_prewarm.py`
- `PYTHONPATH=src python3 -m contextgo.context_cli setup`

## Upgrade Path / 升级路径

```bash
pipx upgrade "contextgo[vector]"
contextgo setup
```

## Dependency Note / 依赖说明

No dependency changes are required for this release. / 本次发布无需调整依赖。
