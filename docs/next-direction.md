# Next Direction: General-Purpose Governance

## User's Vision
Move from demo three-role orchestrator to a practical governance layer that can be used in real development.

## Key Principles (from user feedback)
1. **Claude plans, orchestrator executes with guardrails** — The user wants Claude to make a plan, then the orchestrator ensures the plan is executed safely. The governance layer is the safety net, not the workflow driver.

2. **Scope-based constraints over role division** — The architect/implementer/reviewer split is less interesting than:
   - Define which directories/files an agent CAN write to
   - Define which directories/files are OFF LIMITS
   - File pattern matching (e.g. "can edit `src/**` but not `config/**`")

3. **Workflow gates / conditional permissions** — The interesting governance:
   - "Cannot commit without a completed review"
   - "Cannot deploy without passing tests"
   - "Cannot modify database schema without approval"
   - These map naturally to AORTA obligations + prohibitions with conditions

4. **Practical, not esoteric** — Should feel like a useful tool, not an academic demo

## What Already Works
- GovernanceService with scope-based write blocking (scope matching via path prefix)
- PreToolUse hooks that intercept Claude Code tool calls
- Event logging + dashboard for monitoring
- Stream-json parsing for live agent output
- Obligation/prohibition lifecycle (NC/OG phases)
- YAML org specs that define norms

## What Needs to Change
- Orchestrator shouldn't hardcode architect→implementer→reviewer phases
- Should support plan-driven execution: take a plan, break into steps, execute each with appropriate constraints
- Org specs should be simpler to write for common use cases
- Need practical templates: "protect these files", "require review before commit", "limit scope to this directory"
- The hook integration already works — it's the orchestration layer and org spec format that need rethinking

## Technical Notes
- The governance kernel (terms.py, evaluator.py, py_engine.py, compiler.py, service.py) is solid and general — 156 tests
- The hook integration (hooks.py) is solid — path normalization, event sourcing, CLI entry point
- The dashboard is functional — SSE, agent detail, orchestrator UI
- Main refactoring target: `orchestrator/run.py` (hardcoded phases) and org spec format
- agent.py is reusable as-is (spawns governed Claude CLI subprocesses)
