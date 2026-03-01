# Health Check Project — Orchestrator Example

A minimal Python project used to demonstrate the aorta4llm multi-agent
orchestrator. The orchestrator will run three governed Claude Code agents
(architect → implementer → reviewer) to add a health check endpoint.

## Prerequisites

- SWI-Prolog: `brew install swi-prolog`
- Claude Code CLI: `npm install -g @anthropic-ai/claude-code`
- aorta4llm installed: `uv pip install -e ".[dashboard]"` (from repo root)

## Run the orchestrator

```bash
# From the aorta4llm repo root:

# 1. Start the dashboard (optional — watch at http://localhost:5111)
uv run python -m dashboard.server \
    --org-spec org-specs/three_role_workflow.yaml \
    --events examples/health_check_project/.aorta/events.jsonl \
    --port 5111 &

# 2. Run the orchestrator
uv run python -m orchestrator.run \
    --org-spec org-specs/three_role_workflow.yaml \
    --task "Add a health check endpoint that returns system status" \
    --scope src/health/ \
    --model haiku \
    --cwd examples/health_check_project
```

## What happens

1. **Architect** explores the codebase and writes a design doc to `docs/`
2. **Implementer** (scoped to `src/health/`) creates the health check code.
   Writes outside scope are blocked by governance.
3. **Reviewer** reads all code and writes a review doc to `docs/`.
   Source file writes (.py, .ts, .js) are blocked by governance.

All events appear in the dashboard and in `.aorta/events.jsonl`.

## Project structure (before orchestrator)

```
src/
├── __init__.py
├── app.py          # Simple utility functions
└── health/
    └── __init__.py # Empty — ready for the implementer
```
