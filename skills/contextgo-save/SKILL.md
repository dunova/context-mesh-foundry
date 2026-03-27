---
name: contextgo-save
version: "0.9"
description: |
  Persist key conclusions, decisions, and lessons to ContextGO local memory.
  Activates when the user solves a hard problem, makes an architectural decision,
  finishes a task, or says "save this", "remember this", "note this", "wrap up".
  Also activates proactively when a significant decision should survive across sessions.
  Requires: contextgo CLI.
---

# ContextGO Save
# ContextGO 持久化

Write durable memory entries that persist across sessions and are searchable
via `contextgo semantic` and `contextgo search`.

写入持久记忆条目，跨会话留存，可通过 `contextgo semantic` 和 `contextgo search` 检索。

Saved as Markdown under `~/.contextgo/resources/shared/conversations/`,
indexed automatically by the daemon.

保存为 Markdown 文件至 `~/.contextgo/resources/shared/conversations/`，daemon 自动索引。

---

## Command / 命令

```bash
contextgo save \
  --title "Concise title: what was decided or learned" \
  --content "Full context: rationale, file paths, commands, gotchas, next steps" \
  --tags "project,topic,category"
```

---

## When to Save / 保存时机

| Situation / 场景 | Example Title / 标题示例 |
|---|---|
| Architectural decision / 架构决策 | `Decision: chose SQLite over Elasticsearch` |
| Bug root cause found / 发现 bug 根因 | `Bug: OOM in daemon — unbounded cursor dict` |
| Config/env choice / 配置选择 | `Config: POLL_INTERVAL_SEC=180 optimal for battery` |
| Warning for future self / 对未来的警告 | `Warning: do not use mmap on NFS storage roots` |
| Session handoff / 会话交接 | `Handoff: auth middleware done, token refresh next` |

---

## When NOT to Save / 不保存的情况

- Routine file edits — the daemon captures these automatically / 日常编辑，daemon 已自动索引
- Temporary debug steps that failed / 失败的临时调试步骤
- Information the user already knows / 用户已知的信息
- Anything containing secrets, tokens, or credentials / 含密钥或凭证的任何内容

---

## Tag Conventions / 标签约定

Lowercase, hyphenated. / 小写+连字符格式。

| Category / 类别 | Examples / 示例 |
|---|---|
| Project / 项目 | `contextgo`, `my-app`, `infra` |
| Topic / 主题 | `auth`, `performance`, `ci-cd` |
| Type / 类型 | `decision`, `bug-fix`, `config`, `handoff` |

---

## Proactive Save Prompts / 主动保存提示

After a non-trivial fix, suggest saving:
攻克疑难问题后，主动建议保存：

> "This was a significant fix. Want me to save it to ContextGO for future reference?"
> "这是一个重要修复，是否保存到 ContextGO 以备将来参考？"

At session end with decisions made, save without waiting:
会话结束且有决策产生时，无需等待，直接保存：

> "Saving 2 architectural decisions from this session for continuity."
> "保存本次会话的 2 项架构决策，确保上下文连续性。"

---

## Export for Sharing / 导出共享

```bash
contextgo export "" /tmp/memories.json --limit 500
contextgo import /tmp/memories.json   # on the receiving end / 接收端
```
