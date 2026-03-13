"""aorta continue — clear an active hold and reset guardrails counters."""

from aorta4llm.cli.spec_utils import find_org_spec
from aorta4llm.integration.hooks import GovernanceHook


def add_parser(subparsers):
    p = subparsers.add_parser("continue", help="Clear an active hold")
    p.add_argument("--org-spec", default=None)
    p.set_defaults(func=run)


def run(args):
    spec_path = find_org_spec(args.org_spec)
    hook = GovernanceHook(spec_path)

    if not hook._hold:
        print("No active hold.")
        return

    reason = hook._hold.get("reason", "unknown")
    hook.clear_hold()
    print(f"Hold cleared: {reason}")
    print("Guardrails counters have been reset.")
