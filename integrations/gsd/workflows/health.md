<purpose>
Validate the `.planning/` directory integrity and report actionable issues.
Checks for missing files, invalid configurations, inconsistent state, and
orphaned plans. Optionally repairs auto-fixable issues.
</purpose>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before
starting.
</required_reading>

<process>

<step name="parse_args">
**Parse arguments:**

Check whether the `--repair` flag is present in the command arguments.

```
REPAIR_FLAG=""
if arguments contain "--repair"; then
  REPAIR_FLAG="--repair"
fi
```
</step>

<step name="run_health_check">
**Run health validation:**

```bash
CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
GSD_HOME="${GSD_HOME:-$CLAUDE_CONFIG_DIR/get-shit-done}"
if [ ! -f "$GSD_HOME/bin/gsd-tools.cjs" ] && \
   [ -f "$HOME/.codex/skills/gsd-v1/bin/gsd-tools.cjs" ]; then
  GSD_HOME="$HOME/.codex/skills/gsd-v1"
fi
node "$GSD_HOME/bin/gsd-tools.cjs" validate health $REPAIR_FLAG
```

Parse the JSON output fields:
- `status`: `"healthy"` | `"degraded"` | `"broken"`
- `errors[]`: Critical issues (code, message, fix, repairable)
- `warnings[]`: Non-critical issues
- `info[]`: Informational notes
- `repairable_count`: Number of auto-fixable issues
- `repairs_performed[]`: Actions taken when `--repair` was used

**Cross-terminal context health (if ContextGO is available):**

```bash
for HC in \
  "$PWD/scripts/context_healthcheck.sh" \
  "$HOME/.local/share/contextgo/scripts/context_healthcheck.sh" \
  "$HOME/.codex/skills/contextgo/scripts/context_healthcheck.sh"
do
  if [ -f "$HC" ]; then
    bash "$HC" || true
    break
  fi
done
```
</step>

<step name="format_output">
**Format and display results:**

```
-----------------------------------------------------
 GSD Health Check
-----------------------------------------------------

Status: HEALTHY | DEGRADED | BROKEN
Errors: N | Warnings: N | Info: N
```

**If repairs were performed:**

```
Repairs Performed
-----------------
- config.json: Created with defaults
- STATE.md: Regenerated from roadmap
```

**If errors exist:**

```
Errors
------
[E001] config.json: JSON parse error at line 5
  Fix: Run /gsd:health --repair to reset to defaults

[E002] PROJECT.md not found
  Fix: Run /gsd:new-project to create
```

**If warnings exist:**

```
Warnings
--------
[W001] STATE.md references phase 5, but only phases 1-3 exist
  Fix: Run /gsd:health --repair to regenerate

[W005] Phase directory "1-setup" does not follow NN-name format
  Fix: Rename to match pattern (e.g. 01-setup)
```

**If informational notes exist:**

```
Info
----
[I001] 02-implementation/02-01-PLAN.md has no SUMMARY.md
  Note: May be in progress
```

**Footer (when repairable issues exist and --repair was NOT used):**

```
---
N issues can be auto-repaired. Run: /gsd:health --repair
```
</step>

<step name="offer_repair">
**If repairable issues exist and --repair was NOT used:**

Ask the user whether they want to run repairs:

```
Would you like to run /gsd:health --repair to fix N issues automatically?
```

If yes, re-run with the `--repair` flag and display results.
</step>

<step name="verify_repairs">
**If repairs were performed:**

Re-run the health check without `--repair` to confirm all issues are resolved:

```bash
CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
GSD_HOME="${GSD_HOME:-$CLAUDE_CONFIG_DIR/get-shit-done}"
if [ ! -f "$GSD_HOME/bin/gsd-tools.cjs" ] && \
   [ -f "$HOME/.codex/skills/gsd-v1/bin/gsd-tools.cjs" ]; then
  GSD_HOME="$HOME/.codex/skills/gsd-v1"
fi
node "$GSD_HOME/bin/gsd-tools.cjs" validate health
```

Report the final status.
</step>

</process>

<error_codes>

| Code | Severity | Description                              | Repairable |
|------|----------|------------------------------------------|------------|
| E001 | error    | .planning/ directory not found           | No         |
| E002 | error    | PROJECT.md not found                     | No         |
| E003 | error    | ROADMAP.md not found                     | No         |
| E004 | error    | STATE.md not found                       | Yes        |
| E005 | error    | config.json parse error                  | Yes        |
| W001 | warning  | PROJECT.md missing required section      | No         |
| W002 | warning  | STATE.md references invalid phase        | Yes        |
| W003 | warning  | config.json not found                    | Yes        |
| W004 | warning  | config.json invalid field value          | No         |
| W005 | warning  | Phase directory naming mismatch          | No         |
| W006 | warning  | Phase in ROADMAP but no directory        | No         |
| W007 | warning  | Phase on disk but not in ROADMAP         | No         |
| I001 | info     | Plan without SUMMARY (may be in progress)| No         |

</error_codes>

<repair_actions>

| Action           | Effect                              | Risk                     |
|------------------|-------------------------------------|--------------------------|
| createConfig     | Create config.json with defaults    | None                     |
| resetConfig      | Delete and recreate config.json     | Loses custom settings    |
| regenerateState  | Create STATE.md from ROADMAP        | Loses session history    |

**Not auto-repairable (too risky):**
- PROJECT.md and ROADMAP.md content
- Phase directory renaming
- Orphaned plan cleanup

</repair_actions>
