"""Tests for governance.bash_analyzer — LLM-based Bash command analysis."""

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from aorta4llm.governance.bash_analyzer import (
    BashAnalysis, analyze_bash_command, _is_safe_command, _heuristic_analyze,
)


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

    def test_extra_safe_single_word(self):
        """User-defined safe commands are respected."""
        assert not _is_safe_command("pytest")
        assert _is_safe_command("pytest", extra_safe=frozenset(["pytest"]))

    def test_extra_safe_two_word(self):
        """Multi-word safe commands like 'git status' work."""
        assert not _is_safe_command("git status")
        assert _is_safe_command("git status", extra_safe=frozenset(["git status"]))

    def test_extra_safe_two_word_with_args(self):
        """'git status --short' matches 'git status'."""
        assert _is_safe_command("git status --short", extra_safe=frozenset(["git status"]))

    def test_extra_safe_does_not_override_redirect_check(self):
        """Redirects are always unsafe, even for allowlisted commands."""
        assert not _is_safe_command("pytest > out.txt", extra_safe=frozenset(["pytest"]))

    def test_git_dash_c_safe_command(self):
        """git -C /path status should still match 'git status' safe command."""
        assert _is_safe_command(
            "git -C /private/tmp/project status",
            extra_safe=frozenset(["git status"]),
        )

    def test_git_dash_c_safe_command_with_args(self):
        """git -C /path diff --cached should match 'git diff'."""
        assert _is_safe_command(
            "git -C /tmp/proj diff --cached",
            extra_safe=frozenset(["git diff"]),
        )


class TestHeuristicGitNormalization:
    """Tests for _SAFE_WRITE_PREFIXES with git -C."""

    def test_git_dash_c_commit_is_safe_write(self):
        """git -C /path commit should be recognized as a safe write prefix."""
        result = _heuristic_analyze("git -C /tmp/proj commit -m 'test'")
        assert result is not None
        assert "known safe command" in result.summary

    def test_git_dash_c_push_is_safe_write(self):
        result = _heuristic_analyze("git -C /tmp/proj push origin main")
        assert result is not None
        assert "known safe command" in result.summary


class TestAnalyzeBashCommand:
    """Tests with mocked claude CLI subprocess."""

    def _mock_run(self, stdout: str, returncode: int = 0):
        return MagicMock(
            stdout=stdout, stderr="", returncode=returncode,
        )

    def test_safe_command_skips_cli(self):
        """Safe commands never call claude CLI."""
        with patch("aorta4llm.governance.bash_analyzer.subprocess.run") as mock_run:
            result = analyze_bash_command("ls -la")
            mock_run.assert_not_called()
        assert result.writes == []

    @patch("aorta4llm.governance.bash_analyzer.subprocess.run")
    def test_successful_analysis(self, mock_run):
        """Commands with variable expansion bypass heuristic, reach LLM."""
        mock_run.return_value = self._mock_run(json.dumps({
            "writes": ["config/a.py"],
            "is_destructive": False,
            "summary": "copies file to config/",
        }))

        result = analyze_bash_command("cp $SRC config/a.py")

        assert result.writes == ["config/a.py"]
        assert result.is_destructive is False
        # Verify CLAUDECODE is stripped from env
        call_kwargs = mock_run.call_args
        assert "CLAUDECODE" not in call_kwargs.kwargs.get("env", {})

    @patch("aorta4llm.governance.bash_analyzer.subprocess.run")
    def test_cli_failure_fails_open(self, mock_run):
        mock_run.return_value = self._mock_run("", returncode=1)

        result = analyze_bash_command("python -c $SCRIPT")

        assert result.writes == []
        assert "failed" in result.summary

    @patch("aorta4llm.governance.bash_analyzer.subprocess.run")
    def test_malformed_json_fails_open(self, mock_run):
        mock_run.return_value = self._mock_run("not json at all")

        result = analyze_bash_command("python -c $SCRIPT")

        assert result.writes == []
        assert "failed" in result.summary

    @patch("aorta4llm.governance.bash_analyzer.subprocess.run")
    def test_timeout_fails_open(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=10)

        result = analyze_bash_command("python -c $SCRIPT")

        assert result.writes == []
        assert "timed out" in result.summary

    @patch("aorta4llm.governance.bash_analyzer.subprocess.run")
    def test_markdown_fenced_json(self, mock_run):
        """Handle LLM wrapping JSON in markdown code fences."""
        mock_run.return_value = self._mock_run(
            '```json\n{"writes": [".env"], "is_destructive": false, "summary": "writes to .env"}\n```'
        )

        # Variable expansion bypasses heuristic, reaches LLM.
        result = analyze_bash_command("echo $SECRET > .env")

        assert result.writes == [".env"]

    @patch("aorta4llm.governance.bash_analyzer.subprocess.run")
    def test_cli_not_found_fails_open(self, mock_run):
        mock_run.side_effect = FileNotFoundError("claude not found")

        result = analyze_bash_command("python -c $SCRIPT")

        assert result.writes == []
        assert "failed" in result.summary

    @patch("aorta4llm.governance.bash_analyzer.subprocess.run")
    def test_uses_haiku_model(self, mock_run):
        """Verify we call claude with --model haiku."""
        mock_run.return_value = self._mock_run(json.dumps({
            "writes": [], "is_destructive": False, "summary": "test",
        }))

        # Variable expansion bypasses heuristic, reaches LLM.
        analyze_bash_command("python -c $SCRIPT")

        args = mock_run.call_args[0][0]
        assert "--model" in args
        assert "haiku" in args


