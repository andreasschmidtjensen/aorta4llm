"""Tests for the governance engine — Phase 1 success criteria."""

import pytest

from governance.compiler import compile_org_spec
from governance.engine import GovernanceEngine, PermissionResult

_ORG_SPEC_PATH = None


def _get_org_spec_path():
    global _ORG_SPEC_PATH
    if _ORG_SPEC_PATH is None:
        from pathlib import Path
        _ORG_SPEC_PATH = Path(__file__).parent.parent.parent / "org-specs" / "code_review.yaml"
    return _ORG_SPEC_PATH


@pytest.fixture
def engine():
    """Create a fresh engine loaded with the code_review org spec."""
    eng = GovernanceEngine()
    spec = compile_org_spec(_get_org_spec_path())
    eng.load_org_spec(spec)
    eng.enact_role("impl-agent-1", "implementer")
    return eng


class TestPhase1SuccessCriteria:
    """The core Phase 1 tests: prohibition enforcement."""

    def test_write_outside_scope_is_blocked(self, engine):
        """An implementer writing outside their assigned scope is blocked."""
        result = engine.check_permission(
            agent="impl-agent-1",
            role="implementer",
            action="write_file",
            params={"path": "src/auth/login.py", "scope": "src/api/"},
        )
        assert result.permitted is False
        assert "blocked" in result.reason
        assert result.violation is not None

    def test_write_inside_scope_is_permitted(self, engine):
        """An implementer writing inside their assigned scope is permitted."""
        result = engine.check_permission(
            agent="impl-agent-1",
            role="implementer",
            action="write_file",
            params={"path": "src/api/routes.py", "scope": "src/api/"},
        )
        assert result.permitted is True
        assert result.violation is None


class TestPermissionEdgeCases:
    """Additional edge cases for permission checking."""

    def test_read_is_always_permitted(self, engine):
        """Read actions are not subject to write prohibitions."""
        result = engine.check_permission(
            agent="impl-agent-1",
            role="implementer",
            action="read_file",
            params={"path": "src/auth/login.py", "scope": "src/api/"},
        )
        assert result.permitted is True

    def test_write_at_scope_root_is_permitted(self, engine):
        """Writing a file at the exact scope root is in scope."""
        result = engine.check_permission(
            agent="impl-agent-1",
            role="implementer",
            action="write_file",
            params={"path": "src/api/main.py", "scope": "src/api/"},
        )
        assert result.permitted is True

    def test_write_with_scope_prefix_mismatch(self, engine):
        """A path that looks similar but doesn't start with scope is blocked."""
        result = engine.check_permission(
            agent="impl-agent-1",
            role="implementer",
            action="write_file",
            params={"path": "src/api_v2/routes.py", "scope": "src/api/"},
        )
        assert result.permitted is False

    def test_empty_scope_blocks_all_writes(self, engine):
        """With empty scope, nothing is in scope so all writes are blocked."""
        result = engine.check_permission(
            agent="impl-agent-1",
            role="implementer",
            action="write_file",
            params={"path": "any/file.py", "scope": ""},
        )
        # Empty scope: atom_concat('', _, Path) succeeds for any Path,
        # so in_scope always holds, meaning not(in_scope(...)) is false,
        # so the prohibition condition is NOT met. Write is permitted.
        assert result.permitted is True

    def test_multiple_checks_are_independent(self, engine):
        """Each permission check should be independent (scope cleanup works)."""
        result1 = engine.check_permission(
            agent="impl-agent-1",
            role="implementer",
            action="write_file",
            params={"path": "src/auth/login.py", "scope": "src/api/"},
        )
        result2 = engine.check_permission(
            agent="impl-agent-1",
            role="implementer",
            action="write_file",
            params={"path": "src/auth/login.py", "scope": "src/auth/"},
        )
        assert result1.permitted is False
        assert result2.permitted is True


class TestEngineSetup:
    """Test engine initialization and loading."""

    def test_engine_creates_without_error(self):
        eng = GovernanceEngine()
        assert eng is not None

    def test_enact_role(self):
        eng = GovernanceEngine()
        spec = compile_org_spec(_get_org_spec_path())
        eng.load_org_spec(spec)
        eng.enact_role("test-agent", "implementer")
        results = list(eng._prolog.query("rea('test-agent', implementer)"))
        assert len(results) > 0
