"""Regression: required_before should only block commands matching the pattern."""

import json
import yaml
from aorta4llm.integration.hooks import GovernanceHook


def test_required_before_only_blocks_matching_commands(tmp_path):
    """required_before with command_pattern='git commit' should NOT block 'uv run pytest'."""
    spec_dict = {
        "organization": "test",
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
    }
    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(yaml.dump(spec_dict))
    hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
    hook.register_agent("dev", "agent")

    # git commit should be blocked (no tests_passing yet)
    r = hook.pre_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
        agent="dev",
    )
    assert r["decision"] == "block", f"Expected block for git commit, got {r}"

    # uv run pytest should NOT be blocked
    r = hook.pre_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "uv run pytest tests/"}},
        agent="dev",
    )
    assert r["decision"] == "approve", f"Expected approve for pytest, got {r}"

    # echo hello should NOT be blocked
    r = hook.pre_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "echo hello"}},
        agent="dev",
    )
    assert r["decision"] == "approve", f"Expected approve for echo, got {r}"


def test_required_before_mirrors_safe_agent_config(tmp_path):
    """Exact mirror of the safe-agent config with two required_before norms."""
    spec_dict = {
        "organization": "safe_agent",
        "bash_analysis": True,
        "safe_commands": ["git diff", "git log", "git status", "npm test", "pytest"],
        "roles": {
            "agent": {
                "objectives": ["tests_passing"],
                "capabilities": ["execute_command", "read_file", "write_file"],
            }
        },
        "access": {
            "src/": "read-write",
            "tests/": "read-write",
            "docs/": "read-write",
        },
        "norms": [
            {
                "role": "agent",
                "type": "required_before",
                "blocks": "execute_command",
                "command_pattern": "git commit",
                "requires": "tests_passing",
            },
            {
                "role": "agent",
                "type": "required_before",
                "blocks": "execute_command",
                "command_pattern": "git push",
                "requires": "tests_passing",
            },
        ],
        "achievement_triggers": [{
            "tool": "Bash",
            "command_pattern": "pytest|python -m pytest|uv run pytest",
            "exit_code": 0,
            "marks": "tests_passing",
            "reset_on_file_change": True,
        }],
    }
    spec_file = tmp_path / ".aorta" / "safe-agent.yaml"
    spec_file.parent.mkdir(parents=True)
    spec_file.write_text(yaml.dump(spec_dict, sort_keys=False))
    # Create .claude dir for settings
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.local.json").write_text("{}")

    hook = GovernanceHook(spec_file, state_path=tmp_path / ".aorta" / "state.json")
    hook.register_agent("agent", "agent", "src/ tests/ docs/")

    # git commit: blocked
    r = hook.pre_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
        agent="agent",
    )
    assert r["decision"] == "block"

    # git push: blocked
    r = hook.pre_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "git push origin main"}},
        agent="agent",
    )
    assert r["decision"] == "block"

    # uv run pytest: NOT blocked
    r = hook.pre_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "uv run pytest tests/"}},
        agent="agent",
    )
    assert r["decision"] == "approve", f"pytest blocked: {r}"

    # echo: NOT blocked
    r = hook.pre_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "echo hello"}},
        agent="agent",
    )
    assert r["decision"] == "approve", f"echo blocked: {r}"

    # cat (read-only): NOT blocked
    r = hook.pre_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "cat src/main.py"}},
        agent="agent",
    )
    assert r["decision"] == "approve", f"cat blocked: {r}"


def test_required_before_after_reset_on_file_change(tmp_path):
    """After reset_on_file_change clears tests_passing, only git commit/push should be blocked."""
    spec_dict = {
        "organization": "test",
        "roles": {
            "agent": {
                "objectives": ["tests_passing"],
                "capabilities": ["read_file", "write_file", "execute_command"],
            }
        },
        "norms": [
            {
                "role": "agent",
                "type": "required_before",
                "command_pattern": "git commit",
                "requires": "tests_passing",
            },
            {
                "role": "agent",
                "type": "required_before",
                "command_pattern": "git push",
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
    hook.register_agent("dev", "agent")

    # Achieve tests_passing
    hook.post_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "pytest"},
         "tool_response": {"exit_code": 0}},
        agent="dev",
    )

    # Write a file -> clears tests_passing
    hook.pre_tool_use(
        {"tool_name": "Write", "tool_input": {"file_path": "src/foo.py"}},
        agent="dev",
    )

    # Now tests_passing is cleared. pytest should still be allowed.
    r = hook.pre_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "uv run pytest tests/"}},
        agent="dev",
    )
    assert r["decision"] == "approve", f"pytest blocked after file write: {r}"

    # git commit should be blocked
    r = hook.pre_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
        agent="dev",
    )
    assert r["decision"] == "block"
