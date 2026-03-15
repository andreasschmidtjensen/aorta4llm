"""Tests for aorta context command."""

import json
from pathlib import Path
from unittest.mock import patch

import yaml

from aorta4llm.cli.cmd_context import run, _group_access, _norm_to_plain
from aorta4llm.cli.cmd_init import _generate_description


class TestContext:

    def _make_args(self, org_spec):
        class Args:
            pass
        a = Args()
        a.org_spec = str(org_spec)
        return a

    def _setup(self, tmp_path, spec_dict, state=None):
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict, sort_keys=False))
        state_path = tmp_path / "state.json"
        state_data = state or {"events": [], "exceptions": []}
        state_path.write_text(json.dumps(state_data))
        return spec_file, state_path

    def test_access_sections(self, tmp_path, capsys):
        spec = {
            "organization": "test_org",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
            "access": {"src/": "read-write", ".env": "no-access", "config/": "read-only"},
        }
        spec_file, state_path = self._setup(tmp_path, spec)
        with patch("aorta4llm.cli.cmd_context._load_state", return_value={"events": []}):
            run(self._make_args(spec_file))
        out = capsys.readouterr().out
        assert "# Governance: test_org" in out
        assert "Read-write: src/" in out
        assert "Read-only: config/" in out
        assert "No access: .env" in out

    def test_norms_as_rules(self, tmp_path, capsys):
        spec = {
            "organization": "test_org",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
            "norms": [
                {"type": "forbidden_command", "role": "agent", "command_pattern": "git push", "severity": "soft"},
                {"type": "forbidden_command", "role": "agent", "command_pattern": "rm -rf", "severity": "hard"},
            ],
        }
        spec_file, state_path = self._setup(tmp_path, spec)
        with patch("aorta4llm.cli.cmd_context._load_state", return_value={"events": []}):
            run(self._make_args(spec_file))
        out = capsys.readouterr().out
        assert "## Rules" in out
        assert "Soft-blocked command: `git push`" in out
        assert "Blocked command: `rm -rf`" in out

    def test_achievements_shown(self, tmp_path, capsys):
        spec = {
            "organization": "test_org",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
            "achievement_triggers": [
                {"marks": "tests_pass", "command_pattern": "pytest"},
                {"marks": "lint_pass", "command_pattern": "ruff"},
            ],
        }
        state = {"events": [{"type": "achieved", "objectives": ["tests_pass"]}], "exceptions": []}
        spec_file, state_path = self._setup(tmp_path, spec, state)
        with patch("aorta4llm.cli.cmd_context._load_state", return_value=state):
            run(self._make_args(spec_file))
        out = capsys.readouterr().out
        assert "## Achievement gates" in out
        assert "[done] tests_pass" in out
        assert "[pending] lint_pass" in out

    def test_hold_displayed(self, tmp_path, capsys):
        spec = {
            "organization": "test_org",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
        }
        state = {"events": [], "exceptions": [], "hold": {"reason": "too many violations"}}
        spec_file, state_path = self._setup(tmp_path, spec, state)
        with patch("aorta4llm.cli.cmd_context._load_state", return_value=state):
            run(self._make_args(spec_file))
        out = capsys.readouterr().out
        assert "HOLD ACTIVE" in out
        assert "too many violations" in out

    def test_obligations_listed(self, tmp_path, capsys):
        spec = {
            "organization": "test_org",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
        }
        state = {
            "events": [
                {"type": "obligation_created", "agent": "agent", "role": "agent",
                 "objective": "fix_tests", "deadline": "false"},
            ],
            "exceptions": [],
        }
        spec_file, state_path = self._setup(tmp_path, spec, state)
        with patch("aorta4llm.cli.cmd_context._load_state", return_value=state):
            run(self._make_args(spec_file))
        out = capsys.readouterr().out
        assert "## Active obligations" in out
        assert "fix_tests" in out

    def test_sanctions_shown(self, tmp_path, capsys):
        spec = {
            "organization": "test_org",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
            "sanctions": [{"threshold": 3, "action": "hold"}],
        }
        spec_file, state_path = self._setup(tmp_path, spec)
        with patch("aorta4llm.cli.cmd_context._load_state", return_value={"events": []}):
            run(self._make_args(spec_file))
        out = capsys.readouterr().out
        assert "## Sanctions" in out
        assert "After 3 violations: hold" in out


class TestGenerateDescription:

    def test_basic_description(self):
        spec = {
            "access": {"src/": "read-write", ".env": "no-access"},
        }
        desc = _generate_description(spec)
        assert "scope: src/" in desc
        assert "no-access: .env" in desc

    def test_with_gates(self):
        spec = {
            "access": {"src/": "read-write"},
            "norms": [{"type": "required_before", "command_pattern": "git push", "requires": "tests_pass"}],
            "achievement_triggers": [{"marks": "tests_pass"}],
        }
        desc = _generate_description(spec)
        assert "1 gate(s)" in desc
        assert "1 achievement trigger(s)" in desc

    def test_empty_spec(self):
        desc = _generate_description({})
        assert desc == "Governance context"


class TestGroupAccess:

    def test_groups_correctly(self):
        access = {"src/": "read-write", ".env": "no-access", "config/": "read-only"}
        groups = _group_access(access)
        assert groups["read-write"] == ["src/"]
        assert groups["no-access"] == [".env"]
        assert groups["read-only"] == ["config/"]


class TestNormToPlain:

    def test_forbidden_hard(self):
        assert "Blocked command: `rm`" == _norm_to_plain(
            {"type": "forbidden_command", "command_pattern": "rm", "severity": "hard"}
        )

    def test_forbidden_soft(self):
        result = _norm_to_plain(
            {"type": "forbidden_command", "command_pattern": "git push", "severity": "soft"}
        )
        assert "Soft-blocked" in result
        assert "ask user" in result

    def test_required_before(self):
        result = _norm_to_plain(
            {"type": "required_before", "command_pattern": "git push", "requires": "tests_pass"}
        )
        assert "tests_pass" in result
        assert "git push" in result
