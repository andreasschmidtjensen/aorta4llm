# AORTA-LLM: Organizational Reasoning for LLM Agent Systems

## Background

This project applies the AORTA reasoning framework (Jensen, 2015 — "The AORTA Reasoning Framework: Adding Organizational Reasoning to Agents", DTU PhD thesis) to LLM-based multi-agent systems, specifically Claude Code and its sub-agents.

AORTA was designed to enrich BDI agents with organizational reasoning — roles, obligations, permissions, norms — in a way that is founded in logic, decoupled from the cognitive agent, and generic across agent platforms. The original implementation targeted Prolog-like agent programming languages (Jason, 2APL, AIL) and was implemented in Java with tuProlog.

LLM agents have the same fundamental problem AORTA was designed to solve: in multi-agent systems, individual agent autonomy creates uncertainty. Current approaches to constraining LLM sub-agents (markdown system prompts, ad-hoc tool restrictions) provide no formal guarantees and no verifiable properties. AORTA provides the formal governance layer that's missing.

## Core Thesis

LLM agents are subsymbolic — you cannot extract a belief base or verify internal reasoning. But every meaningful action manifests as a **tool call** (file read, file write, shell command, sub-agent spawn). These are discrete, inspectable events that a logic-based governance layer can reason about.

The architecture is therefore a **hybrid**: LLMs handle natural language understanding, flexible plan generation, and code synthesis. AORTA handles deterministic constraint enforcement, obligation tracking, and organizational goal decomposition.

## Architecture

```
┌─────────────────────────────────────────────┐
│           Organizational Specification       │
│         (YAML → compiled to Prolog facts)    │
└──────────────────┬──────────────────────────┘
                   │
        ┌──────────▼──────────┐
        │   Governance Service │
        │   (Python + pyswip)  │
        │                      │
        │  ┌────┐ ┌────┐ ┌────┐│
        │  │ NC │→│ OG │→│ AE ││
        │  └────┘ └────┘ └────┘│
        │                      │
        │  SWI-Prolog engine    │
        │  (in-process via      │
        │   pyswip)             │
        └──────────┬───────────┘
                   │ local API (stdio/HTTP)
        ┌──────────▼──────────┐
        │   Claude Code        │
        │   Integration        │
        │                      │
        │  Tool call hooks:    │
        │   before_tool_call() │
        │   after_tool_call()  │
        │                      │
        │  Sub-agent hooks:    │
        │   on_spawn()         │
        │   on_complete()      │
        └──────────────────────┘
```

## AORTA Metamodel (from the dissertation)

The organizational specification is built from these predicates (Definition 4.7):

| Predicate | Meaning |
|---|---|
| `role(R, Os)` | Role R with objectives Os |
| `obj(O, S)` | Objective O with sub-objectives S |
| `dep(R1, R2, O)` | Role R1 depends on R2 for objective O |
| `rea(A, R)` | Agent A enacts role R |
| `cond(R, Deon, O, D, C)` | Conditional norm for role R: Deon ∈ {obliged, forbidden}, objective O, deadline D, condition C |
| `norm(A, R, Deon, O, D)` | Active norm for agent A in role R |
| `viol(A, R, Deon, O)` | Agent A in role R violated norm concerning O |

## Reasoning Cycle (three phases)

Following Chapter 5 of the dissertation:

### NC (Norm Check)
Activates, fulfills, or violates norms based on current state. Key transition rules:

- **Obl-Act**: If agent enacts role R, condition C holds, and objective O is not yet achieved → activate norm
- **Obl-Sat**: If norm is active and objective O is achieved → remove norm (fulfilled)
- **Obl-Viol**: If norm is active, objective O not achieved, and deadline D reached → record violation
- **Pro-Act/Pro-Exp/Pro-Viol**: Symmetric rules for prohibitions

### OG (Option Generation)
Generates organizational options based on current state:

- **Enact**: Role is an option if agent has capability for ≥1 objective
- **Deact**: Role deactment is an option if all objectives fulfilled
- **Objective/Norm/Violation**: Active norms and violations become options
- **Delegate/Inform**: Dependency relations generate coordination options

