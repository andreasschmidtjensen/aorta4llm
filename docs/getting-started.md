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
- `.aorta/safe-agent.yaml` — the org spec (governance rules), with `access` map entries set from your `--scope`
- `.claude/settings.local.json` — Claude Code hook configuration
- `.aorta/state.json` — event-sourced state (project-local)
- `.aorta/events.jsonl` — event log for `aorta watch`
- `.claude/commands/aorta-permissions.md` and `aorta-status.md` — slash commands for agent introspection
- Registers agent `agent` with scope `src/`

Multiple scopes are supported:

```bash
aorta init --template safe-agent --scope src/ tests/
```

The `safe-agent` template uses `no-access` entries for `.env`, `secrets/`, `*.key`, `*.pem`, and `*.secret`, so Read/Glob/Grep tools are automatically hooked. Use `--strict` to hook reads even without `no-access` entries.

Available templates (`aorta template list`):
- **safe-agent** — single agent scoped to a directory
- **test-gate** — must pass tests before committing
- **minimal** — scope-only, no norms or bash analysis (built-in)

## Customize the org spec

Edit `.aorta/safe-agent.yaml`. The `access` map is the primary way to control file access:

```yaml
organization: my-project

roles:
  agent:
    objectives: [task_complete]
    capabilities: [read_file, write_file, execute_command]

# Access map — the primary interface for file-level governance.
# read-write: agent can read and write
# read-only:  agent can read but not write
# no-access:  agent cannot read or write
access:
  src/:       read-write
  tests/:     read-write
  .env:       no-access
  .env.local: no-access
  secrets/:   no-access
  "*.key":    no-access
  "*.pem":    no-access
  "*.secret": no-access
  config/:    read-only

# Norms — for command-level governance and advanced rules.
norms:
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

## Access map

The `access` map is the recommended way to control file access:

| Level | Effect |
|-------|--------|
| `read-write` | Agent can read and write (also sets the write scope) |
| `read-only` | Agent can read but writes are blocked |
| `no-access` | Both reads and writes are blocked |

Manage from the CLI:

```bash
aorta access src/ read-write
aorta access .env no-access
aorta protect .ssh/           # shorthand for no-access
aorta readonly config/        # shorthand for read-only
```

## Sensitive content warnings

When the agent reads a file marked `read-only` or `no-access` (via allow-once), a PostToolUse hook injects a governance notice into the conversation:

> **[GOVERNANCE NOTICE]** 'config/settings.yaml' is marked as sensitive (read-only or no-access). Do NOT copy, embed, or hardcode specific values from this file in any code you write. Use environment variable lookups or placeholder values instead.

This is a prompt-level signal, not enforcement — aorta controls *where* the agent writes, not *what* it writes. But the contextual warning at the exact moment of reading significantly reduces the chance of accidental credential leakage into source code.

The warning fires automatically for any path in the `access` map with `read-only` or `no-access` level. No additional configuration needed.

## Norm types

For command-level governance and advanced rules, use explicit norms:

| Type | What it blocks | Key fields |
|------|---------------|------------|
| `forbidden_command` | `execute_command` containing a substring | `command_pattern`, optional `severity` |
| `required_before` | `execute_command` until an achievement exists | `command_pattern`, `requires` |
| `obliged` | Creates an obligation with a deadline | `objective`, `condition`, `deadline` |
| `forbidden` | Creates a prohibition with a condition | `objective`, `condition` |

Any norm can have `severity: soft` (confirmation-required) or `severity: hard` (default, always denied).

For file-level access control, use the `access` map (see above) — it's the recommended interface. The `norms:` list is for command-level governance and conditional rules.

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
aorta dry-run --tool Write --path config/secret.py \
  --agent agent --role agent --scope src/

# Test a bash command
aorta dry-run --bash-command "cp src/a.py /tmp/leak.py" \
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

## Safe commands (bash analysis fast path)

When `bash_analysis` is enabled, every Bash command goes through write-path extraction. Commands on the `safe_commands` list skip this entirely — no heuristic, no LLM call, zero latency.

The default list covers common read-only commands:

```yaml
safe_commands: ["pytest", "git status", "git diff", "git log", "npm test"]
```

If you use custom test runners or build tools, add them to avoid the ~5s LLM analysis hit:

```yaml
safe_commands:
  - pytest
  - git status
  - git diff
  - git log
  - npm test
  - cargo test
  - make check
  - jest
```

Edit `safe_commands` directly in your `.aorta/<template>.yaml`. These are prefix-matched: `"pytest"` covers `pytest tests/ -v`, `"git status"` covers `git status --short`, etc.

## Slash commands

`aorta init` creates slash commands that the agent can use during a Claude Code session:

- `/project:aorta-permissions` — show effective permissions
- `/project:aorta-status` — show governance state

These run read-only `aorta` commands. The agent can also run `aorta status`, `aorta permissions`, `aorta explain`, `aorta validate`, and `aorta doctor` directly via Bash — read-only aorta commands are allowed. Only mutating commands (`init`, `reset`, `allow-once`, etc.) are blocked.

## Check governance state

```bash
aorta status
```

Shows registered agents, active norms, achievements, and recent activity (approved/blocked counts, last blocked actions). Add `--json` for machine-readable output.

All commands auto-detect the org spec from `.aorta/`. Use `--org-spec` to override when you have multiple specs.

## Check effective permissions

```bash
aorta permissions
```

Shows the access map with actual read/write status, command restrictions, achievements, and self-protection rules.

## Reset state

```bash
aorta reset
```

Clears registered agents, achievements, and events. Use `--keep-events` to preserve the event log. Agents must be re-registered afterward.

## Watch (live event tail)

Monitor governance events in real-time from a separate terminal:

```bash
aorta watch
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
aorta allow-once .env
```

The next access to `.env` will be approved; subsequent accesses are blocked again. Use `--agent <name>` to restrict the exception to a specific agent in multi-agent setups.

## Explain (debugging)

To understand why an action is allowed or blocked:

```bash
aorta explain --tool Write --path config/db.yml --agent agent --role agent --scope "src/"
```

Shows each norm, whether it applies, and why it matches or doesn't.

## Troubleshooting

### Hooks not working (actions not blocked)

Claude Code treats hook errors as non-blocking — if the `aorta` binary
isn't found or the hook crashes, actions proceed silently. Run:

    aorta doctor

Common causes:
- `aorta` not on PATH (install with `pip install aorta4llm` or `uv pip install aorta4llm`)
- Stale hooks after reinstall (run `aorta init --reinit`)
- Agent not registered (check `aorta status`)

## Limitations

- **No content governance**: aorta blocks *writing to* `.env` but cannot prevent the agent from *reading* a file and pasting its contents elsewhere. Use `no-access` to block reads of truly sensitive files. For files the agent needs to read but shouldn't leak (e.g. `config/`), the sensitive content warning provides a prompt-level nudge but not hard enforcement.
- **Bash escape hatch**: an agent can construct commands that evade heuristic detection (e.g., `python -c "open('x','w')..."`). LLM analysis catches most of these but isn't bulletproof.
- **No filesystem monitoring**: governance only sees tool calls, not side effects.
- **LLM analysis latency**: ~5s per ambiguous bash command. The heuristic pre-filter handles ~80% of patterns instantly.
