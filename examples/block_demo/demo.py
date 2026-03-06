#!/usr/bin/env python3
"""Block demo: five acts of governance enforcement.

Shows diverse norm types that produce visible blocks:
- File classification (secrets protection)
- Conditional gates (security approval)
- Role separation (source code locks)
- Obligation deadline violations
- Multi-condition gates (deployment approval)

Run: uv run python examples/block_demo/demo.py
     uv run python examples/block_demo/demo.py --emit-events  (for dashboard)
"""

import argparse
import sys
from pathlib import Path

from governance.service import GovernanceService

SPEC_PATH = Path(__file__).parent.parent.parent / "org-specs" / "security_workflow.yaml"

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
    print("  AORTA GOVERNANCE DEMO: Blocks, Gates, and Role Separation")
    print("=" * 68)
    print()
    print("  Five acts showing what deterministic governance enforces:")
    print("  1. File classification — sensitive files are off-limits")
    print("  2. Conditional gates — blocked until security approves")
    print("  3. Role separation — testers can't modify source code")
    print("  4. Deadline violations — obligations tracked over time")
    print("  5. Multi-condition gates — deploy needs tests AND review")
    print()

    # Register agents
    svc.register_agent("security-1", "security_lead", scope="")
    svc.register_agent("dev-1", "developer", scope="src/auth/")
    svc.register_agent("tester-1", "tester", scope="")
    svc.register_agent("deployer-1", "deployer", scope="")

    emit({"type": "register", "agent": "security-1", "role": "security_lead", "scope": ""})
    emit({"type": "register", "agent": "dev-1", "role": "developer", "scope": "src/auth/"})
    emit({"type": "register", "agent": "tester-1", "role": "tester", "scope": ""})
    emit({"type": "register", "agent": "deployer-1", "role": "deployer", "scope": ""})

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
              "action": action, "path": path, "decision": decision,
              "reason": "" if result.permitted else result.reason})
        return result

    # ── Act 1: Secrets Protection ────────────────────────────────

    print("-" * 68)
    print("  Act 1: Secrets Protection")
    print("-" * 68)
    print()
    print("  The developer tries to write sensitive files.")
    print("  These are classified as off-limits regardless of scope.")
    print()

    result = check("dev-1", "developer", "write_file", ".env")
    assert not result.permitted

    result = check("dev-1", "developer", "write_file", "config/prod/db.yaml")
    assert not result.permitted

    print()
    print("  --> BLOCKED. The is_sensitive_file rule matches .env files")
    print("      and anything under config/prod/. No role exception.")
    print()
    print("      This is file classification — certain paths are")
    print("      categorically forbidden based on pattern matching.")
    print()

    # ── Act 2: Security Gate ─────────────────────────────────────

    print("-" * 68)
    print("  Act 2: Security Gate")
    print("-" * 68)
    print()
    print("  The developer tries to write source code before the")
    print("  security lead has approved the plan for their scope.")
    print()

    result = check("dev-1", "developer", "write_file", "src/auth/handler.py")
    assert not result.permitted

    print()
    print("  --> BLOCKED. The condition checks whether")
    print("      achieved(security_plan_approved('src/auth/')) exists.")
    print("      It doesn't yet. The gate holds.")
    print()
    print("  The security lead reviews and approves the security plan.")
    print()

    r = svc.notify_action("security-1", "security_lead",
                          achieved=["security_plan_approved('src/auth/')"])
    print("  -> Achieved: security_plan_approved('src/auth/')")
    emit({"type": "phase_complete", "phase": "security_review", "agent": "security-1"})

    print()
    print("  Now the developer tries the same file again.")
    print()

    result = check("dev-1", "developer", "write_file", "src/auth/handler.py")
    assert result.permitted

    print()
    print("  --> PERMITTED. Same file, same agent, same role.")
    print("      Organizational state changed — the security gate lifted.")
    print()

    # Scope enforcement still active
    print("  (Scope enforcement still active:)")
    result = check("dev-1", "developer", "write_file", "src/api/routes.py")
    assert not result.permitted
    print()

    # ── Act 3: Role Separation ────────────────────────────────────

    print("-" * 68)
    print("  Act 3: Role Separation")
    print("-" * 68)
    print()
    print("  Testers test. Reviewers review. Neither writes source code.")
    print()

    result = check("tester-1", "tester", "write_file", "src/auth/handler.py")
    assert not result.permitted

    result = check("tester-1", "tester", "write_file", "reports/test_results.md")
    assert result.permitted

    print()

    result = check("security-1", "security_lead", "write_file", "src/auth/handler.py")
    assert not result.permitted

    result = check("security-1", "security_lead", "write_file", "docs/security_review.md")
    assert result.permitted

    print()
    print("  --> Tester: .py BLOCKED, .md PERMITTED")
    print("      Security lead: .py BLOCKED, .md PERMITTED")
    print()
    print("      Separation of concerns enforced deterministically.")
    print("      The tester can run tests and write reports.")
    print("      The security lead can write reviews.")
    print("      Neither can touch source code.")
    print()

    # ── Act 4: Deadline Violation ──────────────────────────────────

    print("-" * 68)
    print("  Act 4: Deadline Violation")
    print("-" * 68)
    print()
    print("  A new feature needs security review. The obligation activates.")
    print()

    # Trigger the review obligation: review_requested(payments) is achieved.
    # We notify via security_lead so the NC diff captures the obligation
    # activation (achievements are global facts — the agent parameter controls
    # whose norms appear in the returned diff).
    r = svc.notify_action("security-1", "security_lead",
                          achieved=["review_requested(payments)"])

    for c in r.norms_changed:
        if c.type == "activated":
            print(f"  -> Obligation ACTIVATED: {c.deontic} {c.objective}")
            print(f"     deadline: {c.deadline}")
            emit({"type": "norm_change", "agent": "security-1", "role": "security_lead",
                  "change": "activated", "deontic": c.deontic,
                  "objective": c.objective, "deadline": c.deadline})

    # Check obligations exist
    obls = svc.get_obligations("security-1", "security_lead")
    if obls["obligations"]:
        obl = obls["obligations"][0]
        print()
        print(f"  Active obligation for security_lead:")
        print(f"    {obl['deontic']} to achieve: {obl['objective']}")
        print(f"    deadline: {obl['deadline']}")
        print(f"    status: {obl['status']}")

    print()
    print("  The security lead does NOT complete the review in time.")
    print("  The deadline is reached.")
    print()

    r = svc.notify_action("security-1", "security_lead",
                          deadlines_reached=["review_deadline(payments)"])

    for c in r.norms_changed:
        if c.type == "violated":
            print(f"  !! VIOLATION DETECTED: {c.deontic} {c.objective}")
            print(f"     The security lead was obligated to achieve {c.objective}")
            print(f"     by deadline {c.deadline}, but did not.")
            emit({"type": "norm_change", "agent": "security-1", "role": "security_lead",
                  "change": "violated", "deontic": c.deontic,
                  "objective": c.objective, "deadline": c.deadline})

    print()
    print("  --> Obligations are tracked over time. Violations are formal,")
    print("      auditable records — not hopes that agents followed instructions.")
    print()

    # ── Act 5: Deploy Gate ─────────────────────────────────────────

    print("-" * 68)
    print("  Act 5: Deploy Gate (Multi-Condition)")
    print("-" * 68)
    print()
    print("  Deployment requires BOTH tests passing AND security review.")
    print("  Neither alone is sufficient.")
    print()

    result = check("deployer-1", "deployer", "deploy", "production")
    assert not result.permitted

    print()
    print("  Tests pass...")
    svc.notify_action("dev-1", "developer",
                      achieved=["feature_implemented(production)",
                                "tests_passing(production)"])
    print("  -> Achieved: tests_passing(production)")
    emit({"type": "phase_complete", "phase": "testing", "agent": "dev-1"})
    print()

    result = check("deployer-1", "deployer", "deploy", "production")
    assert not result.permitted

    print()
    print("  --> Still BLOCKED. Tests pass but security review is missing.")
    print()
    print("  Security review completes...")
    svc.notify_action("security-1", "security_lead",
                      achieved=["security_reviewed(production)"])
    print("  -> Achieved: security_reviewed(production)")
    emit({"type": "phase_complete", "phase": "security_review_final", "agent": "security-1"})
    print()

    result = check("deployer-1", "deployer", "deploy", "production")
    assert result.permitted

    print()
    print("  --> PERMITTED. Both conditions met:")
    print("      achieved(tests_passing(production)) ✓")
    print("      achieved(security_reviewed(production)) ✓")
    print()
    print("      A JSON allowlist cannot express 'allowed when A AND B'.")
    print()

    # ── Event Summary ────────────────────────────────────────────

    print("-" * 68)
    print("  Event Summary")
    print("-" * 68)
    print()

    blocked = [e for e in events if e["decision"] == "block"]
    approved = [e for e in events if e["decision"] == "approve"]

    print(f"  Total checks: {len(events)}  |  Approved: {len(approved)}  |  Blocked: {len(blocked)}")
    print()
    for i, e in enumerate(events, 1):
        status = "APPROVED" if e["decision"] == "approve" else "BLOCKED "
        print(f"  [{i:2d}] {e['agent']:14s} {e['action']:40s} {status}")

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
    parser = argparse.ArgumentParser(description="Block demo: governance enforcement showcase")
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
