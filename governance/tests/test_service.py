"""Tests for the governance service."""

import pytest

from governance.service import GovernanceService

_ORG_SPEC_PATH = None


def _get_org_spec_path():
    global _ORG_SPEC_PATH
    if _ORG_SPEC_PATH is None:
        from pathlib import Path
        _ORG_SPEC_PATH = Path(__file__).parent.parent.parent / "org-specs" / "code_review.yaml"
    return _ORG_SPEC_PATH


@pytest.fixture
def service():
    """Create a fresh service loaded with the code_review org spec."""
    svc = GovernanceService(_get_org_spec_path())
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
        svc = GovernanceService(_get_org_spec_path())
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
        svc = GovernanceService(_get_org_spec_path())
        svc.register_agent("agent-a", "implementer", scope="src/api/")
        result = svc.check_permission(
            "agent-a", "implementer", "read_file", {"path": "src/auth/login.py"}
        )
        assert result.permitted is True
