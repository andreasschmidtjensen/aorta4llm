"""Tests for replay trace parser."""

import json

from aorta4llm.replay.trace_parser import parse_trace, ToolEvent


def _write_jsonl(path, objects):
    with open(path, "w") as f:
        for obj in objects:
            f.write(json.dumps(obj) + "\n")


class TestParseTrace:

    def test_pairs_tool_use_with_result(self, tmp_path):
        trace = [
            {
                "type": "assistant",
                "message": {"content": [{
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Write",
                    "input": {"file_path": "src/foo.py", "content": "x = 1"},
                }]},
            },
            {
                "type": "user",
                "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "File written.",
                }]},
            },
        ]
        trace_file = tmp_path / "session.jsonl"
        _write_jsonl(trace_file, trace)
        events = parse_trace(trace_file)

        assert len(events) == 1
        assert events[0].tool_name == "Write"
        assert events[0].tool_input["file_path"] == "src/foo.py"
        assert events[0].tool_result == "File written."
        assert not events[0].is_error

    def test_error_result(self, tmp_path):
        trace = [
            {
                "type": "assistant",
                "message": {"content": [{
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "false"},
                }]},
            },
            {
                "type": "user",
                "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "exit code 1",
                    "is_error": True,
                }]},
            },
        ]
        trace_file = tmp_path / "session.jsonl"
        _write_jsonl(trace_file, trace)
        events = parse_trace(trace_file)

        assert len(events) == 1
        assert events[0].is_error

    def test_skips_non_tool_messages(self, tmp_path):
        trace = [
            {"type": "queue-operation", "data": {}},
            {"type": "file-history-snapshot", "data": {}},
            {"type": "progress", "data": {}},
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello"}]},
            },
        ]
        trace_file = tmp_path / "session.jsonl"
        _write_jsonl(trace_file, trace)
        events = parse_trace(trace_file)
        assert len(events) == 0

    def test_multiple_tool_calls(self, tmp_path):
        trace = [
            {
                "type": "assistant",
                "message": {"content": [
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.py"}},
                    {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "b.py"}},
                ]},
            },
            {
                "type": "user",
                "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "content a"},
                    {"type": "tool_result", "tool_use_id": "t2", "content": "content b"},
                ]},
            },
        ]
        trace_file = tmp_path / "session.jsonl"
        _write_jsonl(trace_file, trace)
        events = parse_trace(trace_file)

        assert len(events) == 2
        assert events[0].tool_name == "Read"
        assert events[1].tool_name == "Read"

    def test_unpaired_tool_use(self, tmp_path):
        """Tool use with no result is still returned."""
        trace = [
            {
                "type": "assistant",
                "message": {"content": [{
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Write",
                    "input": {"file_path": "x.py", "content": ""},
                }]},
            },
        ]
        trace_file = tmp_path / "session.jsonl"
        _write_jsonl(trace_file, trace)
        events = parse_trace(trace_file)

        assert len(events) == 1
        assert events[0].tool_result is None

    def test_list_content_in_result(self, tmp_path):
        """Tool result with list content extracts text blocks."""
        trace = [
            {
                "type": "assistant",
                "message": {"content": [{
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "echo hi"},
                }]},
            },
            {
                "type": "user",
                "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": [
                        {"type": "text", "text": "hi"},
                        {"type": "text", "text": "there"},
                    ],
                }]},
            },
        ]
        trace_file = tmp_path / "session.jsonl"
        _write_jsonl(trace_file, trace)
        events = parse_trace(trace_file)

        assert events[0].tool_result == "hi\nthere"

    def test_preserves_order(self, tmp_path):
        trace = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.py"}},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "a"},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t2", "name": "Write", "input": {"file_path": "b.py", "content": ""}},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t2", "content": "ok"},
            ]}},
        ]
        trace_file = tmp_path / "session.jsonl"
        _write_jsonl(trace_file, trace)
        events = parse_trace(trace_file)

        assert len(events) == 2
        assert events[0].tool_name == "Read"
        assert events[1].tool_name == "Write"
