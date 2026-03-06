"""Tests for norm types — forbidden_command, protected, readonly, glob patterns."""

import yaml
import pytest

from governance.service import GovernanceService
from integration.hooks import GovernanceHook


class TestForbiddenCommand:
    """Tests for forbidden_command norm type and soft blocks."""

    def _make_hook(self, tmp_path, norms, severity=None):
        for norm in norms:
            if severity and "severity" not in norm:
                norm["severity"] = severity
        spec_dict = {
            "organization": "cmd_test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": norms,
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")
        return hook

    def test_forbidden_command_blocks_matching_command(self, tmp_path):
        hook = self._make_hook(tmp_path, [{
            "type": "forbidden_command",
            "role": "agent",
            "command_pattern": "git commit",
        }])
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_forbidden_command_allows_non_matching(self, tmp_path):
        hook = self._make_hook(tmp_path, [{
            "type": "forbidden_command",
            "role": "agent",
            "command_pattern": "git commit",
        }])
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git status"}},
            agent="dev",
        )
        assert result["decision"] == "approve"

    def test_forbidden_command_blocks_substring_match(self, tmp_path):
        """Pattern matches anywhere in command, not just prefix."""
        hook = self._make_hook(tmp_path, [{
            "type": "forbidden_command",
            "role": "agent",
            "command_pattern": "git push",
        }])
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "cd repo && git push origin main"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_soft_block_first_attempt_blocks(self, tmp_path):
        hook = self._make_hook(tmp_path, [{
            "type": "forbidden_command",
            "role": "agent",
            "command_pattern": "git commit",
            "severity": "soft",
        }])
        result = hook.pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            agent="dev",
        )
        assert result["decision"] == "block"
        assert "SOFT BLOCK" in result["reason"]

    def test_soft_block_retry_approves(self, tmp_path):
        hook = self._make_hook(tmp_path, [{
            "type": "forbidden_command",
            "role": "agent",
            "command_pattern": "git commit",
            "severity": "soft",
        }])
        cmd = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}}

        # First attempt — blocked
        r1 = hook.pre_tool_use(cmd, agent="dev")
        assert r1["decision"] == "block"

        # Retry — approved (user confirmed)
        r2 = hook.pre_tool_use(cmd, agent="dev")
        assert r2["decision"] == "approve"

    def test_soft_block_retry_window_expires(self, tmp_path):
        hook = self._make_hook(tmp_path, [{
            "type": "forbidden_command",
            "role": "agent",
            "command_pattern": "git commit",
            "severity": "soft",
        }])
        cmd = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}}

        # First attempt — blocked
        hook.pre_tool_use(cmd, agent="dev")

        # Simulate window expiry
        key = hook._soft_block_key({"command": "git commit -m 'x'"})
        hook._soft_block_cache[key] -= 120  # push timestamp back

        # Retry after window — blocked again
        r2 = hook.pre_tool_use(cmd, agent="dev")
        assert r2["decision"] == "block"

    def test_hard_block_does_not_approve_on_retry(self, tmp_path):
        hook = self._make_hook(tmp_path, [{
            "type": "forbidden_command",
            "role": "agent",
            "command_pattern": "git commit",
        }])
        cmd = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}}

        r1 = hook.pre_tool_use(cmd, agent="dev")
        assert r1["decision"] == "block"
        assert "SOFT BLOCK" not in r1.get("reason", "")

        r2 = hook.pre_tool_use(cmd, agent="dev")
        assert r2["decision"] == "block"

    def test_severity_propagated_to_permission_result(self, tmp_path):
        """The engine returns severity='soft' for soft-norm prohibitions."""
        spec_dict = {
            "organization": "sev_test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": [{
                "type": "forbidden_command",
                "role": "agent",
                "command_pattern": "git push",
                "severity": "soft",
            }],
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        svc = GovernanceService(spec_file)
        svc.register_agent("dev", "agent")
        result = svc.check_permission("dev", "agent", "execute_command",
                                       {"command": "git push origin main"})
        assert not result.permitted
        assert result.severity == "soft"


class TestProtectedNorm:
    """Tests for protected norm type — blocks both reads and writes."""

    def _make_hook(self, tmp_path, paths=None):
        spec_dict = {
            "organization": "protected_test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": [{
                "role": "agent",
                "type": "protected",
                "paths": paths or [".env", "secrets/"],
            }],
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")
        return hook

    def test_read_blocked(self, tmp_path):
        hook = self._make_hook(tmp_path)
        result = hook.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": ".env"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_write_blocked(self, tmp_path):
        hook = self._make_hook(tmp_path)
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": ".env"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_prefix_match(self, tmp_path):
        hook = self._make_hook(tmp_path)
        result = hook.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": "secrets/api_key.txt"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_non_protected_path_allowed(self, tmp_path):
        hook = self._make_hook(tmp_path)
        result = hook.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": "src/app.py"}},
            agent="dev",
        )
        assert result["decision"] == "approve"


class TestGlobPatternPaths:
    """Tests for glob pattern support in readonly and protected norms."""

    def _make_hook(self, tmp_path, norms):
        spec_dict = {
            "organization": "glob_test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": norms,
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent", scope="")
        return hook

    def test_glob_readonly_blocks_matching(self, tmp_path):
        hook = self._make_hook(tmp_path, [{
            "role": "agent",
            "type": "readonly",
            "paths": ["*.key", "*.pem"],
        }])
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "server.key"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_glob_readonly_allows_non_matching(self, tmp_path):
        hook = self._make_hook(tmp_path, [{
            "role": "agent",
            "type": "readonly",
            "paths": ["*.key"],
        }])
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "src/app.py"}},
            agent="dev",
        )
        assert result["decision"] == "approve"

    def test_glob_protected_blocks_read(self, tmp_path):
        hook = self._make_hook(tmp_path, [{
            "role": "agent",
            "type": "protected",
            "paths": ["*.secret"],
        }])
        result = hook.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": "db.secret"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_double_star_glob(self, tmp_path):
        """Test **/*.key pattern."""
        hook = self._make_hook(tmp_path, [{
            "role": "agent",
            "type": "readonly",
            "paths": ["**/*.key"],
        }])
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "certs/server.key"}},
            agent="dev",
        )
        assert result["decision"] == "block"
