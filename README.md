# aorta4llm — Deterministic Governance for Claude Code

You tell Claude Code "don't touch .env" in a system prompt. It works — until context compaction drops that instruction, or the agent reasons itself into an exception. System prompts are suggestions. Tool calls are actions.

aorta4llm enforces governance at the tool call layer. A YAML spec declares what the agent can read, write, and execute. A logic engine checks every tool call deterministically — no LLM decides whether a rule applies. The agent cannot modify its own governance, cannot disable the hooks, and cannot prompt its way past a hard block.

## The problem

**Prompts aren't enforcement.** Telling an agent "do not modify source files" in a system prompt is a suggestion. The agent can still call the Write tool. Context window pressure, long conversations, and ambiguous instructions make compliance unreliable.

**Static allowlists can't express conditions.** "Commit is allowed only after tests pass" requires runtime state tracking. A JSON config of `{allowed_tools: ["Write"]}` cannot express this.

**There's no audit trail.** When the agent writes to the wrong file or runs a destructive command, you find out after the fact. There's no record of what was blocked, what was approved, or what the agent tried to do.

## What this does

- **File access control**: Declare which paths are read-write, read-only, or no-access. Writes outside scope are deterministically blocked — not suggested against, blocked.
- **Self-protection**: The agent cannot edit its own governance config (`.aorta/`) or hook configuration (`.claude/`). Cannot run `aorta reset` or `aorta init` via Bash.
- **Sensitive content warnings**: When the agent reads a read-only file (e.g. `config/`), a governance notice tells it not to hardcode values. In testing, Claude refused to embed a database password and offered runtime loading instead.
- **Bash analysis**: Shell commands are analyzed for hidden file writes (`cp`, `mv`, `>`, `tee`). `cp src/app.py /tmp/leak.py` is blocked even though the Bash tool itself is allowed.
- **Conditional enforcement**: "Commit only after tests pass" with automatic achievement tracking. Tests pass → achievement granted → commit unlocked → file change → achievement reset.
- **Soft blocks**: Git commit/push are soft-blocked — the agent must ask the user for confirmation before proceeding. Guards against post-compaction hallucinated commits.
- **Audit trail**: Every check (approved or blocked) is logged to `.aorta/events.jsonl`. Monitor in real-time with `aorta watch`.
- **Guardrails & sanctions**: Detect thrashing (repeated failures, file rewrites), escalate violations, auto-hold after configurable thresholds.
- **Achievement workflows**: Counts-as rules (`tests_passing + spec_valid = quality_verified`), cascading dependencies, obligation chains.
- **Context injection**: Governance rules injected into agent context at session start via SessionStart hook. Agent knows the rules before its first action.
- **Conversation replay**: Validate policies against real session traces with `aorta replay`. See what your policy would have done during yesterday's session.
- **Hook timing**: Measure governance overhead with `aorta timing`. Shows per-hook latency stats (avg, p50, p95, max) with init/handle breakdown.
- **Policy visualization**: Tree view (`aorta status --tree`), dependency graph (`aorta status --graph`), live dashboard (`aorta watch --dashboard`).
- **Policy packs**: Composable policy modules. `include: [tool-hygiene]` adds norms from a shared pack.

## See it in action

```bash
aorta init --template safe-agent --scope src/ tests/
```

Then in Claude Code:

```
> Create src/models/task.py with a Task dataclass     → APPROVED (in scope)
> Create a README.md at the project root               → BLOCKED (outside scope)
> Read .env                                            → BLOCKED (protected)
> Run pytest                                           → APPROVED
> git commit -m 'feat: add task model'                 → SOFT BLOCK (user must confirm)
```

Every block is deterministic — the engine decides, not the LLM. Soft blocks require one retry to confirm intent.

## Prerequisites

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

The pure-Python engine requires no external dependencies beyond PyYAML.

## Quick start

> **New to aorta4llm?** See [Getting Started](docs/getting-started.md) for a hands-on guide covering the CLI, shorthand norm types, bash analysis, soft/hard blocks, and dry-run mode.

### 1. Install

```bash
uv tool install git+https://github.com/andreasschmidtjensen/aorta4llm.git
```

### 2. Initialize governance

```bash
# One-command setup — creates org spec, hooks, and registers the agent
aorta init --template safe-agent --scope src/ tests/
```

This creates `.aorta/safe-agent.yaml` and `.claude/settings.local.json` with hooks configured. Every `Write`, `Edit`, `Read`, and `Bash` call now goes through the governance check.

### 3. Org spec anatomy

The access map is the primary interface for file governance. Norms handle command-level rules:

