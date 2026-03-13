"""Tests for cmd_matches hash cleanup in block messages."""

import yaml
import pytest

from aorta4llm.governance.service import GovernanceService


class TestCmdMatchesCleanup:
    """Verify that cmd_matches_xxx helper names don't appear in block messages."""

    def _make_service(self, tmp_path):
        spec_dict = {
            "organization": "test",
            "roles": {
                "agent": {
                    "objectives": ["tests_passing", "task_complete"],
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
        svc = GovernanceService(spec_file)
        svc.register_agent("dev", "agent")
        return svc

    def test_block_reason_excludes_cmd_matches_hash(self, tmp_path):
        svc = self._make_service(tmp_path)
        result = svc.check_permission(
            "dev", "agent", "execute_command", {"command": "git commit -m 'test'"}
        )
        assert not result.permitted
        assert "cmd_matches_" not in result.reason

    def test_block_reason_shows_clean_message(self, tmp_path):
        svc = self._make_service(tmp_path)
        result = svc.check_permission(
            "dev", "agent", "execute_command", {"command": "git commit -m 'test'"}
        )
        assert not result.permitted
        assert "requires 'tests_passing' to be achieved first" in result.reason
