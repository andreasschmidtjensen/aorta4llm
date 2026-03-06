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
uv run python examples/obligation_demo/demo.py
```

```
  Act 1: The Architecture Gate

  BLOCK  write_file(src/auth/handler.py)
         reason: prohibition active — architecture not reviewed for scope

  Act 2: Architecture Review Lifts the Gate

  -> Achieved: architecture_reviewed('src/auth/')
  PERMIT write_file(src/auth/handler.py)

  Act 3: The Deadline Violation

  -> Obligation ACTIVATED: obliged code_reviewed(auth)
     deadline: review_deadline(auth)

  !! VIOLATION DETECTED: obliged code_reviewed(auth)
     The reviewer failed to complete review before the deadline.
```

Same file, same agent, same role — permitted after organizational state changed. Then an obligation tracked over time, a missed deadline, and a recorded violation. No JSON allowlist can do this.

## Prerequisites

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

The pure-Python engine requires no external dependencies beyond PyYAML.

## Quick start

> **New to aorta4llm?** See [Getting Started](docs/getting-started.md) for a hands-on guide covering the CLI, shorthand norm types, bash analysis, soft/hard blocks, and dry-run mode.

### 1. Install

```bash
uv pip install -e "/path/to/aorta4llm"

# With dashboard (adds Flask):
uv pip install -e "/path/to/aorta4llm[dashboard]"

```

### 2. Define your organization

Create an org spec YAML file. This example defines three roles for a feature development workflow:

```yaml
organization: feature_workflow

roles:
  architect:
    objectives:
      - system_design_complete(Feature)
    capabilities:
      - read_file
      - write_file
      - spawn_agent

  implementer:
    objectives:
      - feature_implemented(Feature)
      - tests_passing(Feature)
    capabilities:
      - read_file
      - write_file
      - execute_command

  reviewer:
    objectives:
      - code_reviewed(Feature)
    capabilities:
      - read_file

norms:
  # Implementer must not modify files outside assigned scope
  - role: implementer
    type: forbidden
    objective: write_file(Path)
    deadline: false
    condition: not(in_scope(Path, S))

  # Implementer must have tests passing before requesting review
  - role: implementer
    type: obliged
    objective: tests_passing(Feature)
    deadline: review_requested(Feature)
    condition: feature_implemented(Feature)

  # Reviewer must not modify source code
  - role: reviewer
    type: forbidden
    objective: write_file(Path)
    deadline: false
    condition: is_source_file(Path)

rules:
  - "in_scope(Path, Scope) :- current_scope(Scope), atom_concat(Scope, _, Path)."
  - "is_source_file(Path) :- atom_concat(_, '.py', Path)."
  - "is_source_file(Path) :- atom_concat(_, '.ts', Path)."
  - "feature_implemented(F) :- achieved(feature_implemented(F))."
```

### 3. Register agents and configure hooks

```bash
# Register as implementer scoped to a specific directory
uv run python -m integration.hooks register \
    --org-spec org-specs/feature_workflow.yaml \
    --agent claude-dev --role implementer --scope src/auth/
```

Add to your project's `.claude/settings.local.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "uv run python -m integration.hooks pre-tool-use --org-spec org-specs/feature_workflow.yaml --agent claude-dev"
          }
        ]
      }
    ]
  }
}
```

Every `Write`, `Edit`, and `NotebookEdit` call now goes through the governance check.

### 4. Start the dashboard (optional)

```bash
uv run python -m dashboard.server --org-spec org-specs/feature_workflow.yaml --port 5111
```

Open **http://localhost:5111** — registered agents, live permission checks, active obligations, violation tracking.

## Designing org specs

The org spec YAML follows the AORTA metamodel:

### Roles

Each role has **objectives** (what it aims to achieve) and **capabilities** (what actions it can perform):

```yaml
roles:
  my_role:
    objectives:
      - task_complete(Item)
    capabilities:
      - read_file
      - write_file
```

### Norms

Two types:

**Prohibitions** — block an action when a condition holds:

```yaml
- role: implementer
  type: forbidden
  objective: write_file(Path)
  deadline: false
  condition: not(in_scope(Path, S))
```

**Obligations** — require something to be achieved by a deadline:

```yaml
- role: implementer
  type: obliged
  objective: tests_passing(Feature)
  deadline: review_requested(Feature)
  condition: feature_implemented(Feature)
```

### Rules

Rules define how conditions are evaluated. Variables are shared between the norm's objective and condition — when a concrete action unifies with the objective pattern, variables bind throughout:

```yaml
rules:
  - "in_scope(Path, Scope) :- current_scope(Scope), atom_concat(Scope, _, Path)."
  - "is_source_file(Path) :- atom_concat(_, '.py', Path)."
  - "feature_implemented(F) :- achieved(feature_implemented(F))."
```

## How it works

1. **YAML org spec** is compiled to structured facts and rules
2. The **governance engine** (pure Python) evaluates norms deterministically — no LLM involved in enforcement
3. **Claude Code hooks** intercept tool calls, translate them to governance actions, and check permissions before execution
4. **Prohibitions with variables** (like `write_file(Path)`) are evaluated at check time — the concrete file path binds the variable, propagating through the condition
5. **Event log** (`.aorta/events.jsonl`) records all checks for the dashboard and auditing

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
dashboard/
  server.py          Flask web dashboard
  static/index.html  Dashboard UI
org-specs/           Example organizational specifications
  templates/         Templates for aorta init
examples/            Runnable demos
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
