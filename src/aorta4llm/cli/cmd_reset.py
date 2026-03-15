"""aorta reset — clear governance state for an org spec."""

import json
from pathlib import Path


def add_parser(subparsers):
    p = subparsers.add_parser("reset", help="Clear governance state (achievements, counters, holds)")
    p.add_argument("--org-spec", default=None, help="Path to org spec YAML (auto-detected from .aorta/)")
    p.add_argument("--events-path", default=None, help="Events JSONL path")
    p.add_argument("--keep-events", action="store_true", help="Keep event log, only clear state")
    p.set_defaults(func=run)


def run(args):
    import yaml
    from aorta4llm.cli.spec_utils import find_org_spec
    from aorta4llm.integration.hooks import GovernanceHook, _default_state_path, _legacy_state_path

    org_spec_path = find_org_spec(args.org_spec)
    org_spec = str(org_spec_path)
    state_path = _default_state_path(org_spec)
    legacy_path = _legacy_state_path(org_spec)
    events_path = Path(args.events_path) if args.events_path else Path(".aorta/events.jsonl")

    # Read existing registrations before clearing.
    registrations: list[dict] = []
    if state_path.exists():
        state = json.loads(state_path.read_text())
        registrations = [e for e in state.get("events", []) if e.get("type") == "register"]

    cleared = []

    if state_path.exists():
        state_path.unlink()
        cleared.append(f"state: {state_path}")

    # Also clean up legacy state file if it still exists.
    if legacy_path.exists():
        legacy_path.unlink()
        cleared.append(f"state (legacy): {legacy_path}")

    if not args.keep_events and events_path.exists():
        events_path.unlink()
        cleared.append(f"events: {events_path}")

    if cleared:
        print("Cleared:")
        for r in cleared:
            print(f"  {r}")
    else:
        print("Nothing to clear — no state or events found.")

    # Derive scope from org spec access map (authoritative source).
    with open(org_spec_path) as f:
        spec = yaml.safe_load(f)
    rw_scopes = [k for k, v in spec.get("access", {}).items() if v == "read-write"]
    spec_scope = " ".join(rw_scopes)

    # Re-register agents from previous state, or from the org spec.
    hook = GovernanceHook(org_spec, events_path=str(events_path))
    if registrations:
        for reg in registrations:
            scope = reg.get("scope", "") or spec_scope
            hook.register_agent(reg["agent"], reg["role"], scope)
            print(f"Re-registered agent '{reg['agent']}' as '{reg['role']}' (scope: {scope or 'unrestricted'})")
    else:
        roles = list(spec.get("roles", {}).keys())
        role = roles[0] if roles else "agent"
        hook.register_agent("agent", role, spec_scope)
        print(f"Registered agent 'agent' as '{role}' (scope: {spec_scope or 'unrestricted'})")
