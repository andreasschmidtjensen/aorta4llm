# aorta4llm — Deterministic Governance for AI Agent Teams

When you deploy multiple LLM agents, you need to control who can do what. System prompts are suggestions — agents can ignore them. Tool allowlists are static — they can't express "allowed after the architect approves." And nobody tracks whether the reviewer actually reviewed.

aorta4llm moves governance outside the context window. An organizational spec defines roles, prohibitions, and obligations. A logic engine enforces them deterministically on every tool call — no LLM decides whether a rule applies.

## The problem

**Prompts aren't enforcement.** Telling an agent "you are a reviewer, do not modify source files" in a system prompt is a suggestion. The agent can still call the Write tool on a `.py` file. Context window pressure, long conversations, and ambiguous instructions make compliance unreliable.

**Static allowlists can't express conditions.** "The implementer may write to `src/auth/` but only after the architecture has been reviewed" requires runtime state. A JSON config of `{allowed_tools: ["Write"]}` cannot express this.

**Nobody tracks obligations.** "The reviewer must complete their review before the deadline" is a real organizational requirement. When it's violated, you need an auditable record — not a hope that the agent followed instructions.

## What this does

- **Conditional prohibitions**: An implementer scoped to `src/auth/` is blocked from writing to `src/api/`. A reviewer cannot modify `.py/.ts/.js` files. An implementer cannot write code until the architecture is reviewed. All enforced before the tool call executes.
- **Obligations with deadlines**: After implementing a feature, tests must pass before requesting review. After accepting a review, the reviewer must document findings before the deadline. The system tracks activation, fulfillment, and violation.
- **Violation detection**: When a deadline is reached and the obligation is unfulfilled, a formal violation is recorded. Queryable, auditable, actionable.
- **Scope enforcement**: Each agent is assigned a file path scope. Writes outside scope are deterministically blocked — not suggested against, blocked.

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
uv pip install -e "/path/to/aorta4llm"
```

### 2. Initialize governance

```bash
# One-command setup — creates org spec, hooks, and registers the agent
aorta init --template safe-agent --scope src/ tests/
```

This creates `.aorta/safe-agent.yaml` and `.claude/settings.local.json` with hooks configured. Every `Write`, `Edit`, `Read`, and `Bash` call now goes through the governance check.

### 3. Org spec anatomy

The generated spec uses shorthand norm types that compile to the underlying engine:

```yaml
organization: safe_agent
bash_analysis: true

roles:
  agent:
    objectives: [task_complete]
    capabilities: [read_file, write_file, execute_command]

norms:
  # Block writes outside assigned scope
  - role: agent
    type: scope
    paths: [src/, tests/]

  # Block reads AND writes to secrets
  - role: agent
    type: protected
    paths: [.env, .env.local, secrets/]

  # Block writes to config (reads still allowed)
  - role: agent
    type: readonly
    paths: [config/]

  # Soft-block git commit/push (agent must confirm with user)
  - role: agent
    type: forbidden_command
    command_pattern: "git commit"
    severity: soft
```

### 4. Templates

- **safe-agent** — Single agent with scope, protected paths, readonly paths, soft-blocked git operations
- **test-gate** — Like safe-agent, plus `git commit` is hard-blocked until `pytest` passes (achievement triggers)

## How it works

1. **YAML org spec** is compiled to structured facts and rules
2. The **governance engine** (pure Python) evaluates norms deterministically — no LLM involved in enforcement
3. **Claude Code hooks** intercept tool calls, translate them to governance actions, and check permissions before execution
4. **Prohibitions with variables** (like `write_file(Path)`) are evaluated at check time — the concrete file path binds the variable, propagating through the condition
5. **Event log** (`.aorta/events.jsonl`) records all checks for auditing and `aorta watch`

## Project structure

```
governance/
  terms.py           Term representation, parser, unification
  evaluator.py       Condition evaluator, fact database
  py_engine.py       Governance engine
  engine_types.py    Data types
  compiler.py        YAML org specs -> facts/rules
  service.py         High-level API
  validator.py       Org spec schema validation
  bash_analyzer.py   Heuristic + LLM bash command analysis
  tests/             pytest test suite
integration/
  hooks.py           Claude Code hook handlers + CLI
  events.py          JSONL event logger
cli/
  main.py            Unified CLI (aorta init, validate, dry-run)
  cmd_init.py        Project scaffolding from templates
  cmd_validate.py    Org spec validation
  cmd_dry_run.py     Test governance checks offline
org-specs/
  templates/         Templates for aorta init (safe-agent, test-gate)
docs/
  getting-started.md Hands-on setup and usage guide
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
