"""End-to-end integration tests — Phase 3 success criteria.

Tests the three-agent workflow (architect -> implementer -> reviewer)
with organizational constraints enforced deterministically.
"""

import json
import tempfile
from pathlib import Path

import pytest

from governance.service import GovernanceService
from integration.hooks import GovernanceHook, TOOL_ACTION_MAP

_SPEC_PATH = Path(__file__).parent.parent.parent / "org-specs" / "three_role_workflow.yaml"


@pytest.fixture
def service():
    """Fresh service loaded with three_role_workflow spec."""
    return GovernanceService(_SPEC_PATH)


@pytest.fixture
def hook(tmp_path):
    """Fresh hook with temporary state file."""
    state_file = tmp_path / "state.json"
    return GovernanceHook(_SPEC_PATH, state_path=state_file)


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


class TestHookIntegration:
    """Tests for the Claude Code hook layer."""

    def test_pre_tool_use_blocks_out_of_scope(self, hook):
        hook.register_agent("impl-1", "implementer", scope="src/auth/")
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "src/api/x.py"}},
            agent="impl-1",
        )
        assert result["decision"] == "block"

    def test_pre_tool_use_allows_in_scope(self, hook):
        hook.register_agent("impl-1", "implementer", scope="src/auth/")
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "src/auth/x.py"}},
            agent="impl-1",
        )
        assert result["decision"] == "approve"

    def test_pre_tool_use_approves_unknown_tool(self, hook):
        hook.register_agent("impl-1", "implementer", scope="src/auth/")
        result = hook.pre_tool_use(
            {"tool_name": "WebSearch", "tool_input": {"query": "test"}},
            agent="impl-1",
        )
        assert result["decision"] == "approve"

    def test_pre_tool_use_approves_unregistered_agent(self, hook):
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "x.py"}},
            agent="unknown-agent",
        )
        assert result["decision"] == "approve"

    def test_edit_maps_to_write_file(self, hook):
        hook.register_agent("impl-1", "implementer", scope="src/auth/")
        result = hook.pre_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "src/api/x.py"}},
            agent="impl-1",
        )
        assert result["decision"] == "block"

    def test_reviewer_source_blocked_via_hook(self, hook):
        hook.register_agent("rev-1", "reviewer", scope="")
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "src/app.py"}},
            agent="rev-1",
        )
        assert result["decision"] == "block"

    def test_reviewer_nonsource_allowed_via_hook(self, hook):
        hook.register_agent("rev-1", "reviewer", scope="")
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "docs/review.md"}},
            agent="rev-1",
        )
        assert result["decision"] == "approve"

    def test_state_persistence(self, tmp_path):
        """State survives across hook instances."""
        state_file = tmp_path / "state.json"

        # First instance: register agent
        hook1 = GovernanceHook(_SPEC_PATH, state_path=state_file)
        hook1.register_agent("impl-1", "implementer", scope="src/auth/")

        # Second instance: replays state, agent is known
        hook2 = GovernanceHook(_SPEC_PATH, state_path=state_file)
        result = hook2.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "src/api/x.py"}},
            agent="impl-1",
        )
        assert result["decision"] == "block"

    def test_tool_action_map_coverage(self):
        """All mapped tools resolve to governance actions."""
        for tool, action in TOOL_ACTION_MAP.items():
            assert isinstance(action, str)
            assert len(action) > 0


class TestSystemPromptInjection:
    """Tests for system prompt generation from obligations."""

    def test_no_obligation_text_without_obligations(self, hook):
        hook.register_agent("impl-1", "implementer", scope="src/auth/")
        text = hook.get_system_prompt_injection("impl-1")
        # OG may generate delegation options even without obligations
        if text is not None:
            assert "obliged" not in text.lower() or "obligation" not in text.lower()

    def test_injection_with_active_obligation(self, hook):
        hook.register_agent("impl-1", "implementer", scope="src/auth/")
        hook._service.notify_action(
            "impl-1", "implementer",
            achieved=["feature_implemented(auth)"],
        )
        text = hook.get_system_prompt_injection("impl-1")
        assert text is not None
        assert "ORGANIZATIONAL CONTEXT" in text
        assert "tests_passing" in text
        assert "obliged" in text

    def test_injection_for_unknown_agent(self, hook):
        text = hook.get_system_prompt_injection("unknown")
        assert text is None
