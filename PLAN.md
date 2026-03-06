# Improvement Plan

Based on live testing of aorta4llm with Claude Code. All bugs from testing are already fixed on main.

## 1. Clean up `cmd_matches_xxx` hash from block messages

**Problem:** `required_before` block messages show:
```
cmd_matches_d76620(git commit -m test) and requires 'tests_passing' to be achieved first
```
The `cmd_matches_d76620` is an internal helper name — meaningless to users.

**Fix:** In `governance/py_engine.py`, `_describe_condition()` (line 62), the conjunction handler recurses into both parts. The left part `cmd_matches_xxx(Cmd)` falls through to generic `term_to_str()`. Add a case: if the functor starts with `cmd_matches_`, skip it — the right side of the conjunction already has the useful info ("requires 'tests_passing' to be achieved first").

**File:** `governance/py_engine.py` — `_describe_condition()`, around line 62-73.

**Expected result:**
```
requires 'tests_passing' to be achieved first
```

**Tests:** Update any test in `governance/tests/` that asserts on the old message format.

---

## 2. `aorta doctor` command

**Purpose:** Diagnose setup issues. Critical because Claude Code hook errors fail-open silently.

**File:** New `cli/cmd_doctor.py`, register in `cli/main.py`.

**Checks (in order):**
1. Find `.aorta/` directory (walk up from cwd) — fail if not found
2. Find org spec YAML in `.aorta/` — list them, validate each
3. Find `.claude/settings.local.json` — check it exists
4. Check aorta hooks exist in settings — show matcher and command
5. Verify hook command binary is executable (`shutil.which` on the binary in the command)
6. Check state file (`.aorta/state.json`) — valid JSON, has registered agents
7. Run a quick dry-run (write to a protected path) to verify engine works end-to-end

**Output format:**
```
✓ .aorta/ found
✓ Org spec: .aorta/safe-agent.yaml (valid)
✓ Hooks config: .claude/settings.local.json
  PreToolUse: Write|Edit|NotebookEdit|Bash|Read|Glob|Grep
  PostToolUse: Bash
✓ Hook binary: /Users/x/.local/bin/aorta
✓ State: 1 agent registered (agent)
✓ Engine: dry-run passed

All checks passed.
```

Or on failure:
```
✓ .aorta/ found
✓ Org spec: .aorta/safe-agent.yaml (valid)
✗ Hooks config: .claude/settings.local.json not found
  Run: aorta init --template safe-agent --scope src/

1 issue found.
```

---

## 3. Document fail-open in getting-started.md

**File:** `docs/getting-started.md`

Add a "Troubleshooting" section at the end (before current "Limitations"):

```markdown
## Troubleshooting

### Hooks not working (actions not blocked)

Claude Code treats hook errors as non-blocking — if the `aorta` binary
isn't found or the hook crashes, actions proceed silently. Run:

    aorta doctor

Common causes:
- `aorta` not on PATH (install with `pip install aorta4llm` or `uv pip install aorta4llm`)
- Stale hooks after reinstall (run `aorta init --reinit`)
- Agent not registered (check `aorta status`)
```

---

## 4. Template composition

**Goal:** Combine norms from multiple templates without manual YAML editing.

**Approach:** New `aorta add-template` command that merges a template's norms into an existing org spec.

**File:** New `cli/cmd_add_template.py`, register in `cli/main.py`.

**Usage:**
```bash
# Initial setup
aorta init --template safe-agent --scope src/ tests/

# Add test-gate norms to existing spec
aorta add-template test-gate
```

**Logic:**
1. Read existing org spec from `.aorta/<name>.yaml` (auto-detect or `--org-spec`)
2. Read template YAML from `org-specs/templates/<template>.yaml`
3. Merge:
   - `norms`: append template norms, skip duplicates (same type+role+paths/pattern)
   - `achievement_triggers`: append, skip duplicates
   - `roles`: merge objectives and capabilities (union)
   - `bash_analysis`: true if either has it
   - `safe_commands`: union
4. Write merged spec back
5. Rebuild `.claude/settings.local.json` hooks (add PostToolUse if achievement_triggers added, add Read/Glob/Grep if protected norms added)
6. Print summary of what was added

**Edge cases:**
- Template has a scope norm with different paths → warn, keep existing scope
- Duplicate forbidden_command patterns → skip with message

---

## 5. CLI norm management

**Goal:** Add/remove norms without editing YAML.

**Files:** New `cli/cmd_protect.py`, `cli/cmd_norm.py`, register in `cli/main.py`.

### `aorta protect`
```bash
aorta protect .env secrets/ "*.key" "*.pem"
# Adds a protected norm for these paths to the org spec
# Also updates hooks matcher to include Read|Glob|Grep if not already there
```

### `aorta readonly`
```bash
aorta readonly config/ "*.yaml"
```

### `aorta forbid`
```bash
aorta forbid "git push --force" --severity hard
aorta forbid "rm -rf" --severity hard
```

### `aorta require`
```bash
aorta require tests_passing --before "git commit"
# Adds required_before norm + achievement trigger for pytest
```

### `aorta remove-norm`
```bash
aorta status  # shows norms with indices
#   #1 scope (agent) — src/, tests/
#   #2 protected (agent) — .env, secrets/
#   #3 readonly (agent) — config/

aorta remove-norm 3
# Removes norm #3 (readonly on config/)
```

**Shared logic:**
- All commands auto-detect the org spec (first `.yaml` in `.aorta/`, or `--org-spec`)
- Read YAML → modify norms list → write YAML back
- Rebuild hooks config if needed (e.g., adding protected → add Read/Glob/Grep matcher)
- Default role is `agent` (override with `--role`)

**Implementation order:** Start with `aorta protect` and `aorta remove-norm` — highest value. Add others incrementally.

---

## 6. Per-directory scope refinement

**Goal:** Replace separate scope/readonly/protected norms with an intuitive path access map.

**New YAML syntax (additive, not replacing):**
```yaml
access:
  src/:       read-write
  tests/:     read-write
  config/:    read-only
  .env:       no-access
  secrets/:   no-access
  "*.key":    no-access
  docs/:      read-only
```

**Semantics:**
- `read-write`: agent can read and write (= in scope)
- `read-only`: agent can read, not write (= readonly norm)
- `no-access`: agent can't read or write (= protected norm)
- Unmentioned paths: default to `no-access` (configurable via `default: no-access` or `default: read-only`)

**Compiler translation** (`governance/compiler.py`):
- Collect all `read-write` paths → emit `scope` norm
- Collect all `read-only` paths → emit `readonly` norm
- Collect all `no-access` paths → emit `protected` norm
- This happens in the existing `compile_org_spec()`, before the norm loop

**Backward compatible:** Existing `norms:` list still works. `access:` is syntactic sugar that generates norms. If both are present, they merge (access-generated norms + explicit norms).

**Init integration:**
```bash
aorta init --template safe-agent --scope src/ tests/
```
Templates can use either format. The `access` format becomes the default for new templates.

**CLI integration** (builds on #5):
```bash
aorta access src/ read-write
aorta access config/ read-only
aorta access .env no-access
```

---

## Implementation Order

1. **cmd_matches cleanup** — 10 min, pure improvement, no new files
2. **aorta doctor** — new command, standalone, no deps on other changes
3. **Fail-open docs** — docs only, reference doctor command
4. **CLI norm management** — `aorta protect` and `aorta remove-norm` first
5. **Template composition** — `aorta add-template`
6. **Per-directory access map** — compiler + YAML schema change + template update

Items 1-3 are independent and can be done in parallel.
Items 4-6 build on each other slightly but can be done independently.
