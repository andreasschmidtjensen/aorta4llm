"""Tests for the Claude Code hook layer — scope, persistence, triggers, prompt injection."""

import json
from pathlib import Path

import yaml
import pytest

from aorta4llm.integration.hooks import GovernanceHook, TOOL_ACTION_MAP, _normalize_git_cmd


def _make_scoped_hook(tmp_path, scope="src/"):
    """Hook with scope norm — the common case for hook tests."""
    spec_dict = {
        "organization": "hook_test",
        "roles": {
            "agent": {
                "objectives": ["task_complete"],
                "capabilities": ["read_file", "write_file", "execute_command"],
            }
        },
        "norms": [{
            "role": "agent",
            "type": "scope",
            "paths": [scope],
        }],
    }
    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(yaml.dump(spec_dict))
    hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
    hook.register_agent("dev", "agent", scope=scope)
    return hook


@pytest.fixture
def hook(tmp_path):
    """Hook with src/ scope."""
    return _make_scoped_hook(tmp_path, scope="src/")


class TestHookIntegration:
    """Tests for the Claude Code hook layer."""

    def test_pre_tool_use_blocks_out_of_scope(self, hook):
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "docs/x.md"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_pre_tool_use_allows_in_scope(self, hook):
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "src/app.py"}},
            agent="dev",
        )
        assert result["decision"] == "approve"

    def test_pre_tool_use_approves_unknown_tool(self, hook):
        result = hook.pre_tool_use(
            {"tool_name": "WebSearch", "tool_input": {"query": "test"}},
            agent="dev",
        )
        assert result["decision"] == "approve"

    def test_pre_tool_use_blocks_unregistered_agent(self, hook):
        """Unregistered agents are denied (fail-closed)."""
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "x.py"}},
            agent="unknown-agent",
        )
        assert result["decision"] == "block"
        assert "not registered" in result["reason"]

    def test_edit_maps_to_write_file(self, hook):
        result = hook.pre_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "docs/x.md"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_state_persistence(self, tmp_path):
        """State survives across hook instances."""
        spec_dict = {
            "organization": "persist_test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": [{"role": "agent", "type": "scope", "paths": ["src/"]}],
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        state_file = tmp_path / "state.json"

        # First instance: register agent
        hook1 = GovernanceHook(spec_file, state_path=state_file)
        hook1.register_agent("dev", "agent", scope="src/")

        # Second instance: replays state, agent is known
        hook2 = GovernanceHook(spec_file, state_path=state_file)
        result = hook2.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "docs/x.md"}},
            agent="dev",
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
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "pytest tests/"}},
            agent="dev",
        )
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

    def test_git_dash_c_normalized_for_pattern_matching(self, tmp_path):
        """git -C <path> commit should still match 'git commit' pattern."""
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
                "type": "forbidden_command",
                "command_pattern": "git commit",
                "severity": "soft",
            }],
        }
        spec_file = tmp_path / "gate.yaml"
        spec_file.write_text(yaml.dump(spec_dict))

        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")

        # git -C /path commit should be blocked just like git commit
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {
                "command": "git -C /private/tmp/test-project commit -m 'feat: x'",
            }},
            agent="dev",
        )
        assert result["decision"] == "block"
        assert "SOFT BLOCK" in result["reason"]

    def test_git_dash_c_compound_command_normalized(self, tmp_path):
        """git -C in compound commands (add && commit) should be normalized."""
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
                "type": "forbidden_command",
                "command_pattern": "git commit",
                "severity": "soft",
            }],
        }
        spec_file = tmp_path / "gate.yaml"
        spec_file.write_text(yaml.dump(spec_dict))

        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")

        # Compound: git -C /path add ... && git -C /path commit ...
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {
                "command": (
                    "git -C /private/tmp/test-project add src/x.py && "
                    "git -C /private/tmp/test-project commit -m 'feat: x'"
                ),
            }},
            agent="dev",
        )
        assert result["decision"] == "block"
        assert "SOFT BLOCK" in result["reason"]

    def test_git_dash_c_normalized_for_required_before(self, tmp_path):
        """git -C <path> commit should trigger required_before blocks."""
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
        spec_file = tmp_path / "gate.yaml"
        spec_file.write_text(yaml.dump(spec_dict))

        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")

        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {
                "command": "git -C /some/path commit -m 'test'",
            }},
            agent="dev",
        )
        assert result["decision"] == "block"
        assert "tests_passing" in result["reason"]

    def test_absolute_path_in_bash_write_normalized(self, tmp_path):
        """Bash analysis write paths should be normalized to relative."""
        aorta_dir = tmp_path / ".aorta"
        aorta_dir.mkdir()
        spec_dict = {
            "organization": "test_bash",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": [{
                "role": "agent",
                "type": "scope",
                "paths": ["src/"],
            }],
            "bash_analysis": True,
        }
        spec_file = aorta_dir / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))

        hook = GovernanceHook(spec_file, state_path=aorta_dir / "state.json")
        hook.register_agent("dev", "agent", scope="src/")

        # mkdir with absolute path that resolves to in-scope should be approved
        abs_path = str(tmp_path / "src" / "models")
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {
                "command": f"mkdir -p {abs_path}",
            }},
            agent="dev",
        )
        assert result["decision"] == "approve"

    def test_absolute_path_in_bash_write_out_of_scope_blocked(self, tmp_path):
        """Bash analysis should block absolute paths outside scope after normalization."""
        aorta_dir = tmp_path / ".aorta"
        aorta_dir.mkdir()
        spec_dict = {
            "organization": "test_bash",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": [{
                "role": "agent",
                "type": "scope",
                "paths": ["src/"],
            }],
            "bash_analysis": True,
        }
        spec_file = aorta_dir / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))

        hook = GovernanceHook(spec_file, state_path=aorta_dir / "state.json")
        hook.register_agent("dev", "agent", scope="src/")

        # cp to /tmp should still be blocked (not relative to project)
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {
                "command": "cp src/app.py /tmp/leak.py",
            }},
            agent="dev",
        )
        assert result["decision"] == "block"
        assert "/tmp/leak.py" in result["reason"]


