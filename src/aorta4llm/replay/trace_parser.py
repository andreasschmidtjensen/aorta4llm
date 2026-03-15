"""Parse Claude Code session JSONL traces into structured tool events."""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolEvent:
    """A paired tool_use + tool_result from a conversation trace."""
    tool_use_id: str
    tool_name: str
    tool_input: dict
    tool_result: str | None = None
    is_error: bool = False
    line_number: int = 0
    is_sidechain: bool = False


# Message types to skip entirely — they carry no tool information.
_SKIP_TYPES = frozenset({
    "queue-operation", "file-history-snapshot", "last-prompt", "progress",
})


def parse_trace(path: str | Path) -> list[ToolEvent]:
    """Parse a session JSONL file into a list of ToolEvents.

    Pairs assistant tool_use blocks with their corresponding user tool_result
    blocks by tool_use_id. Skips non-tool messages. Detects sidechain calls
    (tool calls nested inside a tool_result).
    """
    path = Path(path)
    pending: dict[str, ToolEvent] = {}  # tool_use_id -> ToolEvent
    events: list[ToolEvent] = []
    in_tool_result = False  # tracks if we're inside a tool_result context

    with open(path) as f:
        for line_no, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = obj.get("type", "")
            if msg_type in _SKIP_TYPES:
                continue

            # Assistant messages: extract tool_use blocks
            if msg_type == "assistant" and "message" in obj:
                content = obj["message"].get("content", [])
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        te = ToolEvent(
                            tool_use_id=block["id"],
                            tool_name=block["name"],
                            tool_input=block.get("input", {}),
                            line_number=line_no,
                            is_sidechain=in_tool_result,
                        )
                        pending[te.tool_use_id] = te

            # User messages: extract tool_result blocks
            elif msg_type == "user" and "message" in obj:
                msg = obj["message"]
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content", [])
                if not isinstance(content, list):
                    continue

                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        tid = block.get("tool_use_id", "")
                        if tid in pending:
                            te = pending.pop(tid)
                            te.is_error = block.get("is_error", False)
                            # Extract text content
                            rc = block.get("content", "")
                            if isinstance(rc, list):
                                parts = []
                                for part in rc:
                                    if isinstance(part, dict) and part.get("type") == "text":
                                        parts.append(part.get("text", ""))
                                    elif isinstance(part, str):
                                        parts.append(part)
                                te.tool_result = "\n".join(parts)
                            elif isinstance(rc, str):
                                te.tool_result = rc
                            events.append(te)

    # Append any unpaired tool_use events (no result received)
    for te in pending.values():
        events.append(te)

    # Sort by line number to preserve conversation order
    events.sort(key=lambda e: e.line_number)
    return events
