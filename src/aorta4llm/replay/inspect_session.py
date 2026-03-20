"""Inspect a Claude Code session JSONL file to understand its structure.

Usage: python -m aorta4llm.replay.inspect_session [session.jsonl]

Parses the JSONL conversation trace and prints a summary of tool calls
and their results, useful for understanding what happened in a session.
"""
import json
import sys
from pathlib import Path


def inspect(path: str | Path, max_lines: int = 0) -> None:
    """Print a summary of tool calls in a session trace."""
    with open(path) as f:
        for i, line in enumerate(f):
            if max_lines and i >= max_lines:
                break
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            msg_type = obj.get("type", "?")

            # Assistant messages with tool calls
            if msg_type == "assistant" and "message" in obj:
                content = obj["message"].get("content", [])
                for block in content:
                    if block.get("type") == "tool_use":
                        name = block["name"]
                        inp = block.get("input", {})
                        detail = ""
                        if name == "Bash":
                            detail = f' cmd="{inp.get("command", "")[:60]}"'
                        elif name in ("Write", "Edit", "Read", "Glob"):
                            detail = f' file="{inp.get("file_path", inp.get("pattern", ""))}"'
                        elif name == "Grep":
                            detail = f' pattern="{inp.get("pattern", "")[:40]}"'
                        print(f"L{i:4d} assistant tool_use: {name}{detail}")

            # Tool results
            elif msg_type == "user" and "message" in obj:
                msg = obj["message"]
                if isinstance(msg, dict):
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                err = block.get("is_error", False)
                                result_content = block.get("content", "")
                                if isinstance(result_content, str):
                                    snippet = result_content[:80]
                                elif isinstance(result_content, list):
                                    snippet = str(result_content[0])[:80] if result_content else ""
                                else:
                                    snippet = ""
                                err_flag = " ERROR" if err else ""
                                print(f"L{i:4d} tool_result:{err_flag} {snippet}")

            # Other interesting types
            elif msg_type not in (
                "assistant", "user", "queue-operation",
                "file-history-snapshot", "last-prompt",
            ):
                print(f"L{i:4d} type={msg_type}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m aorta4llm.replay.inspect_session <session.jsonl>", file=sys.stderr)
        sys.exit(1)
    inspect(sys.argv[1])


if __name__ == "__main__":
    main()
