"""Tests for allow-once exception lifecycle."""

import json
import time
from pathlib import Path

import pytest
import yaml

from aorta4llm.integration.hooks import GovernanceHook


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
        state["exceptions"] = [{"path": ".env", "agent": "*", "ts": time.time(), "uses": 1}]
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
        state["exceptions"] = [{"path": ".env", "agent": "*", "ts": time.time(), "uses": 1}]
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
        state["exceptions"] = [{"path": ".env", "agent": "other-agent", "ts": time.time(), "uses": 1}]
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

    def test_expired_exception_is_ignored(self, tmp_path):
        """Exceptions older than EXCEPTION_TTL are pruned and ignored."""
        from aorta4llm.integration.hooks import EXCEPTION_TTL

        hook = _make_hook(tmp_path)
        state_path = tmp_path / "state.json"
        state = json.loads(state_path.read_text())
        # Exception created 5 hours ago (past the 4h TTL)
        state["exceptions"] = [{"path": ".env", "agent": "*",
                                "ts": time.time() - EXCEPTION_TTL - 3600, "uses": 1}]
        state_path.write_text(json.dumps(state))

        hook2 = GovernanceHook(tmp_path / "spec.yaml", state_path=state_path)
        result = hook2.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": ".env"}},
            agent="dev",
        )
        assert result["decision"] == "block"

        # Verify the expired exception was pruned from state
        state2 = json.loads(state_path.read_text())
        assert len(state2.get("exceptions", [])) == 0

    def test_read_consumes_for_no_access(self, tmp_path):
        """Reading a no-access file consumes the allow-once exception."""
        spec_dict = {
            "organization": "test",
            "roles": {"agent": {
                "objectives": [],
                "capabilities": ["read_file", "write_file"],
            }},
            "access": {
                "src/": "read-write",
                ".env": "no-access",
            },
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        state_path = tmp_path / "state.json"
        hook = GovernanceHook(spec_file, state_path=state_path)
        hook.register_agent("dev", "agent", scope="src/")

        # Add exception
        state = json.loads(state_path.read_text())
        state["exceptions"] = [{"path": ".env", "agent": "*", "ts": time.time(), "uses": 1}]
        state_path.write_text(json.dumps(state))

        hook2 = GovernanceHook(spec_file, state_path=state_path)
        # Read consumes because .env is no-access
        r1 = hook2.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": ".env"}},
            agent="dev",
        )
        assert r1["decision"] == "approve"

        # Second read blocked — exception consumed
        r2 = hook2.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": ".env"}},
            agent="dev",
        )
        assert r2["decision"] == "block"

    def test_read_does_not_consume_for_out_of_scope(self, tmp_path):
        """Reading an out-of-scope file does NOT consume the allow-once exception."""
        spec_dict = {
            "organization": "test",
            "roles": {"agent": {
                "objectives": [],
                "capabilities": ["read_file", "write_file"],
            }},
            "access": {
                "src/": "read-write",
            },
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        state_path = tmp_path / "state.json"
        hook = GovernanceHook(spec_file, state_path=state_path)
        hook.register_agent("dev", "agent", scope="src/")

        # Add exception for README.md (out of scope but not no-access)
        state = json.loads(state_path.read_text())
        state["exceptions"] = [{"path": "README.md", "agent": "*", "ts": time.time(), "uses": 1}]
        state_path.write_text(json.dumps(state))

        hook2 = GovernanceHook(spec_file, state_path=state_path)
        # Read does NOT consume
        r1 = hook2.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": "README.md"}},
            agent="dev",
        )
        assert r1["decision"] == "approve"

        # Write DOES consume
        r2 = hook2.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "README.md", "content": "x"}},
            agent="dev",
        )
        assert r2["decision"] == "approve"

        # Now exception is consumed — write blocked
        r3 = hook2.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "README.md", "content": "x"}},
            agent="dev",
        )
        assert r3["decision"] == "block"
