---
name: contextgo-gsd
version: "0.9"
description: |
  ContextGO GSD (Get Stuff Done) workflow reference.
  Activates when the user mentions "GSD", "workflow", "context lifecycle",
  or wants to understand the full recall-execute-persist loop.
  Requires: contextgo CLI (pip install contextgo or bash skills/install.sh).
---

# ContextGO GSD Workflow
# ContextGO GSD 工作流

GSD is the three-phase loop that runs across every coding session.
GSD 是贯穿每个编程会话的三阶段循环。

No cloud. No MCP. No server. The AI agent calls the local CLI directly.
无云、无 MCP、无服务器。AI 智能体直接调用本地 CLI。

---

## Phase 1 — Recall / 阶段一：召回

Run at session start or when switching tasks.
在会话开始或切换任务时执行。

```bash
# What was I working on? / 我之前在做什么？
contextgo semantic "<current task or project>" --limit 3

# Find a specific decision or fix / 查找特定决策或修复
contextgo search "<keyword>" --limit 5

# Exact match: error message or log line / 精确匹配：错误信息或日志行
contextgo search "<exact phrase>" --limit 5 --literal
```

`semantic` checks saved memory files first, then falls back to session history index.
`search` queries the index directly.

`semantic` 优先检查已保存记忆，再回退到会话历史索引。
`search` 直接查询索引。

**Deliver results as:** "You were working on X. Last session you decided Y. Next step was Z."
Never paste raw CLI output.

**结果呈现格式：**"你之前在做 X。上次会话决定了 Y。下一步是 Z。"
禁止粘贴原始 CLI 输出。

---

## Phase 2 — Execute / 阶段二：执行

Work normally. The ContextGO daemon captures session transcripts and file changes in the background automatically.
正常工作。ContextGO daemon 在后台自动捕获会话记录和文件变更。

Mid-session recall when you hit an unknown:
遇到未知情况时随时召回：

```bash
contextgo search "that auth bug" --limit 3
```

If recall returns empty or stale results, diagnose:
若召回结果为空或陈旧，诊断索引：

```bash
contextgo health --verbose
```

---

## Phase 3 — Persist / 阶段三：持久化

At milestones, hard problems solved, or session end:
在里程碑、攻克难题或会话结束时执行：

```bash
contextgo save \
  --title "Brief title of what was decided or learned" \
  --content "Full explanation: file paths, rationale, gotchas, next steps" \
  --tags "project,topic,type"
```

**Save / 应保存：**
- Architectural decisions with rationale / 带理由的架构决策
- Bug root causes and the actual fix / bug 根因与实际修复
- "Do not do X because Y" warnings / "不要做 X，因为 Y" 的警告
- Environment/config choices / 环境与配置选择
- Cross-session handoff: what to do next / 跨会话交接：下一步做什么

**Do not save / 不应保存：**
- Routine edits (daemon already indexes these) / 日常编辑（daemon 已自动索引）
- Temporary debug attempts that failed / 失败的临时调试步骤

---

## Cross-Tool Handoff / 跨工具交接

```bash
# Export context for another agent / 导出上下文给其他智能体
contextgo export "<query>" /tmp/handoff.json --limit 100

# Import on the receiving side / 接收端导入
contextgo import /tmp/handoff.json
```
