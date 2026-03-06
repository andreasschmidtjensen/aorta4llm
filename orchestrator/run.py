"""Multi-agent orchestrator with governance enforcement.

Reads the `workflow:` section from the org spec and runs each step in order,
passing each step's output to the next. Governance constraints are enforced
by PreToolUse hooks on each agent subprocess.

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
from pathlib import Path

import yaml

from integration.events import log_event
from integration.hooks import GovernanceHook
from orchestrator.agent import run_agent
from orchestrator.prompts import build_system_prompt


def _extract_feature_name(task: str) -> str:
    """Extract a short feature name from the task description."""
    words = re.sub(r"[^\w\s]", "", task.lower()).split()
    return "_".join(words[:3]) if words else "feature"


async def run_workflow(
    org_spec_path: str,
    task: str,
    scope: str = "",
    model: str = "sonnet",
    cwd: str | None = None,
    max_turns: int = 10,
    events_path: Path | str | None = None,
) -> dict:
    """Run the organizational workflow defined in the org spec.

    Each step in the org spec's `workflow:` list is executed in order.
    Step prompts support {task}, {scope}, and {feature} substitutions.
    Each step's output is appended to the next step's prompt.

    Args:
        org_spec_path: Path to org spec YAML.
        task: Natural language task description.
        scope: File path scope (substituted as {scope} in step prompts).
        model: Model to use for agents.
        cwd: Working directory (target project).
        max_turns: Maximum turns per agent.
        events_path: Explicit events file path. If None, uses cwd/.aorta/events.jsonl.

    Returns:
        Dict mapping agent IDs to their text outputs.
    """
    with open(org_spec_path) as f:
        spec = yaml.safe_load(f)

    workflow = spec.get("workflow", [])
    if not workflow:
        raise ValueError(f"No 'workflow:' section found in {org_spec_path}")

    project_dir = Path(cwd or ".")
    if events_path:
        events_path = Path(events_path)
    else:
        events_path = project_dir / ".aorta" / "events.jsonl"
    state_path = events_path.parent / "state.json"

    hook = GovernanceHook(org_spec_path, state_path=str(state_path),
                          events_path=str(events_path))

    feature = _extract_feature_name(task)
    context = {"task": task, "scope": scope, "feature": feature}

    print(f"\n{'='*60}")
    print(f"  AORTA Orchestrator — {spec.get('organization', 'workflow')}")
    print(f"{'='*60}")
    print(f"  Task:    {task}")
    print(f"  Feature: {feature}")
    print(f"  Scope:   {scope}")
    print(f"  Model:   {model}")
    print(f"  Steps:   {len(workflow)}")
    print(f"{'='*60}\n")

    log_event({"type": "workflow_start", "task": task,
               "scope": scope, "feature": feature}, events_path)

    results = {}
    prev_output = ""

    for i, step in enumerate(workflow, 1):
        agent_id = step["agent"]
        role = step["role"]
        agent_scope = step.get("scope", "").format(**context)

        print(f"Step {i}/{len(workflow)}: {role.upper()} ({agent_id})")
        print("-" * 40)

        hook.register_agent(agent_id, role, scope=agent_scope)
        log_event({"type": "phase_start", "phase": role,
                   "agent": agent_id, "task": task}, events_path)

        prompt = step["prompt"].format(**context)
        if prev_output:
            prompt += f"\n\nPrevious step output:\n{prev_output}"

        sys_prompt = build_system_prompt(role, spec, hook._service, agent_id)

        output = await run_agent(
            agent_id=agent_id,
            role=role,
            org_spec_path=org_spec_path,
            prompt=prompt,
            system_prompt=sys_prompt,
            allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
            model=model,
            cwd=cwd,
            events_path=events_path,
            state_path=state_path,
            max_turns=max_turns,
        )

        if step.get("achievements"):
            achieved = [a.format(**context) for a in step["achievements"]]
            hook._service.notify_action(agent_id, role, achieved=achieved)

        log_event({"type": "phase_complete", "phase": role,
                   "agent": agent_id}, events_path)

        results[agent_id] = output
        prev_output = output
        print(f"\n{role.capitalize()} complete. Output length: {len(output)} chars\n")

    print(f"\n{'='*60}")
    print("  WORKFLOW COMPLETE")
    print(f"{'='*60}")
    print(f"  Feature:   {feature}")
    print(f"  Dashboard: http://localhost:5111")
    print(f"{'='*60}\n")

    log_event({"type": "workflow_complete", "status": "success",
               "feature": feature}, events_path)

    return results


def main():
    parser = argparse.ArgumentParser(
        prog="orchestrator",
        description="Multi-agent orchestrator with governance enforcement",
    )
    parser.add_argument("--org-spec", required=True, help="Path to org spec YAML")
    parser.add_argument("--task", required=True, help="Task description")
    parser.add_argument("--scope", default="", help="File scope (e.g., src/auth/)")
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
