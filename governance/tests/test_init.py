"""Tests for cli.cmd_init — aorta init scaffolding."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cli.cmd_init import list_templates, run

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "org-specs" / "templates"


class TestListTemplates:

    def test_returns_all_templates(self):
        templates = list_templates()
        names = {t["name"] for t in templates}
        assert "safe-agent" in names
        assert "test-gate" in names
        assert "review-gate" in names

    def test_templates_have_descriptions(self):
        for t in list_templates():
            assert len(t["description"]) > 0, f"{t['name']} has no description"


class TestRunInit:

    def _make_args(self, template="safe-agent", scope="src/", agent="dev"):
        class Args:
            pass
        a = Args()
        a.template = template
        a.scope = scope
        a.agent = agent
        a.list_templates = False
        return a

    def test_init_creates_org_spec(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run(self._make_args())
        assert (tmp_path / ".aorta" / "safe-agent.yaml").exists()

    def test_init_creates_settings_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run(self._make_args())
        settings = tmp_path / ".claude" / "settings.local.json"
        assert settings.exists()
        data = json.loads(settings.read_text())
        assert "PreToolUse" in data.get("hooks", {})

    def test_init_registers_agent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run(self._make_args())
        # State file should exist in ~/.aorta/
        home_aorta = Path.home() / ".aorta"
        # Find any state file that was created
        state_files = list(home_aorta.glob("state-*.json"))
        assert len(state_files) > 0

    def test_init_with_test_gate_includes_post_hook(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run(self._make_args(template="test-gate"))
        settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        assert "PostToolUse" in settings.get("hooks", {})

    def test_init_unknown_template(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            run(self._make_args(template="nonexistent"))

    def test_init_merges_existing_settings(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.local.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({"permissions": {"allow": ["Bash(*)"]}}) + "\n")

        run(self._make_args())

        data = json.loads(settings_path.read_text())
        assert "permissions" in data  # existing key preserved
        assert "hooks" in data  # new key added
