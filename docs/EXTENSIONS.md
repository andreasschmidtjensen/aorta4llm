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
| PreToolUse | Block all actions when held | Hold (hard gate) |
| PostToolUse | Achievement triggers (path, output, negative) | Obligation fulfillment |
| PostToolUse | Sensitive content warning | Prompt-level nudge |
| PostToolUse | Guardrails (failure rate, budgets) | Monitoring + sanctions |
| PreToolUse | Violation tracking + sanctions | Sanctions (escalation) |
| System prompt | Obligation injection | OG phase (partial) |

---

## Prerequisite: Source layout and refactoring

Before adding extensions, the codebase needs one structural change:

### ~~Move to `src/` layout~~ (done)

Source moved to `src/aorta4llm/`. All imports use `aorta4llm.*` prefix. Entry point unchanged (`aorta` binary).

### ~~Hook state extensibility~~ (done)

State model extended for guardrails: `action_ring` (ring buffer for failure rate), `hold` (active hold), `file_write_counts` (per-file write counts), `bash_command_count` (cumulative). All reset by `aorta continue`.

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

## Extension 2: Behavioral budgets (done)

**Problem:** A confused agent can thrash — rewriting files repeatedly, running dozens of failing commands, modifying files across the repo.

**Concern:** LLM behavior is inherently variable. Four edits vs. one edit for the same logical change. A `for` loop vs. individual commands. Hard thresholds will produce false positives.

Implemented as part of the unified guardrails system (see Extension 4). Each budget check (`files_modified`, `bash_commands`) is configurable as either `hold` (hard gate) or `warning` (stderr nudge). Counters reset when user runs `aorta continue`.

```yaml
guardrails:
  files_modified:
    threshold: 15
    action: warning       # or hold
  bash_commands:
    threshold: 50
    action: warning
```

---

## Extension 3: Scope drift detection

**Problem:** Agent asked to "fix the login bug" gradually expands to refactoring database, tests, CI. Scope creep is invisible until the PR is 40 files.

**Concern:** Same false-positive risk as budgets. Sometimes fixing a bug legitimately requires touching many directories.

**Approach:** Track distinct top-level directories modified. When threshold exceeded, create an obligation to confirm scope with user (soft, not blocking). Combines well with the obligation gate from Extension 1 — the agent must acknowledge scope expansion before committing.

---

## Extension 4: Thrashing detection (done)

**Problem:** Agent writes file, tests fail, rewrites same file, tests fail again — loop. Or retries a failing bash command repeatedly. Or flails through a sequence of wrong approaches, alternating between failures and irrelevant actions. The common thread is a high failure rate, not necessarily consecutive failures.

**This is the clearest signal.** Unlike budgets (Extension 2), a high failure rate is unambiguous — an agent that fails 50% of its recent commands is lost, regardless of whether the failures are consecutive.

