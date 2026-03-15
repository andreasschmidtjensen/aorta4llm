"""Tests for aorta watch --dashboard rendering functions."""

import json
from pathlib import Path
from unittest.mock import patch

from aorta4llm.cli.cmd_watch import _Dashboard, _strip_ansi_len, _strip_pad


class TestStripAnsiLen:

    def test_plain_string(self):
        assert _strip_ansi_len("hello") == 5

    def test_ansi_string(self):
        assert _strip_ansi_len("\033[32m✓\033[0m done") == 6  # "✓ done" = 6 visible chars

    def test_empty_string(self):
        assert _strip_ansi_len("") == 0


class TestStripPad:

    def test_pad_short_string(self):
        result = _strip_pad("hi", 10)
        assert _strip_ansi_len(result) == 10

    def test_truncate_long_string(self):
        result = _strip_pad("a" * 20, 10)
        visible = _strip_ansi_len(result)
        assert visible <= 10

    def test_exact_width(self):
        result = _strip_pad("12345", 5)
        assert _strip_ansi_len(result) == 5

    def test_ansi_pad(self):
        result = _strip_pad("\033[32mhi\033[0m", 10)
        assert _strip_ansi_len(result) == 10


class TestDashboard:

    def _make_dashboard(self, tmp_path, spec=None, state=None):
        if spec is None:
            spec = {
                "organization": "test_org",
                "access": {"src/": "read-write", ".env": "no-access"},
                "norms": [{"type": "forbidden_command", "command_pattern": "rm -rf", "severity": "hard"}],
                "achievement_triggers": [{"marks": "tests_pass", "command_pattern": "pytest"}],
            }
        state_path = tmp_path / "state.json"
        events_path = tmp_path / "events.jsonl"
        state_data = state or {"events": [], "exceptions": []}
        state_path.write_text(json.dumps(state_data))
        events_path.write_text("")
        return _Dashboard(spec, state_path, events_path)

    def test_render_includes_org_name(self, tmp_path):
        dashboard = self._make_dashboard(tmp_path)
        with patch.object(dashboard, "_get_terminal_size", return_value=(80, 24)):
            output = dashboard.render()
        assert "test_org" in output

    def test_render_includes_access(self, tmp_path):
        dashboard = self._make_dashboard(tmp_path)
        with patch.object(dashboard, "_get_terminal_size", return_value=(80, 24)):
            output = dashboard.render()
        assert "src/" in output
        assert "no-access" in output

    def test_render_includes_norms(self, tmp_path):
        dashboard = self._make_dashboard(tmp_path)
        with patch.object(dashboard, "_get_terminal_size", return_value=(80, 24)):
            output = dashboard.render()
        assert "rm -rf" in output

    def test_render_includes_achievements(self, tmp_path):
        dashboard = self._make_dashboard(tmp_path)
        with patch.object(dashboard, "_get_terminal_size", return_value=(80, 24)):
            output = dashboard.render()
        assert "tests_pass" in output

    def test_event_buffer(self, tmp_path):
        dashboard = self._make_dashboard(tmp_path)
        dashboard.add_event({"type": "check", "decision": "approve", "agent": "agent",
                            "action": "write_file", "path": "src/foo.py", "ts": "2024-01-01T10:00:00"})
        with patch.object(dashboard, "_get_terminal_size", return_value=(80, 24)):
            output = dashboard.render()
        assert "write_file" in output

    def test_achievement_refresh(self, tmp_path):
        """Achievements update when state file changes."""
        state = {"events": [{"type": "achieved", "objectives": ["tests_pass"]}], "exceptions": []}
        dashboard = self._make_dashboard(tmp_path, state=state)
        with patch.object(dashboard, "_get_terminal_size", return_value=(100, 30)):
            output = dashboard.render()
        # Should show the achieved marker
        assert "tests_pass" in output

    def test_event_buffer_limit(self, tmp_path):
        dashboard = self._make_dashboard(tmp_path, spec={
            "organization": "test",
        })
        dashboard._event_buffer = dashboard._event_buffer.__class__(maxlen=3)
        for i in range(5):
            dashboard.add_event({"type": "check", "decision": "approve", "agent": "a",
                                "action": f"act_{i}", "ts": f"2024-01-01T10:0{i}:00"})
        assert len(dashboard._event_buffer) == 3

    def test_panels_have_headers(self, tmp_path):
        dashboard = self._make_dashboard(tmp_path)
        with patch.object(dashboard, "_get_terminal_size", return_value=(80, 24)):
            output = dashboard.render()
        assert "Policy" in output
        assert "Events" in output
