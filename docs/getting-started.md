# Getting Started with aorta4llm

## Install

```bash
uv pip install -e "/path/to/aorta4llm"
```

## One-command setup

In your project directory:

```bash
aorta init --template safe-agent --scope src/
```

This creates:
- `.aorta/safe-agent.yaml` — the org spec (governance rules), with `scope` paths set to your `--scope`
- `.claude/settings.local.json` — Claude Code hook configuration
- `.aorta/events.jsonl` — event log (project-local)
- Registers agent `agent` with scope `src/`

Multiple scopes are supported:

```bash
aorta init --template safe-agent --scope src/ tests/
```

The `safe-agent` template uses `protected` norms for `.env` and `secrets/`, so Read/Glob/Grep tools are automatically hooked. Use `--strict` to hook reads even without `protected` norms.

Available templates (`aorta init --list-templates`):
- **safe-agent** — single agent scoped to a directory
- **test-gate** — must pass tests before committing

## Customize the org spec

Edit `.aorta/safe-agent.yaml`. Here's a full example showing all features:

```yaml
organization: my-project

roles:
  agent:
    objectives: [task_complete]
    capabilities: [read_file, write_file, execute_command]

norms:
  # Keep writes inside src/ and tests/
  - type: scope
    role: agent
    paths: [src/, tests/]

  # Protect sensitive files from both reading and writing
  - type: protected
    role: agent
    paths: [".env", ".env.local", "secrets/", "*.key", "*.pem"]

  # Block writes to config (readable but not writable)
  - type: readonly
    role: agent
    paths: ["config/", "**/*.secret"]

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

# Mark tests_passing when pytest succeeds; reset when files change
achievement_triggers:
  - tool: Bash
    command_pattern: pytest
    exit_code: 0
    marks: tests_passing
    reset_on_file_change: true

# Analyze bash commands for hidden file writes
bash_analysis: true

# Commands that skip bash analysis (read-only, fast path)
safe_commands: ["pytest", "git status", "git diff", "git log", "npm test"]

# How long a soft block retry window lasts (default: 60)
soft_block_window: 30
```

**Note:** `.aorta/` and `.claude/` are always protected — the hook engine
hard-blocks writes to governance infrastructure regardless of your org spec.

## Norm types

| Type | What it blocks | Key fields |
|------|---------------|------------|
| `scope` | `write_file` outside allowed directories | `paths` (list) |
| `protected` | `read_file` AND `write_file` matching path prefixes or globs | `paths` (list) |
| `readonly` | `write_file` matching path prefixes or globs | `paths` (list) |
| `forbidden_command` | `execute_command` containing a substring | `command_pattern`, optional `severity` |
| `required_before` | `execute_command` until an achievement exists | `command_pattern`, `requires` |
| `obliged` | Creates an obligation with a deadline | `objective`, `condition`, `deadline` |
| `forbidden` | Creates a prohibition with a condition | `objective`, `condition` |

Any norm can have `severity: soft` (confirmation-required) or `severity: hard` (default, always denied).

## Soft vs hard blocks

- **Hard block**: action denied. The agent sees the reason and cannot proceed.
- **Soft block**: action denied with a "SOFT BLOCK" message that instructs the agent to ask the user for confirmation. If the agent retries the exact same action within the configured window, the retry is approved automatically.

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
  --agent agent --role agent --scope src/

# Test a bash command
aorta dry-run --org-spec .aorta/safe-agent.yaml \
  --bash-command "cp src/a.py /tmp/leak.py" \
  --agent agent --role agent --scope src/
```

## How it works at runtime

When Claude Code calls a tool (Write, Edit, Bash, etc.):

1. **Hook fires** — settings.local.json routes matched tools to `aorta hook pre-tool-use`
2. **Self-protection** — writes to `.aorta/` and `.claude/` are always denied
3. **Agent check** — unregistered agents are denied (fail-closed)
4. **Permission check** — the engine checks the action against active norms
5. **Bash analysis** (if enabled) — for Bash commands that pass:
   - Fast path: known safe commands skip analysis
   - Heuristic: regex extracts write paths from `cp`, `mv`, `>`, `tee`, `rm`, etc.
   - LLM fallback: ambiguous commands (variable expansion, complex pipes) go to Haiku (~5s)
   - Each extracted write path is checked against file-write norms
6. **Block or approve** — hard blocks deny; soft blocks prompt for confirmation

## Check governance state

```bash
aorta status --org-spec .aorta/safe-agent.yaml
```

Shows registered agents, active norms, achievements, and recent activity (approved/blocked counts, last blocked actions). Add `--json` for machine-readable output.

## Reset state

```bash
aorta reset --org-spec .aorta/safe-agent.yaml
```

Clears registered agents, achievements, and events. Use `--keep-events` to preserve the event log. Agents must be re-registered afterward.

## Watch (live event tail)

Monitor governance events in real-time from a separate terminal:

```bash
aorta watch --org-spec .aorta/safe-agent.yaml
```

Shows blocks, approvals, registrations, and achievements as they happen. Useful during a Claude Code session.

## Re-initializing

Running `aorta init` when aorta hooks already exist will exit with an error. Use `--reinit` to overwrite:

```bash
aorta init --template safe-agent --scope src/ tests/ --reinit
```

Non-aorta hooks (e.g., your own linters) are always preserved — only aorta hooks are replaced. `--reinit` also clears allow-once exceptions and soft block state.

## One-time exceptions

When a hook blocks an action, the block message includes an `allow-once` command hint. To grant a one-time exception:

```bash
aorta allow-once --org-spec .aorta/safe-agent.yaml --path .env
```

The next access to `.env` will be approved; subsequent accesses are blocked again. Use `--agent <name>` to restrict the exception to a specific agent in multi-agent setups.

## Explain (debugging)

To understand why an action is allowed or blocked:

```bash
aorta explain --org-spec .aorta/safe-agent.yaml \
  --tool Write --path config/db.yml --agent agent --role agent --scope "src/"
```

Shows each norm, whether it applies, and why it matches or doesn't.

## Limitations

- **No content governance**: aorta blocks *writing to* `.env` but cannot prevent the agent from *reading* a file and pasting its contents elsewhere. Use `protected` norms to block reads of truly sensitive files.
- **Bash escape hatch**: an agent can construct commands that evade heuristic detection (e.g., `python -c "open('x','w')..."`). LLM analysis catches most of these but isn't bulletproof.
- **No filesystem monitoring**: governance only sees tool calls, not side effects.
- **LLM analysis latency**: ~5s per ambiguous bash command. The heuristic pre-filter handles ~80% of patterns instantly.
