"""Tests for CLI norm management commands (protect, readonly, forbid, require, remove-norm, access)."""

import json
import yaml
import pytest
from pathlib import Path
from unittest.mock import patch


def _make_spec(tmp_path, norms=None, access=None):
    """Create a minimal org spec and .aorta/ structure."""
    aorta_dir = tmp_path / ".aorta"
    aorta_dir.mkdir()
    spec = {
        "organization": "test",
        "roles": {
            "agent": {
                "objectives": ["task_complete"],
                "capabilities": ["read_file", "write_file", "execute_command"],
            }
        },
        "norms": norms or [],
    }
    if access:
        spec["access"] = access
    spec_path = aorta_dir / "test.yaml"
    spec_path.write_text(yaml.dump(spec))
    # Create .claude dir for hooks
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.local.json").write_text("{}")
    return spec_path


def _load_spec(path):
    with open(path) as f:
        return yaml.safe_load(f)


class TestProtectCommand:
    def test_sets_no_access(self, tmp_path, monkeypatch):
        spec_path = _make_spec(tmp_path)
        monkeypatch.chdir(tmp_path)

        from cli.cmd_protect import run_protect
        args = type("Args", (), {"paths": [".env", "secrets/"], "org_spec": str(spec_path), "role": "agent"})()
        run_protect(args)

        spec = _load_spec(spec_path)
        assert spec["access"][".env"] == "no-access"
        assert spec["access"]["secrets/"] == "no-access"

    def test_overwrites_existing_level(self, tmp_path, monkeypatch):
        spec_path = _make_spec(tmp_path, access={".env": "read-only"})
        monkeypatch.chdir(tmp_path)

        from cli.cmd_protect import run_protect
        args = type("Args", (), {"paths": [".env"], "org_spec": str(spec_path), "role": "agent"})()
        run_protect(args)

        spec = _load_spec(spec_path)
        assert spec["access"][".env"] == "no-access"


class TestReadonlyCommand:
    def test_sets_read_only(self, tmp_path, monkeypatch):
        spec_path = _make_spec(tmp_path)
        monkeypatch.chdir(tmp_path)

        from cli.cmd_protect import run_readonly
        args = type("Args", (), {"paths": ["config/"], "org_spec": str(spec_path), "role": "agent"})()
        run_readonly(args)

        spec = _load_spec(spec_path)
        assert spec["access"]["config/"] == "read-only"


class TestForbidCommand:
    def test_adds_forbidden_command_norm(self, tmp_path, monkeypatch):
        spec_path = _make_spec(tmp_path)
        monkeypatch.chdir(tmp_path)

        from cli.cmd_protect import run_forbid
        args = type("Args", (), {"pattern": "rm -rf", "severity": "hard", "org_spec": str(spec_path), "role": "agent"})()
        run_forbid(args)

        spec = _load_spec(spec_path)
        assert any(n["type"] == "forbidden_command" and n["command_pattern"] == "rm -rf" for n in spec["norms"])

    def test_soft_severity_stored(self, tmp_path, monkeypatch):
        spec_path = _make_spec(tmp_path)
        monkeypatch.chdir(tmp_path)

        from cli.cmd_protect import run_forbid
        args = type("Args", (), {"pattern": "git push", "severity": "soft", "org_spec": str(spec_path), "role": "agent"})()
        run_forbid(args)

        spec = _load_spec(spec_path)
        norm = next(n for n in spec["norms"] if n["type"] == "forbidden_command")
        assert norm["severity"] == "soft"


class TestRequireCommand:
    def test_adds_required_before_norm(self, tmp_path, monkeypatch):
        spec_path = _make_spec(tmp_path)
        monkeypatch.chdir(tmp_path)

        from cli.cmd_protect import run_require
        args = type("Args", (), {"achievement": "tests_passing", "before": "git commit", "org_spec": str(spec_path), "role": "agent"})()
        run_require(args)

        spec = _load_spec(spec_path)
        norm = next(n for n in spec["norms"] if n["type"] == "required_before")
        assert norm["requires"] == "tests_passing"
        assert norm["command_pattern"] == "git commit"


class TestRemoveNorm:
    def test_removes_by_index(self, tmp_path, monkeypatch):
        spec_path = _make_spec(tmp_path, norms=[
            {"type": "scope", "role": "agent", "paths": ["src/"]},
            {"type": "protected", "role": "agent", "paths": [".env"]},
            {"type": "readonly", "role": "agent", "paths": ["config/"]},
        ])
        monkeypatch.chdir(tmp_path)

        from cli.cmd_norm import run
        args = type("Args", (), {"index": 2, "org_spec": str(spec_path)})()
        run(args)

        spec = _load_spec(spec_path)
        assert len(spec["norms"]) == 2
        types = [n["type"] for n in spec["norms"]]
        assert "protected" not in types

    def test_invalid_index_exits(self, tmp_path, monkeypatch):
        spec_path = _make_spec(tmp_path, norms=[
            {"type": "scope", "role": "agent", "paths": ["src/"]},
        ])
        monkeypatch.chdir(tmp_path)

        from cli.cmd_norm import run
        args = type("Args", (), {"index": 5, "org_spec": str(spec_path)})()
        with pytest.raises(SystemExit):
            run(args)


class TestAccessCommand:
    def test_sets_access_level(self, tmp_path, monkeypatch):
        spec_path = _make_spec(tmp_path)
        monkeypatch.chdir(tmp_path)

        from cli.cmd_access import run
        args = type("Args", (), {"path": "src/", "level": "read-write", "org_spec": str(spec_path)})()
        run(args)

        spec = _load_spec(spec_path)
        assert spec["access"]["src/"] == "read-write"

    def test_updates_existing_level(self, tmp_path, monkeypatch):
        spec_path = _make_spec(tmp_path, access={"src/": "read-only"})
        monkeypatch.chdir(tmp_path)

        from cli.cmd_access import run
        args = type("Args", (), {"path": "src/", "level": "read-write", "org_spec": str(spec_path)})()
        run(args)

        spec = _load_spec(spec_path)
        assert spec["access"]["src/"] == "read-write"


class TestSpecAutoDetect:
    def test_finds_single_spec(self, tmp_path, monkeypatch):
        _make_spec(tmp_path)
        monkeypatch.chdir(tmp_path)

        from cli.spec_utils import find_org_spec
        path = find_org_spec()
        assert path.name == "test.yaml"

    def test_errors_with_no_spec(self, tmp_path, monkeypatch):
        (tmp_path / ".aorta").mkdir()
        monkeypatch.chdir(tmp_path)

        from cli.spec_utils import find_org_spec
        with pytest.raises(SystemExit, match="No org spec"):
            find_org_spec()

    def test_errors_with_multiple_specs(self, tmp_path, monkeypatch):
        aorta_dir = tmp_path / ".aorta"
        aorta_dir.mkdir()
        (aorta_dir / "a.yaml").write_text("organization: a\nroles: {}")
        (aorta_dir / "b.yaml").write_text("organization: b\nroles: {}")
        monkeypatch.chdir(tmp_path)

        from cli.spec_utils import find_org_spec
        with pytest.raises(SystemExit, match="Multiple"):
            find_org_spec()
