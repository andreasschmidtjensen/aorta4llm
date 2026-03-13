"""Tests for guardrails: thrashing detection and behavioral budgets."""

import json
import yaml
import pytest

from aorta4llm.integration.hooks import GovernanceHook


def _make_spec(tmp_path, guardrails=None, extra=None):
    """Create an org spec with guardrails config and register an agent."""
    spec = {
        "organization": "test",
        "roles": {"agent": {"objectives": [], "capabilities": ["read_file", "write_file", "execute_command"]}},
        "norms": [{"type": "scope", "role": "agent", "paths": ["src/"]}],
    }
    if guardrails:
        spec["guardrails"] = guardrails
    if extra:
        spec.update(extra)
    spec_path = tmp_path / ".aorta" / "test.yaml"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(yaml.dump(spec, sort_keys=False))
    # Create settings dir for governance
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(exist_ok=True)
    (claude_dir / "settings.local.json").write_text("{}")
    hook = GovernanceHook(spec_path)
    hook.register_agent("dev", "agent", "src/")
    return hook


def _write_context(path="src/foo.py"):
    return {"tool_name": "Write", "tool_input": {"file_path": path}}


def _bash_context(command="echo hello", exit_code=0):
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"exit_code": exit_code},
    }


def _bash_fail_context(command="pytest"):
    return _bash_context(command, exit_code=1)


class TestFailureRate:

    def test_no_hold_below_threshold(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "window_size": 4,
            "failure_rate": {"threshold": 0.5, "action": "hold"},
        })
        # 1 failure out of 4 = 25%, below 50%
        hook.post_tool_use(_bash_context(), agent="dev")
        hook.post_tool_use(_bash_context(), agent="dev")
        hook.post_tool_use(_bash_context(), agent="dev")
        result = hook.post_tool_use(_bash_fail_context(), agent="dev")
        assert "_guardrail_warning" not in result

    def test_hold_at_threshold(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "window_size": 4,
            "failure_rate": {"threshold": 0.5, "action": "hold"},
        })
        # 2 failures out of 4 = 50%, meets threshold
        hook.post_tool_use(_bash_context(), agent="dev")
        hook.post_tool_use(_bash_fail_context(), agent="dev")
        hook.post_tool_use(_bash_context(), agent="dev")
        result = hook.post_tool_use(_bash_fail_context(), agent="dev")
        assert "_guardrail_warning" in result
        assert "HOLD" in result["_guardrail_warning"]

    def test_warning_at_threshold(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "window_size": 4,
            "failure_rate": {"threshold": 0.5, "action": "warning"},
        })
        hook.post_tool_use(_bash_context(), agent="dev")
        hook.post_tool_use(_bash_fail_context(), agent="dev")
        hook.post_tool_use(_bash_context(), agent="dev")
        result = hook.post_tool_use(_bash_fail_context(), agent="dev")
        assert "_guardrail_warning" in result
        assert "HOLD" not in result["_guardrail_warning"]
        assert "GUARDRAIL" in result["_guardrail_warning"]

    def test_hold_blocks_pre_tool_use(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "window_size": 2,
            "failure_rate": {"threshold": 0.5, "action": "hold"},
        })
        hook.post_tool_use(_bash_fail_context(), agent="dev")
        hook.post_tool_use(_bash_fail_context(), agent="dev")
        # Now hold is active — pre_tool_use should block
        result = hook.pre_tool_use(_write_context(), agent="dev")
        assert result["decision"] == "block"
        assert "HOLD" in result["reason"]
        assert "aorta continue" in result["reason"]

    def test_ring_window_slides(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "window_size": 3,
            "failure_rate": {"threshold": 0.5, "action": "hold"},
        })
        # Fill ring with failures
        hook.post_tool_use(_bash_fail_context(), agent="dev")
        hook.post_tool_use(_bash_fail_context(), agent="dev")
        hook.post_tool_use(_bash_fail_context(), agent="dev")
        assert hook._hold is not None
        # Clear and add successes — old failures should slide out
        hook.clear_hold()
        hook.post_tool_use(_bash_context(), agent="dev")
        hook.post_tool_use(_bash_context(), agent="dev")
        result = hook.post_tool_use(_bash_context(), agent="dev")
        assert "_guardrail_warning" not in result
        assert hook._hold is None


class TestPerFileRewrites:

    def test_no_trigger_below_threshold(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "per_file_rewrites": {"threshold": 3, "action": "hold"},
        })
        hook.post_tool_use(_write_context(), agent="dev")
        result = hook.post_tool_use(_write_context(), agent="dev")
        assert "_guardrail_warning" not in result

    def test_hold_at_threshold(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "per_file_rewrites": {"threshold": 3, "action": "hold"},
        })
        hook.post_tool_use(_write_context(), agent="dev")
        hook.post_tool_use(_write_context(), agent="dev")
        result = hook.post_tool_use(_write_context(), agent="dev")
        assert "_guardrail_warning" in result
        assert "HOLD" in result["_guardrail_warning"]
        assert "src/foo.py" in result["_guardrail_warning"]

    def test_different_files_tracked_separately(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "per_file_rewrites": {"threshold": 3, "action": "hold"},
        })
        hook.post_tool_use(_write_context("src/a.py"), agent="dev")
        hook.post_tool_use(_write_context("src/a.py"), agent="dev")
        result = hook.post_tool_use(_write_context("src/b.py"), agent="dev")
        assert "_guardrail_warning" not in result

    def test_warning_mode(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "per_file_rewrites": {"threshold": 2, "action": "warning"},
        })
        hook.post_tool_use(_write_context(), agent="dev")
        result = hook.post_tool_use(_write_context(), agent="dev")
        assert "_guardrail_warning" in result
        assert "HOLD" not in result["_guardrail_warning"]


