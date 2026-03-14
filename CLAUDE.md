# CLAUDE.md — Development Instructions for aorta4llm

## Project Overview

aorta4llm applies the AORTA organizational reasoning framework (Jensen, 2015) to LLM agent systems. Hybrid architecture: LLMs handle natural language/planning, a pure-Python logic engine handles deterministic constraint enforcement.

## IMPORTANT
- Never implement anything without being asked to. When asked a question, it is not critique, it is a request for information to help you do your job. If you don't know the answer, say so. If you need more information, ask.
- Always ask for clarification if a request is ambiguous. It's better to ask than to make assumptions and potentially go down the wrong path.

## Build & Test

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest -v
```

## Architecture

All source lives under `src/aorta4llm/`:

- **governance/terms.py**: Term representation (Var, Atom, Term), parser for Prolog-syntax strings, structural unification.
- **governance/evaluator.py**: In-memory fact database and condition evaluator.
- **governance/py_engine.py**: Pure-Python governance engine. Implements NC/OG phases using terms.py and evaluator.py.
- **governance/engine_types.py**: Data types (PermissionResult, NormChange, NotifyResult).
- **governance/compiler.py**: YAML org specs → Prolog-syntax fact/rule strings (`CompiledSpec.facts`, no trailing `.`; `CompiledSpec.rules`, with trailing `.`).
- **governance/service.py**: High-level API with agent registration and scope management.
- **integration/hooks.py**: Claude Code hook integration — PreToolUse/PostToolUse handlers, CLI entry point, event-sourced state persistence, allow-once exceptions, system prompt injection from obligations.
- **cli/**: CLI commands — init, validate, dry-run, status, reset, allow-once, explain, hook.
- **org-specs/**: YAML organizational specifications following the metamodel schema from DESIGN.md.
- **org-specs/templates/**: YAML templates for common governance patterns (safe-agent, test-gate).

## Key Design Constraints

- **Permissions are derived, not stored.** An action is permitted unless an active prohibition blocks it (Section 4.1 of the dissertation).
- **Non-ground prohibitions are evaluated at check time**, not during NC phase activation. The engine evaluates `cond/5` prohibitions directly by unifying the action with the objective to bind shared variables before calling the condition.
- **Variable sharing** between `cond/5` objective and condition is the core mechanism. When the compiler emits `cond(R, forbidden, write_file(Path), D, not(in_scope(Path, Scope)))`, `Path` appears in both — unification with a concrete action binds it everywhere. The Python engine implements this via `src/aorta4llm/governance/terms.py` unification.

## Git Conventions

- Never add `Co-Authored-By` lines to commits.

## Coding Conventions

- Python: type hints, dataclasses for data carriers, no unnecessary abstractions.
- Tests: in `tests/` organized by package (`tests/governance/`, `tests/integration/`, `tests/cli/`), use pytest fixtures, group related tests in classes.
- Org specs: YAML mirrors the metamodel structure from DESIGN.md Table 6.1.

## Claude Code Integration

Quickstart:

```bash
aorta init --template safe-agent --scope src/
```

This creates `.aorta/safe-agent.yaml` and `.claude/settings.local.json` with hooks configured. Re-run with `--reinit` to update existing hooks. The agent is registered as `agent` automatically.

Grant temporary exceptions: `aorta allow-once .env`

Debug norm evaluation: `aorta explain --tool Write --path config/x.py --agent agent --role agent`

## Reference

- DESIGN.md contains the full architecture, metamodel definition, API spec, and phased implementation plan.
- Jensen, A. S. (2015). "The AORTA Reasoning Framework". PhD thesis, DTU. PHD-2015-372.
