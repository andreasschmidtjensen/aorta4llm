"""Tests for the governance service."""

import pytest

from aorta4llm.governance.service import GovernanceService

from tests.conftest import CODE_REVIEW_SPEC


@pytest.fixture
def service():
    """Create a fresh service loaded with the code_review org spec."""
    svc = GovernanceService(CODE_REVIEW_SPEC)
    svc.register_agent("impl-agent-1", "implementer", scope="src/api/")
    return svc


class TestServicePermissions:
    """Service-level permission checking with stored scopes."""

    def test_write_outside_scope_blocked(self, service):
        result = service.check_permission(
            agent="impl-agent-1",
            role="implementer",
            action="write_file",
            params={"path": "src/auth/login.py"},
        )
        assert result.permitted is False

    def test_write_inside_scope_permitted(self, service):
        result = service.check_permission(
            agent="impl-agent-1",
            role="implementer",
            action="write_file",
            params={"path": "src/api/routes.py"},
        )
        assert result.permitted is True

    def test_scope_injected_from_registration(self, service):
        """Service injects stored scope when not in params."""
        result = service.check_permission(
            agent="impl-agent-1",
            role="implementer",
            action="write_file",
            params={"path": "src/auth/login.py"},
            # No 'scope' in params — should use stored scope
        )
        assert result.permitted is False

    def test_explicit_scope_overrides_stored(self, service):
        """Explicit scope in params takes precedence over stored scope."""
        result = service.check_permission(
            agent="impl-agent-1",
            role="implementer",
            action="write_file",
            params={"path": "src/auth/login.py", "scope": "src/auth/"},
        )
        assert result.permitted is True


class TestServiceRegistration:
    def test_register_multiple_agents(self):
        svc = GovernanceService(CODE_REVIEW_SPEC)
        svc.register_agent("agent-a", "implementer", scope="src/api/")
        svc.register_agent("agent-b", "implementer", scope="src/auth/")

        # agent-a can write to api/ but not auth/
        result_a = svc.check_permission(
            "agent-a", "implementer", "write_file", {"path": "src/api/x.py"}
        )
        assert result_a.permitted is True

        result_a2 = svc.check_permission(
            "agent-a", "implementer", "write_file", {"path": "src/auth/x.py"}
        )
        assert result_a2.permitted is False

    def test_read_always_permitted(self):
        svc = GovernanceService(CODE_REVIEW_SPEC)
        svc.register_agent("agent-a", "implementer", scope="src/api/")
        result = svc.check_permission(
            "agent-a", "implementer", "read_file", {"path": "src/auth/login.py"}
        )
        assert result.permitted is True


class TestServiceObligations:
    """Service-level obligation and notification tests."""

    def test_notify_activates_obligation(self, service):
        """notify_action with achievement activates obligation."""
        result = service.notify_action(
            "impl-agent-1", "implementer",
            achieved=["feature_implemented(auth)"],
        )
        activated = [c for c in result.norms_changed if c.type == "activated"]
        assert len(activated) == 1

    def test_get_obligations_returns_active(self, service):
        """get_obligations returns active obligations after activation."""
        service.notify_action(
            "impl-agent-1", "implementer",
            achieved=["feature_implemented(auth)"],
        )
        result = service.get_obligations("impl-agent-1", "implementer")
        assert len(result["obligations"]) == 1
        assert result["obligations"][0]["deontic"] == "obliged"

    def test_full_obligation_lifecycle(self, service):
        """Full lifecycle: activate → track → fulfill."""
        # Activate
        r1 = service.notify_action(
            "impl-agent-1", "implementer",
            achieved=["feature_implemented(auth)"],
        )
        assert any(c.type == "activated" for c in r1.norms_changed)

        # Track
        obls = service.get_obligations("impl-agent-1", "implementer")
        assert len(obls["obligations"]) == 1

        # Fulfill
        r2 = service.notify_action(
            "impl-agent-1", "implementer",
            achieved=["tests_passing(auth)"],
        )
        assert any(c.type == "fulfilled" for c in r2.norms_changed)

        # Gone
        obls = service.get_obligations("impl-agent-1", "implementer")
        assert len(obls["obligations"]) == 0

    def test_obligation_and_permission_coexist(self, service):
        """Permission checks still work alongside obligation tracking."""
        service.notify_action(
            "impl-agent-1", "implementer",
            achieved=["feature_implemented(auth)"],
        )
        # Permission check should still enforce scope prohibition
        result = service.check_permission(
            "impl-agent-1", "implementer", "write_file",
            {"path": "src/auth/login.py"},
        )
        assert result.permitted is False