class TestFilesModified:

    def test_no_trigger_below_threshold(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "files_modified": {"threshold": 3, "action": "warning"},
        })
        hook.post_tool_use(_write_context("src/a.py"), agent="dev")
        result = hook.post_tool_use(_write_context("src/b.py"), agent="dev")
        assert "_guardrail_warning" not in result

    def test_warning_at_threshold(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "files_modified": {"threshold": 3, "action": "warning"},
        })
        hook.post_tool_use(_write_context("src/a.py"), agent="dev")
        hook.post_tool_use(_write_context("src/b.py"), agent="dev")
        result = hook.post_tool_use(_write_context("src/c.py"), agent="dev")
        assert "_guardrail_warning" in result
        assert "3 unique files" in result["_guardrail_warning"]

    def test_hold_mode(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "files_modified": {"threshold": 2, "action": "hold"},
        })
        hook.post_tool_use(_write_context("src/a.py"), agent="dev")
        result = hook.post_tool_use(_write_context("src/b.py"), agent="dev")
        assert "_guardrail_warning" in result
        assert "HOLD" in result["_guardrail_warning"]


class TestBashCommands:

    def test_no_trigger_below_threshold(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "bash_commands": {"threshold": 3, "action": "warning"},
        })
        hook.post_tool_use(_bash_context(), agent="dev")
        result = hook.post_tool_use(_bash_context(), agent="dev")
        assert "_guardrail_warning" not in result

    def test_warning_at_threshold(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "bash_commands": {"threshold": 3, "action": "warning"},
        })
        hook.post_tool_use(_bash_context(), agent="dev")
        hook.post_tool_use(_bash_context(), agent="dev")
        result = hook.post_tool_use(_bash_context(), agent="dev")
        assert "_guardrail_warning" in result
        assert "3 bash commands" in result["_guardrail_warning"]


class TestClearHold:

    def test_clear_hold_resets_all(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "window_size": 2,
            "failure_rate": {"threshold": 0.5, "action": "hold"},
            "per_file_rewrites": {"threshold": 2, "action": "hold"},
        })
        # Trigger hold via failure rate
        hook.post_tool_use(_bash_fail_context(), agent="dev")
        hook.post_tool_use(_bash_fail_context(), agent="dev")
        assert hook._hold is not None
        assert len(hook._action_ring) > 0

        hook.clear_hold()
        assert hook._hold is None
        assert hook._action_ring == []
        assert hook._file_write_counts == {}
        assert hook._bash_command_count == 0

    def test_clear_hold_allows_actions(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "window_size": 2,
            "failure_rate": {"threshold": 0.5, "action": "hold"},
        })
        hook.post_tool_use(_bash_fail_context(), agent="dev")
        hook.post_tool_use(_bash_fail_context(), agent="dev")
        # Blocked
        result = hook.pre_tool_use(_write_context(), agent="dev")
        assert result["decision"] == "block"
        # Clear
        hook.clear_hold()
        result = hook.pre_tool_use(_write_context(), agent="dev")
        assert result["decision"] == "approve"


class TestStatePersistence:

    def test_hold_persists_across_instances(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "window_size": 2,
            "failure_rate": {"threshold": 0.5, "action": "hold"},
        })
        hook.post_tool_use(_bash_fail_context(), agent="dev")
        hook.post_tool_use(_bash_fail_context(), agent="dev")
        assert hook._hold is not None

        # New instance loads persisted state
        spec_path = tmp_path / ".aorta" / "test.yaml"
        hook2 = GovernanceHook(spec_path)
        assert hook2._hold is not None
        result = hook2.pre_tool_use(_write_context(), agent="dev")
        assert result["decision"] == "block"

    def test_counters_persist(self, tmp_path):
        hook = _make_spec(tmp_path, guardrails={
            "per_file_rewrites": {"threshold": 5, "action": "hold"},
        })
        hook.post_tool_use(_write_context(), agent="dev")
        hook.post_tool_use(_write_context(), agent="dev")

        spec_path = tmp_path / ".aorta" / "test.yaml"
        hook2 = GovernanceHook(spec_path)
        assert hook2._file_write_counts.get("src/foo.py") == 2


class TestNoGuardrails:

    def test_no_config_no_effect(self, tmp_path):
        """Without guardrails config, nothing is tracked or triggered."""
        hook = _make_spec(tmp_path)
        for _ in range(20):
            result = hook.post_tool_use(_bash_fail_context(), agent="dev")
        assert "_guardrail_warning" not in result
        assert hook._hold is None


class TestMixedChecks:

    def test_multiple_checks_combined(self, tmp_path):
        """Multiple guardrail checks can be configured together."""
        hook = _make_spec(tmp_path, guardrails={
            "window_size": 4,
            "failure_rate": {"threshold": 0.75, "action": "hold"},
            "per_file_rewrites": {"threshold": 2, "action": "warning"},
            "files_modified": {"threshold": 10, "action": "warning"},
            "bash_commands": {"threshold": 50, "action": "warning"},
        })
        # Two writes to same file → warning from per_file_rewrites
        hook.post_tool_use(_write_context(), agent="dev")
        result = hook.post_tool_use(_write_context(), agent="dev")
        assert "_guardrail_warning" in result
        assert "HOLD" not in result["_guardrail_warning"]
