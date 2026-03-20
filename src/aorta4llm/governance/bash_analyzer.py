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


def _normalize_git_flags(command: str) -> str:
    """Strip git global options (like -C <path>) for safe command matching.

    'git -C /path status' -> 'git status'
    """
    if "git " not in command:
        return command
    return re.sub(
        r"\bgit"
        r"(\s+(?:-[Cc]\s+\S+|--(?:git-dir|work-tree|namespace)(?:=|\s+)\S+"
        r"|--no-pager|--bare|--no-replace-objects|--literal-pathspecs"
        r"|--no-optional-locks))+",
        "git",
        command,
    )


def _is_safe_command(command: str, extra_safe: frozenset[str] | None = None) -> bool:
    """Check if command is obviously read-only (no LLM needed)."""
    stripped = command.strip()
    if not stripped:
        return True

    # If it contains redirects, pipes to file, or semicolons, analyze it.
    if re.search(r"[>|;`$&]", stripped):
        return False

    # Normalize git flags before tokenizing (git -C /path status -> git status).
    stripped = _normalize_git_flags(stripped)

    # Get first token.
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return False

    if not tokens:
        return True

    all_safe = _SAFE_COMMANDS | (extra_safe or frozenset())
    first = tokens[0].rsplit("/", 1)[-1]  # basename

    # Check multi-word commands (e.g. "git status", "npm test").
    if len(tokens) >= 2:
        two_word = f"{first} {tokens[1]}"
        if two_word in all_safe:
            return True

    return first in all_safe


# Regex patterns for heuristic write-path extraction.
# Path capture uses [^\s;|&)]+ to exclude shell metacharacters.
_PATH = r"[^\s;|&)]+"
_REDIRECT_RE = re.compile(r">{1,2}\s*(" + _PATH + r")")
_TEE_RE = re.compile(r"\btee\s+(?:-a\s+)?(" + _PATH + r")")
_CP_RE = re.compile(r"\bcp\s+(?:-\w+\s+)*\S+\s+(" + _PATH + r")")
_MV_RE = re.compile(r"\bmv\s+(?:-\w+\s+)*(" + _PATH + r")\s+(" + _PATH + r")")
_RM_RE = re.compile(r"\brm\s+(?:-\w+\s+)*(.+)")
_MKDIR_RE = re.compile(r"\bmkdir\s+(?:-\w+\s+)*(" + _PATH + r")")
_TOUCH_RE = re.compile(r"\btouch\s+(" + _PATH + r")")

# Commands that look like writes but don't create user-visible file changes.
_SAFE_WRITE_PREFIXES = [
    "git add", "git commit", "git push", "git stash", "git tag",
    "npm install", "pip install", "uv pip install", "uv add",
]


def _heuristic_analyze(command: str) -> BashAnalysis | None:
    """Try to extract write paths from common shell patterns.

    Returns BashAnalysis if confident, None if ambiguous (needs LLM).
    """
    stripped = command.strip()

    # Known commands that don't produce user file writes.
    # Normalize git flags so 'git -C /path commit' matches 'git commit'.
    normalized = _normalize_git_flags(stripped)
    for prefix in _SAFE_WRITE_PREFIXES:
        if normalized.startswith(prefix):
            return BashAnalysis(summary=f"known safe command: {prefix}")

    # Variable expansion / subshells — ambiguous, bail to LLM.
    if re.search(r"[$`]|\$\(", stripped):
        return None

    # Too many pipes — complex, bail to LLM.
    # Strip quoted strings first so '|' inside quotes isn't counted.
    unquoted = re.sub(r'"[^"]*"|\'[^\']*\'', '', stripped)
    segments = unquoted.split("|")
    if len(segments) > 2:
        return None

    writes: list[str] = []
    is_destructive = False

    for m in _REDIRECT_RE.finditer(stripped):
        writes.append(m.group(1))
    for m in _TEE_RE.finditer(stripped):
        writes.append(m.group(1))
    for m in _CP_RE.finditer(stripped):
        writes.append(m.group(1))
    for m in _MV_RE.finditer(stripped):
        is_destructive = True  # mv removes the source file
        writes.append(m.group(1))  # source (removed)
        writes.append(m.group(2))  # destination
    for m in _MKDIR_RE.finditer(stripped):
        writes.append(m.group(1))
    for m in _TOUCH_RE.finditer(stripped):
        writes.append(m.group(1))

    rm_match = _RM_RE.search(stripped)
    if rm_match:
        is_destructive = True
        try:
            rm_paths = shlex.split(rm_match.group(1))
            writes.extend(p for p in rm_paths if not p.startswith("-"))
        except ValueError:
            return None  # Can't parse — ambiguous.

    # Filter out /dev/null — not a real file write.
    raw_count = len(writes)
    writes = [w for w in writes if w != "/dev/null"]

    if writes:
        return BashAnalysis(
            writes=writes,
            is_destructive=is_destructive,
            summary=f"heuristic: {len(writes)} write path(s) detected",
        )

    # If we found redirects but they were all /dev/null, that's read-only.
    if raw_count > 0:
        return BashAnalysis(summary="heuristic: no write patterns detected (only /dev/null)")

    # No writes detected and no complex syntax — probably read-only.
    if len(segments) <= 2 and not re.search(r"[>;]", stripped):
        return BashAnalysis(summary="heuristic: no write patterns detected")

    # Ambiguous — fall through to LLM.
    return None


def analyze_bash_command(
    command: str, extra_safe: frozenset[str] | None = None,
) -> BashAnalysis:
    """Analyze a bash command using Claude CLI with Haiku.

    Returns structured output describing file writes and destructiveness.
    Fails open: returns permissive BashAnalysis on any error.
    """
    if _is_safe_command(command, extra_safe):
        return BashAnalysis(summary="safe read-only command")

    # Try heuristic analysis (instant, no LLM call).
    heuristic = _heuristic_analyze(command)
    if heuristic is not None:
        return heuristic

    # Fall through to LLM for ambiguous commands.
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
