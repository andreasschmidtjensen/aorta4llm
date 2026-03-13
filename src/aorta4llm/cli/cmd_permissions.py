"""aorta permissions — show effective permissions for an agent."""

from pathlib import Path

import yaml

from aorta4llm.integration.hooks import GovernanceHook


def add_parser(subparsers):
    p = subparsers.add_parser(
        "permissions",
        help="Show effective permissions for an agent",
    )
    p.add_argument("--org-spec", default=None, help="Path to org spec YAML (auto-detected from .aorta/)")
    p.add_argument("--agent", default="agent", help="Agent name (default: agent)")
    p.set_defaults(func=run)


def _check(hook, agent, tool, path):
    """Run a permission check and return the decision string."""
    ctx = {"tool_name": tool, "tool_input": {}}
    if tool == "Bash":
        ctx["tool_input"]["command"] = path
    else:
        ctx["tool_input"]["file_path"] = path
    result = hook.pre_tool_use(ctx, agent=agent, quiet=True)
    return result["decision"]


def run(args):
    from aorta4llm.cli.spec_utils import find_org_spec

    org_spec_path = find_org_spec(args.org_spec)

    with open(org_spec_path) as f:
        spec = yaml.safe_load(f)

    hook = GovernanceHook(org_spec_path)
    agent = args.agent
    role = hook._get_agent_role(agent)
    if not role:
        print(f"Agent '{agent}' is not registered.")
        raise SystemExit(1)

    print(f"Effective permissions for agent '{agent}' (role: {role})")
    print()

    # Access map entries
    access = spec.get("access", {})
    if access:
        print("Access map:")
        for path, level in access.items():
            # Build a test path that would match the access entry.
            # For directories (src/), append a filename: src/test
            # For glob patterns (*.key), expand to a concrete path: test.key
            # For exact files (.env), use as-is
            if "*" in path:
                test_path = path.replace("**/*", "dir/test").replace("*", "test")
            elif path.endswith("/"):
                test_path = path + "test"
            else:
                test_path = path
            read_ok = _check(hook, agent, "Read", test_path)
            write_ok = _check(hook, agent, "Write", test_path)
            read_sym = "✓" if read_ok == "approve" else "✗"
            write_sym = "✓" if write_ok == "approve" else "✗"
            print(f"  {path:20s} {level:12s}  read: {read_sym}  write: {write_sym}")
        print()

    # Command norms
    cmd_norms = [n for n in spec.get("norms", [])
                 if n.get("type") in ("forbidden_command", "required_before")]
    if cmd_norms:
        print("Command restrictions:")
        for norm in cmd_norms:
            pattern = norm.get("command_pattern", "?")
            ntype = norm.get("type")
            severity = norm.get("severity", "hard")
            if ntype == "required_before":
                req = norm.get("requires", "?")
                # Check if the requirement is currently met
                result = _check(hook, agent, "Bash", f"{pattern} -m 'test'")
                met = "met" if result == "approve" else "NOT met"
                print(f"  '{pattern}' requires '{req}' ({met})")
            else:
                print(f"  '{pattern}' [{severity}]")
        print()

    # Achievements
    achievements = []
    state_path = hook._state_path
    if state_path.exists():
        import json
        state = json.loads(state_path.read_text())
        for event in state.get("events", []):
            if event["type"] == "achieved":
                achievements.extend(event["objectives"])
    if achievements:
        print(f"Achievements: {', '.join(achievements)}")
    else:
        triggers = spec.get("achievement_triggers", [])
        if triggers:
            marks = [t["marks"] for t in triggers]
            print(f"Achievements: none (available: {', '.join(marks)})")

    # Self-protection reminder
    print()
    print("Always protected: .aorta/, .claude/ (governance infrastructure)")
    print("Always blocked:   mutating aorta commands via Bash (init, reset, allow-once, ...)")
