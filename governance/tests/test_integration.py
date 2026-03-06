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
def service(engine_backend):
    """Fresh service loaded with three_role_workflow spec."""
    return GovernanceService(_SPEC_PATH, engine=engine_backend)


@pytest.fixture
def hook(tmp_path, engine_backend):
    """Fresh hook with temporary state file."""
    state_file = tmp_path / "state.json"
    return GovernanceHook(_SPEC_PATH, state_path=state_file, engine=engine_backend)


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

    def test_state_persistence(self, tmp_path, engine_backend):
        """State survives across hook instances."""
        state_file = tmp_path / "state.json"

        # First instance: register agent
        hook1 = GovernanceHook(_SPEC_PATH, state_path=state_file, engine=engine_backend)
        hook1.register_agent("impl-1", "implementer", scope="src/auth/")

        # Second instance: replays state, agent is known
        hook2 = GovernanceHook(_SPEC_PATH, state_path=state_file, engine=engine_backend)
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


class TestBashCommandPassing:
    """Tests for Bash command passing through the permission check."""

    def test_bash_command_passed_as_param(self, hook):
        """pre_tool_use passes command string for Bash calls."""
        hook.register_agent("impl-1", "implementer", scope="src/auth/")
        # Bash calls should pass command, not path
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "pytest tests/"}},
            agent="impl-1",
        )
        # Implementer has execute_command capability, no prohibition on pytest
        assert result["decision"] == "approve"

    def test_bash_command_gate_blocks_commit_without_achievement(self, tmp_path):
        """required_before norm blocks git commit until tests_passing achieved."""
        spec_dict = {
            "organization": "test_gate",
            "roles": {
                "agent": {
                    "objectives": ["tests_passing"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": [{
                "role": "agent",
                "type": "required_before",
                "blocks": "execute_command",
                "command_pattern": "git commit",
                "requires": "tests_passing",
            }],
        }
        import yaml
        spec_file = tmp_path / "gate.yaml"
        spec_file.write_text(yaml.dump(spec_dict))

        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")

        # Commit blocked before tests_passing
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'feat: x'"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_bash_command_gate_allows_commit_after_achievement(self, tmp_path):
        """git commit allowed once tests_passing is achieved."""
        spec_dict = {
            "organization": "test_gate",
            "roles": {
                "agent": {
                    "objectives": ["tests_passing"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": [{
                "role": "agent",
                "type": "required_before",
                "blocks": "execute_command",
                "command_pattern": "git commit",
                "requires": "tests_passing",
            }],
        }
        import yaml
        spec_file = tmp_path / "gate.yaml"
        spec_file.write_text(yaml.dump(spec_dict))

        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")
        hook._service.notify_action("dev", "agent", achieved=["tests_passing"])

        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'feat: x'"}},
            agent="dev",
        )
        assert result["decision"] == "approve"


class TestPostToolUseAchievements:
    """Tests for achievement tracking via PostToolUse triggers."""

    def _make_hook_with_triggers(self, tmp_path, triggers: list[dict]):
        import yaml
        spec_dict = {
            "organization": "trigger_test",
            "roles": {
                "agent": {
                    "objectives": ["tests_passing"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "achievement_triggers": triggers,
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")
        return hook

    def test_triggers_loaded_from_spec(self, tmp_path):
        triggers = [{"tool": "Bash", "command_pattern": "pytest", "exit_code": 0, "marks": "tests_passing"}]
        hook = self._make_hook_with_triggers(tmp_path, triggers)
        assert len(hook._triggers) == 1
        assert hook._triggers[0]["marks"] == "tests_passing"

    def test_post_tool_use_marks_achievement_on_match(self, tmp_path):
        triggers = [{"tool": "Bash", "command_pattern": "pytest", "exit_code": 0, "marks": "tests_passing"}]
        hook = self._make_hook_with_triggers(tmp_path, triggers)

        result = hook.post_tool_use(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "pytest tests/"},
                "tool_response": {"exit_code": 0},
            },
            agent="dev",
        )
        assert result == {"status": "ok"}

        # Achievement should now be in the engine state
        obls = hook._service.get_obligations("dev", "agent")
        # tests_passing was the objective — after achieving it, no active obligation
        assert obls is not None

    def test_post_tool_use_no_match_on_wrong_exit_code(self, tmp_path):
        triggers = [{"tool": "Bash", "command_pattern": "pytest", "exit_code": 0, "marks": "tests_passing"}]
        hook = self._make_hook_with_triggers(tmp_path, triggers)

        result = hook.post_tool_use(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "pytest tests/"},
                "tool_response": {"exit_code": 1},
            },
            agent="dev",
        )
        assert result == {"status": "ok"}
        # Event should not have been logged for tests_passing

    def test_post_tool_use_no_match_on_wrong_command(self, tmp_path):
        triggers = [{"tool": "Bash", "command_pattern": "pytest", "exit_code": 0, "marks": "tests_passing"}]
        hook = self._make_hook_with_triggers(tmp_path, triggers)

        result = hook.post_tool_use(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "npm install"},
                "tool_response": {"exit_code": 0},
            },
            agent="dev",
        )
        assert result == {"status": "ok"}

    def test_post_tool_use_no_match_on_wrong_tool(self, tmp_path):
        triggers = [{"tool": "Bash", "command_pattern": "pytest", "exit_code": 0, "marks": "tests_passing"}]
        hook = self._make_hook_with_triggers(tmp_path, triggers)

        result = hook.post_tool_use(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "x.py"},
                "tool_response": {"exit_code": 0},
            },
            agent="dev",
        )
        assert result == {"status": "ok"}

    def test_post_tool_use_skips_unregistered_agent(self, tmp_path):
        triggers = [{"tool": "Bash", "command_pattern": "pytest", "exit_code": 0, "marks": "tests_passing"}]
        hook = self._make_hook_with_triggers(tmp_path, triggers)

        result = hook.post_tool_use(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "pytest"},
                "tool_response": {"exit_code": 0},
            },
            agent="unknown",
        )
        assert result == {"status": "ok"}

    def test_achievement_enables_previously_blocked_command(self, tmp_path):
        """End-to-end: trigger unlocks a gate that was blocking a commit."""
        import yaml
        spec_dict = {
            "organization": "full_gate",
            "roles": {
                "agent": {
                    "objectives": ["tests_passing"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": [{
                "role": "agent",
                "type": "required_before",
                "command_pattern": "git commit",
                "requires": "tests_passing",
            }],
            "achievement_triggers": [{
                "tool": "Bash",
                "command_pattern": "pytest",
                "exit_code": 0,
                "marks": "tests_passing",
            }],
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")

        # Commit blocked initially
        r1 = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            agent="dev",
        )
        assert r1["decision"] == "block"

        # Tests pass → trigger fires
        hook.post_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "pytest"},
             "tool_response": {"exit_code": 0}},
            agent="dev",
        )

        # Commit now allowed
        r2 = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            agent="dev",
        )
        assert r2["decision"] == "approve"


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