### AE (Action Execution)
Applies reasoning rules of the form `option : context → action` to select and execute one action.

## Reinterpretation for LLM Agents

### Capabilities
In BDI agents: states the agent can achieve, derived from plan libraries.
In LLM agents: **the set of tools available to the sub-agent**. Enumerable, concrete.

```prolog
% Example capability mapping
cap(implementer, write_file).
cap(implementer, read_file).
cap(implementer, execute_command).
cap(reviewer, read_file).
% reviewer cannot write files
```

### Mental State
In BDI agents: belief base + goal base.
In LLM agents: the system prompt context + conversation history. The governance layer doesn't need to model this — it observes **actions** (tool calls) and **state** (filesystem, declared objectives).

### commit(O) and drop(O)
In BDI agents: adds/removes from goal base.
In LLM agents: injects/removes objectives from the sub-agent's system prompt context, and tracks commitment in the governance layer.

### Permissions (derived, not stored)
Following the dissertation's approach (Section 4.1): permissions are implicit. An action is permitted if no active prohibition blocks it. This is the right default for LLM agents — allow everything unless explicitly constrained by role-bound norms.

## Organizational Specification Format

YAML input, compiled mechanically to Prolog facts. The YAML mirrors the metamodel structure from the dissertation's Table 6.1 but in a more accessible format.

```yaml
# org-spec.yaml
organization: code_review_workflow

roles:
  architect:
    objectives:
      - system_design_complete(Project)
      - architecture_documented(Project)
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
      # notably: no write_file, no execute_command

dependencies:
  - role: implementer
    depends_on: architect
    for: system_design_complete(Project)
  - role: reviewer
    depends_on: implementer
    for: feature_implemented(Feature)

norms:
  # Implementer must not modify files outside assigned scope
  - role: implementer
    type: forbidden
    objective: write_file(Path)
    deadline: false  # permanent
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

  # Reviewer is obliged to document review before approving
  - role: reviewer
    type: obliged
    objective: review_documented(Feature)
    deadline: review_approved(Feature)
    condition: code_reviewed(Feature)

rules:
  # Domain-specific Prolog rules (counts-as rules)
  - "in_scope(Path, Scope) :- atom_concat(Scope, _, Path)."
  - "is_source_file(Path) :- atom_concat(_, '.py', Path)."
  - "is_source_file(Path) :- atom_concat(_, '.ts', Path)."
  - "is_source_file(Path) :- atom_concat(_, '.js', Path)."
```

## Governance Service API

Python service exposing three endpoints. Communication via stdio (JSON lines) for simplest Claude Code integration, with optional HTTP for debugging/testing.

### `check_permission(agent, role, action, params) → {permitted: bool, reason?: string}`

Called **before** a tool call is executed. Runs the NC phase to update norm states, then checks whether any active prohibition blocks the action.

```json
// Request
{
  "method": "check_permission",
  "agent": "impl-agent-1",
  "role": "implementer",
  "action": "write_file",
  "params": {"path": "src/auth/login.py", "scope": "src/api/"}
}

// Response (denied)
{
  "permitted": false,
  "reason": "prohibition active: write_file(src/auth/login.py) blocked — path not in assigned scope src/api/",
  "violation": "viol(impl-agent-1, implementer, forbidden, write_file(src/auth/login.py))"
}
```

### `notify_action(agent, role, action, params, result) → {norms_changed: [...]}`

Called **after** a tool call succeeds. Updates the governance state (beliefs), triggers NC to check for norm fulfillment/activation, returns any norm state changes.

```json
// Request
{
  "method": "notify_action",
  "agent": "impl-agent-1",
  "role": "implementer",
  "action": "execute_command",
  "params": {"command": "pytest tests/"},
  "result": {"success": true, "output": "all tests passed"}
}

// Response
{
  "norms_changed": [
    {
      "type": "fulfilled",
      "norm": "norm(impl-agent-1, implementer, obliged, tests_passing(auth_feature), review_requested(auth_feature))"
    }
  ]
}
```

### `get_obligations(agent, role) → {obligations: [...], options: [...]}`

Returns current active obligations and generated options for the agent. Used to inject organizational context into the sub-agent's system prompt.

