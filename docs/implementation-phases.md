# Implementation Phases: Hardening & Bash Analysis

## Status: Planning Complete

Ref: conversation 2026-03-04, review + 4 implemented items + security analysis.

## Already Done (this session)
- **Item 3**: High-level norm syntax (`forbidden_outside`, `forbidden_paths`, `required_before`)
- **Item 4**: Decoupled orchestrator (generic workflow loop from org spec YAML)
- **Item 1**: PostToolUse achievement tracking (triggers, Bash command passing)
- **Item 2**: Template org specs (`safe-agent`, `test-gate`, `review-gate`)
- **Ref 1**: NotebookEdit, Glob, Grep added to TOOL_ACTION_MAP

## Phase 1 — Quick Hardening

### 1A. Move state file outside project tree

**Problem:** `.aorta/state.json` is in the project dir. Agent can overwrite it
via Bash to fake achievements and unlock gates.

**Fix:** Default state path → `~/.aorta/state-<hash>.json` (hash of org spec
absolute path). Explicit `--state` override stays for orchestrator.

**Files:** `integration/hooks.py` (new `_default_state_path`, update init + CLI)

### 1B. Update template matchers to include Bash

**Problem:** Template hook configs don't include `Bash` in the matcher for
scope-restricted configs. `echo > file` bypasses Write/Edit restrictions.

**Fix:** All templates: matcher → `"Write|Edit|NotebookEdit|Bash"`. This lets
existing `required_before` norms and (after Phase 2) LLM analysis intercept
Bash calls.

**Files:** `org-specs/templates/*.yaml` (comments/usage section only)

---

## Phase 2 — LLM-Based Bash Command Analysis

### 2A. New module: `governance/bash_analyzer.py`

Use Haiku to analyze what a shell command does. Returns structured output:
```python
@dataclass
class BashAnalysis:
    writes: list[str]      # file paths the command writes/creates/moves to
    is_destructive: bool   # rm, chmod, etc.
    summary: str           # one-line description
```

- `anthropic` SDK as optional dep under `[llm]` extra
- Fast-path: skip analysis for obviously read-only commands (ls, cat, grep, etc.)
- Graceful degradation: if no API key, return permissive result (fail-open)
- 5 second timeout

### 2B. Integrate into `pre_tool_use`

For Bash calls, after existing checks pass, if `bash_analysis: true` in org spec:
1. Call analyzer to extract write paths
2. Check each path against governance engine (`check_permission` for `write_file`)
3. Block if any path violates scope restrictions

Org spec opt-in:
```yaml
bash_analysis: true
```

### 2C. Dependencies and config

`pyproject.toml`:
```toml
[project.optional-dependencies]
llm = ["anthropic>=0.40"]
```

Import inside function body, not at module level.

### 2D. Logging

Log analysis results to events.jsonl for dashboard visibility:
```json
{"type": "bash_analysis", "agent": "dev", "command": "cp x.py config/",
 "writes": ["config/x.py"], "decision": "block"}
```

### 2E. Tests

- `governance/tests/test_bash_analyzer.py` — mocked Anthropic responses,
  safe-prefix fast path, no-API-key degradation, timeout handling
- `governance/tests/test_integration.py` — `echo > forbidden/file` blocked
  when `bash_analysis: true` + `forbidden_outside` active

---

## Implementation Order

1. Phase 1A — state file relocation (~20 lines)
2. Phase 1B — template matcher updates (YAML comments)
3. Phase 2C — optional `anthropic` dependency
4. Phase 2A — bash_analyzer.py (~80 lines)
5. Phase 2B — pre_tool_use integration (~25 lines)
6. Phase 2D — event logging (~5 lines)
7. Phase 2E — tests

Run `uv run pytest -v` after each step. 128 existing tests must stay green.

---

## Known Limitations

- **LLM analysis is best-effort.** An adversarial agent could craft obfuscated
  commands to evade Haiku. This is defense-in-depth for honest-but-careless
  agents, not a security boundary against active adversaries.
- **State file still writable via ~/.aorta/.** Phase 2 mitigates this (LLM
  would flag writes to ~/.aorta/), but true tamper-proofing needs HMAC signing.
- **Subagents** are governed by the same hooks in single-agent setups.
  Orchestrator-spawned agents get hooks via `_write_hook_settings`.
