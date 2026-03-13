"""Tests for cli.cmd_permissions — effective permission summary."""

import json
from pathlib import Path

import yaml

from aorta4llm.cli.cmd_permissions import run


class TestPermissions:

    def _make_args(self, org_spec, agent="agent"):
        class Args:
            pass
        a = Args()
        a.org_spec = str(org_spec)
        a.agent = agent
        return a

    def _setup_spec(self, tmp_path, spec_dict):
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        state_path = tmp_path / "state.json"
        from aorta4llm.integration.hooks import GovernanceHook
        hook = GovernanceHook(spec_file, state_path=state_path)
        hook.register_agent("agent", "agent", scope="src/")
        return spec_file

    def test_shows_access_map(self, tmp_path, capsys):
        spec_file = self._setup_spec(tmp_path, {
            "organization": "test",
            "roles": {"agent": {
                "objectives": ["task_complete"],
                "capabilities": ["read_file", "write_file", "execute_command"],
            }},
            "access": {"src/": "read-write", ".env": "no-access"},
        })
        run(self._make_args(spec_file))
        out = capsys.readouterr().out
        assert "src/" in out
        assert "read-write" in out
        assert ".env" in out
        assert "no-access" in out

    def test_shows_command_restrictions(self, tmp_path, capsys):
        spec_file = self._setup_spec(tmp_path, {
            "organization": "test",
            "roles": {"agent": {
                "objectives": ["task_complete"],
                "capabilities": ["read_file", "write_file", "execute_command"],
            }},
            "norms": [{
                "type": "forbidden_command",
                "role": "agent",
                "command_pattern": "git push",
                "severity": "soft",
            }],
        })
        run(self._make_args(spec_file))
        out = capsys.readouterr().out
        assert "'git push' [soft]" in out

    def test_unregistered_agent_exits(self, tmp_path):
        spec_file = self._setup_spec(tmp_path, {
            "organization": "test",
            "roles": {"agent": {
                "objectives": ["task_complete"],
                "capabilities": ["read_file", "write_file", "execute_command"],
            }},
        })
        import pytest
        with pytest.raises(SystemExit):
            run(self._make_args(spec_file, agent="unknown"))
