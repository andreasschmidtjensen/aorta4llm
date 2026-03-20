"""Claude Code hook integration for aorta4llm governance.

Provides PreToolUse and PostToolUse hook handlers that enforce
organizational norms on Claude Code tool calls.

State is persisted via event sourcing: a JSON file stores the sequence
of registrations and state changes. Each hook invocation replays events
to reconstruct the governance service state.

Usage (CLI):
    # Register an agent
    python -m integration.hooks register \\
        --org-spec .aorta/safe-agent.yaml \\
        --agent dev --role agent --scope src/

    # PreToolUse hook (reads tool context from stdin)
    echo '{"tool_name":"Write","tool_input":{"file_path":"config/x.py"}}' | \\
        python -m integration.hooks pre-tool-use \\
        --org-spec .aorta/safe-agent.yaml --agent dev

Claude Code hook configuration (.claude/settings.local.json):
    {
      "hooks": {
        "PreToolUse": [{
          "matcher": "Write|Edit|Bash",
          "command": "uv run python -m integration.hooks pre-tool-use --org-spec .aorta/safe-agent.yaml --agent dev"
        }]
      }
    }
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import yaml

from aorta4llm.governance.service import GovernanceService
from aorta4llm.integration.events import log_event

# Paths that are always protected regardless of org spec configuration.
# Prevents agents from modifying their own governance infrastructure.
PROTECTED_PATHS = (".aorta/", ".claude/")

# Allow-once exceptions expire after this many seconds (4 hours).
EXCEPTION_TTL = 4 * 60 * 60

# Mapping from Claude Code tool names to governance action names
TOOL_ACTION_MAP = {
    "Write": "write_file",
    "Edit": "write_file",
    "Read": "read_file",
    "Bash": "execute_command",
    "Glob": "read_file",
    "Grep": "read_file",
    "NotebookEdit": "write_file",
}


# Patterns that indicate an aorta CLI invocation.
_AORTA_CMD_PATTERN = re.compile(
    r"(?:^|\s|&&|\|\||;)"  # start of string or command separator
    r"(?:aorta\s|python\s+-m\s+(?:cli|integration\.hooks)\s)",
)

# Read-only aorta subcommands that agents may run.
_SAFE_AORTA_SUBCOMMANDS = frozenset({
    "status", "permissions", "explain", "validate", "dry-run", "doctor", "watch", "context", "replay",
})


def _command_is_piped(cmd: str) -> bool:
    """Check if a shell command contains a pipe operator.

    Ignores pipes inside quoted strings. This is a heuristic — it won't
    catch every edge case, but covers the common patterns like
    'pytest | tail -20' and 'cmd 2>&1 | grep error'.
    """
    in_single = False
    in_double = False
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if c == '\\' and not in_single:
            i += 2  # skip escaped character
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == '|' and not in_single and not in_double:
            # Distinguish | (pipe) from || (or)
            if i + 1 < len(cmd) and cmd[i + 1] == '|':
                i += 2  # skip ||
                continue
            return True
        i += 1
    return False


def _normalize_git_cmd(cmd: str) -> str:
    """Strip git global options (like -C <path>) for command pattern matching.

    Claude Code often uses 'git -C /absolute/path commit ...' which bypasses
    substring matching on 'git commit'. This normalizes to 'git commit ...'.

    Handles compound commands: 'git -C /path add && git -C /path commit'
    becomes 'git add && git commit'.
    """
    if "git " not in cmd:
        return cmd
    # Use \b word boundary to match 'git' anywhere in compound commands
    # (after &&, ||, ;, or at start). Strips all global flags in one pass.
    normalized = re.sub(
        r"\bgit"
        r"(\s+(?:-[Cc]\s+\S+|--(?:git-dir|work-tree|namespace)(?:=|\s+)\S+"
        r"|--no-pager|--bare|--no-replace-objects|--literal-pathspecs"
        r"|--no-optional-locks))+",
        "git",
        cmd,
    )
    return normalized


def _make_relative(path: str, org_spec_path: Path,
                   project_cwd: str | None = None,
                   context_cwd: str = "") -> str:
    """Normalize an absolute path to be relative to the project root."""
    if not path or not os.path.isabs(path):
        return path
    detected_root = _detect_project_root(org_spec_path)
    for candidate in [project_cwd, context_cwd, detected_root, os.getcwd()]:
        if not candidate:
            continue
        prefix = str(candidate).rstrip("/") + "/"
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _is_governance_command(cmd: str) -> bool:
    """Check if a bash command invokes a mutating aorta governance tool.

    Read-only commands (status, permissions, explain, etc.) are allowed.
    Mutating commands (reset, init, allow-once, protect, etc.) are blocked.
    """
    if not _AORTA_CMD_PATTERN.search(cmd):
        return False
    # Extract the subcommand after 'aorta'.
    m = re.search(r"aorta\s+(\S+)", cmd)
    if m and m.group(1) in _SAFE_AORTA_SUBCOMMANDS:
        return False
    return True


def _truncate_reason(reason: str, max_len: int = 200) -> str:
    """Truncate long block reasons (e.g. multiline heredoc commands)."""
    # Collapse newlines inside the reason to keep it on one conceptual line.
    collapsed = reason.replace("\n", " ").replace("  ", " ")
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[:max_len] + "..."


def _shorten_block_reason(reason: str) -> str:
    """Extract the human-readable part of a block reason, dropping the command echo.

    Engine reasons look like:
        "execute_command(git commit -m '...') blocked for dev (role: agent): command contains 'git commit'"
    Returns: "command contains 'git commit'"

    For path-based reasons:
        "write_file(README.md) blocked for dev (role: agent): path is outside allowed scopes ['src/']"
    Returns: "path is outside allowed scopes ['src/']"
    """
    marker = "): "
    idx = reason.rfind(marker)
    if idx != -1:
        return reason[idx + len(marker):]
    return _truncate_reason(reason)


# User-facing names for governance actions.
ACTION_DISPLAY_NAMES = {
    "write_file": "Write",
    "read_file": "Read",
    "execute_command": "Bash",
}


def _display_action(action: str, path: str) -> str:
    """Format an action for user-facing messages. e.g. 'Write to README.md'."""
    name = ACTION_DISPLAY_NAMES.get(action, action)
    if path:
        return f"{name} to {path}" if action == "write_file" else f"{name} {path}"
    return name


def _format_achievement_notice(achieved: list[str]) -> str:
    """Format a list of newly achieved objectives for agent notification."""
    if len(achieved) == 1:
        return f"[ACHIEVEMENT] Unlocked: {achieved[0]}"
    return "[ACHIEVEMENT] Unlocked: " + ", ".join(achieved)


def _memory_path_prefix() -> str:
    """Compute the Claude Code memory directory for the current project.

    Claude Code encodes the cwd by replacing all non-alphanumeric chars
    with '-'. The leading '-' from the initial '/' is preserved.
    """
    encoded = re.sub(r"[^a-zA-Z0-9]", "-", os.getcwd())
    return str(Path.home() / ".claude" / "projects" / encoded / "memory")


def _is_memory_path(path: str) -> bool:
    """Check if an absolute path is inside the Claude Code memory directory."""
    prefix = _memory_path_prefix()
    return path.startswith(prefix + "/") or path == prefix


def _is_plan_path(path: str) -> bool:
    """Check if an absolute path is inside the Claude Code plans directory."""
    prefix = str(Path.home() / ".claude" / "plans")
    return path.startswith(prefix + "/") or path == prefix


def _format_piped_notice(skipped: list[str]) -> str:
    """Format a notice when piped commands skip achievement triggers."""
    marks = ", ".join(skipped)
    return (
        f"[NOTICE] Command matched trigger for {marks} but was piped — "
        f"exit code is unreliable. Re-run without piping to earn the achievement."
    )


def _default_state_path(org_spec_path: str | Path) -> Path:
    """Return .aorta/state.json relative to the org spec's directory.

    Falls back to the org spec's parent directory if no .aorta/ is found.
    Migrates from the old ~/.aorta/state-<hash>.json location if needed.
    """
    spec_dir = Path(org_spec_path).resolve().parent
    state_path = spec_dir / "state.json"

    # Migrate from old ~/.aorta/state-<hash>.json if needed.
    if not state_path.exists():
        old_path = _legacy_state_path(org_spec_path)
        if old_path.exists():
            spec_dir.mkdir(parents=True, exist_ok=True)
            old_path.rename(state_path)
            print(f"Migrated state: {old_path} → {state_path}", file=sys.stderr)

    return state_path


def _legacy_state_path(org_spec_path: str | Path) -> Path:
    """Return the old ~/.aorta/state-<hash>.json path (for migration only)."""
    digest = hashlib.sha256(str(Path(org_spec_path).resolve()).encode()).hexdigest()[:12]
    return Path.home() / ".aorta" / f"state-{digest}.json"


def _detect_project_root(org_spec_path: Path) -> str | None:
    """Auto-detect the project root by walking up from the org spec.

    Looks for .aorta/ or .git/ directories to identify the project root.
    Falls back to the parent of .aorta/ if the org spec is inside it.
    """
    resolved = org_spec_path.resolve()
    # If org spec is inside .aorta/, the project root is .aorta/'s parent
    for parent in resolved.parents:
        if parent.name == ".aorta":
            return str(parent.parent)
    # Walk up looking for .git or .aorta
    for parent in resolved.parents:
        if (parent / ".git").exists() or (parent / ".aorta").exists():
            return str(parent)
    return None


class GovernanceHook:
    """Hook handler for Claude Code integration.

    Wraps GovernanceService with event-sourced state persistence
    so that state survives across individual hook invocations.
    """

    def __init__(self, org_spec_path: str | Path, state_path: str | Path | None = None,
                 events_path: str | Path | None = None):
        self._org_spec_path = Path(org_spec_path)
        self._state_path = Path(state_path) if state_path else _default_state_path(org_spec_path)
        self._events_path = Path(events_path) if events_path else (
            self._state_path.parent / "events.jsonl"
        )
        self._service = GovernanceService(self._org_spec_path)
        extras = self._load_spec_extras(self._org_spec_path)
        self._triggers = extras[0]
        self._counts_as: list[dict] = extras[6]
        self._sanctions: list[dict] = extras[7]
        self._bash_analysis = extras[1]
        self._safe_commands = extras[2]
        self._allow_memory: bool = extras[8]
        self._no_access_patterns: frozenset[str] = extras[9]
        self._events: list[dict] = []
        self._replaying = False
        self._soft_block_cache: dict[str, float] = {}  # command_hash -> timestamp
        self._exceptions: list[dict] = []
        self._soft_block_window = extras[3]
        self._sensitive_paths = extras[4]
        self._thrashing_config: dict = extras[5]
        self._violation_count: int = 0
        self._action_ring: list[dict] = []
        self._hold: dict | None = None  # {"reason": "...", "ts": ...} or None
        self._file_write_counts: dict[str, int] = {}  # path -> count
        self._bash_command_count: int = 0  # cumulative bash commands
        self._reset_on_file_change: set[str] = {
            t["marks"] for t in self._triggers if t.get("reset_on_file_change")
        }
        self._org_spec_name = self._org_spec_path.stem
        self._replay_state()

    def _load_spec_extras(self, org_spec_path: Path) -> tuple[list[dict], bool, frozenset[str], int, frozenset[str], dict, list[dict], list[dict], bool, frozenset[str]]:
        """Load achievement_triggers, bash_analysis flag, safe_commands, soft_block_window, sensitive paths, guardrails, counts_as, sanctions, and allow_memory."""
        try:
            with open(org_spec_path) as f:
                spec = yaml.safe_load(f)
            safe_cmds = frozenset(spec.get("safe_commands", []))
            window = int(spec.get("soft_block_window", 60))
            # Paths marked read-only or no-access are sensitive.
            sensitive = frozenset(
                path for path, level in spec.get("access", {}).items()
                if level in ("read-only", "no-access")
            )
            guardrails = spec.get("guardrails", {})
            no_access_paths = set(
                path for path, level in spec.get("access", {}).items()
                if level == "no-access"
            )
            # Protected norms also block reads
            for norm in spec.get("norms", []):
                if norm.get("type") == "protected":
                    no_access_paths.update(norm.get("paths", []))
            no_access = frozenset(no_access_paths)
            return (
                spec.get("achievement_triggers", []),
                bool(spec.get("bash_analysis", False)),
                safe_cmds,
                window,
                sensitive,
                guardrails,
                spec.get("counts_as", []),
                spec.get("sanctions", []),
                bool(spec.get("allow_memory", False)),
                no_access,
            )
        except Exception:
            return [], False, frozenset(), 60, frozenset(), {}, [], [], False, frozenset()

    def _replay_state(self):
        """Replay events from state file to reconstruct service state.

        Acquires a shared lock to wait for any concurrent write to finish.
        """
        import fcntl
        if not self._state_path.exists():
            return
        self._replaying = True
        lock_path = self._state_path.with_suffix(".lock")
        lock_path.touch(exist_ok=True)
        with open(lock_path) as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_SH)
            state = json.loads(self._state_path.read_text())
        self._events = state.get("events", [])
        self._soft_block_cache = state.get("soft_blocks", {})
        self._exceptions = state.get("exceptions", [])
        self._action_ring = state.get("action_ring", [])
        self._hold = state.get("hold", None)
        self._file_write_counts = state.get("file_write_counts", {})
        self._bash_command_count = state.get("bash_command_count", 0)
        self._violation_count = state.get("violation_count", 0)
        for event in self._events:
            etype = event["type"]
            if etype == "register":
                self._service.register_agent(
                    event["agent"], event["role"], event.get("scope", "")
                )
            elif etype == "achieved":
                self._service.notify_action(
                    event["agent"], event["role"],
                    achieved=event["objectives"],
                )
            elif etype == "deadline":
                self._service.notify_action(
                    event["agent"], event["role"],
                    deadlines_reached=event["deadlines"],
                )
            elif etype == "obligation_created":
                self._service.create_obligation(
                    event["agent"], event["role"],
                    event["objective"], event.get("deadline", "false"),
                )
        self._replaying = False

    def _save_state(self):
        """Persist events, soft block timestamps, exceptions, and guardrails state.

        Uses file locking to prevent concurrent hook processes from
        clobbering each other's writes (e.g. PostToolUse writing
        achievements while PreToolUse reads state for the next action).
        """
        import fcntl
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        state: dict = {
            "events": self._events,
            "soft_blocks": {
                k: v for k, v in self._soft_block_cache.items()
                if (time.time() - v) < self._soft_block_window
            },
        }
        if self._exceptions:
            state["exceptions"] = self._exceptions
        if self._action_ring:
            state["action_ring"] = self._action_ring
        if self._hold:
            state["hold"] = self._hold
        if self._file_write_counts:
            state["file_write_counts"] = self._file_write_counts
        if self._bash_command_count:
            state["bash_command_count"] = self._bash_command_count
        if self._violation_count:
            state["violation_count"] = self._violation_count
        lock_path = self._state_path.with_suffix(".lock")
        with open(lock_path, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            self._state_path.write_text(json.dumps(state, indent=2))

    def clear_transient_state(self):
        """Clear exceptions and soft block cache. Called during reinit."""
        self._soft_block_cache.clear()
        self._exceptions.clear()
        self._save_state()

    def clear_hold(self):
        """Clear the active hold and reset guardrails/sanctions counters.

        Called by `aorta continue`. Resets ring buffer, file write counts,
        and violation count so that thresholds become periodic checkpoints.
        """
        self._hold = None
        self._action_ring.clear()
        self._file_write_counts.clear()
        self._bash_command_count = 0
        self._violation_count = 0
        self._save_state()

    def register_agent(self, agent: str, role: str, scope: str = "",
                       reinit: bool = False):
        """Register an agent with a role and scope, persisting the event."""
        self._service.register_agent(agent, role, scope)
        event = {"type": "register", "agent": agent, "role": role, "scope": scope}
        if reinit:
            event["reinit"] = True
            # Replace existing registration for this agent instead of duplicating.
            self._events = [
                e for e in self._events
                if not (e.get("type") == "register" and e.get("agent") == agent)
            ]
        self._events.append(event)
        self._save_state()
        if not self._replaying:
            self._log(event)

    def create_obligation(self, agent: str, role: str, objective: str,
                          deadline: str = "false") -> None:
        """Create a runtime obligation, persisted as an event."""
        self._service.create_obligation(agent, role, objective, deadline)
        event = {
            "type": "obligation_created",
            "agent": agent, "role": role,
            "objective": objective, "deadline": deadline,
        }
        self._events.append(event)
        self._save_state()
        if not self._replaying:
            self._log(event)

    def pre_tool_use(self, context: dict, agent: str | None = None,
                     project_cwd: str | None = None,
                     quiet: bool = False) -> dict:
        """Handle PreToolUse hook.

        Args:
            context: Tool call context from Claude Code
                     {"tool_name": "Write", "tool_input": {"file_path": "..."}}
            agent: Agent ID (override context if provided)
            project_cwd: Explicit project root for path normalization.
            quiet: Suppress event logging (for introspection commands).

        Returns:
            {"decision": "approve"} or {"decision": "block", "reason": "..."}
        """
        log = self._log if not quiet else lambda _: None
        tool_name = context.get("tool_name", "")
        tool_input = context.get("tool_input", {})

        action = TOOL_ACTION_MAP.get(tool_name)
        if not action:
            return {"decision": "approve"}

        # Check for active hold — blocks all actions until user runs `aorta continue`.
        if self._hold:
            reason = (
                f"HOLD: {self._hold['reason']}\n"
                f"All actions are blocked until the hold is cleared.\n"
                f"To continue: aorta continue"
            )
            log({
                "type": "check", "agent": agent or "unknown", "role": "held",
                "action": action, "path": "",
                "decision": "block", "reason": "hold active", "severity": "hard",
            })
            return {"decision": "block", "reason": reason}

        agent_id = agent or context.get("agent_name", "default-agent")
        role = self._get_agent_role(agent_id)
        if not role:
            reason = f"agent '{agent_id}' is not registered — action denied (fail-closed)"
            log({
                "type": "check", "agent": agent_id, "role": "unknown",
                "action": action, "path": "",
                "decision": "block", "reason": reason,
            })
            return {"decision": "block", "reason": reason}

        params = {}
        raw_command = ""
        abs_path = ""  # original absolute path before relativization
        if tool_name == "Bash":
            raw_command = tool_input.get("command", "")
            # Normalize git commands for pattern matching (strips -C <path> etc.)
            params["command"] = _normalize_git_cmd(raw_command)
        else:
            abs_path = tool_input.get("file_path") or tool_input.get("path") or ""
            raw_path = _make_relative(
                abs_path, self._org_spec_path,
                project_cwd=project_cwd,
                context_cwd=context.get("cwd", ""),
            )
            if raw_path:
                params["path"] = raw_path

        # Allow memory writes when allow_memory is set.
        # Check before PROTECTED_PATHS since memory lives under ~/.claude/.
        if (self._allow_memory and action == "write_file"
                and abs_path and _is_memory_path(abs_path)):
            log({
                "type": "check", "agent": agent_id, "role": role,
                "action": action, "path": params.get("path", abs_path),
                "decision": "approve", "reason": "memory write allowed",
            })
            return {"decision": "approve"}

        # Allow Claude Code plan files — internal tool state, not user code.
        # Check before PROTECTED_PATHS since plans live under ~/.claude/.
        if (action in ("write_file", "read_file")
                and abs_path and _is_plan_path(abs_path)):
            log({
                "type": "check", "agent": agent_id, "role": role,
                "action": action, "path": params.get("path", abs_path),
                "decision": "approve", "reason": "plan file allowed",
            })
            return {"decision": "approve"}

        # Hard-block writes to governance infrastructure, regardless of org spec.
        if action == "write_file" and params.get("path"):
            for protected in PROTECTED_PATHS:
                if params["path"].startswith(protected) or params["path"] == protected.rstrip("/"):
                    reason = f"write to '{params['path']}' denied: governance infrastructure is protected"
                    log({
                        "type": "check", "agent": agent_id, "role": role,
                        "action": action, "path": params["path"],
                        "decision": "block", "reason": reason, "severity": "hard",
                    })
                    return {"decision": "block", "reason": reason}

        # Hard-block agents from running governance commands.
        if action == "execute_command" and params.get("command"):
            cmd = params["command"]
            if _is_governance_command(cmd):
                reason = "agents cannot run governance commands"
                log({
                    "type": "check", "agent": agent_id, "role": role,
                    "action": action, "path": "",
                    "decision": "block", "reason": reason, "severity": "hard",
                })
                return {"decision": "block", "reason": reason}

        # Check for allow-once exceptions before norm evaluation.
        path = params.get("path", "")
        exc_result = self._check_exception(path, agent_id, action) if path else ""
        if exc_result:
            reason = "allow-once exception" if exc_result == "consumed" else "allow-once (read, not consumed)"
            log({
                "type": "check", "agent": agent_id, "role": role,
                "action": action, "path": path,
                "decision": "approve", "reason": reason,
            })
            return {"decision": "approve"}

        result = self._service.check_permission(agent_id, role, action, params)

        if not result.permitted:
            if result.severity == "soft":
                soft_result = self._handle_soft_block(
                    result.reason, params, block_message=result.block_message)

                log({
                    "type": "check", "agent": agent_id, "role": role,
                    "action": action, "path": params.get("path", ""),
                    "decision": soft_result["decision"],
                    "reason": _truncate_reason(result.reason), "severity": "soft",
                })
                # Confirmed soft block = violation (agent proceeded despite warning).
                if soft_result["decision"] == "approve":
                    short = _shorten_block_reason(result.reason)
                    sanction_result = self._record_violation(
                        agent_id, role, action, f"soft-block confirmed: {short}")
                    if sanction_result:
                        return sanction_result
                return soft_result
            short_reason = _shorten_block_reason(result.reason)
            display = _display_action(action, path)
            user_reason = f"{display} blocked: {short_reason}"
            if result.block_message:
                user_reason += f"\n  Hint: {result.block_message}"
            # For write blocks from access-map rules (not scope violations),
            # show the allowed write scopes so the agent knows where it CAN write.
            if (action == "write_file" and path
                    and "outside allowed scopes" not in short_reason):
                scope = params.get("scope", "")
                if scope:
                    scopes = scope.split()
                    user_reason += f"\n  Allowed write scopes: {', '.join(scopes)}"
            log({
                "type": "check", "agent": agent_id, "role": role,
                "action": action, "path": params.get("path", ""),
                "decision": "block", "reason": _truncate_reason(result.reason),
                "severity": "hard",
            })
            if path:
                user_reason += f"\n  To grant a one-time exception: aorta allow-once {path}"
            # Record violation and check sanctions.
            sanction_result = self._record_violation(
                agent_id, role, action, short_reason)
            if sanction_result:
                return sanction_result
            return {"decision": "block", "reason": user_reason}

        log({
            "type": "check", "agent": agent_id, "role": role,
            "action": action, "path": params.get("path", ""),
            "decision": "approve",
            **({"command": params["command"]} if "command" in params else {}),
        })

        # Phase 2: LLM-based Bash command analysis.
        # Use raw_command (not normalized) so path extraction sees actual paths.
        if tool_name == "Bash" and self._bash_analysis and raw_command:
            from aorta4llm.governance.bash_analyzer import analyze_bash_command

            analysis = analyze_bash_command(raw_command, extra_safe=self._safe_commands)
            for write_path in analysis.writes:
                # Skip memory paths when allow_memory is set.
                if self._allow_memory and _is_memory_path(write_path):
                    continue
                # Normalize absolute paths to relative before scope checking.
                write_path = _make_relative(
                    write_path, self._org_spec_path,
                    project_cwd=project_cwd,
                    context_cwd=context.get("cwd", ""),
                )
                path_result = self._service.check_permission(
                    agent_id, role, "write_file", {"path": write_path},
                )
                if not path_result.permitted:
                    log(
                        {"type": "bash_analysis", "agent": agent_id,
                         "command": params["command"], "writes": analysis.writes,
                         "decision": "block", "reason": path_result.reason},
                    )
                    short = _shorten_block_reason(path_result.reason)
                    return {
                        "decision": "block",
                        "reason": f"Bash command writes to '{write_path}': {short}",
                    }
            if analysis.writes:
                log(
                    {"type": "bash_analysis", "agent": agent_id,
                     "command": params["command"], "writes": analysis.writes,
                     "decision": "approve"},
                )

        # Reset achievements when a file write is approved (e.g., invalidate tests_passing).
        reset_marks: list[str] = []
        if action == "write_file" and self._reset_on_file_change:
            for mark in list(self._reset_on_file_change):
                if self._service.clear_achievement(mark):
                    reset_marks.append(mark)
                    # Remove from persisted events too
                    self._events = [
                        e for e in self._events
                        if not (e.get("type") == "achieved" and mark in e.get("objectives", []))
                    ]
                    self._save_state()
                    log({"type": "achievement_reset", "agent": agent_id,
                         "mark": mark, "reason": "file changed"})
                    self._invalidate_counts_as_dependents(mark)
            self._save_state()

        result: dict = {"decision": "approve"}
        if reset_marks:
            result["_reset_notice"] = (
                "[ACHIEVEMENT RESET] " + ", ".join(reset_marks)
                + " reset (file changed) — re-run to re-achieve"
            )
        return result

    def post_tool_use(self, context: dict, agent: str | None = None) -> dict:
        """Handle PostToolUse hook.

        Checks achievement_triggers from the org spec. When a trigger matches
        the tool name, command pattern, and exit code, marks the corresponding
        achievement and notifies the governance engine.

        Also emits a sensitive content warning when a read-only or no-access
        file was successfully read (via allow-once or read-only access).
        """
        tool_name = context.get("tool_name", "")
        tool_input = context.get("tool_input", {})

        # Sensitive content warning for reads of restricted paths.
        if tool_name in ("Read", "Glob", "Grep") and self._sensitive_paths:
            raw_path = tool_input.get("file_path") or tool_input.get("path") or ""
            raw_path = _make_relative(raw_path, self._org_spec_path)
            if raw_path and self._matches_sensitive_path(raw_path):
                return {
                    "_sensitive_warning": (
                        f"[GOVERNANCE NOTICE] '{raw_path}' is marked as sensitive "
                        f"(read-only or no-access). Do NOT copy, embed, or hardcode "
                        f"specific values from this file in any code you write. Use "
                        f"environment variable lookups or placeholder values instead."
                    ),
                }

        tool_response = context.get("tool_response", {})
        # Claude Code sends camelCase "exitCode"; support both.
        exit_code = tool_response.get("exitCode", tool_response.get("exit_code", 0))

        agent_id = agent or context.get("agent_name", "default-agent")
        role = self._get_agent_role(agent_id)
        if not role:
            return {"status": "ok"}

        # Piped commands (e.g. "pytest | tail -20") have unreliable exit codes:
        # the shell reports the last command's exit code, not the first.
        # Skip exit-code-dependent triggers for piped commands.
        raw_cmd = tool_input.get("command", "")
        cmd_is_piped = _command_is_piped(raw_cmd) if tool_name == "Bash" else False

        achieved_before = self._get_achieved_set()

        achieved = []
        cleared = []
        piped_skipped: list[str] = []  # trigger marks skipped due to piping
        for trigger in self._triggers:
            if trigger.get("tool") != tool_name:
                continue
            required_exit = trigger.get("exit_code")
            if required_exit is not None:
                if cmd_is_piped:
                    # Check if command pattern would have matched
                    pattern = trigger.get("command_pattern", "")
                    if pattern:
                        cmd = _normalize_git_cmd(raw_cmd)
                        if re.search(pattern, cmd) and "marks" in trigger:
                            piped_skipped.append(trigger["marks"])
                    continue  # exit code is unreliable for piped commands
                if exit_code != required_exit:
                    continue
            pattern = trigger.get("command_pattern", "")
            if pattern:
                cmd = raw_cmd
                # Normalize git flags so 'git -C /path ...' matches patterns.
                cmd = _normalize_git_cmd(cmd)
                if not re.search(pattern, cmd):
                    continue
            # path_pattern: glob match on the file path (Write/Edit/Read).
            path_pattern = trigger.get("path_pattern", "")
            if path_pattern:
                import fnmatch
                raw_path = tool_input.get("file_path") or tool_input.get("path") or ""
                rel_path = _make_relative(raw_path, self._org_spec_path) if raw_path else ""
                if not rel_path or not fnmatch.fnmatch(rel_path, path_pattern):
                    continue
            # output_contains: regex match on tool output (stdout + stderr).
            output_pattern = trigger.get("output_contains", "")
            if output_pattern:
                output = tool_response.get("stdout", "") + tool_response.get("stderr", "")
                if not re.search(output_pattern, output):
                    continue
            # Trigger matched — either marks or clears an achievement.
            if "marks" in trigger:
                achieved.append(trigger["marks"])
            elif "clears" in trigger:
                cleared.append(trigger["clears"])

        if achieved:
            self._service.notify_action(agent_id, role, achieved=achieved)
            self._events.append({
                "type": "achieved", "agent": agent_id, "role": role,
                "objectives": achieved,
            })
            self._save_state()
            for mark in achieved:
                self._log(
                    {"type": "achieved", "agent": agent_id, "role": role, "mark": mark},
                )

        if cleared:
            for mark in cleared:
                if self._service.clear_achievement(mark):
                    self._events = [
                        e for e in self._events
                        if not (e.get("type") == "achieved" and mark in e.get("objectives", []))
                    ]
                    self._log(
                        {"type": "achievement_cleared", "agent": agent_id,
                         "role": role, "mark": mark, "reason": "negative trigger"},
                    )
                    self._invalidate_counts_as_dependents(mark)
            self._save_state()

        # Evaluate counts-as rules after any achievement changes.
        if achieved or cleared:
            self._evaluate_counts_as(agent_id, role)

        # Build achievement notice for newly achieved objectives.
        achieved_after = self._get_achieved_set()
        newly_achieved = sorted(achieved_after - achieved_before)

        # Guardrails tracking.
        result = self._check_guardrails(context, agent_id)
        if result:
            if newly_achieved:
                result["_achievement_notice"] = _format_achievement_notice(newly_achieved)
            if piped_skipped:
                result["_piped_notice"] = _format_piped_notice(piped_skipped)
            return result

        result: dict = {"status": "ok"}
        if newly_achieved:
            result["_achievement_notice"] = _format_achievement_notice(newly_achieved)
        if piped_skipped:
            result["_piped_notice"] = _format_piped_notice(piped_skipped)
        return result

    def _get_achieved_set(self) -> set[str]:
        """Build the set of currently achieved objectives from the event log."""
        achieved: set[str] = set()
        for event in self._events:
            if event["type"] == "achieved":
                achieved.update(event["objectives"])
        return achieved

    def _invalidate_counts_as_dependents(self, cleared_mark: str) -> None:
        """Clear any counts-as marks that depend on a cleared achievement."""
        if not self._counts_as:
            return
        for rule in self._counts_as:
            if cleared_mark not in rule.get("when", []):
                continue
            derived = rule.get("marks")
            if not derived:
                continue
            if self._service.clear_achievement(derived):
                self._events = [
                    e for e in self._events
                    if not (e.get("type") == "achieved" and derived in e.get("objectives", []))
                ]
                self._log({"type": "achievement_reset", "agent": "agent",
                           "mark": derived, "reason": f"dependency {cleared_mark} cleared"})
                # Recurse for cascading counts-as chains
                self._invalidate_counts_as_dependents(derived)

    def _evaluate_counts_as(self, agent_id: str, role: str) -> None:
        """Evaluate counts-as rules after achievements change.

        Loops until stable to handle cascading rules (A+B→C, C+D→E).
        """
        if not self._counts_as:
            return

        achieved = self._get_achieved_set()
        changed = True
        while changed:
            changed = False
            for rule in self._counts_as:
                when = rule.get("when", [])
                if not all(w in achieved for w in when):
                    continue

                if "marks" in rule:
                    mark = rule["marks"]
                    if mark in achieved:
                        continue  # already achieved, skip
                    # Grant the new achievement.
                    self._service.notify_action(agent_id, role, achieved=[mark])
                    self._events.append({
                        "type": "achieved", "agent": agent_id, "role": role,
                        "objectives": [mark],
                    })
                    self._log({"type": "counts_as", "agent": agent_id,
                               "role": role, "mark": mark,
                               "when": when})
                    achieved.add(mark)
                    changed = True

                if "creates_obligation" in rule:
                    ob = rule["creates_obligation"]
                    objective = ob.get("objective", "")
                    if not objective or objective in achieved:
                        continue  # already fulfilled
                    # Check if this obligation already exists in events.
                    already_exists = any(
                        e.get("type") == "obligation_created"
                        and e.get("objective") == objective
                        for e in self._events
                    )
                    if already_exists:
                        continue
                    deadline = ob.get("deadline", "false")
                    self.create_obligation(agent_id, role, objective, deadline)
                    self._log({"type": "counts_as_obligation", "agent": agent_id,
                               "role": role, "objective": objective,
                               "when": when})
                    changed = True

        self._save_state()

    def _record_violation(self, agent_id: str, role: str, action: str,
                          reason: str) -> dict | None:
        """Record a norm violation and evaluate sanctions.

        Increments the violation counter and checks if any sanction
        thresholds have been reached. Returns a sanction result dict
        if a sanction was applied (hold or obligation), None otherwise.
        """
        self._violation_count += 1
        self._log({
            "type": "violation", "agent": agent_id, "role": role,
            "action": action, "reason": reason,
            "count": self._violation_count,
        })
        self._save_state()
        return self._evaluate_sanctions(agent_id, role)

    def _evaluate_sanctions(self, agent_id: str, role: str) -> dict | None:
        """Check sanctions rules against the current violation count.

        Applies the first matching sanction, resets violation count,
        and returns a result dict if a hold was triggered.
        """
        if not self._sanctions:
            return None
        for rule in self._sanctions:
            threshold = rule.get("on_violation_count")
            if threshold is None:
                continue
            if self._violation_count < threshold:
                continue
            # Sanction matched — apply consequences and reset count.
            self._violation_count = 0
            self._save_state()
            for consequence in rule.get("then", []):
                ctype = consequence.get("type")
                if ctype == "obliged":
                    objective = consequence.get("objective", "")
                    if objective:
                        deadline = consequence.get("deadline", "false")
                        self.create_obligation(agent_id, role, objective, deadline)
                        self._log({
                            "type": "sanction", "agent": agent_id,
                            "sanction": "obliged", "objective": objective,
                            "threshold": threshold,
                        })
                elif ctype == "hold":
                    message = consequence.get("message", f"Sanction: {threshold} violations reached")
                    self._hold = {"reason": message, "ts": time.time()}
                    self._save_state()
                    self._log({
                        "type": "sanction", "agent": agent_id,
                        "sanction": "hold", "reason": message,
                        "threshold": threshold,
                    })
                    return {
                        "decision": "block",
                        "reason": (
                            f"SANCTION: {message}\n"
                            f"All actions are now blocked until the hold is cleared.\n"
                            f"To continue: aorta continue"
                        ),
                    }
            return None  # sanctions applied but no hold
        return None

    def _check_guardrails(self, context: dict, agent_id: str) -> dict | None:
        """Run guardrails checks after a tool use completes.

        Tracks actions in a ring buffer and checks for:
        - failure_rate: high failure rate in recent actions
        - per_file_rewrites: same file written too many times
        - files_modified: total unique files modified
        - bash_commands: total bash commands executed

        Each check can trigger a hold (hard gate) or warning (stderr nudge).
        Returns a result dict if a warning/hold was triggered, None otherwise.
        """
        if not self._thrashing_config:
            return None

        tool_name = context.get("tool_name", "")
        tool_input = context.get("tool_input", {})
        tool_response = context.get("tool_response", {})
        action = TOOL_ACTION_MAP.get(tool_name)
        if not action:
            return None

        exit_code = tool_response.get("exitCode", tool_response.get("exit_code"))
        failed = (exit_code is not None and exit_code != 0)

        # Track in ring buffer (for failure_rate).
        window_size = self._thrashing_config.get("window_size", 10)
        self._action_ring.append({"tool": tool_name, "failed": failed})
        if len(self._action_ring) > window_size:
            self._action_ring = self._action_ring[-window_size:]

        # Track file writes (for per_file_rewrites and files_modified).
        if action == "write_file":
            raw_path = tool_input.get("file_path") or tool_input.get("path") or ""
            if raw_path:
                rel_path = _make_relative(raw_path, self._org_spec_path)
                self._file_write_counts[rel_path] = self._file_write_counts.get(rel_path, 0) + 1

        # Track bash commands (cumulative, for bash_commands budget).
        if tool_name == "Bash":
            self._bash_command_count += 1

        self._save_state()

        warnings = []

        # Check failure_rate.
        fr_config = self._thrashing_config.get("failure_rate")
        if fr_config and len(self._action_ring) >= window_size:
            threshold = fr_config.get("threshold", 0.5)
            failures = sum(1 for a in self._action_ring if a["failed"])
            rate = failures / len(self._action_ring)
            if rate >= threshold:
                msg = (
                    f"High failure rate: {failures}/{len(self._action_ring)} "
                    f"recent actions failed ({rate:.0%} >= {threshold:.0%} threshold)"
                )
                result = self._apply_guardrail(fr_config, msg, agent_id)
                if result:
                    return result
                warnings.append(msg)

        # Check per_file_rewrites.
        pf_config = self._thrashing_config.get("per_file_rewrites")
        if pf_config and action == "write_file":
            threshold = pf_config.get("threshold", 3)
            raw_path = tool_input.get("file_path") or tool_input.get("path") or ""
            if raw_path:
                rel_path = _make_relative(raw_path, self._org_spec_path)
                count = self._file_write_counts.get(rel_path, 0)
                if count >= threshold:
                    msg = (
                        f"File '{rel_path}' has been modified {count} times "
                        f"(threshold: {threshold}). Consider stepping back and rethinking."
                    )
                    result = self._apply_guardrail(pf_config, msg, agent_id)
                    if result:
                        return result
                    warnings.append(msg)

        # Check files_modified (unique files written).
        fm_config = self._thrashing_config.get("files_modified")
        if fm_config:
            threshold = fm_config.get("threshold", 15)
            if len(self._file_write_counts) >= threshold:
                msg = (
                    f"Modified {len(self._file_write_counts)} unique files "
                    f"(threshold: {threshold}). Is this still within scope?"
                )
                result = self._apply_guardrail(fm_config, msg, agent_id)
                if result:
                    return result
                warnings.append(msg)

        # Check bash_commands (cumulative).
        bc_config = self._thrashing_config.get("bash_commands")
        if bc_config and tool_name == "Bash":
            threshold = bc_config.get("threshold", 50)
            if self._bash_command_count >= threshold:
                msg = (
                    f"Executed {self._bash_command_count} bash commands "
                    f"(threshold: {threshold}). Consider whether this is expected."
                )
                result = self._apply_guardrail(bc_config, msg, agent_id)
                if result:
                    return result
                warnings.append(msg)

        if warnings:
            return {
                "_guardrail_warning": "[GUARDRAIL] " + " | ".join(warnings),
            }

        return None

    def _apply_guardrail(self, config: dict, message: str, agent_id: str) -> dict | None:
        """Apply a guardrail action (hold or warning).

        Returns a result dict for holds, None for warnings (warnings are collected).
        """
        action_type = config.get("action", "warning")
        if action_type == "hold":
            self._hold = {"reason": message, "ts": time.time()}
            self._save_state()
            self._log({
                "type": "hold", "agent": agent_id,
                "reason": message,
            })
            return {
                "_guardrail_warning": (
                    f"[GUARDRAIL HOLD] {message}\n"
                    f"All actions are now blocked until the hold is cleared.\n"
                    f"To continue: aorta continue"
                ),
            }
        # Warning — return None, caller collects the message.
        return None

    def _matches_sensitive_path(self, path: str) -> bool:
        """Check if a path matches any sensitive (read-only/no-access) access map entry."""
        import fnmatch
        for pattern in self._sensitive_paths:
            if any(c in pattern for c in "*?["):
                if fnmatch.fnmatch(path, pattern):
                    return True
            else:
                prefix = pattern.rstrip("/")
                if path == prefix or path.startswith(prefix):
                    return True
        return False

    def get_system_prompt_injection(self, agent: str) -> str | None:
        """Generate system prompt text from active obligations and options.

        Returns text suitable for injection into a sub-agent's system prompt,
        or None if there are no active obligations.
        """
        role = self._get_agent_role(agent)
        if not role:
            return None

        result = self._service.get_obligations(agent, role)
        obligations = result.get("obligations", [])
        options = result.get("options", [])

        if not obligations and not options:
            return None

        lines = ["[ORGANIZATIONAL CONTEXT]"]

        if obligations:
            lines.append("Active obligations:")
            for obl in obligations:
                lines.append(
                    f"  - You are {obl['deontic']} to achieve: {obl['objective']}"
                    f" (deadline: {obl['deadline']})"
                )

        norm_opts = [o for o in options if o["type"] == "norm"]
        viol_opts = [o for o in options if o["type"] == "violation"]
        delegate_opts = [o for o in options if o["type"] == "delegate"]

        if viol_opts:
            lines.append("VIOLATIONS (require attention):")
            for v in viol_opts:
                lines.append(f"  - Violated: {v['deontic']} {v['objective']}")

        if delegate_opts:
            lines.append("Delegation options:")
            for d in delegate_opts:
                lines.append(f"  - Delegate {d['objective']} to role: {d['to_role']}")

        return "\n".join(lines)

    def _check_exception(self, path: str, agent: str,
                         action: str = "write_file") -> str:
        """Check if an allow-once exception exists for this path+agent.

        Returns:
            "consumed" — exception matched and used up
            "matched" — exception matched but not consumed (read of non-no-access)
            "" — no match
        """
        now = time.time()
        expired = [e for e in self._exceptions if now - e.get("ts", 0) > EXCEPTION_TTL]
        if expired:
            for e in expired:
                self._exceptions.remove(e)
            self._save_state()

        for exc in self._exceptions:
            if exc["path"] != path:
                # Check if path starts with exception path (prefix match)
                if not path.startswith(exc["path"]):
                    continue
            if exc["agent"] != "*" and exc["agent"] != agent:
                continue
            # For reads, only consume if the path is no-access.
            if action == "read_file" and not self._is_no_access_path(path):
                return "matched"  # allow but don't consume
            exc["uses"] -= 1
            if exc["uses"] <= 0:
                self._exceptions.remove(exc)
            self._save_state()
            return "consumed"
        return ""

    def _is_no_access_path(self, path: str) -> bool:
        """Check if a path matches a no-access entry in the access map."""
        import fnmatch
        for pattern in self._no_access_patterns:
            if any(c in pattern for c in "*?["):
                if fnmatch.fnmatch(path, pattern):
                    return True
            else:
                prefix = pattern.rstrip("/")
                if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix):
                    return True
        return False

    def _handle_soft_block(self, reason: str, params: dict,
                           block_message: str | None = None) -> dict:
        """Handle a soft (confirmation-required) block.

        On first encounter: block with a message.
        On retry within the time window: approve (user confirmed).

        If block_message is set (custom message from norm), it replaces
        the default confirmation prompt.
        """
        cache_key = self._soft_block_key(params)
        now = time.time()

        # Check if this is a retry of a recently soft-blocked command.
        prev = self._soft_block_cache.get(cache_key)
        if prev is not None and (now - prev) < self._soft_block_window:
            del self._soft_block_cache[cache_key]
            self._save_state()
            return {"decision": "approve"}

        # First time — block and cache for retry.
        self._soft_block_cache[cache_key] = now
        self._save_state()
        short_reason = _shorten_block_reason(reason)
        if block_message:
            msg = f"SOFT BLOCK: {short_reason}\n  {block_message}"
        else:
            msg = (
                f"SOFT BLOCK: {short_reason}\n"
                f"This action requires user confirmation before proceeding."
            )
        return {
            "decision": "block",
            "reason": msg,
        }

    @staticmethod
    def _soft_block_key(params: dict) -> str:
        """Generate a cache key for soft block deduplication.

        Normalizes whitespace so that semantically identical commands
        (differing only in heredoc formatting, trailing newlines, etc.)
        produce the same key.
        """
        raw = params.get("command", "") or params.get("path", "")
        normalized = " ".join(raw.split())
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _log(self, event: dict) -> None:
        """Log an event tagged with the org spec name."""
        event["org_spec"] = self._org_spec_name
        log_event(event, self._events_path)

    def _get_agent_role(self, agent: str) -> str | None:
        """Look up the role for a registered agent."""
        return self._service.get_agent_role(agent)


def main():
    """CLI entry point for Claude Code hooks."""
    parser = argparse.ArgumentParser(
        prog="integration.hooks",
        description="aorta4llm governance hooks for Claude Code",
    )
    parser.add_argument(
        "command",
        choices=["pre-tool-use", "post-tool-use", "register", "prompt", "session-start"],
    )
    parser.add_argument("--org-spec", required=True, help="Path to org spec YAML")
    parser.add_argument("--state", default=None, help="State file path (default: .aorta/state.json)")
    parser.add_argument("--agent", help="Agent ID")
    parser.add_argument("--role", help="Role (for register)")
    parser.add_argument("--scope", default="", help="Scope (for register)")
    parser.add_argument("--events-path", default=None, help="Events JSONL path")
    parser.add_argument("--cwd", default=None, help="Project root for path normalization")

    args = parser.parse_args()
    hook = GovernanceHook(args.org_spec, args.state, events_path=args.events_path)

    if args.command == "register":
        if not args.agent or not args.role:
            parser.error("register requires --agent and --role")
        hook.register_agent(args.agent, args.role, args.scope)
        _respond({"ok": True})

    elif args.command == "pre-tool-use":
        context = json.loads(sys.stdin.read())
        result = hook.pre_tool_use(context, agent=args.agent, project_cwd=args.cwd)
        reset_notice = result.pop("_reset_notice", None)
        _respond_hook(result)
        if reset_notice:
            print(reset_notice, file=sys.stderr, flush=True)

    elif args.command == "post-tool-use":
        context = json.loads(sys.stdin.read())
        result = hook.post_tool_use(context, agent=args.agent)
        warning = result.pop("_sensitive_warning", None)
        guardrail_warning = result.pop("_guardrail_warning", None)
        achievement = result.pop("_achievement_notice", None)
        piped = result.pop("_piped_notice", None)
        if warning:
            print(warning, file=sys.stderr, flush=True)
            sys.exit(2)
        if guardrail_warning:
            print(guardrail_warning, file=sys.stderr, flush=True)
            sys.exit(2)
        notices = []
        if achievement:
            notices.append(achievement)
        if piped:
            notices.append(piped)
        if notices:
            print("\n".join(notices), file=sys.stderr, flush=True)
            sys.exit(2)
        _respond(result)

    elif args.command == "prompt":
        if not args.agent:
            parser.error("prompt requires --agent")
        text = hook.get_system_prompt_injection(args.agent)
        if text:
            print(text)

    elif args.command == "session-start":
        from aorta4llm.cli.cmd_context import run as run_context
        import io
        # Capture context output
        old_stdout = sys.stdout
        sys.stdout = buf = io.StringIO()
        class _Args:
            org_spec = args.org_spec
        run_context(_Args())
        context_text = buf.getvalue()
        sys.stdout = old_stdout
        # Also append obligation injection if agent is registered
        if args.agent:
            obligations = hook.get_system_prompt_injection(args.agent)
            if obligations:
                context_text += "\n" + obligations
        if context_text.strip():
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context_text.strip(),
                }
            }), flush=True)


def _respond(obj: dict):
    print(json.dumps(obj), flush=True)


def _respond_hook(result: dict):
    """Respond in Claude Code hook format.

    Claude Code expects:
    - Approve: exit 0 (empty stdout is fine)
    - Block: exit 0 with hookSpecificOutput containing permissionDecision=deny
    """
    if result.get("decision") == "block":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": result.get("reason", "blocked by governance"),
            }
        }), flush=True)
    # For approve: exit 0 with no output


if __name__ == "__main__":
    main()
