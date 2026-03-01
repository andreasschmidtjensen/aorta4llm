"""Multi-agent orchestrator with governance enforcement.

Takes a task description and runs it through the organizational workflow
defined in the org spec. Each phase uses a real Claude Code agent (via
the CLI) with governance constraints enforced by PreToolUse hooks.

Usage:
    uv run python -m orchestrator.run \\
        --org-spec org-specs/three_role_workflow.yaml \\
        --task "Add a health check endpoint" \\
        --scope src/health/ \\
        --cwd /path/to/target/project
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

import yaml

from integration.events import log_event
from integration.hooks import GovernanceHook
from orchestrator.agent import run_agent
from orchestrator.prompts import build_system_prompt


def _extract_feature_name(task: str) -> str:
    """Extract a short feature name from the task description."""
    words = re.sub(r"[^\w\s]", "", task.lower()).split()
    name = "_".join(words[:3]) if words else "feature"
    return name


async def run_workflow(
    org_spec_path: str,
    task: str,
    scope: str,
    model: str = "sonnet",
    cwd: str | None = None,
    max_turns: int = 10,
) -> dict:
    """Run the full organizational workflow for a task.

    Args:
        org_spec_path: Path to org spec YAML.
        task: Natural language task description.
        scope: File path scope for the implementer.
        model: Model to use for agents.
        cwd: Working directory (target project).
        max_turns: Maximum turns per agent.

    Returns:
        Dict with phase outputs and summary.
    """
    with open(org_spec_path) as f:
        spec = yaml.safe_load(f)

    # Paths for governance state and events — stored in target project
    project_dir = Path(cwd or ".")
    state_path = project_dir / ".aorta" / "state.json"
    events_path = project_dir / ".aorta" / "events.jsonl"

    # GovernanceHook persists state to disk so the CLI hooks (which run
    # as separate processes) can read it back.
    hook = GovernanceHook(org_spec_path, state_path=str(state_path),
                          events_path=str(events_path))

    feature = _extract_feature_name(task)
    results = {}

    print(f"\n{'='*60}")
    print(f"  AORTA Orchestrator — {spec.get('organization', 'workflow')}")
    print(f"{'='*60}")
    print(f"  Task:    {task}")
    print(f"  Feature: {feature}")
    print(f"  Scope:   {scope}")
    print(f"  Model:   {model}")
    print(f"{'='*60}\n")

    # ── Phase 1: Architect ─────────────────────────────────────

    print("Phase 1: ARCHITECT")
    print("-" * 40)

    hook.register_agent("architect-1", "architect")
    log_event({"type": "phase_start", "phase": "architect",
               "agent": "architect-1", "task": task}, events_path)

    architect_prompt = (
        f"You are the architect for this task: {task}\n\n"
        f"Explore the codebase, understand the existing structure, and produce "
        f"a clear design plan. Decide what files need to be created or modified. "
        f"The implementer will be scoped to '{scope}' — design accordingly.\n\n"
        f"Write your design to a file (e.g., docs/design_{feature}.md) so the "
        f"implementer can reference it. Be concise and specific."
    )

    design = await run_agent(
        agent_id="architect-1",
        role="architect",
        org_spec_path=org_spec_path,
        prompt=architect_prompt,
        system_prompt=build_system_prompt("architect", spec),
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        model=model,
        cwd=cwd,
        events_path=events_path,
        state_path=state_path,
        max_turns=max_turns,
    )

    hook._service.notify_action(
        "architect-1", "architect",
        achieved=[f"system_design_complete({feature})"],
    )
    log_event({"type": "phase_complete", "phase": "architect",
               "agent": "architect-1"}, events_path)

    results["architect"] = design
    print(f"\nArchitect complete. Output length: {len(design)} chars\n")

    # ── Phase 2: Implementer ──────────────────────────────────

    print("Phase 2: IMPLEMENTER")
    print("-" * 40)

    hook.register_agent("impl-1", "implementer", scope=scope)
    log_event({"type": "phase_start", "phase": "implementer",
               "agent": "impl-1", "task": task}, events_path)

    impl_prompt = (
        f"You are implementing a feature based on the architect's design.\n\n"
        f"Task: {task}\n\n"
        f"Architect's output:\n{design}\n\n"
        f"You are scoped to '{scope}' — you can only write files within this directory. "
        f"Implement the feature, write tests, and run them. "
        f"Make sure all tests pass before finishing."
    )

    sys_prompt = build_system_prompt("implementer", spec, hook._service, "impl-1")

    implementation = await run_agent(
        agent_id="impl-1",
        role="implementer",
        org_spec_path=org_spec_path,
        prompt=impl_prompt,
        system_prompt=sys_prompt,
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        model=model,
        cwd=cwd,
        events_path=events_path,
        state_path=state_path,
        max_turns=max_turns,
    )

    hook._service.notify_action(
        "impl-1", "implementer",
        achieved=[f"feature_implemented({feature})", f"tests_passing({feature})"],
    )
    log_event({"type": "phase_complete", "phase": "implementer",
               "agent": "impl-1"}, events_path)

    results["implementer"] = implementation
    print(f"\nImplementer complete. Output length: {len(implementation)} chars\n")

    # ── Phase 3: Reviewer ─────────────────────────────────────

    print("Phase 3: REVIEWER")
    print("-" * 40)

    hook.register_agent("rev-1", "reviewer")
    log_event({"type": "phase_start", "phase": "reviewer",
               "agent": "rev-1", "task": task}, events_path)

    review_prompt = (
        f"You are reviewing the implementation of: {task}\n\n"
        f"Implementer's output:\n{implementation}\n\n"
        f"Review the code for correctness, style, and completeness. "
        f"You can read any file but you CANNOT modify source code files "
        f"(.py, .ts, .js) — this is enforced by governance. "
        f"Write your review to a markdown file (e.g., docs/review_{feature}.md)."
    )

    sys_prompt = build_system_prompt("reviewer", spec, hook._service, "rev-1")

    review = await run_agent(
        agent_id="rev-1",
        role="reviewer",
        org_spec_path=org_spec_path,
        prompt=review_prompt,
        system_prompt=sys_prompt,
        allowed_tools=["Read", "Write", "Glob", "Grep"],
        model=model,
        cwd=cwd,
        events_path=events_path,
        state_path=state_path,
        max_turns=max_turns,
    )

    hook._service.notify_action(
        "rev-1", "reviewer",
        achieved=[f"code_reviewed({feature})", f"review_documented({feature})"],
    )
    log_event({"type": "phase_complete", "phase": "reviewer",
               "agent": "rev-1"}, events_path)

    results["reviewer"] = review
    print(f"\nReviewer complete. Output length: {len(review)} chars\n")

    # ── Summary ───────────────────────────────────────────────

    print(f"\n{'='*60}")
    print("  WORKFLOW COMPLETE")
    print(f"{'='*60}")
    print(f"  Phases:     architect → implementer → reviewer")
    print(f"  Feature:    {feature}")
    print(f"  Dashboard:  http://localhost:5111")
    print(f"{'='*60}\n")

    return results


def main():
    parser = argparse.ArgumentParser(
        prog="orchestrator",
        description="Multi-agent orchestrator with governance enforcement",
    )
    parser.add_argument("--org-spec", required=True, help="Path to org spec YAML")
    parser.add_argument("--task", required=True, help="Task description")
    parser.add_argument("--scope", required=True, help="Implementer file scope (e.g., src/auth/)")
    parser.add_argument("--model", default="sonnet", help="Model to use (default: sonnet)")
    parser.add_argument("--cwd", default=None, help="Target project directory")
    parser.add_argument("--max-turns", default=10, type=int, help="Max turns per agent (default: 10)")

    args = parser.parse_args()

    asyncio.run(run_workflow(
        org_spec_path=args.org_spec,
        task=args.task,
        scope=args.scope,
        model=args.model,
        cwd=args.cwd,
        max_turns=args.max_turns,
    ))


if __name__ == "__main__":
    main()
