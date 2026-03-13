# Extending aorta4llm — Guardrails for Increased Determinism

## Motivation

LLMs are non-deterministic black boxes. aorta4llm currently enforces governance at the **tool-call boundary** — PreToolUse blocks forbidden actions, PostToolUse tracks achievements and warns about sensitive content. This is powerful but narrow.

The AORTA framework (Jensen, 2015) defines a much richer set of organizational concepts — sanctions, constitutive norms, obligation lifecycles, delegation — that map to new guardrails. This document captures ideas for extensions that move more decisions from the LLM into the deterministic engine.

### Current coverage

| Hook | What it does | AORTA concept |
|------|-------------|---------------|
| PreToolUse | Block writes outside scope | Prohibition (conditional) |
| PreToolUse | Block reads of protected files | Prohibition (ground) |
| PreToolUse | Block/soft-block commands | Prohibition + severity |
| PreToolUse | Require achievement before command | Obligation (required_before) |
| PreToolUse | Bash write-path analysis | Prohibition (derived) |
| PostToolUse | Achievement triggers | Obligation fulfillment |
| PostToolUse | Sensitive content warning | Prompt-level nudge |
| System prompt | Obligation injection | OG phase (partial) |

---

## Prerequisite: Source layout and refactoring

Before adding extensions, the codebase needs one structural change:

### ~~Move to `src/` layout~~ (done)

Source moved to `src/aorta4llm/`. All imports use `aorta4llm.*` prefix. Entry point unchanged (`aorta` binary).

### Hook state extensibility

The current `GovernanceHook` stores state in a flat dict (`events`, `soft_blocks`, `exceptions`). Several extensions need per-session counters (write counts per file, command retry counts, directory spread). The state model needs a lightweight extension — likely a `session_counters` dict in state.json that resets on `aorta reset`.

---

## Extension 1: Post-write content validation

**Problem:** aorta controls *where* the agent writes but not *what* it writes. An agent that reads `.env` (via allow-once) and then writes those credentials into `src/config.py` is not caught.

**Approach:** After `Write`/`Edit` completes, a PostToolUse hook reads the written file and checks for sensitive content. Two strategies:

1. **Property matching** (recommended first step): When a `no-access` or `read-only` file is read (via allow-once), extract its key-value pairs. After subsequent writes, check if those values appear in the written content. This works for `.env` files, YAML configs, JSON secrets — anything with identifiable values.

2. **Heuristic patterns** (second step): Regex for API keys, passwords, connection strings (`AKIA[0-9A-Z]{16}`, `password\s*=\s*["']`, etc.). Configurable per project.

**When content is detected:** Trigger an **obligation** rather than just warning. This connects to the obligation enforcement mechanism (see below).

**Open question: How to enforce obligations?**

When a post-write check detects leaked content, it creates an obligation (e.g., `remove_leaked_secret`). But how do we ensure the agent follows through?

Proposed mechanism: **obligation gate on specific commands**. A new norm type:

```yaml
norms:
  - type: required_before
    role: agent
    command_pattern: "git commit"
    requires: all_obligations_fulfilled
```

The engine checks that no active obligations exist before allowing the gated command. This is a natural extension of `required_before` — instead of requiring a specific achievement, it requires the absence of unfulfilled obligations.

This pattern composes well: post-write checks create obligations, obligation gates block commits/pushes until obligations are resolved, and the agent must fix the issue to proceed.

---

## Extension 2: Behavioral budgets

**Problem:** A confused agent can thrash — rewriting files repeatedly, running dozens of failing commands, modifying files across the repo.

**Concern:** LLM behavior is inherently variable. Four edits vs. one edit for the same logical change. A `for` loop vs. individual commands. Hard thresholds will produce false positives.

**Verdict:** Interesting but risky as a standalone feature. Better used as a **trigger for obligations** rather than a hard block. See Extension 7 (sanctions) — budget exceedance triggers an obligation to justify/confirm with the user rather than blocking outright.

If implemented, should be opt-in with generous defaults and always soft-block severity.

---

## Extension 3: Scope drift detection

**Problem:** Agent asked to "fix the login bug" gradually expands to refactoring database, tests, CI. Scope creep is invisible until the PR is 40 files.

**Concern:** Same false-positive risk as budgets. Sometimes fixing a bug legitimately requires touching many directories.

**Approach:** Track distinct top-level directories modified. When threshold exceeded, create an obligation to confirm scope with user (soft, not blocking). Combines well with the obligation gate from Extension 1 — the agent must acknowledge scope expansion before committing.

---

## Extension 4: Thrashing detection