```yaml
organization: safe_agent
bash_analysis: true
allow_memory: true   # allow agent to write to Claude Code memory

roles:
  agent:
    objectives: [task_complete]
    capabilities: [read_file, write_file, execute_command]

# File access — read-write, read-only, or no-access per path
access:
  src/:       read-write    # agent can read and write
  tests/:     read-write
  config/:    read-only     # agent can read but not write
  .env:       no-access     # agent cannot read or write
  .env.local: no-access
  secrets/:   no-access

# Command-level governance
norms:
  - type: forbidden_command
    role: agent
    command_pattern: "git commit"
    severity: soft           # agent must ask user before committing

  - type: forbidden_command
    role: agent
    command_pattern: "git push"
    severity: soft
```

Manage access from the CLI:

```bash
aorta access docs/ read-only
aorta protect "*.key" "*.pem"    # shorthand for no-access
```

### 4. Templates

- **safe-agent** — Single agent with scope, protected paths, readonly paths, soft-blocked git operations
- **test-gate** — Like safe-agent, plus `git commit` is hard-blocked until `pytest` passes (achievement triggers)
- **minimal** — Scope-only, no norms or bash analysis

## How it works

1. **YAML org spec** is compiled to structured facts and rules
2. The **governance engine** (pure Python) evaluates norms deterministically — no LLM involved in enforcement
3. **Claude Code hooks** intercept tool calls, translate them to governance actions, and check permissions before execution
4. **Prohibitions with variables** (like `write_file(Path)`) are evaluated at check time — the concrete file path binds the variable, propagating through the condition
5. **Event log** (`.aorta/events.jsonl`) records all checks for auditing and `aorta watch`
6. **SessionStart hook** injects governance context (access map, rules, achievement gates) into the agent's context before its first action

## Project structure

```
src/aorta4llm/
  governance/
    terms.py           Term representation, parser, unification
    evaluator.py       Condition evaluator, fact database
    py_engine.py       Governance engine
    engine_types.py    Data types
    compiler.py        YAML org specs -> facts/rules
    service.py         High-level API
    validator.py       Org spec schema validation
    bash_analyzer.py   Heuristic + LLM bash command analysis
  integration/
    hooks.py           Claude Code hook handlers + CLI
    events.py          JSONL event logger
  cli/
    main.py            Unified CLI entry point
    cmd_init.py        Project scaffolding from templates
    cmd_validate.py    Org spec validation
    cmd_dry_run.py     Test governance checks offline
    cmd_context.py     LLM-friendly governance summary
    cmd_replay.py      Session trace replay
    cmd_status.py      State display, tree view, dependency graph
    cmd_watch.py       Live event tail and dashboard
    cmd_reset.py       State reset with auto-re-registration
  replay/
    trace_parser.py    Parse Claude Code session JSONL traces
    engine.py          Replay engine for policy validation
  org-specs/
    templates/         Templates for aorta init (safe-agent, test-gate)
    packs/             Composable policy modules (tool-hygiene)
tests/
  governance/          Engine, compiler, terms, service tests
  integration/         Hook, access map, soft block tests
  cli/                 CLI command tests
  replay/              Trace parser and replay engine tests
docs/
  getting-started.md   Hands-on setup and usage guide
  index.html           Documentation site
```

## Architecture and theory

aorta4llm applies the [AORTA organizational reasoning framework](https://orbit.dtu.dk/en/publications/the-aorta-reasoning-framework) (Jensen, 2015) to LLM agent systems. The hybrid architecture uses LLMs for natural language understanding and planning, while a logic engine handles deterministic constraint enforcement.

The governance cycle has three phases:

- **NC (Norm Check)**: Activates obligations when conditions are met, fulfills them when objectives are achieved, violates them when deadlines pass. Activates and expires prohibitions.
- **OG (Option Generation)**: Generates options from active norms, violations, and dependency relations — used for system prompt injection.
- **AE (Action Execution)**: Delegated to the LLM agent, which selects actions informed by organizational context.

**Permissions are derived, not stored.** An action is permitted unless an active prohibition blocks it. This follows Section 4.1 of Jensen's dissertation.

**Variable sharing** between norm objectives and conditions is the core mechanism. When the engine checks `write_file('src/auth/login.py')` against a prohibition on `write_file(Path)`, unification binds `Path` to `'src/auth/login.py'`, which propagates through the condition `not(in_scope(Path, Scope))`. The engine implements this with structural unification in pure Python.

See [DESIGN.md](DESIGN.md) for the full architecture, metamodel definition, and API spec.

## Reference

- Jensen, A. S. (2015). *The AORTA Reasoning Framework — Adding Organizational Reasoning to Agents.* PhD thesis, DTU. PHD-2015-372.
