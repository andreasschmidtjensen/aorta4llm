"""aorta status — show current governance state."""

import json
from pathlib import Path

from aorta4llm.integration.events import read_events


def add_parser(subparsers):
    p = subparsers.add_parser("status", help="Show governance state for an org spec")
    p.add_argument("--org-spec", default=None, help="Path to org spec YAML (auto-detected from .aorta/)")
    p.add_argument("--events-path", default=None, help="Events JSONL path")
    p.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    p.add_argument("--tree", action="store_true", help="Show policy as a tree view")
    p.set_defaults(func=run)


def _find_state_path(org_spec_path: str) -> Path:
    """Find the state file for this org spec."""
    from aorta4llm.integration.hooks import _default_state_path
    return _default_state_path(org_spec_path)


def _load_pack_norms(pack_name: str) -> list[dict]:
    """Load norms from a pack, returning them with _pack metadata."""
    from aorta4llm.cli.cmd_init import TEMPLATES_DIR
    packs_dir = TEMPLATES_DIR.parent / "packs"
    pack_path = packs_dir / f"{pack_name}.yaml"
    if not pack_path.exists():
        return []
    import yaml
    with open(pack_path) as f:
        pack = yaml.safe_load(f) or {}
    return pack.get("norms", [])


def _norm_signature(norm: dict) -> tuple:
    """Create a signature for matching norms to their pack origin."""
    return (
        norm.get("type", ""),
        norm.get("role", ""),
        norm.get("command_pattern", ""),
        norm.get("severity", ""),
    )


def _build_pack_provenance(packs: list[str]) -> dict[tuple, str]:
    """Map norm signatures to pack names for provenance display."""
    provenance: dict[tuple, str] = {}
    for pack_name in packs:
        for norm in _load_pack_norms(pack_name):
            provenance[_norm_signature(norm)] = pack_name
    return provenance


def _format_norm_line(norm: dict, pack_name: str | None = None) -> str:
    """Format a single norm for tree display."""
    ntype = norm.get("type", "?")
    severity = norm.get("severity")
    tag = f"[{severity}]" if severity else "[hard]"
    pack_suffix = f"  (from {pack_name})" if pack_name else ""

    if ntype == "forbidden_command":
        pattern = norm.get("command_pattern", "?")
        return f"{tag} {pattern}{pack_suffix}"
    elif ntype == "required_before":
        pattern = norm.get("command_pattern", "?")
        requires = norm.get("requires", "?")
        return f"requires {requires} before {pattern}{pack_suffix}"
    elif ntype == "scope":
        paths = ", ".join(norm.get("paths", []))
        return f"scope: {paths}{pack_suffix}"
    elif ntype == "protected":
        paths = ", ".join(norm.get("paths", []))
        return f"protected: {paths}{pack_suffix}"
    elif ntype == "readonly":
        paths = ", ".join(norm.get("paths", []))
        return f"readonly: {paths}{pack_suffix}"
    else:
        return f"{ntype}{pack_suffix}"


