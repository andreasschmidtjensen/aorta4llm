"""Tests for aorta template commands."""

import yaml
import pytest

from aorta4llm.cli.cmd_template import run_add, run_list, _norm_key


def _make_spec(tmp_path, spec_dict):
    aorta_dir = tmp_path / ".aorta"
    aorta_dir.mkdir(exist_ok=True)
    spec_path = aorta_dir / "test.yaml"
    spec_path.write_text(yaml.dump(spec_dict))
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(exist_ok=True)
    (claude_dir / "settings.local.json").write_text("{}")
    return spec_path


class TestNormKey:
    def test_scope_key(self):
        k = _norm_key({"type": "scope", "role": "agent", "paths": ["src/", "tests/"]})
        assert k == ("scope", "agent", ("src/", "tests/"))

    def test_forbidden_command_key(self):
        k = _norm_key({"type": "forbidden_command", "role": "agent", "command_pattern": "git push"})
        assert k == ("forbidden_command", "agent", "git push")

    def test_different_paths_different_keys(self):
        k1 = _norm_key({"type": "scope", "role": "agent", "paths": ["src/"]})
        k2 = _norm_key({"type": "scope", "role": "agent", "paths": ["lib/"]})
        assert k1 != k2


class TestTemplateAdd:
    def test_merges_norms(self, tmp_path, monkeypatch):
        spec_path = _make_spec(tmp_path, {
            "organization": "test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": [
                {"type": "scope", "role": "agent", "paths": ["src/"]},
            ],
        })
        monkeypatch.chdir(tmp_path)

        args = type("Args", (), {"template": "test-gate", "org_spec": str(spec_path)})()
        run_add(args)

        with open(spec_path) as f:
            spec = yaml.safe_load(f)

        # Should have original scope + test-gate norms (minus duplicate scope)
        types = [n["type"] for n in spec["norms"]]
        assert "required_before" in types
        assert spec.get("achievement_triggers")

    def test_skips_duplicate_norms(self, tmp_path, monkeypatch):
        spec_path = _make_spec(tmp_path, {
            "organization": "test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": [
                {"type": "required_before", "role": "agent",
                 "command_pattern": "git commit", "requires": "tests_passing"},
            ],
        })
        monkeypatch.chdir(tmp_path)

        args = type("Args", (), {"template": "test-gate", "org_spec": str(spec_path)})()
        run_add(args)

        with open(spec_path) as f:
            spec = yaml.safe_load(f)

        # The existing required_before should not be duplicated
        rb_norms = [n for n in spec["norms"] if n["type"] == "required_before" and n.get("command_pattern") == "git commit"]
        assert len(rb_norms) == 1

    def test_merges_roles(self, tmp_path, monkeypatch):
        spec_path = _make_spec(tmp_path, {
            "organization": "test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file"],
                }
            },
            "norms": [],
        })
        monkeypatch.chdir(tmp_path)

        args = type("Args", (), {"template": "test-gate", "org_spec": str(spec_path)})()
        run_add(args)

        with open(spec_path) as f:
            spec = yaml.safe_load(f)

        caps = spec["roles"]["agent"]["capabilities"]
        assert "execute_command" in caps
        assert "write_file" in caps

    def test_unknown_template_exits(self, tmp_path, monkeypatch):
        spec_path = _make_spec(tmp_path, {
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
            "norms": [],
        })
        monkeypatch.chdir(tmp_path)

        args = type("Args", (), {"template": "nonexistent", "org_spec": str(spec_path)})()
        with pytest.raises(SystemExit):
            run_add(args)


class TestTemplateList:
    def test_template_list(self, capsys):
        run_list(type("Args", (), {})())
        output = capsys.readouterr().out
        assert "safe-agent" in output
        assert "test-gate" in output
        assert "minimal" in output
