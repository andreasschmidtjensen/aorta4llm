# aorta4llm Project Memory

## Project Overview
AORTA-based organizational reasoning for LLM agent systems. Hybrid architecture: LLMs handle NLU/planning, a pure-Python logic engine handles deterministic constraint enforcement.

## Key Architecture Decisions
- Prohibitions with non-ground conditions are evaluated at **check time**, not during NC phase activation.
- `check_action_blocked` has two paths: one for activated `norm/5` facts, one for direct `cond/5` evaluation where action unifies with objective to bind shared variables.
- Permissions are derived (not stored) — permitted unless a prohibition blocks it.
- Hook state uses event sourcing — JSON file stores registrations/achievements, replayed on each invocation.
- **Shared data types** in `engine_types.py` — `PermissionResult`, `NormChange`, `NotifyResult`.

## Project Structure
- `governance/terms.py` — Term types (Var, Atom, Term), parser, unification, apply_subst
- `governance/evaluator.py` — FactDatabase + ConditionEvaluator (builtins: not, atom_concat, member, ground, etc.)
- `governance/py_engine.py` — PythonGovernanceEngine (pure-Python)
- `governance/engine_types.py` — shared PermissionResult, NormChange, NotifyResult
- `governance/compiler.py` — YAML org specs -> Prolog-syntax fact/rule strings
- `governance/service.py` — high-level service with agent registration
- `integration/hooks.py` — Claude Code hook handlers + CLI + state persistence + prompt injection
- `integration/events.py` — append-only JSONL event logger for `aorta watch`
- `org-specs/templates/` — safe-agent, test-gate templates
- Build: hatchling

## Git Conventions
- Never add `Co-Authored-By` lines to commits.

