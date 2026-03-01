# aorta4llm

Deterministic organizational governance for LLM agent systems. Enforce role-based constraints — who can write what, what must happen before review, which agents need tests passing — using formal norms backed by SWI-Prolog.

Built on the [AORTA reasoning framework](https://orbit.dtu.dk/en/publications/the-aorta-reasoning-framework) (Jensen, 2015). LLMs handle natural language and planning; Prolog handles constraint enforcement. No LLM decides whether a rule applies — the logic engine does.

## What it does

You define an **organizational specification** in YAML — roles, capabilities, prohibitions, obligations. aorta4llm compiles this to Prolog and enforces it on every tool call:

- **Prohibitions**: An implementer scoped to `src/auth/` is blocked from writing to `src/api/`. A reviewer cannot modify source files.
- **Obligations**: After implementing a feature, tests must pass before requesting review. After reviewing code, the review must be documented.
- **Scope enforcement**: Each agent is assigned a file path scope. Writes outside the scope are deterministically blocked.

All enforcement happens via Claude Code hooks — the governance check runs before each tool call and blocks or approves it.

## Prerequisites

- Python >= 3.10
- [SWI-Prolog](https://www.swi-prolog.org/): `brew install swi-prolog` (macOS) or `apt install swi-prolog` (Linux)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Quick start (try it in an existing project)

### 1. Install aorta4llm

From the aorta4llm source directory:

```bash
# With uv (recommended)
uv pip install -e "/path/to/aorta4llm[dashboard]"

# Or with pip
pip install -e "/path/to/aorta4llm[dashboard]"
```

The `[dashboard]` extra includes Flask for the live monitoring web UI. Omit it if you only need the hooks.

### 2. Define your organization

Create an org spec file in your project. This example defines three roles for a feature development workflow:

```bash
mkdir -p org-specs
```

Create `org-specs/feature_workflow.yaml`:

```yaml
organization: feature_workflow

roles:
  architect:
    objectives:
      - system_design_complete(Feature)
      - architecture_documented(Feature)
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
      - review_documented(Feature)
    capabilities:
      - read_file

dependencies:
  - role: implementer
    depends_on: architect
    for: system_design_complete(Feature)
  - role: reviewer
    depends_on: implementer
    for: feature_implemented(Feature)

norms:
  # Implementer must not modify files outside assigned scope
  - role: implementer
    type: forbidden
    objective: write_file(Path)
    deadline: false
    condition: not(in_scope(Path, AssignedScope))

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

  # Reviewer must document review before approving
  - role: reviewer
    type: obliged
    objective: review_documented(Feature)
    deadline: review_approved(Feature)
    condition: code_reviewed(Feature)

rules:
  - "in_scope(Path, Scope) :- current_scope(Scope), atom_concat(Scope, _, Path)."
  - "feature_implemented(F) :- achieved(feature_implemented(F))."
  - "code_reviewed(F) :- achieved(code_reviewed(F))."
  - "is_source_file(Path) :- atom_concat(_, '.py', Path)."
  - "is_source_file(Path) :- atom_concat(_, '.ts', Path)."
  - "is_source_file(Path) :- atom_concat(_, '.js', Path)."
  - "is_source_file(Path) :- atom_concat(_, '.jsx', Path)."
  - "is_source_file(Path) :- atom_concat(_, '.tsx', Path)."
```

### 3. Register your agent

Before starting Claude Code, register the agent with a role and scope:

```bash
# Register as implementer scoped to a specific directory
uv run python -m integration.hooks register \
    --org-spec org-specs/feature_workflow.yaml \
    --agent claude-dev \
    --role implementer \
    --scope src/auth/

# Or as architect (unrestricted writes)
uv run python -m integration.hooks register \
    --org-spec org-specs/feature_workflow.yaml \
    --agent claude-dev \
    --role architect

# Or as reviewer (can read anything, cannot write source files)
uv run python -m integration.hooks register \
    --org-spec org-specs/feature_workflow.yaml \
    --agent claude-dev \
    --role reviewer
```

State is persisted in `.aorta/state.json` — it survives across hook invocations and Claude Code sessions.

### 4. Configure Claude Code hooks

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
            "command": "uv run python -m integration.hooks pre-tool-use --org-spec org-specs/feature_workflow.yaml --agent claude-dev",
            "statusMessage": "Checking governance constraints..."
          }
        ]
      }
    ]
  }
}
```

Now every `Write`, `Edit`, and `NotebookEdit` tool call goes through the governance check before execution.

### 5. Start the dashboard (optional)

In a separate terminal:

```bash
uv run python -m dashboard.server \
    --org-spec org-specs/feature_workflow.yaml \
    --port 5111
