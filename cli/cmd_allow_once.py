"""aorta allow-once — grant a one-time exception for a blocked path."""

import json
import time
from pathlib import Path


def add_parser(subparsers):
    p = subparsers.add_parser("allow-once", help="Grant a one-time exception for a blocked path")
    p.add_argument("--org-spec", required=True, help="Path to org spec YAML")
    p.add_argument("--path", required=True, help="Path to allow (e.g. .env)")
    p.add_argument("--agent", default="*", help="Agent to allow (default: all)")
    p.set_defaults(func=run)


def run(args):
    from integration.hooks import _default_state_path

    state_path = _default_state_path(args.org_spec)
    state: dict = {}
    if state_path.exists():
        state = json.loads(state_path.read_text())

    exceptions = state.setdefault("exceptions", [])
    exception = {
        "path": args.path,
        "agent": args.agent,
        "ts": time.time(),
        "uses": 1,
    }
    exceptions.append(exception)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))

    agent_desc = f"agent '{args.agent}'" if args.agent != "*" else "all agents"
    print(f"Granted one-time exception for '{args.path}' ({agent_desc}).")
    print(f"The next access to this path will be approved; subsequent accesses will be blocked again.")
