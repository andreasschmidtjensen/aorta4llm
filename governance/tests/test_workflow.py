"""Tests for the three-role workflow via GovernanceService."""

from pathlib import Path

import pytest

from governance.service import GovernanceService

_SPEC_PATH = Path(__file__).parent.parent.parent / "org-specs" / "three_role_workflow.yaml"


@pytest.fixture
def service():
    """Fresh service loaded with three_role_workflow spec."""
    return GovernanceService(_SPEC_PATH)


class TestThreeRoleWorkflow:
    """Phase 3 success criteria: three-agent workflow with constraints."""

    def test_architect_can_write_anywhere(self, service):
        service.register_agent("arch-1", "architect", scope="")
        r1 = service.check_permission("arch-1", "architect", "write_file",
                                      {"path": "docs/design.md"})
        r2 = service.check_permission("arch-1", "architect", "write_file",
                                      {"path": "src/core/config.py"})
        assert r1.permitted
        assert r2.permitted

    def test_implementer_scope_enforcement(self, service):
        service.register_agent("impl-1", "implementer", scope="src/auth/")

        in_scope = service.check_permission(
            "impl-1", "implementer", "write_file",
            {"path": "src/auth/login.py"},
        )
        out_scope = service.check_permission(
            "impl-1", "implementer", "write_file",
            {"path": "src/api/routes.py"},
        )
        assert in_scope.permitted
        assert not out_scope.permitted

    def test_reviewer_source_file_prohibition(self, service):
        service.register_agent("rev-1", "reviewer", scope="")

        # .py file blocked
        r_py = service.check_permission(
            "rev-1", "reviewer", "write_file",
            {"path": "src/auth/login.py"},
        )
        # .ts file blocked
        r_ts = service.check_permission(
            "rev-1", "reviewer", "write_file",
            {"path": "src/app/main.ts"},
        )
        # .md file allowed (not source)
        r_md = service.check_permission(
            "rev-1", "reviewer", "write_file",
            {"path": "docs/review.md"},
        )
        assert not r_py.permitted
        assert not r_ts.permitted
        assert r_md.permitted

    def test_reviewer_read_always_permitted(self, service):
        service.register_agent("rev-1", "reviewer", scope="")
        result = service.check_permission(
            "rev-1", "reviewer", "read_file",
            {"path": "src/auth/login.py"},
        )
        assert result.permitted

    def test_full_workflow_obligation_lifecycle(self, service):
        """Complete three-phase workflow with obligation tracking."""
        # Architect designs
        service.register_agent("arch-1", "architect", scope="")
        service.notify_action("arch-1", "architect",
                              achieved=["system_design_complete(auth)"])

        # Implementer builds
        service.register_agent("impl-1", "implementer", scope="src/auth/")

        # Feature done -> obligation activates
        r = service.notify_action("impl-1", "implementer",
                                  achieved=["feature_implemented(auth)"])
        assert any(c.type == "activated" and c.deontic == "obliged"
                   for c in r.norms_changed)

        # Tests pass -> obligation fulfilled
        r = service.notify_action("impl-1", "implementer",
                                  achieved=["tests_passing(auth)"])
        assert any(c.type == "fulfilled" for c in r.norms_changed)

        # Reviewer reviews
        service.register_agent("rev-1", "reviewer", scope="")

        # Review done -> reviewer obligation activates
        r = service.notify_action("rev-1", "reviewer",
                                  achieved=["code_reviewed(auth)"])
        assert any(c.type == "activated" and "review_documented" in c.objective
                   for c in r.norms_changed)

        # Review documented -> obligation fulfilled
        r = service.notify_action("rev-1", "reviewer",
                                  achieved=["review_documented(auth)"])
        assert any(c.type == "fulfilled" for c in r.norms_changed)

    def test_implementer_violation_on_deadline(self, service):
        """Implementer obligation violated when deadline reached without fulfillment."""
        service.register_agent("impl-1", "implementer", scope="src/auth/")
        service.notify_action("impl-1", "implementer",
                              achieved=["feature_implemented(auth)"])

        # Review requested without tests passing
        r = service.notify_action("impl-1", "implementer",
                                  deadlines_reached=["review_requested(auth)"])
        assert any(c.type == "violated" for c in r.norms_changed)

    def test_constraints_across_agents_are_independent(self, service):
        """Each agent's constraints are independent."""
        service.register_agent("impl-a", "implementer", scope="src/auth/")
        service.register_agent("impl-b", "implementer", scope="src/api/")

        # impl-a can write auth/ but not api/
        assert service.check_permission(
            "impl-a", "implementer", "write_file",
            {"path": "src/auth/x.py"},
        ).permitted
        assert not service.check_permission(
            "impl-a", "implementer", "write_file",
            {"path": "src/api/x.py"},
        ).permitted

        # impl-b can write api/ but not auth/
        assert service.check_permission(
            "impl-b", "implementer", "write_file",
            {"path": "src/api/x.py"},
        ).permitted
        assert not service.check_permission(
            "impl-b", "implementer", "write_file",
            {"path": "src/auth/x.py"},
        ).permitted