**Problem:** Agent writes file, tests fail, rewrites same file, tests fail again — loop. Or retries a failing bash command repeatedly.

**This is the clearest signal.** Unlike budgets (Extension 2), repeated failure is unambiguous — a bash command failing 3 times with the same exit code is never intentional.

**Approach:**
- Track write count per file path in session state
- Track bash command retries (same command prefix + same exit code)
- On threshold, trigger obligation: "This command has failed N times. Ask the user for help before retrying."

The obligation gate (Extension 1) ensures the agent can't just ignore this — it must resolve the obligation before gated commands proceed.

---

## Extension 5: Tool output redaction

**Problem:** Binary choice between no-access and read-only. Sometimes you want the agent to read a config file but not see passwords.

**Status:** Needs PoC to determine feasibility. The current governance notice (PostToolUse stderr) is prompt-level — the agent already has the full content in its context.

**Investigation needed:**
- Can PostToolUse modify the tool result that the agent sees, or only append?
- If PostToolUse can't redact, can PreToolUse intercept a Read and replace it with a filtered version?
- Alternative: a custom MCP tool that wraps Read with redaction, used instead of the native Read tool.

**If feasible:** This is the strongest possible guardrail for sensitive content — the secret never enters the context window. No amount of reasoning can extract what was never provided.

---

## Extension 6: Counts-as rules (constitutive norms)

**Problem:** Achievement triggers are limited to "tool X + exit code Y marks objective Z." Real workflows have richer transitions: "tests pass AND review complete counts as ready-to-merge."

**AORTA mapping:** Constitutive norms (Chapter 3 of thesis). "X counts as Y in context C." These define what states *mean*, separate from regulative norms that define what agents *must/must not* do.

**Approach:** Formalize as a new section in org specs:

```yaml
counts_as:
  - when: [tests_passing, code_reviewed]
    marks: ready_to_merge
  - when: [model_created]
    creates_obligation:
      role: agent
      objective: migration_created
      deadline: git_commit  # must exist before commit
```

The engine evaluates counts-as rules after each state change. When all `when` conditions are met, the `marks` achievement is granted or the obligation is created.

**This enables powerful workflows:** "If a model file is created (`model_created` trigger from Extension 8), the agent has an obligation to also create a migration file (`migration_created`). This obligation must be fulfilled before committing (obligation gate)."

State transitions are logic-driven, not LLM-decided.

---

## Extension 7: Sanctions and violation cascades

**Problem:** Norm violations are logged but have no consequences. The agent doesn't know it violated, and there's no repair mechanism.

**Clarification:** For hard-blocked actions (write outside scope), the action is *prevented* — there's no violation to sanction because the write never happens. Sanctions apply to:
- **Soft-block overrides** — agent confirmed a soft-blocked action but shouldn't have
- **Obligation violations** — agent failed to fulfill an obligation by its deadline
- **Detected-after-the-fact issues** — post-write content validation finds leaked secrets (Extension 1)
- **Behavioral threshold breaches** — thrashing detected (Extension 4), budget exceeded (Extension 2), scope drift (Extension 3)

**Approach:** Sanctions are triggered obligations. When a violation is recorded, secondary norms activate:

```yaml
sanctions:
  - on_violation: leaked_secret
    then:
      - type: obliged
        objective: remove_leaked_content
  - on_violation_count: 3
    then:
      - type: forbidden
        objective: write_file(Path)
        severity: hard
        message: "Too many violations. Writes blocked until user intervenes."
```

Combined with the obligation gate, this creates accountability: violation → obligation → must resolve before committing.

---

## Extension 8: Richer achievement triggers

**Problem:** Current triggers only match tool name + command pattern + exit code. Can't express "file was written to path matching X" or "output contains error."

**Approach:** Extend triggers with new match conditions:

```yaml
achievement_triggers:
  # Existing: tool + command + exit code
  - tool: Bash
    command_pattern: pytest
    exit_code: 0
    marks: tests_passing
    reset_on_file_change: true

  # New: write path pattern
  - tool: Write
    path_pattern: "src/models/*.py"
    marks: model_created

  # New: negative trigger (clears achievement)
  - tool: Bash
    output_contains: "FAIL|error|Exception"
    clears: tests_passing

  # New: write path pattern for migrations
  - tool: Write
    path_pattern: "migrations/*.py"
    marks: migration_created
```

This is the lowest-effort, highest-leverage extension of what already exists. Combined with counts-as rules (Extension 6), it enables: "model file written → `model_created` → counts-as triggers obligation for `migration_created` → blocked from committing until migration exists."

---

## Extension 9: Checkpoint/rollback