class TestPostToolUseAchievements:
    """Tests for achievement tracking via PostToolUse triggers."""

    def _make_hook_with_triggers(self, tmp_path, triggers: list[dict]):
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

        # Tests pass -> trigger fires
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

    def test_reset_on_file_change_invalidates_achievement(self, tmp_path):
        """Writing a file clears achievements marked with reset_on_file_change."""
        spec_dict = {
            "organization": "reset_test",
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
                "reset_on_file_change": True,
            }],
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")

        # Tests pass -> commit unlocked
        hook.post_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "pytest"},
             "tool_response": {"exit_code": 0}},
            agent="dev",
        )
        r1 = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            agent="dev",
        )
        assert r1["decision"] == "approve"

        # Write a file -> achievement cleared
        hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "src/app.py"}},
            agent="dev",
        )

        # Commit blocked again
        r2 = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            agent="dev",
        )
        assert r2["decision"] == "block"


class TestPipedCommandSkipsExitCodeTriggers:
    """Piped commands have unreliable exit codes — don't trust them for triggers."""

    def _make_hook_with_triggers(self, tmp_path, triggers):
        spec = {
            "organization": "pipe_test",
            "roles": {"agent": {"objectives": [], "capabilities": ["execute_command"]}},
            "achievement_triggers": triggers,
        }
        spec_path = tmp_path / ".aorta" / "spec.yaml"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        import yaml
        spec_path.write_text(yaml.dump(spec, sort_keys=False))
        hook = GovernanceHook(spec_path)
        hook.register_agent("dev", "agent")
        return hook

    def test_piped_command_skips_exit_code_trigger(self, tmp_path):
        """pytest | tail should NOT mark tests_passing even with exit code 0."""
        triggers = [{"tool": "Bash", "command_pattern": "pytest", "exit_code": 0, "marks": "tests_passing"}]
        hook = self._make_hook_with_triggers(tmp_path, triggers)
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "pytest tests/ | tail -20"},
             "tool_response": {"exit_code": 0}},
            agent="dev",
        )
        # Should NOT have achieved tests_passing
        achievements = [e for e in hook._events if e["type"] == "achieved"]
        assert len(achievements) == 0

    def test_unpiped_command_still_triggers(self, tmp_path):
        """Regular pytest (no pipe) should still mark tests_passing."""
        triggers = [{"tool": "Bash", "command_pattern": "pytest", "exit_code": 0, "marks": "tests_passing"}]
        hook = self._make_hook_with_triggers(tmp_path, triggers)
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "pytest tests/"},
             "tool_response": {"exit_code": 0}},
            agent="dev",
        )
        achievements = [e for e in hook._events if e["type"] == "achieved"]
        assert len(achievements) == 1
        assert "tests_passing" in achievements[0]["objectives"]

    def test_redirect_not_treated_as_pipe(self, tmp_path):
        """2>&1 redirect should not be treated as a pipe."""
        triggers = [{"tool": "Bash", "command_pattern": "pytest", "exit_code": 0, "marks": "tests_passing"}]
        hook = self._make_hook_with_triggers(tmp_path, triggers)
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "pytest tests/ 2>&1"},
             "tool_response": {"exit_code": 0}},
            agent="dev",
        )
        achievements = [e for e in hook._events if e["type"] == "achieved"]
        assert len(achievements) == 1

    def test_or_operator_not_treated_as_pipe(self, tmp_path):
        """|| (or) should not be treated as a pipe."""
        triggers = [{"tool": "Bash", "command_pattern": "pytest", "exit_code": 0, "marks": "tests_passing"}]
        hook = self._make_hook_with_triggers(tmp_path, triggers)
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "pytest tests/ || echo failed"},
             "tool_response": {"exit_code": 0}},
            agent="dev",
        )
        achievements = [e for e in hook._events if e["type"] == "achieved"]
        assert len(achievements) == 1

    def test_pipe_inside_quotes_not_treated_as_pipe(self, tmp_path):
        """A | inside quotes should not be treated as a pipe."""
        triggers = [{"tool": "Bash", "command_pattern": "pytest", "exit_code": 0, "marks": "tests_passing"}]
        hook = self._make_hook_with_triggers(tmp_path, triggers)
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "pytest tests/ -k 'foo|bar'"},
             "tool_response": {"exit_code": 0}},
            agent="dev",
        )
        achievements = [e for e in hook._events if e["type"] == "achieved"]
        assert len(achievements) == 1

    def test_trigger_without_exit_code_still_works_on_piped(self, tmp_path):
        """Triggers that don't require exit_code should still fire for piped commands."""
        triggers = [{"tool": "Bash", "command_pattern": "deploy", "marks": "deployed"}]
        hook = self._make_hook_with_triggers(tmp_path, triggers)
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "deploy.sh | tee log.txt"},
             "tool_response": {"exit_code": 0}},
            agent="dev",
        )
        achievements = [e for e in hook._events if e["type"] == "achieved"]
        assert len(achievements) == 1


