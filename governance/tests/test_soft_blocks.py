"""Tests for soft block event logging."""

import json

import yaml

from integration.hooks import GovernanceHook


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