**Problem:** Before destructive operations, there's no safety net.

**Approach:** Express through obligations rather than new syntax:

```yaml
norms:
  - type: required_before
    role: agent
    command_pattern: "git rebase"
    requires: checkpoint_created

achievement_triggers:
  - tool: Bash
    command_pattern: "git stash"
    exit_code: 0
    marks: checkpoint_created
    reset_on_file_change: false
```

This reuses existing `required_before` mechanics — no new syntax needed. The agent must run `git stash` before `git rebase` is allowed. The obligation gate ensures compliance.

---

## Extension 10: Workflow phases

**Problem:** Agents don't follow structured workflows. They write code before understanding the problem, or push before testing.

**Approach:** Express through obligations and counts-as, not a separate state machine:

```yaml
counts_as:
  # Phase transitions via achievement composition
  - when: [problem_understood]
    marks: phase_implement
  - when: [implementation_complete]
    marks: phase_test
  - when: [tests_passing]
    marks: phase_commit

norms:
  # Write is forbidden until implement phase
  - type: forbidden
    role: agent
    objective: write_file(Path)
    condition: not(achieved(phase_implement))

  # Commit forbidden until test phase complete
  - type: required_before
    role: agent
    command_pattern: "git commit"
    requires: tests_passing
```

The phases are defined by achievement composition. Moving backward (test → implement) happens naturally when `tests_passing` is cleared by `reset_on_file_change` — the agent re-enters implement phase because the achievement is gone.

**Open question:** How does the agent mark `problem_understood` or `implementation_complete`? Options:
- Manual: user runs `aorta mark problem_understood`
- Tool-based: reading N files triggers `problem_understood`
- Agent-declared: agent runs a read-only `aorta mark` command (would need to be allowed)

---

## Unifying theme: Obligation-driven governance

Several extensions converge on the same pattern:

1. **Something happens** (content leak detected, thrashing, scope drift, file created)
2. **Obligation is created** (fix the leak, ask for help, confirm scope, create migration)
3. **Obligation gate blocks progression** (can't commit/push until obligations resolved)
4. **Violation triggers sanctions** if obligation isn't fulfilled

This is the full AORTA norm lifecycle: activation → fulfillment or violation → sanctions. The engine manages it deterministically.

The key new mechanism is the **obligation gate** — a `required_before` variant that checks `all_obligations_fulfilled` rather than a specific achievement. This is what ensures the agent follows through on obligations.

---

## Implementation order

| Priority | Extension | Depends on |
|----------|-----------|------------|
| ~~0~~ | ~~`src/` layout refactor~~ | done |
| 1 | Richer triggers (#8) | — |
| 2 | Obligation gate (`all_obligations_fulfilled`) | — |
| 3 | Counts-as rules (#6) | #8 |
| 4 | Thrashing detection (#4) | #2 (creates obligations) |
| 5 | Post-write content validation (#1) | #2 (creates obligations) |
| 6 | Sanctions (#7) | #2 |
| 7 | Workflow phases (#10) | #6, #8 |
| 8 | Scope drift (#3) | #2 |
| 9 | Tool output redaction PoC (#5) | — |
| 10 | Behavioral budgets (#2) | #7 (sanctions) |
| 11 | Checkpoint/rollback (#9) | #8 (just uses existing required_before) |

The obligation gate (#2) is the lynchpin — most extensions create obligations, and the gate is what makes them enforceable.

---

## Codebase readiness

### What works well
- The engine's term/unification system is solid and extensible
- The compiler cleanly maps YAML → facts/rules
- The hook system has clear PreToolUse/PostToolUse separation
- Event sourcing provides a natural place to add session counters

### What needs attention
- **`src/` layout**: Required for dogfooding and cleaner scope management
- **Hook state**: Needs a `session_counters` dict for tracking write counts, command retries, directory spread per session
- **Obligation lifecycle**: The engine supports obligations but the hook layer doesn't create them dynamically. Need a path from PostToolUse checks → obligation creation → obligation gate enforcement
- **`counts_as` evaluation**: New evaluator capability — after each state change, check if any counts-as rules now fire. Should be a new phase between NC and OG, or part of NC.
- **`all_obligations_fulfilled` predicate**: New built-in predicate for the evaluator. Checks that no `norm(Agent, Role, obliged, _, _)` facts exist.

### Recommended refactoring before extensions
1. Move to `src/` layout (enables dogfooding)
2. Add `session_counters` to hook state model
3. Add a `create_obligation` method to GovernanceService (currently obligations only come from spec compilation, not runtime events)
4. Add `all_obligations_fulfilled` as a built-in evaluator predicate
