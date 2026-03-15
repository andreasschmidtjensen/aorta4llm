"""aorta context — LLM-friendly governance summary for context injection."""

import json
from pathlib import Path

import yaml

from aorta4llm.cli.spec_utils import find_org_spec
from aorta4llm.governance.compiler import _resolve_includes


def add_parser(subparsers):
    p = subparsers.add_parser("context", help="Output LLM-friendly governance summary")
    p.add_argument("--org-spec", default=None, help="Path to org spec YAML (auto-detected from .aorta/)")
    p.set_defaults(func=run)


def _load_state(org_spec_path: str) -> dict:
    """Load state from the default state path."""
    from aorta4llm.integration.hooks import _default_state_path
    state_path = _default_state_path(org_spec_path)
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text())


def _group_access(access: dict) -> dict[str, list[str]]:
    """Group access map entries by level."""
    groups: dict[str, list[str]] = {"read-write": [], "read-only": [], "no-access": []}
    for path, level in access.items():
        groups.setdefault(level, []).append(path)
    return {k: v for k, v in groups.items() if v}


def _norm_to_plain(norm: dict) -> str:
    """Convert a norm to a plain-language description."""
    ntype = norm.get("type", "?")
    severity = norm.get("severity", "hard")

    if ntype == "forbidden_command":
        pattern = norm.get("command_pattern", "?")
        if severity == "soft":
            return f"Soft-blocked command: `{pattern}` (ask user before running)"
        return f"Blocked command: `{pattern}`"
    elif ntype == "required_before":
        pattern = norm.get("command_pattern", "?")
        requires = norm.get("requires", "?")
        return f"Must achieve `{requires}` before running `{pattern}`"
    elif ntype == "scope":
        paths = ", ".join(norm.get("paths", []))
        return f"Write scope limited to: {paths}"
    elif ntype == "protected":
        paths = ", ".join(norm.get("paths", []))
        return f"Protected (no read/write): {paths}"
    elif ntype == "readonly":
        paths = ", ".join(norm.get("paths", []))
        return f"Read-only: {paths}"
    return f"{ntype}"


def _trigger_how(trigger: dict) -> str:
    """Describe how to achieve a trigger in plain language."""
    parts = []
    if trigger.get("command_pattern"):
        parts.append(f"run `{trigger['command_pattern']}`")
    if trigger.get("exit_code") is not None:
        parts.append(f"exit code {trigger['exit_code']}")
    if trigger.get("path_pattern"):
        parts.append(f"on `{trigger['path_pattern']}`")
    if trigger.get("output_contains"):
        parts.append(f"output matches `{trigger['output_contains']}`")
    if trigger.get("reset_on_file_change"):
        parts.append("resets on file change")
    return ", ".join(parts) if parts else "automatic"


def run(args):
    org_spec_path = find_org_spec(args.org_spec)

    with open(org_spec_path) as f:
        spec = yaml.safe_load(f)

    resolved = _resolve_includes(dict(spec))
    state = _load_state(str(org_spec_path))

    # Collect achievements from state events
    achieved: set[str] = set()
    obligations: list[dict] = []
    hold = state.get("hold")
    for event in state.get("events", []):
        if event["type"] == "achieved":
            achieved.update(event.get("objectives", []))
        elif event["type"] == "obligation_created":
            obligations.append(event)
    # Remove fulfilled obligations
    obligations = [o for o in obligations if o["objective"] not in achieved]

    # --- Output sections ---

    print(f"# Governance: {resolved.get('organization', '?')}")
    print()

    # Hold
    if hold:
        reason = hold.get("reason", "unknown")
        print(f"**HOLD ACTIVE**: {reason}")
        print("All actions are blocked until the hold is lifted with `aorta continue`.")
        print()

    # Access
    access = resolved.get("access", {})
    if access:
        groups = _group_access(access)
        print("## Access")
        if "read-write" in groups:
            print(f"- Read-write: {', '.join(groups['read-write'])}")
        if "read-only" in groups:
            print(f"- Read-only: {', '.join(groups['read-only'])}")
        if "no-access" in groups:
            print(f"- No access: {', '.join(groups['no-access'])}")
        print()

    # Rules (norms in plain language)
    norms = resolved.get("norms", [])
    if norms:
        print("## Rules")
        for norm in norms:
            print(f"- {_norm_to_plain(norm)}")
        print()

    # Achievement gates
    triggers = resolved.get("achievement_triggers", [])
    counts_as = resolved.get("counts_as", [])
    if triggers or counts_as:
        print("## Achievement gates")
        for trigger in triggers:
            mark = trigger.get("marks", "?")
            status = "done" if mark in achieved else "pending"
            how = _trigger_how(trigger)
            print(f"- [{status}] {mark}: {how}")
        for ca in counts_as:
            mark = ca.get("marks", "")
            if not mark:
                continue
            when = ca.get("when", [])
            status = "done" if mark in achieved else "pending"
            print(f"- [{status}] {mark} = {' + '.join(when)}")
        print()

    # Sanctions
    sanctions = resolved.get("sanctions", [])
    if sanctions:
        print("## Sanctions")
        for s in sanctions:
            threshold = s.get("threshold", "?")
            action = s.get("action", "?")
            print(f"- After {threshold} violations: {action}")
        print()

    # Obligations
    if obligations:
        print("## Active obligations")
        for ob in obligations:
            obj = ob["objective"]
            deadline = ob.get("deadline", "false")
            dl_str = f" (deadline: {deadline})" if deadline != "false" else ""
            print(f"- {obj}{dl_str}")
        print()
