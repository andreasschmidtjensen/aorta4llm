"""Tests for aorta doctor command."""

import json
import yaml
import pytest
from pathlib import Path


class TestDoctorFindAortaDir:
    def test_finds_aorta_in_cwd(self, tmp_path, monkeypatch):
        (tmp_path / ".aorta").mkdir()
        monkeypatch.chdir(tmp_path)

        from aorta4llm.cli.cmd_doctor import _find_aorta_dir
        assert _find_aorta_dir() == tmp_path / ".aorta"

    def test_returns_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        from aorta4llm.cli.cmd_doctor import _find_aorta_dir
        assert _find_aorta_dir() is None


class TestDoctorRun:
    def _setup_project(self, tmp_path):
        """Create a valid aorta project structure."""
        aorta_dir = tmp_path / ".aorta"
        aorta_dir.mkdir()
        spec = {
            "organization": "test",
            "roles": {"agent": {"objectives": ["x"], "capabilities": ["read_file"]}},
            "norms": [{"type": "scope", "role": "agent", "paths": ["src/"]}],
        }
        (aorta_dir / "test.yaml").write_text(yaml.dump(spec))

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {
            "hooks": {
                "PreToolUse": [{
                    "matcher": "Write|Edit|Bash",
                    "hooks": [{"type": "command", "command": "aorta hook pre-tool-use --org-spec .aorta/test.yaml"}],
                }]
            }
        }
        (claude_dir / "settings.local.json").write_text(json.dumps(settings))

        state = {
            "events": [{"type": "register", "agent": "agent", "role": "agent", "scope": "src/"}],
        }
        (aorta_dir / "state.json").write_text(json.dumps(state))

    def test_all_checks_pass(self, tmp_path, monkeypatch, capsys):
        self._setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        from aorta4llm.cli.cmd_doctor import run
        args = type("Args", (), {})()
        run(args)

        output = capsys.readouterr().out
        assert "All checks passed" in output

    def test_missing_aorta_dir(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)

        from aorta4llm.cli.cmd_doctor import run
        args = type("Args", (), {})()
        run(args)

        output = capsys.readouterr().out
        assert "not found" in output
        assert "1 issue" in output

    def test_missing_state_file(self, tmp_path, monkeypatch, capsys):
        self._setup_project(tmp_path)
        (tmp_path / ".aorta" / "state.json").unlink()
        monkeypatch.chdir(tmp_path)

        from aorta4llm.cli.cmd_doctor import run
        args = type("Args", (), {})()
        run(args)

        output = capsys.readouterr().out
        assert "state.json not found" in output.lower() or "issue" in output
