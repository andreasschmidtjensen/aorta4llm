"""LLM-based Bash command analysis for governance enforcement.

Uses Claude CLI (``claude --print --model haiku``) to extract file write
paths from shell commands. The governance engine then checks those paths
against existing scope/path rules.

Opt-in via ``bash_analysis: true`` in the org spec YAML.

Design:
- Fast-path: skip LLM call for single-token read-only commands.
- Fail-open: if claude CLI unavailable, timeout, or error, allow the command.
- Uses the user's existing Claude Code auth — no separate API key needed.
"""

import json
import re
import shlex
import subprocess
from dataclasses import dataclass, field

_SAFE_COMMANDS = frozenset([
    "ls", "cat", "head", "tail", "grep", "rg", "find", "pwd", "cd",
    "which", "type", "env", "printenv", "date", "whoami", "uname",
    "wc", "diff", "file", "stat", "du", "df", "tree", "less", "more",
    "man", "help", "true", "false", "test", "echo",
])

_PROMPT_TEMPLATE = (
    "Analyze this shell command and return ONLY a JSON object with fields: "
    "writes (list of file paths written to, created, moved to, or deleted — "
    "use exact paths from the command, empty list if read-only), "
    "is_destructive (bool — true if deletes files, changes permissions, etc), "
    "summary (one-line string). "
    "Command: {command}"
)

_TIMEOUT_SECONDS = 10


@dataclass
class BashAnalysis:
    """Result of analyzing a bash command."""
    writes: list[str] = field(default_factory=list)
    is_destructive: bool = False
    summary: str = ""


def _is_safe_command(command: str) -> bool:
    """Check if command is obviously read-only (no LLM needed)."""
    stripped = command.strip()
    if not stripped:
        return True

    # If it contains redirects, pipes to file, or semicolons, analyze it.
    if re.search(r"[>|;`$&]", stripped):
        return False

    # Get first token.
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return False

    if not tokens:
        return True

    first = tokens[0].rsplit("/", 1)[-1]  # basename
    return first in _SAFE_COMMANDS


def analyze_bash_command(command: str) -> BashAnalysis:
    """Analyze a bash command using Claude CLI with Haiku.

    Returns structured output describing file writes and destructiveness.
    Fails open: returns permissive BashAnalysis on any error.
    """
    if _is_safe_command(command):
        return BashAnalysis(summary="safe read-only command")

    prompt = _PROMPT_TEMPLATE.format(command=command)

    try:
        # Unset CLAUDECODE to allow nested invocation.
        import os
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        result = subprocess.run(
            ["claude", "--print", "--model", "haiku", prompt],
            capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, env=env,
        )
        if result.returncode != 0:
            return BashAnalysis(summary="claude CLI failed, allowing command")

        text = result.stdout.strip()
        # Strip markdown code fences if present.
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        return BashAnalysis(
            writes=data.get("writes", []),
            is_destructive=data.get("is_destructive", False),
            summary=data.get("summary", ""),
        )
    except subprocess.TimeoutExpired:
        return BashAnalysis(summary="analysis timed out, allowing command")
    except Exception:
        # Fail open on any error (missing CLI, parse error, etc).
        return BashAnalysis(summary="analysis failed, allowing command")