class TestHeuristicAnalysis:
    """Tests for regex-based write path extraction."""

    def test_redirect_detected(self):
        r = _heuristic_analyze("echo x > config/out.txt")
        assert r is not None
        assert "config/out.txt" in r.writes

    def test_append_redirect_detected(self):
        r = _heuristic_analyze("echo x >> log.txt")
        assert r is not None
        assert "log.txt" in r.writes

    def test_cp_detected(self):
        r = _heuristic_analyze("cp src/a.py dest/b.py")
        assert r is not None
        assert "dest/b.py" in r.writes

    def test_mv_detected(self):
        r = _heuristic_analyze("mv old.py new.py")
        assert r is not None
        assert "new.py" in r.writes

    def test_tee_detected(self):
        r = _heuristic_analyze("echo hello | tee output.txt")
        assert r is not None
        assert "output.txt" in r.writes

    def test_mkdir_detected(self):
        r = _heuristic_analyze("mkdir -p src/new_dir")
        assert r is not None
        assert "src/new_dir" in r.writes

    def test_touch_detected(self):
        r = _heuristic_analyze("touch newfile.py")
        assert r is not None
        assert "newfile.py" in r.writes

    def test_rm_detected_as_destructive(self):
        r = _heuristic_analyze("rm -rf build/")
        assert r is not None
        assert r.is_destructive
        assert "build/" in r.writes

    def test_git_commit_is_safe(self):
        r = _heuristic_analyze("git commit -m 'feat: x'")
        assert r is not None
        assert r.writes == []

    def test_git_push_is_safe(self):
        r = _heuristic_analyze("git push origin main")
        assert r is not None
        assert r.writes == []

    def test_npm_install_is_safe(self):
        r = _heuristic_analyze("npm install express")
        assert r is not None
        assert r.writes == []

    def test_variable_expansion_returns_none(self):
        """Variable expansion is ambiguous — bail to LLM."""
        assert _heuristic_analyze("cp $SRC $DEST") is None

    def test_subshell_returns_none(self):
        assert _heuristic_analyze("$(cat cmd.sh)") is None

    def test_complex_pipe_returns_none(self):
        """More than 2 pipe segments — bail to LLM."""
        assert _heuristic_analyze("cat a | sort | tee b | head") is None

    def test_simple_command_no_writes(self):
        """Simple command with no write patterns — heuristic says safe."""
        r = _heuristic_analyze("python script.py")
        assert r is not None
        assert r.writes == []

    def test_heuristic_skips_llm_for_cp(self):
        """cp is handled by heuristic, LLM not called."""
        with patch("aorta4llm.governance.bash_analyzer.subprocess.run") as mock_run:
            result = analyze_bash_command("cp src/a.py config/a.py")
            mock_run.assert_not_called()
        assert "config/a.py" in result.writes

    def test_redirect_to_dev_null_with_semicolon(self):
        """/dev/null; should not be treated as a write path."""
        r = _heuristic_analyze("ls 2>/dev/null; ls foo 2>/dev/null")
        assert r is not None
        assert r.writes == []

    def test_redirect_with_semicolon_separator(self):
        """Redirect path should not include trailing semicolons."""
        r = _heuristic_analyze("echo x > out.txt; echo y")
        assert r is not None
        assert "out.txt" in r.writes
        assert "out.txt;" not in r.writes

    def test_redirect_with_ampersand_separator(self):
        r = _heuristic_analyze("echo x > out.txt && echo done")
        assert r is not None
        assert "out.txt" in r.writes

    def test_redirect_with_pipe_separator(self):
        r = _heuristic_analyze("echo x > out.txt | cat")
        assert r is not None
        assert "out.txt" in r.writes

    def test_cp_with_semicolon(self):
        r = _heuristic_analyze("cp a.py b.py; echo done")
        assert r is not None
        assert "b.py" in r.writes
        assert "b.py;" not in r.writes
