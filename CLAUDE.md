# CLAUDE.md — Development Instructions for aorta4llm

## Project Overview

aorta4llm applies the AORTA organizational reasoning framework (Jensen, 2015) to LLM agent systems. Hybrid architecture: LLMs handle natural language/planning, a logic engine handles deterministic constraint enforcement. Two backends: pure Python (default, no dependencies) or SWI-Prolog via pyswip (optional).

## Build & Test

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest -v
```

SWI-Prolog is optional: `brew install swi-prolog` to enable the Prolog backend. Tests run against both engines when pyswip is available.

## Architecture

- **governance/terms.py**: Term representation (Var, Atom, Term), parser for Prolog-syntax strings, structural unification.
- **governance/evaluator.py**: In-memory fact database and condition evaluator — replaces SWI-Prolog for the pure-Python engine.
- **governance/py_engine.py**: Pure-Python governance engine (default). Reimplements NC/OG phases using terms.py and evaluator.py.
- **governance/engine.py**: SWI-Prolog engine (optional). pyswip wrapper that consults metamodel.pl, nc.pl, og.pl.
- **governance/engine_types.py**: Shared data types (PermissionResult, NormChange, NotifyResult) used by both engines.
- **governance/compiler.py**: YAML org specs → Prolog-syntax fact/rule strings (`CompiledSpec.facts`, no trailing `.`; `CompiledSpec.rules`, with trailing `.`).
- **governance/service.py**: High-level API with agent registration, scope management, and engine selection (`engine="auto"|"python"|"prolog"`).
- **integration/hooks.py**: Claude Code hook integration — PreToolUse/PostToolUse handlers, CLI entry point, event-sourced state persistence, system prompt injection from obligations.
- **org-specs/**: YAML organizational specifications following the metamodel schema from DESIGN.md.
- **examples/three_role_demo/demo.py**: Runnable three-role workflow simulation.

## Key Design Constraints

- **Permissions are derived, not stored.** An action is permitted unless an active prohibition blocks it (Section 4.1 of the dissertation).
- **Non-ground prohibitions are evaluated at check time**, not during NC phase activation. `nc_activate_prohibition` guards with `ground(Obj), ground(Cond)` to avoid Prolog instantiation errors. `check_action_blocked/4` evaluates `cond/5` prohibitions directly by unifying the action with the objective to bind shared variables before calling the condition.
- **Variable sharing** between `cond/5` objective and condition is the core mechanism. When the compiler emits `cond(R, forbidden, write_file(Path), D, not(in_scope(Path, Scope)))`, `Path` appears in both — unification with a concrete action binds it everywhere. Both the Python engine (via `governance/terms.py` unification) and the Prolog engine (via native Prolog unification) implement this.

## Git Conventions

- Never add `Co-Authored-By` lines to commits.

## Coding Conventions

- Python: type hints, dataclasses for data carriers, no unnecessary abstractions.
- Prolog: section headers with `%% ===...===`, declare all dynamic predicates explicitly, comment non-obvious rules.
- Tests: one test file per module in `governance/tests/`, use pytest fixtures, group related tests in classes.
- Org specs: YAML mirrors the metamodel structure from DESIGN.md Table 6.1.

## Claude Code Integration

Configure hooks in `.claude/settings.local.json`:

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Write|Edit|Bash",
      "command": "uv run python -m integration.hooks pre-tool-use --org-spec org-specs/three_role_workflow.yaml --agent $AGENT_NAME"
    }]
  }
}
```

Register agents before starting:

```bash
uv run python -m integration.hooks register \
    --org-spec org-specs/three_role_workflow.yaml \
    --agent impl-1 --role implementer --scope src/auth/
```

Run the demo: `uv run python examples/three_role_demo/demo.py`

## Reference

- DESIGN.md contains the full architecture, metamodel definition, API spec, and phased implementation plan.
- Jensen, A. S. (2015). "The AORTA Reasoning Framework". PhD thesis, DTU. PHD-2015-372.
