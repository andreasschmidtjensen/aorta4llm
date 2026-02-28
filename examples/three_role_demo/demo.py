#!/usr/bin/env python3
"""Three-role workflow demo: architect -> implementer -> reviewer.

Simulates a complete organizational workflow with AORTA governance
constraints enforced at each step. Produces an execution trace
showing which actions were permitted and blocked.

Run: uv run python examples/three_role_demo/demo.py
"""

from pathlib import Path

from governance.service import GovernanceService

SPEC_PATH = Path(__file__).parent.parent.parent / "org-specs" / "three_role_workflow.yaml"


class WorkflowTrace:
    """Collects and formats execution trace entries."""

    def __init__(self):
        self.entries: list[dict] = []
        self.permitted_count = 0
        self.blocked_count = 0

    def log_check(self, agent, role, action, path, permitted, reason=""):
        status = "PERMITTED" if permitted else "BLOCKED"
        self.entries.append({
            "type": "check",
            "agent": agent,
            "role": role,
            "action": f"{action}({path})",
            "status": status,
            "reason": reason,
        })
        if permitted:
            self.permitted_count += 1
        else:
            self.blocked_count += 1
        print(f"  {'PERMIT' if permitted else 'BLOCK '} {action}({path})")
        if not permitted and reason:
            print(f"         reason: {reason}")

    def log_event(self, msg):
        self.entries.append({"type": "event", "message": msg})
        print(f"  -> {msg}")

    def log_phase(self, msg):
        self.entries.append({"type": "phase", "message": msg})
        print(f"\n{'='*60}")
        print(f"  {msg}")
        print(f"{'='*60}")

    def summary(self):
        print(f"\n{'='*60}")
        print(f"  EXECUTION TRACE SUMMARY")
        print(f"{'='*60}")
        print(f"  Total checks:  {self.permitted_count + self.blocked_count}")
        print(f"  Permitted:     {self.permitted_count}")
        print(f"  Blocked:       {self.blocked_count}")
        print(f"  Norm events:   {sum(1 for e in self.entries if e['type'] == 'event')}")


def run_demo():
    svc = GovernanceService(SPEC_PATH)
    trace = WorkflowTrace()

    def check(agent, role, action, path):
        result = svc.check_permission(agent, role, action, {"path": path})
        trace.log_check(agent, role, action, path, result.permitted, result.reason)
        return result

    # ── Phase 1: Architect ──────────────────────────────────────

    trace.log_phase("Phase 1: Architect designs auth system")
    svc.register_agent("architect-1", "architect", scope="")

    check("architect-1", "architect", "write_file", "docs/design.md")
    check("architect-1", "architect", "read_file", "src/auth/login.py")
    check("architect-1", "architect", "write_file", "src/core/config.py")

    r = svc.notify_action(
        "architect-1", "architect",
        achieved=["system_design_complete(auth)"],
    )
    trace.log_event("Achieved: system_design_complete(auth)")

    # ── Phase 2: Implementer ────────────────────────────────────

    trace.log_phase("Phase 2: Implementer builds auth feature (scope: src/auth/)")
    svc.register_agent("impl-1", "implementer", scope="src/auth/")

    # In-scope writes: permitted
    check("impl-1", "implementer", "write_file", "src/auth/login.py")
    check("impl-1", "implementer", "write_file", "src/auth/models.py")

    # Out-of-scope writes: blocked
    check("impl-1", "implementer", "write_file", "src/api/routes.py")
    check("impl-1", "implementer", "write_file", "src/core/config.py")

    # Reads anywhere: permitted
    check("impl-1", "implementer", "read_file", "src/api/routes.py")

    # Feature implemented -> obligation activates
    r = svc.notify_action(
        "impl-1", "implementer",
        achieved=["feature_implemented(auth)"],
    )
    trace.log_event("Achieved: feature_implemented(auth)")
    for c in r.norms_changed:
        trace.log_event(f"Norm {c.type}: {c.deontic} {c.objective} (deadline: {c.deadline})")

    # Check obligations
    obls = svc.get_obligations("impl-1", "implementer")
    for o in obls["obligations"]:
        trace.log_event(f"Active: {o['deontic']} {o['objective']} by {o['deadline']}")

    # Tests pass -> obligation fulfilled
    r = svc.notify_action(
        "impl-1", "implementer",
        achieved=["tests_passing(auth)"],
    )
    trace.log_event("Achieved: tests_passing(auth)")
    for c in r.norms_changed:
        trace.log_event(f"Norm {c.type}: {c.deontic} {c.objective}")

    # ── Phase 3: Reviewer ───────────────────────────────────────

    trace.log_phase("Phase 3: Reviewer reviews auth feature")
    svc.register_agent("reviewer-1", "reviewer", scope="")

    # Read source: permitted
    check("reviewer-1", "reviewer", "read_file", "src/auth/login.py")

    # Write source file (.py): blocked by prohibition
    check("reviewer-1", "reviewer", "write_file", "src/auth/login.py")

    # Write non-source file: permitted (no prohibition on non-source)
    # Note: in production, reviewer wouldn't have write_file tool at all
    # (capability enforcement is the agent platform's responsibility)
    check("reviewer-1", "reviewer", "write_file", "docs/review.md")

    # Reviewer starts reviewing -> obligation activates
    r = svc.notify_action(
        "reviewer-1", "reviewer",
        achieved=["code_reviewed(auth)"],
    )
    trace.log_event("Achieved: code_reviewed(auth)")
    for c in r.norms_changed:
        trace.log_event(f"Norm {c.type}: {c.deontic} {c.objective} (deadline: {c.deadline})")

    # Reviewer documents review -> obligation fulfilled
    r = svc.notify_action(
        "reviewer-1", "reviewer",
        achieved=["review_documented(auth)"],
    )
    trace.log_event("Achieved: review_documented(auth)")
    for c in r.norms_changed:
        trace.log_event(f"Norm {c.type}: {c.deontic} {c.objective}")

    # ── Summary ─────────────────────────────────────────────────

    trace.summary()
    return trace


if __name__ == "__main__":
    run_demo()
