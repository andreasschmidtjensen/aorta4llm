"""Tests for soft block event logging and message truncation."""

import json

import yaml

from aorta4llm.integration.hooks import (
    GovernanceHook, _truncate_reason, _shorten_block_reason, _display_action,
)


class TestSoftBlockLogging:
    """Verify soft block events log the actual decision, not pre-decision."""

    def _make_hook(self, tmp_path):
        spec_dict = {
            "organization": "log_test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": [{
                "type": "forbidden_command",
                "role": "agent",
                "command_pattern": "git commit",
                "severity": "soft",
            }],
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")
        return hook

    def test_first_attempt_logged_as_block(self, tmp_path):
        hook = self._make_hook(tmp_path)
        cmd = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}}
        hook.pre_tool_use(cmd, agent="dev")

        lines = hook._events_path.read_text().strip().split("\n")
        checks = [json.loads(l) for l in lines if json.loads(l).get("type") == "check"]
        assert checks[-1]["decision"] == "block"

    def test_retry_logged_as_approve(self, tmp_path):
        hook = self._make_hook(tmp_path)
        cmd = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}}

        hook.pre_tool_use(cmd, agent="dev")  # first: block
        hook.pre_tool_use(cmd, agent="dev")  # retry: approve

        lines = hook._events_path.read_text().strip().split("\n")
        checks = [json.loads(l) for l in lines if json.loads(l).get("type") == "check"]
        assert checks[-1]["decision"] == "approve"
        assert checks[-1]["severity"] == "soft"

    def test_retry_with_whitespace_difference_matches(self, tmp_path):
        """Retry with reformatted heredoc whitespace should match the original."""
        hook = self._make_hook(tmp_path)
        cmd1 = {"tool_name": "Bash", "tool_input": {
            "command": "git commit -m \"$(cat <<'EOF'\nfeat: test\n\nEOF\n)\""
        }}
        cmd2 = {"tool_name": "Bash", "tool_input": {
            "command": "git commit -m \"$(cat <<'EOF'\nfeat: test\nEOF\n)\""
        }}

        result1 = hook.pre_tool_use(cmd1, agent="dev")
        assert result1["decision"] == "block"

        result2 = hook.pre_tool_use(cmd2, agent="dev")
        assert result2["decision"] == "approve"

    def test_different_commands_dont_match(self, tmp_path):
        """git commit -m 'x' and git commit -am 'x' should NOT match."""
        hook = self._make_hook(tmp_path)
        cmd1 = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}}
        cmd2 = {"tool_name": "Bash", "tool_input": {"command": "git commit -am 'x'"}}

        result1 = hook.pre_tool_use(cmd1, agent="dev")
        assert result1["decision"] == "block"

        result2 = hook.pre_tool_use(cmd2, agent="dev")
        assert result2["decision"] == "block"  # different command, not a retry


