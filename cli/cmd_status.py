"""aorta status — show current governance state."""

import json
from pathlib import Path

from integration.events import read_events


def add_parser(subparsers):
    p = subparsers.add_parser("status", help="Show governance state for an org spec")
    p.add_argument("--org-spec", required=True, help="Path to org spec YAML")
    p.add_argument("--events-path", default=None, help="Events JSONL path")
    p.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    p.set_defaults(func=run)


def _find_state_path(org_spec_path: str) -> Path:
    """Find the state file for this org spec."""
    from integration.hooks import _default_state_path
    return _default_state_path(org_spec_path)


def run(args):
    import yaml

    org_spec_path = Path(args.org_spec)
    if not org_spec_path.exists():
        print(f"Org spec not found: {org_spec_path}")
        raise SystemExit(1)

    with open(org_spec_path) as f:
        spec = yaml.safe_load(f)

    # Find state and events
    state_path = _find_state_path(args.org_spec)
    events_path = Path(args.events_path) if args.events_path else Path(".aorta/events.jsonl")

    # Load state
    agents = {}
    achievements = []
    exceptions = []
    if state_path.exists():
        state = json.loads(state_path.read_text())
        for event in state.get("events", []):
            if event["type"] == "register":
                agents[event["agent"]] = {
                    "role": event["role"],
                    "scope": event.get("scope", ""),
                }
            elif event["type"] == "achieved":
                achievements.extend(event["objectives"])
        exceptions = state.get("exceptions", [])

    # Load recent events for stats
    events = read_events(events_path, limit=200) if events_path.exists() else []
    checks = [e for e in events if e.get("type") == "check"]
    approved = sum(1 for e in checks if e.get("decision") == "approve")
    blocked = sum(1 for e in checks if e.get("decision") == "block")

    if args.json_output:
        print(json.dumps({
            "org_spec": str(org_spec_path),
            "organization": spec.get("organization", "?"),
            "agents": agents,
            "achievements": achievements,
            "exceptions": exceptions,
            "access": spec.get("access", {}),
            "norms": len(spec.get("norms", [])),
            "stats": {"checks": len(checks), "approved": approved, "blocked": blocked},
            "state_path": str(state_path),
            "events_path": str(events_path),
        }, indent=2))
        return

    print(f"Org spec:      {org_spec_path}")
    print(f"Organization:  {spec.get('organization', '?')}")
    print(f"State file:    {state_path}")
    print(f"Events file:   {events_path}")
    print()

    # Agents
    if agents:
        print("Agents:")
        for name, info in agents.items():
            scope = info['scope'] or 'unrestricted'
            print(f"  {name:20s} role: {info['role']}, scope: {scope}")
    else:
        print("Agents: none registered")
    print()

    # Access map
    access = spec.get("access", {})
    if access:
        print(f"Access map ({len(access)} entries):")
        for path, level in access.items():
            print(f"  {path:20s} {level}")
        print()

    # Norms
    norms = spec.get("norms", [])
    if norms:
        print(f"Norms ({len(norms)}):")
        for i, norm in enumerate(norms):
            severity = f" [{norm['severity']}]" if norm.get("severity") else ""
            role = norm.get("role", "?")
            ntype = norm.get("type", "?")
            detail = ""
            if ntype == "scope":
                detail = f" — scope: {', '.join(norm.get('paths', []))}"
            elif ntype == "protected":
                detail = f" — {', '.join(norm.get('paths', []))}"
            elif ntype == "readonly":
                detail = f" — {', '.join(norm.get('paths', []))}"
            elif ntype == "forbidden_command":
                detail = f" — pattern: '{norm.get('command_pattern', '')}'"
            elif ntype == "required_before":
                detail = f" — '{norm.get('command_pattern', '')}' requires {norm.get('requires', '?')}"
            print(f"  #{i+1} {ntype} ({role}){severity}{detail}")
    print()

    # Achievements
    if achievements:
        print(f"Achievements: {', '.join(achievements)}")
    else:
        print("Achievements: none")
    print()

    # Allow-once exceptions
    if exceptions:
        print(f"Allow-once exceptions ({len(exceptions)}):")
        for exc in exceptions:
            agent_str = f" (agent: {exc['agent']})" if exc.get("agent", "*") != "*" else ""
            print(f"  {exc['path']}{agent_str} — {exc.get('uses', 0)} use(s) remaining")
        print()


    # Stats
    if checks:
        print(f"Recent activity ({len(checks)} checks):")
        print(f"  Approved: {approved}")
        print(f"  Blocked:  {blocked}")

        # Show last 5 blocks
        recent_blocks = [e for e in checks if e.get("decision") == "block"][-5:]
        if recent_blocks:
            print(f"\n  Last blocked actions:")
            for e in recent_blocks:
                ts = e.get("ts", "?")[:19]
                action = e.get("action", "?")
                path = e.get("path", "")
                reason = e.get("reason", "")
                # Truncate reason for display
                if len(reason) > 80:
                    reason = reason[:77] + "..."
                target = f" ({path})" if path else ""
                print(f"    [{ts}] {action}{target}")
                if reason:
                    print(f"      {reason}")
    else:
        print("Recent activity: none")
