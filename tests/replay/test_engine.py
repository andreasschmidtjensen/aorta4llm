"""Tests for replay engine."""

import json

import yaml

from aorta4llm.replay.engine import ReplayEngine, _approximate_tool_response, _classify_block
from aorta4llm.replay.trace_parser import ToolEvent


class TestReplayEngine:

    def _make_spec(self, tmp_path, spec_dict=None):
        if spec_dict is None:
            spec_dict = {
                "organization": "test_org",
                "roles": {"agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }},
                "access": {"src/": "read-write", ".env": "no-access"},
            }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict, sort_keys=False))
        return spec_file

    def test_approve_in_scope_write(self, tmp_path):
        spec = self._make_spec(tmp_path)
        engine = ReplayEngine(spec, agent="agent")
        events = [
            ToolEvent(
                tool_use_id="t1",
                tool_name="Write",
                tool_input={"file_path": "src/foo.py", "content": "x = 1"},
                tool_result="File written.",
            ),
        ]
        results = engine.replay(events)
        assert len(results) == 1
        assert results[0].pre_decision == "approve"

    def test_block_out_of_scope_write(self, tmp_path):
        spec = self._make_spec(tmp_path)
        engine = ReplayEngine(spec, agent="agent")
        events = [
            ToolEvent(
                tool_use_id="t1",
                tool_name="Write",
                tool_input={"file_path": ".env", "content": "SECRET=x"},
                tool_result="File written.",
            ),
        ]
        results = engine.replay(events)
        assert len(results) == 1
        assert results[0].pre_decision == "block"
        assert results[0].block_type == "policy"

    def test_sanction_block_type(self, tmp_path):
        """Sanctions are classified as 'sanction' block type."""
        spec_dict = {
            "organization": "test_org",
            "roles": {"agent": {
                "objectives": [],
                "capabilities": ["write_file"],
            }},
            "access": {"src/": "read-write", ".env": "no-access"},
            "sanctions": [{"on_violation_count": 2, "then": [
                {"type": "hold", "message": "too many violations"},
            ]}],
        }
        spec = self._make_spec(tmp_path, spec_dict)
        engine = ReplayEngine(spec, agent="agent")
        # 2 violations should trigger sanction on the 2nd
        events = [
            ToolEvent(tool_use_id=f"t{i}", tool_name="Write",
                      tool_input={"file_path": ".env", "content": "x"},
                      tool_result="written")
            for i in range(3)
        ]
        results = engine.replay(events)
        types = [r.block_type for r in results]
        assert "policy" in types
        assert "sanction" in types
        assert "held" in types

    def test_held_block_type_after_sanction(self, tmp_path):
        """Events after a hold are classified as 'held'."""
        spec_dict = {
            "organization": "test_org",
            "roles": {"agent": {
                "objectives": [],
                "capabilities": ["write_file", "read_file"],
            }},
            "access": {"src/": "read-write", ".env": "no-access"},
            "sanctions": [{"on_violation_count": 1, "then": [
                {"type": "hold", "message": "stopped"},
            ]}],
        }
        spec = self._make_spec(tmp_path, spec_dict)
        engine = ReplayEngine(spec, agent="agent")
        events = [
            ToolEvent(tool_use_id="t1", tool_name="Write",
                      tool_input={"file_path": ".env", "content": "x"},
                      tool_result="written"),
            ToolEvent(tool_use_id="t2", tool_name="Read",
                      tool_input={"file_path": "src/foo.py"},
                      tool_result="content"),
        ]
        results = engine.replay(events)
        # First is sanction (violation triggers hold), second is held
        assert results[0].block_type == "sanction"
        assert results[1].block_type == "held"

    def test_achievement_tracking(self, tmp_path):
        spec_dict = {
            "organization": "test_org",
            "roles": {"agent": {
                "objectives": ["tests_pass"],
                "capabilities": ["execute_command"],
            }},
            "access": {"src/": "read-write"},
            "achievement_triggers": [{
                "marks": "tests_pass",
                "command_pattern": "pytest",
                "exit_code": 0,
            }],
        }
        spec = self._make_spec(tmp_path, spec_dict)
        engine = ReplayEngine(spec, agent="agent")
        events = [
            ToolEvent(
                tool_use_id="t1",
                tool_name="Bash",
                tool_input={"command": "pytest"},
                tool_result="2 passed",
                is_error=False,
            ),
        ]
        results = engine.replay(events)
        assert len(results) == 1
        # PostToolUse should have processed the achievement
        assert results[0].post_result is not None

    def test_sidechain_flag_preserved(self, tmp_path):
        spec = self._make_spec(tmp_path)
        engine = ReplayEngine(spec, agent="agent")
        events = [
            ToolEvent(
                tool_use_id="t1",
                tool_name="Read",
                tool_input={"file_path": "src/foo.py"},
                tool_result="content",
                is_sidechain=True,
            ),
        ]
        results = engine.replay(events)
        assert results[0].is_sidechain


class TestClassifyBlock:

    def test_hold(self):
        assert _classify_block("HOLD: something") == "held"

    def test_sanction(self):
        assert _classify_block("SANCTION: too many") == "sanction"

    def test_policy(self):
        assert _classify_block("path is outside scope") == "policy"

    def test_empty(self):
        assert _classify_block("") == ""


class TestApproximateToolResponse:

    def test_bash_success(self):
        event = ToolEvent(
            tool_use_id="t1", tool_name="Bash",
            tool_input={"command": "echo hi"},
            tool_result="hi", is_error=False,
        )
        resp = _approximate_tool_response(event)
        assert resp["exitCode"] == 0
        assert resp["stdout"] == "hi"

    def test_bash_error(self):
        event = ToolEvent(
            tool_use_id="t1", tool_name="Bash",
            tool_input={"command": "false"},
            tool_result="", is_error=True,
        )
        resp = _approximate_tool_response(event)
        assert resp["exitCode"] == 1

    def test_non_bash(self):
        event = ToolEvent(
            tool_use_id="t1", tool_name="Write",
            tool_input={"file_path": "x.py", "content": ""},
            tool_result="Written",
        )
        resp = _approximate_tool_response(event)
        assert "exitCode" not in resp
        assert resp["stdout"] == "Written"