class TestRicherTriggers:
    """Tests for path_pattern, output_contains, and clears triggers."""

    def _make_hook(self, tmp_path, triggers, norms=None):
        objectives = list({t.get("marks") or t.get("clears") for t in triggers})
        spec_dict = {
            "organization": "rich_trigger_test",
            "roles": {
                "agent": {
                    "objectives": objectives,
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "achievement_triggers": triggers,
        }
        if norms:
            spec_dict["norms"] = norms
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")
        return hook

    # --- path_pattern ---

    def test_path_pattern_matches_write(self, tmp_path):
        triggers = [{"tool": "Write", "path_pattern": "src/models/*.py", "marks": "model_created"}]
        hook = self._make_hook(tmp_path, triggers)
        hook.post_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "src/models/user.py"}},
            agent="dev",
        )
        achieved = [e for e in hook._events if e["type"] == "achieved"]
        assert len(achieved) == 1
        assert "model_created" in achieved[0]["objectives"]

    def test_path_pattern_no_match_wrong_path(self, tmp_path):
        triggers = [{"tool": "Write", "path_pattern": "src/models/*.py", "marks": "model_created"}]
        hook = self._make_hook(tmp_path, triggers)
        hook.post_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "src/views/home.py"}},
            agent="dev",
        )
        achieved = [e for e in hook._events if e["type"] == "achieved"]
        assert len(achieved) == 0

    def test_path_pattern_with_absolute_path(self, tmp_path, monkeypatch):
        """Absolute paths are made relative before matching."""
        monkeypatch.chdir(tmp_path)
        triggers = [{"tool": "Write", "path_pattern": "src/models/*.py", "marks": "model_created"}]
        hook = self._make_hook(tmp_path, triggers)
        hook.post_tool_use(
            {"tool_name": "Write",
             "tool_input": {"file_path": str(tmp_path / "src/models/user.py")}},
            agent="dev",
        )
        achieved = [e for e in hook._events if e["type"] == "achieved"]
        assert len(achieved) == 1

    def test_path_pattern_on_edit(self, tmp_path):
        triggers = [{"tool": "Edit", "path_pattern": "migrations/*.py", "marks": "migration_created"}]
        hook = self._make_hook(tmp_path, triggers)
        hook.post_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "migrations/001.py"}},
            agent="dev",
        )
        achieved = [e for e in hook._events if e["type"] == "achieved"]
        assert len(achieved) == 1

    # --- output_contains ---

    def test_output_contains_matches(self, tmp_path):
        triggers = [{"tool": "Bash", "output_contains": "All tests passed", "marks": "tests_passing"}]
        hook = self._make_hook(tmp_path, triggers)
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "pytest"},
             "tool_response": {"exit_code": 0, "stdout": "===== All tests passed ====="}},
            agent="dev",
        )
        achieved = [e for e in hook._events if e["type"] == "achieved"]
        assert len(achieved) == 1

    def test_output_contains_no_match(self, tmp_path):
        triggers = [{"tool": "Bash", "output_contains": "All tests passed", "marks": "tests_passing"}]
        hook = self._make_hook(tmp_path, triggers)
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "pytest"},
             "tool_response": {"exit_code": 1, "stdout": "FAILED 3 tests"}},
            agent="dev",
        )
        achieved = [e for e in hook._events if e["type"] == "achieved"]
        assert len(achieved) == 0

    def test_output_contains_regex(self, tmp_path):
        triggers = [{"tool": "Bash", "output_contains": r"\d+ passed", "marks": "tests_passing"}]
        hook = self._make_hook(tmp_path, triggers)
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "pytest"},
             "tool_response": {"exit_code": 0, "stdout": "42 passed in 1.2s"}},
            agent="dev",
        )
        achieved = [e for e in hook._events if e["type"] == "achieved"]
        assert len(achieved) == 1

    def test_output_contains_with_command_pattern(self, tmp_path):
        """output_contains and command_pattern can be combined."""
        triggers = [{
            "tool": "Bash",
            "command_pattern": "pytest",
            "output_contains": "passed",
            "marks": "tests_passing",
        }]
        hook = self._make_hook(tmp_path, triggers)
        # Right command, wrong output
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "pytest"},
             "tool_response": {"exit_code": 1, "stdout": "FAILED"}},
            agent="dev",
        )
        assert len([e for e in hook._events if e["type"] == "achieved"]) == 0
        # Right command, right output
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "pytest"},
             "tool_response": {"exit_code": 0, "stdout": "5 passed"}},
            agent="dev",
        )
        assert len([e for e in hook._events if e["type"] == "achieved"]) == 1

    # --- clears (negative triggers) ---

    def test_clears_removes_achievement(self, tmp_path):
        triggers = [
            {"tool": "Bash", "command_pattern": "pytest", "exit_code": 0, "marks": "tests_passing"},
            {"tool": "Bash", "output_contains": "FAIL|error|Exception", "clears": "tests_passing"},
        ]
        hook = self._make_hook(tmp_path, triggers)
        # Mark achievement
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "pytest"},
             "tool_response": {"exit_code": 0, "stdout": "5 passed"}},
            agent="dev",
        )
        assert len([e for e in hook._events if e["type"] == "achieved"]) == 1

        # Clear it via negative trigger
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "make build"},
             "tool_response": {"exit_code": 1, "stdout": "Exception: build failed"}},
            agent="dev",
        )
        # Achievement event should be removed from persisted events
        assert len([e for e in hook._events if e["type"] == "achieved"]) == 0

    def test_clears_reblocks_gated_command(self, tmp_path):
        """Negative trigger re-blocks a command that was previously unlocked."""
        triggers = [
            {"tool": "Bash", "command_pattern": "pytest", "exit_code": 0, "marks": "tests_passing"},
            {"tool": "Bash", "output_contains": "FAIL", "clears": "tests_passing"},
        ]
        norms = [{
            "role": "agent",
            "type": "required_before",
            "command_pattern": "git commit",
            "requires": "tests_passing",
        }]
        hook = self._make_hook(tmp_path, triggers, norms=norms)

        # Unlock gate
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "pytest"},
             "tool_response": {"exit_code": 0, "stdout": "ok"}},
            agent="dev",
        )
        r = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            agent="dev",
        )
        assert r["decision"] == "approve"

        # Negative trigger clears it
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "npm run lint"},
             "tool_response": {"exit_code": 1, "stdout": "FAIL: lint errors"}},
            agent="dev",
        )

        # Gate re-blocked
        r = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            agent="dev",
        )
        assert r["decision"] == "block"

    def test_clears_noop_if_not_achieved(self, tmp_path):
        """Clearing a non-existent achievement is a no-op."""
        triggers = [
            {"tool": "Bash", "output_contains": "FAIL", "clears": "tests_passing"},
        ]
        hook = self._make_hook(tmp_path, triggers)
        hook.post_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "make"},
             "tool_response": {"exit_code": 1, "stdout": "FAIL"}},
            agent="dev",
        )
        # Should not crash, no achieved events to remove
        assert len([e for e in hook._events if e["type"] == "achieved"]) == 0


