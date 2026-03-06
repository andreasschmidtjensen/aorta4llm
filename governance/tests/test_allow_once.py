"""Tests for allow-once exception lifecycle."""

import json
from pathlib import Path

import pytest
import yaml

from integration.hooks import GovernanceHook


def _make_hook(tmp_path, norms=None):
    spec_dict = {
        "organization": "test",
        "roles": {
            "agent": {
                "objectives": ["task_complete"],
                "capabilities": ["read_file", "write_file", "execute_command"],
            }
        },
        "norms": norms or [{
            "role": "agent",
            "type": "protected",
            "paths": [".env"],
        }],
    }
    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(yaml.dump(spec_dict))
    hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
    hook.register_agent("dev", "agent", scope="src/")
    return hook


class TestAllowOnce:

    def test_blocked_without_exception(self, tmp_path):
        hook = _make_hook(tmp_path)
        result = hook.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": ".env"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_exception_allows_access(self, tmp_path):
        hook = _make_hook(tmp_path)

        # Write exception to state file.
        state_path = tmp_path / "state.json"
        state = json.loads(state_path.read_text())
        state["exceptions"] = [{"path": ".env", "agent": "*", "ts": 0, "uses": 1}]
        state_path.write_text(json.dumps(state))

        # Re-create hook to reload state.
        hook2 = GovernanceHook(tmp_path / "spec.yaml", state_path=state_path)
        result = hook2.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": ".env"}},
            agent="dev",
        )
        assert result["decision"] == "approve"

    def test_exception_consumed_after_use(self, tmp_path):
        hook = _make_hook(tmp_path)

        # Write exception.
        state_path = tmp_path / "state.json"
        state = json.loads(state_path.read_text())
        state["exceptions"] = [{"path": ".env", "agent": "*", "ts": 0, "uses": 1}]
        state_path.write_text(json.dumps(state))

        hook2 = GovernanceHook(tmp_path / "spec.yaml", state_path=state_path)

        # First access — approved, exception consumed.
        r1 = hook2.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": ".env"}},
            agent="dev",
        )
        assert r1["decision"] == "approve"

        # Second access — blocked again.
        r2 = hook2.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": ".env"}},
            agent="dev",
        )
        assert r2["decision"] == "block"

    def test_exception_agent_specific(self, tmp_path):
        hook = _make_hook(tmp_path)

        state_path = tmp_path / "state.json"
        state = json.loads(state_path.read_text())
        state["exceptions"] = [{"path": ".env", "agent": "other-agent", "ts": 0, "uses": 1}]
        state_path.write_text(json.dumps(state))

        hook2 = GovernanceHook(tmp_path / "spec.yaml", state_path=state_path)
        # "dev" agent should still be blocked.
        result = hook2.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": ".env"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_block_message_includes_allow_once_hint(self, tmp_path):
        hook = _make_hook(tmp_path)
        result = hook.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": ".env"}},
            agent="dev",
        )
        assert result["decision"] == "block"
        assert "aorta allow-once" in result["reason"]
