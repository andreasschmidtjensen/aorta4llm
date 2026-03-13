"""Tests for custom block messages on forbidden_command norms."""

import pytest
import yaml

from aorta4llm.governance.compiler import compile_spec_dict
from aorta4llm.governance.validator import validate_spec
from aorta4llm.integration.hooks import GovernanceHook


class TestCompilerBlockMessage:
    """Compiler emits block_message facts when message is present."""

    def test_emits_block_message_fact(self):
        spec = compile_spec_dict({
            "norms": [{
                "role": "agent",
                "type": "forbidden_command",
                "command_pattern": "grep",
                "message": "Use the Grep tool instead",
            }]
        })
        bm_facts = [f for f in spec.facts if f.startswith("block_message(")]
        assert len(bm_facts) == 1
        assert "agent" in bm_facts[0]
        assert "execute_command(Cmd)" in bm_facts[0]
        assert "str_contains(Cmd, 'grep')" in bm_facts[0]
        assert "Use the Grep tool instead" in bm_facts[0]

    def test_no_block_message_without_message_field(self):
        spec = compile_spec_dict({
            "norms": [{
                "role": "agent",
                "type": "forbidden_command",
                "command_pattern": "grep",
            }]
        })
        bm_facts = [f for f in spec.facts if f.startswith("block_message(")]
        assert len(bm_facts) == 0

    def test_block_message_with_soft_severity(self):
        spec = compile_spec_dict({
            "norms": [{
                "role": "agent",
                "type": "forbidden_command",
                "command_pattern": "rm -rf",
                "severity": "soft",
                "message": "This is dangerous",
            }]
        })
        bm_facts = [f for f in spec.facts if f.startswith("block_message(")]
        assert len(bm_facts) == 1
        # Also has soft_norm
        soft_facts = [f for f in spec.facts if f.startswith("soft_norm(")]
        assert len(soft_facts) == 1

    def test_message_with_single_quotes_escaped(self):
        spec = compile_spec_dict({
            "norms": [{
                "role": "agent",
                "type": "forbidden_command",
                "command_pattern": "curl",
                "message": "Don't use curl directly",
            }]
        })
        bm_facts = [f for f in spec.facts if f.startswith("block_message(")]
        assert len(bm_facts) == 1
        assert "Don\\'t use curl directly" in bm_facts[0]


class TestValidatorBlockMessage:
    """Validator accepts message as optional field."""

    def test_valid_with_message(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": ["x"], "capabilities": ["execute_command"]}},
            "norms": [{
                "role": "agent",
                "type": "forbidden_command",
                "command_pattern": "grep",
                "message": "Use the Grep tool instead",
            }],
        })
        assert r.valid

    def test_valid_without_message(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": ["x"], "capabilities": ["execute_command"]}},
            "norms": [{
                "role": "agent",
                "type": "forbidden_command",
                "command_pattern": "grep",
            }],
        })
        assert r.valid

    def test_invalid_message_type(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": ["x"], "capabilities": ["execute_command"]}},
            "norms": [{
                "role": "agent",
                "type": "forbidden_command",
                "command_pattern": "grep",
                "message": 123,
            }],
        })
        assert not r.valid
        assert any("message" in e for e in r.errors)


class TestEngineBlockMessage:
    """Engine returns block_message in PermissionResult."""

    def test_block_message_returned_for_matching_command(self, tmp_path):
        spec_dict = {
            "organization": "test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["execute_command"],
                }
            },
            "norms": [{
                "role": "agent",
                "type": "forbidden_command",
                "command_pattern": "grep",
                "message": "Use the Grep tool instead",
            }],
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")

        result = hook._service.check_permission(
            "dev", "agent", "execute_command", {"command": "grep -r foo src/"}
        )
        assert not result.permitted
        assert result.block_message == "Use the Grep tool instead"

    def test_no_block_message_when_not_configured(self, tmp_path):
        spec_dict = {
            "organization": "test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["execute_command"],
                }
            },
            "norms": [{
                "role": "agent",
                "type": "forbidden_command",
                "command_pattern": "grep",
            }],
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")

        result = hook._service.check_permission(
            "dev", "agent", "execute_command", {"command": "grep foo"}
        )
        assert not result.permitted
        assert result.block_message is None

    def test_no_block_message_for_non_matching_command(self, tmp_path):
        spec_dict = {
            "organization": "test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["execute_command"],
                }
            },
            "norms": [{
                "role": "agent",
                "type": "forbidden_command",
                "command_pattern": "grep",
                "message": "Use the Grep tool",
            }],
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")

        result = hook._service.check_permission(
            "dev", "agent", "execute_command", {"command": "pytest"}
        )
        assert result.permitted
        assert result.block_message is None


class TestHookBlockMessage:
    """Hook includes custom message in block reason."""

    def test_hard_block_includes_custom_message(self, tmp_path):
        spec_dict = {
            "organization": "test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["execute_command"],
                }
            },
            "norms": [{
                "role": "agent",
                "type": "forbidden_command",
                "command_pattern": "grep",
                "message": "Use the Grep tool instead of running grep via Bash",
            }],
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")

        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "grep -r foo src/"}},
            agent="dev",
        )
        assert result["decision"] == "block"
        assert "Use the Grep tool instead" in result["reason"]

    def test_soft_block_includes_custom_message(self, tmp_path):
        spec_dict = {
            "organization": "test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["execute_command"],
                }
            },
            "norms": [{
                "role": "agent",
                "type": "forbidden_command",
                "command_pattern": "grep",
                "severity": "soft",
                "message": "Use the Grep tool instead",
            }],
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")

        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "grep foo"}},
            agent="dev",
        )
        assert result["decision"] == "block"
        assert "SOFT BLOCK" in result["reason"]
        assert "Use the Grep tool instead" in result["reason"]

    def test_no_hint_when_no_message(self, tmp_path):
        spec_dict = {
            "organization": "test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["execute_command"],
                }
            },
            "norms": [{
                "role": "agent",
                "type": "forbidden_command",
                "command_pattern": "grep",
            }],
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")

        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "grep foo"}},
            agent="dev",
        )
        assert result["decision"] == "block"
        assert "Hint:" not in result["reason"]

    def test_end_to_end_forbidden_command_with_message(self, tmp_path):
        """Full end-to-end: YAML with message -> compiler -> engine -> hook output."""
        yaml_content = """\
organization: test_org
roles:
  agent:
    objectives: [task_complete]
    capabilities: [read_file, write_file, execute_command]
norms:
  - type: forbidden_command
    role: agent
    command_pattern: "grep"
    severity: soft
    message: "Use the Grep tool instead of running grep via Bash"
"""
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml_content)
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")

        # Command matching the pattern gets blocked with custom message
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "grep -r foo src/"}},
            agent="dev",
        )
        assert result["decision"] == "block"
        assert "SOFT BLOCK" in result["reason"]
        assert "Use the Grep tool instead" in result["reason"]
