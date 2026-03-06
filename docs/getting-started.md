# Getting Started with aorta4llm

## Install

```bash
uv pip install -e "/path/to/aorta4llm"
```

## One-command setup

In your project directory:

```bash
aorta init --template safe-agent --scope src/ --agent dev
```

This creates:
- `.aorta/safe-agent.yaml` — the org spec (governance rules)
- `.claude/settings.local.json` — Claude Code hook configuration
- Registers agent `dev` with scope `src/`

Available templates (`aorta init --list-templates`):
- **safe-agent** — single agent scoped to a directory
- **test-gate** — must pass tests before committing
- **review-gate** — reviewer cannot modify source files

## Customize the org spec

Edit `.aorta/safe-agent.yaml`. Here's a full example showing all features:

```yaml
organization: my-project

roles:
  agent:
    objectives: [task_complete]
    capabilities: [read_file, write_file, execute_command]

norms:
  # Keep writes inside src/
  - type: forbidden_outside
    role: agent
    path: src/

  # Block writes to sensitive paths
  - type: forbidden_paths
    role: agent
    paths: [".env", "secrets/", ".claude/"]

  # Require tests before committing
  - type: required_before
    role: agent
    command_pattern: "git commit"
    requires: tests_passing

  # Soft-block git operations (agent must confirm with user)
  - type: forbidden_command
    role: agent
    command_pattern: "git commit"
    severity: soft

  - type: forbidden_command
    role: agent
    command_pattern: "git push"
    severity: soft

  # Hard-block destructive operations (never allowed)
  - type: forbidden_command
    role: agent
    command_pattern: "git reset --hard"

# Mark tests_passing when pytest succeeds
achievement_triggers:
  - tool: Bash
    command_pattern: pytest
    exit_code: 0
    marks: tests_passing

# Analyze bash commands for hidden file writes
bash_analysis: true

# Commands that skip bash analysis (read-only, fast path)
safe_commands: ["pytest", "git status", "git diff", "git log", "npm test"]

# How long a soft block retry window lasts (default: 60)
soft_block_window: 30
```

## Norm types

| Type | What it blocks | Key fields |
|------|---------------|------------|
| `forbidden_outside` | `write_file` outside a directory | `path` |
| `forbidden_paths` | `write_file` matching path prefixes | `paths` (list) |
| `forbidden_command` | `execute_command` containing a substring | `command_pattern`, optional `severity` |
| `required_before` | `execute_command` until an achievement exists | `command_pattern`, `requires` |
| `obliged` | Creates an obligation with a deadline | `objective`, `condition`, `deadline` |
| `forbidden` | Creates a prohibition with a condition | `objective`, `condition` |

Any norm can have `severity: soft` (confirmation-required) or `severity: hard` (default, always denied).

## Soft vs hard blocks

- **Hard block**: action denied. The agent sees the reason and cannot proceed.
- **Soft block**: action denied with "CONFIRMATION REQUIRED" message. If the user tells the agent to proceed and it retries within the configured window, the retry is approved.

This guards against post-compaction hallucination — if the agent tries to commit based on stale context, it gets blocked and must ask the user.

## Validate your spec

```bash
aorta validate .aorta/safe-agent.yaml
```

Checks for missing fields, undefined roles, invalid norm types, and broken references.

## Dry-run (test without a live session)

```bash
# Test a file write
aorta dry-run --org-spec .aorta/safe-agent.yaml \
  --tool Write --path config/secret.py \
  --agent dev --role agent --scope src/

# Test a bash command
aorta dry-run --org-spec .aorta/safe-agent.yaml \
  --bash-command "cp src/a.py /tmp/leak.py" \
  --agent dev --role agent --scope src/
```

## How it works at runtime

When Claude Code calls a tool (Write, Edit, Bash, etc.):

1. **Hook fires** — settings.local.json routes matched tools to the governance hook
2. **Permission check** — the engine checks the action against active norms
3. **Bash analysis** (if enabled) — for Bash commands that pass:
   - Fast path: known safe commands skip analysis
   - Heuristic: regex extracts write paths from `cp`, `mv`, `>`, `tee`, `rm`, etc.
   - LLM fallback: ambiguous commands (variable expansion, complex pipes) go to Haiku (~5s)
   - Each extracted write path is checked against file-write norms
4. **Block or approve** — hard blocks deny; soft blocks prompt for confirmation

## Dashboard

```bash
uv run python -m dashboard.server --org-spec .aorta/safe-agent.yaml --port 5111
```

Open http://localhost:5111. Shows permission checks, bash analysis events, norm changes, and per-agent detail. Events are tagged with `org_spec` for filtering across projects.

## Multi-agent setup

For projects with multiple agents:

```bash
uv run python -m integration.hooks register \
  --org-spec .aorta/workflow.yaml \
  --agent impl-1 --role implementer --scope src/auth/

uv run python -m integration.hooks register \
  --org-spec .aorta/workflow.yaml \
  --agent rev-1 --role reviewer
```

## Limitations

- **Bash escape hatch**: an agent can construct commands that evade heuristic detection (e.g., `python -c "open('x','w')..."`). LLM analysis catches most of these but isn't bulletproof.
- **No filesystem monitoring**: governance only sees tool calls, not side effects.
- **Soft block cache is per-session**: each hook invocation is a fresh process. The soft block retry mechanism works within a single Claude Code session.
- **LLM analysis latency**: ~5s per ambiguous bash command. The heuristic pre-filter handles ~80% of patterns instantly.