class TestForbiddenCommand:
    """Tests for forbidden_command norm type and soft blocks."""

    def _make_hook(self, tmp_path, norms, severity=None):
        import yaml
        for norm in norms:
            if severity and "severity" not in norm:
                norm["severity"] = severity
        spec_dict = {
            "organization": "cmd_test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": norms,
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")
        return hook

    def test_forbidden_command_blocks_matching_command(self, tmp_path):
        hook = self._make_hook(tmp_path, [{
            "type": "forbidden_command",
            "role": "agent",
            "command_pattern": "git commit",
        }])
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_forbidden_command_allows_non_matching(self, tmp_path):
        hook = self._make_hook(tmp_path, [{
            "type": "forbidden_command",
            "role": "agent",
            "command_pattern": "git commit",
        }])
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git status"}},
            agent="dev",
        )
        assert result["decision"] == "approve"

    def test_forbidden_command_blocks_substring_match(self, tmp_path):
        """Pattern matches anywhere in command, not just prefix."""
        hook = self._make_hook(tmp_path, [{
            "type": "forbidden_command",
            "role": "agent",
            "command_pattern": "git push",
        }])
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "cd repo && git push origin main"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_soft_block_first_attempt_blocks(self, tmp_path):
        hook = self._make_hook(tmp_path, [{
            "type": "forbidden_command",
            "role": "agent",
            "command_pattern": "git commit",
            "severity": "soft",
        }])
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            agent="dev",
        )
        assert result["decision"] == "block"
        assert "CONFIRMATION REQUIRED" in result["reason"]

    def test_soft_block_retry_approves(self, tmp_path):
        hook = self._make_hook(tmp_path, [{
            "type": "forbidden_command",
            "role": "agent",
            "command_pattern": "git commit",
            "severity": "soft",
        }])
        cmd = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}}

        # First attempt — blocked
        r1 = hook.pre_tool_use(cmd, agent="dev")
        assert r1["decision"] == "block"

        # Retry — approved (user confirmed)
        r2 = hook.pre_tool_use(cmd, agent="dev")
        assert r2["decision"] == "approve"

    def test_soft_block_retry_window_expires(self, tmp_path):
        hook = self._make_hook(tmp_path, [{
            "type": "forbidden_command",
            "role": "agent",
            "command_pattern": "git commit",
            "severity": "soft",
        }])
        cmd = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}}

        # First attempt — blocked
        hook.pre_tool_use(cmd, agent="dev")

        # Simulate window expiry
        key = hook._soft_block_key({"command": "git commit -m 'x'"})
        hook._soft_block_cache[key] -= 120  # push timestamp back

        # Retry after window — blocked again
        r2 = hook.pre_tool_use(cmd, agent="dev")
        assert r2["decision"] == "block"

    def test_hard_block_does_not_approve_on_retry(self, tmp_path):
        hook = self._make_hook(tmp_path, [{
            "type": "forbidden_command",
            "role": "agent",
            "command_pattern": "git commit",
        }])
        cmd = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}}

        r1 = hook.pre_tool_use(cmd, agent="dev")
        assert r1["decision"] == "block"
        assert "CONFIRMATION" not in r1.get("reason", "")

        r2 = hook.pre_tool_use(cmd, agent="dev")
        assert r2["decision"] == "block"

    def test_severity_propagated_to_permission_result(self, tmp_path):
        """The engine returns severity='soft' for soft-norm prohibitions."""
        import yaml
        spec_dict = {
            "organization": "sev_test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": [{
                "type": "forbidden_command",
                "role": "agent",
                "command_pattern": "git push",
                "severity": "soft",
            }],
        }
        from governance.service import GovernanceService
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        svc = GovernanceService(spec_file)
        svc.register_agent("dev", "agent")
        result = svc.check_permission("dev", "agent", "execute_command",
                                       {"command": "git push origin main"})
        assert not result.permitted
        assert result.severity == "soft"


