# Troubleshooting & Integration Gotchas

This document summarizes known issues, integration blind spots, and troubleshooting steps for the standalone `context_cli.py` + `session_index.py` + `viking_daemon.py` stack, while also covering optional OpenViking legacy paths.

> **Note**: All paths referenced below are standard/relative forms. Actual deployment paths vary based on your environment configurations.

## 1. OpenViking Server Crash Loop (`litellm` Dependency)

**Symptom:**
legacy remote-sync service (for example an old OpenViking launchd/systemd unit) is stuck in a crash loop (`spawn scheduled`).
Logs indicate:
```text
ModuleNotFoundError: No module named 'litellm.llms.base_llm.skills'
```

**Root Cause:**
OpenViking requires the `skills` module from `litellm`, which was refactored or removed in `litellm` versions >= `1.81.0`.

**Fix:**
Downgrade or pin `litellm` to `<1.81.0` within the OpenViking virtual environment before starting the server.
```bash
# Example
/path/to/openviking_env/bin/pip install "litellm<1.81.0"
```
*Tip: Always restart the daemon or launch agent after modifying the environment.*

## 2. Aline Watcher/Worker Failures (`realign` Import Error)

**Symptom:**
If you rely on `Aline`'s hooks for capturing events (e.g., Claude Code), you might notice that recent conversations aren't indexed.
Logs for `aline_watcher_launchd.err` show:
```text
ModuleNotFoundError: No module named 'realign'
```
*(Even when using an isolated runner like `uv tool`.)*

**Root Cause:**
When starting the watcher/worker module via `python -m realign.watcher_daemon`, Python may fail to resolve the package if the wrapper scripts do not properly set the working directory or `PYTHONPATH` to the top layer of the `.venv/lib/python3.XX/site-packages`.

**Fix:**
Ensure that your `LaunchAgent` plist or `systemd` service explicitly includes `PYTHONPATH` pointing to the `site-packages` directory where `realign` is installed.
```xml
<key>EnvironmentVariables</key>
<dict>
    <key>PYTHONPATH</key>
    <string>/path/to/aline-ai/lib/python3.x/site-packages</string>
</dict>
```

## 3. Local Index Build Seems Slow on First Run

**Symptom:**
`python3 scripts/context_cli.py health` or `search` is noticeably slower the first time.

**Root Cause:**
`session_index.py` is scanning local Codex / Claude / shell history files and building `session_index.db`.

**Fix / Expectation:**
- First run is expected to be slower.
- Subsequent runs are incremental and usually much faster.
- If needed, inspect the generated DB at `~/.unified_context_data/index/session_index.db`.

## 4. Legacy MCP / Config Drift Still Exists

Even though the mainline no longer depends on MCP, stale configs can still leave old `openviking_mcp.py` references around.

**Common hidden sources to verify:**
- `~/.claude.json`
- `~/.codex/config.toml`
- `~/.gemini/antigravity/mcp_config.json`

**Fix:**
Remove or ignore stale MCP references unless you explicitly still need the legacy bridge.

## 5. General Diagnosis Advice
1. **Healthcheck Command**: Always run the included `context_healthcheck.sh --deep` or `python3 scripts/context_cli.py health`.
2. **Reviewing Logs**: Keep an eye on `.context_system/logs/` or `journalctl --user -u viking-daemon`.
3. **Empty Searches?**: Verify the actual JSONL sources (like `~/.codex/sessions`, `~/.claude/projects`, `history.jsonl`) are being actively modified by your terminals. Sometimes tools silently change their storage paths.
