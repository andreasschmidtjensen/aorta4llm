"""aorta reset — clear governance state for an org spec."""

from pathlib import Path


def add_parser(subparsers):
    p = subparsers.add_parser("reset", help="Clear governance state (registered agents, achievements, events)")
    p.add_argument("--org-spec", required=True, help="Path to org spec YAML")
    p.add_argument("--events-path", default=None, help="Events JSONL path")
    p.add_argument("--keep-events", action="store_true", help="Keep event log, only clear state")
    p.set_defaults(func=run)


def run(args):
    from integration.hooks import _default_state_path

    state_path = _default_state_path(args.org_spec)
    events_path = Path(args.events_path) if args.events_path else Path(".aorta/events.jsonl")

    removed = []

    if state_path.exists():
        state_path.unlink()
        removed.append(f"state: {state_path}")

    if not args.keep_events and events_path.exists():
        events_path.unlink()
        removed.append(f"events: {events_path}")

    if removed:
        print("Cleared:")
        for r in removed:
            print(f"  {r}")
        print("\nAgents must be re-registered before the next hook invocation.")
        print(f"Run: aorta hook register --org-spec {args.org_spec} --agent <name> --role <role>")
    else:
        print("Nothing to clear — no state or events found.")
