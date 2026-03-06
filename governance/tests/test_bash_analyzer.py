"""Tests for governance.bash_analyzer — LLM-based Bash command analysis."""

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from governance.bash_analyzer import BashAnalysis, analyze_bash_command, _is_safe_command


class TestSafeCommandDetection:
    """Fast-path: skip LLM for obviously read-only commands."""

    @pytest.mark.parametrize("cmd", [
        "ls", "ls -la", "cat foo.py", "head -20 bar.txt", "grep TODO src/",
        "pwd", "whoami", "date", "echo hello",
    ])
    def test_safe_commands(self, cmd):
        assert _is_safe_command(cmd)

    @pytest.mark.parametrize("cmd", [
        "echo x > file.txt",       # redirect
        "cat foo | tee out.txt",    # pipe
        "rm -rf build/",           # not in safe list
        "cp a.py b.py",            # not in safe list
        "python script.py",        # not in safe list
        "ls; rm foo",              # semicolon
        "echo `date` > log",       # backtick
    ])
    def test_unsafe_commands(self, cmd):
        assert not _is_safe_command(cmd)

    def test_empty_command(self):
        assert _is_safe_command("")
        assert _is_safe_command("   ")


class TestAnalyzeBashCommand:
    """Tests with mocked claude CLI subprocess."""

    def _mock_run(self, stdout: str, returncode: int = 0):
        return MagicMock(
            stdout=stdout, stderr="", returncode=returncode,
        )

    def test_safe_command_skips_cli(self):
        """Safe commands never call claude CLI."""
        with patch("governance.bash_analyzer.subprocess.run") as mock_run:
            result = analyze_bash_command("ls -la")
            mock_run.assert_not_called()
        assert result.writes == []

    @patch("governance.bash_analyzer.subprocess.run")
    def test_successful_analysis(self, mock_run):
        mock_run.return_value = self._mock_run(json.dumps({
            "writes": ["config/a.py"],
            "is_destructive": False,
            "summary": "copies file to config/",
        }))

        result = analyze_bash_command("cp src/a.py config/a.py")

        assert result.writes == ["config/a.py"]
        assert result.is_destructive is False
        assert "config" in result.summary
        # Verify CLAUDECODE is stripped from env
        call_kwargs = mock_run.call_args
        assert "CLAUDECODE" not in call_kwargs.kwargs.get("env", {})

    @patch("governance.bash_analyzer.subprocess.run")
    def test_destructive_command(self, mock_run):
        mock_run.return_value = self._mock_run(json.dumps({
            "writes": ["build/"],
            "is_destructive": True,
            "summary": "recursively deletes build/",
        }))

        result = analyze_bash_command("rm -rf build/")

        assert result.is_destructive is True
        assert "build/" in result.writes

    @patch("governance.bash_analyzer.subprocess.run")
    def test_cli_failure_fails_open(self, mock_run):
        mock_run.return_value = self._mock_run("", returncode=1)

        result = analyze_bash_command("cp a.py b.py")

        assert result.writes == []
        assert "failed" in result.summary

    @patch("governance.bash_analyzer.subprocess.run")
    def test_malformed_json_fails_open(self, mock_run):
        mock_run.return_value = self._mock_run("not json at all")

        result = analyze_bash_command("cp a.py b.py")

        assert result.writes == []
        assert "failed" in result.summary

    @patch("governance.bash_analyzer.subprocess.run")
    def test_timeout_fails_open(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=10)

        result = analyze_bash_command("cp a.py b.py")

        assert result.writes == []
        assert "timed out" in result.summary

    @patch("governance.bash_analyzer.subprocess.run")
    def test_markdown_fenced_json(self, mock_run):
        """Handle LLM wrapping JSON in markdown code fences."""
        mock_run.return_value = self._mock_run(
            '```json\n{"writes": [".env"], "is_destructive": false, "summary": "writes to .env"}\n```'
        )

        result = analyze_bash_command("echo SECRET=x > .env")

        assert result.writes == [".env"]

    @patch("governance.bash_analyzer.subprocess.run")
    def test_cli_not_found_fails_open(self, mock_run):
        mock_run.side_effect = FileNotFoundError("claude not found")

        result = analyze_bash_command("cp a.py b.py")

        assert result.writes == []
        assert "failed" in result.summary

    @patch("governance.bash_analyzer.subprocess.run")
    def test_uses_haiku_model(self, mock_run):
        """Verify we call claude with --model haiku."""
        mock_run.return_value = self._mock_run(json.dumps({
            "writes": [], "is_destructive": False, "summary": "test",
        }))

        analyze_bash_command("python script.py")

        args = mock_run.call_args[0][0]
        assert "--model" in args
        assert "haiku" in args
