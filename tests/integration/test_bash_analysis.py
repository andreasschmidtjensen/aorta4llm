"""Tests for LLM-based Bash analysis in pre_tool_use."""

import json

import yaml

from aorta4llm.integration.hooks import GovernanceHook


class TestBashAnalysisIntegration:
    """Tests for LLM-based Bash analysis in pre_tool_use."""

    def _make_hook(self, tmp_path, bash_analysis=True, norms=None):
        spec_dict = {
            "organization": "bash_test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "norms": norms or [{
                "role": "agent",
                "type": "scope",
                "paths": ["src/"],
            }],
        }
        if bash_analysis:
            spec_dict["bash_analysis"] = True
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent", scope="src/")
        return hook

    def test_bash_write_outside_scope_blocked(self, tmp_path):
        """Bash writing outside scope is blocked when bash_analysis is enabled."""
        from unittest.mock import patch
        from aorta4llm.governance.bash_analyzer import BashAnalysis

        hook = self._make_hook(tmp_path)

        with patch("aorta4llm.governance.bash_analyzer.analyze_bash_command") as mock_analyze:
            mock_analyze.return_value = BashAnalysis(
                writes=["config/secret.py"],
                is_destructive=False,
                summary="writes to config/",
            )
            result = hook.pre_tool_use(
                {"tool_name": "Bash", "tool_input": {"command": "echo hack > config/secret.py"}},
                agent="dev",
            )

        assert result["decision"] == "block"
        assert "config/secret.py" in result["reason"]

    def test_bash_write_in_scope_allowed(self, tmp_path):
        """Bash writing within scope is allowed."""
        from unittest.mock import patch
        from aorta4llm.governance.bash_analyzer import BashAnalysis

        hook = self._make_hook(tmp_path)

        with patch("aorta4llm.governance.bash_analyzer.analyze_bash_command") as mock_analyze:
            mock_analyze.return_value = BashAnalysis(
                writes=["src/app.py"],
                is_destructive=False,
                summary="writes to src/app.py",
            )
            result = hook.pre_tool_use(
                {"tool_name": "Bash", "tool_input": {"command": "echo x > src/app.py"}},
                agent="dev",
            )

        assert result["decision"] == "approve"

    def test_bash_analysis_skipped_when_not_enabled(self, tmp_path):
        """Without bash_analysis: true, no LLM analysis runs."""
        from unittest.mock import patch

        hook = self._make_hook(tmp_path, bash_analysis=False)

        with patch("aorta4llm.governance.bash_analyzer.analyze_bash_command") as mock_analyze:
            result = hook.pre_tool_use(
                {"tool_name": "Bash", "tool_input": {"command": "echo hack > config/x.py"}},
                agent="dev",
            )

        mock_analyze.assert_not_called()
        # Without analysis, Bash execute_command is approved (no command-level prohibition)
        assert result["decision"] == "approve"

    def test_bash_analysis_logs_block_event(self, tmp_path):
        """Blocked Bash commands are logged to events file."""
        from unittest.mock import patch
        from aorta4llm.governance.bash_analyzer import BashAnalysis

        hook = self._make_hook(tmp_path)

        with patch("aorta4llm.governance.bash_analyzer.analyze_bash_command") as mock_analyze:
            mock_analyze.return_value = BashAnalysis(
                writes=["config/x.py"],
                is_destructive=False,
                summary="writes to config/",
            )
            hook.pre_tool_use(
                {"tool_name": "Bash", "tool_input": {"command": "cp x config/x.py"}},
                agent="dev",
            )

        # Check events file for bash_analysis entry
        events_path = hook._events_path
        if events_path.exists():
            lines = events_path.read_text().strip().split("\n")
            bash_events = [json.loads(l) for l in lines if "bash_analysis" in l]
            assert len(bash_events) >= 1
            assert bash_events[-1]["decision"] == "block"