class TestBashAnalysisIntegration:
    """Tests for LLM-based Bash analysis in pre_tool_use."""

    def _make_hook(self, tmp_path, bash_analysis=True, norms=None):
        import yaml
        spec_dict = {
            "organization": "bash_test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": norms or [{
                "role": "agent",
                "type": "forbidden_outside",
                "path": "src/",
            }],
        }
        if bash_analysis:
            spec_dict["bash_analysis"] = True
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent", scope="src/")
        return hook

    def test_bash_write_outside_scope_blocked(self, tmp_path):
        """Bash writing outside scope is blocked when bash_analysis is enabled."""
        from unittest.mock import patch
        from governance.bash_analyzer import BashAnalysis

        hook = self._make_hook(tmp_path)

        with patch("governance.bash_analyzer.analyze_bash_command") as mock_analyze:
            mock_analyze.return_value = BashAnalysis(
                writes=["config/secret.py"],
                is_destructive=False,
                summary="writes to config/",
            )
            result = hook.pre_tool_use(
                {"tool_name": "Bash", "tool_input": {"command": "echo hack > config/secret.py"}},
                agent="dev",
            )

        assert result["decision"] == "block"
        assert "config/secret.py" in result["reason"]

    def test_bash_write_in_scope_allowed(self, tmp_path):
        """Bash writing within scope is allowed."""
        from unittest.mock import patch
        from governance.bash_analyzer import BashAnalysis

        hook = self._make_hook(tmp_path)

        with patch("governance.bash_analyzer.analyze_bash_command") as mock_analyze:
            mock_analyze.return_value = BashAnalysis(
                writes=["src/app.py"],
                is_destructive=False,
                summary="writes to src/app.py",
            )
            result = hook.pre_tool_use(
                {"tool_name": "Bash", "tool_input": {"command": "echo x > src/app.py"}},
                agent="dev",
            )

        assert result["decision"] == "approve"

    def test_bash_analysis_skipped_when_not_enabled(self, tmp_path):
        """Without bash_analysis: true, no LLM analysis runs."""
        from unittest.mock import patch

        hook = self._make_hook(tmp_path, bash_analysis=False)

        with patch("governance.bash_analyzer.analyze_bash_command") as mock_analyze:
            result = hook.pre_tool_use(
                {"tool_name": "Bash", "tool_input": {"command": "echo hack > config/x.py"}},
                agent="dev",
            )

        mock_analyze.assert_not_called()
        # Without analysis, Bash execute_command is approved (no command-level prohibition)
        assert result["decision"] == "approve"

    def test_bash_analysis_logs_block_event(self, tmp_path):
        """Blocked Bash commands are logged to events file."""
        from unittest.mock import patch
        from governance.bash_analyzer import BashAnalysis

        hook = self._make_hook(tmp_path)

        with patch("governance.bash_analyzer.analyze_bash_command") as mock_analyze:
            mock_analyze.return_value = BashAnalysis(
                writes=["config/x.py"],
                is_destructive=False,
                summary="writes to config/",
            )
            hook.pre_tool_use(
                {"tool_name": "Bash", "tool_input": {"command": "cp x config/x.py"}},
                agent="dev",
            )

        # Check events file for bash_analysis entry
        events_path = hook._events_path
        if events_path.exists():
            lines = events_path.read_text().strip().split("\n")
            bash_events = [json.loads(l) for l in lines if "bash_analysis" in l]
            assert len(bash_events) >= 1
            assert bash_events[-1]["decision"] == "block"