def run_tree(spec: dict, achievements: list[str], packs: list[str],
             hold: dict | None = None,
             obligations: list[dict] | None = None):
    """Render the org spec as a tree with box-drawing characters."""
    org_name = spec.get("organization", "?")
    roles = spec.get("roles", {})
    access = spec.get("access", {})
    norms = spec.get("norms", [])
    triggers = spec.get("achievement_triggers", [])
    obligations = obligations or []

    # Build provenance map
    provenance = _build_pack_provenance(packs)

    # Collect all known objectives from triggers and roles
    all_objectives: list[str] = []
    seen = set()
    for trigger in triggers:
        obj = trigger.get("marks", "")
        if obj and obj not in seen:
            all_objectives.append(obj)
            seen.add(obj)
    for role_def in roles.values():
        for obj in role_def.get("objectives", []):
            if obj not in seen:
                all_objectives.append(obj)
                seen.add(obj)

    achieved_set = set(achievements)

    # Determine which sections exist to know what's "last"
    sections: list[str] = []
    if hold:
        sections.append("hold")
    if roles:
        sections.append("roles")
    if access:
        sections.append("access")
    if norms:
        sections.append("norms")
    if obligations:
        sections.append("obligations")
    if all_objectives:
        sections.append("achievements")
    if packs:
        sections.append("packs")

    print(org_name)

    for si, section in enumerate(sections):
        is_last_section = (si == len(sections) - 1)
        branch = "└── " if is_last_section else "├── "
        cont = "    " if is_last_section else "│   "

        if section == "hold":
            print(f"{branch}HOLD ACTIVE")
            reason = hold.get("reason", "unknown") if hold else "unknown"
            print(f"{cont}└── {reason}")

        elif section == "roles":
            for ri, (role_name, role_def) in enumerate(roles.items()):
                is_last_role = (ri == len(roles) - 1) and is_last_section
                if len(roles) > 1:
                    # Multiple roles: show each as a sub-branch
                    rbranch = "└── " if (ri == len(roles) - 1) else "├── "
                    if ri == 0:
                        print(f"{branch}Roles")
                    rcont = cont + ("    " if ri == len(roles) - 1 else "│   ")
                    print(f"{cont}{rbranch}Role: {role_name}")
                    objs = ", ".join(role_def.get("objectives", []))
                    caps = ", ".join(role_def.get("capabilities", []))
                    if caps:
                        print(f"{rcont}├── Objectives: {objs}")
                        print(f"{rcont}└── Capabilities: {caps}")
                    else:
                        print(f"{rcont}└── Objectives: {objs}")
                else:
                    objs = ", ".join(role_def.get("objectives", []))
                    caps = ", ".join(role_def.get("capabilities", []))
                    print(f"{branch}Role: {role_name}")
                    if caps:
                        print(f"{cont}├── Objectives: {objs}")
                        print(f"{cont}└── Capabilities: {caps}")
                    else:
                        print(f"{cont}└── Objectives: {objs}")

        elif section == "access":
            print(f"{branch}Access")
            items = list(access.items())
            for ai, (path, level) in enumerate(items):
                is_last = ai == len(items) - 1
                ab = "└── " if is_last else "├── "
                print(f"{cont}{ab}{path:<20s} {level}")

        elif section == "norms":
            print(f"{branch}Norms")
            for ni, norm in enumerate(norms):
                is_last = ni == len(norms) - 1
                nb = "└── " if is_last else "├── "
                pack_name = provenance.get(_norm_signature(norm))
                line = _format_norm_line(norm, pack_name)
                print(f"{cont}{nb}{line}")

        elif section == "obligations":
            print(f"{branch}Obligations")
            for oi, ob in enumerate(obligations):
                is_last = oi == len(obligations) - 1
                ob_branch = "└── " if is_last else "├── "
                obj = ob["objective"]
                deadline = ob.get("deadline", "false")
                dl_str = f"  (deadline: {deadline})" if deadline != "false" else ""
                print(f"{cont}{ob_branch}! {obj}{dl_str}")

        elif section == "achievements":
            print(f"{branch}Achievements")
            for ai, obj in enumerate(all_objectives):
                is_last = ai == len(all_objectives) - 1
                ab = "└── " if is_last else "├── "
                marker = "●" if obj in achieved_set else "○"
                # Find trigger details
                detail = ""
                for trigger in triggers:
                    if trigger.get("marks") == obj:
                        parts = []
                        if trigger.get("command_pattern"):
                            parts.append(trigger["command_pattern"])
                        if trigger.get("exit_code") is not None:
                            parts.append(f"exit {trigger['exit_code']}")
                        if trigger.get("reset_on_file_change"):
                            parts.append("resets on change")
                        if parts:
                            detail = f"  ({', '.join(parts)})"
                        break
                print(f"{cont}{ab}{marker} {obj}{detail}")

        elif section == "packs":
            print(f"{branch}Packs: {', '.join(packs)}")


def run(args):
    import yaml
    from aorta4llm.cli.spec_utils import find_org_spec

    org_spec_path = find_org_spec(args.org_spec)

    with open(org_spec_path) as f:
        spec = yaml.safe_load(f)

    # Find state and events
    state_path = _find_state_path(str(org_spec_path))
    events_path = Path(args.events_path) if args.events_path else Path(".aorta/events.jsonl")

    # Load state
    agents = {}
    achievements = []
    obligations: list[dict] = []
    exceptions = []
    if state_path.exists():
        state = json.loads(state_path.read_text())
        achieved_set: set[str] = set()
        for event in state.get("events", []):
            if event["type"] == "register":
                agents[event["agent"]] = {
                    "role": event["role"],
                    "scope": event.get("scope", ""),
                }
            elif event["type"] == "achieved":
                achievements.extend(event["objectives"])
                achieved_set.update(event["objectives"])
            elif event["type"] == "obligation_created":
                obligations.append({
                    "agent": event["agent"],
                    "role": event["role"],
                    "objective": event["objective"],
                    "deadline": event.get("deadline", "false"),
                })
        # Remove fulfilled obligations (objective was achieved)
        obligations = [o for o in obligations if o["objective"] not in achieved_set]
        exceptions = state.get("exceptions", [])

    # Load recent events for stats
    events = read_events(events_path, limit=200) if events_path.exists() else []
    checks = [e for e in events if e.get("type") == "check"]
    approved = sum(1 for e in checks if e.get("decision") == "approve")
    blocked = sum(1 for e in checks if e.get("decision") == "block")

    if args.tree:
        # Resolve includes so tree shows effective policy
        from aorta4llm.governance.compiler import _resolve_includes
        packs = list(spec.get("include", []))
        resolved = _resolve_includes(dict(spec))
        hold = None
        if state_path.exists():
            state_data = json.loads(state_path.read_text())
            hold = state_data.get("hold")
        run_tree(resolved, achievements, packs, hold=hold, obligations=obligations)
        return

    if args.json_output:
        print(json.dumps({
            "org_spec": str(org_spec_path),
            "organization": spec.get("organization", "?"),
            "agents": agents,
            "achievements": achievements,
            "obligations": obligations,
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

    # Obligations
    if obligations:
        print(f"Active obligations ({len(obligations)}):")
        for ob in obligations:
            deadline = ob.get("deadline", "false")
            dl_str = f" (deadline: {deadline})" if deadline != "false" else ""
            print(f"  ! {ob['objective']}{dl_str}  [{ob['agent']}]")
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