class TestGovernanceCommandBlocking:
    """Mutating aorta commands are blocked; read-only ones are allowed."""

    def test_aorta_reset_blocked(self, hook):
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "aorta reset --org-spec .aorta/spec.yaml"}},
            agent="dev",
        )
        assert result["decision"] == "block"
        assert "governance commands" in result["reason"]

    def test_aorta_init_blocked(self, hook):
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "aorta init --template safe-agent"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_aorta_allow_once_blocked(self, hook):
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "aorta allow-once .env"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_aorta_status_allowed(self, hook):
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "aorta status --org-spec .aorta/spec.yaml"}},
            agent="dev",
        )
        assert result["decision"] == "approve"

    def test_aorta_permissions_allowed(self, hook):
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "aorta permissions --org-spec .aorta/spec.yaml"}},
            agent="dev",
        )
        assert result["decision"] == "approve"

    def test_aorta_explain_allowed(self, hook):
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "aorta explain --org-spec .aorta/spec.yaml --tool Write --path x.py"}},
            agent="dev",
        )
        assert result["decision"] == "approve"

    def test_aorta_validate_allowed(self, hook):
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "aorta validate .aorta/spec.yaml"}},
            agent="dev",
        )
        assert result["decision"] == "approve"

    def test_aorta_doctor_allowed(self, hook):
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "aorta doctor"}},
            agent="dev",
        )
        assert result["decision"] == "approve"


