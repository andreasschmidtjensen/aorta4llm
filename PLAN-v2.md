# Improvement Plan v2

Based on live Claude Code testing and developer assessment of aorta4llm.

## Current state

280 tests passing. All PLAN.md items from the previous session are implemented. This session found and fixed a critical bug (soft block masking hard block), and produced a developer assessment.

### Already done (this session, uncommitted)

**Bug fix: hard blocks take priority over soft blocks**

When `forbidden_command` (soft) and `required_before` (hard) both match the same command (e.g., `git commit`), the soft block was masking the hard block. On soft-block retry, the hard block was silently bypassed — allowing commits with failing tests.

Fix (3 files):
- `governance/py_engine.py`: `_check_action_blocked` collects all matching violations and returns the hardest severity. `_get_norm_severity` now accepts an optional condition and checks arity-3 `soft_norm` facts.
- `governance/compiler.py`: `_compile_forbidden_command` emits `soft_norm(role, obj, condition)` (arity 3) instead of blanket `soft_norm(role, obj)` (arity 2), scoping soft designation to the specific command pattern.
- `governance/tests/test_soft_blocks.py`: 4 new regression tests (`TestHardBlockOverridesSoft`).

**Also uncommitted from previous session** (all PLAN.md items):
- `cli/cmd_doctor.py`, `cli/cmd_protect.py`, `cli/cmd_norm.py`, `cli/cmd_add_template.py`, `cli/cmd_access.py`, `cli/spec_utils.py` — new CLI commands
- `governance/compiler.py` — access map expansion, cmd_matches cleanup
- `governance/py_engine.py` — cmd_matches cleanup in `_describe_condition`
- `governance/validator.py` — access map validation
- `docs/getting-started.md` — troubleshooting section, CLI docs
- `docs/test-suite.md` — groups 11-14 (CLI norms, template composition, access map, doctor)
- Tests: `test_access_map.py`, `test_add_template.py`, `test_cli_norm_mgmt.py`, `test_cmd_matches_cleanup.py`, `test_doctor.py`

---

## New improvements

### 1. Make `access` map the primary interface

**Goal:** Replace the separate scope/readonly/protected norm types in templates and docs with the more intuitive `access` map. Remove the old norm types from user-facing surface (keep them internally for backward compat... actually no — this is unreleased, just remove them from templates).

**Changes:**

Templates (`org-specs/templates/safe-agent.yaml`):
```yaml
# Before (current):
norms:
  - role: agent
    type: scope
    paths: [src/]
  - role: agent
    type: protected
    paths: [.env, .env.local, secrets/, .ssh/]
  - role: agent
    type: readonly
    paths: [config/]
  - role: agent
    type: forbidden_command
    command_pattern: "git commit"
    severity: soft
  - role: agent
    type: forbidden_command
    command_pattern: "git push"
    severity: soft

# After:
access:
  src/:       read-write
  .env:       no-access
  .env.local: no-access
  secrets/:   no-access
  .ssh/:      no-access
  config/:    read-only

norms:
  - role: agent
    type: forbidden_command
    command_pattern: "git commit"
    severity: soft
  - role: agent
    type: forbidden_command
    command_pattern: "git push"
    severity: soft
```

- `aorta init --scope src/ tests/` should generate `access:` entries for scope+defaults, not `norms:` entries.
- `aorta protect`, `aorta readonly` should update the `access:` map, not add norms. Or maybe keep both paths — `aorta access` is the primary, `aorta protect` / `aorta readonly` still work but modify norms.
- `aorta status` should show the access map prominently.
- `docs/getting-started.md` should lead with the access map syntax.
- Remove scope/readonly/protected from the "Customize the org spec" example as the primary way. Keep as "Advanced: explicit norms" section.

**Scope decision needed:** Should `aorta protect` / `aorta readonly` modify the access map or add norms? Simplest: they modify the access map. The `norms:` list is for things that don't fit the access map (forbidden_command, required_before, obliged, etc.).

