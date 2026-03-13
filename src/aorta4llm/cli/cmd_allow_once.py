"""aorta allow-once — grant a one-time exception for a blocked path."""

import json
import time
from pathlib import Path


def add_parser(subparsers):
    p = subparsers.add_parser("allow-once", help="Grant a one-time exception for a blocked path")
    p.add_argument("path", help="Path to allow (e.g. .env)")
    p.add_argument("--org-spec", default=None, help="Path to org spec YAML (auto-detected from .aorta/)")
    p.add_argument("--agent", default="*", help="Agent to allow (default: all)")
    p.set_defaults(func=run)


def run(args):
    from aorta4llm.cli.spec_utils import find_org_spec
    from aorta4llm.integration.events import log_event
    from aorta4llm.integration.hooks import _default_state_path

    org_spec_path = find_org_spec(args.org_spec)
    state_path = _default_state_path(str(org_spec_path))
    events_path = state_path.parent / "events.jsonl"

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

    log_event({
        "type": "allow_once",
        "path": args.path,
        "agent": args.agent,
        "org_spec": org_spec_path.stem,
    }, events_path)

    agent_desc = f"agent '{args.agent}'" if args.agent != "*" else "all agents"
    print(f"Granted one-time exception for '{args.path}' ({agent_desc}).")
    print(f"The next access to this path will be approved; subsequent accesses will be blocked again.")