class TestSystemPromptInjection:
    """Tests for system prompt generation from obligations."""

    def test_no_obligation_text_without_obligations(self, hook):
        text = hook.get_system_prompt_injection("dev")
        if text is not None:
            assert "obliged" not in text.lower() or "obligation" not in text.lower()

    def test_injection_for_unknown_agent(self, hook):
        text = hook.get_system_prompt_injection("unknown")
        assert text is None


class TestClearTransientState:
    """Verify reinit clears exceptions and soft block cache."""

    def test_clear_transient_state(self, tmp_path):
        spec_dict = {
            "organization": "clear_test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": [{
                "role": "agent",
                "type": "protected",
                "paths": [".env"],
            }],
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        state_path = tmp_path / "state.json"

        hook = GovernanceHook(spec_file, state_path=state_path)
        hook.register_agent("dev", "agent", scope="src/")

        # Add exception and soft block
        hook._exceptions.append({"path": ".env", "agent": "*", "ts": 0, "uses": 1})
        import time
        hook._soft_block_cache["test_key"] = time.time()
        hook._save_state()

        # Verify they're in state
        state = json.loads(state_path.read_text())
        assert len(state.get("exceptions", [])) == 1
        assert len(state.get("soft_blocks", {})) == 1

        # Clear
        hook.clear_transient_state()

        state = json.loads(state_path.read_text())
        assert state.get("exceptions", []) == []
        assert state.get("soft_blocks", {}) == {}


class TestActionableBlockMessages:
    """Verify block messages include allowed scopes."""

    def test_scope_block_includes_allowed_scopes(self, tmp_path):
        spec_dict = {
            "organization": "msg_test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": [{
                "role": "agent",
                "type": "scope",
                "paths": ["src/", "tests/"],
            }],
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent", scope="src/ tests/")

        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "README.md"}},
            agent="dev",
        )
        assert result["decision"] == "block"
        assert "src/" in result["reason"]
        assert "tests/" in result["reason"]


class TestNormalizeGitCmd:
    """Tests for _normalize_git_cmd helper."""

    def test_plain_git_commit_unchanged(self):
        assert _normalize_git_cmd("git commit -m 'x'") == "git commit -m 'x'"

    def test_strips_dash_c_path(self):
        assert _normalize_git_cmd("git -C /some/path commit -m 'x'") == "git commit -m 'x'"

    def test_strips_multiple_global_flags(self):
        result = _normalize_git_cmd("git -C /path -c user.name=x commit -m 'y'")
        assert result == "git commit -m 'y'"

    def test_strips_no_pager(self):
        assert _normalize_git_cmd("git --no-pager log") == "git log"

    def test_non_git_command_unchanged(self):
        assert _normalize_git_cmd("cp src/a.py /tmp/b.py") == "cp src/a.py /tmp/b.py"

    def test_git_push_with_dash_c(self):
        assert _normalize_git_cmd("git -C /tmp/proj push origin main") == "git push origin main"

    def test_compound_command_both_normalized(self):
        result = _normalize_git_cmd(
            "git -C /path add src/x.py && git -C /path commit -m 'feat: x'"
        )
        assert "git add" in result
        assert "git commit" in result
        assert "-C" not in result

    def test_compound_with_semicolon(self):
        result = _normalize_git_cmd("git -C /p status; git -C /p commit -m 'x'")
        assert "git status" in result
        assert "git commit" in result
