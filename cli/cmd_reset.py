"""aorta reset — clear governance state for an org spec."""

from pathlib import Path


def add_parser(subparsers):
    p = subparsers.add_parser("reset", help="Clear governance state (registered agents, achievements, events)")
    p.add_argument("--org-spec", default=None, help="Path to org spec YAML (auto-detected from .aorta/)")
    p.add_argument("--events-path", default=None, help="Events JSONL path")
    p.add_argument("--keep-events", action="store_true", help="Keep event log, only clear state")
    p.set_defaults(func=run)


def run(args):
    from cli.spec_utils import find_org_spec
    from integration.hooks import _default_state_path, _legacy_state_path

    org_spec = str(find_org_spec(args.org_spec))
    state_path = _default_state_path(org_spec)
    legacy_path = _legacy_state_path(org_spec)
    events_path = Path(args.events_path) if args.events_path else Path(".aorta/events.jsonl")

    removed = []

    if state_path.exists():
        state_path.unlink()
        removed.append(f"state: {state_path}")

    # Also clean up legacy state file if it still exists.
    if legacy_path.exists():
        legacy_path.unlink()
        removed.append(f"state (legacy): {legacy_path}")

    if not args.keep_events and events_path.exists():
        events_path.unlink()
        removed.append(f"events: {events_path}")

    if removed:
        print("Cleared:")
        for r in removed:
            print(f"  {r}")
        print("\nAgents must be re-registered before the next hook invocation.")
        print(f"Run: aorta hook register --org-spec {org_spec} --agent <name> --role <role>")
    else:
        print("Nothing to clear — no state or events found.")
