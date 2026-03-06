"""Tests for aorta explain — norm relevance checking."""

from cli.cmd_explain import _check_norm_relevance


class TestCheckNormRelevance:

    def test_scope_match_outside(self):
        norm = {"type": "scope", "role": "agent", "paths": ["src/"]}
        result = _check_norm_relevance(norm, "agent", "write_file", {"path": "config/x.py"})
        assert result["status"] == "match"

    def test_scope_no_match_inside(self):
        norm = {"type": "scope", "role": "agent", "paths": ["src/"]}
        result = _check_norm_relevance(norm, "agent", "write_file", {"path": "src/app.py"})
        assert result["status"] == "no_match"

    def test_scope_skip_for_read(self):
        norm = {"type": "scope", "role": "agent", "paths": ["src/"]}
        result = _check_norm_relevance(norm, "agent", "read_file", {"path": "config/x.py"})
        assert result["status"] == "skip"

    def test_scope_skip_wrong_role(self):
        norm = {"type": "scope", "role": "reviewer", "paths": ["src/"]}
        result = _check_norm_relevance(norm, "agent", "write_file", {"path": "config/x.py"})
        assert result["status"] == "skip"

    def test_protected_match(self):
        norm = {"type": "protected", "role": "agent", "paths": [".env"]}
        result = _check_norm_relevance(norm, "agent", "read_file", {"path": ".env"})
        assert result["status"] == "match"

    def test_protected_no_match(self):
        norm = {"type": "protected", "role": "agent", "paths": [".env"]}
        result = _check_norm_relevance(norm, "agent", "read_file", {"path": "src/app.py"})
        assert result["status"] == "no_match"

    def test_forbidden_command_match(self):
        norm = {"type": "forbidden_command", "role": "agent", "command_pattern": "git push"}
        result = _check_norm_relevance(norm, "agent", "execute_command", {"command": "git push origin main"})
        assert result["status"] == "match"

    def test_forbidden_command_no_match(self):
        norm = {"type": "forbidden_command", "role": "agent", "command_pattern": "git push"}
        result = _check_norm_relevance(norm, "agent", "execute_command", {"command": "git status"})
        assert result["status"] == "no_match"

    def test_required_before_match(self):
        norm = {"type": "required_before", "role": "agent", "command_pattern": "git commit", "requires": "tests_passing"}
        result = _check_norm_relevance(norm, "agent", "execute_command", {"command": "git commit -m 'x'"})
        assert result["status"] == "match"
