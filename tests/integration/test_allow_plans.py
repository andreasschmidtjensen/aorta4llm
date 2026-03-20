"""Tests for plan file exemption: agent can read/write Claude Code plan files."""

from pathlib import Path

import yaml

from aorta4llm.integration.hooks import GovernanceHook, _is_plan_path


class TestPlanPathDetection:

    def test_is_plan_path(self):
        plans_dir = str(Path.home() / ".claude" / "plans")
        assert _is_plan_path(plans_dir + "/greedy-sleeping-crescent.md")
        assert _is_plan_path(plans_dir + "/some-plan.md")
        assert not _is_plan_path("/some/other/path.md")
        assert not _is_plan_path(plans_dir.replace("/plans", "/settings.json"))

    def test_is_plan_path_exact_dir(self):
        plans_dir = str(Path.home() / ".claude" / "plans")
        assert _is_plan_path(plans_dir)


class TestAllowPlans:

    def _make_hook(self, tmp_path):
        spec = {
            "organization": "test_org",
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

    def test_plan_write_allowed(self, tmp_path):
        hook = self._make_hook(tmp_path)
        plans_dir = str(Path.home() / ".claude" / "plans")
        result = hook.pre_tool_use({
            "tool_name": "Write",
            "tool_input": {"file_path": plans_dir + "/greedy-sleeping-crescent.md", "content": "# Plan"},
        }, agent="agent")
        assert result["decision"] == "approve"

    def test_plan_read_allowed(self, tmp_path):
        hook = self._make_hook(tmp_path)
        plans_dir = str(Path.home() / ".claude" / "plans")
        result = hook.pre_tool_use({
            "tool_name": "Read",
            "tool_input": {"file_path": plans_dir + "/greedy-sleeping-crescent.md"},
        }, agent="agent")
        assert result["decision"] == "approve"

    def test_plan_edit_allowed(self, tmp_path):
        hook = self._make_hook(tmp_path)
        plans_dir = str(Path.home() / ".claude" / "plans")
        result = hook.pre_tool_use({
            "tool_name": "Edit",
            "tool_input": {"file_path": plans_dir + "/plan.md", "old_string": "a", "new_string": "b"},
        }, agent="agent")
        assert result["decision"] == "approve"

    def test_non_plan_claude_path_still_blocked(self, tmp_path):
        """Writes to ~/.claude/ outside plans/ are still blocked."""
        hook = self._make_hook(tmp_path)
        home = str(Path.home())
        result = hook.pre_tool_use({
            "tool_name": "Write",
            "tool_input": {"file_path": home + "/.claude/settings.json", "content": "{}"},
        }, agent="agent")
        assert result["decision"] == "block"
