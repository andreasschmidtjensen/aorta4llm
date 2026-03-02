# aorta4llm Project Memory

## Project Overview
AORTA-based organizational reasoning for LLM agent systems. Hybrid architecture: LLMs handle NLU/planning, a logic engine handles deterministic constraint enforcement. Two backends: pure Python (default) or SWI-Prolog via pyswip (optional).

## Key Architecture Decisions
- Prohibitions with non-ground conditions are evaluated at **check time**, not during NC phase activation. `nc_activate_prohibition` guards with `ground(Obj), ground(Cond)`.
- Obligation conditions use `catch(call(Cond), error(instantiation_error, _), fail)` semantics — allows non-ground conditions to bind variables through unification.
- `check_action_blocked` has two paths: one for activated `norm/5` facts, one for direct `cond/5` evaluation where action unifies with objective to bind shared variables.
- Permissions are derived (not stored) — permitted unless a prohibition blocks it.
- Hook state uses event sourcing — JSON file stores registrations/achievements, replayed on each invocation.
- **Dual engine backends**: `GovernanceService(path, engine="auto"|"python"|"prolog")`. Auto tries Prolog first, falls back to Python.
- **Shared data types** in `engine_types.py` — both engines import `PermissionResult`, `NormChange`, `NotifyResult` from there.
- **pyswip shares a single SWI-Prolog engine** across `Prolog()` instances. `GovernanceEngine.__init__` must `retractall` all dynamic predicates.

## Project Structure
- `governance/terms.py` — Term types (Var, Atom, Term), parser, unification, apply_subst
- `governance/evaluator.py` — FactDatabase + ConditionEvaluator (builtins: not, atom_concat, member, ground, etc.)
- `governance/py_engine.py` — PythonGovernanceEngine (pure-Python, default)
- `governance/engine.py` — GovernanceEngine (pyswip/SWI-Prolog, optional)
- `governance/engine_types.py` — shared PermissionResult, NormChange, NotifyResult
- `governance/compiler.py` — YAML org specs -> Prolog-syntax fact/rule strings
- `governance/service.py` — high-level service with agent registration + engine selection
- `governance/prolog/` — metamodel.pl, nc.pl, og.pl
- `integration/hooks.py` — Claude Code hook handlers + CLI + state persistence + prompt injection
- `integration/events.py` — append-only JSONL event logger for dashboard
- `dashboard/server.py` — Flask + SSE web dashboard (port 5111)
- `dashboard/static/index.html` — single-page dark-theme dashboard with agent detail panel
- `orchestrator/run.py` — multi-agent workflow: architect -> implementer -> reviewer
- `orchestrator/agent.py` — spawns Claude Code CLI subprocesses with governance hooks, stream-json parser
- `orchestrator/prompts.py` — builds role-appropriate system prompts from org spec
- `org-specs/code_review.yaml` — two-role spec (Phase 1/2 tests)
- `org-specs/three_role_workflow.yaml` — three-role spec (Phase 3)
- `org-specs/arch_gate_workflow.yaml` — architecture gate spec (obligation demo)
- `examples/` — demo scripts (three_role_demo, obligation_demo, health_check_project)
- Build: hatchling, pyswip is optional dep under `[prolog]`
- 156 tests total (parametrized across both engines)

## Git Conventions
- Never add `Co-Authored-By` lines to commits.

## Orchestrator & Dashboard Notes
- Orchestrator uses CLI subprocess: `claude --print --output-format stream-json` with PreToolUse hooks.
- Must unset `CLAUDECODE` env var to avoid nested session detection when spawning from within Claude Code.
- Shared state via `.aorta/state.json` — orchestrator writes registrations, hooks read them.
- Dashboard watches `.aorta/events.jsonl` via SSE for real-time monitoring.
- **Stream-json format is JSONL** (NOT SSE). Types: `system` (init), `assistant` (content in `msg["message"]["content"]`), `user` (tool results), `result` (final). Content block types: `text`, `thinking`, `tool_use`.
- Agent detail events (`agent_text`, `agent_tool_call`, `agent_turn_complete`) route only to per-agent store, not the main event feed. Click agent card to view.
- `project_dir` from orchestrator UI passed as `cwd` to agent subprocesses AND as `--cwd` to hook for path normalization.
- Events path explicitly passed so dashboard always watches the right file regardless of project_dir.
- stderr must be read concurrently with stdout (`asyncio.create_task`) to avoid pipe deadlock.
- Debug logs written to `.aorta/debug-stream-{agent_id}.log`.

## Phase Status
- Phase 1 (governance kernel): COMPLETE
- Phase 2 (full reasoning cycle): COMPLETE
- Phase 3 (Claude Code integration): COMPLETE
- Phase 4 (dashboard + orchestrator): COMPLETE
- Phase 5 (pure-Python engine + obligation demo + README reframe): COMPLETE — 156 tests passing
- Dashboard live agent viewer: COMPLETE — stream-json parsing, clickable agent cards, detail panel

## Next Direction (User Vision)
User wants to move away from the demo/esoteric three-role workflow toward a **general-purpose governance layer**:
- Claude plans work, orchestrator executes with governance guardrails
- Focus on **scope-based constraints**: allowed/forbidden directories, file patterns, actions
- Focus on **workflow gates**: e.g. "cannot commit without completed review"
- Less about architect/implementer/reviewer role division
- More about defining safety boundaries for a single agent or small team
- Should be practically usable in real application development, not just demos
- See `memory/next-direction.md` for detailed notes
