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

    def test_templates_have_descriptions(self):
        for t in list_templates():
            assert len(t["description"]) > 0, f"{t['name']} has no description"


class TestRunInit:

    def _make_args(self, template="safe-agent", scope=None):
        class Args:
            pass
        a = Args()
        a.template = template
        a.scope = scope or ["src/"]
        a.list_templates = False
        a.strict = False
        a.reinit = False
        a.dry_run = False
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

    def test_init_creates_slash_commands(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run(self._make_args())
        perms = tmp_path / ".claude" / "commands" / "aorta-permissions.md"
        status = tmp_path / ".claude" / "commands" / "aorta-status.md"
        assert perms.exists()
        assert status.exists()
        assert "aorta permissions" in perms.read_text()
        assert "aorta status" in status.read_text()

    def test_init_registers_agent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run(self._make_args())
        # State file should exist in .aorta/ (project-local)
        state_file = tmp_path / ".aorta" / "state.json"
        assert state_file.exists()

    def test_init_with_test_gate_includes_post_hook(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run(self._make_args(template="test-gate"))
        settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        assert "PostToolUse" in settings.get("hooks", {})

    def test_init_unknown_template(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            run(self._make_args(template="nonexistent"))

    def test_init_preserves_non_hook_settings(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.local.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({"permissions": {"allow": ["Bash(*)"]}}) + "\n")

        run(self._make_args())

        data = json.loads(settings_path.read_text())
        assert "permissions" in data  # existing non-hook key preserved
        assert "hooks" in data  # hooks key added

    def test_init_replaces_stale_hooks(self, tmp_path, monkeypatch):
        """Re-running init with --reinit replaces hooks (doesn't merge stale entries)."""
        monkeypatch.chdir(tmp_path)

        # First init with test-gate (has PostToolUse)
        run(self._make_args(template="test-gate"))
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        assert "PostToolUse" in data["hooks"]

        # Second init with safe-agent — has PostToolUse for sensitive content
        # warnings (Read/Glob/Grep) but NOT for Bash (no achievement triggers).
        args = self._make_args(template="safe-agent")
        args.reinit = True
        run(args)
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        assert "PostToolUse" in data["hooks"]
        post_matcher = data["hooks"]["PostToolUse"][0]["matcher"]
        assert "Bash" not in post_matcher  # test-gate's Bash trigger removed
        assert "Read" in post_matcher  # sensitive content warning active

    def test_init_multi_scope(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run(self._make_args(scope=["src/", "tests/"]))
        import yaml
        spec = yaml.safe_load((tmp_path / ".aorta" / "safe-agent.yaml").read_text())
        # Should have multi-scope access entries
        access = spec.get("access", {})
        assert access.get("src/") == "read-write"
        assert access.get("tests/") == "read-write"

    def test_init_blocks_without_reinit(self, tmp_path, monkeypatch):
        """Re-running init without --reinit exits with error."""
        monkeypatch.chdir(tmp_path)
        run(self._make_args())

        with pytest.raises(SystemExit):
            run(self._make_args())

    def test_init_preserves_non_aorta_hooks(self, tmp_path, monkeypatch):
        """Non-aorta hooks are preserved when init writes aorta hooks."""
        monkeypatch.chdir(tmp_path)
        settings_path = tmp_path / ".claude" / "settings.local.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [{
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "my-linter"}],
                }]
            }
        }) + "\n")

        run(self._make_args())

        data = json.loads(settings_path.read_text())
        pre_hooks = data["hooks"]["PreToolUse"]
        # Should have both: user's linter AND aorta hook
        commands = [h["command"] for entry in pre_hooks for h in entry.get("hooks", [])]
        assert "my-linter" in commands
        assert any("aorta hook" in c for c in commands)

    def test_init_reinit_preserves_non_aorta_hooks(self, tmp_path, monkeypatch):
        """--reinit replaces aorta hooks but keeps non-aorta hooks."""
        monkeypatch.chdir(tmp_path)
        run(self._make_args())

        # Add a non-aorta hook manually
        settings_path = tmp_path / ".claude" / "settings.local.json"
        data = json.loads(settings_path.read_text())
        data["hooks"]["PreToolUse"].append({
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "my-linter"}],
        })
        settings_path.write_text(json.dumps(data, indent=2) + "\n")

        # Reinit should keep the linter hook
        args = self._make_args()
        args.reinit = True
        run(args)

        data = json.loads(settings_path.read_text())
        commands = [h["command"] for entry in data["hooks"]["PreToolUse"]
                    for h in entry.get("hooks", [])]
        assert "my-linter" in commands
        assert any("aorta hook" in c for c in commands)

    def test_init_strict_hooks_read(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        args = self._make_args()
        args.strict = True
        run(args)
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        matcher = data["hooks"]["PreToolUse"][0]["matcher"]
        assert "Read" in matcher
        assert "Glob" in matcher
        assert "Grep" in matcher

    def test_init_dry_run_creates_nothing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        args = self._make_args()
        args.dry_run = True
        run(args)
        # No files should be created
        assert not (tmp_path / ".aorta").exists()
        assert not (tmp_path / ".claude").exists()
        # Should print dry-run output
        output = capsys.readouterr().out
        assert "Dry run" in output
        assert "Would create" in output

    def test_init_no_access_hooks_read(self, tmp_path, monkeypatch):
        """safe-agent template has no-access entries, so Read/Glob/Grep should be hooked."""
        monkeypatch.chdir(tmp_path)
        run(self._make_args())
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        matcher = data["hooks"]["PreToolUse"][0]["matcher"]
        assert "Read" in matcher

    def test_init_access_map_updated_with_scope(self, tmp_path, monkeypatch):
        """--scope should update access map read-write entries."""
        monkeypatch.chdir(tmp_path)
        run(self._make_args(scope=["lib/", "app/"]))
        import yaml
        spec = yaml.safe_load((tmp_path / ".aorta" / "safe-agent.yaml").read_text())
        access = spec.get("access", {})
        assert access.get("lib/") == "read-write"
        assert access.get("app/") == "read-write"
        # Original template's src/ should be replaced
        assert "src/" not in access

    def test_init_minimal_creates_scope_only_spec(self, tmp_path, monkeypatch):
        """--template minimal creates a bare scope-only spec with no norms."""
        monkeypatch.chdir(tmp_path)
        run(self._make_args(template="minimal", scope=["src/", "lib/"]))
        import yaml
        spec_path = tmp_path / ".aorta" / "minimal.yaml"
        assert spec_path.exists()
        spec = yaml.safe_load(spec_path.read_text())
        assert spec["organization"] == "minimal"
        assert spec.get("access") == {"src/": "read-write", "lib/": "read-write"}
        assert spec.get("norms") is None
        assert spec.get("bash_analysis") is None

    def test_init_minimal_no_read_hooks(self, tmp_path, monkeypatch):
        """Minimal template should not hook Read/Glob/Grep (no no-access entries)."""
        monkeypatch.chdir(tmp_path)
        run(self._make_args(template="minimal"))
        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        matcher = data["hooks"]["PreToolUse"][0]["matcher"]
        assert "Read" not in matcher
