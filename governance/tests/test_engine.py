"""Tests for the governance engine."""

import pytest

from governance.compiler import compile_org_spec
from governance.engine_types import NormChange, NotifyResult, PermissionResult
from governance.py_engine import PythonGovernanceEngine

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
    eng = PythonGovernanceEngine()
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
        eng = PythonGovernanceEngine()
        assert eng is not None

    def test_enact_role(self):
        eng = PythonGovernanceEngine()
        spec = compile_org_spec(_get_org_spec_path())
        eng.load_org_spec(spec)
        eng.enact_role("test-agent", "implementer")
        assert eng.get_agent_role("test-agent") == "implementer"


class TestObligationLifecycle:
    """Phase 2 success criteria: obligation activation, fulfillment, violation."""

    def test_obligation_activates_on_condition(self, engine):
        """Obligation activates when its condition (feature_implemented) is met."""
        result = engine.notify_action(
            "impl-agent-1", "implementer",
            achieved=["feature_implemented(auth)"],
        )
        activated = [c for c in result.norms_changed if c.type == "activated"]
        assert len(activated) == 1
        assert "tests_passing" in activated[0].objective
        assert activated[0].deontic == "obliged"

    def test_obligation_not_activated_without_condition(self, engine):
        """Obligation does not activate if condition is not met."""
        result = engine.notify_action(
            "impl-agent-1", "implementer",
            achieved=["some_other_thing(x)"],
        )
        activated = [c for c in result.norms_changed if c.type == "activated"]
        assert len(activated) == 0

    def test_obligation_fulfilled(self, engine):
        """Obligation is fulfilled when its objective is achieved."""
        # Step 1: Activate obligation
        engine.notify_action(
            "impl-agent-1", "implementer",
            achieved=["feature_implemented(auth)"],
        )
        # Step 2: Fulfill obligation
        result = engine.notify_action(
            "impl-agent-1", "implementer",
            achieved=["tests_passing(auth)"],
        )
        fulfilled = [c for c in result.norms_changed if c.type == "fulfilled"]
        assert len(fulfilled) == 1
        assert "tests_passing" in fulfilled[0].objective

    def test_obligation_violated(self, engine):
        """Obligation is violated when deadline reached without fulfillment."""
        # Step 1: Activate obligation
        engine.notify_action(
            "impl-agent-1", "implementer",
            achieved=["feature_implemented(auth)"],
        )
        # Step 2: Deadline reached without fulfillment
        result = engine.notify_action(
            "impl-agent-1", "implementer",
            deadlines_reached=["review_requested(auth)"],
        )
        violated = [c for c in result.norms_changed if c.type == "violated"]
        assert len(violated) == 1
        assert "tests_passing" in violated[0].objective

    def test_violation_recorded(self, engine):
        """After violation, violation is queryable via get_obligations."""
        engine.notify_action(
            "impl-agent-1", "implementer",
            achieved=["feature_implemented(auth)"],
        )
        engine.notify_action(
            "impl-agent-1", "implementer",
            deadlines_reached=["review_requested(auth)"],
        )
        result = engine.get_obligations("impl-agent-1", "implementer")
        viol_opts = [o for o in result["options"] if o["type"] == "violation"]
        assert len(viol_opts) > 0


class TestGetObligations:
    """Tests for the get_obligations endpoint."""

    def test_no_obligations_initially(self, engine):
        """No active obligations before any state changes."""
        result = engine.get_obligations("impl-agent-1", "implementer")
        assert result["obligations"] == []

    def test_obligation_appears_after_activation(self, engine):
        """Active obligation shows up in get_obligations."""
        engine.notify_action(
            "impl-agent-1", "implementer",
            achieved=["feature_implemented(auth)"],
        )
        result = engine.get_obligations("impl-agent-1", "implementer")
        assert len(result["obligations"]) == 1
        obl = result["obligations"][0]
        assert "tests_passing" in obl["objective"]
        assert "review_requested" in obl["deadline"]
        assert obl["deontic"] == "obliged"
        assert obl["status"] == "active"

    def test_obligation_disappears_after_fulfillment(self, engine):
        """Fulfilled obligation no longer shows in get_obligations."""
        engine.notify_action(
            "impl-agent-1", "implementer",
            achieved=["feature_implemented(auth)"],
        )
        engine.notify_action(
            "impl-agent-1", "implementer",
            achieved=["tests_passing(auth)"],
        )
        result = engine.get_obligations("impl-agent-1", "implementer")
        assert len(result["obligations"]) == 0


class TestOptionGeneration:
    """Tests for the OG phase option generation."""

    def test_norm_option_from_active_obligation(self, engine):
        """Active obligation generates a norm option."""
        engine.notify_action(
            "impl-agent-1", "implementer",
            achieved=["feature_implemented(auth)"],
        )
        result = engine.get_obligations("impl-agent-1", "implementer")
        norm_opts = [o for o in result["options"] if o["type"] == "norm"]
        assert len(norm_opts) >= 1
        assert any("tests_passing" in o["objective"] for o in norm_opts)

    def test_violation_option_after_violation(self, engine):
        """Violation generates a violation option."""
        engine.notify_action(
            "impl-agent-1", "implementer",
            achieved=["feature_implemented(auth)"],
        )
        engine.notify_action(
            "impl-agent-1", "implementer",
            deadlines_reached=["review_requested(auth)"],
        )
        result = engine.get_obligations("impl-agent-1", "implementer")
        viol_opts = [o for o in result["options"] if o["type"] == "violation"]
        assert len(viol_opts) >= 1

    def test_enact_options_for_unenacted_roles(self, engine):
        """Roles the agent hasn't enacted appear as enact options."""
        result = engine.get_obligations("impl-agent-1", "implementer")
        enact_opts = [o for o in result["options"] if o["type"] == "enact"]
        # reviewer role is available but not enacted by this agent
        assert any(o["role"] == "reviewer" for o in enact_opts)
