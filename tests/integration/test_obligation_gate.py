"""Tests for obligation gate (all_obligations_fulfilled)."""

import json

import yaml

from aorta4llm.governance.compiler import compile_spec_dict
from aorta4llm.governance.service import GovernanceService
from aorta4llm.integration.hooks import GovernanceHook


def _make_spec_dict(**overrides):
    """Base spec with obligation gate on git commit."""
    spec = {
        "organization": "test",
        "roles": {
            "agent": {
                "objectives": ["task_complete"],
                "capabilities": ["read_file", "write_file", "execute_command"],
            }
        },
        "norms": [{
            "role": "agent",
            "type": "required_before",
            "command_pattern": "git commit",
            "requires": "all_obligations_fulfilled",
        }],
    }
    spec.update(overrides)
    return spec


# --- Compiler tests ---

class TestCompilerObligationGate:
    def test_emits_not_all_obligations_fulfilled(self):
        spec = compile_spec_dict(_make_spec_dict())
        cond_facts = [f for f in spec.facts if "execute_command(Cmd)" in f]
        assert len(cond_facts) == 1
        assert "not(all_obligations_fulfilled)" in cond_facts[0]
        assert "achieved" not in cond_facts[0]

    def test_still_emits_helper_rule(self):
        spec = compile_spec_dict(_make_spec_dict())
        helper_rules = [r for r in spec.rules if "git commit" in r]
        assert len(helper_rules) == 1
        assert "regex_matches" in helper_rules[0]

    def test_normal_requires_unchanged(self):
        """requires: tests_passing still emits not(achieved(...))."""
        spec = compile_spec_dict({
            "norms": [{
                "role": "agent",
                "type": "required_before",
                "command_pattern": "git commit",
                "requires": "tests_passing",
            }]
        })
        cond_facts = [f for f in spec.facts if "execute_command(Cmd)" in f]
        assert "not(achieved(tests_passing))" in cond_facts[0]


# --- Service tests ---

class TestServiceObligationGate:
    def _make_service(self, tmp_path):
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(_make_spec_dict()))
        svc = GovernanceService(spec_file)
        svc.register_agent("dev", "agent")
        return svc

    def test_no_obligations_allows_commit(self, tmp_path):
        svc = self._make_service(tmp_path)
        result = svc.check_permission(
            "dev", "agent", "execute_command", {"command": "git commit -m 'x'"},
        )
        assert result.permitted is True

    def test_obligation_blocks_commit(self, tmp_path):
        svc = self._make_service(tmp_path)
        svc.create_obligation("dev", "agent", "fix_leak")
        result = svc.check_permission(
            "dev", "agent", "execute_command", {"command": "git commit -m 'x'"},
        )
        assert result.permitted is False
        assert "obligations" in result.reason

    def test_fulfilling_obligation_unblocks_commit(self, tmp_path):
        svc = self._make_service(tmp_path)
        svc.create_obligation("dev", "agent", "fix_leak")
        # Fulfill via standard achieved path
        svc.notify_action("dev", "agent", achieved=["fix_leak"])
        result = svc.check_permission(
            "dev", "agent", "execute_command", {"command": "git commit -m 'x'"},
        )
        assert result.permitted is True

    def test_multiple_obligations_all_must_clear(self, tmp_path):
        svc = self._make_service(tmp_path)
        svc.create_obligation("dev", "agent", "fix_leak")
        svc.create_obligation("dev", "agent", "add_tests")

        # Fulfill only one
        svc.notify_action("dev", "agent", achieved=["fix_leak"])
        result = svc.check_permission(
            "dev", "agent", "execute_command", {"command": "git commit -m 'x'"},
        )
        assert result.permitted is False

        # Fulfill the second
        svc.notify_action("dev", "agent", achieved=["add_tests"])
        result = svc.check_permission(
            "dev", "agent", "execute_command", {"command": "git commit -m 'x'"},
        )
        assert result.permitted is True

    def test_non_matching_command_not_blocked(self, tmp_path):
        svc = self._make_service(tmp_path)
        svc.create_obligation("dev", "agent", "fix_leak")
        result = svc.check_permission(
            "dev", "agent", "execute_command", {"command": "echo hello"},
        )
        assert result.permitted is True

    def test_block_reason_describes_obligations(self, tmp_path):
        svc = self._make_service(tmp_path)
        svc.create_obligation("dev", "agent", "fix_leak")
        result = svc.check_permission(
            "dev", "agent", "execute_command", {"command": "git commit -m 'x'"},
        )
        assert "all obligations to be fulfilled" in result.reason


# --- Hook integration tests ---

class TestHookObligationGate:
    def _make_hook(self, tmp_path, spec_overrides=None):
        spec_dict = _make_spec_dict(**(spec_overrides or {}))
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")
        return hook

    def test_no_obligations_commit_allowed(self, tmp_path):
        hook = self._make_hook(tmp_path)
        r = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            agent="dev",
        )
        assert r["decision"] == "approve"

    def test_obligation_blocks_commit(self, tmp_path):
        hook = self._make_hook(tmp_path)
        hook.create_obligation("dev", "agent", "fix_leak")
        r = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            agent="dev",
        )
        assert r["decision"] == "block"

    def test_obligation_persisted_and_replayed(self, tmp_path):
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(_make_spec_dict()))
        state_file = tmp_path / "state.json"

        # Create obligation in first hook instance
        hook1 = GovernanceHook(spec_file, state_path=state_file)
        hook1.register_agent("dev", "agent")
        hook1.create_obligation("dev", "agent", "fix_leak")

        # New hook instance replays from state
        hook2 = GovernanceHook(spec_file, state_path=state_file)
        r = hook2.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            agent="dev",
        )
        assert r["decision"] == "block"

    def test_obligation_event_in_state(self, tmp_path):
        hook = self._make_hook(tmp_path)
        hook.create_obligation("dev", "agent", "fix_leak")
        state = json.loads((tmp_path / "state.json").read_text())
        obl_events = [e for e in state["events"] if e["type"] == "obligation_created"]
        assert len(obl_events) == 1
        assert obl_events[0]["objective"] == "fix_leak"

    def test_achieve_obligation_then_commit(self, tmp_path):
        hook = self._make_hook(tmp_path, spec_overrides={
            "achievement_triggers": [{
                "tool": "Bash",
                "command_pattern": "fix_leak_script",
                "exit_code": 0,
                "marks": "fix_leak",
            }],
        })
        hook.create_obligation("dev", "agent", "fix_leak")

        # Blocked before fulfillment
        r = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            agent="dev",
        )
        assert r["decision"] == "block"

        # Trigger achievement
        hook.post_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "fix_leak_script"},
             "tool_response": {"exitCode": 0}},
            agent="dev",
        )

        # Now commit should be allowed
        r = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            agent="dev",
        )
        assert r["decision"] == "approve"
