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
    from aorta4llm.cli.spec_utils import find_org_spec
    from aorta4llm.integration.hooks import _default_state_path, _legacy_state_path

    org_spec = str(find_org_spec(args.org_spec))
    state_path = _default_state_path(org_spec)
    legacy_path = _legacy_state_path(org_spec)
    events_path = Path(args.events_path) if args.events_path else Path(".aorta/events.jsonl")

    cleared = []

    if state_path.exists():
        state = json.loads(state_path.read_text())
        # Keep registration events, clear everything else.
        reg_events = [e for e in state.get("events", []) if e.get("type") == "register"]
        state_path.write_text(json.dumps({"events": reg_events, "soft_blocks": {}}, indent=2))
        n_removed = len(state.get("events", [])) - len(reg_events)
        cleared.append(f"state: kept {len(reg_events)} registration(s), cleared {n_removed} event(s) and counters")

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