---

### 2. Truncate long commands in soft block messages

**Problem:** Soft block messages dump the entire command including multi-line heredocs:
```
SOFT BLOCK — user confirmation required.
Reason: execute_command(git add src/models/user.py && git commit -m "$(cat <<'EOF'
feat: add user model

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)") blocked for agent (role: agent): command contains 'git commit'
```

**Fix:** In `integration/hooks.py`, `_handle_soft_block`, truncate the reason's command portion to ~120 chars with `...`.

**File:** `integration/hooks.py` — `_handle_soft_block` method (~line 504).

**Also:** The `_describe_condition` output in `py_engine.py` uses the raw command atom. Could truncate at the `term_to_str` level for `execute_command(...)`, but that's more invasive. Better to truncate in the hook layer where the message is formatted.

---

### 3. `aorta init --dry-run`

**Goal:** Show what would be created without creating anything. Useful for understanding a template before committing.

**File:** `cli/cmd_init.py`

**Output:**
```
Dry run — no files will be created.

Would create:
  .aorta/safe-agent.yaml
  .claude/settings.local.json
  .aorta/state.json (agent 'agent' registered)

Org spec contents:
  access:
    src/:       read-write
    tests/:     read-write
    .env:       no-access
    ...
  norms:
    forbidden_command [soft]: git commit
    forbidden_command [soft]: git push

Hooks:
  PreToolUse: Write|Edit|NotebookEdit|Bash|Read|Glob|Grep
```

---

### 4. Show effective severity in `aorta explain`

**File:** `cli/cmd_explain.py`

Currently explain shows norms with `[soft]` only if the YAML has `severity: soft`. For `required_before` norms, it shows no severity tag — even though they compile to hard blocks.

**Fix:** Add `[hard]` tag for norms that don't specify severity, making it explicit. Or better: show the effective block type in the evaluation section:

```
  >> #6 [MATCH] requires 'tests_passing' to be achieved first [hard block]
```

---

### 5. Clean up Prolog internals from user-facing messages

**Problem:** Terms like `cond/5`, `rea/2`, "unification", and Prolog-style list syntax leak into comments, docstrings, and occasionally error messages.

**Files:** Audit and fix:
- `governance/py_engine.py` — class/method docstrings reference nc.pl, og.pl, Prolog lists
- `governance/compiler.py` — comments mention "Prolog-syntax strings"
- `governance/terms.py` — the module itself is fine (it IS the term system), but its docstring could be clearer
- `governance/evaluator.py` — docstrings
- Error messages in dry-run / explain output

**Approach:** Replace Prolog jargon with plain descriptions in docstrings and comments. Internal code can keep the terminology (it's accurate), but anything user-facing should not mention Prolog.

---

### 6. `--strict` mode for bash analysis

**Goal:** Option to always use LLM analysis for bash commands (slower but more thorough).

**File:** `governance/bash_analyzer.py`, org spec YAML field.

**YAML:**
```yaml
bash_analysis: strict   # instead of: true
```

Values: `true` (heuristic first, LLM fallback), `strict` (always LLM), `false` (disabled).

**Low priority.** The heuristic handles ~80% of cases. Strict mode adds ~5s per command. Worth having for high-security contexts but not urgent.

---

## Implementation order

1. **Commit current changes** — bug fix + all PLAN.md items (already passing, just needs commit)
2. **Truncate soft block messages** (#2) — small, standalone, high UX impact
3. **Access map as primary interface** (#1) — largest change, template rewrite + CLI updates + docs
4. **`aorta init --dry-run`** (#3) — small, standalone
5. **Explain severity** (#4) — small
6. **Prolog cleanup** (#5) — cosmetic, do alongside other changes
7. **Strict bash analysis** (#6) — low priority, do if time permits

Items 2, 4, 5 are independent. Item 1 (access map) depends on deciding how `aorta protect`/`aorta readonly` interact with the access map.