class TestHardBlockOverridesSoft:
    """Hard blocks must take priority over soft blocks on the same command.

    Regression test: when safe-agent (soft forbidden_command on git commit) is
    combined with test-gate (hard required_before on git commit), the soft block
    was masking the hard block, allowing commits with failing tests.
    """

    def _make_combined_hook(self, tmp_path):
        """Hook with both soft forbidden_command and hard required_before on git commit."""
        spec_dict = {
            "organization": "combined_test",
            "roles": {
                "agent": {
                    "objectives": ["tests_passing", "task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": [
                {
                    "type": "forbidden_command",
                    "role": "agent",
                    "command_pattern": "git commit",
                    "severity": "soft",
                },
                {
                    "type": "required_before",
                    "role": "agent",
                    "command_pattern": "git commit",
                    "requires": "tests_passing",
                },
            ],
            "achievement_triggers": [{
                "tool": "Bash",
                "command_pattern": "pytest",
                "exit_code": 0,
                "marks": "tests_passing",
                "reset_on_file_change": True,
            }],
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent", scope="src/")
        return hook

    def test_hard_block_wins_over_soft(self, tmp_path):
        """When tests haven't passed, git commit should be hard-blocked, not soft."""
        hook = self._make_combined_hook(tmp_path)
        cmd = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'test'"}}

        result = hook.pre_tool_use(cmd, agent="dev")
        assert result["decision"] == "block"
        # The hard required_before block should win over the soft forbidden_command
        assert "requires 'tests_passing'" in result["reason"]

    def test_soft_block_after_tests_pass(self, tmp_path):
        """After tests pass, only the soft forbidden_command remains."""
        hook = self._make_combined_hook(tmp_path)

        # Simulate pytest passing
        hook.post_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "pytest"},
             "tool_response": {"exit_code": 0}},
            agent="dev",
        )

        cmd = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'test'"}}
        result = hook.pre_tool_use(cmd, agent="dev")
        assert result["decision"] == "block"
        assert result["reason"].startswith("SOFT BLOCK:")

    def test_soft_retry_approved_after_tests_pass(self, tmp_path):
        """After tests pass, the soft block retry should be approved."""
        hook = self._make_combined_hook(tmp_path)

        # Simulate pytest passing
        hook.post_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "pytest"},
             "tool_response": {"exit_code": 0}},
            agent="dev",
        )

        cmd = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'test'"}}
        hook.pre_tool_use(cmd, agent="dev")  # first: soft block
        result = hook.pre_tool_use(cmd, agent="dev")  # retry: approve
        assert result["decision"] == "approve"

    def test_file_change_resets_to_hard_block(self, tmp_path):
        """After tests pass, a file write resets tests_passing, making commit hard-blocked again."""
        hook = self._make_combined_hook(tmp_path)

        # Tests pass
        hook.post_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "pytest"},
             "tool_response": {"exit_code": 0}},
            agent="dev",
        )

        # File write clears tests_passing
        hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "src/app.py"}},
            agent="dev",
        )

        # Commit should now be hard-blocked (required_before), not soft
        cmd = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'test'"}}
        result = hook.pre_tool_use(cmd, agent="dev")
        assert result["decision"] == "block"
        assert "requires 'tests_passing'" in result["reason"]
        assert "SOFT BLOCK" not in result["reason"]


class TestTruncateReason:

    def test_short_reason_unchanged(self):
        reason = "command contains 'git commit'"
        assert _truncate_reason(reason) == reason

    def test_long_reason_truncated(self):
        reason = "x" * 300
        result = _truncate_reason(reason)
        assert len(result) == 203  # 200 + "..."
        assert result.endswith("...")

    def test_newlines_collapsed(self):
        reason = "execute_command(git commit -m 'feat:\nsome long\nmessage') blocked"
        result = _truncate_reason(reason)
        assert "\n" not in result

    def test_custom_max_len(self):
        reason = "x" * 50
        result = _truncate_reason(reason, max_len=20)
        assert len(result) == 23  # 20 + "..."


class TestShortenBlockReason:

    def test_extracts_tail_from_engine_reason(self):
        reason = "execute_command(git commit -m 'test') blocked for dev (role: agent): command contains 'git commit'"
        assert _shorten_block_reason(reason) == "command contains 'git commit'"

    def test_extracts_tail_from_scope_reason(self):
        reason = "write_file(README.md) blocked for dev (role: agent): path is outside allowed scopes ['src/']"
        assert _shorten_block_reason(reason) == "path is outside allowed scopes ['src/']"

    def test_extracts_tail_from_required_before(self):
        reason = "execute_command(git commit -m 'x') blocked for dev (role: agent): requires 'tests_passing' to be achieved first"
        assert _shorten_block_reason(reason) == "requires 'tests_passing' to be achieved first"

    def test_fallback_for_plain_reason(self):
        reason = "agents cannot run governance commands"
        assert _shorten_block_reason(reason) == reason

    def test_long_heredoc_command_not_echoed(self):
        heredoc = "git commit -m \"$(cat <<'EOF'\nfeat: long\n\nmultiline message\nEOF\n)\""
        reason = f"execute_command({heredoc}) blocked for dev (role: agent): command contains 'git commit'"
        assert _shorten_block_reason(reason) == "command contains 'git commit'"


class TestDisplayAction:

    def test_write_file_with_path(self):
        assert _display_action("write_file", "README.md") == "Write to README.md"

    def test_read_file_with_path(self):
        assert _display_action("read_file", ".env") == "Read .env"

    def test_execute_command_no_path(self):
        assert _display_action("execute_command", "") == "Bash"

    def test_unknown_action(self):
        assert _display_action("custom_action", "x.py") == "custom_action x.py"