```

Open **http://localhost:5111** to see:
- Registered agents with their roles and scopes
- Live feed of permission checks (approved/blocked)
- Active obligations and their status
- Organizational norms from your spec

### 6. Use it

Start Claude Code normally. When the agent tries to write a file:

- **In scope** → approved silently, logged to dashboard
- **Out of scope** → blocked with reason, visible in dashboard as red event

To change roles mid-session (e.g., switch from implementer to architect):

```bash
uv run python -m integration.hooks register \
    --org-spec org-specs/feature_workflow.yaml \
    --agent claude-dev \
    --role architect
```

## Designing your own org spec

The org spec YAML follows the AORTA metamodel:

### Roles

Each role has **objectives** (what the role aims to achieve) and **capabilities** (what actions the role can perform):

```yaml
roles:
  my_role:
    objectives:
      - task_complete(Item)
    capabilities:
      - read_file
      - write_file
      - execute_command
      - spawn_agent
```

### Norms

Norms are the enforcement rules. Two types:

**Prohibitions** — block an action when a condition holds:

```yaml
- role: implementer
  type: forbidden
  objective: write_file(Path)        # What's being blocked
  deadline: false                     # Never expires
  condition: not(in_scope(Path, S))  # When it applies
```

**Obligations** — require something to be achieved by a deadline:

```yaml
- role: implementer
  type: obliged
  objective: tests_passing(Feature)           # What must be achieved
  deadline: review_requested(Feature)         # By when
  condition: feature_implemented(Feature)     # When obligation activates
```

### Rules

Prolog rules that define how conditions are evaluated. Variables are shared between the norm's objective and condition through Prolog unification:

```yaml
rules:
  # Path prefix matching for scope enforcement
  - "in_scope(Path, Scope) :- current_scope(Scope), atom_concat(Scope, _, Path)."

  # File extension matching
  - "is_source_file(Path) :- atom_concat(_, '.py', Path)."

  # Link achieved objectives to conditions
  - "feature_implemented(F) :- achieved(feature_implemented(F))."
```

## Example: multi-agent feature workflow

Here's how a full feature development workflow looks with governance:

```
1. Register architect (unrestricted)
   → Designs feature, writes to docs/ and any source files
   → Achieves: system_design_complete(auth)

2. Register implementer (scope: src/auth/)
   → Writes src/auth/*.py — approved
   → Tries src/api/routes.py — BLOCKED (out of scope)
   → Achieves: feature_implemented(auth)
   → Obligation activates: must achieve tests_passing(auth) before review
   → Achieves: tests_passing(auth) — obligation fulfilled

3. Register reviewer (read-only for source)
   → Reads any file — approved
   → Tries to edit src/auth/login.py — BLOCKED (source file prohibition)
   → Writes docs/review.md — approved (non-source file)
   → Achieves: code_reviewed(auth)
   → Obligation: must achieve review_documented(auth)
```

Run the built-in demo to see this in action:

```bash
uv run python examples/three_role_demo/demo.py
```

## Project structure

```
governance/
  prolog/            Prolog rules (metamodel, NC phase, OG phase)
  compiler.py        YAML org specs → Prolog facts
  engine.py          pyswip wrapper, permission checking
  service.py         High-level API
  tests/             pytest test suite (61 tests)
integration/
  hooks.py           Claude Code hook handlers + CLI
  events.py          JSONL event logger
dashboard/
  server.py          Flask web dashboard
  static/index.html  Dashboard UI
org-specs/           Example organizational specifications
examples/            Runnable demos
```

## Resetting state

```bash
# Clear all agent registrations and events
rm -rf .aorta/

# Re-register agents as needed
uv run python -m integration.hooks register ...
```

## How it works

1. **YAML org spec** is compiled to Prolog facts (`role/2`, `cap/2`, `cond/5`, etc.)
2. **SWI-Prolog** (via pyswip) evaluates norms deterministically — no LLM involved in enforcement
3. **Claude Code hooks** intercept tool calls, translate them to governance actions, and check permissions
4. **Prohibitions with variables** (like `write_file(Path)`) are evaluated at check time — the concrete file path unifies with `Path`, binding it in the condition
5. **Event log** (`.aorta/events.jsonl`) records all checks for the dashboard and auditing

## Reference

- Jensen, A. S. (2015). *The AORTA Reasoning Framework — Adding Organizational Reasoning to Agents.* PhD thesis, DTU. PHD-2015-372.
- See [DESIGN.md](DESIGN.md) for the full architecture, metamodel definition, and API spec.
