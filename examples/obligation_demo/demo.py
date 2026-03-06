#!/usr/bin/env python3
"""Obligation lifecycle demo: gates, unblocks, and deadline violations.

Shows what separates formal governance from a static JSON allowlist:
- Prohibitions that depend on runtime state (architecture gate)
- Obligations with deadlines that can be violated
- Organizational state changes that alter what's permitted

Run: uv run python examples/obligation_demo/demo.py
     uv run python examples/obligation_demo/demo.py --emit-events  (for dashboard)
"""

import argparse
import sys
from pathlib import Path

from governance.service import GovernanceService

SPEC_PATH = Path(__file__).parent.parent.parent / "org-specs" / "arch_gate_workflow.yaml"

# Event emitter (optional, for dashboard integration)
_emit_events = False
_events_path = None


def emit(event: dict):
    if _emit_events and _events_path:
        from integration.events import log_event
        log_event(event, _events_path)


def run_demo():
    svc = GovernanceService(SPEC_PATH)

    print()
    print("=" * 68)
    print("  AORTA GOVERNANCE DEMO: Obligations, Gates, and Violations")
    print("=" * 68)
    print()
    print("  This demo shows three things a JSON allowlist cannot do:")
    print("  1. Block an action based on whether something has happened yet")
    print("  2. Track obligations with deadlines")
    print("  3. Detect and record when an obligation is violated")
    print()

    # Register agents
    svc.register_agent("architect-1", "architect", scope="")
    svc.register_agent("impl-1", "implementer", scope="src/auth/")
    svc.register_agent("reviewer-1", "reviewer", scope="")

    emit({"type": "register", "agent": "architect-1", "role": "architect", "scope": ""})
    emit({"type": "register", "agent": "impl-1", "role": "implementer", "scope": "src/auth/"})
    emit({"type": "register", "agent": "reviewer-1", "role": "reviewer", "scope": ""})

    events = []

    def check(agent, role, action, path, label=""):
        result = svc.check_permission(agent, role, action, {"path": path})
        status = "PERMIT" if result.permitted else "BLOCK "
        print(f"  {status} {action}({path})")
        if not result.permitted:
            print(f"         reason: {result.reason}")
        decision = "approve" if result.permitted else "block"
        events.append({"agent": agent, "action": f"{action}({path})", "decision": decision})
        emit({"type": "check", "agent": agent, "role": role,
              "action": action, "path": path, "decision": decision})
        return result

    # ── Act 1: The Architecture Gate ──────────────────────────────

    print("-" * 68)
    print("  Act 1: The Architecture Gate")
    print("-" * 68)
    print()
    print("  The implementer tries to write code before the architect")
    print("  has reviewed the architecture for their scope.")
    print()

    result = check("impl-1", "implementer", "write_file", "src/auth/handler.py")
    assert not result.permitted

    print()
    print("  --> BLOCKED. The prohibition condition evaluates")
    print("      not(architecture_reviewed_for_scope('src/auth/handler.py'))")
    print("      which checks whether achieved(architecture_reviewed('src/auth/'))")
    print("      exists. It doesn't yet. So the write is forbidden.")
    print()
    print("      A static allowlist cannot express 'allowed AFTER X happens.'")
    print()

    # ── Act 2: The Unblock ────────────────────────────────────────

    print("-" * 68)
    print("  Act 2: Architecture Review Lifts the Gate")
    print("-" * 68)
    print()
    print("  The architect reviews and approves the architecture.")
    print()

    r = svc.notify_action("architect-1", "architect",
                          achieved=["architecture_reviewed('src/auth/')"])
    print("  -> Achieved: architecture_reviewed('src/auth/')")
    emit({"type": "phase_complete", "phase": "architecture_review", "agent": "architect-1"})

    print()
    print("  Now the implementer tries the same file again.")
    print()

    result = check("impl-1", "implementer", "write_file", "src/auth/handler.py")
    assert result.permitted

    print()
    print("  --> PERMITTED. Same file, same agent, same role.")
    print("      The only change: organizational state. The architecture")
    print("      review achievement now satisfies the prohibition condition.")
    print()

    # Also show scope enforcement still works
    print("  (Scope enforcement still active:)")
    result_out = check("impl-1", "implementer", "write_file", "src/api/routes.py")
    assert not result_out.permitted
    print()

    # ── Act 3: The Deadline Violation ─────────────────────────────

    print("-" * 68)
    print("  Act 3: The Deadline Violation")
    print("-" * 68)
    print()
    print("  The implementer finishes and requests review.")
    print()

    r = svc.notify_action("impl-1", "implementer",
                          achieved=["feature_implemented(auth)", "review_requested(auth)"])
    print("  -> Achieved: feature_implemented(auth)")
    print("  -> Achieved: review_requested(auth)")

    for c in r.norms_changed:
        if c.type == "activated":
            print(f"  -> Obligation ACTIVATED: {c.deontic} {c.objective}")
            print(f"     deadline: {c.deadline}")
            emit({"type": "norm_change", "agent": "reviewer-1", "role": "reviewer",
                  "change": "activated", "deontic": c.deontic,
                  "objective": c.objective, "deadline": c.deadline})

    # Show the obligation is active
    obls = svc.get_obligations("reviewer-1", "reviewer")
    if obls["obligations"]:
        obl = obls["obligations"][0]
        print()
        print(f"  Active obligation for reviewer:")
        print(f"    {obl['deontic']} to achieve: {obl['objective']}")
        print(f"    deadline: {obl['deadline']}")
        print(f"    status: {obl['status']}")

    print()
    print("  The reviewer does NOT complete the review in time.")
    print("  The deadline is reached.")
    print()

    r = svc.notify_action("reviewer-1", "reviewer",
                          deadlines_reached=["review_deadline(auth)"])

    for c in r.norms_changed:
        if c.type == "violated":
            print(f"  !! VIOLATION DETECTED: {c.deontic} {c.objective}")
            print(f"     The reviewer was obligated to achieve {c.objective}")
            print(f"     by deadline {c.deadline}, but did not.")
            emit({"type": "norm_change", "agent": "reviewer-1", "role": "reviewer",
                  "change": "violated", "deontic": c.deontic,
                  "objective": c.objective, "deadline": c.deadline})

    # Show violation in obligations query
    obls = svc.get_obligations("reviewer-1", "reviewer")
    viol_opts = [o for o in obls["options"] if o["type"] == "violation"]
    if viol_opts:
        print()
        print(f"  Recorded violations:")
        for v in viol_opts:
            print(f"    - {v['deontic']} {v['objective']}")

    print()
    print("  --> The system tracked an obligation over time, detected that it")
    print("      was not fulfilled before its deadline, and recorded a formal")
    print("      violation. This is auditable, queryable, and actionable.")
    print()
    print("      No prompt instruction, JSON config, or tool allowlist can do this.")
    print()

    # ── Act 4: Event Summary ──────────────────────────────────────

    print("-" * 68)
    print("  Event Summary (what the dashboard shows)")
    print("-" * 68)
    print()
    for i, e in enumerate(events, 1):
        status = "APPROVED" if e["decision"] == "approve" else "BLOCKED "
        print(f"  [{i}] {e['agent']:14s} {e['action']:40s} {status}")

    print()
    print("  Norm lifecycle:")
    print("    [activated] reviewer-1  obliged code_reviewed(auth)")
    print("    [violated]  reviewer-1  obliged code_reviewed(auth)")
    print()

    if _emit_events:
        print(f"  Events written to {_events_path}")
        print("  View in dashboard: uv run python -m dashboard.server \\")
        print(f"      --org-spec {SPEC_PATH} --port 5111")
    else:
        print("  Run with --emit-events to write events for the dashboard.")

    print()


def main():
    global _emit_events, _events_path
    parser = argparse.ArgumentParser(description="Obligation lifecycle demo")
    parser.add_argument("--emit-events", action="store_true",
                        help="Write events to .aorta/events.jsonl for dashboard")
    parser.add_argument("--events-path", default=".aorta/events.jsonl",
                        help="Path for events JSONL file")
    args = parser.parse_args()

    _emit_events = args.emit_events
    if _emit_events:
        _events_path = Path(args.events_path)
        _events_path.parent.mkdir(parents=True, exist_ok=True)

    run_demo()


if __name__ == "__main__":
    main()