Implemented as part of a unified guardrails system alongside behavioral budgets (#2). All checks share one config section and each is independently configurable as `hold` (hard gate, user clears with `aorta continue`) or `warning` (stderr nudge).

```yaml
guardrails:
  window_size: 10
  failure_rate:
    threshold: 0.5
    action: hold          # high failure rate = agent is stuck
  per_file_rewrites:
    threshold: 3
    action: hold          # same file rewritten N times = thrashing
  files_modified:
    threshold: 15
    action: warning       # budget: total unique files
  bash_commands:
    threshold: 50
    action: warning       # budget: total bash commands
```

**How it works:**
- PostToolUse tracks actions in a ring buffer (for failure rate) and cumulative counters (for budgets)
- When a threshold is breached, the configured action fires: `hold` sets a hard gate in state.json that PreToolUse checks before any other check; `warning` emits a stderr message like the sensitive content notice
- `aorta continue` clears the hold AND resets all counters, so budgets become periodic checkpoints rather than one-time caps
- `aorta status --tree` shows active holds
- Agents cannot clear holds (blocked from running `aorta` commands)

**Also fixed:** Piped commands (`pytest | tail -20`) have unreliable exit codes (shell reports the last command's exit code). Achievement triggers that depend on `exit_code` now skip piped commands.

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

**Done.** Implemented in `hooks.py` (`_evaluate_counts_as`), validated in `validator.py`, formatted in `cmd_watch.py`. Key behaviors:
- Cascading evaluation: A+B→C, C→D fires in a single loop until stable.
- Cascading invalidation: when `reset_on_file_change` clears a dependency, derived counts-as marks are also cleared recursively.
- Deduplication: marks and obligations are only created once; re-firing triggers after reset correctly re-creates them.

---

## Extension 7: Sanctions and violation cascades (done)

**Problem:** Norm violations are logged but have no consequences. The agent doesn't know it violated, and there's no repair mechanism.

Implemented with violation tracking and configurable sanction rules:

- **Violations**: Hard blocks and confirmed soft blocks increment a violation counter. Each violation is logged as an event.
- **Sanctions**: `on_violation_count: N` rules fire when the counter reaches the threshold. Consequences: `obliged` (create obligation) or `hold` (activate hold, blocking all actions).
- **Reset**: Violation count resets when a sanction fires (clean slate after consequences), and on `aorta continue` / `aorta reset`.
- Named violations (`on_violation: leaked_secret`) deferred until typed violation sources exist (e.g., post-write content validation).

```yaml
sanctions:
  - on_violation_count: 3
    then:
      - type: obliged
        objective: review_approach
  - on_violation_count: 5
    then:
      - type: hold
        message: "Too many violations. Review with user."
```

Combined with the obligation gate, this creates accountability: violation → obligation → must resolve before committing.

---

## Extension 8: Richer achievement triggers (done)

**Problem:** Current triggers only match tool name + command pattern + exit code. Can't express "file was written to path matching X" or "output contains error."

Implemented with three new match conditions that compose freely with existing ones:

- **`path_pattern`** — glob match on the file path (Write/Edit/Read). Absolute paths are normalized to project-relative before matching.
- **`output_contains`** — regex match on Bash stdout (`tool_response.stdout`). Can combine with `command_pattern` and/or `exit_code`.
- **`clears`** — negative trigger, alternative to `marks`. Removes a previously achieved objective and its persisted events. Re-blocks any gate that depends on it.

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

Combined with counts-as rules (Extension 6), this enables: "model file written → `model_created` → counts-as triggers obligation for `migration_created` → blocked from committing until migration exists."

---

## Extension 9: Checkpoint/rollback (done — configuration pattern, no new code)

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

## Extension 11: Custom block messages for tool redirection

**Problem:** When a `forbidden_command` norm blocks or soft-blocks a Bash command, the agent sees a generic block reason derived from the term representation (e.g., `execute_command(Cmd) is forbidden`). This tells the agent *what* was blocked but not *why* or *what to do instead*. The agent may retry, work around, or just give up.

**Motivating example:** Claude Code has dedicated tools (Grep, Glob, Read, Edit, Write) that are better than their Bash equivalents (`grep`, `find`, `cat`, `sed`). An org spec can forbid these Bash commands, but without guidance the agent doesn't know what to use instead. A custom message turns a block into a redirect:

```yaml
norms:
  - type: forbidden_command
    role: agent
    command_pattern: "^\\s*grep\\b"
    severity: soft
    message: "Use the Grep tool instead of running grep via Bash"

  - type: forbidden_command
    role: agent
    command_pattern: "^\\s*find\\b"
    severity: soft
    message: "Use the Glob tool instead of running find via Bash"

  - type: forbidden_command
    role: agent
    command_pattern: "^\\s*(cat|head|tail)\\b"
    severity: soft
    message: "Use the Read tool instead of running cat/head/tail via Bash"

  - type: forbidden_command
    role: agent
    command_pattern: "^\\s*(sed|awk)\\b"
    severity: soft
    message: "Use the Edit tool instead of running sed/awk via Bash"
```

**Approach:** Add an optional `message` field to all norm types. When present, the message is included in the block reason returned to the agent via the hook's stderr output. The compiler stores messages as additional facts:

```yaml
# Compiled fact (strawman)
block_message(agent, execute_command(Cmd), str_contains(Cmd, 'grep'), 'Use the Grep tool instead')
```

The hook layer looks up `block_message` facts when a block fires and appends the message to the governance notice.

**Current state:** `_compile_forbidden_command` in [compiler.py](../src/aorta4llm/governance/compiler.py) has no `message` field support. Block reasons are auto-generated from the term representation. Adding `message` support requires:
1. Compiler: emit `block_message` facts when `message` is present
2. Validator: accept `message` as an optional field on norm types
3. Hook layer: look up and include messages in block notices

**Scope:** While the motivating example is tool redirection, custom messages are useful for any norm. A `protected` path block could say "This file contains production credentials — use the staging config instead." A `required_before` block could say "Run the linter first."

**Pattern design for tool redirection.** A naive `\bgrep\b` pattern matches `grep` anywhere in the command — including legitimate pipeline usage like `git log | grep feat | sort`, where the Grep tool can't substitute. To reduce false positives, patterns should match only when the tool is the *primary command*, not a pipeline filter:

```yaml
# Matches "grep -r pattern src/" but not "git log | grep feat"
command_pattern: "^\\s*grep\\b"
```

Matching the first token handles the 90% case. Grep-as-pipeline-filter is a legitimate Bash use; grep-as-primary-command almost always has a built-in equivalent. The same logic applies to `find`, `cat`, `sed`, etc. — when they appear after `|`, the agent is likely using them as part of a larger workflow that has no single-tool equivalent.

**Determinism gain:** The redirect itself is deterministic — the engine decides both that the action is blocked and what the agent should do instead. No LLM reasoning needed to figure out the alternative.

---

## Extension 12: Policy packs and configuration management

**Problem:** As extensions are added, the org spec YAML grows. A project that wants tool hygiene, secret protection, thrashing detection, and scope drift ends up with 50+ lines of boilerplate norms. Most of this is generic — not project-specific. Configuration becomes overwhelming and error-prone.

**Approach: Built-in policy packs.** Ship curated norm bundles with the engine. Users reference them by name:

```yaml
organization: my_project
include:
  - tool-hygiene       # soft-blocks grep/find/cat/sed → built-in tools
  - secret-protection   # no-access for .env, *.key, *.pem + secret patterns
  - thrash-guard        # thrashing + budget detection with sane defaults

# Project-specific config only
access:
  src/:    read-write
  tests/:  read-write

norms:
  - type: forbidden_command
    role: agent
    command_pattern: "git push"
    severity: soft
```

Packs expand to concrete norms at compile time. The compiler resolves `include` before processing norms, merging pack norms with user norms. User norms take precedence when there's a conflict.

**Example packs shipped with the engine:**

| Pack | What it includes |
|------|-----------------|
| `tool-hygiene` | Soft-blocks Bash equivalents of built-in tools with redirect messages (#11) |
| `secret-protection` | `no-access` for common secret files, post-write secret pattern checks (#1) |
| `thrash-guard` | Thrashing detection (#4) + behavioral budgets (#2) with generous defaults |
| `git-safety` | Soft-blocks commit/push, requires test pass before commit |
| `scope-guard` | Scope drift detection (#3) with configurable directory spread |

**Overrides.** Users can override individual settings from a pack:

```yaml
include:
  - thrash-guard

# Override the default max_rewrites from the pack
thrashing_detection:
  max_rewrites_per_file: 5   # default in pack is 3
```

**Layered files.** Multiple YAML files in `.aorta/` that merge together. A base policy shared across repos, plus project-specific overrides:

```yaml
# .aorta/base.yaml (shared, perhaps from a team template)
include:
  - tool-hygiene
  - secret-protection

# .aorta/project.yaml (project-specific)
extends: base.yaml
access:
  src/:    read-write
```

**`aorta policy` command.** Shows the effective merged configuration — all packs expanded, all layers merged, all norms listed with their source. Answers "what's actually active?" without reading multiple files:

```
$ aorta policy
Source: pack:tool-hygiene
  [soft] forbidden_command: \bgrep\b → "Use the Grep tool instead"
  [soft] forbidden_command: \bfind\b → "Use the Glob tool instead"

Source: pack:secret-protection
  [hard] no-access: .env, .env.local, *.key, *.pem

Source: .aorta/project.yaml
  [rw]   access: src/, tests/
  [soft] forbidden_command: git push
```

Extending `aorta explain` to show "this norm came from pack X" or "this norm was overridden by file Y" closes the loop on debuggability.

**Implementation:**
1. Packs are YAML files shipped in `org-specs/packs/` alongside existing templates
2. Compiler resolves `include` first, loading and merging pack YAML before compiling
3. Merge strategy: user norms append to pack norms; user access overrides pack access for the same path; user settings override pack settings for the same key
4. `aorta policy` walks the merge chain and prints effective config with provenance

**Relationship to templates:** Templates (`aorta init --template`) scaffold a starting config. Packs are referenced at runtime and always resolve to their latest version. A template might *include* packs in the generated YAML, but packs and templates are independent concepts.

---

## Extension 13: Policy visualization (TUI) (level 1 done, level 2 done, level 3 done)

**Problem:** As org specs grow — access maps, norms, achievements, packs, counts-as rules — understanding the effective policy requires reading YAML and mentally compiling it. Users need a quick way to see what's active, what's blocking what, and where things stand.

**Three levels of visualization, each building on the last:**

### Level 1: Tree view (`aorta status --tree`)

Static snapshot of the compiled policy. No dependencies, just box-drawing characters:

```
safe_agent
├── Role: agent
│   ├── Objectives: task_complete
│   └── Capabilities: read, write, execute
├── Access
│   ├── src/        read-write
│   ├── tests/      read-write
│   ├── config/     read-only
│   └── .env        no-access
├── Norms
│   ├── [soft] git commit        ← forbidden_command
│   ├── [soft] git push          ← forbidden_command
│   ├── [hard] write outside scope
│   └── [hard] requires tests_passing before git commit
├── Achievements
│   ├── ○ tests_passing    (pytest exit 0, resets on file change)
│   └── ○ task_complete
└── Packs: tool-hygiene, secret-protection
```

Filled `●` for achieved, empty `○` for not-yet-achieved. Reads achievement state from `.aorta/state.json`. This is the natural output for `aorta policy` (#12) — same data, structured as a tree rather than a flat list.

### Level 2: Live dashboard (`aorta watch --dashboard`)

Extends the existing `aorta watch` event stream into a split-panel view:

```
┌─ Policy ─────────────────────┬─ Events (live) ──────────────────┐
│ Access                       │ 14:02:01 Write src/app.py  ALLOW │
│  src/       rw               │ 14:02:03 Read .env         BLOCK │
│  tests/     rw               │ 14:02:05 Bash: pytest      ALLOW │
│  .env       no-access        │ 14:02:05 ● tests_passing         │
│                              │ 14:02:08 Bash: git commit  ALLOW │
│ Norms                        │ 14:02:10 Write src/app.py  ALLOW │
│  [soft] git commit           │ 14:02:10 ○ tests_passing (reset) │
│  [soft] git push             │                                   │
├─ Achievements ───────────────┤                                   │
│ ● tests_passing              │                                   │
│ ○ task_complete              │                                   │
└──────────────────────────────┴───────────────────────────────────┘
```

Left panel is the static policy (updates when achievements change). Right panel is the live event stream. Achievements toggle between `●`/`○` in real-time as triggers fire and resets occur.

### Level 3: Dependency graph (`aorta status --graph`)

For complex policies with counts-as rules (#6), obligation chains, and workflow phases (#10). Shows causal relationships:

```
tests_passing ──┐
                ├──→ ready_to_merge ──→ unlocks: git push
code_reviewed ──┘

model_created ──→ obliged: migration_created ──→ gates: git commit
```

Answers "why is my commit blocked?" by tracing the chain visually. Most useful for debugging, less for day-to-day monitoring.

**Implementation:**
- Level 1: Pretty-print the compiled spec + state. No new dependencies — box-drawing characters and terminal width detection. Ships alongside `aorta policy` (#12).
- Level 2: Extend `aorta watch` with a layout engine. Could use `curses` (stdlib) or keep it simple with ANSI escape codes for positioning.
- Level 3: ASCII graph layout. Only valuable once counts-as and obligation gates exist.

---

## Extension 14: Conversation replay harness (done)

**Problem:** Testing AORTA policies against real agent behavior requires running an actual Claude Code session. There's no way to ask "what would this policy have done during yesterday's conversation?" or to validate new norms, thresholds, and extensions against real traces without deploying them live.

**Key insight:** Claude Code stores full conversation traces as JSONL files in `~/.claude/projects/<project-dir>/<session-id>.jsonl`. These traces contain every tool call with full inputs and results — exactly what the AORTA hooks consume.

### Trace format

Each line is a JSON object with a `type` field:

| `type` | Contents |
|--------|----------|
| `user` | User messages, or `tool_result` responses (content contains `type: "tool_result"` blocks with `tool_use_id`, `content`, `is_error`) |
| `assistant` | Model responses with `tool_use` blocks (`name`, `input`, `id`) in `message.content[]` |
| `progress` | Streaming progress (skip during replay) |
| `queue-operation` | Internal queue management (skip) |
| `file-history-snapshot` | File backup tracking (skip) |
| `last-prompt` | Session end marker |

Tool use blocks contain exactly what PreToolUse receives:
- `name`: tool name (Bash, Write, Edit, Read, Glob, Grep, etc.)
- `input.command`: for Bash
- `input.file_path`: for Write/Edit/Read
- `input.pattern`: for Grep/Glob

Tool result blocks contain what PostToolUse receives:
- `content`: result text (stdout for Bash)
- `is_error`: whether the tool call failed

Each line also carries `cwd`, `gitBranch`, `timestamp`, and `sessionId`.

### What's missing from tool_result for PostToolUse replay

The hook system receives a `tool_response` envelope with structured fields (`exitCode`, `stdout`, `stderr`). The JSONL `tool_result` stores the rendered content string, not the structured envelope. Replay needs to either:
1. Parse exit codes and stdout/stderr back out of the result text (fragile)
2. Accept that PostToolUse replay is approximate for Bash results
3. Extend Claude Code's trace format (upstream change, not in our control)

PreToolUse replay is exact — it only needs tool name and input params, which are fully preserved.

### Approach

An `aorta replay` command that walks a conversation trace and runs each tool call through the governance engine:

```
$ aorta replay --spec .aorta/safe-agent.yaml --trace ~/.claude/projects/.../session.jsonl

Replaying 47 tool calls from session c08f7006...

  #3  Read  .env                          → WOULD BLOCK (no-access)
  #7  Write src/config.py                 → ALLOW
  #12 Bash  "grep -r password src/"       → WOULD SOFT-BLOCK (use Grep tool)
  #15 Bash  "pytest"                      → ALLOW
  #15 PostToolUse                         → achievement: tests_passing
  #23 Write ../other-repo/hack.py         → WOULD BLOCK (outside scope)
  #31 Bash  "git commit -m 'wip'"         → ALLOW (tests_passing achieved)

Summary:
  47 tool calls, 3 would have been blocked, 1 soft-blocked
  Achievements triggered: tests_passing (at #15, cleared at #28, re-triggered at #35)
  Guardrails: failure rate peaked at 0.4 (threshold 0.5 — no hold triggered)
```

### Implementation

1. **Trace parser** (`src/aorta4llm/replay/`): Extract `(tool_use, tool_result)` pairs from JSONL. Skip `progress`, `queue-operation`, and other noise. Handle sidechains (events with `isSidechain: true` — these are sub-agent calls like Bash command analysis). A `inspect_session.py` utility already exists for exploring the format.

2. **Replay engine**: For each tool pair, construct the same event dicts that `hooks.py` builds from Claude Code's hook invocation, then call the governance check functions. Use a fresh in-memory state (or optionally load existing state for mid-session replay).

3. **Dry-run mode**: All checks run but no state is persisted. The engine evaluates PreToolUse blocks, PostToolUse achievements, guardrail counters, and obligation gates — but writes nothing to `.aorta/state.json`.

4. **Output modes**:
   - `--format summary` (default): blocked/warned actions + achievement timeline
   - `--format full`: every tool call annotated with ALLOW/BLOCK/WARN
   - `--format json`: machine-readable for batch analysis

### What this enables

- **Policy validation**: Test new norms against real conversations before deploying
- **Threshold tuning**: Run guardrails (failure rate, budgets) against real traces to find sweet spots
- **Extension testing**: Validate counts-as rules, obligation chains, sanctions against real workflows
- **Regression testing**: Ensure engine changes don't alter behavior on known-good traces
- **Batch analysis**: Replay all sessions for a project to find patterns ("how often do agents write outside scope?")

### Trace discovery

Session files live at `~/.claude/projects/<encoded-project-path>/<session-id>.jsonl`. The project path is the absolute path with `/` replaced by `-`. The `aorta replay` command could accept either a direct path or discover sessions automatically:

```
$ aorta replay --spec .aorta/safe-agent.yaml --last     # most recent session
$ aorta replay --spec .aorta/safe-agent.yaml --all      # all sessions for this project
$ aorta replay --spec .aorta/safe-agent.yaml --trace <path>  # specific session
```

---

## Extension 15: Context injection via skill description (done)

**Problem:** The agent doesn't know the governance rules before its first action. Hooks will block violations, but the agent wastes turns hitting walls. We need the rules in the LLM's context *before* it acts.

**Constraint:** Claude Code only loads skill *descriptions* into the system prompt for free. Full skill content loads only when invoked (confirmed via [skills docs](https://code.claude.com/docs/en/skills)). So the description is the only zero-cost context.

**Approach:** `aorta init` generates a skill file at `.claude/commands/aorta/context.md` with:

1. **Description (first line):** A dense summary of the effective governance rules, derived from the org spec at init time. This is always in the system prompt (~50-100 tokens). Example:

   ```
   AORTA governance active. Write scope: src/, tests/, docs/. No access: .env, secrets/, *.key, *.pem. git commit/push blocked until quality_verified (tests_passing + spec_valid). Run for full live state.
   ```

2. **Body:** Instruction to run `aorta context` for live state (achievements, holds, obligations, violation count). This loads only when the skill is invoked.

**`aorta context` CLI command:** Reads the org spec and current state, outputs a human-readable governance summary. Covers access map, norms, achievement gates, sanctions, current holds/obligations. Added to `_SAFE_AORTA_SUBCOMMANDS` so the agent can run it.

**Staleness:** The description is a static snapshot from `aorta init` / `--reinit` time. Skills are not reloaded mid-session (only `--add-dir` skills have live change detection). This means:
- Between sessions: `aorta init --reinit` regenerates the description. Could also regenerate on spec file changes via a hook.
- Within a session: the baked description may be stale if the spec changes. The `aorta context` command gives live state as a fallback.

**Hybrid approach:** Static rules in the description (always visible) + dynamic state via the command (on demand). The agent knows the rules upfront and can check current state when needed.

**Trade-offs:**
- Pro: Agent sees constraints before its first action — no wasted turns
- Pro: Lives in a place aorta controls (`.claude/commands/aorta/`), no CLAUDE.md editing
- Pro: Already-established pattern alongside `/aorta:status` and `/aorta:permissions`
- Con: Description is stale if spec changes without reinit
- Con: Agent must actively call the skill for live state (achievements, holds)
- Con: Overlap with `aorta status` and `aorta permissions` — `context` is the LLM-optimized view

---

## Extension 16: Allow memory writes (done)

**Problem:** Claude Code has a persistent memory system at `~/.claude/projects/<encoded-project-path>/memory/`. Agents use it to store user preferences, feedback, and project context across conversations. But this path is outside the project directory, so aorta's scope check blocks it. The agent can't build up memory without `allow-once` exceptions for every write.

**Approach:** A boolean `allow_memory: true` in the org spec. When set, the hook derives the memory path from the current working directory and whitelists writes to it.

```yaml
organization: my_project
allow_memory: true
access:
  src/: read-write
  tests/: read-write
```

**Path derivation:** Claude Code encodes the project path by replacing `/` with `-`. For a project at `/Users/alice/workspace/myapp`, the memory directory is `~/.claude/projects/-Users-alice-workspace-myapp/memory/`. The hook already knows `cwd` — it computes the memory path and adds it to the allowed write set before scope checking.

**What it allows:** Only writes to the `memory/` subdirectory, not the entire `~/.claude/projects/<project>/` directory (which contains session traces, settings, and other files the agent shouldn't touch).

**Implementation:**
1. Validator: accept `allow_memory` as an optional boolean
2. Compiler: pass `allow_memory` through to the compiled spec
3. Hook layer: when `allow_memory` is true, compute the memory path from `cwd` and skip scope checks for writes targeting that directory

---

## Implementation order

| Priority | Extension | Depends on |
|----------|-----------|------------|
| ~~0~~ | ~~`src/` layout refactor~~ | done |
| ~~1~~ | ~~Custom block messages (#11)~~ | done |
| ~~2~~ | ~~Policy packs (#12)~~ | done |
| ~~2a~~ | ~~`aorta include` CLI command (#12)~~ | done |
| ~~3~~ | ~~Policy visualization, level 1: tree (#13)~~ | done |
| ~~4~~ | ~~Thrashing detection + behavioral budgets (#4, #2)~~ | done — unified guardrails system |
| ~~5~~ | ~~Richer triggers (#8)~~ | done |
| ~~6~~ | ~~Obligation gate (`all_obligations_fulfilled`)~~ | done |
| ~~6a~~ | ~~`aorta status` shows active obligations~~ | done |
| ~~7~~ | ~~Counts-as rules (#6)~~ | done |
| 8 | Post-write content validation (#1) | #6 (creates obligations) |
| ~~9~~ | ~~Sanctions (#7)~~ | done |
| 10 | Workflow phases (#10) | #7, #5 |
| 11 | Scope drift (#3) | #6 |
| 12 | Tool output redaction PoC (#5) | — |
| ~~13~~ | ~~Checkpoint/rollback (#9)~~ | done — configuration pattern, no new code |
| ~~14~~ | ~~Conversation replay harness (#14)~~ | done |
| ~~15~~ | ~~Policy visualization, level 2: dashboard (#13)~~ | done |
| ~~16~~ | ~~Policy visualization, level 3: graph (#13)~~ | done |
| ~~17~~ | ~~Context injection via skill description (#15)~~ | done |
| ~~18~~ | ~~Allow memory writes (#16)~~ | done |

21 of 24 priorities are done. Remaining: #8 (post-write content validation), #10 (workflow phases), #11 (scope drift), #12 (tool output redaction).

---

## Codebase readiness

### What works well
- The engine's term/unification system is solid and extensible
- The compiler cleanly maps YAML → facts/rules
- The hook system has clear PreToolUse/PostToolUse separation
- Event sourcing provides a natural place to add session counters

### What needs attention
- ~~**`src/` layout**: Required for dogfooding and cleaner scope management~~ (done)
- ~~**Hook state**: Needs a `session_counters` dict for tracking write counts, command retries, directory spread per session~~ (done — guardrails state in state.json)
- ~~**Obligation lifecycle**: Path from PostToolUse checks → obligation creation → obligation gate enforcement~~ (done — counts-as `creates_obligation`, sanctions `obliged`)
- ~~**`counts_as` evaluation**: After each state change, check if any counts-as rules now fire~~ (done — cascading evaluation in post_tool_use)
- ~~**`all_obligations_fulfilled` predicate**: Built-in predicate for the evaluator~~ (done)