```json
// Request
{
  "method": "get_obligations",
  "agent": "impl-agent-1",
  "role": "implementer"
}

// Response
{
  "obligations": [
    {
      "objective": "tests_passing(auth_feature)",
      "deadline": "review_requested(auth_feature)",
      "status": "active"
    }
  ],
  "options": [
    {"type": "delegate", "to_role": "architect", "objective": "system_design_complete(auth)"},
    {"type": "norm", "norm": "obliged", "objective": "tests_passing(auth_feature)"}
  ]
}
```

## Project Structure

```
aorta-llm/
├── DESIGN.md                  # This file
├── README.md
├── governance/                # Python governance service
│   ├── __init__.py
│   ├── service.py             # Main service (stdio JSON-lines API)
│   ├── compiler.py            # YAML → Prolog fact compiler
│   ├── engine.py              # pyswip wrapper, NC/OG/AE phases
│   ├── prolog/
│   │   ├── metamodel.pl       # Base metamodel predicates and rules
│   │   ├── nc.pl              # Norm check transition rules
│   │   ├── og.pl              # Option generation transition rules
│   │   └── ae.pl              # Action execution transition rules
│   └── tests/
│       ├── test_compiler.py
│       ├── test_engine.py
│       └── test_service.py
├── integration/               # Claude Code integration
│   └── hooks.py               # Tool call interception layer
├── org-specs/                 # Example organizational specifications
│   └── code_review.yaml
└── examples/                  # Example usage scenarios
    └── three_role_demo/
```

## Implementation Plan

### Phase 1: Governance kernel (vertical slice)
1. YAML → Prolog compiler for the metamodel predicates
2. Prolog implementation of NC phase (norm activation, fulfillment, violation)
3. `check_permission` endpoint with prohibition checking
4. Single test case: implementer blocked from writing outside scope

**Success criteria**: Given an org spec with one prohibition norm, `check_permission` correctly blocks a tool call that violates it and permits one that doesn't.

### Phase 2: Full reasoning cycle
5. OG phase (option generation from active norms, roles, dependencies)
6. `notify_action` endpoint with state updates
7. `get_obligations` endpoint
8. Obligation lifecycle: activation → tracking → fulfillment/violation

**Success criteria**: An obligation (e.g., "tests must pass before review") is activated, tracked, and correctly detected as fulfilled or violated.

### Phase 3: Claude Code integration
9. Tool call interception hooks for Claude Code
10. Sub-agent spawn hooks (role assignment on spawn)
11. System prompt injection from `get_obligations`
12. End-to-end demo: architect → implementer → reviewer workflow

**Success criteria**: A three-agent workflow executes with organizational constraints enforced deterministically, producing an execution trace that can be compared against the same workflow without AORTA.

### Phase 4: Extensions (future)
- Contrary-to-duty norms (sanctions as norm-triggered norms)
- Delegation via dependency relations
- Consequence-based decision making (Part IV of dissertation)
- Execution trace verification

## What We Deliberately Omit (for now)

- **Groups**: The dissertation notes this as a limitation (Section 12.2.3). Not needed for the PoC.
- **Consequence-based decision making** (Part IV): The QDT model is elegant but adds complexity. LLM sub-agents don't have competing personal desires in the same way BDI agents do.
- **Model checking** (Chapter 8): Valuable long-term, not for PoC.
- **Custom AORTA programming language**: The dissertation's DSL for reasoning rules (Table 6.3) is replaced by Prolog rules directly in the org spec. Less accessible but more powerful and avoids building a parser.
- **Artifact-based coordination** (Section 12.2.1): Interesting future direction, not needed now.
- **Constitutive norms** (counts-as rules): Partially supported via the `rules` section in the org spec. Full counts-as support is future work.

## References

- Jensen, A. S. (2015). "The AORTA Reasoning Framework — Adding Organizational Reasoning to Agents". PhD thesis, DTU. PHD-2015-372.
- Original Java implementation: https://github.com/andreasschmidtjensen/aorta
- Key formal definitions: Chapter 4 (metamodel, norm semantics), Chapter 5 (operational semantics, transition rules)
