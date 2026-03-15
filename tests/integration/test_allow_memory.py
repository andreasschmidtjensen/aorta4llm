"""Tests for allow_memory: agent can write to Claude Code memory directory."""

import json
import os
from pathlib import Path

import yaml

from aorta4llm.integration.hooks import GovernanceHook, _memory_path_prefix, _is_memory_path


class TestMemoryPathDerivation:

    def test_memory_path_prefix(self):
        prefix = _memory_path_prefix()
        home = str(Path.home())
        assert prefix.startswith(home)
        assert "/.claude/projects/" in prefix
        assert prefix.endswith("/memory")

    def test_is_memory_path(self):
        prefix = _memory_path_prefix()
        assert _is_memory_path(prefix + "/user_role.md")
        assert _is_memory_path(prefix + "/MEMORY.md")
        assert not _is_memory_path("/some/other/path.md")
        assert not _is_memory_path(prefix.replace("/memory", "/settings.json"))


class TestAllowMemory:

    def _make_hook(self, tmp_path, allow_memory=True):
        spec = {
            "organization": "test_org",
            "allow_memory": allow_memory,
            "roles": {"agent": {
                "objectives": [],
                "capabilities": ["read_file", "write_file"],
            }},
            "access": {"src/": "read-write"},
        }
        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(yaml.dump(spec, sort_keys=False))
        state_path = tmp_path / "state.json"
        events_path = tmp_path / "events.jsonl"
        hook = GovernanceHook(spec_path, state_path=state_path, events_path=str(events_path))
        hook.register_agent("agent", "agent", "src/")
        return hook

    def test_memory_write_allowed(self, tmp_path):
        hook = self._make_hook(tmp_path, allow_memory=True)
        prefix = _memory_path_prefix()
        result = hook.pre_tool_use({
            "tool_name": "Write",
            "tool_input": {"file_path": prefix + "/user_role.md", "content": "test"},
        }, agent="agent")
        assert result["decision"] == "approve"

    def test_memory_write_blocked_when_disabled(self, tmp_path):
        hook = self._make_hook(tmp_path, allow_memory=False)
        prefix = _memory_path_prefix()
        result = hook.pre_tool_use({
            "tool_name": "Write",
            "tool_input": {"file_path": prefix + "/user_role.md", "content": "test"},
        }, agent="agent")
        assert result["decision"] == "block"

    def test_non_memory_claude_path_still_blocked(self, tmp_path):
        """Writes to ~/.claude/ outside memory/ are still blocked."""
        hook = self._make_hook(tmp_path, allow_memory=True)
        home = str(Path.home())
        result = hook.pre_tool_use({
            "tool_name": "Write",
            "tool_input": {"file_path": home + "/.claude/settings.json", "content": "{}"},
        }, agent="agent")
        assert result["decision"] == "block"

    def test_memory_edit_allowed(self, tmp_path):
        hook = self._make_hook(tmp_path, allow_memory=True)
        prefix = _memory_path_prefix()
        result = hook.pre_tool_use({
            "tool_name": "Edit",
            "tool_input": {"file_path": prefix + "/MEMORY.md", "old_string": "a", "new_string": "b"},
        }, agent="agent")
        assert result["decision"] == "approve"

    def test_in_scope_write_still_works(self, tmp_path):
        hook = self._make_hook(tmp_path, allow_memory=True)
        result = hook.pre_tool_use({
            "tool_name": "Write",
            "tool_input": {"file_path": "src/foo.py", "content": "x = 1"},
        }, agent="agent")
        assert result["decision"] == "approve"
