"""Tests for aorta continue — clear holds."""

import json
import yaml

from aorta4llm.cli.cmd_continue import run


def _setup(tmp_path, hold=None):
    """Create an org spec and optionally set a hold in state."""
    spec = {
        "organization": "test",
        "roles": {"agent": {"objectives": [], "capabilities": ["execute_command"]}},
    }
    aorta_dir = tmp_path / ".aorta"
    aorta_dir.mkdir()
    spec_path = aorta_dir / "test.yaml"
    spec_path.write_text(yaml.dump(spec, sort_keys=False))
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.local.json").write_text("{}")

    state = {"events": [{"type": "register", "agent": "dev", "role": "agent", "scope": ""}]}
    if hold:
        state["hold"] = hold
    (aorta_dir / "state.json").write_text(json.dumps(state))
    return spec_path


class TestContinue:

    def test_clear_hold(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec_path = _setup(tmp_path, hold={"reason": "too many failures", "ts": 1234})

        class Args:
            org_spec = str(spec_path)
        run(Args())
        out = capsys.readouterr().out
        assert "Hold cleared" in out
        assert "too many failures" in out

        # Verify state file was updated
        state = json.loads((tmp_path / ".aorta" / "state.json").read_text())
        assert "hold" not in state

    def test_no_hold(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec_path = _setup(tmp_path)

        class Args:
            org_spec = str(spec_path)
        run(Args())
        out = capsys.readouterr().out
        assert "No active hold" in out
